"""Nigeria election pulse: scans news feeds, scores tone, flags checkable claims.
Run: uvicorn app:app --host 0.0.0.0 --port 8000
Env: ANTHROPIC_API_KEY, ADMIN_TOKEN
"""
import asyncio, hashlib, json, os, re, sqlite3, time, datetime
import connectors
from contextlib import asynccontextmanager
import anthropic, feedparser
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

MODEL = "claude-sonnet-5-5"            # claim checking (needs web search)
SCORE_MODEL = os.environ.get("SCORE_MODEL", "claude-haiku-5-5")  # cheaper model for tone scoring
ELECTION_DATE = "2027-01-16"  # confirm on inec.gov.ng; INEC has revised dates before
SCAN_EVERY = 600              # seconds
SOCIAL_EVERY = 1800           # seconds, protects API quotas
MAX_VERIFY = 2                # new claims checked per scan (controls cost)
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
# Verify each feed URL still works before launch.
FEEDS = {
    "Premium Times": "https://www.premiumtimesng.com/feed",
    "Punch": "https://punchng.com/feed/",
    "TheCable": "https://www.thecable.ng/feed",
    "Daily Trust": "https://dailytrust.com/feed",
    "Guardian Nigeria": "https://guardian.ng/feed/",
    "Vanguard": "https://www.vanguardngr.com/feed/",
}

client = anthropic.Anthropic()
db = sqlite3.connect(os.environ.get("DB_PATH", "pulse.db"), check_same_thread=False)
db.executescript("""
CREATE TABLE IF NOT EXISTS items(link TEXT PRIMARY KEY, source TEXT, title TEXT, ts INTEGER,
  kind TEXT, tone INTEGER, topic TEXT, claim TEXT, state TEXT, platform TEXT DEFAULT 'news', text_hash TEXT, analyzed INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS flags(id INTEGER PRIMARY KEY, claim TEXT, verdict TEXT, source_url TEXT,
  note TEXT, ts INTEGER, status TEXT DEFAULT 'pending');
""")

def ask(prompt, tools=None, model=MODEL):
    kw = {"tools": tools} if tools else {}
    r = client.messages.create(model=model, max_tokens=1500,
                               messages=[{"role": "user", "content": prompt}], **kw)
    return "".join(b.text for b in r.content if b.type == "text")

def parse_json(text):
    m = re.search(r"[\[{].*[\]}]", text, re.S)
    return json.loads(m.group(0)) if m else None

def fetch():
    for name, url in FEEDS.items():
        try:
            for e in feedparser.parse(url).entries[:30]:
                db.execute("INSERT OR IGNORE INTO items(link,source,title,ts) VALUES(?,?,?,?)",
                           (e.link, name, e.title, int(time.time())))
        except Exception as ex:
            print("feed failed", name, ex)
    db.commit()

def fetch_social():
    posts = connectors.youtube_posts() + asyncio.run(connectors.telegram_posts())
    for p in posts:
        norm = re.sub(r"[^a-z ]", "", p["text"].lower())
        h = hashlib.sha1(" ".join(norm.split()).encode()).hexdigest()[:16]
        plat = p["source"].split(":")[0].lower()
        db.execute("INSERT OR IGNORE INTO items(link,source,title,ts,platform,text_hash) VALUES(?,?,?,?,?,?)",
                   (p["link"], p["source"], p["text"], p["ts"], plat, h))
    db.commit()

def analyze():
    rows = db.execute("SELECT link,title,platform FROM items WHERE analyzed=0 LIMIT 40").fetchall()
    if not rows:
        return []
    listing = "\n".join(f"{i}. {t}" for i, (_, t, _p) in enumerate(rows))
    out = parse_json(ask(
        "These are Nigerian news headlines and public social media posts. For each, return a JSON list "
        'of objects {"i":int,"kind":"news"|"opinion","tone":-100..100 (mood of the coverage),'
        '"topic":"Security"|"Economy"|"Electoral process"|"Candidates and parties"|"Governance"|"Other","state":"the one Nigerian state the story is mainly about, or null","claim":"a specific, checkable factual claim or null"}. '
        "Return JSON only.\n" + listing, model=SCORE_MODEL)) or []
    claims = []
    for o in out:
        link = rows[o["i"]][0]
        db.execute("UPDATE items SET kind=?,tone=?,topic=?,claim=?,state=?,analyzed=1 WHERE link=?",
                   ("social" if rows[o["i"]][2] != "news" else o.get("kind", "news"), o.get("tone", 0), o.get("topic"), o.get("claim"), o.get("state"), link))
        if o.get("claim"):
            claims.append(o["claim"])
    db.commit()
    return claims

def verify(claim):
    text = ask(
        f"Check this claim about Nigerian elections using web search, preferring INEC, wire services "
        f'and fact-checkers (Dubawa, Africa Check, FactCheckHub): "{claim}". Return JSON only: '
        '{"verdict":"supported"|"contradicted"|"unsupported"|"needs_context",'
        '"source_url":"...","note":"one sentence"}. If no reliable source exists, use "unsupported".',
        tools=[{"type": "web_search_20250305", "name": "web_search"}])
    v = parse_json(text) or {}
    db.execute("INSERT INTO flags(claim,verdict,source_url,note,ts) VALUES(?,?,?,?,?)",
               (claim, v.get("verdict", "unsupported"), v.get("source_url"), v.get("note"), int(time.time())))
    db.commit()

async def loop():
    last_social = 0
    while True:
        try:
            await asyncio.to_thread(fetch)
            if time.time() - last_social > SOCIAL_EVERY:
                last_social = time.time()
                await asyncio.to_thread(fetch_social)
            claims = await asyncio.to_thread(analyze)
            done = 0
            for c in claims:
                if done >= MAX_VERIFY:
                    break
                if db.execute("SELECT 1 FROM flags WHERE claim=?", (c,)).fetchone():
                    continue  # never pay to re-check the same claim
                await asyncio.to_thread(verify, c)
                done += 1
        except Exception as ex:
            print("scan error", ex)
        await asyncio.sleep(SCAN_EVERY)

@asynccontextmanager
async def lifespan(_):
    t = asyncio.create_task(loop())
    yield
    t.cancel()

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def admin(tok):
    if not ADMIN_TOKEN or tok != ADMIN_TOKEN:
        raise HTTPException(401, "Admin token required")

@app.get("/api/pulse")
def pulse():
    rows = db.execute("""SELECT strftime('%Y-%m-%d %H:00',ts,'unixepoch') h, kind, ROUND(AVG(tone)), COUNT(*)
        FROM items WHERE analyzed=1 GROUP BY h,kind ORDER BY h""").fetchall()
    return {"election_date": ELECTION_DATE,
            "series": [{"hour": h, "kind": k, "tone": t, "count": n} for h, k, t, n in rows]}

@app.get("/api/narratives")
def narratives():
    rows = db.execute("""SELECT topic, COUNT(*), ROUND(AVG(tone)) FROM items
        WHERE analyzed=1 AND ts>? AND topic IS NOT NULL GROUP BY topic ORDER BY 2 DESC LIMIT 10""",
        (int(time.time()) - 86400,)).fetchall()
    return [{"topic": t, "items": n, "tone": a} for t, n, a in rows]

@app.get("/api/flags")
def public_flags():
    rows = db.execute("SELECT claim,verdict,source_url,note FROM flags WHERE status='approved' ORDER BY ts DESC LIMIT 50").fetchall()
    return [dict(zip(("claim", "verdict", "source_url", "note"), r)) for r in rows]

@app.get("/api/flags/pending")
def pending(x_admin_token: str = Header("")):
    admin(x_admin_token)
    rows = db.execute("SELECT id,claim,verdict,source_url,note FROM flags WHERE status='pending'").fetchall()
    return [dict(zip(("id", "claim", "verdict", "source_url", "note"), r)) for r in rows]

@app.post("/api/flags/{fid}/review")
def review(fid: int, status: str, x_admin_token: str = Header("")):
    admin(x_admin_token)
    if status not in ("approved", "dismissed"):
        raise HTTPException(400, "status must be approved or dismissed")
    db.execute("UPDATE flags SET status=? WHERE id=?", (status, fid))
    db.commit()
    return {"ok": True}

def mean_ci(v):
    k = len(v)
    if not k:
        return None, None
    m = sum(v) / k
    sd = (sum((x - m) ** 2 for x in v) / (k - 1)) ** 0.5 if k > 1 else 0
    return round(m, 1), round(1.96 * sd / k ** 0.5, 1)

@app.get("/api/stats")
def stats(days: int = 7):
    days = max(1, min(days, 90))
    since = int(time.time()) - days * 86400
    rows = db.execute("SELECT ts,tone,topic,state FROM items WHERE analyzed=1 AND ts>?", (since,)).fetchall()
    n = len(rows)
    tone, tci = mean_ci([r[1] for r in rows if r[1] is not None])
    topics = []
    for t in sorted({r[2] for r in rows if r[2]}):
        k = sum(1 for r in rows if r[2] == t)
        p = k / n
        topics.append({"topic": t, "n": k, "share": round(p, 4), "ci": round(1.96 * (p * (1 - p) / n) ** 0.5, 4)})
    states = []
    for st in {r[3] for r in rows if r[3]}:
        m, c = mean_ci([r[1] for r in rows if r[3] == st and r[1] is not None])
        states.append({"state": st, "n": sum(1 for r in rows if r[3] == st), "tone": m, "ci": c})
    daily = []
    for d in sorted({datetime.datetime.utcfromtimestamp(r[0]).strftime("%Y-%m-%d") for r in rows}):
        v = [r[1] for r in rows if datetime.datetime.utcfromtimestamp(r[0]).strftime("%Y-%m-%d") == d and r[1] is not None]
        m, c = mean_ci(v)
        daily.append({"day": d, "n": len(v), "tone": m, "ci": c})
    cl = dict(db.execute("SELECT verdict,COUNT(*) FROM flags WHERE status='approved' AND ts>? GROUP BY verdict", (since,)).fetchall())
    src = [{"kind": k, "n": c, "tone": None if t is None else round(t)} for k, c, t in db.execute(
        "SELECT kind,COUNT(*),AVG(tone) FROM items WHERE analyzed=1 AND ts>? GROUP BY kind", (since,)).fetchall()]
    soc = db.execute("SELECT COUNT(*) c FROM items WHERE platform!='news' AND text_hash IS NOT NULL AND ts>? GROUP BY text_hash", (since,)).fetchall()
    tot = sum(c for (c,) in soc)
    dup = sum(c for (c,) in soc if c >= 3)
    return {"sources": src, "social_dup": round(dup / tot, 3) if tot else None,
            "election_date": ELECTION_DATE, "days": days, "n": n, "tone": tone, "tone_ci": tci,
            "topics": sorted(topics, key=lambda x: -x["n"]),
            "states": sorted(states, key=lambda x: -x["n"]), "daily": daily, "claims": cl}

@app.get("/")
def home():
    return FileResponse("static/index.html")

@app.get("/sw.js")
def sw():
    return FileResponse("static/sw.js", media_type="application/javascript")

@app.get("/api/game/rumors")
def game_rumors(n: int = 10):
    """Random approved, clear-cut checks only (contradicted or supported)."""
    n = max(1, min(n, 20))
    rows = db.execute("""SELECT claim,verdict,source_url,note FROM flags WHERE status='approved'
        AND verdict IN ('contradicted','supported') ORDER BY RANDOM() LIMIT ?""", (n,)).fetchall()
    return [{"claim": c, "answer": "rumour" if v == "contradicted" else "real", "source_url": u, "note": nt}
            for c, v, u, nt in rows]

@app.get("/rumor")
def rumor_page():
    return FileResponse("static/rumor.html")

app.mount("/static", StaticFiles(directory="static"), name="static")
