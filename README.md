# Weekly Digest — Full Reference

A weekly summarizer for Substack, YouTube, Telegram (automated) and LinkedIn/PDFs (manual batch).
Outputs Notion-importable `.md` files.


## Configure sources

Edit `config/sources.yaml`.

### Substack RSS
Every Substack has a public feed at `https://authorname.substack.com/feed`.
No settings to change on their end — it just exists.

```yaml
substack:
  - name: "Name of blog"
    feed: "https://nameofblog.substack.com/feed"
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

## Usage

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
