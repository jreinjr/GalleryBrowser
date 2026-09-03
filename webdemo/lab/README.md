# Design lab

Static mockups of UI options (filters, gallery rank, map modes) rendered over
the real Los Angeles payload with the app's own `styles.css`. Nothing here is
wired into the app or the build.

```bash
scraper/.venv/bin/python webdemo/build.py           # once, for data.js + images
python3 -m http.server -d webdemo 8000               # then open http://localhost:8000/lab/
NODE_PATH=/opt/homebrew/lib/node_modules node webdemo/lab/shoot.js   # -> lab/shots/<id>.jpg
```

Frame IDs (F1a, R2b, M4, ...) are the vocabulary for downselecting.

Shipped (2026-09-03): F1 (filter button top-right, nothing else on the page),
F3b (grouped filter sheet, with Show Rank / Gallery Rank / Venue Type pickers
and the search field), R2 (dot glyphs on rows, show detail and venue page) and
M1 (Shows | Galleries map switch). `lab.css` keeps copies of the list-bar rules
the app dropped so the older frames still render.
