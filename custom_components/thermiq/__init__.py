import json
import logging
from datetime import timedelta

import voluptuous as vol
from collections import OrderedDict

# import ThermIQ register defines
from custom_components.thermiq_mqtt.thermiq_regs import (
    FIELD_BITMASK,
    FIELD_MAXVALUE,
    FIELD_MINVALUE,
    FIELD_REGNUM,
    FIELD_REGTYPE,
    FIELD_UNIT,
    id_names,
    id_units,
    reg_id,
)

from homeassistant.components import mqtt

# import homeassistant.components.sensor as sensor
from homeassistant.components.input_select import (
    ATTR_OPTION,
    ATTR_OPTIONS,
    DOMAIN,
    SERVICE_SELECT_NEXT,
    SERVICE_SELECT_OPTION,
    SERVICE_SELECT_PREVIOUS,
    SERVICE_SET_OPTIONS,
)
from homeassistant.components.input_number import (
    ATTR_VALUE as INP_ATTR_VALUE,
    DOMAIN as INP_DOMAIN,
    SERVICE_RELOAD,
    SERVICE_SET_VALUE as INP_SERVICE_SET_VALUE,
)
from homeassistant.const import ATTR_ENTITY_ID  # UNIT_PERCENTAGE,
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    ATTR_EDITABLE,
    ATTR_ENTITY_ID,
    ATTR_FRIENDLY_NAME,
    ATTR_NAME,
    CONF_HOST,
    DEVICE_CLASS_BATTERY,
    DEVICE_CLASS_HUMIDITY,
    DEVICE_CLASS_TEMPERATURE,
    POWER_WATT,
    SERVICE_TOGGLE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_ON,
    STATE_UNKNOWN,
    TEMP_CELSIUS,
)
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import discovery
from homeassistant.helpers.template import Template
from homeassistant.util import Throttle


from .helper import (
    create_automations,
    create_entities_and_automations,
    CONFIG_INPUT_BOOLEAN,
    COMPONENT_INPUT_BOOLEAN,
    CONFIG_INPUT_DATETIME,
    COMPONENT_INPUT_DATETIME,
    CONFIG_INPUT_NUMBER,
    COMPONENT_INPUT_NUMBER,
    CONFIG_INPUT_TEXT,
    COMPONENT_INPUT_TEXT,
    CONFIG_TIMER,
    COMPONENT_TIMER,
)

from .helper import (
    create_input_datetime,
    create_input_number,
    create_input_select,
    create_automation,
)

from homeassistant.components.automation import EVENT_AUTOMATION_RELOADED
from homeassistant.const import CONF_ENTITY_ID, CONF_STATE, EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant, Event


THERMIQ_PLATFORMS = ["binary_sensor", "sensor"]

DOMAIN = "thermiq_mqtt"

# List of integration names (string) your integration depends upon.
DEPENDENCIES = ["mqtt"]


# Constants and Schema used to validate the configuration
CONF_MQTT_NODE = "mqtt_node"
CONF_MQTT_DBG = "thermiq_dbg"
DEFAULT_NODE = "ThermIQ/ThermIQ-mqtt"
CONF_DATA = "data_msg"
DEFAULT_DATA = "/data"
CONF_CMD = "cmd_msg"
DEFAULT_CMD = "/WRITE"
DEFAULT_DBG = False
MSG_RECEIVED_STATE = "thermiq_mqtt.last_msg_time"


CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_MQTT_NODE, default=DEFAULT_NODE): cv.string,
        vol.Optional(CONF_MQTT_DBG, default=False): cv.boolean,
    },
    extra=vol.ALLOW_EXTRA,
)


_LOGGER = logging.getLogger(__name__)


async def async_setup(hass, config):

    conf = config.get(DOMAIN, {})
    mqtt_base = conf.get(CONF_MQTT_NODE)
    dbg = conf.get(CONF_MQTT_DBG)

    conf.entity_id = "thermiq_mqtt"

    _LOGGER.debug("mqtt base: " + mqtt_base)
    conf.data_topic = mqtt_base + "/data"
    conf.cmd_topic = mqtt_base + "/write"
    conf.set_topic = mqtt_base + "/set"

    if dbg == True:
        conf.cmd_topic += "_dbg"
        conf.set_topic += "_dbg"
        _LOGGER.debug("MQTT Debug write enabled")

    hass.data[DOMAIN] = {}
    hass.data[DOMAIN] = ThermIQ_MQTT(config[DOMAIN])
    hass.states.async_set("thermiq_mqtt.time_str", "Waiting on " + conf.data_topic)
    hass.data[DOMAIN]._data["mqtt_counter"] = 0

    # ### Setup the input helper #############################
    CONFIG_INPUT_BOOLEAN.update(config.get(COMPONENT_INPUT_BOOLEAN, {}))
    CONFIG_INPUT_DATETIME.update(config.get(COMPONENT_INPUT_DATETIME, {}))
    CONFIG_INPUT_NUMBER.update(config.get(COMPONENT_INPUT_NUMBER, {}))
    CONFIG_INPUT_TEXT.update(config.get(COMPONENT_INPUT_TEXT, {}))
    CONFIG_TIMER.update(config.get(COMPONENT_TIMER, {}))

    async def handle_home_assistant_started_event(event: Event):
        await create_entities_and_automations(hass)

    async def handle_automation_reload_event(event: Event):
        await create_automations(hass)

    hass.bus.async_listen(
        EVENT_HOMEASSISTANT_STARTED, handle_home_assistant_started_event
    )
    hass.bus.async_listen(EVENT_AUTOMATION_RELOADED, handle_automation_reload_event)

    # #########################################################
    # ### Load sensors
    for platform in THERMIQ_PLATFORMS:
        _LOGGER.debug("platform:" + platform)
        discovery.load_platform(hass, platform, DOMAIN, {}, config)

    # Create reverse lookup dictionary (id_reg->reg_number)
    id_reg = {}
    for k, v in reg_id.items():
        id_reg[v[0]] = k
        hass.states.async_set("thermiq_mqtt." + v[0], -1)
        # _LOGGER.debug("id_reg[%s] => %s", v[0], k)

    # ### Create the inputs and automations  #############################
    icon_list = {
        "time_input": "mdi:timer",
        "sensor_input": "mdi:speedometer",
        "temperature_input": "mdi:temperature-celsius",
    }
    mode_list = {
        "time_input": "slider",
        "sensor_input": "box",
        "temperature_input": "box",
    }
    entity_list = []
    for key in reg_id:
        if reg_id[key][1] in [
            "temperature_input",
            "time_input",
            "sensor_input",
        ]:
            device_id = key
            if key in id_names:
                friendly_name = id_names[key]
            else:
                friendly_name = None
            input_reg = reg_id[key][0]
            input_type = reg_id[key][1]
            input_unit = reg_id[key][2]
            input_min = reg_id[key][3]
            input_max = reg_id[key][4]
            input_step = 1
            input_initial = -1
            # Check if room_sensor then allow for decimals
            if reg_id[key][0] == "rf0":
                input_step = 0.1

            await create_input_number(
                "thermiq_" + key,
                friendly_name,
                input_min,
                input_max,
                input_step,
                input_initial,
                mode_list[input_type],
                input_unit,
                icon=icon_list[input_type],
            )
            entity_list.append("input_number.thermiq_" + key)

        if reg_id[key][1] in [
            "select_input",
        ]:
            device_id = key
            if key in id_names:
                friendly_name = id_names[key]
            else:
                friendly_name = None
            input_reg = reg_id[key][0]
            input_options = [
                "Off",
                "Auto",
                "Heatpump only",
                "Electric only",
                "Hotwater only",
            ]
            input_initial = None

            await create_input_select(
                "thermiq_" + key,
                friendly_name,
                input_options,
                input_initial,
                icon="mdi:power",
            )

    entity_list.remove("input_number.thermiq_room_sensor_set_t")
    await create_automation_for_input_numbers(entity_list)
    await create_automation_for_room_sensor()
    # ### ##################################################################

    # ###
    @callback
    def message_received(message):
        """Handle new MQTT messages."""
        _LOGGER.debug("message.payload:[%s]", message.payload)
        try:
            json_dict = json.loads(message.payload)
            if json_dict["Client_Name"][:8] == "ThermIQ_":
                for k in json_dict.keys():
                    kstore = k.lower()
                    dstore = k
                    # Make INDR_T and timestamp appear as normal register
                    if kstore == "indr_t":
                        kstore = "rf0"
                    if kstore == "timestamp":
                        kstore = "rf1"
                    # Create hex notation if incoming register is decimal format
                    if k[0] == "d":
                        reg = int(k[1:])
                        kstore = "r" + format(reg, "02x")
                        dstore = "d" + format(reg, "03d")
                        if len(kstore) != 3:
                            kstore = k
                    # Create decimal notation if incoming register is hex format
                    if k[0] == "r" and len(k) == 3:
                        reg = int(k[1:], 16)
                        dstore = "d" + format(reg, "03d")

                    # Internal mapping of ThermIQ_MQTT regs, used to create update events
                    hass.data[DOMAIN]._data[kstore] = json_dict[k]

                    _LOGGER.debug("[%s] [%s] [%s]", kstore, json_dict[k], dstore)
                    # Map incomming registers to named settings based on id_reg (thermiq_regs)
                    if kstore in id_reg:
                        # r01 and r03 should be combined with respective decimal part r02 and r04
                        if kstore != "r01" and kstore != "r03":
                            hass.states.async_set(
                                "thermiq_mqtt." + id_reg[kstore], json_dict[k]
                            )
                            ## Set the corresponding input_number if applicable, incomming message always rules over UI settings
                            if reg_id[id_reg[kstore]][1] in [
                                "temperature_input",
                                "time_input",
                                "sensor_input",
                            ]:
                                context = {
                                    INP_ATTR_VALUE: json_dict[k],
                                    ATTR_ENTITY_ID: "input_number.thermiq_"
                                    + id_reg[kstore],
                                }
                                hass.async_create_task(
                                    hass.services.async_call(
                                        INP_DOMAIN,
                                        INP_SERVICE_SET_VALUE,
                                        context,
                                        blocking=False,
                                    )
                                )
                            if reg_id[id_reg[kstore]][1] == "input_select":

                                context = {
                                    INP_ATTR_VALUE: json_dict[k],
                                    ATTR_ENTITY_ID: "input_select.thermiq_"
                                    + id_reg[kstore],
                                }
                                hass.async_create_task(
                                    hass.services.async_call(
                                        INP_DOMAIN,
                                        INP_SERVICE_SET_VALUE,
                                        context,
                                        blocking=False,
                                    )
                                )

                # Do some post processing of data received
                hass.data[DOMAIN]._data["r01"] = (
                    hass.data[DOMAIN]._data["r01"] + hass.data[DOMAIN]._data["r02"] / 10
                )
                hass.states.async_set(
                    "thermiq_mqtt." + id_reg["r01"], hass.data[DOMAIN]._data["r01"]
                )

                hass.data[DOMAIN]._data["r03"] = (
                    hass.data[DOMAIN]._data["r03"] + hass.data[DOMAIN]._data["r04"] / 10
                )
                hass.states.async_set(
                    "thermiq_mqtt." + id_reg["r03"], hass.data[DOMAIN]._data["r03"]
                )

                hass.data[DOMAIN]._data["mqtt_counter"] += 1

                hass.states.async_set("thermiq_mqtt.time_str", json_dict["time"])

                hass.bus.fire("thermiq_mqtt_msg_rec_event", {})

            else:
                _LOGGER.error("JSON result was not from ThermIQ-mqtt")
        except ValueError:
            _LOGGER.error("MQTT payload could not be parsed as JSON")
            _LOGGER.debug("Erroneous JSON: %s", payload)

    # Service to publish a message on MQTT.
    @callback
    def write_msg_service(call):
        """Service to send a message."""
        _LOGGER.debug("message.entity_id:[%s]", call.data.get("entity_id"))
        hass.async_create_task(
            hass.components.mqtt.async_publish(
                hass, conf.cmd_topic, call.data.get("msg"), qos=2, retain=False
            )
        )

    # Service to write specific reg with data, value_id will be translated to register number.
    @callback
    def write_reg_service(call):
        reg = call.data.get("reg")
        reg = reg.lower()
        value = call.data.get("value")
        bitmask = call.data.get("bitmask")

        # We should check that reg is of format rxx (hex) or dnnn (decimal with possible leading zeroes) and is btwn 0-127, regardless case of x and d
        # and give error here. ThermIQ-mqtt will throw away the message anyway
        ##

        if not (isinstance(value, int)) or value is None:
            _LOGGER.error("no value message sent due to missing value:[%s]", value)
            return

        if bitmask is None:
            bitmask = 0xFFFF

        ## check the bitmask
        # value = value | bitmask
        value = int(value) & int(bitmask)

        # Lets use the decimal register notation in the MQTT message towards ThermIQ-MQTT to improve human readability
        if reg[0] == "d":
            dreg = reg
        if reg[0] == "r" and len(reg) == 3:
            reg = int(k[1:], 16)
            dreg = "d" + format(reg, "03d")

        if dreg == "d240":
            topic = conf.set_topic
        else:
            conf.cmd_topic

        # Make up the JSON payload
        payload = json.dumps({dreg: value})

        _LOGGER.debug("topic:[%s]", conf.cmd_topic)
        _LOGGER.debug("payload:[%s]", payload)
        hass.async_create_task(
            hass.components.mqtt.async_publish(
                hass, topic, payload, qos=2, retain=False
            )
        )

    # Service to write specific value_id with data, value_id will be translated to register number.
    @callback
    def write_id_service(call):
        """Service to send a message."""
        register_id = call.data.get("register_id")
        register_id = register_id.lower()
        value = call.data.get("value")
        bitmask = call.data.get("bitmask")

        if not (isinstance(value, int) or isinstance(value, float)) or value is None:
            _LOGGER.error("no value message sent due to missing value:[%s]", value)
            return

        if bitmask is None:
            bitmask = 0xFFFF

        ## check the bitmask
        # value = value | bitmask
        value = int(value) & int(bitmask)

        # Strip any leading instance names, then strip thermiq_
        idx = register_id.find(".") + 1
        idx = len(register_id) - (register_id.find("thermiq_", idx) + 8)
        if idx > 0:
            register_id = register_id[-idx:]

        _LOGGER.debug("register_id:[%s]", register_id)
        _LOGGER.debug("value:[%s]", value)

        if not (register_id in reg_id):
            _LOGGER.error("no value message sent due to faulty reg:[%s]", register_id)
            return

        reg = reg_id[register_id][0]

        _LOGGER.debug("reg:[%s]", reg)
        # Lets use the decimal register notation in the MQTT message towards ThermIQ-MQTT to improve human readability
        dreg = "d" + format(int(reg[1:], 16), "03d")
        if dreg == "d240":
            topic = conf.set_topic
        else:
            conf.cmd_topic

        # Make up the JSON payload
        payload = json.dumps({dreg: value})

        _LOGGER.debug("topic:[%s]", conf.cmd_topic)
        _LOGGER.debug("payload:[%s]", payload)
        if value != hass.data[DOMAIN]._data[reg]:
            # Lets update the internal state,
            hass.data[DOMAIN]._data[reg] = value
            hass.states.async_set("thermiq_mqtt." + id_reg[reg], value)
            _LOGGER.debug("set _data[%s]=%d", reg, hass.data[DOMAIN]._data[reg])
            if hass.data[DOMAIN]._data["mqtt_counter"] > 3:
                hass.async_create_task(
                    hass.components.mqtt.async_publish(
                        hass, conf.cmd_topic, payload, qos=2, retain=False
                    )
                )
        else:
            _LOGGER.debug("No need to write")

    @callback
    def write_mode_service(call):
        """Service to send a message."""
        value = int(call.data.get("value"))
        if not (isinstance(value, int)) or value is None:
            _LOGGER.error("no value message sent due to missing value:[%s]", value)
            return

        reg = "d51"
        bitmask = 0x01F

        if (value < 0) or (value > 5):
            _LOGGER.error("Mode value is out of range:[%s]", value)
            return

        value = int(2 ** value) & int(bitmask)

        # Make up the JSON payload
        payload = json.dumps({dreg: value})
        _LOGGER.debug("topic:[%s]", conf.cmd_topic)
        _LOGGER.debug("payload:[%s]", payload)

        if value != hass.data[DOMAIN]._data[reg]:
            hass.states.async_set("thermiq_mqtt.r33", value)
            hass.async_create_task(
                hass.components.mqtt.async_publish(
                    hass, conf.cmd_topic, payload, qos=2, retain=False
                )
            )
        else:
            _LOGGER.debug("No need to write")

    @callback
    def set_indr_t_service(call):
        """Service to send a message."""
        value = float(call.data.get("value"))
        if not (isinstance(value, float)) or value is None:
            _LOGGER.error("no value message sent due to missing value:[%s]", value)
            return

        reg = "rf0"
        if (value < 10) or (value > 30):
            _LOGGER.error("Mode value is out of range:[%s]", value)
            return

        # Make up the JSON payload
        payload = json.dumps({"INDR_T": value})
        _LOGGER.debug("topic:[%s]", conf.cmd_topic)
        _LOGGER.debug("payload:[%s]", payload)

        if value != hass.data[DOMAIN]._data[reg]:
            hass.states.async_set("thermiq_mqtt.rf0", value)
            hass.async_create_task(
                hass.components.mqtt.async_publish(
                    hass, conf.set_topic, payload, qos=2, retain=False
                )
            )
        else:
            _LOGGER.debug("No need to write")

    # Register our service with Home Assistant.
    hass.services.async_register(DOMAIN, "write_msg", write_msg_service)
    hass.services.async_register(DOMAIN, "write_id", write_id_service)
    hass.services.async_register(DOMAIN, "write_reg", write_reg_service)
    hass.services.async_register(DOMAIN, "write_mode", write_mode_service)
    hass.services.async_register(DOMAIN, "set_indr_t", set_indr_t_service)

    _LOGGER.info("Subscribe:" + conf.data_topic)
    await hass.components.mqtt.async_subscribe(conf.data_topic, message_received)
    # Return boolean to indicate that initialization was successfully.
    return True


# #### automate it
async def create_automation_for_input_numbers(entities: list):
    data = {
        "alias": "ThermIQ Input numbers to MQTT",
        "trigger": [{"platform": "state", "entity_id": entities}],
        #        'condition': [
        #          {
        #          }
        #        ],
        "action": [
            {
                "service": "thermiq_mqtt.write_id",
                "data_template": {
                    "register_id": Template("{{ trigger.entity_id  }}"),
                    "value": Template("{{ trigger.to_state.state | int }}"),
                    "bitmask": 0xFFFF,
                },
            }
        ],
        "mode": "single",
        "max_exceeded": "WARNING",
        "max": 1,
        "trace": {"stored_traces": 5},
    }
    # _LOGGER.debug(data)
    await create_automation(OrderedDict(data))


async def create_automation_for_room_sensor():
    data = {
        "alias": "ThermIQ Room sensor to MQTT",
        "trigger": [
            {"platform": "state", "entity_id": "input_number.thermiq_room_sensor_set_t"}
        ],
        #        'condition': [
        #          {
        #          }
        #        ],
        "action": [
            {
                "service": "thermiq_mqtt.set_indr_t",
                "data_template": {
                    "value": Template("{{ trigger.to_state.state | float }}"),
                },
            }
        ],
        "mode": "single",
        "max_exceeded": "WARNING",
        "max": 1,
        "trace": {"stored_traces": 5},
    }
    # _LOGGER.debug(data)
    await create_automation(OrderedDict(data))

    # ### Select -> MQTT
    data = {
        "alias": "ThermIQ, Inputs select MOde to MQTT",
        "trigger": [
            {"platform": "state", "entity_id": ["input_select.thermiq_main_mode"],}
        ],
        "action": [
            {
                "service": "thermiq_mqtt.write_id",
                "data_template": {
                    "register_id": Template("{{ trigger.entity_id  }}"),
                    "value": Template(
                        "{{ state_attr(trigger.entity_id,'options').index(states(trigger.entity_id)) | int }}"
                    ),
                    "bitmask": 0xFFFF,
                },
            },
        ],
        "mode": "single",
        "max_exceeded": "WARNING",
        "max": 1,
        "trace": {"stored_traces": 5},
    }
    await create_automation(OrderedDict(data))

    data = {
        "alias": "ThermIQ, Mode to Inputs Select",
        "trigger": [{"platform": "state", "entity_id": ["thermiq_mqtt.main_mode"],}],
        #      'condition': {
        #          'condition': 'template',
        #          'value_template': Template("{{ ( ((trigger.to_state.state | int )  >=0 ) and ((trigger.to_state.state | int )  <5 ) ) }}"),
        #       },
        "action": [
            {
                "service": "input_select.select_option",
                "data_template": {
                    "entity_id": ["input_select.thermiq_main_mode"],
                    "option": Template(
                        "{{ state_attr('input_select.thermiq_main_mode','options')[(trigger.to_state.state | int )] }}"
                    ),
                },
            },
        ],
        "mode": "single",
        "max_exceeded": "WARNING",
        "max": 1,
        "trace": {"stored_traces": 5},
    }
    await create_automation(OrderedDict(data))


class ThermIQ_MQTT:
    # Listener to be called when we receive a message.
    # The msg parameter is a Message object with the following members:
    # - topic, payload, qos, retain
    # Listen to a message on MQTT.
    """Handle all communication with ThermIQ."""

    def __init__(self, host):
        """Initialize the MQTT Record."""
        self._data = {}

    def get_value(self, item):
        """Get value for sensor."""
        res = self._data.get(item)
        _LOGGER.debug("get_value(" + item + ")=%d", res)
        return res

    def update_state(self, command, state_command):
        """Send update command to ThermIQ."""
        _LOGGER.debug("update_state:" + command + " " + state_command)
        self._data[state_command] = self._client.command(command)
        hass.async_create_task(
            hass.components.mqtt.async_publish(
                conf.cmd_topic, self._data[state_command]
            )
        )

    async def async_update(self):
        _LOGGER.debug("Fetching data from ThermIQ-MQTT")
        _LOGGER.debug("Done fetching data from ThermIQ-MQTT")
