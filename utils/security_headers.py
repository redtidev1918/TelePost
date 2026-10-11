"""Security response headers for the local HTTP surfaces (§71).

Both HTTP entrypoints — the webhook server (`utils/webhook_server.py`) and the
polling server (`utils/polling_server.py`) — attach this middleware to the
aiohttp application they build, so the Mini App document at ``/app/*``, its
hashed assets, the JSON API and the health endpoints all carry a consistent,
least-privilege header set.

The Mini App policy is derived from what the built frontend actually needs
(verified against `webapp/`):

* ``script-src 'self'`` — `webapp/dist/index.html` has no inline script and the
  bundle is same-origin; there is no ``eval``/``new Function`` in the app code.
* ``style-src 'self' 'unsafe-inline'`` — telegram-ui plus inline ``style``
  attributes used for layout.
* ``img-src 'self' data: blob:`` / ``media-src 'self' blob:`` — local media
  previews and thumbnails use ``URL.createObjectURL`` (blob:) and CSS SVG
  placeholders use ``data:``.
* ``connect-src 'self'`` — every request goes to ``/api/{bot}/v1`` on the same
  origin; the app opens no WebSocket/SSE and loads no third-party host.
* ``frame-ancestors`` allows Telegram's web clients to keep embedding the Mini
  App. ``X-Frame-Options`` is deliberately NOT sent: it cannot express that
  allow-list and a ``SAMEORIGIN`` value would break web.telegram.org embedding.
* ``Permissions-Policy`` keeps camera/microphone/display-capture available to
  self (Uppy webcam and screen capture) while switching off the rest.
"""

from __future__ import annotations

from aiohttp import web

# Headers applied to every response; handlers may override them.
_BASE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": (
        "camera=(self), microphone=(self), display-capture=(self), "
        "geolocation=(), payment=(), usb=(), serial=(), hid=()"
    ),
    "Cross-Origin-Resource-Policy": "same-origin",
}

MINIAPP_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "media-src 'self' blob:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "worker-src 'self' blob:; "
    "manifest-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'self' https://*.telegram.org"
)


def is_miniapp_path(path: str) -> bool:
    """True for the Mini App document and its static assets (``/app``, ``/app/…``)."""
    return path == "/app" or path.startswith("/app/")


@web.middleware
async def security_headers_middleware(request: web.Request, handler):
    """Attach least-privilege security headers to every HTTP response.

    Existing headers win: the media download handlers set a stricter
    ``Content-Security-Policy: sandbox`` themselves (utils/api_server.py) and
    that value must survive this middleware.
    """
    try:
        response = await handler(request)
    except web.HTTPException as error:
        response = error
    for name, value in _BASE_HEADERS.items():
        response.headers.setdefault(name, value)
    if is_miniapp_path(request.path):
        response.headers.setdefault("Content-Security-Policy", MINIAPP_CSP)
    return response
