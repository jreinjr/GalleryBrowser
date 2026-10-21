"""Show-image pipeline: trim plain edge bands, resize/recompress source JPEGs
to WebP with a disk cache.

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

from PIL import Image, ImageChops, ImageFilter

CACHE_DIR = Path(__file__).resolve().parent / ".cache" / "img"

# Emit a full-res variant whenever the source is any wider than the proxy, so
# zooming always reaches the source's own resolution.
FULL_MIN_GAIN = 1.0


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


# ---- trimming baked-in margins ----
# Show pictures often arrive with a band of wall, white page or backdrop on one
# or more sides. The app frames every picture in its own thin white border, so a
# plain band along an edge is cut away first: an edge whose outer strip is one
# even pale gray or white is trimmed inward until the rows (or columns) stop being that
# color, or further while every row is still pale and neutral, which follows
# wall that fades or picks up a soft shadow toward the floor (the strip left
# under paintings when only the edge color was matched). Edges where the art or
# a varied photo reaches the frame are left alone.
# Gallery photos and artist bio cards are never trimmed. Boxes are memoised by
# content hash; TRIM_VERSION is part of every trimmed picture's cache key.
TRIM_VERSION = 4
TRIM_TOL = 12          # a pixel further than this (max channel) from the edge color is art
TRIM_EDGE_TOL = 20     # an edge counts as margin when TRIM_EDGE_EVEN of its outer
TRIM_EDGE_EVEN = 0.95  # strip is within TRIM_EDGE_TOL of the strip's median color
TRIM_ROW_CONTENT = 0.01  # a row/column with more than this share of non-margin pixels is art
TRIM_MIN_SIDE = 0.005  # ignore trims thinner than this share of the side
# only a pale, near-gray edge counts as wall or page: a colored or dark edge is
# much more likely the work's own ground (a pink painting, a night photograph)
TRIM_MIN_LIGHT = 170   # darkest channel of the edge color
TRIM_MAX_TINT = 25     # spread between its channels
TRIM_PALE_LIGHT = 150  # the pale-row walk: a pixel is wall when its darkest channel is
TRIM_PALE_TINT = 25    # at least this and its channels spread no more than this
# Last, any edge of what is left that is still mostly near-white (an off-white
# rim, a soft antialiased border, a sliver of page) is shaved off, cutting a
# little into the art if needed, so no white shows inside the picture's frame:
# on a side that was cut (or that ends in a white rim), rows are shaved while
# TRIM_SHAVE_SHARE of them is near-white, up to TRIM_SHAVE_MAX of the side.
# Every cut side then loses TRIM_SHAVE_INSET more. Never further: a sparse
# line drawing on white paper is mostly white too.
TRIM_SHAVE_LIGHT = 200
TRIM_SHAVE_TINT = 30
TRIM_SHAVE_SHARE = 0.6
TRIM_SHAVE_MAX = 0.025
TRIM_SHAVE_RIM = 0.85   # an untrimmed side is only shaved when its outer row is this white
TRIM_SHAVE_INSET = 0.006
_TRIM_FILE = CACHE_DIR.parent / "img-trim.json"
_trims: dict[str, list | None] | None = None


def trimmable(src: Path) -> bool:
    return "venues" not in src.parts and not src.name.startswith("bio-")


def _maxdiff(img: Image.Image, color: tuple) -> Image.Image:
    """Per-pixel max channel distance from color, as an L image."""
    diff = ImageChops.difference(img, Image.new("RGB", img.size, color))
    r, g, b = diff.split()
    return ImageChops.lighter(ImageChops.lighter(r, g), b)


def _median(img: Image.Image) -> tuple:
    return tuple(sorted(c.getdata())[c.width * c.height // 2] for c in img.split())


def find_trim(img: Image.Image) -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) crop box without the plain edge bands, or None."""
    W, H = img.size
    a = img.convert("RGB")
    s = min(1.0, 800 / max(W, H))
    a = a.resize((max(1, round(W * s)), max(1, round(H * s))), Image.BOX)
    w, h = a.size
    if w < 40 or h < 40:
        return None
    # wall-like pixels for the pale-row walk (median-filtered against JPEG noise)
    r_, g_, b_ = a.filter(ImageFilter.MedianFilter(3)).split()
    lo = ImageChops.darker(ImageChops.darker(r_, g_), b_)
    hi = ImageChops.lighter(ImageChops.lighter(r_, g_), b_)
    art = ImageChops.lighter(lo.point(lambda v: 255 if v < TRIM_PALE_LIGHT else 0),
                             ImageChops.difference(hi, lo).point(lambda v: 255 if v > TRIM_PALE_TINT else 0))
    k = 3
    strips = {"top": (0, 0, w, k), "bottom": (0, h - k, w, h),
              "left": (0, 0, k, h), "right": (w - k, 0, w, h)}
    cut, edge = {}, {}
    for side, box in strips.items():
        strip = a.crop(box)
        color = _median(strip)
        if min(color) < TRIM_MIN_LIGHT or max(color) - min(color) > TRIM_MAX_TINT:
            cut[side] = edge[side] = 0
            continue
        even = _maxdiff(strip, color).point(lambda v: 255 if v <= TRIM_EDGE_TOL else 0)
        if sum(even.getdata()) / 255 / (strip.width * strip.height) < TRIM_EDGE_EVEN:
            cut[side] = edge[side] = 0
            continue
        mask = _maxdiff(a, color).point(lambda v: 255 if v > TRIM_TOL else 0)
        mask = mask.filter(ImageFilter.MedianFilter(3))
        if side in ("top", "bottom"):
            prof = list(mask.resize((1, h), Image.BOX).getdata())
        else:
            prof = list(mask.resize((w, 1), Image.BOX).getdata())
        if side in ("bottom", "right"):
            prof.reverse()
        n = 0
        while n < len(prof) and prof[n] / 255 <= TRIM_ROW_CONTENT:
            n += 1
        if side in ("top", "bottom"):
            pale = list(art.resize((1, h), Image.BOX).getdata())
        else:
            pale = list(art.resize((w, 1), Image.BOX).getdata())
        if side in ("bottom", "right"):
            pale.reverse()
        m = 0
        while m < len(pale) and pale[m] / 255 <= TRIM_ROW_CONTENT:
            m += 1
        edge[side], cut[side] = n, max(n, m)

    def framed_work(c: dict) -> bool:
        """Wall on all four sides and a solid straight edge on each: a hung
        painting or frame, not a sculpture or drawing whose box only touches it."""
        if not all(c[k] >= TRIM_MIN_SIDE * (w if k in ("left", "right") else h) for k in c):
            return False
        l, t, r, b = c["left"], c["top"], w - c["right"], h - c["bottom"]
        if r - l < 4 or b - t < 4:
            return False
        for box in ((l, t, r, t + 2), (l, b - 2, r, b), (l, t, l + 2, b), (r - 2, t, r, b)):
            band = art.crop(box)
            if sum(band.getdata()) / 255 / (band.width * band.height) < 0.6:
                return False
        return True

    def keeps(c: dict, area: float) -> bool:
        kw, kh = w - c["left"] - c["right"], h - c["top"] - c["bottom"]
        if kw >= 0.12 * w and kh >= 0.12 * h and framed_work(c):
            # a painting or frame hung on a wall: cropped tight however small it hangs
            return True
        return kw >= 0.25 * w and kh >= 0.25 * h and kw * kh >= area * w * h
    # the pale walk may only follow wall, never swallow most of the picture: an
    # installation view with a white wall down to the floor, or a pale work,
    # keeps the edge-color trim alone
    if not keeps(cut, 0.4):
        cut = edge
        if not keeps(cut, 0):
            return None                               # mostly "margin": a pale or plain work
    for side, n in cut.items():
        if n < TRIM_MIN_SIDE * (w if side in ("left", "right") else h):
            cut[side] = 0
    # shave near-white rims off what is left
    white = ImageChops.darker(lo.point(lambda v: 255 if v >= TRIM_SHAVE_LIGHT else 0),
                              ImageChops.difference(hi, lo).point(lambda v: 255 if v <= TRIM_SHAVE_TINT else 0))
    kept = white.crop((cut["left"], cut["top"], w - cut["right"], h - cut["bottom"]))
    kw, kh = kept.size
    rows = list(kept.resize((1, kh), Image.BOX).getdata())
    cols = list(kept.resize((kw, 1), Image.BOX).getdata())
    shaved = dict(cut)
    for side, prof, size in (("top", rows, h), ("bottom", rows[::-1], h),
                             ("left", cols, w), ("right", cols[::-1], w)):
        n = 0
        # a side already cut, or one whose outer row is a white rim, is shaved a
        # little further (into the art if need be) while its rows stay mostly white
        if cut[side] or (prof and prof[0] / 255 >= TRIM_SHAVE_RIM):
            while n < min(len(prof), TRIM_SHAVE_MAX * size) and prof[n] / 255 >= TRIM_SHAVE_SHARE:
                n += 1
        if n or cut[side]:
            shaved[side] = cut[side] + n + max(1, round(TRIM_SHAVE_INSET * size))
    if keeps(shaved, 0.3):
        cut = shaved
    if not any(cut.values()):
        return None
    box = (round(cut["left"] / s), round(cut["top"] / s),
           W - round(cut["right"] / s), H - round(cut["bottom"] / s))
    return box


def _trim_key(src: Path) -> str:
    return f"{_content_hash(src)}|v{TRIM_VERSION}"


def _load_trims() -> dict:
    global _trims
    if _trims is None:
        try:
            _trims = json.loads(_TRIM_FILE.read_text())
        except Exception:
            _trims = {}
    return _trims


def _trim_job(src: Path):
    with Image.open(src) as im:
        im.load()
        return find_trim(im)


def trim_box(src: Path) -> tuple | None:
    if not trimmable(src):
        return None
    trims = _load_trims()
    key = _trim_key(src)
    if key not in trims:
        box = _trim_job(src)
        trims[key] = list(box) if box else None
    box = trims[key]
    return tuple(box) if box else None


def save_trims() -> None:
    if _trims is not None:
        _TRIM_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TRIM_FILE.write_text(json.dumps(_trims))


def source_width(src: Path) -> int:
    box = trim_box(src)
    if box:
        return box[2] - box[0]
    with Image.open(src) as im:
        return im.width


def _cached_path(src: Path, quality: int, max_width: int | None, max_side: int | None) -> Path:
    box = trim_box(src)
    trim = f"|trim{TRIM_VERSION}:{','.join(map(str, box))}" if box else ""
    key = hashlib.sha1(
        f"{_content_hash(src)}|{max_width}|{max_side}|{quality}{trim}".encode()
    ).hexdigest()
    cached = CACHE_DIR / f"{key}.webp"
    if not cached.exists() and not box:
        legacy = CACHE_DIR / (hashlib.sha1(
            f"{src}|{src.stat().st_mtime_ns}|{max_width}|{max_side}|{quality}".encode()
        ).hexdigest() + ".webp")
        if legacy.exists():
            legacy.rename(cached)
    return cached


def _encode(src: Path, cached: Path, quality: int,
            max_width: int | None, max_side: int | None,
            box: tuple | None = None) -> None:
    cached.parent.mkdir(parents=True, exist_ok=True)
    img = Image.open(src)
    img.load()
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    if box:
        img = img.crop(box)
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
         full_side: int = 6000, full_quality: int = 80) -> int:
    """Encode every proxy and full-res variant missing from the cache, on all
    cores; the build's own (sequential) pass then only copies. Returns how many
    were encoded."""
    srcs = [s for s in dict.fromkeys(srcs) if s.is_file()]
    # trim boxes first, on all cores: every cache key below depends on them
    trims = _load_trims()
    todo = [s for s in srcs if trimmable(s) and _trim_key(s) not in trims]
    if todo:
        print(f"  finding margins in {len(todo)} images on {os.cpu_count()} cores")
        with ProcessPoolExecutor() as pool:
            for src, box in zip(todo, pool.map(_trim_job, todo, chunksize=4)):
                trims[_trim_key(src)] = list(box) if box else None
        save_trims()
    jobs = []
    for src in srcs:
        box = trim_box(src)
        cached = _cached_path(src, quality, max_width, None)
        if not cached.exists():
            jobs.append((src, cached, quality, max_width, None, box))
        if full_side:
            if source_width(src) > max_width * FULL_MIN_GAIN:
                cached = _cached_path(src, full_quality, None, full_side)
                if not cached.exists():
                    jobs.append((src, cached, full_quality, None, full_side, box))
    save_hashes()
    save_trims()
    if jobs:
        print(f"  encoding {len(jobs)} images on {os.cpu_count()} cores")
        with ProcessPoolExecutor() as pool:
            list(pool.map(_encode_job, jobs, chunksize=4))
    return len(jobs)


def _convert(src: Path, dest: Path, quality: int,
             max_width: int | None = None, max_side: int | None = None) -> None:
    cached = _cached_path(src, quality, max_width, max_side)
    if not cached.exists():
        _encode(src, cached, quality, max_width, max_side, trim_box(src))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(cached.read_bytes())


def process(src: Path, dest: Path, max_width: int = 1080, quality: int = 78) -> None:
    """Convert one source image to a proxy WebP at dest."""
    _convert(src, dest, quality, max_width=max_width)


def process_full(src: Path, dest: Path, proxy_width: int = 1080,
                 max_side: int = 6000, quality: int = 80) -> bool:
    """Emit a full-res variant at dest if the source out-resolves the proxy.

    Returns False (writing nothing) when the proxy already is max-res.
    """
    if source_width(src) <= proxy_width * FULL_MIN_GAIN:
        return False
    _convert(src, dest, quality, max_side=max_side)
    return True
