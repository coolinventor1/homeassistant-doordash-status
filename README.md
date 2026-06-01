# DoorDash Status for Home Assistant

`doordash_status` is an unofficial HACS-ready custom integration for Home Assistant that reads DoorDash consumer order status and exposes it as sensors.

It supports two connection modes:

- `Tracking link` for a single order from an email or text
- `Browser session cookie` for automatically following the latest orders on your DoorDash account

Because DoorDash does not provide a general public consumer API for this use case, this integration reads DoorDash order pages and extracts structured order data from the page payloads.

Some richer fields on DoorDash's per-order page, especially the Dasher name, are rendered in the browser after JavaScript runs. For those fields, this repo now includes an optional local rendered helper that Home Assistant can call.

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

## Dashboard card

This integration now includes an auto-loaded Lovelace card for the latest order.

After updating and restarting Home Assistant, add a manual card with:

```yaml
type: custom:doordash-latest-order-card
entity: sensor.latest_order_summary
title: Latest order
```

Use your actual `Latest order summary` entity id from Home Assistant if it differs.

The card shows:

- store logo
- store name
- order total
- item thumbnails for the latest order

Tapping the card opens more-info for the configured summary sensor.

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

If DoorDash rotates your session later, open the integration `Options` in Home Assistant and paste a fresh browser cookie there. Leave the cookie field blank if you only want to change the scan interval and keep the current session.

## Optional rendered helper

If you want rendered-only fields like `Latest dasher` to work more reliably, run the local rendered helper on a Windows machine with Microsoft Edge installed.

### Install the helper dependency

```powershell
pip install playwright
```

This helper launches your local Edge browser through Playwright. It does not install anything inside Home Assistant.

### Start the helper

From this repo:

```powershell
python .\scripts\doordash_rendered_helper.py --host 0.0.0.0 --port 8765
```

If you want the helper to have a default DoorDash session for local testing, keep `cookie.txt` in the repo root or pass `--cookie-file .\cookie.txt`.

### Point Home Assistant at the helper

1. Open `DoorDash Status` in Home Assistant.
2. Click `Options`.
3. Paste your usual DoorDash cookie if needed.
4. Set `Rendered helper URL` to the machine running the helper, for example:
   - `http://192.168.1.50:8765`
   - or `http://192.168.1.50:8765/render-detail`
5. Save and reload the integration.

Home Assistant will then send the latest order detail URL plus your current DoorDash cookie to the helper, and the helper will return a parsed rendered-page result that gets merged back into the sensors.

## Local debugging

You can debug the parser locally without reinstalling the Home Assistant integration on every change.

Live fetch the current DoorDash orders page:

```powershell
python .\scripts\doordash_local_debug.py
```

The script will automatically use either:

- `DOORDASH_COOKIE` from your environment
- `.\cookie.txt` in the repo root
- `.\.cookie.txt` in the repo root

You can still pass a cookie explicitly and save the fetched snapshot:

```powershell
python .\scripts\doordash_local_debug.py --cookie-file .\cookie.txt --save-html .\orders_snapshot.html
```

Parse a saved DoorDash orders HTML file:

```powershell
python .\scripts\doordash_local_debug.py --html .\orders_snapshot.html
```

Fetch the latest order's richer detail page automatically from a live DoorDash session:

```powershell
python .\scripts\doordash_local_debug.py --latest-detail --save-orders-html .\orders_snapshot.html --save-html .\latest_order_detail.html
```

That flow:

- fetches `https://www.doordash.com/orders/`
- extracts the latest order's `order_detail_url`
- fetches the richer `/orders/<uuid>/...` page
- saves both the history page and the specific order page locally for debugging

The script prints a compact summary plus the normalized order JSON using the same parser logic as the integration.

Add `--print-json` if you want the full normalized JSON on stdout as well.

Parse a saved rendered browser HTML file with the same detail parser:

```powershell
python .\scripts\doordash_rendered_helper.py --html "C:\path\to\saved_doordash_detail.html"
```

Render one live DoorDash order-detail page through Edge and print the parsed JSON:

```powershell
python .\scripts\doordash_rendered_helper.py --order-url "https://www.doordash.com/orders/<uuid>/?fromCheckout=true&userResumed=false&doubledash-redirect=false" --cookie-file .\cookie.txt
```

## Notes

- Browser-session mode is unofficial and may stop working if DoorDash changes its website.
- If DoorDash logs you out or rotates the session, you will need to paste a fresh cookie header.
- Some fields only appear when DoorDash includes them on the current order page.
