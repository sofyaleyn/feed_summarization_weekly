# CLAUDE.md — Weekly Digest Project

This file tells Claude Code about this project's structure and conventions.

## What this project does

Automated weekly summarizer. Fetches new content from Substack (RSS), YouTube (Data API),
and Telegram (Telethon), plus accepts manual LinkedIn PDFs/text. Outputs Notion-importable
markdown summaries via the Anthropic API.

## Key files

| File | Purpose |
|---|---|
| `weekly_digest.py` | Main script — all logic lives here |
| `config/sources.yaml` | Source list — Substacks, YouTube channels, Telegram usernames |
| `config/prompt_template.txt` | Summary prompt — edit to change output format |
| `state/seen.json` | Tracks processed item IDs — do not edit manually |
| `requirements.txt` | Python dependencies |
| `.env` | API keys — never committed, never shown in code |

## Environment

- Python 3.10+
- Virtual environment at `.venv/`
- Keys loaded from `.env` via `python-dotenv`

Required env vars:
- `ANTHROPIC_API_KEY` — always required
- `YOUTUBE_API_KEY` — required only if YouTube sources are configured
- `TELEGRAM_API_ID` + `TELEGRAM_API_HASH` — required only if Telegram sources are configured

## Running the script

```bash
source .venv/bin/activate
python weekly_digest.py --auto              # fetch all new automated sources
python weekly_digest.py --auto --dry-run    # check without calling API
python weekly_digest.py --manual file.pdf  # manual input
python weekly_digest.py --batch inbox/2025-W16/  # batch folder (see README)
```

## Pending work / planned features

- [ ] `--batch FOLDER` argument: process all `.pdf`/`.txt`/`.md` files in a folder
      Implementation notes in README.md under "Batch folder plan"
- [ ] Telegram support: needs `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` set,
      plus `pip install telethon`. First run requires interactive phone auth.
- [ ] Consider adding `--since DATE` flag to override `lookback_days` for catch-up runs

## Conventions

- All content fetchers return a list of dicts with keys:
  `uid`, `title`, `link`, `source_name`, `content_type`, `date`, `content`
- `uid` is used for deduplication in `state/seen.json`
- Output files are named `YYYY-MM-DD_Title_slug.md` and saved to `summaries/`
- The prompt template in `config/prompt_template.txt` uses Python `.format()` substitution

## Do not

- Do not add API keys to any Python file
- Do not commit `state/`, `summaries/`, `logs/`, `.env`, or `inbox/`
- Do not change the placeholder names in `prompt_template.txt`
  (`{title}`, `{source_name}`, `{content_type}`, `{date}`, `{link}`, `{content}`)
  without updating the `summarize()` function in `weekly_digest.py`

## Adding a new source type

1. Write a `fetch_newtype(sources, state, lookback_days)` function following the
   same pattern as `fetch_substack` — return a list of item dicts
2. Add a section to `config/sources.yaml`
3. Call it in the `if args.auto:` block in `main()`
4. Document the new env vars (if any) in this file and in README.md
