"""The SPA's route table, on the Python side.

There was no such table: `spa.py` answered *every* unknown path with
`index.html` and HTTP **200**, so `/nonsense` was an indexable soft 404 that
rendered the NotFound screen. A crawler that finds a hundred 200s all containing
"page not found" is being told the site has a hundred pages.

Kept here rather than derived, because the only other list of routes is
`App.tsx` and Flask cannot read it. `tests/test_spa.py` compares the two so they
cannot drift.
"""

from __future__ import annotations

import re

# Public, indexable pages. The order does not matter; these are matched, not
# ranked.
PUBLIC_ROUTES = (
    "/",
    "/weather",
    "/commands",
    "/privacy",
    "/terms",
    "/settings",
)

# Real routes that must answer 200 but must not be indexed: the internal review
# surface, the sign-in screen, and everything behind auth. A crawler reaching
# these gets an empty shell, and indexing that is worse than not indexing it.
PRIVATE_ROUTES = (
    "/kitchen-sink",
    "/login",
    "/g",
)

# `/g/<snowflake>` and its five sub-pages. A pattern rather than an enumeration
# because the guild id is unbounded.
GUILD_ROUTE = re.compile(
    r"^/g/\d+(?:/(?:music|weather-alerts|ai|settings|audit))?$"
)


def is_known(path: str) -> bool:
    """Whether ``path`` is a route the SPA actually renders."""
    normalised = "/" + path.strip("/") if path.strip("/") else "/"
    if normalised in PUBLIC_ROUTES or normalised in PRIVATE_ROUTES:
        return True
    return bool(GUILD_ROUTE.match(normalised))


def is_indexable(path: str) -> bool:
    normalised = "/" + path.strip("/") if path.strip("/") else "/"
    return normalised in PUBLIC_ROUTES


def sitemap(origin: str) -> str:
    """A sitemap of the public routes only.

    No lastmod: these are hand-maintained pages with no build timestamp worth
    publishing, and a wrong lastmod is worse than none.
    """
    origin = origin.rstrip("/")
    entries = "".join(
        f"  <url><loc>{origin}{route}</loc></url>\n" for route in PUBLIC_ROUTES
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}"
        "</urlset>\n"
    )


def robots(origin: str) -> str:
    """Disallow the private routes and the API, and point at the sitemap."""
    origin = origin.rstrip("/")
    disallowed = "".join(f"Disallow: {route}\n" for route in (*PRIVATE_ROUTES, "/api/"))
    return (
        "User-agent: *\n"
        f"{disallowed}"
        "Allow: /\n"
        f"\nSitemap: {origin}/sitemap.xml\n"
    )
