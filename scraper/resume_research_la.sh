#!/bin/sh
# Resume the LA list-gallery research run (skips venues that already have a report).
TS=$(date +%s); LOG=/Users/jreinjr/Projects/GalleryBrowser/content/spend/logs/research-list-la-$TS.log
launchctl remove com.gallerybrowser.research 2>/dev/null
launchctl submit -l com.gallerybrowser.research -o $LOG -e $LOG -- /Users/jreinjr/Projects/GalleryBrowser/scraper/.venv/bin/python /Users/jreinjr/Projects/GalleryBrowser/scraper/research_venue.py run --city los-angeles --venue-ids @/Users/jreinjr/Projects/GalleryBrowser/content/spend/logs/research-list-la-ids.txt --gapfill --workers 4 --budget 18
echo "resumed -> $LOG"
