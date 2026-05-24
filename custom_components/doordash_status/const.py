"""Shared constants for DoorDash Status."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "doordash_status"
PLATFORMS = [Platform.SENSOR]

CONF_AUTH_MODE = "auth_mode"
CONF_BASE_URL = "base_url"
CONF_BROWSER_COOKIE = "browser_cookie"
CONF_TRACKING_URL = "tracking_url"
CONF_SCAN_INTERVAL_MINUTES = "scan_interval_minutes"

DEFAULT_BASE_URL = "https://www.doordash.com"
DEFAULT_SCAN_INTERVAL_MINUTES = 2
DEFAULT_REQUEST_TIMEOUT = 30

AUTH_MODE_BROWSER_SESSION = "browser_session"
AUTH_MODE_TRACKING_URL = "tracking_url"

MAX_ATTRIBUTE_ORDERS = 10
