"""Constants for the Nulleinspeisung integration."""

from datetime import timedelta

DOMAIN = "nulleinspeisung"

SUBENTRY_TYPE_DTU = "dtu"

CONF_URL = "url"
CONF_PASSWORD = "password"
CONF_MAC = "mac"
"""Hardware address of a DTU, lower case without separators."""

DTU_USER = "admin"
"""OpenDTU's administrator user name; it cannot be changed on the DTU."""

DTU_UPDATE_INTERVAL = timedelta(seconds=10)
"""How often each DTU is polled, independent of any House."""

DTU_REQUEST_TIMEOUT = 10
"""Seconds before a request to a DTU is given up."""

CONF_SUN_ANGLE = "sun_angle"
CONF_STALENESS_TIME = "staleness_time"
CONF_RESTART_WAIT = "restart_wait"
CONF_NOTIFY_TARGET = "notify_target"

DEFAULT_SUN_ANGLE = 5.0
"""Degrees of sun elevation above which production must be possible."""
DEFAULT_STALENESS_TIME = 120
"""Seconds without fresh Inverter data after which a DTU counts as silent."""
DEFAULT_RESTART_WAIT = 180
"""Seconds to wait after a restart before judging the DTU again."""

SUBENTRY_TYPE_HOUSE = "house"

CONF_NAME = "name"
CONF_LOCATION = "location"
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"
CONF_GRID_METER = "grid_meter"
CONF_GRID_METER_SIGN = "grid_meter_sign"
CONF_INVERTERS = "inverters"
CONF_BATTERY_BACKED = "battery_backed"
CONF_GX_HOST = "gx_host"
CONF_GX_PORT = "gx_port"
CONF_GX_PORTAL_ID = "gx_portal_id"
CONF_BATTERY_CAPACITY = "battery_capacity"
CONF_GRID_METER_TOPIC = "grid_meter_topic"
"""MQTT topic the AC Battery's virtual grid meter listens to."""

DEFAULT_GX_PORT = 1883
"""Port of the MQTT broker built into a Victron GX."""

SIGN_IMPORT = "import"
"""The Grid Meter reports positive values for import."""
SIGN_EXPORT = "export"
"""The Grid Meter reports positive values for export."""

HOUSE_MODEL = "House"

CONF_TIBBER_TOKEN = "tibber_token"
CONF_TIBBER_HOME = "tibber_home"

TIBBER_URL = "https://api.tibber.com/v1-beta/gql"
TIBBER_REQUEST_TIMEOUT = 15
"""Seconds before a request to Tibber is given up."""

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_REQUEST_TIMEOUT = 15
"""Seconds before a request to Open-Meteo is given up."""
