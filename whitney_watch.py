#!/usr/bin/env python3
"""
whitney_watch.py

Polls recreation.gov's permitinyo availabilityv2 API (the endpoint that
actually feeds the "detailed availability" table on
https://www.recreation.gov/permits/445860/registration/detailed-availability)
and alerts (macOS notification + sound) when tickets open up for a target date.

Usage:
    python3 whitney_watch.py 2026-11-05
    python3 whitney_watch.py 2026-11-05 --interval 300
    python3 whitney_watch.py 2026-11-05 --permit 445860 --division "Mt. Whitney Trail"

Meant to be run under `caffeinate` (see run_watch.sh) so your Mac doesn't
sleep mid-poll.
"""

import argparse
import calendar
import json
import random
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

BASE = "https://www.recreation.gov"
HEADERS = {
    # A real browser UA avoids some basic bot filtering
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://www.recreation.gov/",
}


# CloudFront serves this endpoint with a 15s TTL (observed: age cycles
# 0 -> 5 -> 10 -> miss). Polling faster than this returns identical cached
# bytes, so 15s is the useful floor no matter how fast we're willing to poll.
CACHE_TTL = 15


def fetch_json(url: str, headers_out: dict = None) -> dict:
    """GET and parse JSON. If headers_out is given, response headers land in it."""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        if headers_out is not None:
            headers_out.clear()
            headers_out.update({k.lower(): v for k, v in resp.headers.items()})
        return json.loads(resp.read().decode("utf-8"))


def seconds_until_fresh(headers: dict) -> float:
    """
    How long until the CDN's cached copy is replaced by a fresh one.

    Landing just after a refresh means acting on data that's ~1s old instead of
    a random point inside the 15s window (~7.5s stale on average). Falls back to
    a full TTL when the header is missing or unparseable.
    """
    raw = (headers or {}).get("age")
    try:
        age = int(raw)
    except (TypeError, ValueError):
        return CACHE_TTL
    remaining = CACHE_TTL - (age % CACHE_TTL)
    # +1s so we arrive just past the boundary rather than racing it.
    return max(1.0, remaining + 1.0)


def get_division_names(permit_id: str) -> dict:
    """Map division_id -> human readable name, e.g. '166' -> 'Mt. Whitney Trail (Overnight)'."""
    url = f"{BASE}/api/permitcontent/{permit_id}"
    try:
        data = fetch_json(url)
        divisions = data.get("payload", {}).get("divisions", {}) or {}
        return {str(k): v.get("name", str(k)) for k, v in divisions.items()}
    except Exception as exc:
        print(f"[warn] couldn't fetch division names: {exc}", file=sys.stderr)
        return {}


def month_bounds(date_str: str):
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    last_day = calendar.monthrange(dt.year, dt.month)[1]
    start = dt.replace(day=1).strftime("%Y-%m-%d")
    end = dt.replace(day=last_day).strftime("%Y-%m-%d")
    return start, end


def find_all_date_entries(node, path=(), found=None):
    """
    Recursively walk the JSON collecting every availability record. Records
    live under a YYYY-MM-DD-keyed dict but may be nested one or two levels
    below it (e.g. payload -> "2026-07-06" -> "166" -> "quota_usage_by_member_daily").
    Returns a list of (date_str, path, record) tuples.
    """
    if found is None:
        found = []

    if isinstance(node, dict):
        for key, value in node.items():
            key_str = str(key)
            date_prefix = key_str[:10]
            is_date = False
            if len(date_prefix) == 10 and date_prefix.count("-") == 2:
                try:
                    datetime.strptime(date_prefix, "%Y-%m-%d")
                    is_date = True
                except ValueError:
                    pass
            if is_date and isinstance(value, dict):
                stack = [(value, path + (key_str,))]
                while stack:
                    n, p = stack.pop()
                    if not isinstance(n, dict):
                        continue
                    if "remaining" in n or "total" in n:
                        found.append((date_prefix, p, n))
                        continue
                    for k, v in n.items():
                        stack.append((v, p + (str(k),)))
            else:
                find_all_date_entries(value, path + (key_str,), found)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            find_all_date_entries(item, path + (str(i),), found)

    return found


def division_id_from_path(path):
    """Best-effort guess at which path segment is the division id (a numeric string)."""
    for segment in path:
        if segment.isdigit():
            return segment
    return None


def months_in_range(start_date: str, end_date: str):
    """Yield (year, month) covering every month touched by [start, end]."""
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


def check_once(permit_id: str, start_date: str, end_date: str, division_filter: str,
               division_names: dict, debug: bool):
    """Fetch every month spanning [start_date, end_date] and return matching records.

    Returns (results, last_response_headers) — the headers drive cache-aligned
    polling in the main loop.
    """
    results = []
    resp_headers = {}
    for y, m in months_in_range(start_date, end_date):
        last_day = calendar.monthrange(y, m)[1]
        month_start = f"{y:04d}-{m:02d}-01"
        month_end = f"{y:04d}-{m:02d}-{last_day:02d}"
        url = (
            f"{BASE}/api/permitinyo/{permit_id}/availabilityv2"
            f"?start_date={month_start}&end_date={month_end}&commercial_acct=false"
        )
        data = fetch_json(url, headers_out=resp_headers)

        if debug:
            debug_path = f"/tmp/whitney_watch_last_{y:04d}-{m:02d}.json"
            with open(debug_path, "w") as f:
                json.dump(data, f, indent=2)
            print(f"[debug] wrote raw response to {debug_path}")

        for date_str, path, record in find_all_date_entries(data):
            if not (start_date <= date_str <= end_date):
                continue
            div_id = division_id_from_path(path)
            name = division_names.get(div_id, div_id or "unknown division")
            if division_filter and division_filter.lower() not in name.lower():
                continue
            remaining = record.get("remaining")
            total = record.get("total")
            results.append((date_str, name, remaining, total, record))

    return results, resp_headers


def _applescript_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def send_imessage(handle: str, message: str) -> None:
    """Send an iMessage via Messages.app. Logs and continues on failure."""
    safe_handle = _applescript_escape(handle)
    safe_message = _applescript_escape(message)
    script = (
        'tell application "Messages"\n'
        '  set targetService to 1st service whose service type = iMessage\n'
        f'  set targetBuddy to buddy "{safe_handle}" of targetService\n'
        f'  send "{safe_message}" to targetBuddy\n'
        'end tell'
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            check=False,
            timeout=15,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"  -> iMessage to {handle} failed: {result.stderr.strip()}", file=sys.stderr)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(f"  -> iMessage to {handle} skipped: {exc}", file=sys.stderr)


def notify(title: str, message: str, imessage_handles=(), url: str = ""):
    """macOS notification + sound + optional iMessage. No-ops quietly on non-macOS.

    The booking URL rides along in the banner and the iMessage (tappable on a
    phone) but is deliberately kept out of the spoken alert — having `say` read
    a full recreation.gov URL aloud takes longer than the booking window.
    """
    try:
        safe_title = _applescript_escape(title)
        safe_message = _applescript_escape(f"{message}\n{url}" if url else message)
        script = f'display notification "{safe_message}" with title "{safe_title}" sound name "Glass"'
        subprocess.run(["osascript", "-e", script], check=False)
        subprocess.run(["say", message], check=False)
    except FileNotFoundError:
        pass  # not on macOS

    body = f"{title} {message}"
    if url:
        body = f"{body}\n{url}"
    for handle in imessage_handles:
        send_imessage(handle, body)


def main():
    parser = argparse.ArgumentParser(description="Watch recreation.gov permit availability.")
    parser.add_argument("date", nargs="?", default="",
                        help="Start date, YYYY-MM-DD (or single date if --end is not set). "
                             "Defaults to today; a past date is clamped forward to today.")
    parser.add_argument("--end", default="", help="End date (inclusive) for range watching, YYYY-MM-DD. Defaults to start date.")
    parser.add_argument("--permit", default="445860", help="Permit ID (default: 445860, Mt. Whitney)")
    parser.add_argument("--interval", type=int, default=CACHE_TTL,
                        help=f"Seconds between polls (default {CACHE_TTL}, the CDN cache TTL — "
                             "polling faster returns identical cached data)")
    parser.add_argument("--division", default="", help="Only alert for divisions whose name contains this substring")
    parser.add_argument("--group-size", type=int, default=1, metavar="N",
                        help="Your party size. Only alert when at least N spots are open — "
                             "recreation.gov gates availability on group size, so an opening "
                             "smaller than your party can't be booked (default 1)")
    parser.add_argument("--stop-on-found", action="store_true", help="Exit after the first successful alert")
    parser.add_argument("--no-open", dest="open_page", action="store_false", default=True,
                        help="Don't auto-open the booking page on alert (it opens by default so "
                             "the SPA is loading while you get to the keyboard)")
    parser.add_argument("--debug", action="store_true", help="Dump raw API response to /tmp on each poll")
    parser.add_argument("--raw", action="store_true", help="Print the full raw JSON response to stdout once, then exit")
    parser.add_argument("--imessage", default="", help="Comma-separated phone numbers or emails to iMessage when a permit opens")
    args = parser.parse_args()

    imessage_handles = [h.strip() for h in args.imessage.split(",") if h.strip()]

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    start_arg = args.date or today.strftime("%Y-%m-%d")
    end_date = args.end or start_arg

    try:
        start_dt = datetime.strptime(start_arg, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        sys.exit("Dates must be in YYYY-MM-DD format")

    # Past dates can't be booked, and including them can push the range across a
    # month boundary — which costs an extra request per poll for nothing.
    if start_dt < today:
        print(f"[note] start {start_arg} is in the past, watching from today "
              f"({today.strftime('%Y-%m-%d')}) instead")
        start_dt = today
    args.date = start_dt.strftime("%Y-%m-%d")

    if end_dt < start_dt:
        sys.exit(f"--end ({end_date}) must be on or after the start date ({args.date})")

    if args.raw:
        month_start, month_end = month_bounds(args.date)
        url = (
            f"{BASE}/api/permitinyo/{args.permit}/availabilityv2"
            f"?start_date={month_start}&end_date={month_end}&commercial_acct=false"
        )
        print(f"GET {url}\n")
        data = fetch_json(url)
        print(json.dumps(data, indent=2))
        return

    range_label = args.date if end_date == args.date else f"{args.date}..{end_date}"
    print(f"Watching permit {args.permit} for {range_label} every {args.interval}s "
          f"(division filter: {args.division or 'none'}, group size: {args.group_size})")

    def reg_url_for(date_str: str) -> str:
        return (
            f"{BASE}/permits/{args.permit}/registration/detailed-availability"
            f"?date={date_str}&type=overnight-permit"
        )
    print(f"Registration page (start): {reg_url_for(args.date)}")

    division_names = get_division_names(args.permit)

    already_alerted = set()
    backoff = 0  # consecutive throttled/failed polls

    while True:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        resp_headers = {}
        try:
            results, resp_headers = check_once(args.permit, args.date, end_date, args.division,
                                               division_names, args.debug)
            backoff = 0
        except urllib.error.HTTPError as e:
            # 403/429 here mean the WAF has flagged us. There are no rate-limit
            # headers on this endpoint, so a block arrives silently and the only
            # safe response is to back off hard — retrying at the normal interval
            # just keeps the block alive.
            backoff += 1
            if e.code in (403, 429):
                retry_after = e.headers.get("Retry-After") if e.headers else None
                try:
                    delay = int(retry_after)
                except (TypeError, ValueError):
                    delay = min(600, args.interval * (2 ** backoff))
                print(f"[{timestamp}] HTTP {e.code} (throttled/blocked) — backing off {delay}s")
            else:
                delay = min(300, args.interval * (2 ** backoff))
                print(f"[{timestamp}] HTTP error {e.code}, retrying in {delay}s")
            time.sleep(delay)
            continue
        except Exception as e:
            backoff += 1
            delay = min(300, args.interval * (2 ** backoff))
            print(f"[{timestamp}] error: {e}, retrying in {delay}s")
            time.sleep(delay)
            continue

        if not results:
            print(f"[{timestamp}] no availability records found in {range_label} "
                  f"(division filter may be too narrow, or check --debug output)")
        else:
            newly_open_date = None
            for date_str, name, remaining, total, record in results:
                remaining_display = remaining if remaining is not None else "?"
                total_display = total if total is not None else "?"
                print(f"[{timestamp}] {date_str} {name}: {remaining_display}/{total_display} remaining")
                if isinstance(remaining, (int, float)) and remaining >= args.group_size:
                    key = (name, date_str)
                    if key not in already_alerted:
                        already_alerted.add(key)
                        notify(
                            "Permit available!",
                            f"{name} has {remaining_display} spot(s) open for {date_str}",
                            imessage_handles=imessage_handles,
                            url=reg_url_for(date_str),
                        )
                        print(f"  -> ALERT sent for {name} on {date_str}")
                        if newly_open_date is None:
                            newly_open_date = date_str

            if newly_open_date:
                # Start the page load immediately: it's ~4 MB of SPA and takes
                # far longer than the walk to the keyboard. Only the first
                # newly-opened date per cycle, so a multi-date opening doesn't
                # bury you in tabs.
                if args.open_page:
                    subprocess.run(["open", reg_url_for(newly_open_date)], check=False)
                    print(f"  -> opened booking page for {newly_open_date}")
                if args.stop_on_found:
                    print(f"Availability found for {newly_open_date}, stopping (--stop-on-found).")
                    break

        if args.interval <= CACHE_TTL:
            # Sync to the CDN refresh boundary: one request per cache generation,
            # each landing on data ~1s old instead of up to 15s stale.
            delay = seconds_until_fresh(resp_headers)
        else:
            # Explicitly slower than the cache: jitter so we don't fall into
            # lockstep with every other poller hitting the same boundary.
            delay = args.interval * random.uniform(0.9, 1.1)
        time.sleep(delay)


if __name__ == "__main__":
    main()