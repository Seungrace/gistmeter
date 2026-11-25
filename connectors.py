"""Social connectors. Each returns Posts: {link, source, text, ts}.
Public posts only. No author names or handles are stored, only the public channel or video source.
Add a platform by writing one function that returns this shape and listing it in ALL."""
import datetime, os
import httpx

YT = "https://www.googleapis.com/youtube/v3"

def youtube_posts():
    """Top comments on recent videos from channels you choose. Needs YOUTUBE_API_KEY and
    YOUTUBE_CHANNELS (comma-separated channel IDs). Uses a few quota units per channel per run."""
    key = os.environ.get("YOUTUBE_API_KEY")
    chans = [c.strip() for c in os.environ.get("YOUTUBE_CHANNELS", "").split(",") if c.strip()]
    if not key or not chans:
        return []
    out = []
    with httpx.Client(timeout=20) as h:
        for cid in chans:
            try:
                ch = h.get(f"{YT}/channels", params={"part": "contentDetails,snippet", "id": cid, "key": key}).json()["items"][0]
                name = ch["snippet"]["title"]
                up = ch["contentDetails"]["relatedPlaylists"]["uploads"]
                vids = h.get(f"{YT}/playlistItems", params={"part": "contentDetails", "playlistId": up,
                                                            "maxResults": 5, "key": key}).json().get("items", [])
                for v in vids:
                    vid = v["contentDetails"]["videoId"]
                    r = h.get(f"{YT}/commentThreads", params={"part": "snippet", "videoId": vid, "maxResults": 20,
                                                              "order": "relevance", "textFormat": "plainText", "key": key})
                    for it in r.json().get("items", []):  # comments may be disabled: no items
                        s = it["snippet"]["topLevelComment"]["snippet"]
                        ts = datetime.datetime.fromisoformat(s["publishedAt"].replace("Z", "+00:00")).timestamp()
                        out.append({"link": f"https://www.youtube.com/watch?v={vid}&lc={it['id']}",
                                    "source": f"YouTube: {name}", "text": s["textDisplay"][:280], "ts": int(ts)})
            except Exception as ex:
                print("youtube failed", cid, ex)
    return out

async def telegram_posts():
    """Recent messages from PUBLIC channels. Needs TG_API_ID, TG_API_HASH, TG_SESSION (a Telethon
    string session, see README) and TG_CHANNELS (comma-separated public channel usernames)."""
    api_id, api_hash, sess = os.environ.get("TG_API_ID"), os.environ.get("TG_API_HASH"), os.environ.get("TG_SESSION")
    chans = [c.strip().lstrip("@") for c in os.environ.get("TG_CHANNELS", "").split(",") if c.strip()]
    if not (api_id and api_hash and sess and chans):
        return []
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    out = []
    try:
        async with TelegramClient(StringSession(sess), int(api_id), api_hash) as c:
            for ch in chans:
                async for m in c.iter_messages(ch, limit=30):
                    if m.message:
                        out.append({"link": f"https://t.me/{ch}/{m.id}", "source": f"Telegram: {ch}",
                                    "text": m.message[:280], "ts": int(m.date.timestamp())})
    except Exception as ex:
        print("telegram failed", ex)
    return out
