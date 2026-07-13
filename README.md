# whitney-watch

Polls recreation.gov's permit availability API and alerts (macOS notification
+ spoken alert) when tickets open up for a target date.

## Source page

https://www.recreation.gov/permits/445860/registration/detailed-availability?date=2026-11-05&type=overnight-permit

## API endpoint used

```
GET https://www.recreation.gov/api/permitinyo/445860/availabilityv2?start_date=YYYY-MM-01&end_date=YYYY-MM-31&commercial_acct=false
```

This is the endpoint the page's own JavaScript calls to fill in the day-by-day
number grid — the HTML itself is just a template; the actual quota counts are
fetched from this API after the page loads. Hitting it directly is faster and
lighter than scraping/rendering the page, and it's the same data the site
shows you, straight from the source rather than a re-derived copy.

## Setup

```bash
chmod +x run_watch.sh whitney_watch.py
```

## Usage

```bash
./run_watch.sh 2026-10-25
```

Runs under `caffeinate` so your Mac won't sleep mid-poll. Ctrl+C to stop.

## Flags

```
whitney_watch.py DATE [flags]

--permit ID          permit ID (default 445860, Mt. Whitney)
--interval SECONDS   poll interval (default 180)
--division TEXT      only alert for divisions matching this substring
--stop-on-found       exit + open browser after first alert
--debug               dump raw JSON to /tmp on each poll
--raw                 print full JSON once and exit (no polling)
```

## Notes

- Only works for dates inside the **quota season** (roughly May 1 – Nov 1).
  Outside that window there's no per-day count to poll — permits are
  unlimited and bookable up to 2 weeks ahead.
- If alerts stop matching what the site shows, run with `--raw` and compare
  against the API response to re-check the parsing.
