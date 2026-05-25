"""Local DoorDash parser harness for fast iteration outside Home Assistant."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "https://www.doordash.com"
DEFAULT_PAGE_URL = f"{DEFAULT_BASE_URL}/orders/"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)


def _build_argument_parser() -> argparse.ArgumentParser:
    """Configure command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Fetch or parse DoorDash orders locally using the same parser logic as "
            "the Home Assistant custom integration."
        )
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--html",
        type=Path,
        help="Path to a saved DoorDash orders HTML file to parse.",
    )
    source.add_argument(
        "--cookie-file",
        type=Path,
        help="Path to a text file containing the raw DoorDash Cookie request header.",
    )
    source.add_argument(
        "--cookie",
        help="Raw DoorDash Cookie request header value.",
    )

    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"DoorDash base URL to fetch from. Default: {DEFAULT_BASE_URL}",
    )
    parser.add_argument(
        "--page-url",
        default=DEFAULT_PAGE_URL,
        help=(
            "Page URL label used during parsing when reading saved HTML. "
            f"Default: {DEFAULT_PAGE_URL}"
        ),
    )
    parser.add_argument(
        "--tracking-url",
        help="Optional DoorDash tracking URL to fetch instead of the orders history page.",
    )
    parser.add_argument(
        "--save-html",
        type=Path,
        help="Optional path to save the fetched HTML for repeatable local parsing.",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        help="Optional path to save the parsed orders JSON.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="How many parsed orders to print in the terminal summary. Default: 5",
    )
    return parser


def _load_cookie(args: argparse.Namespace) -> str | None:
    """Load a cookie value from CLI arguments."""
    if args.cookie is not None:
        return args.cookie.strip()
    if args.cookie_file is not None:
        return args.cookie_file.read_text(encoding="utf-8").strip()
    return None


def _candidate_urls(base_url: str, tracking_url: str | None) -> list[str]:
    """Return likely DoorDash URLs to try in the same order as the integration."""
    if tracking_url:
        return [tracking_url]

    normalized = base_url.rstrip("/")
    return [
        f"{normalized}/orders/",
        f"{normalized}/orders",
        f"{normalized}/consumer/orders/",
        f"{normalized}/consumer/orders",
    ]


def _fetch_html(
    *,
    cookie_header: str,
    base_url: str,
    tracking_url: str | None,
) -> tuple[str, str]:
    """Fetch a DoorDash orders-like page with stdlib networking."""
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": DEFAULT_USER_AGENT,
        "Cookie": cookie_header,
        "Referer": f"{base_url.rstrip('/')}/",
    }

    last_error: Exception | None = None
    for candidate in _candidate_urls(base_url, tracking_url):
        request = Request(candidate, headers=headers)
        try:
            with urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
                final_url = response.geturl()
                html = response.read().decode("utf-8", errors="replace")
                if "identity.doordash.com" in final_url.lower():
                    last_error = RuntimeError(
                        f"DoorDash redirected the session to sign in: {final_url}"
                    )
                    continue
                return html, final_url
        except HTTPError as err:
            last_error = RuntimeError(
                f"{candidate} returned HTTP {err.code}: {err.reason}"
            )
        except URLError as err:
            last_error = RuntimeError(f"{candidate} could not be reached: {err.reason}")

    raise SystemExit(str(last_error or "Could not fetch a DoorDash orders page."))


def _load_parser_module(repo_root: Path) -> Any:
    """Load the integration parser directly from the repo source tree."""
    module_path = repo_root / "custom_components" / "doordash_status" / "api.py"
    spec = importlib.util.spec_from_file_location("doordash_local_api", module_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Could not load parser module from {module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _print_summary(orders: list[dict[str, Any]], *, source_url: str, limit: int) -> None:
    """Print a compact terminal summary of parsed orders."""
    print(f"Parsed {len(orders)} orders from {source_url}")
    print()

    for index, order in enumerate(orders[:limit]):
        print(f"[{index}] {order.get('store_name') or 'Unknown store'}")
        print(f"  status: {order.get('status') or 'Unknown'}")
        print(f"  total: {order.get('total_display') or order.get('total_amount') or 'Unknown'}")
        print(f"  item_count: {order.get('item_count') or 0}")
        print(f"  fulfillment_type: {order.get('fulfillment_type') or 'Unknown'}")
        print(f"  created_at: {order.get('created_at') or 'Unknown'}")
        print(f"  updated_at: {order.get('updated_at') or 'Unknown'}")
        print(f"  eta_text: {order.get('eta_text') or 'Unknown'}")
        print(f"  dasher_name: {order.get('dasher_name') or 'Unknown'}")
        items = order.get("items") or []
        if items:
            print("  items:")
            for item in items:
                print(f"    - {item.get('quantity', 1)} x {item.get('name', 'Unknown item')}")
        else:
            print("  items: []")
        print()


def main() -> int:
    """Run the local DoorDash parser harness."""
    parser = _build_argument_parser()
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    parser_module = _load_parser_module(repo_root)

    if args.html is not None:
        html = args.html.read_text(encoding="utf-8")
        source_url = args.page_url
    else:
        cookie_header = _load_cookie(args)
        if not cookie_header:
            raise SystemExit("A non-empty DoorDash cookie value is required to fetch live pages.")
        html, source_url = _fetch_html(
            cookie_header=cookie_header,
            base_url=args.base_url,
            tracking_url=args.tracking_url,
        )
        if args.save_html is not None:
            args.save_html.parent.mkdir(parents=True, exist_ok=True)
            args.save_html.write_text(html, encoding="utf-8")

    orders = parser_module.extract_orders_from_html(html, source_url)

    _print_summary(orders, source_url=source_url, limit=max(args.limit, 0))

    serialized = json.dumps(orders, indent=2, default=str)
    print(serialized)

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(serialized, encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
