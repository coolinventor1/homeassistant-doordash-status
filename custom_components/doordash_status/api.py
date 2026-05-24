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
_US_STATE_CODES = {
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "IA",
    "ID",
    "IL",
    "IN",
    "KS",
    "KY",
    "LA",
    "MA",
    "MD",
    "ME",
    "MI",
    "MN",
    "MO",
    "MS",
    "MT",
    "NC",
    "ND",
    "NE",
    "NH",
    "NJ",
    "NM",
    "NV",
    "NY",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VA",
    "VT",
    "WA",
    "WI",
    "WV",
    "WY",
}

_JSON_PARSE_RE = re.compile(
    r"JSON\.parse\((?P<quote>['\"])(?P<payload>.*?)(?P=quote)\)",
    re.DOTALL,
)
_NEXT_PUSH_RE = re.compile(
    r"self\.__next_f\.push\((?P<payload>\[.*\])\)\s*;?\s*$",
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
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/136.0.0.0 Safari/537.36"
            ),
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

        html, final_url = await self._async_get_orders_page()

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

        html, final_url = await self._async_get_orders_page()
        return _extract_orders_from_html(html, str(final_url))

    async def _async_get_orders_page(self) -> tuple[str, aiohttp.client_reqrep.URL]:
        """Fetch a logged-in DoorDash orders page using a few likely routes."""
        candidates = (
            f"{self._base_url}/orders/",
            f"{self._base_url}/orders",
            f"{self._base_url}/consumer/orders/",
            f"{self._base_url}/consumer/orders",
        )
        last_auth_error: DoorDashAuthError | None = None

        for candidate in candidates:
            try:
                html, final_url = await self._async_get_text(candidate)
            except DoorDashAuthError as err:
                last_auth_error = err
                continue

            if _looks_like_login_page(str(final_url), html):
                _LOGGER.debug(
                    "DoorDash candidate %s redirected to login-like page at %s",
                    candidate,
                    final_url,
                )
                continue

            return html, final_url

        if last_auth_error is not None:
            raise last_auth_error
        raise DoorDashAuthError("DoorDash redirected the session to sign in.")

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
    _LOGGER.debug(
        "Extracted %s DoorDash orders from %s using %s script tags",
        len(extracted),
        page_url,
        len(collector.scripts),
    )
    return extracted


def _extract_json_payloads(scripts: Iterable[dict[str, Any]]) -> list[Any]:
    """Parse JSON documents embedded in script tags."""
    payloads: list[Any] = []
    next_chunks: dict[str, list[str]] = {}
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

        next_chunk = _parse_next_push_chunk(content)
        if next_chunk is not None:
            chunk_id, chunk_text = next_chunk
            next_chunks.setdefault(chunk_id, []).append(chunk_text)

    for chunk_id, parts in next_chunks.items():
        joined = "".join(parts)
        extracted = _extract_structured_values_from_text(joined)
        if extracted:
            payloads.extend(extracted)
            continue
        _LOGGER.debug("DoorDash Next.js chunk %s did not yield structured values", chunk_id)
    return payloads


def _parse_next_push_chunk(content: str) -> tuple[str, str] | None:
    """Parse a Next.js App Router hydration chunk from a script body."""
    match = _NEXT_PUSH_RE.match(content)
    if match is None:
        return None

    try:
        payload = json.loads(match.group("payload"))
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, list) or len(payload) < 2:
        return None

    chunk_id = str(payload[0])
    chunk_text = payload[1]
    if not isinstance(chunk_text, str):
        return None

    return chunk_id, chunk_text


def _extract_structured_values_from_text(text: str) -> list[Any]:
    """Extract JSON-like objects from a raw hydration chunk string."""
    values: list[Any] = []

    parsed = _try_json_load(text)
    if parsed is not None:
        values.append(parsed)

    for segment in _iter_json_substrings(text):
        parsed_segment = _try_json_load(segment)
        if parsed_segment is not None:
            values.append(parsed_segment)

    colon_index = text.find(":{")
    if colon_index != -1:
        parsed_suffix = _try_json_load(text[colon_index + 1 :])
        if parsed_suffix is not None:
            values.append(parsed_suffix)

    return values


def _iter_json_substrings(text: str) -> Iterable[str]:
    """Yield candidate object/array substrings from mixed hydration text."""
    opener_to_closer = {"{": "}", "[": "]"}
    for index, char in enumerate(text):
        if char not in opener_to_closer:
            continue

        closer = opener_to_closer[char]
        depth = 0
        in_string = False
        escaped = False

        for end_index in range(index, len(text)):
            current = text[end_index]
            if in_string:
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == "\"":
                    in_string = False
                continue

            if current == "\"":
                in_string = True
                continue

            if current == char:
                depth += 1
            elif current == closer:
                depth -= 1
                if depth == 0:
                    candidate = text[index : end_index + 1]
                    if _looks_structured_and_relevant(candidate):
                        yield candidate
                    break


def _looks_structured_and_relevant(candidate: str) -> bool:
    """Return whether a JSON substring is likely to contain order data."""
    lowered = candidate.lower()
    return any(
        token in lowered
        for token in (
            "order",
            "merchant",
            "store",
            "tracking",
            "delivery",
            "dasher",
            "subtotal",
            "total",
        )
    )


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
        "orderId",
        "order_uuid",
        "orderUuid",
        "delivery_uuid",
        "deliveryUuid",
        "delivery_id",
        "deliveryId",
        "external_delivery_id",
        "externalDeliveryId",
        "id",
        "uuid",
    )
    tracking_url = _normalize_url(
        _first_value(candidate, "tracking_url", "trackingUrl", "share_tracking_url"),
        page_url,
    )
    help_url = _normalize_url(_first_value(candidate, "help_url", "helpUrl"), page_url)
    status = _stringify(_extract_status(candidate))
    status = _normalize_status(status)
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
        _first_value(
            candidate,
            "created_at",
            "createdAt",
            "submitted_at",
            "submittedAt",
            "placed_at",
            "placedAt",
            "order_time",
            "orderTime",
        )
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
            created_at,
            updated_at,
            total_display or total_amount,
            items,
            order_id,
        )
    )
    if signal_count < 2 or (store_name is None and tracking_url is None and order_id is None):
        return None

    confidence = _score_order_candidate(
        order_id=order_id,
        tracking_url=tracking_url,
        store_name=store_name,
        status=status,
        created_at=created_at,
        updated_at=updated_at,
        total_display=total_display,
        total_amount=total_amount,
        items=items,
    )
    if confidence < 3:
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
        "confidence": confidence,
    }


def _extract_status(candidate: dict[str, Any]) -> Any:
    """Extract a status-like field."""
    for key in (
        "order_status",
        "orderStatus",
        "status",
        "delivery_status",
        "deliveryStatus",
        "status_text",
        "statusText",
        "phase",
        "state",
    ):
        if key not in candidate:
            continue
        value = candidate[key]
        if isinstance(value, dict):
            return _first_value(value, "label", "text", "display_string", "value")
        return value
    return None


def _normalize_status(status: str | None) -> str | None:
    """Drop values that look like location codes rather than order statuses."""
    if status is None:
        return None

    normalized = status.strip()
    if not normalized:
        return None

    if normalized.upper() in _US_STATE_CODES:
        return None

    if len(normalized) <= 2 and normalized.isalpha():
        return None

    if normalized.isdigit():
        return None

    return normalized


def _score_order_candidate(
    *,
    order_id: Any,
    tracking_url: str | None,
    store_name: str | None,
    status: str | None,
    created_at: datetime | None,
    updated_at: datetime | None,
    total_display: str | None,
    total_amount: float | None,
    items: list[dict[str, Any]],
) -> int:
    """Return a rough confidence score for whether a payload is a real order."""
    score = 0

    if store_name:
        score += 3
    if tracking_url:
        score += 2
    if order_id:
        score += 1
    if created_at or updated_at:
        score += 1
    if total_display or total_amount is not None:
        score += 1
    if items:
        score += 1
    if status:
        score += 1

    return score


def _extract_store_name(candidate: dict[str, Any]) -> str | None:
    """Extract a merchant or store name from a candidate payload."""
    for key in (
        "store_name",
        "storeName",
        "merchant_name",
        "merchantName",
        "business_name",
        "businessName",
        "name",
    ):
        value = candidate.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    for key in ("store", "merchant", "business", "restaurant", "store_info", "storeInfo"):
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
        "eta_text",
        "etaText",
        "estimated_arrival",
        "estimatedArrival",
        "estimated_delivery_time",
        "estimatedDeliveryTime",
        "delivery_eta",
        "deliveryEta",
        "arrival_time",
        "arrivalTime",
        "dropoff_time",
        "dropoffTime",
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
    for key in (
        "total",
        "total_price",
        "totalPrice",
        "order_total",
        "orderTotal",
        "grand_total",
        "grandTotal",
        "subtotal",
        "subTotal",
        "display_total",
        "displayTotal",
    ):
        if key not in candidate:
            continue
        return _normalize_money(candidate[key])
    return None, None


def _extract_dasher_name(candidate: dict[str, Any]) -> str | None:
    """Extract a Dasher name when it is present."""
    for key in (
        "dasher_name",
        "dasherName",
        "courier_name",
        "courierName",
        "driver_name",
        "driverName",
    ):
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
    for key in (
        "items",
        "order_items",
        "orderItems",
        "cart_items",
        "cartItems",
        "line_items",
        "lineItems",
    ):
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


def _order_sort_key(order: dict[str, Any]) -> tuple[int, datetime, str]:
    """Return a stable sort key for order recency."""
    timestamp = (
        order.get("updated_at")
        or order.get("eta_at")
        or order.get("created_at")
        or dt_util.utcnow()
    )
    return order.get("confidence", 0), timestamp, order["id"]


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
