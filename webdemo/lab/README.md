# Design lab

Static mockups of UI options rendered over the real Los Angeles payload with
the app's own `styles.css`. Nothing here is wired into the app or the build.

```bash
scraper/.venv/bin/python webdemo/build.py           # once, for data.js + images
python3 -m http.server -d webdemo 8000               # then open http://localhost:8000/lab/
NODE_PATH=/opt/homebrew/lib/node_modules node webdemo/lab/shoot.js                # -> lab/shots/<id>.jpg
NODE_PATH=/opt/homebrew/lib/node_modules node webdemo/lab/shoot.js --page lists   # -> lab/shots/lists/<id>.jpg
```

Frame IDs (F1a, R2b, M4, L3c, A2e, ...) are the vocabulary for downselecting.
`lab-common.js` holds the helpers both pages share (phone frame, rows, cards,
MapLibre frames, section mounting).

## Page 1: `index.html` — filters, gallery rank, map modes

Shipped (2026-09-03): F1 (filter button top-right, nothing else on the page),
F3b (grouped filter sheet, with Show Rank / Gallery Rank / Venue Type pickers
and the search field), R2 (dot glyphs on rows, show detail and venue page) and
M1 (Shows | Galleries map switch). `lab.css` keeps copies of the list-bar rules
the app dropped so the older frames still render.

## Page 2: `lists.html` — Lists + Ask

Two concepts, 25 frames. **L** frames: the List tab becomes a library of lists
(today's filtered list is the special case "All shows in Los Angeles"); list
detail in owned / curated / received / edit states; add-to-list from the
bookmark; a list as context on Featured and Map; share; a curated guides page;
"Lists" vs "Guides" naming (L1a vs L1b). **A** frames: where Ask lives (tab,
capsule, filter search, floating button); four conversations over real data;
answers as unsaved lists with per-entry grounding; the answer on Map and Featured.

Shipped (2026-09-03, branch design/lists-ask): L1a with the header kept as the
city, Editor's Picks moved into Curated (no "Museums this month", no Recent
asks) and a See-all button to L7; L2; L3a with a caret per row instead of a
save button, Map · Edit (owned) or Map · Save (curated, answers), no Featured
button; L3d; L4; L5a/b/c (the List group replaced Saved only); L7. Ask became
**Discover** (A1a without the icon and heading, four prompts; A2 answers as
lists with Save as list / Show on Map, chips when the model offers them; A2d
routes drawn on the Map as in A3b). Not shipped: L1b, L6 (share), A1b–d, the
per-entry + of A3a. The real RAG layer is `webdemo/api/` (see `webdemo/README.md`).

Four frames are live (L1a, L3d, L4, A2e) and share one lab-only store in
`localStorage['lab.lists.v1']` (never the app's `savedShowIDs`); "Reset lab
data" in the page header clears it. `lab-ask.js` is a keyword retriever over the
payload (neighborhood aliases, concept synonyms, reception-date and hours
parsing, a greedy walking route). It exists only so the mockups carry honest
counts and snippets; it is not a design for the real RAG layer. Today is pinned
to 2026-09-03 (`?today=YYYY-MM-DD` overrides) so the weekend and route frames
are reproducible.
