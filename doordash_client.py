"""Standalone DoorDash client built from the same parser as the HA integration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://www.doordash.com"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)


class DoorDashClientError(RuntimeError):
    """Raised when the standalone DoorDash client cannot fetch or parse data."""


class DoorDashClient:
    """Simple standalone client for DoorDash order history and detail pages."""

    def __init__(
        self,
        *,
        cookie_header: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.cookie_header = cookie_header.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent
        self._parser_module = _load_parser_module(Path(__file__).resolve().parent)

        if not self.cookie_header:
            raise DoorDashClientError("A DoorDash Cookie header value is required.")

    @classmethod
    def from_cookie_file(
        cls,
        cookie_file: str | Path,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> "DoorDashClient":
        """Construct a client from a file containing the raw Cookie header."""
        cookie_path = Path(cookie_file)
        cookie_header = cookie_path.read_text(encoding="utf-8").strip()
        return cls(
            cookie_header=cookie_header,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            user_agent=user_agent,
        )

    def fetch_orders(self, *, include_latest_detail: bool = True) -> list[dict[str, Any]]:
        """Fetch the DoorDash order history page and return normalized orders."""
        html, final_url = self._fetch_html(f"{self.base_url}/orders/")
        orders = self.parse_orders_html(html, final_url)

        if include_latest_detail and orders:
            enriched = self.fetch_order_detail(existing_order=orders[0])
            if enriched is not None:
                orders[0] = enriched

        return orders

    def fetch_latest_order(self, *, include_detail: bool = True) -> dict[str, Any] | None:
        """Fetch and return the latest DoorDash order."""
        orders = self.fetch_orders(include_latest_detail=include_detail)
        return orders[0] if orders else None

    def fetch_order_detail(
        self,
        *,
        order_url: str | None = None,
        order_uuid: str | None = None,
        existing_order: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Fetch and parse a specific DoorDash order detail page."""
        detail_url = order_url
        if detail_url is None and order_uuid is not None:
            detail_url = self.build_order_detail_url(order_uuid)
        if detail_url is None and existing_order is not None:
            detail_url = existing_order.get("order_detail_url")
        if not isinstance(detail_url, str) or not detail_url.strip():
            return None

        html, final_url = self._fetch_html(detail_url)
        parsed = self.parse_detail_html(html, final_url)
        if not parsed:
            return None

        detail_order = parsed[0]
        if existing_order is None:
            return detail_order

        merged = dict(existing_order)
        merge_order = getattr(self._parser_module, "_merge_order_into", None)
        if callable(merge_order):
            merge_order(merged, detail_order)
            return merged

        merged.update(
            {
                key: value
                for key, value in detail_order.items()
                if value not in (None, "", [], {})
            }
        )
        return merged

    def parse_orders_html(self, html: str, page_url: str | None = None) -> list[dict[str, Any]]:
        """Parse a saved DoorDash orders page HTML string."""
        return self._parser_module.extract_orders_from_html(
            html,
            page_url or f"{self.base_url}/orders/",
        )

    def parse_detail_html(self, html: str, page_url: str) -> list[dict[str, Any]]:
        """Parse a saved DoorDash order-detail page HTML string."""
        return self._parser_module.extract_orders_from_html(html, page_url)

    def build_order_detail_url(self, order_uuid: str) -> str:
        """Build the direct DoorDash order detail URL for an order UUID."""
        return (
            f"{self.base_url}/orders/{order_uuid}/"
            "?fromCheckout=true&userResumed=false&doubledash-redirect=false"
        )

    def _fetch_html(self, url: str) -> tuple[str, str]:
        """Fetch a DoorDash page and return its HTML plus the final URL."""
        headers = {
            "Accept": "text/html,application/xhtml+xml,application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": self.user_agent,
            "Cookie": self.cookie_header,
            "Referer": f"{self.base_url}/",
        }

        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                final_url = response.geturl()
                html = response.read().decode("utf-8", errors="replace")
        except HTTPError as err:
            raise DoorDashClientError(f"{url} returned HTTP {err.code}: {err.reason}") from err
        except URLError as err:
            raise DoorDashClientError(f"{url} could not be reached: {err.reason}") from err

        lowered_url = final_url.lower()
        lowered_html = html.lower()
        if "identity.doordash.com" in lowered_url or "/consumer/login" in lowered_url:
            raise DoorDashClientError(f"DoorDash redirected the session to sign in: {final_url}")
        if "sign in to doordash" in lowered_html:
            raise DoorDashClientError("DoorDash returned a sign-in page instead of order data.")

        return html, final_url


def _load_parser_module(repo_root: Path) -> Any:
    """Load the DoorDash parser directly from the integration source tree."""
    module_path = repo_root / "custom_components" / "doordash_status" / "api.py"
    spec = importlib.util.spec_from_file_location("doordash_standalone_api", module_path)
    if spec is None or spec.loader is None:
        raise DoorDashClientError(f"Could not load parser module from {module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

