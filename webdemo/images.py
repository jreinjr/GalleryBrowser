"""Show-image pipeline: resize/recompress source JPEGs to WebP with a disk cache.

Two variants per image: a proxy (width-capped, quality-lean) that every surface
loads first, and — when the source meaningfully out-resolves the proxy — a
full-res variant (longest-side-capped) that the app swaps in on pinch/viewer.
"""

from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import Image

CACHE_DIR = Path(__file__).resolve().parent / ".cache" / "img"

# Emit a full-res variant only when the source is at least this much wider than
# the proxy; below it the "upgrade" would be an imperceptible near-duplicate.
FULL_MIN_GAIN = 1.25


# The cache is keyed on the source's bytes, not its mtime: a CI checkout gives
# every file a fresh mtime, so an mtime key never hit there and each deploy
# recompressed everything. Hashes are memoised by (path, size, mtime) so local
# rebuilds don't re-read 4 GB; entries under the older mtime key are reused.
_HASHES_FILE = CACHE_DIR.parent / "img-hashes.json"
_hashes: dict[str, list] | None = None


def _content_hash(src: Path) -> str:
    global _hashes
    if _hashes is None:
        try:
            _hashes = json.loads(_HASHES_FILE.read_text())
        except Exception:
            _hashes = {}
    st = src.stat()
    hit = _hashes.get(str(src))
    if hit and hit[0] == st.st_size and hit[1] == st.st_mtime_ns:
        return hit[2]
    h = hashlib.sha1(src.read_bytes()).hexdigest()
    _hashes[str(src)] = [st.st_size, st.st_mtime_ns, h]
    return h


def save_hashes() -> None:
    if _hashes is not None:
        _HASHES_FILE.parent.mkdir(parents=True, exist_ok=True)
        _HASHES_FILE.write_text(json.dumps(_hashes))


def _cached_path(src: Path, quality: int, max_width: int | None, max_side: int | None) -> Path:
    key = hashlib.sha1(
        f"{_content_hash(src)}|{max_width}|{max_side}|{quality}".encode()
    ).hexdigest()
    cached = CACHE_DIR / f"{key}.webp"
    if not cached.exists():
        legacy = CACHE_DIR / (hashlib.sha1(
            f"{src}|{src.stat().st_mtime_ns}|{max_width}|{max_side}|{quality}".encode()
        ).hexdigest() + ".webp")
        if legacy.exists():
            legacy.rename(cached)
    return cached


def _encode(src: Path, cached: Path, quality: int,
            max_width: int | None, max_side: int | None) -> None:
    cached.parent.mkdir(parents=True, exist_ok=True)
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
    tmp = cached.with_suffix(f".{os.getpid()}.tmp")
    img.save(tmp, "WEBP", quality=quality, method=6)
    tmp.replace(cached)


def _encode_job(job: tuple) -> None:
    _encode(*job)


def warm(srcs, max_width: int = 1080, quality: int = 78,
         full_side: int = 3840, full_quality: int = 80) -> int:
    """Encode every proxy and full-res variant missing from the cache, on all
    cores; the build's own (sequential) pass then only copies. Returns how many
    were encoded."""
    jobs = []
    for src in dict.fromkeys(srcs):
        if not src.is_file():
            continue
        cached = _cached_path(src, quality, max_width, None)
        if not cached.exists():
            jobs.append((src, cached, quality, max_width, None))
        if full_side:
            with Image.open(src) as im:
                w = im.width
            if w >= max_width * FULL_MIN_GAIN:
                cached = _cached_path(src, full_quality, None, full_side)
                if not cached.exists():
                    jobs.append((src, cached, full_quality, None, full_side))
    save_hashes()
    if jobs:
        print(f"  encoding {len(jobs)} images on {os.cpu_count()} cores")
        with ProcessPoolExecutor() as pool:
            list(pool.map(_encode_job, jobs, chunksize=4))
    return len(jobs)


def _convert(src: Path, dest: Path, quality: int,
             max_width: int | None = None, max_side: int | None = None) -> None:
    cached = _cached_path(src, quality, max_width, max_side)
    if not cached.exists():
        _encode(src, cached, quality, max_width, max_side)
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
