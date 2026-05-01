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
from urllib.parse import urljoin
import anthropic

# ── PATHS ─────────────────────────────────────────────────────────────────────

ROOT         = Path(__file__).parent
CONFIG_FILE  = ROOT / "config" / "sources.yaml"
PROMPT_FILE  = ROOT / "config" / "prompt_template.txt"
STATE_FILE   = ROOT / "state" / "seen.json"
OUTPUT_DIR   = ROOT / "summaries"

ALLOWED_SOURCES = {"substack", "youtube", "telegram", "inbox", "scraped_pages", "journals"}

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
MEDIA_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}

# ── HELPERS ───────────────────────────────────────────────────────────────────

def load_config(path=None):
    with open(path or CONFIG_FILE) as f:
        return yaml.safe_load(f)

def uid_source(uid):
    """Infer which source produced a uid — used for scoped --reset-seen."""
    if uid.startswith("jr_"):
        return "journals"
    if uid.startswith("yt_"):
        return "youtube"
    if uid.startswith("tg_"):
        return "telegram"
    if uid.startswith("sp_"):
        return "scraped_pages"
    if "substack.com" in uid:
        return "substack"
    return "inbox"

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
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - datetime.timedelta(days=n)

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

    try:
        client_ctx = TelegramClient(session_path, int(tg_id), tg_hash)
        client_ctx.start()
    except Exception as e:
        print(f"  ✗ Telegram connection failed: {e}")
        return []

    with client_ctx as client:
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


def _scrape_article_body(url):
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        for sel in [
            "div.documentContent", "div.article-body", "div.news-body",
            "article", "main", "div#content",
        ]:
            body = soup.select_one(sel)
            if body:
                return body.get_text(separator="\n", strip=True)[:12000]
    except Exception:
        pass
    return None


def fetch_scraped_pages(sources, state, lookback_days):
    items = []
    cutoff = days_ago(lookback_days)
    for source in sources:
        print(f"  Checking scraped page: {source['name']}")
        try:
            r = requests.get(source["url"], timeout=15, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
        except Exception as e:
            print(f"    ✗ Fetch error: {e}")
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        selector = source.get("link_selector", "a")
        links = soup.select(selector)
        if not links:
            print(f"    ⚠ No links matched selector '{selector}' — check HTML and update sources.yaml")
            continue
        found = 0
        for a in links:
            href = a.get("href", "")
            if not href or href.startswith("mailto:") or href.startswith("#"):
                continue
            url = urljoin(source["url"], href)
            uid = f"sp_{url}"
            if is_seen(state, uid):
                continue
            title = a.get_text(strip=True)
            if not title:
                continue
            pub = None
            date_pattern = source.get("date_pattern", "%d %B %Y")
            for candidate in a.parent.stripped_strings:
                try:
                    pub = datetime.datetime.strptime(candidate.strip(), date_pattern)
                    break
                except ValueError:
                    pass
            if pub and pub < cutoff:
                continue
            content = _scrape_article_body(url)
            items.append({
                "uid": uid,
                "title": title,
                "link": url,
                "source_name": source["name"],
                "content_type": "Web article",
                "date": pub.strftime("%Y-%m-%d") if pub else datetime.date.today().isoformat(),
                "content": content or title,
            })
            print(f"    + {title[:70]}")
            found += 1
        if found == 0:
            print(f"    ⚠ No new items found (selector matched {len(links)} links but none were new/in-window)")
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

def extract_from_screenshot(image_path):
    """Use Haiku vision to extract author, text, and date from a LinkedIn screenshot."""
    import base64
    path = Path(image_path)
    media_type = MEDIA_TYPES.get(path.suffix.lstrip(".").lower(), "image/jpeg")
    data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1000,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}},
                {"type": "text", "text": (
                    "This is a LinkedIn post screenshot. Extract:\n"
                    "1) Author full name\n"
                    "2) Full post text\n"
                    "3) Date if visible\n\n"
                    'Reply with JSON only: {"author": "...", "text": "...", "date": "YYYY-MM-DD or empty"}'
                )},
            ],
        }],
    )
    raw = msg.content[0].text
    match = re.search(r'\{.*\}', raw, re.DOTALL)
    if not match:
        return {"author": "", "text": "", "date": ""}
    return json.loads(match.group())


def _group_images_by_consecutive_number(image_files):
    """Group images whose filenames have consecutive trailing numbers.

    e.g. IMG_1234, IMG_1235, IMG_1236 → one group; IMG_1240 → new group.
    Files with no number sort last and each become their own group.
    """
    def trailing_number(f):
        nums = re.findall(r'\d+', f.stem)
        return int(nums[-1]) if nums else None

    numbered = sorted(
        [(trailing_number(f), f) for f in image_files if trailing_number(f) is not None],
        key=lambda x: x[0]
    )
    unnumbered = [f for f in image_files if trailing_number(f) is None]

    groups = []
    current = []
    for num, f in numbered:
        if current and num != trailing_number(current[-1]) + 1:
            groups.append(current)
            current = []
        current.append(f)
    if current:
        groups.append(current)
    for f in unnumbered:
        groups.append([f])
    return groups


def fetch_batch(folder, state):
    """Process .pdf/.txt/.md files and LinkedIn screenshots in a folder.

    Top-level images are grouped by minute (same-minute = one multi-part post).
    Subfolders of images are each treated as one post (manual override).
    """
    folder_path = Path(folder)
    if not folder_path.is_dir():
        print(f"  ✗ Not a directory: {folder}")
        return []

    text_files = sorted(f for f in folder_path.iterdir()
                        if f.is_file() and f.suffix in (".pdf", ".txt", ".md"))
    top_images = [f for f in folder_path.iterdir()
                  if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS]
    image_subdirs = sorted(d for d in folder_path.iterdir()
                           if d.is_dir() and any(
                               f.suffix.lower() in IMAGE_EXTENSIONS
                               for f in d.iterdir() if f.is_file()))

    if not text_files and not top_images and not image_subdirs:
        print(f"  ⚠ No processable files found in {folder}")
        return []

    items = []

    if text_files:
        print(f"  Found {len(text_files)} text/PDF file(s)")
        items += fetch_manual([str(f) for f in text_files])

    for images in _group_images_by_consecutive_number(top_images):
        uid = f"li_{images[0].stem}"
        if is_seen(state, uid):
            print(f"  [seen] {images[0].name}" + (f" (+{len(images)-1} more)" if len(images) > 1 else ""))
            continue
        label = f"{len(images)}-part post" if len(images) > 1 else "screenshot"
        print(f"  Extracting {label}: {', '.join(f.name for f in images)}")
        parts, author, date = [], None, None
        for img in images:
            extracted = extract_from_screenshot(img)
            author = author or extracted.get("author") or None
            date = date or extracted.get("date") or None
            if extracted.get("text"):
                parts.append(extracted["text"])
        author = author or f"Unknown ({images[0].stem})"
        content = f"Author: {author}\n\n" + "\n\n".join(parts)
        items.append({
            "uid": uid,
            "title": f"LinkedIn post by {author}",
            "link": str(images[0]),
            "source_name": author,
            "content_type": "LinkedIn post",
            "date": date or datetime.date.today().isoformat(),
            "content": content,
        })

    for subdir in image_subdirs:
        uid = f"li_{subdir.name}"
        if is_seen(state, uid):
            print(f"  [seen] {subdir.name}/")
            continue
        images = sorted(f for f in subdir.iterdir()
                        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS)
        print(f"  Extracting {len(images)}-part post: {subdir.name}/")
        parts, author, date = [], None, None
        for img in images:
            extracted = extract_from_screenshot(img)
            author = author or extracted.get("author") or None
            date = date or extracted.get("date") or None
            if extracted.get("text"):
                parts.append(extracted["text"])
        author = author or f"Unknown ({subdir.name})"
        content = f"Author: {author}\n\n" + "\n\n".join(parts)
        items.append({
            "uid": uid,
            "title": f"LinkedIn post by {author}",
            "link": str(subdir),
            "source_name": author,
            "content_type": "LinkedIn post",
            "date": date or datetime.date.today().isoformat(),
            "content": content,
        })

    return items


def is_review(entry):
    _REVIEW_KEYWORDS = {"review", "perspective", "overview", "primer"}
    art_type = entry.get("celpress_articletype", "").lower()
    if any(k in art_type for k in _REVIEW_KEYWORDS):
        return True
    tags = [getattr(t, "term", "").lower() for t in entry.get("tags", [])]
    if any(k in tag for k in _REVIEW_KEYWORDS for tag in tags):
        return True
    return any(k in entry.get("title", "").lower() for k in _REVIEW_KEYWORDS)


def try_fetch_fulltext(url):
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for selector in ["div.article__body", "div.c-article-body", "article"]:
            el = soup.select_one(selector)
            if el:
                text = el.get_text(separator="\n", strip=True)
                if len(text) > 200:
                    return text[:12000]
    except Exception:
        pass
    return None


def fetch_journals(sources, state, lookback_days):
    items = []
    cutoff = days_ago(lookback_days)
    for journal in sources:
        name = journal["name"]
        is_cell_press = bool(journal.get("rss_inpress") or journal.get("rss_current"))
        feed_urls = [journal[k] for k in ("rss", "rss_inpress", "rss_current") if journal.get(k)]
        seen_this_run: set = set()

        for feed_url in feed_urls:
            print(f"  Checking journal feed: {name}")
            try:
                feed = feedparser.parse(feed_url, agent="Mozilla/5.0")
            except Exception as e:
                print(f"    ✗ Feed error: {e}")
                continue

            for entry in feed.entries:
                raw_id = entry.get("id") or entry.get("link", "")
                uid = f"jr_{raw_id}"
                if uid in seen_this_run or is_seen(state, uid):
                    continue

                parsed = entry.get("published_parsed") or entry.get("updated_parsed")
                if not parsed:
                    continue
                try:
                    pub = datetime.datetime(*parsed[:6])
                except Exception:
                    continue
                if pub < cutoff:
                    continue

                if is_cell_press and not is_review(entry):
                    continue

                content = try_fetch_fulltext(entry.get("link", ""))
                if not content:
                    content = re.sub(r"<[^>]+>", " ", entry.get("summary", "")).strip()

                items.append({
                    "uid": uid,
                    "title": entry.get("title", "(no title)"),
                    "link": entry.get("link", ""),
                    "source_name": name,
                    "content_type": "Journal review",
                    "date": pub.strftime("%Y-%m-%d"),
                    "content": content,
                })
                seen_this_run.add(uid)
                print(f"    + {entry.get('title', '')[:70]}")

    return items


# ── SUMMARIZER ────────────────────────────────────────────────────────────────

def summarize(item, prompt_template, model="claude-sonnet-4-6"):
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    safe_content = item["content"].replace("{", "{{").replace("}", "}}")
    template = prompt_template
    if item.get("content_type") == "Journal review":
        template = prompt_template + JOURNAL_PROMPT_ADDENDUM
    prompt = template.format(
        title=item["title"],
        source_name=item["source_name"],
        content_type=item["content_type"],
        date=item["date"],
        link=item["link"],
        content=safe_content,
    )
    message = client.messages.create(
        model=model,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    if not message.content:
        raise ValueError(f"Empty response from API (stop_reason={message.stop_reason})")
    return message.content[0].text


def save_summary(item, text, subfolder=None):
    week_label = datetime.date.today().strftime("%Y-W%W")
    week_dir = OUTPUT_DIR / week_label
    if subfolder:
        week_dir = week_dir / subfolder
    week_dir.mkdir(parents=True, exist_ok=True)
    filename = safe_filename(item["title"], item["date"])
    out_path = week_dir / filename
    out_path.write_text(text, encoding="utf-8")
    label = f"{week_label}/{subfolder}/{filename}" if subfolder else f"{week_label}/{filename}"
    print(f"    ✓ Saved: {label}")
    return out_path


_KEYWORDS = [
    "immun", "autoimm", "t cell", "t-cell", "b cell", "b-cell", "treg", "tcr", "bcr",
    "antibody", "antigen", "vaccine", "cytokine", "chemokine", "interferon", "interleukin",
    "car-t", "car t", "checkpoint", "adjuvant", "mhc", "hla",
    "infect", "microb", "virus", "viral", "bacter", "patho", "parasit", "fungal",
    "cancer", "oncolog", "tumor", "tumour", "metasta", "myeloid", "lymphoid",
    "leukem", "lymphom", "neuro", "gene therapy", "cell therapy", "crispr",
    "rna", "mrna", "dna", "sequencing", "omics", "genomic", "proteomic",
    "transcriptomic", "metabolomic", "single-cell", "single cell",
    "receptor", "ligand", "signaling", "signalling", "molecul", "enzyme",
    "protein design", "therapeutic", "drug discovery", "drug design",
    "small molecule", "biologic", "pharma", "biotech", "fda", "ema",
    "clinical trial", "preclinical", "phase 1", "phase 2", "phase 3",
    "translation", "biomarker", "bioinformat", "computational biology",
    "ml for biology", "foundation model", "lab automation", "wet lab", "dry lab",
    "high-throughput", "screening", "cell biology", "molecular biology",
    "structural biology", "systems biology",
]


JOURNAL_PROMPT_ADDENDUM = """

---

If this is a published journal review or perspective:
- Identify the review's scope: which biological system, disease, or technology is being synthesized
- Note which findings are well-established consensus vs. actively debated
- Flag if the review proposes a new model, framework, or taxonomy
- For blog angles: focus on hooks useful for an immunology-specialist audience
"""


def keyword_filter(item):
    haystack = (item["title"] + " " + item["content"][:500]).lower()
    hit = next((k for k in _KEYWORDS if k in haystack), None)
    if hit:
        return {"relevance": "yes", "reason": f"keyword match: '{hit}'"}
    return {"relevance": "no", "reason": "no keyword match"}


def classify_relevance(item, model="claude-haiku-4-5-20251001"):
    import json
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    snippet = item["content"][:500]
    prompt = (
        "You are a relevance filter for a science communicator specializing in immunology.\n"
        "Relevant topics: immunology, T cells, Tregs, TCR/BCR, antibodies, vaccines, "
        "cytokines, cancer immunology, cell/gene therapy, CRISPR, omics, single-cell, "
        "biotech, drug discovery, clinical translation, ML for biology, lab tooling, "
        "computational biology, structural/molecular/systems biology.\n\n"
        f"Title: {item['title']}\n"
        f"Opening: {snippet}\n\n"
        'Reply with JSON only: {"relevance": "yes"|"maybe"|"no", "reason": "<one sentence>"}'
    )
    msg = client.messages.create(
        model=model,
        max_tokens=100,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if not match:
        return {"relevance": "maybe", "reason": "classifier returned unparseable response"}
    return json.loads(match.group())


def log_skipped(item, reason):
    log_path = ROOT / "skipped.log"
    with open(log_path, "a") as f:
        f.write(f"{item['date']} | {item['source_name']} | {item['title']} | {reason}\n")

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
                        help="Clear seen-items state (scoped to --sources/--only if given)")
    parser.add_argument("--batch", metavar="FOLDER",
                        help="Process all .pdf/.txt/.md files in a folder")
    parser.add_argument("--since", metavar="DATE",
                        help="Fetch items published on or after this date (YYYY-MM-DD), overrides lookback_days")
    parser.add_argument("--model", default="claude-sonnet-4-6",
                        choices=["claude-haiku-4-5-20251001", "claude-sonnet-4-6"],
                        help="Claude model to use (default: sonnet)")
    parser.add_argument("--config", metavar="PATH",
                        help="Alternative sources YAML config (default: config/sources.yaml)")
    parser.add_argument("--sources", metavar="LIST",
                        help=f"Comma-separated sources to process: {', '.join(sorted(ALLOWED_SOURCES))}. "
                             "inbox/ is always processed when present.")
    parser.add_argument("--only", metavar="SOURCE",
                        help="Shorthand for --sources with a single source")
    parser.add_argument("--filter-mode", choices=["llm", "keyword", "off"], default="llm",
                        help="Relevance filter: llm (default, uses Haiku), keyword (no API), off")
    args = parser.parse_args()

    # Resolve active source filter
    if args.only and args.sources:
        parser.error("--only and --sources are mutually exclusive")
    raw = args.only or args.sources
    if raw:
        active_sources = {s.strip().lower() for s in raw.split(",")}
        unknown = active_sources - ALLOWED_SOURCES
        if unknown:
            parser.error(f"Unknown source(s): {', '.join(sorted(unknown))}. "
                         f"Allowed: {', '.join(sorted(ALLOWED_SOURCES))}")
    else:
        active_sources = ALLOWED_SOURCES  # all

    if not args.auto and not args.manual and not args.batch:
        parser.print_help()
        sys.exit(0)

    config  = load_config(args.config)
    prompt  = load_prompt()
    state   = load_state()
    items   = []

    if args.reset_seen:
        if active_sources == ALLOWED_SOURCES:
            save_state({"seen": []})
            print("State reset (all sources).")
        else:
            state["seen"] = [uid for uid in state["seen"]
                             if uid_source(uid) not in active_sources]
            save_state(state)
            print(f"State reset for: {', '.join(sorted(active_sources))}.")

    if args.since:
        try:
            since_date = datetime.date.fromisoformat(args.since)
        except ValueError:
            print(f"✗ Invalid --since date '{args.since}'. Use YYYY-MM-DD format.")
            sys.exit(1)
        lookback = (datetime.date.today() - since_date).days
        print(f"  Using --since {args.since} ({lookback} days lookback)")
    else:
        lookback = config.get("lookback_days", 8)

    if args.auto:
        print("\n── Auto sources ──────────────────────────────────────")
        for src_name, fetcher, cfg_key in [
            ("substack",      fetch_substack,      "substack"),
            ("youtube",       fetch_youtube,       "youtube"),
            ("telegram",      fetch_telegram,      "telegram"),
            ("scraped_pages", fetch_scraped_pages, "scraped_pages"),
            ("journals",      fetch_journals,      "journals"),
        ]:
            if src_name not in active_sources or not config.get(cfg_key):
                continue
            try:
                items += fetcher(config[cfg_key], state, lookback)
            except Exception as e:
                print(f"  ✗ {src_name} fetcher crashed: {e}")

    if args.manual:
        print("\n── Manual inputs ─────────────────────────────────────")
        items += fetch_manual(args.manual)

    if args.batch:
        print(f"\n── Batch folder: {args.batch} ────────────────────────")
        items += fetch_batch(args.batch, state)

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
            if args.filter_mode == "llm":
                verdict = classify_relevance(item)
            elif args.filter_mode == "keyword":
                verdict = keyword_filter(item)
            else:
                verdict = {"relevance": "yes", "reason": "filter off"}

            rel = verdict["relevance"]
            if rel == "no":
                print(f"    ✗ Skipped ({verdict['reason']})")
                log_skipped(item, verdict["reason"])
                mark_seen(state, item["uid"])
                continue

            subfolder = "maybe" if rel == "maybe" else None
            if subfolder:
                print(f"    ~ maybe ({verdict['reason']})")

            summary = summarize(item, prompt, args.model)
            path = save_summary(item, summary, subfolder=subfolder)
            saved_paths.append(path)
            mark_seen(state, item["uid"])
        except Exception as e:
            print(f"    ✗ Error summarizing: {e}")

    print(f"\nDone. {len(saved_paths)} summary file(s) in {OUTPUT_DIR}/")
    print("Import to Notion: Settings → Import → Markdown & CSV → select files")

if __name__ == "__main__":
    main()
