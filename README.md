# Weekly Digest — Full Reference

A weekly summarizer for Substack, YouTube, Telegram (automated) and LinkedIn/PDFs (manual batch).
Outputs Notion-importable `.md` files.

---

## First-time setup checklist

Work through this once. Tick as you go.

- [ ] Extract project, `cd weekly_digest`
- [ ] `git init`
- [ ] Create `.gitignore` (copy from below)
- [ ] Create `.env` (copy from below, fill in your keys)
- [ ] `python -m venv .venv`
- [ ] `source .venv/bin/activate` (Mac/Linux) or `.venv\Scripts\activate` (Windows)
- [ ] `pip install -r requirements.txt`
- [ ] Edit `config/sources.yaml` — add at least one Substack feed
- [ ] `python weekly_digest.py --auto --dry-run` — verify feeds work, no API calls made
- [ ] `python weekly_digest.py --auto` — first real run
- [ ] Check `summaries/` folder for output files
- [ ] Import one file to Notion to verify formatting

---

## .gitignore

Create this file in the project root before your first `git commit`:

```
.env
.venv/
state/
summaries/
logs/
inbox/
__pycache__/
*.session*
*.pyc
```

`state/` and `summaries/` are runtime output, not source code. Keep summaries
backed up separately (export to Notion, or a folder outside the repo).

---

## Environment variables — use a .env file

Create `.env` in the project root (never committed):

```bash
# .env

ANTHROPIC_API_KEY=sk-ant-...

# Only needed if using YouTube
YOUTUBE_API_KEY=AIza...

# Only needed if using Telegram
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abcdef1234567890abcdef1234567890
```

Add to the top of `weekly_digest.py` (two lines — also in CLAUDE.md as a reminder):

```python
from dotenv import load_dotenv
load_dotenv()
```

Add to `requirements.txt`:
```
python-dotenv
```

The script picks up keys from `.env` automatically. No shell exports needed.

---

## Getting API keys

### Anthropic
https://console.anthropic.com → API Keys → Create key

### YouTube API key (free, no billing needed for read-only)
- [ ] https://console.cloud.google.com → create or select a project
- [ ] APIs & Services → Enable APIs → search "YouTube Data API v3" → Enable
- [ ] APIs & Services → Credentials → Create Credentials → API Key → copy to `.env`

### Telegram API credentials (free)
- [ ] https://my.telegram.org → log in with your phone number
- [ ] "API development tools" → fill in app name (anything) → copy `api_id` and `api_hash` to `.env`
- [ ] **First run only:** script prompts for phone number + SMS code in terminal
- [ ] Creates `state/telegram_session.session` — never prompted again after that

---

## Configure sources

Edit `config/sources.yaml`.

### Substack RSS
Every Substack has a public feed at `https://authorname.substack.com/feed`.
No settings to change on their end — it just exists.

```yaml
substack:
  - name: "Debora Marks Lab"
    feed: "https://deboramarkslab.substack.com/feed"
  - name: "Evgeny Kiner"
    feed: "https://evgenykiner.substack.com/feed"
```

### YouTube channel IDs
The channel ID (`UCxxx...`) differs from the handle (`@name`). To find it:
open the channel → View Page Source → Ctrl+F `channelId`.
Or use https://commentpicker.com/youtube-channel-id.php

```yaml
youtube:
  - name: "Channel Name"
    channel_id: "UCxxxxxxxxxxxxxxxxxxxxxxxx"
```

### Telegram
Use the username without `@`. Must be a public channel or one you're already a member of.

```yaml
telegram:
  - name: "Channel Name"
    username: "channel_username"
```

---

## Weekly usage

```bash
# Activate venv first (every terminal session)
source .venv/bin/activate

# Normal weekly auto run
python weekly_digest.py --auto

# Check what's new without spending API credits
python weekly_digest.py --auto --dry-run

# Manual: single LinkedIn post as quoted text
python weekly_digest.py --manual "paste the full post text here"

# Manual: single file or URL
python weekly_digest.py --manual /path/to/paper.pdf
python weekly_digest.py --manual https://some-article.com/post

# Batch: process a whole folder (LinkedIn weekly PDFs/txts)
python weekly_digest.py --batch inbox/2025-W16/

# Auto + batch together
python weekly_digest.py --auto --batch inbox/2025-W16/

# Wipe seen-items memory (re-process everything)
python weekly_digest.py --auto --reset-seen
```

---

## Batch folder (`--batch`)

Drop all LinkedIn PDFs/txts/mds for the week into a folder, run `--batch` once.

```bash
# Process all .pdf/.txt/.md files in a folder
python weekly_digest.py --batch inbox/2025-W16/

# Combine with auto sources in one run
python weekly_digest.py --auto --batch inbox/2025-W16/

# Preview without calling the API
python weekly_digest.py --batch inbox/2025-W16/ --dry-run
```

Suggested folder structure:
```
weekly_digest/
└── inbox/
    ├── 2025-W16/
    │   ├── linkedin_john_smith.pdf
    │   ├── linkedin_jane_doe.txt
    │   └── interesting_preprint.pdf
    └── 2025-W17/
```

Files already processed are tracked in `state/seen.json` by file path — re-running the same folder won't reprocess them unless you use `--reset-seen`.

---

## Catch-up runs (`--since DATE`)

Override `lookback_days` from `sources.yaml` with an explicit start date. Useful after a holiday or gap.

```bash
# Fetch everything published since April 1st
python weekly_digest.py --auto --since 2026-04-01

# Full catch-up from the start of the year
python weekly_digest.py --auto --since 2026-01-01

# Preview what would be fetched
python weekly_digest.py --auto --since 2026-04-01 --dry-run
```

Date format: `YYYY-MM-DD`.

---

## Cron job — setup and workarounds

### Why cron needs special handling
Cron runs in a bare shell. It does not load your `.zshrc`, does not activate your
venv, and does not see `.env` unless you explicitly handle all three.

### Step 1 — create a wrapper script

Create `run_digest.sh` in the project root:

```bash
#!/bin/bash
# run_digest.sh — wrapper called by cron

PROJECT="/absolute/path/to/weekly_digest"   # ← change this

cd "$PROJECT"
source "$PROJECT/.venv/bin/activate"
mkdir -p "$PROJECT/logs"
python "$PROJECT/weekly_digest.py" --auto >> "$PROJECT/logs/digest.log" 2>&1
```

Make it executable:
```bash
chmod +x run_digest.sh
```

**Test it manually before setting up cron** — this is exactly what cron will run:
```bash
./run_digest.sh
cat logs/digest.log
```

### Step 2 — add to crontab

```bash
crontab -e
```

Pick a schedule:
```
# Every Monday at 8am
0 8 * * 1 /absolute/path/to/weekly_digest/run_digest.sh

# Every Sunday at 7pm
0 19 * * 0 /absolute/path/to/weekly_digest/run_digest.sh

# Every day at 9am
0 9 * * * /absolute/path/to/weekly_digest/run_digest.sh
```

Cron format: `minute hour day-of-month month weekday`
Weekday: 0=Sunday 1=Monday 2=Tuesday 3=Wednesday 4=Thursday 5=Friday 6=Saturday

### Check if cron ran
```bash
cat logs/digest.log           # full log
tail -50 logs/digest.log      # last 50 lines
ls -lt summaries/ | head -10  # newest output files
```

### Cron not running? Work through this list
- [ ] Is the path in `run_digest.sh` the correct absolute path? (`pwd` in the project folder)
- [ ] Is the script executable? `chmod +x run_digest.sh`
- [ ] Does `./run_digest.sh` work when you run it manually?
- [ ] macOS: cron may need Full Disk Access — System Settings → Privacy & Security → Full Disk Access → add `/usr/sbin/cron`
- [ ] Try replacing `python` in the script with the full venv path: `which python` inside the active venv

### macOS launchd — more reliable than cron on Mac

If cron is flaky (common on macOS), use launchd instead.

Create `~/Library/LaunchAgents/com.weeklydigest.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.weeklydigest</string>
    <key>ProgramArguments</key>
    <array>
        <string>/absolute/path/to/weekly_digest/run_digest.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key><integer>1</integer>
        <key>Hour</key><integer>8</integer>
        <key>Minute</key><integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/absolute/path/to/weekly_digest/logs/digest.log</string>
    <key>StandardErrorPath</key>
    <string>/absolute/path/to/weekly_digest/logs/digest.log</string>
</dict>
</plist>
```

Load/unload:
```bash
launchctl load ~/Library/LaunchAgents/com.weeklydigest.plist
launchctl list | grep weeklydigest   # verify loaded
launchctl unload ~/Library/LaunchAgents/com.weeklydigest.plist  # to disable
```

### VS Code task — simplest option while you're learning cron

Create `.vscode/tasks.json` in the project folder:

```json
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "Run Weekly Digest",
      "type": "shell",
      "command": "source .venv/bin/activate && python weekly_digest.py --auto",
      "options": { "cwd": "${workspaceFolder}" },
      "presentation": { "reveal": "always", "panel": "new" }
    },
    {
      "label": "Dry Run (check sources, no API)",
      "type": "shell",
      "command": "source .venv/bin/activate && python weekly_digest.py --auto --dry-run",
      "options": { "cwd": "${workspaceFolder}" },
      "presentation": { "reveal": "always", "panel": "new" }
    }
  ]
}
```

Run via: **Terminal → Run Task → Run Weekly Digest**

This is the right approach while you're still setting up cron. Run manually from VS Code,
switch to cron once you're confident the script works reliably.

---

## Importing to Notion

**Settings & Members → Import → Markdown & CSV** → select files from `summaries/`

Each `.md` file becomes a Notion page with correct heading hierarchy.
You can select multiple files at once.

---

## Customising summaries

Edit `config/prompt_template.txt` — no code changes needed.
Placeholders that must stay: `{title}` `{source_name}` `{content_type}` `{date}` `{link}` `{content}`
Everything else (sections, tone, framing, length) is free to change.

---

## Project structure

```
weekly_digest/
├── weekly_digest.py           # main script
├── requirements.txt
├── run_digest.sh              # cron wrapper — create manually
├── .env                       # API keys — never committed
├── .gitignore
├── config/
│   ├── sources.yaml           # your sources — edit this
│   └── prompt_template.txt    # summary format — edit this
├── inbox/                     # drop LinkedIn PDFs/txts here
│   └── 2025-W16/
├── state/
│   ├── seen.json              # auto-created
│   └── telegram_session.*     # auto-created on first Telegram run
├── summaries/                 # output .md files
├── logs/                      # cron run logs
└── .vscode/
    └── tasks.json             # optional
```
