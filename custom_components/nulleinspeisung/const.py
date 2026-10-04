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
