"""Quickstart example for the standalone DoorDash client.

Paste your DoorDash Cookie request header into COOKIE_HEADER below, then run:

    python .\scripts\doordash_quickstart.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

COOKIE_HEADER = ""
BASE_URL = "https://www.doordash.com"
INCLUDE_DETAIL_PAGE = True
OUTPUT_JSON_PATH = "latest_order_standalone.json"


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from doordash_client import DoorDashClient, DoorDashClientError


def main() -> int:
    """Fetch the latest DoorDash order and print a compact summary."""
    if not COOKIE_HEADER.strip():
        print("Paste your DoorDash Cookie request header into COOKIE_HEADER first.")
        return 1

    client = DoorDashClient(cookie_header=COOKIE_HEADER, base_url=BASE_URL)

    try:
        latest_order = client.fetch_latest_order(include_detail=INCLUDE_DETAIL_PAGE)
    except DoorDashClientError as err:
        print(f"DoorDash fetch failed: {err}")
        return 1

    if latest_order is None:
        print("No DoorDash orders were found for this account.")
        return 1

    print("Latest DoorDash order")
    print(f"Store: {latest_order.get('store_name') or 'Unknown'}")
    print(f"Status: {latest_order.get('status') or 'Unknown'}")
    print(f"Dasher: {latest_order.get('dasher_name') or 'Unknown'}")
    print(f"Total: {latest_order.get('total_display') or latest_order.get('total_amount') or 'Unknown'}")
    print(f"Address: {latest_order.get('delivery_address') or 'Unknown'}")
    print(f"Left at: {latest_order.get('delivery_instructions') or 'Unknown'}")
    print(f"Card: {latest_order.get('payment_method') or 'Unknown'}")
    print(f"Items: {latest_order.get('item_count') or 0}")

    output_path = REPO_ROOT / OUTPUT_JSON_PATH
    output_path.write_text(
        json.dumps(latest_order, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\nSaved full JSON to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

