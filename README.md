# Debat Direct speech scraper

Collects video fragments of Dutch MPs speaking in the plenary hall of the Tweede Kamer, from [Debat Direct](https://debatdirect.tweedekamer.nl). For each politician in a CSV list it gathers up to 20–22 minutes of footage of them giving speeches (moments labelled *spreekt*, not interruptions).

The tool talks to the same public services the Debat Direct website uses, so it collects exactly what you would get by searching and downloading fragments by hand.

## How clips are chosen

1. **Debates.** Plenary debates (Plenaire zaal) between 15 March 2017 and 28 October 2025 are searched for the politician's full name, in the website's relevance order. Debates the site links to the politician but that the free-text search misses are added after those. Debates in which the politician spoke as an MP come before debates where they spoke as minister or state secretary.
2. **Speaking turns.** Within each debate, only the politician's own *speaker* turns are used. A turn runs from its start until the next event (the chair, an interruption or another speaker).
3. **Rules per turn:**
   - turns shorter than 30 seconds are skipped;
   - turns longer than 22 minutes are cut to their first 12 minutes;
   - each clip starts 20 seconds before the turn and ends 20 seconds after it;
   - if two clips would overlap because of the buffers, they are merged into one.
4. **Quota.** Clips are collected until the politician has at least 20 minutes. The last clip is trimmed if needed so the total never passes 22 minutes. If there is less material, everything available is collected.
5. **Quality.** 720p MP4 (the highest the public service offers). If 720p fails for a debate, 540p and then 360p are tried.

## Installation (Windows)

1. Install Python 3.9 or newer from [python.org](https://www.python.org/downloads/) (tick "Add Python to PATH").
2. Open a terminal in this folder and run:

```
pip install -r requirements.txt
```

## Usage

The input CSV needs a `full_name` column (other columns are ignored), for example:

```
first_name,tussenvoegsel,last_name,full_name
Roy,van,Aalst,Roy van Aalst
Max,,Aardema,Max Aardema
```

**See what would be downloaded, without downloading anything:**

```
python scrape_debatdirect.py --names politicians.csv --out "C:\path\to\output" --only "Rob Jetten" --dry-run
```

This writes `clips_plan_dryrun.csv` in the output folder.

**Download:**

```
python scrape_debatdirect.py --names politicians.csv --out "C:\path\to\output"
```

Options:

| Option | Meaning |
|---|---|
| `--start`, `--end` | Date range (default `2017-03-15` to `2025-10-28`) |
| `--only "Name"` | Process only this politician (can be repeated) |
| `--limit N` | Process only the first N politicians |
| `--dry-run` | Plan the clips without downloading |

The run can be stopped at any time (Ctrl+C). Running the same command again continues where it stopped: finished clips are skipped, and politicians who already have 20 minutes are skipped.

## Output

```
output/
├── clips_metadata.csv
└── videos/
    └── Rob_Jetten/
        ├── Rob_Jetten_2019-07-03_210633.mp4
        └── ...
```

`clips_metadata.csv` has one row per clip: politician, debate ID, name and date, the politician's role that day, turn and clip start/end times (time of day, with time zone), clip length, quality, file path and notes (e.g. merged or trimmed). Clips that could not be downloaded are listed with status `failed`, so the run can be repeated later for those.

The metadata file also makes the dataset reproducible without sharing the videos: the debate IDs and exact times are enough to download the same fragments again.

## Licence and terms of use

The code is released under the MIT licence (see `LICENSE`).

The **footage** is not covered by that licence. Its use is governed by the [Licentievoorwaarden Audiovisueel Materiaal Tweede Kamer](https://www.tweedekamer.nl/contact-en-bezoek/persinformatie/audiovisuele-vergaderbeelden-afnemen). Running this tool counts as accepting those terms, in the same way as ticking the box in the website's download dialog. The Tweede Kamer does not hold portrait rights of the people shown, and the footage contains personal data, so check with your institution before sharing the videos.

This project is not affiliated with the Tweede Kamer. Please keep the request rate low; the script pauses between requests and downloads one fragment at a time.
