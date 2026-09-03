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
