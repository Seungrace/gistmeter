# Gist Meter backend

Scans Nigerian news feeds every 10 minutes, scores tone, clusters topics, and checks claims with Claude + web search. Only claims a human approves appear publicly.

## Run locally
    pip install -r requirements.txt
    export ANTHROPIC_API_KEY=sk-ant-...
    export ADMIN_TOKEN=choose-a-long-random-string
    uvicorn app:app --port 8000

## Endpoints
- GET  /api/pulse        tone by hour and source type
- GET  /api/narratives   top themes in the last 24h
- GET  /api/flags        approved claim checks (public)
- GET  /api/flags/pending            review queue (header x-admin-token)
- POST /api/flags/{id}/review?status=approved|dismissed

## Deploy (Render / Railway / Fly.io)
Start command: `uvicorn app:app --host 0.0.0.0 --port $PORT`. Set the two env vars. Attach a persistent disk for pulse.db, or swap SQLite for Postgres.

## Before launch
1. Confirm the election date on inec.gov.ng and update ELECTION_DATE.
2. Check every feed URL in FEEDS.
3. Keep a human in the review step for any claim labelled false.
4. Add rate limits and a spend cap on your Anthropic key.

## One-command deploy on Render
Push this folder to GitHub, create a Blueprint on Render pointing at it (uses render.yaml), then set ANTHROPIC_API_KEY. The dashboard is served at the root URL and is installable as a phone app.

## Social sources (optional, public posts only)
Set these env vars on Render. Leave any unset to skip that platform. Check each platform's current terms first.
- YouTube: `YOUTUBE_API_KEY`, `YOUTUBE_CHANNELS` (comma-separated channel IDs). Reads top comments on recent videos.
- Telegram: `TG_API_ID`, `TG_API_HASH` (from my.telegram.org), `TG_SESSION`, `TG_CHANNELS` (public channel usernames).
  Create the session once on your computer:
      python -c "from telethon.sync import TelegramClient; from telethon.sessions import StringSession; c=TelegramClient(StringSession(),API_ID,API_HASH); c.start(); print(c.session.save())"
  Treat TG_SESSION like a password.
Social is scanned every 30 minutes. No author names are stored. Do not scrape platforms or read private groups.
To add a platform, write a function in connectors.py that returns {link, source, text, ts} items and add it to fetch_social in app.py.

## Real or Rumour? (game)
Served at /rumor from static/rumor.html. It reads /api/game/rumors, which returns only reviewer-approved claims with a clear verdict (contradicted or supported). Needs at least 5 approved checks before it starts. Nothing unreviewed ever appears in the game.

## Cost controls
- Tone scoring uses SCORE_MODEL (default claude-haiku-5-5); claim checking uses the stronger model.
- At most 2 new claims are checked per scan and identical claims are never re-checked.
- Still set a monthly spend limit in the Anthropic Console.
