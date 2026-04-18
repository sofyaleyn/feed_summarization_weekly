# Weekly Digest — Setup Guide

## 1. Install dependencies

```bash
cd weekly_digest
pip install -r requirements.txt
```

---

## 2. Set environment variables

Add these to your `~/.zshrc` or `~/.bashrc` (or a `.env` file — see note below):

```bash
# Required always
export ANTHROPIC_API_KEY="sk-ant-..."

# Required for YouTube
export YOUTUBE_API_KEY="AIza..."

# Required for Telegram (see Telegram setup below)
export TELEGRAM_API_ID="12345678"
export TELEGRAM_API_HASH="abcdef1234567890abcdef1234567890"
```

Then `source ~/.zshrc` or open a new terminal.

### Getting a YouTube API key (free)
1. Go to https://console.cloud.google.com
2. Create a project → Enable "YouTube Data API v3"
3. Credentials → Create API Key → copy it

### Getting Telegram API credentials (free)
1. Go to https://my.telegram.org
2. Log in → "API development tools"
3. Create an app → copy `api_id` and `api_hash`

**First Telegram run only:** the script will prompt for your phone number and a
verification code. This creates a session file in `state/telegram_session.session`
and is never needed again.

---

## 3. Configure your sources

Edit `config/sources.yaml`:

```yaml
substack:
  - name: "Author Name"
    feed: "https://authorname.substack.com/feed"

youtube:
  - name: "Channel Name"
    channel_id: "UCxxxxxxxxxxxxxxxxxxxxxxxx"
    # Find channel ID: open channel → view page source → search "channelId"
    # Or: https://commentpicker.com/youtube-channel-id.php

telegram:
  - name: "Channel Name"
    username: "channel_username"   # without the @
```

---

## 4. Usage

### Weekly automated run (Substack + YouTube + Telegram)
```bash
python weekly_digest.py --auto
```

### LinkedIn post (paste text directly)
```bash
python weekly_digest.py --manual "paste the full post text here in quotes"
```

### LinkedIn post saved as a text file
```bash
python weekly_digest.py --manual linkedin_post.txt
```

### Any URL (article, preprint, etc.)
```bash
python weekly_digest.py --manual https://some-article.com/post
```

### PDF (local file or URL)
```bash
python weekly_digest.py --manual /path/to/paper.pdf
python weekly_digest.py --manual https://arxiv.org/pdf/2503.12345
```

### Mix of everything in one run
```bash
python weekly_digest.py --auto --manual linkedin_post.txt /path/to/paper.pdf
```

### See what's new without summarizing (no API calls)
```bash
python weekly_digest.py --auto --dry-run
```

### Re-process everything (ignore already-seen items)
```bash
python weekly_digest.py --auto --reset-seen
```

---

## 5. Import to Notion

In Notion: **Settings → Import → Markdown & CSV** → select one or all files
from the `summaries/` folder.

Each file imports as a full page with proper heading hierarchy.

---

## 6. Automate weekly (optional)

### macOS / Linux — cron
```bash
crontab -e
# Add this line (runs every Monday at 8am):
0 8 * * 1 cd /path/to/weekly_digest && python weekly_digest.py --auto >> logs/digest.log 2>&1
```

### VS Code task (runs on demand, no cron needed)
Create `.vscode/tasks.json` in the project folder:
```json
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "Run Weekly Digest",
      "type": "shell",
      "command": "python weekly_digest.py --auto",
      "options": { "cwd": "${workspaceFolder}" },
      "presentation": { "reveal": "always", "panel": "new" }
    }
  ]
}
```
Then: **Terminal → Run Task → Run Weekly Digest**

---

## 7. Customizing the summary format

Edit `config/prompt_template.txt` to change what sections appear in every summary,
adjust the tone, or add/remove immunology-specific instructions. Changes apply to
the next run — no code changes needed.

---

## Project structure

```
weekly_digest/
├── weekly_digest.py          # main script
├── requirements.txt
├── config/
│   ├── sources.yaml          # your source list — edit this
│   └── prompt_template.txt   # summary format — edit this
├── state/
│   ├── seen.json             # tracks processed items (auto-created)
│   └── telegram_session.*    # Telegram auth (auto-created on first run)
└── summaries/                # output .md files go here
```
