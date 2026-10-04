"""Constants for the Nulleinspeisung integration."""

from datetime import timedelta

DOMAIN = "nulleinspeisung"

SUBENTRY_TYPE_DTU = "dtu"

CONF_URL = "url"
CONF_PASSWORD = "password"

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
