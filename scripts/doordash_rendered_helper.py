"""Render DoorDash order-detail pages in a real browser and expose parsed JSON."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

DEFAULT_BASE_URL = "https://www.doordash.com"
DEFAULT_COOKIE_FILES = ("cookie.txt", ".cookie.txt")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)


def _build_argument_parser() -> argparse.ArgumentParser:
    """Create the command-line interface."""
    parser = argparse.ArgumentParser(
        description=(
            "Render DoorDash order detail pages with a real browser so rendered-only "
            "fields like the Dasher name become available to the same parser used by "
            "the Home Assistant integration."
        )
    )
    parser.add_argument(
        "--order-url",
        help="Render one specific DoorDash order-detail URL and print the parsed JSON.",
    )
    parser.add_argument(
        "--cookie-file",
        type=Path,
        help="Optional file containing the raw DoorDash Cookie request header.",
    )
    parser.add_argument(
        "--cookie",
        help="Optional raw DoorDash Cookie request header value.",
    )
    parser.add_argument(
        "--html",
        type=Path,
        help="Parse a saved rendered DoorDash detail HTML file instead of launching a browser.",
    )
    parser.add_argument(
        "--page-url",
        help="Explicit page URL label to use when parsing saved HTML.",
    )
    parser.add_argument(
        "--save-html",
        type=Path,
        help="Optional path to save the rendered HTML during a one-shot browser fetch.",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Bind host for helper-server mode. Default: {DEFAULT_HOST}",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Bind port for helper-server mode. Default: {DEFAULT_PORT}",
    )
    parser.add_argument(
        "--channel",
        default="msedge",
        help="Playwright browser channel to launch. Default: msedge",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show the browser window instead of running headless.",
    )
    return parser


def _resolve_default_cookie_file(repo_root: Path) -> Path | None:
    """Return the first repo-level cookie file that exists."""
    for filename in DEFAULT_COOKIE_FILES:
        candidate = repo_root / filename
        if candidate.exists():
            return candidate
    return None


def _load_cookie(
    *,
    cookie: str | None,
    cookie_file: Path | None,
    repo_root: Path,
) -> str | None:
    """Load a DoorDash cookie header from args, env, or repo-level defaults."""
    if cookie is not None:
        return cookie.strip()
    if cookie_file is not None:
        return cookie_file.read_text(encoding="utf-8").strip()
    if env_cookie := os.getenv("DOORDASH_COOKIE"):
        return env_cookie.strip()
    default_cookie_file = _resolve_default_cookie_file(repo_root)
    if default_cookie_file is not None:
        return default_cookie_file.read_text(encoding="utf-8").strip()
    return None


def _load_parser_module(repo_root: Path) -> Any:
    """Load the integration parser directly from the repo tree."""
    module_path = repo_root / "custom_components" / "doordash_status" / "api.py"
    spec = importlib.util.spec_from_file_location("doordash_rendered_api", module_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Could not load parser module from {module_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _guess_page_url_from_html(html: str) -> str:
    """Infer the most useful page URL label from saved HTML."""
    canonical_match = next(
        (
            candidate
            for candidate in (
                _regex_search(
                    r'<link rel=canonical href=(https://www\.doordash\.com/orders/[0-9a-f\-]{36}/)',
                    html,
                ),
                _regex_search(
                    r'https://www\.doordash\.com/orders/[0-9a-f\-]{36}/\?fromCheckout=true&amp;userResumed=false&amp;doubledash-redirect=false',
                    html,
                ),
            )
            if candidate is not None
        ),
        None,
    )
    if canonical_match is None:
        return f"{DEFAULT_BASE_URL}/orders/"
    return canonical_match.replace("&amp;", "&")


def _regex_search(pattern: str, text: str) -> str | None:
    """Return the first regex match group 0, if any."""
    import re

    match = re.search(pattern, text)
    return match.group(0) if match is not None else None


def _cookie_dicts_from_header(cookie_header: str, *, target_url: str) -> list[dict[str, Any]]:
    """Convert a raw Cookie header string into Playwright cookie objects."""
    origin = urlsplit(target_url)
    if not origin.scheme or not origin.netloc:
        target_url = DEFAULT_BASE_URL

    cookies: list[dict[str, Any]] = []
    for fragment in cookie_header.split(";"):
        part = fragment.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        if not name:
            continue
        cookies.append(
            {
                "name": name,
                "value": value.strip(),
                "url": f"{urlsplit(target_url).scheme}://{urlsplit(target_url).netloc}/",
            }
        )
    return cookies


async def _render_order_detail(
    *,
    parser_module: Any,
    order_url: str,
    cookie_header: str,
    channel: str,
    headed: bool,
    save_html: Path | None,
) -> dict[str, Any]:
    """Render a DoorDash order-detail page in a real browser and parse it."""
    try:
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        from playwright.async_api import async_playwright
    except ModuleNotFoundError as err:
        raise RuntimeError(
            "playwright is required for rendered DoorDash extraction. "
            "Install it with: pip install playwright"
        ) from err

    html = ""
    final_url = order_url
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                channel=channel,
                headless=not headed,
            )
            context = await browser.new_context(
                ignore_https_errors=True,
                locale="en-US",
                user_agent=DEFAULT_USER_AGENT,
            )
            cookies = _cookie_dicts_from_header(cookie_header, target_url=order_url)
            if cookies:
                await context.add_cookies(cookies)

            page = await context.new_page()
            await page.goto(order_url, wait_until="domcontentloaded", timeout=45000)
            try:
                await page.wait_for_load_state("networkidle", timeout=15000)
            except PlaywrightTimeoutError:
                pass

            for selector in (
                "[data-testid='DasherProfileDetails']",
                "text=Subtotal",
                "text=Payment",
                "[data-testid='merchantSection']",
            ):
                try:
                    await page.locator(selector).wait_for(state="attached", timeout=4000)
                    break
                except PlaywrightTimeoutError:
                    continue

            await page.wait_for_timeout(1200)
            html = await page.content()
            final_url = page.url
            await context.close()
            await browser.close()
    except PlaywrightError as err:
        raise RuntimeError(f"Could not render the DoorDash page in {channel}: {err}") from err

    if save_html is not None:
        save_html.parent.mkdir(parents=True, exist_ok=True)
        save_html.write_text(html, encoding="utf-8")

    orders = parser_module.extract_orders_from_html(html, final_url)
    order = orders[0] if orders else None
    if order is None:
        raise RuntimeError("Rendered the DoorDash page, but the parser found no order data.")

    return {
        "ok": True,
        "order_url": order_url,
        "final_url": final_url,
        "order": order,
    }


class _RenderedHelperHandler(BaseHTTPRequestHandler):
    """Serve a tiny local HTTP API for rendered DoorDash detail extraction."""

    parser_module: Any = None
    default_cookie_header: str | None = None
    channel: str = "msedge"
    headed: bool = False

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        """Handle health checks."""
        if self.path.rstrip("/") == "/health":
            self._send_json(HTTPStatus.OK, {"ok": True})
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        """Handle render requests."""
        if self.path.rstrip("/") != "/render-detail":
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "Not found"})
            return

        content_length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(content_length) if content_length else b"{}"
        try:
            payload = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "Invalid JSON body"})
            return

        order_url = payload.get("order_url")
        cookie_header = payload.get("cookie_header") or self.default_cookie_header
        if not isinstance(order_url, str) or not order_url.strip():
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "order_url is required"})
            return
        if not isinstance(cookie_header, str) or not cookie_header.strip():
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"ok": False, "error": "cookie_header is required"},
            )
            return

        try:
            result = asyncio.run(
                _render_order_detail(
                    parser_module=self.parser_module,
                    order_url=order_url.strip(),
                    cookie_header=cookie_header.strip(),
                    channel=self.channel,
                    headed=self.headed,
                    save_html=None,
                )
            )
        except RuntimeError as err:
            self._send_json(HTTPStatus.BAD_GATEWAY, {"ok": False, "error": str(err)})
            return

        self._send_json(HTTPStatus.OK, result)

    def log_message(self, format: str, *args: Any) -> None:
        """Keep the helper output readable."""
        print(f"[helper] {self.address_string()} - {format % args}")

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        """Send a JSON response."""
        encoded = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def _run_server(
    *,
    parser_module: Any,
    host: str,
    port: int,
    channel: str,
    headed: bool,
    default_cookie_header: str | None,
) -> int:
    """Start the local helper HTTP server."""
    handler = type(
        "RenderedDoorDashHandler",
        (_RenderedHelperHandler,),
        {
            "parser_module": parser_module,
            "default_cookie_header": default_cookie_header,
            "channel": channel,
            "headed": headed,
        },
    )
    server = ThreadingHTTPServer((host, port), handler)
    print(f"DoorDash rendered helper listening on http://{host}:{port}/render-detail")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
        print("Stopping helper.")
    finally:
        server.server_close()
    return 0


def main() -> int:
    """Run the helper in one-shot or server mode."""
    parser = _build_argument_parser()
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    parser_module = _load_parser_module(repo_root)

    if args.html is not None:
        html = args.html.read_text(encoding="utf-8", errors="ignore")
        page_url = args.page_url or _guess_page_url_from_html(html)
        orders = parser_module.extract_orders_from_html(html, page_url)
        print(json.dumps({"ok": bool(orders), "order": orders[0] if orders else None}, indent=2, default=str))
        return 0

    cookie_header = _load_cookie(
        cookie=args.cookie,
        cookie_file=args.cookie_file,
        repo_root=repo_root,
    )

    if args.order_url:
        if not cookie_header:
            raise SystemExit(
                "Rendering a live DoorDash detail page requires a cookie via --cookie, "
                "--cookie-file, DOORDASH_COOKIE, or a repo-level cookie.txt file."
            )
        result = asyncio.run(
            _render_order_detail(
                parser_module=parser_module,
                order_url=args.order_url,
                cookie_header=cookie_header,
                channel=args.channel,
                headed=args.headed,
                save_html=args.save_html,
            )
        )
        print(json.dumps(result, indent=2, default=str))
        return 0

    return _run_server(
        parser_module=parser_module,
        host=args.host,
        port=args.port,
        channel=args.channel,
        headed=args.headed,
        default_cookie_header=cookie_header,
    )


if __name__ == "__main__":
    raise SystemExit(main())
