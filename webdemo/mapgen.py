"""Static city basemap generation for the venue-page map card.

Fetches CARTO dark raster tiles, stitches them per city with Web Mercator
math, and returns metadata for projecting venue lat/lng to image pixels.
Tiles are cached forever in .cache/tiles/.
"""

from __future__ import annotations

import io
import math
import time
from pathlib import Path

import requests
from PIL import Image

TILE = 256            # CSS px per tile
TILE_PX = 512         # @2x actual px
CACHE_DIR = Path(__file__).resolve().parent / ".cache" / "tiles"
UA = "GalleryBrowser-demo-build/1.0 (contact: repo owner)"
SUBDOMAINS = "abcd"

ATTRIBUTION = "© OpenStreetMap contributors © CARTO"


def _world_px(lat: float, lng: float, zoom: int) -> tuple[float, float]:
    """Web Mercator world pixel coords (CSS px) at a zoom level."""
    n = 2 ** zoom
    x = TILE * n * (lng + 180.0) / 360.0
    phi = math.radians(lat)
    y = TILE * n * (1.0 - math.log(math.tan(phi) + 1.0 / math.cos(phi)) / math.pi) / 2.0
    return x, y


def _fetch_tile(z: int, x: int, y: int) -> Image.Image:
    cached = CACHE_DIR / str(z) / str(x) / f"{y}.png"
    if not cached.exists():
        cached.parent.mkdir(parents=True, exist_ok=True)
        sub = SUBDOMAINS[(x + y) % len(SUBDOMAINS)]
        url = f"https://{sub}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png"
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=30)
        resp.raise_for_status()
        cached.write_bytes(resp.content)
        time.sleep(0.1)
    return Image.open(io.BytesIO(cached.read_bytes())).convert("RGB")


def build_city_map(points: list[tuple[float, float]], center: tuple[float, float],
                   max_w: int = 1280, max_h: int = 1600) -> tuple[Image.Image, dict]:
    """Stitch a basemap covering all points; return (image, projection meta).

    meta: {zoom, originX, originY, scale} where pin px =
    (2*world_px(lat,lng,zoom) - origin) * scale on the returned image.
    """
    lats = [p[0] for p in points] + [center[0]]
    lngs = [p[1] for p in points] + [center[1]]
    lat0, lat1 = min(lats), max(lats)
    lng0, lng1 = min(lngs), max(lngs)
    pad_lat = max((lat1 - lat0) * 0.2, 0.01)
    pad_lng = max((lng1 - lng0) * 0.2, 0.01)
    lat0, lat1 = lat0 - pad_lat, lat1 + pad_lat
    lng0, lng1 = lng0 - pad_lng, lng1 + pad_lng

    zoom = 9
    for z in range(15, 8, -1):
        x0, y0 = _world_px(lat1, lng0, z)   # top-left (max lat = min y)
        x1, y1 = _world_px(lat0, lng1, z)
        if (x1 - x0) * 2 <= max_w * 1.6 and (y1 - y0) * 2 <= max_h * 1.6:
            zoom = z
            break

    x0, y0 = _world_px(lat1, lng0, zoom)
    x1, y1 = _world_px(lat0, lng1, zoom)
    tx0, tx1 = int(x0 // TILE), int(x1 // TILE)
    ty0, ty1 = int(y0 // TILE), int(y1 // TILE)

    stitched = Image.new("RGB", ((tx1 - tx0 + 1) * TILE_PX, (ty1 - ty0 + 1) * TILE_PX))
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            stitched.paste(_fetch_tile(zoom, tx, ty), ((tx - tx0) * TILE_PX, (ty - ty0) * TILE_PX))

    scale = min(1.0, max_w / stitched.width, max_h / stitched.height)
    if scale < 1.0:
        stitched = stitched.resize(
            (round(stitched.width * scale), round(stitched.height * scale)), Image.LANCZOS)

    meta = {"zoom": zoom, "originX": tx0 * TILE_PX, "originY": ty0 * TILE_PX, "scale": scale}
    return stitched, meta


def project(lat: float, lng: float, meta: dict) -> tuple[float, float]:
    """Venue lat/lng -> pixel coords on the stitched (possibly downscaled) image."""
    wx, wy = _world_px(lat, lng, meta["zoom"])
    return ((wx * 2 - meta["originX"]) * meta["scale"],
            (wy * 2 - meta["originY"]) * meta["scale"])
