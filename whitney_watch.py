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


def fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


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


def find_date_entries(node, target_date: str, path=(), found=None):
    """
    Recursively walk the JSON looking for any dict keyed by a date string
    matching target_date (with or without a time/timezone suffix) whose
    value looks like an availability record (has 'remaining' or 'total').
    Returns a list of (path, record) tuples.
    """
    if found is None:
        found = []

    if isinstance(node, dict):
        for key, value in node.items():
            key_str = str(key)
            if key_str == target_date or key_str.startswith(target_date + "T"):
                if isinstance(value, dict) and (
                    "remaining" in value or "total" in value or "show_walkup" in value
                ):
                    found.append((path, value))
            find_date_entries(value, target_date, path + (key_str,), found)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            find_date_entries(item, target_date, path + (str(i),), found)

    return found


def division_id_from_path(path):
    """Best-effort guess at which path segment is the division id (a numeric string)."""
    for segment in path:
        if segment.isdigit():
            return segment
    return None


def check_once(permit_id: str, target_date: str, division_filter: str, division_names: dict, debug: bool):
    start_date, end_date = month_bounds(target_date)
    url = (
        f"{BASE}/api/permitinyo/{permit_id}/availabilityv2"
        f"?start_date={start_date}&end_date={end_date}&commercial_acct=false"
    )
    data = fetch_json(url)

    if debug:
        debug_path = "/tmp/whitney_watch_last_response.json"
        with open(debug_path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"[debug] wrote raw response to {debug_path}")

    entries = find_date_entries(data, target_date)

    results = []
    for path, record in entries:
        div_id = division_id_from_path(path)
        name = division_names.get(div_id, div_id or "unknown division")
        if division_filter and division_filter.lower() not in name.lower():
            continue
        remaining = record.get("remaining")
        total = record.get("total")
        results.append((name, remaining, total, record))

    return results


def notify(title: str, message: str):
    """macOS notification + sound. No-ops quietly on non-macOS."""
    try:
        script = f'display notification "{message}" with title "{title}" sound name "Glass"'
        subprocess.run(["osascript", "-e", script], check=False)
        subprocess.run(["say", message], check=False)
    except FileNotFoundError:
        pass  # not on macOS


def main():
    parser = argparse.ArgumentParser(description="Watch recreation.gov permit availability.")
    parser.add_argument("date", help="Target date, YYYY-MM-DD (e.g. 2026-11-05)")
    parser.add_argument("--permit", default="445860", help="Permit ID (default: 445860, Mt. Whitney)")
    parser.add_argument("--interval", type=int, default=300, help="Seconds between polls (default 300)")
    parser.add_argument("--division", default="", help="Only alert for divisions whose name contains this substring")
    parser.add_argument("--stop-on-found", action="store_true", help="Exit after the first successful alert")
    parser.add_argument("--debug", action="store_true", help="Dump raw API response to /tmp on each poll")
    parser.add_argument("--raw", action="store_true", help="Print the full raw JSON response to stdout once, then exit")
    args = parser.parse_args()

    if args.raw:
        start_date, end_date = month_bounds(args.date)
        url = (
            f"{BASE}/api/permitinyo/{args.permit}/availabilityv2"
            f"?start_date={start_date}&end_date={end_date}&commercial_acct=false"
        )
        print(f"GET {url}\n")
        data = fetch_json(url)
        print(json.dumps(data, indent=2))
        return

    try:
        datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        sys.exit("Date must be in YYYY-MM-DD format")

    print(f"Watching permit {args.permit} for {args.date} every {args.interval}s "
          f"(division filter: {args.division or 'none'})")
    reg_url = (
        f"{BASE}/permits/{args.permit}/registration/detailed-availability"
        f"?date={args.date}&type=overnight-permit"
    )
    print(f"Registration page: {reg_url}")

    division_names = get_division_names(args.permit)

    already_alerted = set()

    while True:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            results = check_once(args.permit, args.date, args.division, division_names, args.debug)
        except urllib.error.HTTPError as e:
            print(f"[{timestamp}] HTTP error {e.code}, backing off")
            time.sleep(min(args.interval, 60))
            continue
        except Exception as e:
            print(f"[{timestamp}] error: {e}")
            time.sleep(min(args.interval, 60))
            continue

        if not results:
            print(f"[{timestamp}] no availability records found for {args.date} "
                  f"(division filter may be too narrow, or check --debug output)")
        else:
            any_open = False
            for name, remaining, total, record in results:
                remaining_display = remaining if remaining is not None else "?"
                total_display = total if total is not None else "?"
                print(f"[{timestamp}] {name}: {remaining_display}/{total_display} remaining")
                if isinstance(remaining, (int, float)) and remaining > 0:
                    any_open = True
                    key = (name, args.date)
                    if key not in already_alerted:
                        already_alerted.add(key)
                        notify(
                            "Permit available!",
                            f"{name} has {remaining_display} spot(s) open for {args.date}",
                        )
                        print(f"  -> ALERT sent for {name}")

            if any_open and args.stop_on_found:
                print("Availability found, stopping (--stop-on-found).")
                subprocess.run(["open", reg_url], check=False)
                break

        time.sleep(args.interval)


if __name__ == "__main__":
    main()