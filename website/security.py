"""Response security headers.

Applied app-wide (not on the API blueprint) so the SPA, the API and /health all get
them.  ``website/api/guard.py`` owns the per-request caching headers for
authenticated responses; this module only adds headers that are the same for every
response.
"""

from flask import Flask

# Content-Security-Policy.
#
# Two entries here are load-bearing rather than boilerplate:
#
# * ``img-src`` must include https://cdn.discordapp.com or every guild icon and user
#   avatar on the dashboard fails to load and the picker renders blank.  The API
#   returns those URLs directly (see website/discord_api.py).  i.ytimg.com and
#   i.scdn.co are listed for the track artwork the music remote will show.
# * ``script-src`` cannot use a nonce: Vite emits a plain <script type="module">
#   with a content-hashed filename, and there is no server-side templating step to
#   inject a nonce into.  'self' is the honest bound, and 'unsafe-inline' is
#   deliberately absent so an injected inline script still cannot run.
#
# ``style-src`` needs 'unsafe-inline' because React sets element style attributes
# (motion animates transforms inline).  Tightening that means removing every inline
# style from the component tree, which is a frontend refactor rather than a header
# change.
CSP = "; ".join([
    "default-src 'self'",
    "base-uri 'self'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "form-action 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: https://cdn.discordapp.com https://i.ytimg.com https://i.scdn.co",
    "font-src 'self' data:",
    # The SPA talks to its own origin only; the OAuth hop is a top-level navigation,
    # which connect-src does not govern.
    "connect-src 'self'",
    "manifest-src 'self'",
    "worker-src 'self'",
])

HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    # frame-ancestors above supersedes this for modern browsers; kept for older ones.
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    # Nothing here uses these, so deny them rather than leave the defaults.
    "Permissions-Policy": "geolocation=(self), camera=(), microphone=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


def install(app: Flask) -> None:
    """Attach the headers to every response the app produces."""

    @app.after_request
    def _security_headers(response):
        for name, value in HEADERS.items():
            response.headers.setdefault(name, value)
        # HSTS only over https, and only when the deployment is actually https --
        # sending it from a plain-http dev server would pin localhost to https in the
        # browser for a year.
        if app.config.get("FORCE_HTTPS_HEADERS"):
            response.headers.setdefault("Strict-Transport-Security",
                                        "max-age=31536000; includeSubDomains")
        return response
