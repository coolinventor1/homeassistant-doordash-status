# DoorDash Status for Home Assistant

`doordash_status` is an unofficial HACS-ready custom integration for Home Assistant that reads DoorDash consumer order status and exposes it as sensors.

It supports two connection modes:

- `Tracking link` for a single order from an email or text
- `Browser session cookie` for automatically following the latest orders on your DoorDash account

Because DoorDash does not provide a general public consumer API for this use case, this integration reads DoorDash order pages and extracts structured order data from the page payloads.

## What it exposes

- `Active orders`
- `Latest order status`
- `Latest order store`
- `Latest order ETA`
- `Latest order ETA text`
- `Latest order total`
- `Latest order item count`
- `Latest dasher`

Sensor attributes can include item lists, tracking URLs, help URLs, fulfillment type, ETA text, recent order summaries, and order totals when DoorDash includes them.

## Installation

### HACS custom repository

1. In HACS, add `https://github.com/coolinventor1/homeassistant-doordash-status` as a custom repository with category `Integration`.
2. Install `DoorDash Status`.
3. Restart Home Assistant.

### Manual install

1. Copy `custom_components/doordash_status` into your Home Assistant `custom_components` directory.
2. Restart Home Assistant.

## Configuration

Add the integration from **Settings -> Devices & services -> Add integration** and search for `DoorDash Status`.

Then choose one of these setup paths.

## Tracking link setup

This is the cleanest setup when DoorDash gives you a shareable order tracking link.

1. Copy the tracking URL from the DoorDash email or text message for an order.
2. In Home Assistant, choose the `Tracking link` setup method.
3. Paste the tracking URL.

This mode follows one order at a time. If you want automatic future-order tracking, use browser-session mode instead.

## Browser session cookie setup

This is the best setup when you want Home Assistant to automatically watch the latest orders on your DoorDash account.

1. Log into DoorDash in your browser.
2. Open browser developer tools.
3. Open the Network tab and reload a DoorDash page such as `https://www.doordash.com/orders/`.
4. Click the page request going to `https://www.doordash.com/orders/` or another logged-in `doordash.com` page request.
5. Copy the full request `Cookie` header value.
6. In Home Assistant, choose the `Browser session cookie` setup method and paste that value.

As long as the DoorDash session stays valid, Home Assistant should continue tracking new and active orders without needing a new tracking URL each time.

## Local debugging

You can debug the parser locally without reinstalling the Home Assistant integration on every change.

Parse a saved DoorDash orders HTML file:

```powershell
python .\scripts\doordash_local_debug.py --html .\orders_snapshot.html
```

Fetch your live DoorDash orders page with a browser cookie and save the snapshot:

```powershell
python .\scripts\doordash_local_debug.py --cookie-file .\cookie.txt --save-html .\orders_snapshot.html
```

The script prints a compact summary plus the normalized order JSON using the same parser logic as the integration.

## Notes

- Browser-session mode is unofficial and may stop working if DoorDash changes its website.
- If DoorDash logs you out or rotates the session, you will need to paste a fresh cookie header.
- Some fields only appear when DoorDash includes them on the current order page.
