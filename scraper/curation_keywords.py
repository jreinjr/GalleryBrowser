"""Keyword lexicon for the free, deterministic ``keyword`` curation feature
(plan 2.2, keyword_v1 layer (a)).

Each class carries a weight and a list of case-insensitive regexes. A show's
keyword feature is ``min(1, sum of class weights over DISTINCT classes hit)``:
repeated hits inside one class count once, so a description that says
"retrospective" three times scores the same as one that says it once.

Scanned text (in ``curate.compute_features``): the show description plus the
snippets of press signals matched to the show. The curation prompts module
imports the same lexicon so the scrape agent's ``press_release_claim`` hints
use the identical vocabulary (layer (c)).
"""

from __future__ import annotations

import re

KEYWORDS: list[dict] = [
    {
        "class": "retrospective_survey",
        "label": "retrospective / survey",
        "weight": 1.0,
        "patterns": [
            r"\bretrospective\b",
            r"\bsurvey (?:exhibition|show|presentation|of (?:his|her|their|the artist))\b",
            r"\bcareer[- ]spanning\b",
            r"\b(?:three|four|five|six|seven|\d)[- ]decades?\b",
            r"\bmid[- ]career survey\b",
        ],
    },
    {
        "class": "first_solo",
        "label": "first US / LA / museum solo",
        "weight": 0.8,
        "patterns": [
            r"\bfirst (?:(?:major|ever|solo|institutional|museum|u\.?s\.?|american|"
            r"los angeles|l\.?a\.?|west coast|california|north american)\s+){1,3}"
            r"(?:solo\s+)?(?:exhibition|show|presentation|survey|outing)\b",
            r"\bfirst solo\b",
            r"\b(?:u\.?s\.?|american|los angeles|l\.?a\.?|west coast|museum|institutional) debut\b",
        ],
    },
    {
        "class": "biennial",
        "label": "biennial / Made in L.A. / Whitney",
        "weight": 0.8,
        "patterns": [
            r"\bbiennial\b",
            r"\bbiennale\b",
            r"\bmade in l\.?a\.?\b",
            r"\bdocumenta\b",
            r"\btriennial\b",
        ],
    },
    {
        "class": "major_award",
        "label": "MacArthur / Turner / Hugo Boss",
        "weight": 0.8,
        "patterns": [
            r"\bmacarthur\b",
            r"\bturner prize\b",
            r"\bhugo boss prize\b",
            r"\bguggenheim fellow(?:ship)?\b",
            r"\bgolden lion\b",
            r"\bnational medal of arts\b",
            r"\bpulitzer\b",
        ],
    },
    {
        "class": "acquired",
        "label": "acquired by a museum / collection",
        "weight": 0.6,
        "patterns": [
            r"\bacquired by\b",
            r"\bacquisitions? (?:by|of|for)\b",
            r"\bentered the (?:permanent )?collection\b",
            r"\bpermanent collections? of\b",
        ],
    },
    {
        "class": "commissioned",
        "label": "commissioned / site-specific",
        "weight": 0.5,
        "patterns": [
            r"\bcommission(?:ed|s)?\b",
            r"\bsite[- ]specific\b",
        ],
    },
    {
        "class": "new_work",
        "label": "new work / debut",
        "weight": 0.3,
        "patterns": [
            r"\bnew (?:work|works|paintings|sculptures|photographs|body of work|series|film|video)\b",
            r"\bdebut\b",
            r"\bpremiere\b",
            r"\bnever[- ]before[- ]seen\b",
        ],
    },
]

_COMPILED = [
    (k["class"], k["label"], k["weight"], [re.compile(p, re.I) for p in k["patterns"]])
    for k in KEYWORDS
]

CLASS_WEIGHTS = {k["class"]: k["weight"] for k in KEYWORDS}


def scan_keywords(text: str, where: str = "description") -> list[dict]:
    """Return one hit per class found in ``text``:
    ``{class, label, weight, term, where}`` where ``term`` is the first
    matched substring. Distinct-class semantics are enforced here so callers
    can simply sum ``weight`` (and cap at 1)."""
    hits = []
    if not text:
        return hits
    for cls, label, weight, regexes in _COMPILED:
        for rx in regexes:
            m = rx.search(text)
            if m:
                hits.append({"class": cls, "label": label, "weight": weight,
                             "term": m.group(0), "where": where})
                break
    return hits


def merge_hits(*hit_lists: list[dict]) -> list[dict]:
    """Union of hit lists keeping the first hit per class (description wins
    over snippets when both hit)."""
    seen: dict[str, dict] = {}
    for hits in hit_lists:
        for h in hits:
            seen.setdefault(h["class"], h)
    return list(seen.values())


def keyword_feature(hits: list[dict]) -> float:
    """min(1, sum of class weights over distinct classes)."""
    classes = {h["class"]: h["weight"] for h in hits}
    return min(1.0, sum(classes.values()))
