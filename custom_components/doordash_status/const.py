"""Shared constants for DoorDash Status."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "doordash_status"
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.IMAGE]
VERSION = "0.1.33"
FRONTEND_STATIC_PATH = f"/{DOMAIN}_static"
FRONTEND_CARD_FILENAME = "doordash-status-card.js"
FRONTEND_CARD_URL = f"{FRONTEND_STATIC_PATH}/{FRONTEND_CARD_FILENAME}?v={VERSION}"

CONF_AUTH_MODE = "auth_mode"
CONF_BASE_URL = "base_url"
CONF_BROWSER_COOKIE = "browser_cookie"
CONF_RENDERED_HELPER_URL = "rendered_helper_url"
CONF_TRACKING_URL = "tracking_url"
CONF_SCAN_INTERVAL_MINUTES = "scan_interval_minutes"

DEFAULT_BASE_URL = "https://www.doordash.com"
DEFAULT_SCAN_INTERVAL_MINUTES = 2
DEFAULT_REQUEST_TIMEOUT = 30

AUTH_MODE_BROWSER_SESSION = "browser_session"
AUTH_MODE_TRACKING_URL = "tracking_url"

MAX_ATTRIBUTE_ORDERS = 10
