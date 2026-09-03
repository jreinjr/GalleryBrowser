"""Fill the registry's venue `tier` / `notability` slots from curation evidence.

notability (0-1) = 0.50 * min(1, fair_listings / 3)          # distinct fairs the venue exhibited at
                 + 0.30 * min(1, press_sources / 3)          # distinct outlets that picked/reviewed a show there
                 + 0.20 * museum                              # capped at 1.0
                 (+ seesaw_weight * seesaw_presence — OFF by default: See Saw is the benchmark,
                  feeding it into features would leak the answer into the overlap metric.
                  --seesaw-weight 0.3 turns it on for "learn from See Saw" experiments only.)
tier: >= 0.55 -> 1 (refresh weekly), >= 0.25 -> 2 (biweekly), > 0 -> 3, else null

Also stores `notability_breakdown` so the dashboard/report can show why.
    python venue_tiers.py --city los-angeles [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import curation_store as store  # noqa: E402
import tools  # noqa: E402
import venues  # noqa: E402
from cities import CITIES  # noqa: E402

PRESS_KINDS = {"pick", "review", "news"}


def seesaw_venue_norms(city: str) -> tuple[set[str], set[str]]:
    """(venue norms on the latest Featured snapshot, on the latest 'all' snapshot)."""
    featured, everything = set(), set()
    files = sorted(store.list_snapshots(city))
    latest_by_tab: dict[str, dict] = {}
    for f in files:
        try:
            snap = json.loads(Path(f).read_text())
        except (OSError, json.JSONDecodeError):
            continue
        latest_by_tab[snap.get("tab", "featured")] = snap
    for e in latest_by_tab.get("featured", {}).get("entries", []):
        featured.add(tools._norm_venue(e.get("venue") or ""))
    for tab in ("all", "editors_picks"):
        for e in latest_by_tab.get(tab, {}).get("entries", []):
            everything.add(tools._norm_venue(e.get("venue") or ""))
    return featured, everything


def compute(city: str, seesaw_weight: float = 0.0) -> dict[str, dict]:
    reg = venues.load_registry(city)
    signals = store.collapse_signals(store.load_signals(city))
    matches = store.load_matches(city)
    fairs: dict[str, set[str]] = {}
    press: dict[str, set[str]] = {}
    for s in signals:
        m = matches.get(s["id"], {})
        norm = m.get("venue_norm") or tools._norm_venue(s["show_ref"].get("venue") or "")
        if not norm:
            continue
        if s["kind"] == "fair_exhibitor":
            fairs.setdefault(norm, set()).add(s["source"]["id"])
        elif s["kind"] in PRESS_KINDS:
            press.setdefault(norm, set()).add(s["source"]["id"])
    feat, allv = seesaw_venue_norms(city)
    out: dict[str, dict] = {}
    for v in reg["venues"]:
        norms = {tools._norm_venue(v["name"])} | {tools._norm_venue(a) for a in v.get("aliases", [])}
        nf = len(set().union(*(fairs.get(n, set()) for n in norms)))
        npress = len(set().union(*(press.get(n, set()) for n in norms)))
        ss = 1.0 if norms & feat else (0.5 if norms & allv else 0.0)
        museum = 1.0 if venues.is_museum(v) else 0.0
        score = min(1.0, 0.50 * min(1, nf / 3) + 0.30 * min(1, npress / 3) + 0.20 * museum
                    + seesaw_weight * ss)
        tier = 1 if score >= 0.55 else 2 if score >= 0.25 else 3 if score > 0 else None
        out[v["id"]] = {"notability": round(score, 3), "tier": tier,
                        "breakdown": {"fairs": nf, "press_sources": npress, "museum": museum,
                                      "seesaw": ss, "seesaw_weight": seesaw_weight}}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", required=True, choices=sorted(CITIES))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--seesaw-weight", type=float, default=0.0,
                    help="LEAKAGE: weight for See Saw presence (default 0 keeps the benchmark honest)")
    args = ap.parse_args()
    if args.seesaw_weight:
        print(f"WARNING: seesaw_weight={args.seesaw_weight} feeds the benchmark into venue tiers; "
              "overlap metrics are no longer an honest test")
    scores = compute(args.city, args.seesaw_weight)
    ranked = sorted(scores.items(), key=lambda kv: -kv[1]["notability"])
    tiers = {}
    for _, s in scores.items():
        tiers[s["tier"]] = tiers.get(s["tier"], 0) + 1
    print(f"{args.city}: {len(scores)} venues; tiers {tiers}")
    for vid, s in ranked[:25]:
        print(f"  {s['notability']:.2f} t{s['tier'] or '-'} {vid:<32} {s['breakdown']}")
    if args.dry_run:
        return
    with venues.locked_registry(args.city) as reg:
        for v in reg["venues"]:
            s = scores.get(v["id"])
            if not s:
                continue
            v["tier"], v["notability"] = s["tier"], s["notability"]
            v["notability_breakdown"] = s["breakdown"]
            # Tier changes the cadence: recompute next_check except where a skip
            # set a specific reopening date (no active show + a last_skip).
            if venues.active_show(v) or not v.get("last_skip"):
                v["next_check"] = venues._iso(venues.compute_next_check(v))
    print("registry updated")


if __name__ == "__main__":
    main()
