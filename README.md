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

### 1. Install Python

Open Command Prompt (Start → type `cmd` → Enter) and check whether Python is already installed:

```
py --version
```

If this shows a version (3.9 or newer), go to step 2.

If not, download the **Windows installer (64-bit)** from [python.org/downloads/windows](https://www.python.org/downloads/windows/), run it, tick **"Add python.exe to PATH"**, and click **Install Now**. Then close Command Prompt, open a new one, and run `py --version` again.

> **Use `py`, not `python` or `pip`.** On many Windows laptops only the `py` launcher works. `python` may answer *"Python was not found"* and `pip` may be *"not recognized"*. Every command in this README therefore starts with `py`.

### 2. Install the required packages

```
py -m pip install -r requirements.txt
```

If you only have the script and not `requirements.txt`, install the packages by name:

```
py -m pip install pandas requests "python-socketio[client]"
```

### 3. Put the files in one folder

The folder needs at least:

```
Tweede_kamer_scrap/
├── scrape_debatdirect.py
└── Names_to_scrape.csv
```

The names CSV needs a `full_name` column (other columns are ignored). Commas, semicolons or tabs as separators all work:

```
first_name,tussenvoegsel,last_name,full_name
Roy,van,Aalst,Roy van Aalst
Max,,Aardema,Max Aardema
```

If the CSV is only on GitHub, download it into the folder:

```
curl -L -o Names_to_scrape.csv https://raw.githubusercontent.com/Antonis-Koutsoumpis/debatdirect-scraper/main/Names_to_scrape.csv
```

For a private repository, use **Download raw file** on the file's GitHub page instead and move the file into the folder.

## Usage

Every session starts by going to the project folder in Command Prompt:

```
cd "C:\Users\<you>\path\to\Tweede_kamer_scrap"
```

### About `--out .`

`--out` sets where the videos and metadata are saved, and it is required. A dot (`.`) means **the current folder**. **Put a space between `--out` and the dot:**

| Typed | Result |
|---|---|
| `--out .` | ✅ saves in the current folder |
| `--out.` | ❌ error: *the following arguments are required: --out* |
| `--out` (nothing after it) | ❌ error: *argument --out: expected one argument* |

To save somewhere else, give a full path in quotes, e.g. `--out "C:\Users\<you>\Tweede_kamer_videos"`. A folder outside OneDrive or Dropbox avoids syncing tens of gigabytes of video.

### 1. Dry run: plan the clips, download nothing

```
py scrape_debatdirect.py --names Names_to_scrape.csv --out . --only "Rob Jetten" --dry-run
```

This prints the planned clips and writes them to `clips_plan_dryrun.csv`. **No videos are downloaded.** Use it to check the selection before a long run.

### 2. Test download for one politician

```
py scrape_debatdirect.py --names Names_to_scrape.csv --out . --only "Rob Jetten"
```

The name after `--only` must match the `full_name` column exactly. Each clip can take from 30 seconds to a few minutes, because the Tweede Kamer server cuts the fragment before it is downloaded.

### 3. Full run

```
py scrape_debatdirect.py --names Names_to_scrape.csv --out .
```

### Options

| Option | Meaning |
|---|---|
| `--names FILE` | CSV with a `full_name` column (required) |
| `--out FOLDER` | Where videos and metadata go; `.` = current folder (required) |
| `--only "Name"` | Process only this politician (can be repeated) |
| `--limit N` | Process only the first N politicians |
| `--start`, `--end` | Date range (default `2017-03-15` to `2025-10-28`) |
| `--dry-run` | Plan the clips without downloading |

## Pausing and resuming

The run can be stopped at any time and **continues where it left off; it does not start over.**

**To pause:** press **Ctrl+C** and wait for *"Stopped. Run the same command again to continue where it left off."* You can then close the laptop.

**To resume:** open Command Prompt and run:

```
cd "C:\Users\<you>\path\to\Tweede_kamer_scrap"
py scrape_debatdirect.py --names Names_to_scrape.csv --out .
```

When it resumes:

- downloaded clips are skipped;
- politicians who already have 20 minutes or more are skipped;
- clips marked `failed` (for example after a network drop or closing the lid mid-download) are retried.

If the laptop goes to sleep during a download without Ctrl+C first, that clip may be retried at 540p instead of 720p. Check the `quality` column in `clips_metadata.csv`. For long runs, keep the laptop plugged in and set sleep to "Never" (Settings → System → Power → Screen and sleep).

## Output

```
Tweede_kamer_scrap/
├── clips_metadata.csv
├── clips_plan_dryrun.csv      (only after a dry run)
└── videos/
    └── Rob_Jetten/
        ├── Rob_Jetten_2019-07-03_210633.mp4
        └── ...
```

**File names** have the form `Politician_YYYY-MM-DD_HHMMSS.mp4`:

- `Rob_Jetten` is the politician;
- `2019-07-03` is the date of the debate;
- `210633` is the time of day the politician **starts speaking** (21:06:33). The clip itself starts 20 seconds earlier because of the buffer.

**`clips_metadata.csv`** has one row per clip: politician, status (`ok` or `failed`), file path, debate ID, name and date, the politician's role that day, the turn and clip start/end times (time of day, with time zone), clip length in seconds, video quality, and notes (e.g. *merged with following turn*, *cut to first 12 min*, *trimmed to stay under 22 min*). Open it in Excel to follow progress.

Expect roughly 300–400 MB of video per politician.

The metadata file also makes the dataset reproducible without sharing the videos: the debate IDs and exact times are enough to download the same fragments again.

## Troubleshooting

| Message | Fix |
|---|---|
| `Python was not found` | Use `py` instead of `python`, or install Python (see Installation). |
| `'pip' is not recognized` | Use `py -m pip install …` instead of `pip install …`. |
| `the following arguments are required: --out` / `expected one argument` | Add `--out .` with a space before the dot. |
| `Total number of politicians: 0` | The name after `--only` doesn't match the `full_name` column exactly, or the CSV has no `full_name` column. |
| `ModuleNotFoundError` | Packages are missing; run the install command in step 2 again. |
| `failed at 1280x720: …` | Usually a temporary server or network problem. Run the same command again later; failed clips are retried. |

## Licence and terms of use

The code is released under the MIT licence (see `LICENSE`).

The **footage** is not covered by that licence. Its use is governed by the [Licentievoorwaarden Audiovisueel Materiaal Tweede Kamer](https://www.tweedekamer.nl/contact-en-bezoek/persinformatie/audiovisuele-vergaderbeelden-afnemen). Running this tool counts as accepting those terms, in the same way as ticking the box in the website's download dialog. The Tweede Kamer does not hold portrait rights of the people shown, and the footage contains personal data, so check with your institution before sharing the videos.

This project is not affiliated with the Tweede Kamer. Please keep the request rate low; the script pauses between requests and downloads one fragment at a time.
