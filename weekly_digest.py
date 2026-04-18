#!/usr/bin/env python3
"""
weekly_digest.py — automated weekly content summarizer

Modes:
  python weekly_digest.py --auto              # Substack + YouTube + Telegram (new items only)
  python weekly_digest.py --manual TEXT       # paste LinkedIn post text or any text directly
  python weekly_digest.py --manual path/to/file.pdf
  python weekly_digest.py --manual https://any-url.com
  python weekly_digest.py --auto --manual ... # both at once
  python weekly_digest.py --dry-run           # show what would be fetched, don't summarize
"""

import os
import re
from dotenv import load_dotenv
load_dotenv()
import sys
import json
import yaml
import argparse
import datetime
import textwrap
from pathlib import Path

import requests
import feedparser
from bs4 import BeautifulSoup
import anthropic

# ── PATHS ─────────────────────────────────────────────────────────────────────

ROOT         = Path(__file__).parent
CONFIG_FILE  = ROOT / "config" / "sources.yaml"
PROMPT_FILE  = ROOT / "config" / "prompt_template.txt"
STATE_FILE   = ROOT / "state" / "seen.json"
OUTPUT_DIR   = ROOT / "summaries"

# ── HELPERS ───────────────────────────────────────────────────────────────────

def load_config():
    with open(CONFIG_FILE) as f:
        return yaml.safe_load(f)

def load_prompt():
    return PROMPT_FILE.read_text()

def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"seen": []}

def save_state(state):
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))

def mark_seen(state, uid):
    if uid not in state["seen"]:
        state["seen"].append(uid)
    save_state(state)

def is_seen(state, uid):
    return uid in state["seen"]

def days_ago(n):
    return datetime.datetime.utcnow() - datetime.timedelta(days=n)

def clean_html(html_text, max_chars=14000):
    soup = BeautifulSoup(html_text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    # Collapse excessive blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:max_chars]

def safe_filename(title, date_str):
    clean = re.sub(r"[^\w\s-]", "", title).strip()
    clean = re.sub(r"\s+", "_", clean)[:60]
    return f"{date_str}_{clean}.md"

# ── FETCHERS ──────────────────────────────────────────────────────────────────

def fetch_substack(sources, state, lookback_days):
    items = []
    cutoff = days_ago(lookback_days)
    for source in sources:
        print(f"  Checking Substack: {source['name']}")
        try:
            feed = feedparser.parse(source["feed"])
        except Exception as e:
            print(f"    ✗ Feed error: {e}")
            continue
        for entry in feed.entries:
            uid = entry.get("id") or entry.get("link")
            if is_seen(state, uid):
                continue
            try:
                pub = datetime.datetime(*entry.published_parsed[:6])
            except Exception:
                continue
            if pub < cutoff:
                continue
            # Fetch full post
            try:
                r = requests.get(entry.link, timeout=15,
                                  headers={"User-Agent": "Mozilla/5.0"})
                content = clean_html(r.text)
            except Exception:
                content = entry.get("summary", "")
            items.append({
                "uid": uid,
                "title": entry.title,
                "link": entry.link,
                "source_name": source["name"],
                "content_type": "Substack article",
                "date": pub.strftime("%Y-%m-%d"),
                "content": content,
            })
            print(f"    + {entry.title[:70]}")
    return items


def fetch_youtube(sources, state, lookback_days):
    yt_key = os.environ.get("YOUTUBE_API_KEY")
    if not yt_key:
        print("  ⚠ YOUTUBE_API_KEY not set — skipping YouTube")
        return []
    try:
        from googleapiclient.discovery import build
        from youtube_transcript_api import YouTubeTranscriptApi
    except ImportError:
        print("  ⚠ Missing packages: pip install google-api-python-client youtube-transcript-api")
        return []

    items = []
    cutoff = days_ago(lookback_days)
    yt = build("youtube", "v3", developerKey=yt_key)
    week_ago_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    for source in sources:
        print(f"  Checking YouTube: {source['name']}")
        try:
            res = yt.search().list(
                channelId=source["channel_id"],
                publishedAfter=week_ago_iso,
                type="video",
                part="snippet",
                maxResults=10
            ).execute()
        except Exception as e:
            print(f"    ✗ YouTube API error: {e}")
            continue
        for item in res.get("items", []):
            vid_id = item["id"]["videoId"]
            uid = f"yt_{vid_id}"
            if is_seen(state, uid):
                continue
            title = item["snippet"]["title"]
            pub_str = item["snippet"]["publishedAt"][:10]
            # Get transcript
            try:
                transcript_list = YouTubeTranscriptApi.get_transcript(vid_id)
                transcript = " ".join(t["text"] for t in transcript_list)[:14000]
            except Exception:
                transcript = "(Transcript not available for this video)"
            items.append({
                "uid": uid,
                "title": title,
                "link": f"https://youtube.com/watch?v={vid_id}",
                "source_name": source["name"],
                "content_type": "YouTube video",
                "date": pub_str,
                "content": f"[YouTube transcript]\n\n{transcript}",
            })
            print(f"    + {title[:70]}")
    return items


def fetch_telegram(sources, state, lookback_days):
    """
    Requires one-time setup:
      pip install telethon
      Set TELEGRAM_API_ID and TELEGRAM_API_HASH env vars
      First run will prompt for phone number + code (creates a session file)
    """
    tg_id   = os.environ.get("TELEGRAM_API_ID")
    tg_hash = os.environ.get("TELEGRAM_API_HASH")
    if not tg_id or not tg_hash:
        print("  ⚠ TELEGRAM_API_ID / TELEGRAM_API_HASH not set — skipping Telegram")
        return []
    try:
        from telethon.sync import TelegramClient
        from telethon import functions
    except ImportError:
        print("  ⚠ Missing package: pip install telethon")
        return []

    items = []
    session_path = str(ROOT / "state" / "telegram_session")
    cutoff = days_ago(lookback_days)

    with TelegramClient(session_path, int(tg_id), tg_hash) as client:
        for source in sources:
            print(f"  Checking Telegram: {source['name']}")
            try:
                entity = client.get_entity(source["username"])
                messages = client.get_messages(entity, limit=20)
            except Exception as e:
                print(f"    ✗ Error: {e}")
                continue
            for msg in messages:
                if not msg.text:
                    continue
                if msg.date.replace(tzinfo=None) < cutoff:
                    continue
                uid = f"tg_{msg.id}_{source['username']}"
                if is_seen(state, uid):
                    continue
                # Build a link to the message if public channel
                link = f"https://t.me/{source['username']}/{msg.id}"
                items.append({
                    "uid": uid,
                    "title": f"{source['name']} — {msg.date.strftime('%Y-%m-%d')}",
                    "link": link,
                    "source_name": source["name"],
                    "content_type": "Telegram post",
                    "date": msg.date.strftime("%Y-%m-%d"),
                    "content": msg.text[:14000],
                })
                print(f"    + post {msg.id} ({msg.date.strftime('%Y-%m-%d')})")
    return items


def fetch_manual(inputs):
    """
    Accepts:
      - a URL string
      - a local file path (.pdf or .txt .md)
      - raw text (if it doesn't look like a URL or path)
    """
    items = []
    for inp in inputs:
        inp = inp.strip()
        print(f"  Processing manual input: {inp[:80]}")

        # Local PDF
        if inp.endswith(".pdf") and not inp.startswith("http"):
            try:
                import pdfplumber
                with pdfplumber.open(inp) as pdf:
                    text = "\n".join(p.extract_text() or "" for p in pdf.pages)
                text = text[:14000]
                title = Path(inp).stem
            except ImportError:
                print("  ⚠ pip install pdfplumber")
                continue
            except Exception as e:
                print(f"  ✗ PDF error: {e}")
                continue
            items.append({
                "uid": inp,
                "title": title,
                "link": inp,
                "source_name": "Manual (PDF)",
                "content_type": "PDF document",
                "date": datetime.date.today().isoformat(),
                "content": text,
            })

        # Local text/markdown file
        elif Path(inp).exists() and Path(inp).suffix in (".txt", ".md"):
            text = Path(inp).read_text()[:14000]
            items.append({
                "uid": inp,
                "title": Path(inp).stem,
                "link": inp,
                "source_name": "Manual (file)",
                "content_type": "Text file",
                "date": datetime.date.today().isoformat(),
                "content": text,
            })

        # URL
        elif inp.startswith("http"):
            try:
                r = requests.get(inp, timeout=15,
                                  headers={"User-Agent": "Mozilla/5.0"})
                content = clean_html(r.text)
                # Try to extract a title from <title> tag
                soup = BeautifulSoup(r.text, "html.parser")
                title = soup.title.string if soup.title else inp
                title = title.strip()[:120]
            except Exception as e:
                print(f"  ✗ Fetch error: {e}")
                continue
            items.append({
                "uid": inp,
                "title": title,
                "link": inp,
                "source_name": "Manual (URL)",
                "content_type": "Web article",
                "date": datetime.date.today().isoformat(),
                "content": content,
            })

        # Raw pasted text (LinkedIn posts etc.)
        else:
            first_line = inp.split("\n")[0][:80]
            items.append({
                "uid": f"manual_{hash(inp)}",
                "title": first_line or "Pasted content",
                "link": "N/A",
                "source_name": "Manual (pasted text)",
                "content_type": "Pasted text (e.g. LinkedIn)",
                "date": datetime.date.today().isoformat(),
                "content": inp[:14000],
            })

    return items

# ── SUMMARIZER ────────────────────────────────────────────────────────────────

def summarize(item, prompt_template):
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    prompt = prompt_template.format(
        title=item["title"],
        source_name=item["source_name"],
        content_type=item["content_type"],
        date=item["date"],
        link=item["link"],
        content=item["content"],
    )
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text


def save_summary(item, text):
    OUTPUT_DIR.mkdir(exist_ok=True)
    filename = safe_filename(item["title"], item["date"])
    out_path = OUTPUT_DIR / filename
    out_path.write_text(text, encoding="utf-8")
    print(f"    ✓ Saved: {filename}")
    return out_path

# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Weekly content digest")
    parser.add_argument("--auto",    action="store_true",
                        help="Fetch new items from all configured auto sources")
    parser.add_argument("--manual",  nargs="+", metavar="INPUT",
                        help="URL, PDF path, text file path, or quoted raw text")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be processed without calling the API")
    parser.add_argument("--reset-seen", action="store_true",
                        help="Clear seen-items state (will re-process everything)")
    args = parser.parse_args()

    if not args.auto and not args.manual:
        parser.print_help()
        sys.exit(0)

    if args.reset_seen:
        save_state({"seen": []})
        print("State reset.")

    config  = load_config()
    prompt  = load_prompt()
    state   = load_state()
    items   = []
    lookback = config.get("lookback_days", 8)

    if args.auto:
        print("\n── Auto sources ──────────────────────────────────────")
        if config.get("substack"):
            items += fetch_substack(config["substack"], state, lookback)
        if config.get("youtube"):
            items += fetch_youtube(config["youtube"], state, lookback)
        if config.get("telegram"):
            items += fetch_telegram(config["telegram"], state, lookback)

    if args.manual:
        print("\n── Manual inputs ─────────────────────────────────────")
        items += fetch_manual(args.manual)

    if not items:
        print("\nNothing new to summarize.")
        return

    print(f"\n── Summarizing {len(items)} item(s) ──────────────────────")
    if args.dry_run:
        for item in items:
            print(f"  [DRY RUN] Would summarize: {item['title'][:70]}")
        return

    saved_paths = []
    for item in items:
        print(f"  → {item['title'][:70]}")
        try:
            summary = summarize(item, prompt)
            path = save_summary(item, summary)
            saved_paths.append(path)
            mark_seen(state, item["uid"])
        except Exception as e:
            print(f"    ✗ Error summarizing: {e}")

    print(f"\nDone. {len(saved_paths)} summary file(s) in {OUTPUT_DIR}/")
    print("Import to Notion: Settings → Import → Markdown & CSV → select files")

if __name__ == "__main__":
    main()
