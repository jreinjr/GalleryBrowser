"""One-off (2026-10-16): regroup every stored `hours` list into the grouped
form ("Tue–Sat 11–6, closed Sun–Mon") for all cities: the venue registries
(content/venues/<city>.json, primary and `locations`), the published show
files (content/<city>.json) and the pending ones (content/pending/<city>.json).
Google's raw `google.hours` stays as evidence. Safe to re-run (idempotent).

    scraper/.venv/bin/python scraper/migrate_grouped_hours.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hours import group_hours  # noqa: E402

CONTENT = Path(__file__).resolve().parents[1] / "content"


def _fmt(path: Path) -> tuple[int, bool]:
    raw = path.read_text()
    data = json.loads(raw)
    for ind in (1, 2, None):
        out = json.dumps(data, indent=ind, ensure_ascii=False)
        if out == raw or out + "\n" == raw:
            return ind, out + "\n" == raw
    return 2, raw.endswith("\n")


def _write(path: Path, data, fmt) -> None:
    ind, nl = fmt
    path.write_text(json.dumps(data, indent=ind, ensure_ascii=False) + ("\n" if nl else ""))


def main() -> None:
    changed = 0
    for f in sorted((CONTENT / "venues").glob("*.json")):
        fmt = _fmt(f)
        reg = json.loads(f.read_text())
        if "venues" not in reg:
            continue
        n = 0
        for v in reg["venues"]:
            for rec in [v] + list(v.get("locations") or []):
                if rec.get("hours"):
                    g = group_hours(rec["hours"])
                    if g != rec["hours"]:
                        rec["hours"] = g
                        n += 1
        if n:
            _write(f, reg, fmt)
        print(f"{f.relative_to(CONTENT)}: {n} venue hours regrouped")
        changed += n
    for f in sorted(list(CONTENT.glob("*.json")) + list((CONTENT / "pending").glob("*.json"))):
        fmt = _fmt(f)
        data = json.loads(f.read_text())
        if not isinstance(data, dict) or "shows" not in data:
            continue
        n = 0
        for s in data["shows"]:
            v = s.get("venue") or {}
            if v.get("hours"):
                g = group_hours(v["hours"])
                if g != v["hours"]:
                    v["hours"] = g
                    n += 1
        if n:
            _write(f, data, fmt)
        print(f"{f.relative_to(CONTENT)}: {n} show hours regrouped")
        changed += n
    print("total", changed)


if __name__ == "__main__":
    main()
