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

## Poll interval and the CDN cache

The endpoint sits behind CloudFront with a **15 second TTL**. You can watch the
cache generation roll over in the `age` header:

```
Miss → Hit(age 5) → Hit(age 10) → Miss → Hit(age 5) → ...
```

Two things follow:

- **Polling faster than 15s is pointless.** You get byte-identical cached data
  back, so you gain no freshness and only raise your odds of being flagged.
  15s is the useful floor, and it's the default.
- **That floor applies to everyone**, including the bots you're racing. Nobody
  sees an opening sooner than ~15s after it appears. Detection is not where
  this is won or lost.

The watcher reads `age` and sleeps until just past the next refresh, so each
poll lands on data ~1s old rather than a random point in the window (~7.5s
stale on average). Same request count, better freshness.

There are no rate-limit headers on this endpoint, so a block would arrive
silently as a 403. The watcher backs off exponentially (capped at 10 min) on
403/429 and honors `Retry-After` when present.

**Keep your date range inside one calendar month where you can.** The watcher
issues one request per month spanned, so a range crossing a month boundary
doubles every poll for no benefit if half those dates are in the past.

### Past dates produce phantom openings

The payload keeps serving records for dates that have already happened, with
whatever quota went unused still showing as `remaining`. As of 2026-08-03, all
five open records site-wide were July dates — unbookable, but they look
identical to a real opening. A long-running watcher whose start date stays put
will drift into alerting on these.

The watcher now clamps the start date forward to today, so this can't happen.

### Group size gates availability

The booking page won't render availability until you pick a group size — it
filters inventory by party size. The API's `remaining` is the raw quota and
knows nothing about your party, so **an alert does not mean you can book it**.
If 1 spot opens and you need 4, that permit is not yours.

Pass `--group-size N` so the watcher only fires on openings that can actually
fit your party. Without it you'll be woken up by permits you can't buy — and
possibly carried all the way to a payment failure at the commit step.

The page legend also distinguishes states the API flattens away:
`NR` (not yet released), `L` (lottery-only), and `In-Station` (walk-up, issued
in person). Dates missing from the API payload are usually `NR`, not an error.

## Race-day checklist

Detection is capped at 15s for everyone; **checkout speed is the only part you
control.** From the network trace of a real attempt, a cold page load was 139
requests / 4.3 MB / 1.4 min to finish. Nearly all of the losable time is here,
not in the polling.

Do these *before* you're racing:

1. **Stay logged in**, with the detailed-availability page already open in a
   tab. Refreshing a warm tab beats cold-loading the SPA by close to a minute.
2. **Save a card to your profile and pay with it directly — not Apple Pay.**
   Apple Pay sends a device token plus whatever fields the payment sheet was
   configured to return, not your card, so "my card works" doesn't imply the
   wallet path works. It's a separate integration, it's Safari-first (shakier
   in Chrome), and its payment sheet adds time to a checkout you're racing.
   A saved card removes the whole thing from the failure path.
3. **Pre-add every group member** under your account's group/companion list.
   Typing names and addresses at checkout is the single slowest step.
4. **Decide trip details in advance** — entry date, exit date, group size,
   division (Day Use vs Overnight). Hesitating on a dropdown costs the spot.

Note: a `500` from the payment endpoint usually means the inventory went while
your order was in flight — someone else's checkout committed first. It is not
necessarily a bug in your session, and retrying the same cart won't recover it.

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

DATE                 start date (YYYY-MM-DD), also the single date if --end is unset
--end DATE           end date (inclusive) — watch every day in [DATE, --end]
--permit ID          permit ID (default 445860, Mt. Whitney)
--interval SECONDS   poll interval (default 15 = CDN cache TTL; lower is wasted)
--division TEXT      only alert for divisions matching this substring
--group-size N       your party size; only alert when >= N spots are open
--no-open             don't auto-open the booking page on alert
--stop-on-found       exit after first alert
--debug               dump raw JSON to /tmp on each poll
--raw                 print full JSON once and exit (no polling)
--imessage LIST       comma-separated phone numbers or emails to iMessage on alert
                      (e.g. --imessage "+15551234567,friend@icloud.com")
```

## Run in background

```bash
nohup ./run_watch.sh 2026-10-25 > watch.log 2>&1 &
disown
tail -f watch.log        # check progress
pgrep -f whitney_watch    # find the PID
kill <PID>                 # stop it
```

## Notes

- Only works for dates inside the **quota season** (roughly May 1 – Nov 1).
  Outside that window there's no per-day count to poll — permits are
  unlimited and bookable up to 2 weeks ahead.
- If alerts stop matching what the site shows, run with `--raw` and compare
  against the API response to re-check the parsing.
