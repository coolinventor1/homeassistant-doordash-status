"""DoorDash consumer page client."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from html.parser import HTMLParser
import json
import logging
import re
from typing import Any
from urllib.parse import urljoin, urlsplit

import aiohttp
from homeassistant.util import dt as dt_util

from .const import DEFAULT_REQUEST_TIMEOUT

_LOGGER = logging.getLogger(__name__)

_JSON_PARSE_RE = re.compile(
    r"JSON\.parse\((?P<quote>['\"])(?P<payload>.*?)(?P=quote)\)",
    re.DOTALL,
)


class DoorDashApiError(Exception):
    """Base API error for DoorDash status lookups."""


class DoorDashAuthError(DoorDashApiError):
    """Raised when DoorDash rejects the provided session."""


class DoorDashConnectionError(DoorDashApiError):
    """Raised when DoorDash cannot be reached."""


class DoorDashApiClient:
    """Minimal client that scrapes DoorDash order pages."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        base_url: str,
        cookie_header: str | None = None,
        tracking_url: str | None = None,
    ) -> None:
        """Store client dependencies."""
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._tracking_url = tracking_url
        self._headers = {
            "Accept": "text/html,application/xhtml+xml,application/json",
            "User-Agent": "Mozilla/5.0 (Home Assistant DoorDash Status)",
        }
        if cookie_header:
            self._headers["Cookie"] = cookie_header
            self._headers["Referer"] = f"{self._base_url}/"
        self._timeout = aiohttp.ClientTimeout(total=DEFAULT_REQUEST_TIMEOUT)

    async def async_validate(self) -> dict[str, Any]:
        """Validate the configured source and return metadata."""
        if self._tracking_url is not None:
            html, final_url = await self._async_get_text(self._tracking_url)
            if "doordash" not in final_url.host.lower():
                raise DoorDashApiError("Tracking URL did not resolve to DoorDash.")
            orders = _extract_orders_from_html(html, str(final_url))
            return {
                "title": "DoorDash Status",
                "host": final_url.host.lower(),
                "source": "tracking_url",
                "orders_found": len(orders),
            }

        html, final_url = await self._async_get_text(f"{self._base_url}/orders/")
        if _looks_like_login_page(str(final_url), html):
            raise DoorDashAuthError("DoorDash redirected the session to sign in.")

        orders = _extract_orders_from_html(html, str(final_url))
        return {
            "title": f"DoorDash @ {final_url.host}",
            "host": final_url.host.lower(),
            "source": "browser_session",
            "orders_found": len(orders),
        }

    async def async_get_orders(self) -> list[dict[str, Any]]:
        """Fetch and normalize the latest DoorDash orders available to this source."""
        if self._tracking_url is not None:
            html, final_url = await self._async_get_text(self._tracking_url)
            return _extract_orders_from_html(html, str(final_url))

        html, final_url = await self._async_get_text(f"{self._base_url}/orders/")
        if _looks_like_login_page(str(final_url), html):
            raise DoorDashAuthError("DoorDash redirected the session to sign in.")
        return _extract_orders_from_html(html, str(final_url))

    async def _async_get_text(
        self,
        url: str,
    ) -> tuple[str, aiohttp.client_reqrep.URL]:
        """Perform a GET request and return the response text."""
        response: aiohttp.ClientResponse | None = None
        try:
            response = await self._session.get(
                url,
                headers=self._headers,
                timeout=self._timeout,
                allow_redirects=True,
            )
            if response.status in (401, 403):
                raise DoorDashAuthError("DoorDash rejected the supplied credentials.")
            if response.status >= 400:
                detail = await response.text()
                raise DoorDashApiError(
                    f"DoorDash returned HTTP {response.status}: {detail[:200]}"
                )
            return await response.text(), response.url
        except aiohttp.ClientError as err:
            raise DoorDashConnectionError("Could not reach DoorDash.") from err
        except TimeoutError as err:
            raise DoorDashConnectionError("DoorDash request timed out.") from err
        finally:
            if response is not None:
                response.release()


class _ScriptCollector(HTMLParser):
    """Collect script tags from an HTML document."""

    def __init__(self) -> None:
        """Initialize the parser."""
        super().__init__()
        self.scripts: list[dict[str, Any]] = []
        self._current_attrs: dict[str, str] | None = None
        self._current_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Track script tag boundaries."""
        if tag != "script":
            return
        self._current_attrs = {key: value or "" for key, value in attrs}
        self._current_parts = []

    def handle_endtag(self, tag: str) -> None:
        """Finalize a script tag."""
        if tag != "script" or self._current_attrs is None:
            return
        self.scripts.append(
            {
                "attrs": self._current_attrs,
                "content": "".join(self._current_parts),
            }
        )
        self._current_attrs = None
        self._current_parts = []

    def handle_data(self, data: str) -> None:
        """Collect script tag contents."""
        if self._current_attrs is None:
            return
        self._current_parts.append(data)


def _looks_like_login_page(final_url: str, html: str) -> bool:
    """Return whether the response appears to be a sign-in page."""
    lowered_url = final_url.lower()
    lowered_html = html.lower()
    return (
        "identity.doordash.com" in lowered_url
        or "/consumer/login" in lowered_url
        or "sign in to doordash" in lowered_html
    )


def _extract_orders_from_html(html: str, page_url: str) -> list[dict[str, Any]]:
    """Extract order-like payloads from a DoorDash HTML page."""
    collector = _ScriptCollector()
    collector.feed(html)

    orders: dict[str, dict[str, Any]] = {}
    for payload in _extract_json_payloads(collector.scripts):
        for order in _find_orders(payload, page_url):
            orders[order["id"]] = order

    extracted = sorted(
        orders.values(),
        key=_order_sort_key,
        reverse=True,
    )
    _LOGGER.debug("Extracted %s DoorDash orders from %s", len(extracted), page_url)
    return extracted


def _extract_json_payloads(scripts: Iterable[dict[str, Any]]) -> list[Any]:
    """Parse JSON documents embedded in script tags."""
    payloads: list[Any] = []
    for script in scripts:
        attrs = script["attrs"]
        content = script["content"].strip()
        if not content:
            continue

        if attrs.get("id") == "__NEXT_DATA__" or attrs.get("type") == "application/json":
            parsed = _try_json_load(content)
            if parsed is not None:
                payloads.append(parsed)
                continue

        for match in _JSON_PARSE_RE.finditer(content):
            parsed_string = _decode_js_string(match.group("quote"), match.group("payload"))
            if parsed_string is None:
                continue
            parsed = _try_json_load(parsed_string)
            if parsed is not None:
                payloads.append(parsed)
    return payloads


def _try_json_load(value: str) -> Any | None:
    """Safely parse a JSON string."""
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _decode_js_string(quote: str, payload: str) -> str | None:
    """Decode a JavaScript string literal payload into normal text."""
    try:
        return json.loads(f"{quote}{payload}{quote}")
    except json.JSONDecodeError:
        return None


def _find_orders(payload: Any, page_url: str) -> list[dict[str, Any]]:
    """Walk a JSON payload and collect order-like dictionaries."""
    found: list[dict[str, Any]] = []
    stack = [payload]

    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            normalized = _normalize_order_candidate(current, page_url)
            if normalized is not None:
                found.append(normalized)
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)

    return found


def _normalize_order_candidate(candidate: dict[str, Any], page_url: str) -> dict[str, Any] | None:
    """Convert a JSON object into a normalized order summary when it looks like one."""
    order_id = _first_value(
        candidate,
        "order_id",
        "delivery_uuid",
        "external_delivery_id",
        "id",
        "uuid",
    )
    tracking_url = _normalize_url(
        _first_value(candidate, "tracking_url", "trackingUrl", "share_tracking_url"),
        page_url,
    )
    help_url = _normalize_url(_first_value(candidate, "help_url", "helpUrl"), page_url)
    status = _stringify(_extract_status(candidate))
    store_name = _extract_store_name(candidate)
    eta_at, eta_text = _extract_eta(candidate)
    updated_at = _parse_any_datetime(
        _first_value(
            candidate,
            "updated_at",
            "updatedAt",
            "last_updated_at",
            "lastUpdatedAt",
        )
    )
    created_at = _parse_any_datetime(
        _first_value(candidate, "created_at", "createdAt", "placed_at", "placedAt")
    )
    total_display, total_amount = _extract_total(candidate)
    fulfillment_type = _stringify(
        _first_value(candidate, "fulfillment_type", "fulfillmentType", "delivery_type")
    )
    dasher_name = _extract_dasher_name(candidate)
    items = _extract_items(candidate)

    signal_count = sum(
        bool(value)
        for value in (
            status,
            tracking_url,
            store_name,
            eta_at or eta_text,
            total_display or total_amount,
            items,
        )
    )
    if signal_count < 2:
        return None

    synthetic_id = order_id or tracking_url or help_url or f"{store_name}:{status}:{eta_text}"
    if synthetic_id is None:
        return None

    return {
        "id": str(synthetic_id),
        "status": status,
        "store_name": store_name,
        "eta_at": eta_at,
        "eta_text": eta_text,
        "updated_at": updated_at,
        "created_at": created_at,
        "total_display": total_display,
        "total_amount": total_amount,
        "fulfillment_type": fulfillment_type,
        "tracking_url": tracking_url,
        "help_url": help_url,
        "dasher_name": dasher_name,
        "items": items,
    }


def _extract_status(candidate: dict[str, Any]) -> Any:
    """Extract a status-like field."""
    for key in ("order_status", "status", "delivery_status", "status_text", "phase"):
        if key not in candidate:
            continue
        value = candidate[key]
        if isinstance(value, dict):
            return _first_value(value, "label", "text", "display_string", "value")
        return value
    return None


def _extract_store_name(candidate: dict[str, Any]) -> str | None:
    """Extract a merchant or store name from a candidate payload."""
    for key in ("store_name", "merchant_name", "business_name", "name"):
        value = candidate.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    for key in ("store", "merchant", "business", "restaurant"):
        nested = candidate.get(key)
        if isinstance(nested, dict):
            value = _first_value(nested, "name", "business_name", "display_name")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _extract_eta(candidate: dict[str, Any]) -> tuple[datetime | None, str | None]:
    """Extract ETA information as both datetime and display text."""
    for key in (
        "eta",
        "estimated_arrival",
        "estimated_delivery_time",
        "delivery_eta",
        "arrival_time",
        "dropoff_time",
    ):
        if key not in candidate:
            continue
        value = candidate[key]
        if isinstance(value, dict):
            eta_at = _parse_any_datetime(
                _first_value(
                    value,
                    "time",
                    "timestamp",
                    "iso",
                    "value",
                )
            )
            eta_text = _stringify(_first_value(value, "display_string", "label", "text", "value"))
            return eta_at, eta_text
        eta_at = _parse_any_datetime(value)
        return eta_at, _stringify(value)

    eta_range = candidate.get("eta_minutes") or candidate.get("eta_range")
    if isinstance(eta_range, (str, int, float)):
        return None, _stringify(eta_range)

    return None, None


def _extract_total(candidate: dict[str, Any]) -> tuple[str | None, float | None]:
    """Extract total price information from a candidate payload."""
    for key in ("total", "total_price", "order_total", "grand_total"):
        if key not in candidate:
            continue
        return _normalize_money(candidate[key])
    return None, None


def _extract_dasher_name(candidate: dict[str, Any]) -> str | None:
    """Extract a Dasher name when it is present."""
    for key in ("dasher_name", "courier_name", "driver_name"):
        value = candidate.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    for key in ("dasher", "courier", "driver"):
        nested = candidate.get(key)
        if not isinstance(nested, dict):
            continue
        value = _first_value(nested, "name", "display_name", "first_name")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_items(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract a normalized item list from a candidate payload."""
    for key in ("items", "order_items", "cart_items", "line_items"):
        value = candidate.get(key)
        if not isinstance(value, list):
            continue

        items: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            name = _first_value(item, "name", "title", "item_name")
            if not isinstance(name, str) or not name.strip():
                continue
            quantity = item.get("quantity") or item.get("count") or 1
            try:
                quantity = int(quantity)
            except (TypeError, ValueError):
                quantity = 1
            items.append({"name": name.strip(), "quantity": quantity})
        return items

    return []


def _normalize_money(value: Any) -> tuple[str | None, float | None]:
    """Normalize various DoorDash money payloads into display + float amount."""
    if value is None:
        return None, None

    if isinstance(value, dict):
        display = _stringify(
            _first_value(value, "display_string", "formatted_amount", "label")
        )
        numeric = _first_value(value, "amount", "value", "unit_amount", "cents")
        amount = _normalize_money_number(numeric)
        if display is None and amount is not None:
            display = f"${amount:.2f}"
        return display, amount

    if isinstance(value, (int, float)):
        amount = _normalize_money_number(value)
        return f"${amount:.2f}", amount

    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None, None
        number_match = re.search(r"-?\d+(?:\.\d+)?", cleaned.replace(",", ""))
        amount = float(number_match.group(0)) if number_match else None
        return cleaned, amount

    return None, None


def _normalize_money_number(value: Any) -> float | None:
    """Convert DoorDash numeric money values into dollars when possible."""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None

    if abs(numeric) >= 100:
        return numeric / 100
    return numeric


def _parse_any_datetime(value: Any) -> datetime | None:
    """Parse common datetime representations into timezone-aware datetimes."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=dt_util.UTC)
    if isinstance(value, (int, float)):
        return dt_util.utc_from_timestamp(float(value))
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        parsed = dt_util.parse_datetime(stripped)
        if parsed is not None:
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=dt_util.UTC)
        if stripped.isdigit():
            return dt_util.utc_from_timestamp(float(stripped))
    return None


def _normalize_url(value: Any, page_url: str) -> str | None:
    """Normalize absolute or relative URLs."""
    if not isinstance(value, str) or not value.strip():
        return None
    return urljoin(page_url, value.strip())


def _order_sort_key(order: dict[str, Any]) -> tuple[datetime, str]:
    """Return a stable sort key for order recency."""
    timestamp = (
        order.get("updated_at")
        or order.get("eta_at")
        or order.get("created_at")
        or dt_util.utcnow()
    )
    return timestamp, order["id"]


def _first_value(mapping: dict[str, Any], *keys: str) -> Any:
    """Return the first present and non-empty key from a mapping."""
    for key in keys:
        value = mapping.get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _stringify(value: Any) -> str | None:
    """Convert a value to a stripped string when sensible."""
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, (int, float)):
        return str(value)
    return None
