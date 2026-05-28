"""DoorDash consumer page client."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import json
import logging
import re
from typing import Any
from urllib.parse import urljoin, urlsplit

try:
    import aiohttp
except ModuleNotFoundError:  # pragma: no cover - local debug harness can run without aiohttp
    aiohttp = None

try:
    from homeassistant.util import dt as dt_util
except ModuleNotFoundError:  # pragma: no cover - local debug harness fallback
    class _FallbackDateTimeUtil:
        """Small subset of Home Assistant datetime helpers for local debugging."""

        UTC = timezone.utc

        @staticmethod
        def now() -> datetime:
            """Return the current local time."""
            return datetime.now().astimezone()

        @staticmethod
        def utcnow() -> datetime:
            """Return the current UTC time."""
            return datetime.now(timezone.utc)

        @staticmethod
        def parse_datetime(value: str) -> datetime | None:
            """Parse an ISO-like datetime string."""
            normalized = value.strip()
            if not normalized:
                return None
            if normalized.endswith("Z"):
                normalized = f"{normalized[:-1]}+00:00"
            try:
                parsed = datetime.fromisoformat(normalized)
            except ValueError:
                return None
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed

        @staticmethod
        def as_local(value: datetime) -> datetime:
            """Convert a datetime into the current local timezone."""
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.astimezone()

        @classmethod
        def start_of_local_day(cls, value: datetime) -> datetime:
            """Return local midnight for the supplied datetime."""
            local = cls.as_local(value)
            return local.replace(hour=0, minute=0, second=0, microsecond=0)

        @staticmethod
        def utc_from_timestamp(value: float) -> datetime:
            """Convert a POSIX timestamp into UTC."""
            return datetime.fromtimestamp(value, tz=timezone.utc)

    dt_util = _FallbackDateTimeUtil()

try:
    from .const import DEFAULT_REQUEST_TIMEOUT
except ImportError:  # pragma: no cover - direct file import for local debugging
    DEFAULT_REQUEST_TIMEOUT = 30

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
_STATUS_KEYS = (
    "order_status",
    "orderStatus",
    "status",
    "delivery_status",
    "deliveryStatus",
    "fulfillment_status",
    "fulfillmentStatus",
    "status_text",
    "statusText",
    "status_description",
    "statusDescription",
    "progress_label",
    "progressLabel",
    "current_status",
    "currentStatus",
    "phase",
    "state",
)
_STATUS_NORMALIZATION_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Cancelled",
        (
            "cancel",
            "unable to complete",
            "could not complete",
            "couldn't complete",
            "failed",
            "refund",
        ),
    ),
    (
        "Issue",
        (
            "delay",
            "problem",
            "issue",
            "support",
            "unavailable",
            "substitute",
        ),
    ),
    (
        "Delivered",
        (
            "deliver",
            "complete",
            "completed",
            "dropped off",
            "success",
            "fulfilled",
            "finished",
        ),
    ),
    (
        "Arriving soon",
        (
            "arriving soon",
            "almost there",
            "nearby",
            "close by",
            "minutes away",
        ),
    ),
    (
        "On the way",
        (
            "on the way",
            "out for delivery",
            "picked up",
            "en route",
            "heading to you",
            "heading your way",
        ),
    ),
    (
        "Picking up",
        (
            "picking up",
            "at store",
            "arrived at store",
            "waiting at store",
            "heading to store",
            "ready for pickup",
        ),
    ),
    (
        "Dasher assigned",
        (
            "dasher assigned",
            "driver assigned",
            "courier assigned",
            "shopper assigned",
            "your dasher",
            "your driver",
        ),
    ),
    (
        "Preparing",
        (
            "prepar",
            "being made",
            "making your order",
            "cooking",
            "shopping",
            "gathering items",
            "working on your order",
        ),
    ),
    (
        "Confirmed",
        (
            "confirm",
            "accepted",
            "merchant accepted",
            "store accepted",
            "scheduled",
        ),
    ),
    (
        "Placed",
        (
            "placed",
            "order received",
            "received your order",
            "we got your order",
            "submitted",
            "sending to merchant",
        ),
    ),
)
_ACTIVE_ORDER_STATUSES = {
    "Placed",
    "Confirmed",
    "Preparing",
    "Dasher assigned",
    "Picking up",
    "On the way",
    "Arriving soon",
}
_TERMINAL_ORDER_STATUSES = {"Delivered", "Cancelled", "Issue"}
_FALLBACK_ORDER_STATUSES = {"Unknown", "Delivered"}


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
        if aiohttp is None:
            raise DoorDashConnectionError(
                "aiohttp is required to use DoorDashApiClient directly. "
                "Use the local debug script for parser-only debugging."
            )
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


class _VisibleTextCollector(HTMLParser):
    """Collect visible text chunks from an HTML document."""

    def __init__(self) -> None:
        """Initialize the parser."""
        super().__init__()
        self.text_chunks: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Ignore script/style content."""
        if tag in {"script", "style", "noscript"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        """Resume collection after ignored tags."""
        if tag in {"script", "style", "noscript"} and self._ignored_depth > 0:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        """Collect non-empty visible text chunks."""
        if self._ignored_depth > 0:
            return
        cleaned = " ".join(data.split())
        if cleaned:
            self.text_chunks.append(cleaned)


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

    candidates: list[dict[str, Any]] = []
    for payload in _extract_json_payloads(collector.scripts):
        candidates.extend(_find_orders(payload, page_url))
    candidates.extend(_extract_order_summaries_from_text(html, page_url))

    orders = _merge_orders(candidates)

    extracted = sorted(
        orders,
        key=_order_sort_key,
        reverse=True,
    )
    _LOGGER.debug(
        "Extracted %s DoorDash orders from %s using %s script tags (%s raw candidates)",
        len(extracted),
        page_url,
        len(collector.scripts),
        len(candidates),
    )
    return extracted


def extract_orders_from_html(html: str, page_url: str) -> list[dict[str, Any]]:
    """Public wrapper used by the local debug harness."""
    return _extract_orders_from_html(html, page_url)


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


def _extract_order_summaries_from_text(html: str, page_url: str) -> list[dict[str, Any]]:
    """Extract order-history summaries from visible DoorDash page text."""
    collector = _VisibleTextCollector()
    collector.feed(html)

    chunks = _sanitize_visible_chunks(collector.text_chunks)
    summaries: list[dict[str, Any]] = []
    for index, _chunk in enumerate(chunks):
        meta, meta_span = _parse_visible_order_meta(chunks, index)
        if meta is None or index == 0:
            continue

        store_name = chunks[index - 1]
        if not _looks_like_store_name(store_name):
            continue

        date_text = chunks[index - 2] if index >= 2 else None
        items_line = chunks[index + meta_span] if index + meta_span < len(chunks) else None
        items = _parse_items_line(items_line, meta["item_count"])
        created_at = _parse_relative_order_date(date_text)
        order_id = f"{store_name}|{meta['total_display']}|{date_text or index}"

        summaries.append(
            {
                "id": order_id,
                "source_order_id": None,
                "raw_status": None,
                "status": "Delivered",
                "store_name": store_name,
                "eta_at": None,
                "eta_text": None,
                "delivered_at": None,
                "updated_at": created_at,
                "created_at": created_at,
                "total_display": meta["total_display"],
                "total_amount": meta["total_amount"],
                "subtotal_display": None,
                "subtotal_amount": None,
                "tip_display": None,
                "tip_amount": None,
                "tax_display": None,
                "tax_amount": None,
                "fees_display": None,
                "fees_amount": None,
                "fulfillment_type": meta["fulfillment_type"],
                "tracking_url": None,
                "help_url": None,
                "dasher_name": None,
                "items": items,
                "item_count": meta["item_count"],
                "confidence": 8,
                "status_candidates": [{"key": "visible_order_history", "value": "Delivered"}],
                "money_candidates": [
                    {
                        "key": "visible_order_history",
                        "label": "total",
                        "display": meta["total_display"],
                        "amount": meta["total_amount"],
                        "score": 10,
                    }
                ],
            }
        )

    return summaries


def _sanitize_visible_chunks(chunks: list[str]) -> list[str]:
    """Drop boilerplate text chunks that interfere with history parsing."""
    ignored = {"Icon Loading"}
    return [chunk for chunk in chunks if chunk not in ignored]


def _parse_visible_order_meta(
    chunks: list[str],
    index: int,
) -> tuple[dict[str, Any] | None, int]:
    """Parse a visible order meta line that may be split across multiple chunks."""
    if index >= len(chunks):
        return None, 0

    start = chunks[index]
    if not _looks_like_money_fragment(start):
        return None, 0

    candidates: list[tuple[str, int]] = [(start, 1)]
    if index + 1 < len(chunks) and _looks_like_item_count_fragment(chunks[index + 1]):
        candidates.append((f"{start} {chunks[index + 1]}", 2))
        if index + 2 < len(chunks) and _looks_like_fulfillment_fragment(chunks[index + 2]):
            candidates.append((f"{start} {chunks[index + 1]} {chunks[index + 2]}", 3))

    for text, span in reversed(candidates):
        parsed = _parse_order_meta_line(text)
        if parsed is not None:
            return parsed, span

    return None, 0


def _parse_order_meta_line(text: str) -> dict[str, Any] | None:
    """Parse a visible order meta line like '$48.22 • 3 items • Personal'."""
    parts = [part.strip() for part in re.split(r"\s*[•·]\s*", text) if part.strip()]
    if len(parts) < 2:
        return None

    total_display = parts[0]
    if not _looks_like_money_string(total_display):
        return None

    item_count_match = re.search(r"(\d+)\s+items?", parts[1], re.IGNORECASE)
    if item_count_match is None:
        return None

    total_amount = _normalize_money_number(total_display)
    item_count = int(item_count_match.group(1))
    fulfillment_type = parts[2] if len(parts) >= 3 else None

    return {
        "total_display": total_display,
        "total_amount": total_amount,
        "item_count": item_count,
        "fulfillment_type": fulfillment_type,
    }


def _looks_like_money_fragment(text: str) -> bool:
    """Return whether a visible text chunk can start an order meta line."""
    return _looks_like_money_string(text.strip().rstrip("•").strip())


def _looks_like_item_count_fragment(text: str) -> bool:
    """Return whether a chunk looks like the middle order-meta item count."""
    return re.fullmatch(r"\d+\s+items?", text.strip(), re.IGNORECASE) is not None


def _looks_like_fulfillment_fragment(text: str) -> bool:
    """Return whether a chunk looks like the trailing order-meta fulfillment segment."""
    cleaned = text.strip()
    if cleaned.startswith("•"):
        cleaned = cleaned.lstrip("•").strip()
    return cleaned in {"Personal", "Business", "Group"}


def _parse_items_line(text: str | None, expected_count: int) -> list[dict[str, Any]]:
    """Parse an item-summary line separated by bullets."""
    if text is None:
        return [{"name": f"Item {index + 1}", "quantity": 1} for index in range(expected_count)]

    parts = [part.strip() for part in re.split(r"\s*[•·]\s*", text) if part.strip()]
    if not parts:
        return [{"name": f"Item {index + 1}", "quantity": 1} for index in range(expected_count)]

    items = [{"name": part, "quantity": 1} for part in parts[:expected_count]]
    overflow = expected_count - len(items)
    if overflow > 0 and items:
        items[-1]["quantity"] += overflow
    return items


def _looks_like_store_name(value: str | None) -> bool:
    """Return whether a visible text chunk looks like a merchant name."""
    if value is None:
        return False

    cleaned = value.strip()
    if not cleaned:
        return False
    if _looks_like_money_string(cleaned):
        return False
    if re.search(r"\d+\s+items?", cleaned, re.IGNORECASE):
        return False
    if cleaned.lower().startswith(("yesterday", "today")):
        return False
    return True


def _parse_relative_order_date(text: str | None) -> datetime | None:
    """Parse visible relative order dates like 'Yesterday, May 24'."""
    if text is None:
        return None

    lowered = text.lower()
    now = dt_util.now()
    if lowered.startswith("today"):
        return dt_util.start_of_local_day(now)
    if lowered.startswith("yesterday"):
        return dt_util.start_of_local_day(now - timedelta(days=1))

    month_day_match = re.search(r"([A-Za-z]+)\s+(\d{1,2})", text)
    if month_day_match is None:
        return None

    try:
        parsed = datetime.strptime(
            f"{month_day_match.group(1)} {month_day_match.group(2)} {now.year}",
            "%B %d %Y",
        )
    except ValueError:
        try:
            parsed = datetime.strptime(
                f"{month_day_match.group(1)} {month_day_match.group(2)} {now.year}",
                "%b %d %Y",
            )
        except ValueError:
            return None

    local_tz = dt_util.as_local(now).tzinfo or dt_util.UTC
    return parsed.replace(tzinfo=local_tz)


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


def _merge_orders(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge multiple fragments that appear to describe the same order."""
    merged: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=_order_sort_key, reverse=True):
        match = next((order for order in merged if _orders_match(order, candidate)), None)
        if match is None:
            merged.append(dict(candidate))
            continue
        _merge_order_into(match, candidate)
    return merged


def _orders_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Return whether two normalized candidates likely describe the same order."""
    left_source_id = left.get("source_order_id")
    right_source_id = right.get("source_order_id")
    if left_source_id and right_source_id and left_source_id == right_source_id:
        return True

    left_tracking = left.get("tracking_url")
    right_tracking = right.get("tracking_url")
    if left_tracking and right_tracking and left_tracking == right_tracking:
        return True

    left_store = left.get("store_name")
    right_store = right.get("store_name")
    if left_store and right_store and left_store == right_store:
        left_time = left.get("created_at") or left.get("updated_at")
        right_time = right.get("created_at") or right.get("updated_at")
        if left_time and right_time and abs((left_time - right_time).total_seconds()) <= 7200:
            return True

        if (
            left_time
            and right_time
            and (_is_visible_summary_candidate(left) or _is_visible_summary_candidate(right))
            and dt_util.as_local(left_time).date() == dt_util.as_local(right_time).date()
        ):
            return True

        left_total = left.get("total_amount")
        right_total = right.get("total_amount")
        if (
            left_total is not None
            and right_total is not None
            and abs(left_total - right_total) < 0.01
        ):
            return True

    return False


def _merge_order_into(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    """Merge a normalized order fragment into the target order."""
    for key in (
        "source_order_id",
        "tracking_url",
        "help_url",
        "store_name",
        "eta_at",
        "eta_text",
        "delivered_at",
        "dasher_name",
        "raw_status",
    ):
        if target.get(key) is None and incoming.get(key) is not None:
            target[key] = incoming[key]

    target_fulfillment = target.get("fulfillment_type")
    incoming_fulfillment = incoming.get("fulfillment_type")
    if incoming_fulfillment and (
        target_fulfillment is None or str(target_fulfillment).strip().casefold() == "any"
    ):
        target["fulfillment_type"] = incoming_fulfillment

    if (
        incoming.get("total_display") is not None
        and (
            target.get("total_display") is None
            or _display_needs_cleanup(target.get("total_display"))
        )
    ):
        target["total_display"] = incoming["total_display"]
    if target.get("total_amount") is None and incoming.get("total_amount") is not None:
        target["total_amount"] = incoming["total_amount"]
    for component in ("subtotal", "tip", "tax", "fees"):
        display_key = f"{component}_display"
        amount_key = f"{component}_amount"
        if (
            incoming.get(display_key) is not None
            and (
                target.get(display_key) is None
                or _display_needs_cleanup(target.get(display_key))
            )
        ):
            target[display_key] = incoming[display_key]
        if target.get(amount_key) is None and incoming.get(amount_key) is not None:
            target[amount_key] = incoming[amount_key]

    if _should_replace_items(target, incoming):
        target["items"] = incoming["items"]
    if target.get("item_count") is None and incoming.get("item_count") is not None:
        target["item_count"] = incoming["item_count"]
    elif (
        isinstance(target.get("item_count"), int)
        and isinstance(incoming.get("item_count"), int)
        and incoming["item_count"] > target["item_count"]
    ):
        target["item_count"] = incoming["item_count"]

    target["status_candidates"] = _merge_candidate_lists(
        target.get("status_candidates", []),
        incoming.get("status_candidates", []),
    )
    target["money_candidates"] = _merge_candidate_lists(
        target.get("money_candidates", []),
        incoming.get("money_candidates", []),
    )

    existing_status = target.get("status")
    incoming_status = incoming.get("status")
    if _should_replace_status(target, incoming):
        target["status"] = incoming_status or existing_status
        if incoming.get("raw_status") is not None:
            target["raw_status"] = incoming["raw_status"]

    target["confidence"] = max(target.get("confidence", 0), incoming.get("confidence", 0))

    target_created = target.get("created_at")
    incoming_created = incoming.get("created_at")
    incoming_is_summary = _is_visible_summary_candidate(incoming)
    if (
        target_created is None
        or (
            incoming_created is not None
            and incoming_created < target_created
            and not (
                incoming_is_summary
                and target_created is not None
                and not _is_midnight_timestamp(target_created)
            )
        )
    ):
        target["created_at"] = incoming_created or target_created

    target_updated = target.get("updated_at")
    incoming_updated = incoming.get("updated_at")
    if (
        target_updated is None
        or (
            incoming_updated is not None
            and incoming_updated > target_updated
            and not (
                incoming_is_summary
                and target_updated is not None
                and not _is_midnight_timestamp(target_updated)
            )
        )
    ):
        if not (
            incoming_is_summary
            and incoming_updated is not None
            and _is_midnight_timestamp(incoming_updated)
            and target_updated is None
        ):
            target["updated_at"] = incoming_updated or target_updated


def _merge_candidate_lists(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge diagnostic candidate lists while preserving order and uniqueness."""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in [*left, *right]:
        marker = json.dumps(candidate, sort_keys=True, default=str)
        if marker in seen:
            continue
        seen.add(marker)
        merged.append(candidate)
    return merged[:10]


def _should_replace_status(target: dict[str, Any], incoming: dict[str, Any]) -> bool:
    """Return whether an incoming status is more trustworthy than the current one."""
    incoming_status = incoming.get("status")
    if incoming_status is None:
        return False

    existing_status = target.get("status")
    if existing_status is None:
        return True

    existing_raw = _normalize_status(_stringify(target.get("raw_status")))
    incoming_raw = _normalize_status(_stringify(incoming.get("raw_status")))

    if existing_status == incoming_status and existing_raw is None and incoming_raw is not None:
        return True

    if existing_status == "Unknown" and incoming_status != "Unknown":
        return True

    if (
        existing_status in _FALLBACK_ORDER_STATUSES
        and incoming_status not in _FALLBACK_ORDER_STATUSES
    ):
        return True

    if existing_raw is None and incoming_raw is not None:
        return True

    return False


def _is_visible_summary_candidate(order: dict[str, Any]) -> bool:
    """Return whether an order candidate came from visible order-history text."""
    return any(
        candidate.get("key") == "visible_order_history"
        for candidate in order.get("status_candidates", [])
        if isinstance(candidate, dict)
    )


def _should_replace_items(target: dict[str, Any], incoming: dict[str, Any]) -> bool:
    """Return whether incoming items look more complete than the current ones."""
    incoming_items = incoming.get("items")
    if not incoming_items:
        return False

    target_items = target.get("items")
    if not target_items:
        return True

    incoming_count = _effective_item_count(incoming)
    target_count = _effective_item_count(target)
    if incoming_count > target_count:
        return True

    if len(incoming_items) > len(target_items) and not _items_are_placeholders(incoming_items):
        return True

    return _items_are_placeholders(target_items) and not _items_are_placeholders(incoming_items)


def _display_needs_cleanup(display: str | None) -> bool:
    """Return whether a display string looks less polished than a replacement."""
    return isinstance(display, str) and display.strip().startswith("$$")


def _is_midnight_timestamp(value: datetime) -> bool:
    """Return whether a timestamp looks like a date-only midnight placeholder."""
    local = dt_util.as_local(value)
    return (
        local.hour == 0
        and local.minute == 0
        and local.second == 0
        and local.microsecond == 0
    )


def _effective_item_count(order: dict[str, Any]) -> int:
    """Return the best available item count for an order candidate."""
    count = order.get("item_count")
    if isinstance(count, int) and count >= 0:
        return count
    items = order.get("items") or []
    if isinstance(items, list):
        return _count_items(items)
    return 0


def _items_are_placeholders(items: list[dict[str, Any]]) -> bool:
    """Return whether an item list only contains synthetic placeholder entries."""
    return all(
        isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and item["name"].startswith("Item ")
        for item in items
    )


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
    raw_status = _normalize_status(_stringify(_extract_status(candidate)))
    store_name = _extract_store_name(candidate)
    eta_at, eta_text = _extract_eta(candidate)
    delivered_at = _extract_delivered_at(candidate)
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
    total_display = _normalize_total_display(total_display, total_amount)
    subtotal_display, subtotal_amount = _extract_subtotal(candidate)
    subtotal_display = _normalize_total_display(subtotal_display, subtotal_amount)
    tip_display, tip_amount = _extract_tip(candidate)
    tip_display = _normalize_total_display(tip_display, tip_amount)
    tax_display, tax_amount = _extract_tax(candidate)
    tax_display = _normalize_total_display(tax_display, tax_amount)
    fees_display, fees_amount = _extract_fees(candidate)
    fees_display = _normalize_total_display(fees_display, fees_amount)
    fulfillment_type = _stringify(
        _first_value(candidate, "fulfillment_type", "fulfillmentType", "delivery_type")
    )
    dasher_name = _extract_dasher_name(candidate)
    items = _extract_items(candidate)
    status_candidates = _collect_status_candidates(candidate)
    money_candidates = _collect_money_candidates(candidate)

    if not _looks_like_concrete_order_record(
        candidate,
        order_id=order_id,
        tracking_url=tracking_url,
        created_at=created_at,
        updated_at=updated_at,
        eta_at=eta_at,
        eta_text=eta_text,
    ):
        return None

    signal_count = sum(
        bool(value)
        for value in (
            raw_status,
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
        status=raw_status,
        created_at=created_at,
        updated_at=updated_at,
        total_display=total_display,
        total_amount=total_amount,
        items=items,
    )
    if confidence < 3:
        return None

    status = _derive_status(
        raw_status=raw_status,
        created_at=created_at,
        updated_at=updated_at,
        eta_at=eta_at,
        eta_text=eta_text,
        dasher_name=dasher_name,
    )

    synthetic_id = order_id or tracking_url or help_url or f"{store_name}:{status}:{eta_text}"
    if synthetic_id is None:
        return None

    return {
        "id": str(synthetic_id),
        "source_order_id": str(order_id) if order_id is not None else None,
        "raw_status": raw_status,
        "status": status,
        "store_name": store_name,
        "eta_at": eta_at,
        "eta_text": eta_text,
        "delivered_at": delivered_at,
        "updated_at": updated_at,
        "created_at": created_at,
        "total_display": total_display,
        "total_amount": total_amount,
        "subtotal_display": subtotal_display,
        "subtotal_amount": subtotal_amount,
        "tip_display": tip_display,
        "tip_amount": tip_amount,
        "tax_display": tax_display,
        "tax_amount": tax_amount,
        "fees_display": fees_display,
        "fees_amount": fees_amount,
        "fulfillment_type": fulfillment_type,
        "tracking_url": tracking_url,
        "help_url": help_url,
        "dasher_name": dasher_name,
        "items": items,
        "item_count": _count_items(items) if items else None,
        "confidence": confidence,
        "status_candidates": status_candidates,
        "money_candidates": money_candidates,
    }


def _looks_like_concrete_order_record(
    candidate: dict[str, Any],
    *,
    order_id: Any,
    tracking_url: str | None,
    created_at: datetime | None,
    updated_at: datetime | None,
    eta_at: datetime | None,
    eta_text: str | None,
) -> bool:
    """Return whether a JSON node looks like a concrete order rather than a page wrapper."""
    if order_id is not None or tracking_url is not None:
        return True
    if created_at is not None or updated_at is not None or eta_at is not None or eta_text:
        return True

    # Reject large wrapper nodes that only expose order-like data via deep nesting.
    if any(key in candidate for key in ("platformProps", "children", "initialReactQueryState")):
        return False

    return False


def _extract_status(candidate: dict[str, Any]) -> Any:
    """Extract a status-like field."""
    for key in _STATUS_KEYS:
        if key not in candidate:
            continue
        value = candidate[key]
        if isinstance(value, dict):
            return _first_value(value, "label", "text", "display_string", "value")
        return value

    nested = _find_nested_value(candidate, *_STATUS_KEYS)
    if isinstance(nested, dict):
        return _first_value(
            nested,
            "label",
            "text",
            "display_string",
            "displayString",
            "description",
            "value",
        )
    return nested


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


def _normalize_status_label(raw_status: str | None) -> str | None:
    """Map a raw DoorDash status string onto a stable user-facing lifecycle label."""
    cleaned = _normalize_status(raw_status)
    if cleaned is None:
        return None

    lowered = cleaned.casefold()
    for normalized, keywords in _STATUS_NORMALIZATION_RULES:
        if any(keyword in lowered for keyword in keywords):
            return normalized

    return "Unknown"


def _collect_status_candidates(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect status-like fields for debugging the latest order mapping."""
    collected: list[dict[str, Any]] = []
    queue: list[Any] = [candidate]
    seen_nodes: set[int] = set()
    seen_values: set[str] = set()

    while queue:
        current = queue.pop(0)
        if not isinstance(current, dict):
            continue

        current_id = id(current)
        if current_id in seen_nodes:
            continue
        seen_nodes.add(current_id)

        for key, value in current.items():
            if key in _STATUS_KEYS:
                if isinstance(value, dict):
                    normalized = _normalize_status(
                        _stringify(
                            _first_value(
                                value,
                                "label",
                                "text",
                                "display_string",
                                "displayString",
                                "description",
                                "value",
                            )
                        )
                    )
                else:
                    normalized = _normalize_status(_stringify(value))

                if normalized is not None:
                    marker = f"{key}:{normalized}"
                    if marker not in seen_values:
                        seen_values.add(marker)
                        collected.append({"key": key, "value": normalized})

            if isinstance(value, dict):
                queue.append(value)
            elif isinstance(value, list):
                queue.extend(item for item in value if isinstance(item, dict))

    return collected[:10]


def _derive_status(
    *,
    raw_status: str | None,
    created_at: datetime | None,
    updated_at: datetime | None,
    eta_at: datetime | None,
    eta_text: str | None,
    dasher_name: str | None,
) -> str | None:
    """Provide a conservative fallback status when DoorDash omits one."""
    normalized = _normalize_status_label(raw_status)
    if normalized not in (None, "Unknown"):
        return normalized

    now = dt_util.utcnow()
    reference_time = updated_at or created_at

    lowered_eta_text = eta_text.casefold() if eta_text else ""
    if any(token in lowered_eta_text for token in ("arriving", "nearby", "away", "almost")):
        return "Arriving soon"

    if eta_at is not None and eta_at >= now:
        if dasher_name:
            if eta_at - now <= timedelta(minutes=10):
                return "Arriving soon"
            return "On the way"
        return "Preparing"

    if dasher_name:
        return "Dasher assigned"

    if reference_time is None:
        return normalized or "Unknown"

    if now - reference_time >= timedelta(hours=3):
        return "Delivered"

    return normalized or "Unknown"


def is_active_order_status(status: str | None) -> bool:
    """Return whether a normalized DoorDash status is still active."""
    return status in _ACTIVE_ORDER_STATUSES


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
        "restaurant_name",
        "restaurantName",
    ):
        value = candidate.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    for key in (
        "store",
        "merchant",
        "business",
        "restaurant",
        "store_info",
        "storeInfo",
        "merchant_info",
        "merchantInfo",
        "restaurant_info",
        "restaurantInfo",
    ):
        nested = candidate.get(key)
        if isinstance(nested, dict):
            value = _first_value(
                nested,
                "store_name",
                "storeName",
                "merchant_name",
                "merchantName",
                "business_name",
                "businessName",
                "restaurant_name",
                "restaurantName",
                "display_name",
                "displayName",
                "name",
            )
            if isinstance(value, str) and value.strip():
                return value.strip()

    nested_value = _find_nested_store_name(candidate)
    if isinstance(nested_value, str) and nested_value.strip():
        return nested_value.strip()
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
                    "datetime",
                    "dateTime",
                    "value",
                )
            )
            eta_text = _stringify(
                _first_value(
                    value,
                    "display_string",
                    "displayString",
                    "label",
                    "text",
                    "description",
                    "value",
                )
            )
            return eta_at, eta_text
        eta_at = _parse_any_datetime(value)
        return eta_at, _stringify(value)

    eta_range = candidate.get("eta_minutes") or candidate.get("eta_range")
    if isinstance(eta_range, (str, int, float)):
        return None, _stringify(eta_range)

    nested = _find_nested_value(
        candidate,
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
    )
    if isinstance(nested, dict):
        eta_at = _parse_any_datetime(
            _first_value(
                nested,
                "time",
                "timestamp",
                "iso",
                "datetime",
                "dateTime",
                "value",
            )
        )
        eta_text = _stringify(
            _first_value(
                nested,
                "display_string",
                "displayString",
                "label",
                "text",
                "description",
                "value",
            )
        )
        return eta_at, eta_text
    if nested is not None:
        return _parse_any_datetime(nested), _stringify(nested)

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

    nested = _find_nested_value(
        candidate,
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
        "amount_charged",
        "amountCharged",
    )
    if nested is not None:
        return _normalize_money(nested)

    fallback = _find_best_money_value(candidate)
    if fallback is not None:
        return fallback
    return None, None


def _extract_delivered_at(candidate: dict[str, Any]) -> datetime | None:
    """Extract a delivered/completed timestamp when DoorDash exposes one."""
    direct = _parse_any_datetime(
        _first_value(
            candidate,
            "delivered_at",
            "deliveredAt",
            "completed_at",
            "completedAt",
            "fulfilled_at",
            "fulfilledAt",
            "actual_delivery_time",
            "actualDeliveryTime",
            "dropoff_completed_at",
            "dropoffCompletedAt",
        )
    )
    if direct is not None:
        return direct

    nested = _find_nested_value(
        candidate,
        "delivered_at",
        "deliveredAt",
        "completed_at",
        "completedAt",
        "fulfilled_at",
        "fulfilledAt",
        "actual_delivery_time",
        "actualDeliveryTime",
        "dropoff_completed_at",
        "dropoffCompletedAt",
    )
    return _parse_any_datetime(nested)


def _extract_subtotal(candidate: dict[str, Any]) -> tuple[str | None, float | None]:
    """Extract the latest order subtotal when it is available."""
    explicit = _extract_money_component(
        candidate,
        include_tokens=("subtotal",),
        exclude_tokens=("minimum subtotal", "min subtotal"),
    )
    if explicit != (None, None):
        return explicit

    derived_amount = _derive_subtotal_from_candidate_items(candidate)
    if derived_amount is None:
        return None, None
    return f"${derived_amount:.2f}", derived_amount


def _extract_tip(candidate: dict[str, Any]) -> tuple[str | None, float | None]:
    """Extract the latest order tip when it is available."""
    return _extract_money_component(
        candidate,
        include_tokens=("tip",),
        exclude_tokens=("before tip", "default tip", "tip amount label"),
    )


def _extract_tax(candidate: dict[str, Any]) -> tuple[str | None, float | None]:
    """Extract the latest order tax when it is available."""
    return _extract_money_component(
        candidate,
        include_tokens=("tax",),
        exclude_tokens=("tax exempt",),
    )


def _extract_fees(candidate: dict[str, Any]) -> tuple[str | None, float | None]:
    """Extract the latest order fees when they are available."""
    fees = _collect_money_component_candidates(
        candidate,
        include_tokens=("fee",),
        exclude_tokens=("tax", "tip", "monthly", "save", "saving", "discount"),
    )
    if not fees:
        return None, None

    total_amount = 0.0
    found_amount = False
    for fee in fees:
        amount = fee.get("amount")
        if not isinstance(amount, (int, float)):
            continue
        total_amount += float(amount)
        found_amount = True

    if not found_amount:
        best = fees[0]
        return best.get("display"), best.get("amount")

    return f"${total_amount:.2f}", round(total_amount, 2)


def _extract_money_component(
    candidate: dict[str, Any],
    *,
    include_tokens: tuple[str, ...],
    exclude_tokens: tuple[str, ...] = (),
) -> tuple[str | None, float | None]:
    """Extract a single receipt-style money component from a nested order payload."""
    matches = _collect_money_component_candidates(
        candidate,
        include_tokens=include_tokens,
        exclude_tokens=exclude_tokens,
    )
    if not matches:
        return None, None

    best = matches[0]
    return best.get("display"), best.get("amount")


def _collect_money_component_candidates(
    candidate: dict[str, Any],
    *,
    include_tokens: tuple[str, ...],
    exclude_tokens: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Collect nested money entries that match a set of receipt-style tokens."""
    collected: list[dict[str, Any]] = []
    queue: list[Any] = [candidate]
    seen_nodes: set[int] = set()
    seen_values: set[str] = set()

    while queue:
        current = queue.pop(0)
        current_id = id(current)
        if current_id in seen_nodes:
            continue
        seen_nodes.add(current_id)

        if isinstance(current, dict):
            for key, value in current.items():
                if isinstance(value, (dict, list)):
                    queue.append(value)

                normalized = _normalize_money(value)
                if normalized == (None, None):
                    continue

                key_label = key.lower()
                label = _extract_money_label(value)
                haystack = " ".join(part for part in (key_label, label) if part)
                if not haystack:
                    continue
                if any(token in haystack for token in exclude_tokens):
                    continue
                if not any(token in haystack for token in include_tokens):
                    continue

                display, amount = normalized
                score = 0
                if label and any(token in label for token in include_tokens):
                    score += 4
                if any(token in key_label for token in include_tokens):
                    score += 3
                if label:
                    score += 1

                candidate_info = {
                    "key": key,
                    "label": label or None,
                    "display": _normalize_total_display(display, amount),
                    "amount": amount,
                    "score": score,
                }
                marker = json.dumps(candidate_info, sort_keys=True, default=str)
                if marker in seen_values:
                    continue
                seen_values.add(marker)
                collected.append(candidate_info)
            continue

        if isinstance(current, list):
            queue.extend(item for item in current if isinstance(item, (dict, list)))

    collected.sort(
        key=lambda item: (
            item.get("score", 0),
            item.get("amount") is not None,
            item.get("display") is not None,
        ),
        reverse=True,
    )
    return collected


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
    order_entries = candidate.get("orders")
    if isinstance(order_entries, list):
        nested_order_items: list[dict[str, Any]] = []
        for order_entry in order_entries:
            if not isinstance(order_entry, dict):
                continue
            items = order_entry.get("items")
            if isinstance(items, list):
                nested_order_items.extend(_normalize_item_list(items))
        if nested_order_items:
            return nested_order_items

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
        normalized = _normalize_item_list(value)
        if normalized:
            return normalized

    nested_items = _find_nested_items(candidate)
    if nested_items:
        return nested_items

    return []


def _find_nested_value(candidate: dict[str, Any], *keys: str) -> Any:
    """Find the first matching key anywhere in a nested payload."""
    queue: list[Any] = [candidate]
    seen: set[int] = set()

    while queue:
        current = queue.pop(0)
        if not isinstance(current, dict):
            continue

        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)

        for key in keys:
            value = current.get(key)
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            return value

        for value in current.values():
            if isinstance(value, dict):
                queue.append(value)
            elif isinstance(value, list):
                queue.extend(item for item in value if isinstance(item, dict))

    return None


def _find_nested_items(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Search nested lists for item-like entries."""
    queue: list[Any] = [candidate]
    seen: set[int] = set()

    while queue:
        current = queue.pop(0)
        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)

        if isinstance(current, list):
            normalized = _normalize_item_list(current)
            if normalized:
                return normalized
            queue.extend(item for item in current if isinstance(item, (dict, list)))
            continue

        if isinstance(current, dict):
            queue.extend(
                value for value in current.values() if isinstance(value, (dict, list))
            )

    return []


def _find_nested_store_name(candidate: dict[str, Any]) -> str | None:
    """Search nested merchant/store containers for a likely store name."""
    container_keys = {
        "store",
        "merchant",
        "business",
        "restaurant",
        "store_info",
        "storeinfo",
        "merchant_info",
        "merchantinfo",
        "restaurant_info",
        "restaurantinfo",
    }
    name_keys = (
        "store_name",
        "storeName",
        "merchant_name",
        "merchantName",
        "business_name",
        "businessName",
        "restaurant_name",
        "restaurantName",
        "display_name",
        "displayName",
        "name",
    )

    queue: list[Any] = [candidate]
    seen: set[int] = set()

    while queue:
        current = queue.pop(0)
        if not isinstance(current, dict):
            continue

        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)

        for key, value in current.items():
            lowered = key.lower()
            if lowered in container_keys and isinstance(value, dict):
                candidate_name = _first_value(value, *name_keys)
                if isinstance(candidate_name, str) and candidate_name.strip():
                    return candidate_name.strip()
                queue.append(value)
                continue

            if isinstance(value, dict):
                queue.append(value)
            elif isinstance(value, list):
                queue.extend(item for item in value if isinstance(item, dict))

    return None


def _normalize_item_list(value: list[Any]) -> list[dict[str, Any]]:
    """Normalize an item-like list into name/quantity pairs."""
    items: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue

        name = _first_value(
            item,
            "name",
            "title",
            "item_name",
            "itemName",
            "display_name",
            "displayName",
        )
        if not isinstance(name, str) or not name.strip():
            nested_name = _find_nested_value(
                item,
                "name",
                "title",
                "item_name",
                "itemName",
                "display_name",
                "displayName",
            )
            if not isinstance(nested_name, str) or not nested_name.strip():
                continue
            name = nested_name

        quantity = (
            item.get("quantity")
            or item.get("count")
            or item.get("item_quantity")
            or item.get("itemQuantity")
            or 1
        )
        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            quantity = 1

        unit_price_amount = _extract_item_unit_price_amount(item)
        line_total_amount = (
            round(unit_price_amount * quantity, 2)
            if isinstance(unit_price_amount, (int, float))
            else None
        )

        items.append(
            {
                "name": name.strip(),
                "quantity": quantity,
                "unit_price_amount": unit_price_amount,
                "unit_price_display": (
                    f"${unit_price_amount:.2f}"
                    if isinstance(unit_price_amount, (int, float))
                    else None
                ),
                "line_total_amount": line_total_amount,
                "line_total_display": (
                    f"${line_total_amount:.2f}"
                    if isinstance(line_total_amount, (int, float))
                    else None
                ),
            }
        )

    return items


def _count_items(items: list[dict[str, Any]]) -> int:
    """Return the total quantity represented by an item list."""
    total = 0
    for item in items:
        quantity = item.get("quantity") if isinstance(item, dict) else None
        try:
            total += int(quantity)
        except (TypeError, ValueError):
            total += 1
    return total


def _derive_subtotal_from_candidate_items(candidate: dict[str, Any]) -> float | None:
    """Derive a subtotal from item prices when DoorDash omits an explicit subtotal."""
    items = _extract_items(candidate)
    if not items:
        return None

    total = 0.0
    found_price = False
    for item in items:
        line_total = item.get("line_total_amount") if isinstance(item, dict) else None
        if isinstance(line_total, (int, float)):
            total += float(line_total)
            found_price = True

    if not found_price:
        return None
    return round(total, 2)


def _extract_item_unit_price_amount(item: dict[str, Any]) -> float | None:
    """Extract a single item's unit price including any selected paid extras."""
    base_amount = None
    for key in (
        "originalItemPrice",
        "itemPrice",
        "item_price",
        "unitPrice",
        "unit_price",
        "price",
    ):
        if key not in item:
            continue
        base_amount = _normalize_money_number(item.get(key))
        if base_amount is not None:
            break

    if base_amount is None:
        nested = _find_nested_value(
            item,
            "originalItemPrice",
            "itemPrice",
            "item_price",
            "unitPrice",
            "unit_price",
            "price",
        )
        base_amount = _normalize_money_number(nested)

    extras_amount = _extract_item_extras_total(item)
    if base_amount is None and extras_amount is None:
        return None

    total = float(base_amount or 0.0) + float(extras_amount or 0.0)
    return round(total, 2)


def _extract_item_extras_total(item: dict[str, Any]) -> float | None:
    """Extract the additional price contributed by item extras."""
    extras = item.get("orderItemExtras")
    if not isinstance(extras, list):
        return None

    total = 0.0
    found_amount = False
    for extra in extras:
        if not isinstance(extra, dict):
            continue
        options = extra.get("orderItemExtraOptions")
        if not isinstance(options, list):
            continue
        for option in options:
            if not isinstance(option, dict):
                continue
            amount = _normalize_money_number(option.get("price"))
            if amount is None:
                continue
            try:
                quantity = int(option.get("quantity") or 1)
            except (TypeError, ValueError):
                quantity = 1
            total += amount * quantity
            found_amount = True

    if not found_amount:
        return None
    return round(total, 2)


def _find_best_money_value(candidate: dict[str, Any]) -> tuple[str | None, float | None] | None:
    """Search nested payloads for the most likely order-total money field."""
    best_score = -1
    best_value: tuple[str | None, float | None] | None = None
    queue: list[Any] = [candidate]
    seen: set[int] = set()

    while queue:
        current = queue.pop(0)
        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)

        if isinstance(current, dict):
            for key, value in current.items():
                if isinstance(value, (dict, list)):
                    queue.append(value)

                score = _score_money_candidate(key, value)
                if score <= best_score:
                    continue

                normalized = _normalize_money(value)
                if normalized == (None, None):
                    continue

                best_score = score
                best_value = normalized
            continue

        if isinstance(current, list):
            queue.extend(item for item in current if isinstance(item, (dict, list)))

    return best_value


def _collect_money_candidates(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect total-like money fields for debugging the latest order mapping."""
    collected: list[dict[str, Any]] = []
    queue: list[Any] = [candidate]
    seen_nodes: set[int] = set()
    seen_values: set[str] = set()

    while queue:
        current = queue.pop(0)
        current_id = id(current)
        if current_id in seen_nodes:
            continue
        seen_nodes.add(current_id)

        if isinstance(current, dict):
            for key, value in current.items():
                if isinstance(value, (dict, list)):
                    queue.append(value)

                score = _score_money_candidate(key, value)
                if score < 0:
                    continue

                display, amount = _normalize_money(value)
                if display is None and amount is None:
                    continue

                candidate_info = {
                    "key": key,
                    "label": _extract_money_label(value) or None,
                    "display": display,
                    "amount": amount,
                    "score": score,
                }
                marker = json.dumps(candidate_info, sort_keys=True, default=str)
                if marker in seen_values:
                    continue
                seen_values.add(marker)
                collected.append(candidate_info)
            continue

        if isinstance(current, list):
            queue.extend(item for item in current if isinstance(item, (dict, list)))

    collected.sort(
        key=lambda item: (item.get("score", 0), item.get("amount") is not None, item.get("display") is not None),
        reverse=True,
    )
    return collected[:10]


def _score_money_candidate(key: str, value: Any) -> int:
    """Score how likely a key/value pair is to represent the full order total."""
    lowered = key.lower()
    label = _extract_money_label(value)

    if any(
        token in f"{lowered} {label}".strip()
        for token in (
            "tip",
            "tax",
            "fee",
            "discount",
            "saving",
            "savings",
            "credit",
            "refund",
            "item_total",
            "unit_price",
        )
    ):
        return -1

    normalized = _normalize_money(value)
    if normalized == (None, None):
        return -1

    if any(token in label for token in ("grand total", "order total", "amount charged", "total")):
        return 9
    if any(token in label for token in ("subtotal",)):
        return 3

    if any(token in lowered for token in ("grand_total", "grandtotal", "order_total", "ordertotal")):
        return 8
    if any(token in lowered for token in ("amount_charged", "amountcharged", "charged_total")):
        return 7
    if "display_total" in lowered:
        return 6
    if "total" in lowered:
        return 5
    if "subtotal" in lowered or "sub_total" in lowered:
        return 3
    if "amount" in lowered:
        return 2
    if "price" in lowered:
        return 1

    return 0


def _extract_money_label(value: Any) -> str:
    """Extract a lowercase descriptive label from a money-like structure."""
    if not isinstance(value, dict):
        return ""

    label = _first_value(
        value,
        "label",
        "title",
        "name",
        "description",
        "display_name",
        "displayName",
    )
    if not isinstance(label, str):
        return ""
    return label.strip().lower()


def _normalize_total_display(display: str | None, amount: float | None) -> str | None:
    """Return a stable human-readable total value."""
    if display is not None:
        stripped = display.strip()
        while stripped.startswith("$$"):
            stripped = stripped[1:]
        if stripped and _looks_like_money_string(stripped):
            return stripped

    if amount is None:
        return None
    return f"${amount:.2f}"


def _normalize_money(value: Any) -> tuple[str | None, float | None]:
    """Normalize various DoorDash money payloads into display + float amount."""
    if value is None:
        return None, None

    if isinstance(value, dict):
        display = _stringify(
            _first_value(
                value,
                "display_string",
                "displayString",
                "formatted_amount",
                "formattedAmount",
                "amount_display",
                "amountDisplay",
            )
        )
        numeric = _first_value(
            value,
            "amount",
            "value",
            "unit_amount",
            "unitAmount",
            "cents",
            "price",
        )
        amount = _normalize_money_number(numeric)

        if display is None and isinstance(numeric, str):
            candidate_display = numeric.strip()
            if _looks_like_money_string(candidate_display):
                display = candidate_display

        if amount is None:
            nested_money = _first_value(
                value,
                "money",
                "amount_info",
                "amountInfo",
                "price_info",
                "priceInfo",
            )
            if nested_money is not None and nested_money is not value:
                display, amount = _normalize_money(nested_money)

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


def _looks_like_money_string(value: str) -> bool:
    """Return whether a string resembles a currency amount rather than a label."""
    return bool(re.search(r"\$?\s*-?\d+(?:,\d{3})*(?:\.\d{2})?", value))


def _normalize_money_number(value: Any) -> float | None:
    """Convert DoorDash numeric money values into dollars when possible."""
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if not cleaned:
            return None
        match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
        if match is None:
            return None
        value = match.group(0)

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


def _order_sort_key(order: dict[str, Any]) -> tuple[int, datetime, int, str]:
    """Return a stable sort key for order recency."""
    timestamp = order.get("updated_at") or order.get("eta_at") or order.get("created_at")
    fallback_timestamp = datetime(1970, 1, 1, tzinfo=dt_util.UTC)
    return (
        1 if timestamp is not None else 0,
        timestamp or fallback_timestamp,
        order.get("confidence", 0),
        order["id"],
    )


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
