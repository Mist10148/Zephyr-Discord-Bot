"""Generate the PWA icon set for the Zephyr website.

Committed alongside the PNGs it produces so the icons are reproducible rather than
opaque binaries: change a colour here, re-run, and the whole set stays consistent.

The artwork is derived from the site's own design tokens in
``website/frontend/src/styles/theme.css`` -- the same radial-gradient backdrop and
accent blue the app uses -- so the installed icon matches the app it opens.

    python -m scripts.generate_icons

Prefer a photographic icon (the bot avatar, say)?  Pass a source image:

    python -m scripts.generate_icons --source roxy.jpg

Requires Pillow, which is already an optional dependency in requirements.txt.
"""

import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "website" / "frontend" / "public" / "icons"

# theme.css: body background gradients over #0b1020, link/accent #8ab4ff.
BASE = (11, 16, 32)
GLOWS = (
    (0.15, 0.10, 0.62, (49, 86, 129)),    # #315681
    (0.80, 0.80, 0.66, (78, 54, 90)),     # #4e365a
)
MARK = (232, 240, 255)
MARK_SHADOW = (10, 132, 255)              # #0a84ff

# A maskable icon may be cropped to a circle of 80% diameter, so the mark has to sit
# well inside that. 0.52 keeps it comfortable in both a circle and a squircle.
MASKABLE_SCALE = 0.52
STANDARD_SCALE = 0.72


def _backdrop(size: int) -> Image.Image:
    """The app's radial-gradient backdrop, rendered at ``size``."""
    image = Image.new("RGB", (size, size), BASE)
    pixels = image.load()
    for y in range(size):
        fy = y / size
        for x in range(size):
            fx = x / size
            red, green, blue = BASE
            for cx, cy, radius, colour in GLOWS:
                distance = math.hypot(fx - cx, fy - cy) / radius
                if distance >= 1.0:
                    continue
                weight = (1.0 - distance) ** 1.6
                red += int((colour[0] - red) * weight)
                green += int((colour[1] - green) * weight)
                blue += int((colour[2] - blue) * weight)
            pixels[x, y] = (red, green, blue)
    return image


def _z_polygon(size: int, scale: float) -> list[tuple[float, float]]:
    """A blocky 'Z' for Zephyr.

    Drawn as a polygon rather than typeset: no font file is guaranteed to exist on
    the machine running this, and a hand-built mark stays legible down to 16px where
    an anti-aliased glyph turns to mush.
    """
    span = size * scale
    left = (size - span) / 2
    top = (size - span * 0.94) / 2
    width = span
    height = span * 0.94
    bar = height * 0.22           # thickness of the horizontal strokes
    slant = width * 0.30          # thickness of the diagonal, measured horizontally

    x0, y0 = left, top
    x1, y1 = left + width, top + height
    return [
        (x0, y0), (x1, y0), (x1, y0 + bar),                 # top bar
        (x0 + slant, y1 - bar),                              # diagonal, inner edge
        (x1, y1 - bar), (x1, y1), (x0, y1), (x0, y1 - bar),  # bottom bar
        (x1 - slant, y0 + bar),                              # diagonal, outer edge
        (x0, y0 + bar),
    ]


def _render(size: int, *, scale: float, source: Image.Image | None = None) -> Image.Image:
    if source is not None:
        image = source.convert("RGB").resize((size, size), Image.LANCZOS)
        if scale >= STANDARD_SCALE:
            return image
        # Maskable: inset the photo on the app's backdrop so nothing important is
        # cropped away by the platform's mask.
        canvas = _backdrop(size)
        inset = int(size * scale)
        canvas.paste(image.resize((inset, inset), Image.LANCZOS),
                     ((size - inset) // 2, (size - inset) // 2))
        return canvas

    image = _backdrop(size)
    draw = ImageDraw.Draw(image, "RGBA")
    points = _z_polygon(size, scale)
    offset = max(1, size // 96)
    draw.polygon([(x + offset, y + offset) for x, y in points], fill=(*MARK_SHADOW, 150))
    draw.polygon(points, fill=(*MARK, 255))
    return image


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", help="optional image to use instead of the generated mark")
    args = parser.parse_args()

    source = Image.open(PROJECT_ROOT / args.source) if args.source else None
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    written = []
    for size in (192, 512):
        path = OUTPUT_DIR / f"icon-{size}.png"
        _render(size, scale=STANDARD_SCALE, source=source).save(path, optimize=True)
        written.append(path)

    for size in (192, 512):
        path = OUTPUT_DIR / f"maskable-{size}.png"
        _render(size, scale=MASKABLE_SCALE, source=source).save(path, optimize=True)
        written.append(path)

    # iOS ignores the manifest and reads apple-touch-icon; it also applies its own
    # rounding, so this uses the standard (un-inset) artwork.
    apple = OUTPUT_DIR / "apple-touch-icon.png"
    _render(180, scale=STANDARD_SCALE, source=source).save(apple, optimize=True)
    written.append(apple)

    favicon = OUTPUT_DIR / "favicon.ico"
    _render(256, scale=STANDARD_SCALE, source=source).save(
        favicon, sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    written.append(favicon)

    for path in written:
        print(f"{path.relative_to(PROJECT_ROOT)}  {path.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
