"""
Collect speech fragments ("spreekt" moments) of Dutch MPs from Debat Direct
(https://debatdirect.tweedekamer.nl), the video platform of the Tweede Kamer.

For every politician in an input CSV the script:
  1. finds plenary debates in a date range (relevance order, as on the website),
  2. reads each debate's speaker events and keeps the politician's own
     "speaker" turns (not interruptions),
  3. builds clips: turn start - 20 s to turn end + 20 s, skipping turns shorter
     than 30 s and cutting turns longer than 22 min to their first 12 min,
  4. asks the Debat Direct fragment service to render each clip as MP4 and
     downloads it, until 20-22 minutes are collected (or nothing is left).

Use of the footage is subject to the "Licentievoorwaarden Audiovisueel
Materiaal Tweede Kamer". Running this script means you accept those terms,
just as ticking the box in the website's download dialog does.

Usage (see README.md for more):
    python scrape_debatdirect.py --names politicians.csv --out "C:/path/to/folder"
    python scrape_debatdirect.py --names politicians.csv --out out --only "Rob Jetten" --dry-run
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import threading
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib.parse import quote

import pandas as pd
import requests
import socketio

# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #

API = "https://cdn.debatdirect.tweedekamer.nl"
STREAM_SHARE_URL = "https://stream-share.debatdirect.tweedekamer.nl"
APP_VERSION = "11.23.8"  # sent by the website with each search request

DEFAULT_START = "2017-03-15"
DEFAULT_END = "2025-10-28"
LOCATION = "Plenaire zaal"
LOCATION_ID = "plenaire-zaal"

BUFFER_S = 20            # seconds added before and after each speaking turn
MIN_TURN_S = 30          # turns shorter than this are skipped
LONG_TURN_S = 22 * 60    # turns longer than this are ...
LONG_TURN_CAP_S = 12 * 60  # ... cut to this length
TARGET_MIN_S = 20 * 60   # stop once a politician has this much footage
TARGET_MAX_S = 22 * 60   # never collect more than this

# Preferred download resolutions, best first. 720p is the highest the public
# service offers; lower ones are only used if 720p fails for a debate.
QUALITIES = ["1280x720", "960x540", "640x360"]

REQUEST_PAUSE_S = 0.5    # pause between API calls, to be gentle on the site
FRAGMENT_TIMEOUT_S = 15 * 60

# Namespace the website uses to give each fragment request a stable id.
FRAGMENT_NAMESPACE = uuid.UUID("8cabbd15-aa4a-40df-be24-0b07ac0db0fe")

METADATA_COLUMNS = [
    "politician", "clip_id", "status", "file", "debate_id", "debate_name",
    "debate_date", "role", "turn_start", "turn_end", "clip_start", "clip_end",
    "clip_seconds", "quality", "note",
]

session = requests.Session()
session.headers["User-Agent"] = "debatdirect-research-scraper (academic research)"


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

def parse_time(value: str) -> datetime:
    """Parse '2019-07-03T21:06:33+0200' (offset without colon) or ISO 8601."""
    value = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value)
    return datetime.fromisoformat(value)


def fmt_time(dt: datetime) -> str:
    """Format like the website does: 2019-07-03T21:06:13+02:00."""
    return dt.isoformat(timespec="seconds")


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")


def encode_uri_component(text: str) -> str:
    return quote(text, safe="-_.!~*'()")


def get_json(path: str, params: Optional[dict] = None) -> dict:
    url = path if path.startswith("http") else API + path
    for attempt in range(4):
        try:
            response = session.get(url, params=params, timeout=60)
            response.raise_for_status()
            time.sleep(REQUEST_PAUSE_S)
            return response.json()
        except requests.RequestException as error:
            if attempt == 3:
                raise
            wait = 5 * (attempt + 1)
            print(f"    request failed ({error}); retrying in {wait}s")
            time.sleep(wait)
    raise RuntimeError("unreachable")


# --------------------------------------------------------------------------- #
# Finding debates and speaking turns
# --------------------------------------------------------------------------- #

def search_debates(start: str, end: str, query: Optional[str] = None,
                   politician: Optional[str] = None) -> List[dict]:
    """All plenary debates matching a free-text query or a politician filter."""
    params = {
        "van": start, "tot": end, "locatie": LOCATION, "sortering": "relevant",
        "appVersion": APP_VERSION, "platform": "web", "totalFormat": "new",
    }
    if query:
        params["q"] = query
    if politician:
        params["politici"] = politician
    results, offset = [], 0
    while True:
        params["vanaf"] = offset
        hits = get_json("/search", params)["hits"]["hits"]
        if not hits:
            break
        results.extend(hit["_source"] for hit in hits)
        offset += len(hits)
    return [d for d in results if d.get("locationId") == LOCATION_ID]


def debates_for(full_name: str, start: str, end: str) -> List[dict]:
    """Debates in the order we work through them.

    First the free-text search for the name (what you get by typing the name
    on the website, ranked by relevance), then all other plenary debates the
    site links to this politician, which the free-text search often misses.
    """
    ordered: Dict[str, dict] = {}
    for debate in search_debates(start, end, query=full_name):
        ordered.setdefault(debate["id"], debate)
    for debate in search_debates(start, end, politician=full_name):
        ordered.setdefault(debate["id"], debate)
    return list(ordered.values())


_actor_cache: Dict[str, List[dict]] = {}


def actors_on(date: str) -> List[dict]:
    if date not in _actor_cache:
        _actor_cache[date] = get_json(f"/api/actors/{date}").get("politicians", [])
    return _actor_cache[date]


def find_actor(full_name: str, date: str) -> Optional[dict]:
    target = full_name.casefold().strip()
    for actor in actors_on(date):
        if actor.get("name", "").casefold().strip() == target:
            return actor
    return None


@dataclass
class Clip:
    politician: str
    debate: dict
    role: str
    turn_start: datetime
    turn_end: datetime
    clip_start: datetime
    clip_end: datetime
    note: str = ""

    @property
    def seconds(self) -> int:
        return int((self.clip_end - self.clip_start).total_seconds())

    @property
    def clip_id(self) -> str:
        return f"{self.debate['id']}_{self.turn_start:%Y%m%dT%H%M%S}"


def clips_in_debate(full_name: str, debate_summary: dict) -> List[Clip]:
    debate = get_json(f"/api/debates/{debate_summary['id']}")
    if not debate.get("endedAt") or not debate.get("video"):
        return []
    actor = find_actor(full_name, debate["debateDate"])
    if actor is None:
        return []
    role = actor.get("title", "")
    debate_start = parse_time(debate["startedAt"])
    debate_end = parse_time(debate["endedAt"])

    events = sorted(debate.get("events", []), key=lambda e: e["eventStart"])
    clips: List[Clip] = []
    for i, event in enumerate(events):
        if event["eventType"] != "speaker" or event["objectId"] != actor["id"]:
            continue
        turn_start = parse_time(event["eventStart"])
        # A turn lasts until the next event (chair, interruption, next speaker).
        turn_end = parse_time(events[i + 1]["eventStart"]) if i + 1 < len(events) else debate_end
        length = (turn_end - turn_start).total_seconds()
        if length < MIN_TURN_S:
            continue
        note = ""
        if length > LONG_TURN_S:
            turn_end = turn_start + timedelta(seconds=LONG_TURN_CAP_S)
            note = f"turn of {length / 60:.1f} min cut to first {LONG_TURN_CAP_S // 60} min"
        clip_start = max(debate_start, turn_start - timedelta(seconds=BUFFER_S))
        clip_end = min(debate_end, turn_end + timedelta(seconds=BUFFER_S))
        clips.append(Clip(full_name, debate, role, turn_start, turn_end,
                          clip_start, clip_end, note))
    return merge_overlapping(clips)


def merge_overlapping(clips: List[Clip]) -> List[Clip]:
    """Merge clips whose 20-second buffers overlap, so no footage is
    downloaded or counted twice. This happens when a speech is broken up by a
    short interruption (gap under 40 seconds)."""
    merged: List[Clip] = []
    for clip in clips:
        if merged and clip.clip_start <= merged[-1].clip_end:
            last = merged[-1]
            last.clip_end = max(last.clip_end, clip.clip_end)
            last.turn_end = max(last.turn_end, clip.turn_end)
            last.note = "; ".join(filter(None, [last.note, "merged with following turn", clip.note]))
        else:
            merged.append(clip)
    return merged


# --------------------------------------------------------------------------- #
# Downloading
# --------------------------------------------------------------------------- #

def fragment_name(debate_name: str, start: str, end: str, quality: str, kind: str) -> str:
    """Name the website gives a fragment (it also becomes the server file name)."""
    suffix = f"-{start}-{end}-{quality}-{kind}"
    base = (debate_name or "debatdirect-video")[: 254 - len(suffix)]
    name = f"{base}{suffix}"
    name = re.sub(r"\s+", "-", name.strip())
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9-]", "", name)


def request_fragment(clip: Clip, quality: str) -> str:
    """Ask the fragment service to render the clip; returns the MP4 URL."""
    debate = clip.debate
    vod_url = debate["video"]["catchup"]["vodUrl"]
    separator = "&" if "?" in vod_url else "?"
    location = (f"{vod_url}{separator}start={encode_uri_component(debate['startedAt'])}"
                f"&end={encode_uri_component(debate['endedAt'])}")
    start, end = fmt_time(clip.clip_start), fmt_time(clip.clip_end)
    raw_name = f"{debate['name']}-{start}-{end}-{quality}-mp4"
    payload = {
        "location": location,
        "name": fragment_name(debate["name"], start, end, quality, "mp4"),
        "quality": quality,
        "type": "mp4",
        "startsAt": start,
        "endsAt": end,
    }
    payload["externalId"] = str(uuid.uuid5(
        FRAGMENT_NAMESPACE, start + end + location + raw_name + quality + "mp4"))

    done = threading.Event()
    result: dict = {}
    client = socketio.Client(reconnection=False)

    @client.on("fragment_ready")
    def on_ready(data):
        result.update(data or {})
        client.emit("fragment_received", data)
        done.set()

    @client.on("fragment_error")
    def on_error(data):
        result["error"] = data
        done.set()

    client.connect(STREAM_SHARE_URL, transports=["websocket"], wait_timeout=30)
    try:
        client.emit("fragment_request", payload)
        if not done.wait(FRAGMENT_TIMEOUT_S):
            raise TimeoutError("fragment service did not answer in time")
    finally:
        client.disconnect()
    if "error" in result or not result.get("url"):
        raise RuntimeError(f"fragment service error: {result.get('error', result)}")
    return result["url"]


def download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part")
    with session.get(url, stream=True, timeout=120) as response:
        response.raise_for_status()
        with open(partial, "wb") as handle:
            for chunk in response.iter_content(chunk_size=1 << 20):
                handle.write(chunk)
    partial.replace(target)


# --------------------------------------------------------------------------- #
# Bookkeeping (makes the script resumable)
# --------------------------------------------------------------------------- #

class Metadata:
    def __init__(self, path: Path):
        self.path = path
        self.rows: List[dict] = []
        if path.exists():
            with open(path, newline="", encoding="utf-8") as handle:
                self.rows = list(csv.DictReader(handle))

    def done_ids(self, politician: str) -> set:
        return {r["clip_id"] for r in self.rows
                if r["politician"] == politician and r["status"] == "ok"}

    def seconds(self, politician: str) -> int:
        return sum(int(r["clip_seconds"]) for r in self.rows
                   if r["politician"] == politician and r["status"] == "ok")

    def add(self, row: dict) -> None:
        self.rows.append(row)
        new_file = not self.path.exists()
        with open(self.path, "a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=METADATA_COLUMNS)
            if new_file:
                writer.writeheader()
            writer.writerow(row)


def clip_row(clip: Clip, status: str, file: str = "", quality: str = "", note: str = "") -> dict:
    return {
        "politician": clip.politician, "clip_id": clip.clip_id, "status": status,
        "file": file, "debate_id": clip.debate["id"], "debate_name": clip.debate["name"],
        "debate_date": clip.debate["debateDate"], "role": clip.role,
        "turn_start": fmt_time(clip.turn_start), "turn_end": fmt_time(clip.turn_end),
        "clip_start": fmt_time(clip.clip_start), "clip_end": fmt_time(clip.clip_end),
        "clip_seconds": clip.seconds, "quality": quality,
        "note": "; ".join(filter(None, [clip.note, note])),
    }


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #

def iter_clips(full_name: str, start: str, end: str) -> Iterable[Clip]:
    """Clips in search order, with debates where the politician spoke as MP
    first; debates where they spoke as minister or state secretary are only
    used afterwards, if the MP material did not reach the target."""
    debates = debates_for(full_name, start, end)
    print(f"  {len(debates)} plenary debates found")
    other_roles: List[dict] = []
    for debate in debates:
        actor = find_actor(full_name, debate["debateDate"])
        if actor is None:
            continue
        if actor.get("title", "") != "Tweede Kamerlid":
            other_roles.append(debate)
            continue
        yield from clips_in_debate(full_name, debate)
    for debate in other_roles:
        yield from clips_in_debate(full_name, debate)


def process_politician(full_name: str, args, metadata: Metadata) -> None:
    print(f"\n=== {full_name} ===")
    collected = metadata.seconds(full_name)
    done = metadata.done_ids(full_name)
    if collected >= TARGET_MIN_S:
        print(f"  already has {collected / 60:.1f} min, skipping")
        return

    folder = Path(args.out) / "videos" / slugify(full_name)
    for clip in iter_clips(full_name, args.start, args.end):
        if collected >= TARGET_MIN_S:
            break
        if clip.clip_id in done:
            continue
        room = TARGET_MAX_S - collected
        if clip.seconds > room:
            # Trim so the politician's total does not pass 22 minutes.
            clip.clip_end = clip.clip_start + timedelta(seconds=room)
            clip.note = "; ".join(filter(None, [clip.note, "trimmed to stay under 22 min"]))
        if clip.seconds < MIN_TURN_S:
            continue

        label = (f"{clip.debate['debateDate']} {clip.clip_start:%H:%M:%S}-"
                 f"{clip.clip_end:%H:%M:%S} ({clip.seconds}s) {clip.debate['name']}")
        if args.dry_run:
            print(f"  [plan] {label}")
            metadata.add(clip_row(clip, "planned"))
            collected += clip.seconds
            continue

        file = folder / f"{slugify(full_name)}_{clip.turn_start:%Y-%m-%d_%H%M%S}.mp4"
        print(f"  downloading {label}")
        error = ""
        for quality in QUALITIES:
            try:
                url = request_fragment(clip, quality)
                download(url, file)
                metadata.add(clip_row(clip, "ok", str(file), quality))
                collected += clip.seconds
                error = ""
                break
            except Exception as exc:  # try the next quality, then give up on this clip
                error = f"{quality}: {exc}"
                print(f"    failed at {quality}: {exc}")
        if error:
            metadata.add(clip_row(clip, "failed", note=error))

    status = "dry run total" if args.dry_run else "collected"
    print(f"  {status}: {collected / 60:.1f} min")


def read_names(path: str) -> List[str]:
    df = pd.read_csv(path, sep=None, engine="python", dtype=str)
    df.columns = [c.strip() for c in df.columns]
    if "full_name" not in df.columns:
        sys.exit(f"{path} needs a 'full_name' column; found {list(df.columns)}")
    return [n.strip() for n in df["full_name"].dropna() if n.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--names", required=True, help="CSV with a full_name column")
    parser.add_argument("--out", required=True, help="folder for videos and metadata")
    parser.add_argument("--start", default=DEFAULT_START, help="first debate date (YYYY-MM-DD)")
    parser.add_argument("--end", default=DEFAULT_END, help="last debate date (YYYY-MM-DD)")
    parser.add_argument("--only", action="append",
                        help="process only this politician (repeatable)")
    parser.add_argument("--limit", type=int, help="process only the first N politicians")
    parser.add_argument("--dry-run", action="store_true",
                        help="list the clips that would be downloaded, download nothing")
    args = parser.parse_args()

    names = read_names(args.names)
    if args.only:
        wanted = {n.casefold() for n in args.only}
        names = [n for n in names if n.casefold() in wanted]
    if args.limit:
        names = names[: args.limit]
    print(f"Total number of politicians: {len(names)}")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.dry_run:
        plan = out / "clips_plan_dryrun.csv"
        plan.unlink(missing_ok=True)  # a dry run always starts a fresh plan
        metadata = Metadata(plan)
    else:
        metadata = Metadata(out / "clips_metadata.csv")
    for name in names:
        try:
            process_politician(name, args, metadata)
        except KeyboardInterrupt:
            print("\nStopped. Run the same command again to continue where it left off.")
            return
        except Exception as exc:
            print(f"  error for {name}: {exc}; continuing with the next politician")


if __name__ == "__main__":
    main()
