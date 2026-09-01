"""Show-image pipeline: resize/recompress source JPEGs to WebP with a disk cache.

Two variants per image: a proxy (width-capped, quality-lean) that every surface
loads first, and — when the source meaningfully out-resolves the proxy — a
full-res variant (longest-side-capped) that the app swaps in on pinch/viewer.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image

CACHE_DIR = Path(__file__).resolve().parent / ".cache" / "img"

# Emit a full-res variant only when the source is at least this much wider than
# the proxy; below it the "upgrade" would be an imperceptible near-duplicate.
FULL_MIN_GAIN = 1.25


def _convert(src: Path, dest: Path, quality: int,
             max_width: int | None = None, max_side: int | None = None) -> None:
    key = hashlib.sha1(
        f"{src}|{src.stat().st_mtime_ns}|{max_width}|{max_side}|{quality}".encode()
    ).hexdigest()
    cached = CACHE_DIR / f"{key}.webp"
    if not cached.exists():
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        img = Image.open(src)
        img.load()
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        w, h = img.size
        scale = 1.0
        if max_width and w > max_width:
            scale = max_width / w
        if max_side and max(w, h) > max_side:
            scale = min(scale, max_side / max(w, h))
        if scale < 1:
            img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
        img.save(cached, "WEBP", quality=quality, method=6)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(cached.read_bytes())


def process(src: Path, dest: Path, max_width: int = 1080, quality: int = 78) -> None:
    """Convert one source image to a proxy WebP at dest."""
    _convert(src, dest, quality, max_width=max_width)


def process_full(src: Path, dest: Path, proxy_width: int = 1080,
                 max_side: int = 3840, quality: int = 80) -> bool:
    """Emit a full-res variant at dest if the source out-resolves the proxy.

    Returns False (writing nothing) when the proxy already is max-res.
    """
    with Image.open(src) as im:
        w = im.width
    if w < proxy_width * FULL_MIN_GAIN:
        return False
    _convert(src, dest, quality, max_side=max_side)
    return True
