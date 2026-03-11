#!/usr/bin/env python3
"""
PodcastSync - Automatic podcast syncer for Rockbox iPod Classic
Run this file, then open http://localhost:5000 in your browser.
"""

import os
import sys
import json
import time
import shutil
import hashlib
import threading
import subprocess
import urllib.request
import urllib.error
import ssl
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

# Fix SSL certificate verification on macOS where Python doesn't use system certs
try:
    import certifi
    SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    # certifi not installed — fall back to unverified (still encrypted, just no cert check)
    SSL_CONTEXT = ssl._create_unverified_context()

# ── Config file lives next to this script ──────────────────────────────────────
BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
LOG_FILE = BASE_DIR / "sync.log"

DEFAULT_CONFIG = {
    "podcasts": [],          # list of {name, url, back_episodes}
    "ipod_path": "",         # e.g. /Volumes/IPOD or E:\\ or /media/user/IPOD
    "auto_sync": True,
    "check_interval": 30,    # seconds between iPod detection checks
    "last_sync": None,
    # Deletion modes:
    #   "off"      — never delete anything automatically (safest)
    #   "grace"    — delete only after file has been on iPod >= grace_days AND no active bookmark
    #   "bookmark" — delete only files that were started (had a bookmark) but bookmark is now gone
    "deletion_mode": "grace",
    "grace_days": 7,
}

# ── Globals ────────────────────────────────────────────────────────────────────
config = {}
sync_log = []          # in-memory log shown in UI
sync_running = False
watcher_thread = None

# ══════════════════════════════════════════════════════════════════════════════
# Config helpers
# ══════════════════════════════════════════════════════════════════════════════

def load_config():
    global config
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            data = json.load(f)
        # merge missing keys
        for k, v in DEFAULT_CONFIG.items():
            data.setdefault(k, v)
        config = data
    else:
        config = DEFAULT_CONFIG.copy()
    return config

def save_config():
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

# ══════════════════════════════════════════════════════════════════════════════
# Logging
# ══════════════════════════════════════════════════════════════════════════════

def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    entry = {"ts": ts, "level": level, "msg": msg}
    sync_log.append(entry)
    if len(sync_log) > 500:
        sync_log.pop(0)
    line = f"[{ts}] {level}: {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

# ══════════════════════════════════════════════════════════════════════════════
# iPod detection
# ══════════════════════════════════════════════════════════════════════════════

def is_ipod_connected():
    """Return True if the configured iPod path exists and looks like a Rockbox device."""
    path = config.get("ipod_path", "").strip()
    if not path:
        return False
    p = Path(path)
    # Rockbox devices always have a .rockbox folder
    return p.exists() and (p / ".rockbox").exists()

def detect_ipod_auto():
    """Try common mount points to find a Rockbox iPod automatically."""
    candidates = []
    if sys.platform == "darwin":
        volumes = Path("/Volumes")
        if volumes.exists():
            candidates = list(volumes.iterdir())
    elif sys.platform == "win32":
        import string
        candidates = [Path(f"{d}:\\") for d in string.ascii_uppercase]
    else:
        # Linux
        media_base = Path("/media")
        if media_base.exists():
            for user_dir in media_base.iterdir():
                candidates += list(user_dir.iterdir())
        mnt = Path("/mnt")
        if mnt.exists():
            candidates += list(mnt.iterdir())

    for candidate in candidates:
        try:
            if (candidate / ".rockbox").exists():
                return str(candidate)
        except PermissionError:
            pass
    return None

# ══════════════════════════════════════════════════════════════════════════════
# RSS parsing
# ══════════════════════════════════════════════════════════════════════════════

def fetch_rss(url):
    """Fetch and parse an RSS feed. Returns (episodes, channel_info) tuple."""
    ITUNES = "http://www.itunes.com/dtds/podcast-1.0.dtd"
    headers = {"User-Agent": "PodcastSync/1.0"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20, context=SSL_CONTEXT) as resp:
        data = resp.read()
    root = ET.fromstring(data)
    episodes = []
    channel = root.find("channel")
    if channel is None:
        return episodes, {}

    # ── Channel-level info (artwork, show title, author) ──────────────────────
    def itunes_el(parent, tag):
        el = parent.find(f"{{{ITUNES}}}{tag}")
        return el.text.strip() if el is not None and el.text else ""

    def itunes_attr(parent, tag, attr):
        el = parent.find(f"{{{ITUNES}}}{tag}")
        return el.get(attr, "") if el is not None else ""

    # Artwork: prefer <itunes:image href="…">, fall back to <image><url>
    channel_art = itunes_attr(channel, "image", "href")
    if not channel_art:
        img_el = channel.find("image")
        if img_el is not None:
            url_el = img_el.find("url")
            if url_el is not None and url_el.text:
                channel_art = url_el.text.strip()

    chan_title_el = channel.find("title")
    channel_title = chan_title_el.text.strip() if chan_title_el is not None and chan_title_el.text else ""
    channel_author = itunes_el(channel, "author") or itunes_el(channel, "owner/itunes:name")

    channel_info = {
        "artwork_url": channel_art,
        "title": channel_title,
        "author": channel_author,
    }

    # ── Per-episode parsing ───────────────────────────────────────────────────
    ep_number = 0
    for item in channel.findall("item"):
        enclosure = item.find("enclosure")
        if enclosure is None:
            continue
        mime = enclosure.get("type", "")
        if "audio" not in mime and not enclosure.get("url", "").lower().endswith(".mp3"):
            continue

        title_el = item.find("title")
        title = title_el.text.strip() if title_el is not None and title_el.text else "Untitled"

        pub_el = item.find("pubDate")
        pub = pub_el.text.strip() if pub_el is not None and pub_el.text else ""

        audio_url = enclosure.get("url", "")

        # Episode description
        desc = itunes_el(item, "summary") or itunes_el(item, "subtitle") or ""
        if not desc:
            desc_el = item.find("description")
            if desc_el is not None and desc_el.text:
                import re
                desc = re.sub(r"<[^>]+>", "", desc_el.text).strip()

        # Episode-level artwork (overrides channel art if present)
        ep_art = itunes_attr(item, "image", "href")

        # Episode number from <itunes:episode> or counter
        ep_num_el = item.find(f"{{{ITUNES}}}episode")
        if ep_num_el is not None and ep_num_el.text:
            try:
                ep_num = int(ep_num_el.text.strip())
            except ValueError:
                ep_number += 1
                ep_num = ep_number
        else:
            ep_number += 1
            ep_num = ep_number

        episodes.append({
            "title": title,
            "url": audio_url,
            "pub": pub,
            "mime": mime,
            "description": desc,
            "ep_art": ep_art,
            "ep_num": ep_num,
        })

    return episodes, channel_info

def sanitize_filename(name):
    """Make a string safe for use as a filename."""
    keep = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ._-()'")
    result = "".join(c if c in keep else "_" for c in name)
    return result[:120].strip()

def episode_id(episode):
    """Stable short ID for an episode based on its URL."""
    return hashlib.md5(episode["url"].encode()).hexdigest()[:8]

# ══════════════════════════════════════════════════════════════════════════════
# Download helpers
# ══════════════════════════════════════════════════════════════════════════════

def download_file(url, dest_path):
    """Download url → dest_path, return True on success."""
    headers = {"User-Agent": "PodcastSync/1.0"}
    req = urllib.request.Request(url, headers=headers)
    tmp = Path(str(dest_path) + ".part")
    try:
        with urllib.request.urlopen(req, timeout=60, context=SSL_CONTEXT) as resp, open(tmp, "wb") as out:
            shutil.copyfileobj(resp, out)
        tmp.rename(dest_path)
        return True
    except Exception as e:
        if tmp.exists():
            tmp.unlink()
        raise e

# ══════════════════════════════════════════════════════════════════════════════
# Artwork cache
# ══════════════════════════════════════════════════════════════════════════════

ARTWORK_CACHE_DIR = BASE_DIR / "artwork_cache"
ARTWORK_CACHE_DIR.mkdir(exist_ok=True)
_art_cache = {}  # url → bytes in memory

def fetch_artwork(url):
    """Download artwork bytes, cache to disk. Returns bytes or None."""
    if not url:
        return None
    if url in _art_cache:
        return _art_cache[url]
    # Check disk cache
    key = hashlib.md5(url.encode()).hexdigest()
    disk_path = ARTWORK_CACHE_DIR / f"{key}.jpg"
    if disk_path.exists():
        data = disk_path.read_bytes()
        _art_cache[url] = data
        return data
    # Download
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PodcastSync/1.0"})
        with urllib.request.urlopen(req, timeout=15, context=SSL_CONTEXT) as resp:
            data = resp.read()
        disk_path.write_bytes(data)
        _art_cache[url] = data
        return data
    except Exception as e:
        log(f"  Artwork download failed: {e}", "WARN")
        return None

# ══════════════════════════════════════════════════════════════════════════════
# ID3 tag embedding
# ══════════════════════════════════════════════════════════════════════════════

def embed_tags(mp3_path, podcast_name, episode_title, episode_num,
               description, artwork_bytes, author, year):
    """Write ID3v2 tags into the MP3 so Rockbox shows cover art & metadata."""
    try:
        from mutagen.id3 import (
            ID3, ID3NoHeaderError,
            TIT2, TPE1, TPE2, TALB, TRCK, TCON, TDRC, COMM, APIC
        )
        try:
            tags = ID3(str(mp3_path))
        except ID3NoHeaderError:
            tags = ID3()

        tags.delall("TIT2"); tags.add(TIT2(encoding=3, text=episode_title))
        tags.delall("TPE1"); tags.add(TPE1(encoding=3, text=author or podcast_name))
        tags.delall("TPE2"); tags.add(TPE2(encoding=3, text=author or podcast_name))
        tags.delall("TALB"); tags.add(TALB(encoding=3, text=podcast_name))
        tags.delall("TCON"); tags.add(TCON(encoding=3, text="Podcast"))
        if episode_num:
            tags.delall("TRCK"); tags.add(TRCK(encoding=3, text=str(episode_num)))
        if year:
            tags.delall("TDRC"); tags.add(TDRC(encoding=3, text=str(year)))
        if description:
            tags.delall("COMM")
            tags.add(COMM(encoding=3, lang="eng", desc="", text=description[:500]))
        if artwork_bytes:
            tags.delall("APIC")
            # Detect image type
            mime = "image/jpeg"
            if artwork_bytes[:8] == b"\x89PNG\r\n\x1a\n":
                mime = "image/png"
            tags.add(APIC(
                encoding=3,
                mime=mime,
                type=3,          # 3 = Cover (front)
                desc="Cover",
                data=artwork_bytes,
            ))
        tags.save(str(mp3_path), v2_version=3)  # ID3v2.3 — widest Rockbox support
        return True
    except ImportError:
        return False  # mutagen not installed — silently skip
    except Exception as e:
        log(f"  Tag write failed for {mp3_path.name}: {e}", "WARN")
        return False

# ══════════════════════════════════════════════════════════════════════════════
# Rockbox "listened" detection
# ══════════════════════════════════════════════════════════════════════════════

def get_rockbox_bookmarks(ipod_path):
    """
    Rockbox writes resume info and playlist history to .rockbox/nvram.bin and
    bookmarks to .rockbox/bookmarks. A simpler heuristic: Rockbox removes the
    bookmark when playback reaches 100%, or the user can use the 'delete after
    listen' plugin. We expose the option but warn it's based on Rockbox's
    bookmark files if present, otherwise skip.
    """
    listened = set()
    rb = Path(ipod_path) / ".rockbox"
    bm_file = rb / "bookmarks"
    if not bm_file.exists():
        return listened
    # Bookmarks file format: one line per bookmark, fields semicolon separated
    # We consider a file "listened" if it has NO bookmark (i.e. not in file)
    # So we collect what HAS a bookmark and subtract later.
    bookmarked = set()
    try:
        with open(bm_file, errors="replace") as f:
            for line in f:
                parts = line.strip().split(";")
                if parts:
                    bookmarked.add(parts[-1].strip().lower())
    except Exception:
        pass
    return bookmarked  # files that still have a resume point (NOT finished)

# ══════════════════════════════════════════════════════════════════════════════
# Sync state  (tracks what's been synced per podcast)
# ══════════════════════════════════════════════════════════════════════════════

SYNC_STATE_FILE = BASE_DIR / "sync_state.json"

def load_sync_state():
    if SYNC_STATE_FILE.exists():
        with open(SYNC_STATE_FILE) as f:
            return json.load(f)
    return {}

def save_sync_state(state):
    with open(SYNC_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

# ══════════════════════════════════════════════════════════════════════════════
# Core sync logic
# ══════════════════════════════════════════════════════════════════════════════

def run_sync(ipod_path=None):
    global sync_running
    if sync_running:
        log("Sync already in progress, skipping.", "WARN")
        return
    sync_running = True
    try:
        path = ipod_path or config.get("ipod_path", "")
        if not path:
            log("No iPod path configured.", "ERROR")
            return
        ipod = Path(path)
        podcasts_dir = ipod / "Podcasts"
        podcasts_dir.mkdir(exist_ok=True)

        state = load_sync_state()
        bookmarked = get_rockbox_bookmarks(path)

        for podcast in config.get("podcasts", []):
            name = podcast["name"]
            url = podcast["url"]
            back = int(podcast.get("back_episodes", 3))
            pod_dir = podcasts_dir / sanitize_filename(name)
            pod_dir.mkdir(exist_ok=True)

            log(f"Checking feed: {name}")
            try:
                episodes, chan_info = fetch_rss(url)
            except Exception as e:
                log(f"  Failed to fetch RSS for {name}: {e}", "ERROR")
                continue

            if not episodes:
                log(f"  No episodes found for {name}", "WARN")
                continue

            episodes = episodes[:back]  # newest N episodes
            pod_state = state.setdefault(name, {"synced": []})

            # ── Fetch podcast artwork once per sync ────────────────────────────
            art_url = podcast.get("artwork") or chan_info.get("artwork_url", "")
            artwork_bytes = fetch_artwork(art_url) if art_url else None
            pod_author = podcast.get("author") or chan_info.get("author", "")
            if artwork_bytes:
                log(f"  Cover art ready ({len(artwork_bytes)//1024}KB)")

            # ── Delete listened episodes ───────────────────────────────────────
            deletion_mode = config.get("deletion_mode", "grace")
            grace_days    = int(config.get("grace_days", 7))

            if deletion_mode != "off":
                now = datetime.now()
                for f in pod_dir.glob("*.mp3"):
                    if f.name not in pod_state.get("files", []):
                        continue  # not a file we synced — never touch it
                    rel = str(f.relative_to(ipod)).lower().replace("\\", "/")
                    had_bookmark = rel in pod_state.get("bookmarked_seen", [])
                    has_bookmark = rel in bookmarked

                    # Track that we have seen this file bookmarked (started)
                    if has_bookmark:
                        pod_state.setdefault("bookmarked_seen", [])
                        if rel not in pod_state["bookmarked_seen"]:
                            pod_state["bookmarked_seen"].append(rel)
                        had_bookmark = True

                    # Determine sync age
                    sync_ts = pod_state.get("sync_times", {}).get(f.name)
                    if sync_ts:
                        age_days = (now - datetime.fromisoformat(sync_ts)).days
                    else:
                        age_days = 0  # unknown — treat as new

                    should_delete = False
                    if deletion_mode == "bookmark":
                        # Only delete if it was started (had bookmark) and bookmark is now gone
                        should_delete = had_bookmark and not has_bookmark
                    elif deletion_mode == "grace":
                        # Delete if: no active bookmark AND file has been on iPod >= grace_days
                        # This prevents deleting brand-new unplayed episodes
                        should_delete = (not has_bookmark) and (age_days >= grace_days)

                    if should_delete:
                        log(f"  Removing listened: {f.name} (age {age_days}d, mode={deletion_mode})")
                        try:
                            f.unlink()
                            pod_state.get("files", []).remove(f.name)
                        except Exception as ex:
                            log(f"  Could not delete {f.name}: {ex}", "WARN")

            # ── Download new episodes ──────────────────────────────────────────
            log(f"  {len(episodes)} episode(s) in feed, {len(pod_state.get('synced', []))} in history")
            for ep in reversed(episodes):  # oldest first
                eid = episode_id(ep)
                if eid in pod_state["synced"]:
                    log(f"  Skipping (already synced): {ep['title'][:55]}")
                    continue
                safe_title = sanitize_filename(ep["title"])
                filename = f"{safe_title}.mp3"
                dest = pod_dir / filename
                if dest.exists():
                    log(f"  Skipping (file exists): {filename[:55]}")
                    pod_state["synced"].append(eid)
                    continue
                log(f"  Downloading: {ep['title'][:60]}")
                try:
                    download_file(ep["url"], dest)

                    # ── Embed ID3 tags + cover art ─────────────────────────────
                    year = ""
                    if ep.get("pub"):
                        try:
                            from email.utils import parsedate
                            pd = parsedate(ep["pub"])
                            if pd:
                                year = str(pd[0])
                        except Exception:
                            pass
                    # Use episode-level art if available, else channel art
                    ep_art = fetch_artwork(ep.get("ep_art")) if ep.get("ep_art") else None
                    tag_art = ep_art or artwork_bytes
                    tagged = embed_tags(
                        mp3_path=dest,
                        podcast_name=name,
                        episode_title=ep["title"],
                        episode_num=ep.get("ep_num"),
                        description=ep.get("description", ""),
                        artwork_bytes=tag_art,
                        author=pod_author,
                        year=year,
                    )
                    suffix = " + cover art" if (tagged and tag_art) else (" (no mutagen)" if not tagged else "")
                    pod_state["synced"].append(eid)
                    pod_state.setdefault("files", []).append(filename)
                    # Record when this file was synced so grace-period deletion works
                    pod_state.setdefault("sync_times", {})[filename] = datetime.now().isoformat()
                    log(f"  ✓ {filename}{suffix}")
                except Exception as e:
                    log(f"  ✗ Failed: {e}", "ERROR")

            state[name] = pod_state

        save_sync_state(state)
        config["last_sync"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_config()
        log("Sync complete! ✓")
    finally:
        sync_running = False

# ══════════════════════════════════════════════════════════════════════════════
# Background watcher thread
# ══════════════════════════════════════════════════════════════════════════════

watcher_active = True

def ipod_watcher():
    """Polls for iPod connection and triggers sync when found."""
    was_connected = False
    while watcher_active:
        # Auto-detect iPod path if not configured
        if not config.get("ipod_path", "").strip():
            found = detect_ipod_auto()
            if found:
                log(f"Auto-detected iPod at: {found}")
                config["ipod_path"] = found
                save_config()

        connected = is_ipod_connected()
        if connected and not was_connected:
            log("iPod detected! Starting sync…")
            threading.Thread(target=run_sync, daemon=True).start()
        elif not connected and was_connected:
            log("iPod disconnected.")
        was_connected = connected
        time.sleep(config.get("check_interval", 30))

# ══════════════════════════════════════════════════════════════════════════════
# Podcast search (uses Podcast Index public API - no key required for basic search)
# ══════════════════════════════════════════════════════════════════════════════

def search_podcasts(query):
    """Search for podcasts via the iTunes Search API (no key needed)."""
    encoded = urllib.request.quote(query)
    url = f"https://itunes.apple.com/search?term={encoded}&media=podcast&limit=20&entity=podcast"
    headers = {"User-Agent": "PodcastSync/1.0"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10, context=SSL_CONTEXT) as resp:
            data = json.loads(resp.read())
        results = []
        for item in data.get("results", []):
            results.append({
                "name": item.get("collectionName", ""),
                "author": item.get("artistName", ""),
                "description": item.get("description") or item.get("shortDescription") or "",
                "artwork": item.get("artworkUrl600") or item.get("artworkUrl100", ""),
                "feed_url": item.get("feedUrl", ""),
                "genre": item.get("primaryGenreName", ""),
                "episode_count": item.get("trackCount", 0),
            })
        return [r for r in results if r["feed_url"]]
    except Exception as e:
        log(f"Search error: {e}", "ERROR")
        return []

# ══════════════════════════════════════════════════════════════════════════════
# HTTP server
# ══════════════════════════════════════════════════════════════════════════════

HTML = r"""<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PodcastSync</title>
<style>
  :root {
    --accent: #ff6b2b;
    --accent2: #ffb347;
    --success: #4caf7d;
    --error: #e05252;
    --warn: #e0a052;
    --mono: 'Courier New', 'Lucida Console', monospace;
    --sans: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
  }
  [data-theme="dark"] {
    --bg: #0e0e0e; --surface: #161616; --surface2: #1c1c1c;
    --border: #2a2a2a; --text: #e8e8e8; --text2: #aaa;
    --muted: #555; --input-bg: #080808; --shadow: none;
    --overlay: rgba(0,0,0,0.75);
  }
  [data-theme="light"] {
    --bg: #f2efe9; --surface: #ffffff; --surface2: #f7f4ef;
    --border: #ddd8cf; --text: #1a1a1a; --text2: #555;
    --muted: #999; --input-bg: #f7f4ef; --shadow: 0 1px 5px rgba(0,0,0,0.08);
    --overlay: rgba(0,0,0,0.4);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: var(--sans); min-height: 100vh; transition: background 0.25s, color 0.25s; }
  header { background: var(--surface); border-bottom: 1px solid var(--border); padding: 0 28px; display: flex; align-items: center; justify-content: space-between; height: 60px; position: sticky; top: 0; z-index: 100; box-shadow: var(--shadow); }
  .logo { display: flex; align-items: center; gap: 12px; }
  .logo-icon { width: 36px; height: 36px; background: var(--accent); border-radius: 8px; display: flex; align-items: center; justify-content: center; font-size: 18px; }
  .logo h1 { font-family: var(--mono); font-size: 1.1rem; font-weight: 700; letter-spacing: -0.3px; }
  .logo span { font-size: 0.7rem; color: var(--muted); display: block; }
  .header-right { display: flex; align-items: center; gap: 12px; }
  .ipod-pill { display: flex; align-items: center; gap: 8px; background: var(--surface2); border: 1px solid var(--border); border-radius: 20px; padding: 5px 12px; font-family: var(--mono); font-size: 0.72rem; color: var(--text2); }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); flex-shrink: 0; }
  .dot.connected { background: var(--success); box-shadow: 0 0 6px var(--success); animation: blink 2s infinite; }
  .dot.syncing { background: var(--accent); box-shadow: 0 0 6px var(--accent); animation: blink 0.6s infinite; }
  @keyframes blink { 0%,100%{opacity:1} 50%{opacity:0.3} }
  .theme-btn { background: var(--surface2); border: 1px solid var(--border); border-radius: 8px; color: var(--text); cursor: pointer; font-size: 1.1rem; width: 36px; height: 36px; display: flex; align-items: center; justify-content: center; transition: background 0.2s; }
  .theme-btn:hover { background: var(--border); }
  .tab-bar { background: var(--surface); border-bottom: 1px solid var(--border); display: flex; padding: 0 28px; gap: 4px; }
  .tab { background: none; border: none; border-bottom: 2px solid transparent; color: var(--muted); cursor: pointer; font-family: var(--mono); font-size: 0.75rem; letter-spacing: 1px; padding: 12px 16px 10px; text-transform: uppercase; transition: color 0.2s, border-color 0.2s; }
  .tab:hover { color: var(--text); }
  .tab.active { color: var(--accent); border-bottom-color: var(--accent); }
  .page { display: none; }
  .page.active { display: block; }
  main { max-width: 860px; margin: 0 auto; padding: 28px 20px; display: grid; gap: 20px; }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 22px; box-shadow: var(--shadow); }
  .card-title { font-family: var(--mono); font-size: 0.65rem; letter-spacing: 2.5px; text-transform: uppercase; color: var(--accent); margin-bottom: 16px; display: flex; align-items: center; gap: 10px; }
  .card-title::after { content:''; flex:1; height:1px; background:var(--border); }
  input[type=text], input[type=number], select { background: var(--input-bg); border: 1px solid var(--border); border-radius: 6px; color: var(--text); font-family: var(--mono); font-size: 0.82rem; padding: 9px 12px; width: 100%; transition: border-color 0.2s; }
  input:focus, select:focus { outline: none; border-color: var(--accent); }
  label { font-size: 0.7rem; color: var(--muted); font-family: var(--mono); display: block; margin-bottom: 5px; letter-spacing: 0.5px; }
  .field { display: flex; flex-direction: column; }
  .hint { font-size: 0.7rem; color: var(--muted); margin-top: 6px; font-family: var(--mono); }
  button { background: var(--accent); border: none; border-radius: 6px; color: #fff; cursor: pointer; font-family: var(--mono); font-size: 0.78rem; font-weight: 700; padding: 9px 16px; white-space: nowrap; transition: filter 0.15s, transform 0.1s; letter-spacing: 0.5px; }
  button:hover { filter: brightness(1.1); }
  button:active { transform: scale(0.97); }
  button.secondary { background: transparent; border: 1px solid var(--border); color: var(--text2); }
  button.secondary:hover { border-color: var(--text); color: var(--text); filter: none; }
  button.danger { background: var(--error); }
  button:disabled { opacity: 0.35; cursor: not-allowed; transform: none; filter: none; }
  .sync-btn { width: 100%; padding: 15px; font-size: 0.95rem; letter-spacing: 2px; border-radius: 8px; background: linear-gradient(135deg, var(--accent), var(--accent2)); }
  .row { display: flex; gap: 10px; align-items: flex-end; }
  .row .field { flex: 1; }
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .full { grid-column: 1 / -1; }
  .status-bar { display: flex; align-items: center; justify-content: space-between; font-family: var(--mono); font-size: 0.7rem; color: var(--muted); padding: 10px 0 2px; }
  .toggle-row { display: flex; align-items: center; justify-content: space-between; padding: 12px 0; border-bottom: 1px solid var(--border); }
  .toggle-row:last-child { border-bottom: none; padding-bottom: 0; }
  .toggle-label { font-size: 0.9rem; }
  .toggle-desc { font-size: 0.73rem; color: var(--muted); margin-top: 2px; }
  .toggle { position: relative; width: 42px; height: 23px; flex-shrink: 0; }
  .toggle input { opacity:0; width:0; height:0; }
  .slider { position: absolute; inset: 0; background: var(--border); border-radius: 23px; cursor: pointer; transition: 0.2s; }
  .slider::before { content:''; position: absolute; width: 17px; height: 17px; left: 3px; top: 3px; background: white; border-radius: 50%; transition: 0.2s; }
  input:checked + .slider { background: var(--accent); }
  input:checked + .slider::before { transform: translateX(19px); }
  .podcast-list { display: grid; gap: 10px; }
  .podcast-item { background: var(--surface2); border: 1px solid var(--border); border-radius: 8px; padding: 13px 15px; display: flex; align-items: center; gap: 13px; transition: border-color 0.2s; }
  .podcast-item:hover { border-color: var(--accent); }
  .pod-art { width: 48px; height: 48px; border-radius: 6px; object-fit: cover; flex-shrink: 0; background: var(--border); }
  .pod-art-placeholder { width: 48px; height: 48px; border-radius: 6px; background: linear-gradient(135deg, var(--accent), var(--accent2)); display: flex; align-items: center; justify-content: center; font-size: 20px; flex-shrink: 0; }
  .pod-info { flex: 1; min-width: 0; }
  .pod-name { font-weight: 600; font-size: 0.9rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .pod-author { font-size: 0.73rem; color: var(--text2); margin-top: 2px; }
  .pod-back { font-family: var(--mono); font-size: 0.7rem; color: var(--accent2); flex-shrink: 0; background: var(--surface); border: 1px solid var(--border); border-radius: 4px; padding: 2px 7px; }
  .empty-state { text-align: center; padding: 32px; color: var(--muted); font-family: var(--mono); font-size: 0.8rem; }
  .log-box { background: var(--input-bg); border: 1px solid var(--border); border-radius: 6px; font-family: var(--mono); font-size: 0.73rem; height: 200px; overflow-y: auto; padding: 10px 12px; display: flex; flex-direction: column; gap: 2px; }
  .log-entry { display: flex; gap: 10px; }
  .log-ts { color: var(--muted); flex-shrink: 0; }
  .log-INFO { color: var(--text); }
  .log-WARN { color: var(--warn); }
  .log-ERROR { color: var(--error); }
  .search-bar { display: flex; gap: 10px; margin-bottom: 20px; }
  .search-bar input { font-size: 0.9rem; padding: 11px 14px; }
  .search-bar button { padding: 11px 20px; font-size: 0.8rem; flex-shrink: 0; }
  .results-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 16px; }
  .result-card { background: var(--surface2); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; transition: border-color 0.2s, transform 0.15s; cursor: pointer; display: flex; flex-direction: column; }
  .result-card:hover { border-color: var(--accent); transform: translateY(-2px); }
  .result-art { width: 100%; aspect-ratio: 1; object-fit: cover; background: var(--border); display: block; }
  .result-art-placeholder { width: 100%; aspect-ratio: 1; background: linear-gradient(135deg, var(--surface2), var(--border)); display: flex; align-items: center; justify-content: center; font-size: 48px; }
  .result-body { padding: 12px; flex: 1; display: flex; flex-direction: column; gap: 5px; }
  .result-name { font-weight: 600; font-size: 0.85rem; line-height: 1.3; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .result-author { font-size: 0.72rem; color: var(--text2); }
  .result-genre { font-size: 0.62rem; font-family: var(--mono); background: var(--surface); border: 1px solid var(--border); border-radius: 3px; padding: 1px 6px; width: fit-content; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; }
  .result-card .add-btn { margin: 0 12px 12px; width: calc(100% - 24px); padding: 8px; }
  .result-card .add-btn.added { background: var(--success); }
  .modal-overlay { position: fixed; inset: 0; background: var(--overlay); z-index: 200; display: none; align-items: center; justify-content: center; padding: 20px; }
  .modal-overlay.open { display: flex; }
  .modal { background: var(--surface); border: 1px solid var(--border); border-radius: 14px; width: 100%; max-width: 520px; max-height: 90vh; overflow-y: auto; box-shadow: 0 20px 60px rgba(0,0,0,0.4); }
  .modal-header { display: flex; gap: 16px; padding: 20px; border-bottom: 1px solid var(--border); align-items: flex-start; }
  .modal-art { width: 90px; height: 90px; border-radius: 10px; object-fit: cover; flex-shrink: 0; background: var(--surface2); }
  .modal-art-placeholder { width: 90px; height: 90px; border-radius: 10px; background: linear-gradient(135deg, var(--accent), var(--accent2)); display: flex; align-items: center; justify-content: center; font-size: 40px; flex-shrink: 0; }
  .modal-meta { flex: 1; min-width: 0; padding-top: 2px; }
  .modal-title { font-size: 1.05rem; font-weight: 700; line-height: 1.3; margin-bottom: 5px; }
  .modal-author { font-size: 0.8rem; color: var(--text2); margin-bottom: 8px; }
  .modal-badges { display: flex; gap: 6px; flex-wrap: wrap; }
  .badge { font-size: 0.65rem; font-family: var(--mono); background: var(--surface2); border: 1px solid var(--border); border-radius: 4px; padding: 2px 7px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.5px; }
  .modal-body { padding: 20px; }
  .modal-desc { font-size: 0.85rem; line-height: 1.65; color: var(--text2); margin-bottom: 20px; max-height: 150px; overflow-y: auto; }
  .modal-add-row { display: flex; gap: 10px; align-items: flex-end; padding-top: 16px; border-top: 1px solid var(--border); }
  .modal-add-row .field { flex: 1; }
  .modal-footer { display: flex; justify-content: flex-end; padding: 0 20px 18px; }
  .search-placeholder { text-align: center; padding: 60px 20px; color: var(--muted); font-family: var(--mono); font-size: 0.82rem; }
  .search-placeholder .big { font-size: 3rem; margin-bottom: 12px; }
  .searching-spinner { text-align: center; padding: 40px; color: var(--muted); font-family: var(--mono); }
  .top-podcasts-section { }
  .section-label { font-family: var(--mono); font-size: 0.7rem; letter-spacing: 1.5px; text-transform: uppercase; color: var(--muted); margin-bottom: 14px; }
  .genre-bar { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 20px; margin-top: 4px; }
  .genre-btn { background: var(--surface2); border: 1px solid var(--border); border-radius: 20px; color: var(--text2); cursor: pointer; font-family: var(--sans); font-size: 0.78rem; font-weight: 400; padding: 6px 14px; transition: all 0.15s; letter-spacing: 0; }
  .genre-btn:hover { border-color: var(--accent); color: var(--text); filter: none; }
  .genre-btn.active { background: var(--accent); border-color: var(--accent); color: #fff; font-weight: 600; }
  .card-rating { display: flex; align-items: center; gap: 5px; margin-top: 6px; }
  .stars { color: #f5a623; font-size: 0.75rem; letter-spacing: 1px; }
  .rating-num { font-family: var(--mono); font-size: 0.72rem; color: var(--text2); font-weight: 600; }
  .rating-count { font-family: var(--mono); font-size: 0.68rem; color: var(--muted); }
  .modal-rating { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; padding-bottom: 12px; border-bottom: 1px solid var(--border); }
  .modal-stars { color: #f5a623; font-size: 1rem; letter-spacing: 2px; }
  .modal-rating-num { font-family: var(--mono); font-size: 0.9rem; font-weight: 700; color: var(--text); }
</style>
</head>
<body>

<header>
  <div class="logo">
    <div class="logo-icon">🎙</div>
    <div>
      <h1>PodcastSync</h1>
      <span>for Rockbox iPod Classic</span>
    </div>
  </div>
  <div class="header-right">
    <div class="ipod-pill">
      <div class="dot" id="statusDot"></div>
      <span id="statusText">Checking…</span>
    </div>
    <button class="theme-btn" id="themeBtn" title="Toggle dark/light mode" onclick="toggleTheme()">🌙</button>
  </div>
</header>

<div class="tab-bar">
  <button class="tab active" onclick="showTab('sync',this)">⚡ Sync</button>
  <button class="tab" onclick="showTab('podcasts',this)">🎙 My Podcasts</button>
  <button class="tab" onclick="showTab('discover',this)">🔍 Discover</button>
  <button class="tab" onclick="showTab('settings',this)">⚙ Settings</button>
</div>

<!-- SYNC TAB -->
<div class="page active" id="page-sync">
<main>
  <div class="card">
    <div class="card-title">Sync Control</div>
    <button class="sync-btn" id="syncBtn" onclick="manualSync()">⚡ SYNC NOW</button>
    <div class="status-bar">
      <span>Last sync: <span id="lastSync">never</span></span>
      <button class="secondary" onclick="resetAllSyncHistory()" style="font-size:0.7rem;padding:4px 10px">
        🗑 Reset sync history
      </button>
    </div>
    <p class="hint" style="margin-top:8px">If episodes aren't downloading, reset the sync history — this forces all episodes to re-download on the next sync.</p>
  </div>
  <div class="card">
    <div class="card-title">iPod Location</div>
    <div class="row">
      <div class="field">
        <label>Mount path (e.g. /Volumes/IPOD or E:\)</label>
        <input type="text" id="ipodPath" placeholder="Leave blank to auto-detect">
      </div>
      <button onclick="autoDetect()" class="secondary">Auto-Detect</button>
      <button onclick="savePath()">Save</button>
    </div>
    <p class="hint">Your iPod must have Rockbox installed (a .rockbox folder on the drive).</p>
    <div style="margin-top:14px;padding-top:14px;border-top:1px solid var(--border)">
      <button class="secondary" onclick="scanDrives()" id="scanBtn" style="font-size:0.72rem;padding:7px 12px">
        🔎 Show all detected drives
      </button>
      <div id="scanResult" style="margin-top:10px;font-family:var(--mono);font-size:0.75rem;color:var(--text2);display:none"></div>
    </div>
  </div>
  <div class="card">
    <div class="card-title">Activity Log</div>
    <div class="log-box" id="logBox"></div>
  </div>
</main>
</div>

<!-- PODCASTS TAB -->
<div class="page" id="page-podcasts">
<main>
  <div class="card">
    <div class="card-title">My Podcasts</div>
    <div class="podcast-list" id="podcastList">
      <div class="empty-state">No podcasts yet — use Discover to find some!</div>
    </div>
  </div>
  <div class="card">
    <div class="card-title">Add by RSS URL</div>
    <div class="grid2">
      <div class="field">
        <label>Podcast Name</label>
        <input type="text" id="newName" placeholder="e.g. My Favourite Podcast">
      </div>
      <div class="field">
        <label>Back-episodes to keep</label>
        <input type="number" id="newBack" value="3" min="1" max="50">
      </div>
      <div class="field full">
        <label>RSS Feed URL</label>
        <input type="text" id="newUrl" placeholder="https://example.com/podcast/rss">
      </div>
      <div class="full">
        <button onclick="addPodcastManual()">+ Add Podcast</button>
      </div>
    </div>
    <p class="hint">Tip: Use the Discover tab to search, or paste an RSS URL directly.</p>
  </div>
</main>
</div>

<!-- DISCOVER TAB -->
<div class="page" id="page-discover">
<main style="max-width:960px">
  <div class="card">
    <div class="card-title">Discover Podcasts</div>
    <div class="search-bar">
      <input type="text" id="searchInput" placeholder="Search by title, topic, or host…" onkeydown="if(event.key==='Enter')doSearch()">
      <button onclick="doSearch()">Search</button>
    </div>
    <div class="genre-bar" id="genreBar">
      <button class="genre-btn active" onclick="selectGenre(this,'')">All</button>
      <button class="genre-btn" onclick="selectGenre(this,'comedy')">😂 Comedy</button>
      <button class="genre-btn" onclick="selectGenre(this,'true crime')">🔪 True Crime</button>
      <button class="genre-btn" onclick="selectGenre(this,'news')">📰 News</button>
      <button class="genre-btn" onclick="selectGenre(this,'science')">🔬 Science</button>
      <button class="genre-btn" onclick="selectGenre(this,'history')">🏛 History</button>
      <button class="genre-btn" onclick="selectGenre(this,'technology')">💻 Technology</button>
      <button class="genre-btn" onclick="selectGenre(this,'business')">💼 Business</button>
      <button class="genre-btn" onclick="selectGenre(this,'sport')">⚽ Sport</button>
      <button class="genre-btn" onclick="selectGenre(this,'health')">❤️ Health</button>
      <button class="genre-btn" onclick="selectGenre(this,'music')">🎵 Music</button>
      <button class="genre-btn" onclick="selectGenre(this,'fiction')">📖 Fiction</button>
      <button class="genre-btn" onclick="selectGenre(this,'education')">🎓 Education</button>
    </div>
    <div id="searchResults">
      <div class="searching-spinner">Loading…</div>
    </div>
  </div>
</main>
</div>

<!-- SETTINGS TAB -->
<div class="page" id="page-settings">
<main>
  <div class="card">
    <div class="card-title">Sync Settings</div>
    <div class="toggle-row">
      <div>
        <div class="toggle-label">Auto-sync when iPod plugged in</div>
        <div class="toggle-desc">Detects your iPod every 30 seconds and syncs automatically</div>
      </div>
      <label class="toggle">
        <input type="checkbox" id="autoSync" onchange="saveSettings()">
        <span class="slider"></span>
      </label>
    </div>
    <div class="toggle-row">
      <div style="flex:1;margin-right:16px">
        <div class="toggle-label">Episode deletion</div>
        <div class="toggle-desc" id="deletionDesc"></div>
      </div>
      <select id="deletionMode" onchange="saveDeletion()" style="width:140px;padding:6px 8px;flex-shrink:0">
        <option value="off">Never delete</option>
        <option value="grace">After grace period</option>
        <option value="bookmark">After listening</option>
      </select>
    </div>
    <div class="toggle-row" id="graceDaysRow" style="display:none">
      <div>
        <div class="toggle-label">Grace period (days)</div>
        <div class="toggle-desc">Episodes newer than this will never be auto-deleted</div>
      </div>
      <input type="number" id="graceDays" value="7" min="1" max="90"
        onchange="saveDeletion()" style="width:70px;padding:6px 8px">
    </div>
  </div>
  <div class="card">
    <div class="card-title">Appearance</div>
    <div class="toggle-row">
      <div>
        <div class="toggle-label">Light mode</div>
        <div class="toggle-desc">Switch between dark and light interface</div>
      </div>
      <label class="toggle">
        <input type="checkbox" id="lightModeToggle" onchange="toggleThemeFromCheckbox()">
        <span class="slider"></span>
      </label>
    </div>
    <div class="toggle-row">
      <div>
        <div class="toggle-label">Podcasts per page (Discover)</div>
        <div class="toggle-desc">How many results to show at once when browsing</div>
      </div>
      <select id="perPageSelect" onchange="savePerPage()" style="width:80px;padding:6px 8px">
        <option value="10">10</option>
        <option value="20" selected>20</option>
        <option value="30">30</option>
        <option value="50">50</option>
      </select>
    </div>
  </div>
</main>
</div>

<!-- MODAL -->
<div class="modal-overlay" id="modalOverlay" onclick="closeModal(event)">
  <div class="modal" id="modal">
    <div class="modal-header">
      <div id="modalArt"></div>
      <div class="modal-meta">
        <div class="modal-title" id="modalTitle"></div>
        <div class="modal-author" id="modalAuthor"></div>
        <div class="modal-badges" id="modalBadges"></div>
      </div>
    </div>
    <div class="modal-body">
      <div id="modalRating"></div>
      <div class="modal-desc" id="modalDesc"></div>
      <div class="modal-add-row">
        <div class="field">
          <label>Back-episodes to download</label>
          <input type="number" id="modalBack" value="3" min="1" max="50">
        </div>
        <button id="modalAddBtn" onclick="addFromModal()">+ Add to My Podcasts</button>
      </div>
    </div>
    <div class="modal-footer">
      <button class="secondary" onclick="document.getElementById('modalOverlay').classList.remove('open')">Close</button>
    </div>
  </div>
</div>

<script>
let appState = {};
let currentModal = null;
let addedFeeds = new Set();

// Theme
function getTheme() { return localStorage.getItem('ps_theme') || 'dark'; }
function applyTheme(t) {
  document.documentElement.setAttribute('data-theme', t);
  document.getElementById('themeBtn').textContent = t === 'dark' ? '🌙' : '☀️';
  const cb = document.getElementById('lightModeToggle');
  if (cb) cb.checked = (t === 'light');
  localStorage.setItem('ps_theme', t);
}
function toggleTheme() { applyTheme(getTheme() === 'dark' ? 'light' : 'dark'); }
function toggleThemeFromCheckbox() {
  applyTheme(document.getElementById('lightModeToggle').checked ? 'light' : 'dark');
}
applyTheme(getTheme());

// Tabs
let topPodcastsLoaded = false;
function showTab(name, btn) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('page-' + name).classList.add('active');
  btn.classList.add('active');
  // Load top podcasts the first time the discover tab is opened
  if (name === 'discover' && !topPodcastsLoaded) {
    topPodcastsLoaded = true;
    loadTopPodcasts();
  }
}

// API
async function api(path, method='GET', body=null) {
  const opts = { method, headers: {'Content-Type':'application/json'} };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(path, opts);
  return r.json();
}

// Refresh state
async function refresh() {
  appState = await api('/api/state');
  renderAll();
}

function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function renderAll() {
  const s = appState;
  const dot = document.getElementById('statusDot');
  const txt = document.getElementById('statusText');
  if (s.sync_running) { dot.className='dot syncing'; txt.textContent='Syncing…'; }
  else if (s.ipod_connected) { dot.className='dot connected'; txt.textContent='iPod connected'; }
  else { dot.className='dot'; txt.textContent='iPod not detected'; }

  const pathEl = document.getElementById('ipodPath');
  if (pathEl && document.activeElement !== pathEl) pathEl.value = s.config.ipod_path || '';
  document.getElementById('lastSync').textContent = s.config.last_sync || 'never';
  const as = document.getElementById('autoSync');
  if (as) as.checked = !!s.config.auto_sync;
  renderDeletionUI(s.config.deletion_mode || 'grace', s.config.grace_days || 7);

  const list = document.getElementById('podcastList');
  const pods = s.config.podcasts || [];
  if (pods.length === 0) {
    list.innerHTML = '<div class="empty-state">No podcasts yet — use the Discover tab to find some!</div>';
  } else {
    list.innerHTML = pods.map((p, i) => `
      <div class="podcast-item">
        ${p.artwork
          ? `<img class="pod-art" src="${esc(p.artwork)}" alt="" onerror="this.style.display='none';this.nextSibling.style.display='flex'">`
          : ''}
        <div class="pod-art-placeholder" style="${p.artwork?'display:none':''}">🎙</div>
        <div class="pod-info">
          <div class="pod-name">${esc(p.name)}</div>
          <div class="pod-author">${esc(p.author||'')}</div>
        </div>
        <div class="pod-back">${p.back_episodes} ep</div>
        <button class="secondary" title="Reset sync history for this podcast" onclick="resetOnePodcast(${i})" style="padding:6px 8px;font-size:0.8rem">↺</button>
        <button class="danger" onclick="removePodcast(${i})" style="padding:6px 10px;font-size:0.75rem">✕</button>
      </div>`).join('');
  }

  const sb = document.getElementById('syncBtn');
  if (sb) sb.disabled = s.sync_running;

  const logBox = document.getElementById('logBox');
  if (logBox) {
    logBox.innerHTML = (s.log||[]).map(e =>
      `<div class="log-entry"><span class="log-ts">${esc(e.ts)}</span><span class="log-${e.level}">${esc(e.msg)}</span></div>`
    ).join('');
    logBox.scrollTop = logBox.scrollHeight;
  }
}

async function manualSync() { await api('/api/sync','POST'); setTimeout(refresh,400); }
async function autoDetect() {
  const btn = event.currentTarget;
  btn.textContent = 'Scanning…'; btn.disabled = true;
  try {
    const r = await api('/api/detect','POST');
    if (r.path) {
      document.getElementById('ipodPath').value = r.path;
      await api('/api/config','POST',{ipod_path: r.path});
      refresh();
      alert('Found & saved iPod at: '+r.path);
    } else {
      alert('No Rockbox iPod found.\n\nMake sure:\n• Your iPod is plugged in via USB\n• It appears as a drive on your computer\n• Rockbox is installed (a .rockbox folder exists on it)\n\nYou can also type the path manually and click Save.');
    }
  } finally {
    btn.textContent = 'Auto-Detect'; btn.disabled = false;
  }
}
async function savePath() { await api('/api/config','POST',{ipod_path:document.getElementById('ipodPath').value.trim()}); refresh(); }
async function saveSettings() {
  await api('/api/config', 'POST', { auto_sync: document.getElementById('autoSync').checked });
}

const DELETION_DESCS = {
  off:      'Episodes are never automatically deleted — you manage files manually.',
  grace:    'Deletes after the grace period once there is no active resume bookmark. Good middle-ground.',
  bookmark: 'Only deletes episodes you have actually started playing once they are finished. New unplayed episodes are never touched.',
};

function renderDeletionUI(mode, days) {
  const sel  = document.getElementById('deletionMode');
  const desc = document.getElementById('deletionDesc');
  const row  = document.getElementById('graceDaysRow');
  const inp  = document.getElementById('graceDays');
  if (!sel) return;
  sel.value = mode || 'grace';
  if (desc) desc.textContent = DELETION_DESCS[mode] || '';
  if (row)  row.style.display = (mode === 'grace') ? '' : 'none';
  if (inp)  inp.value = days || 7;
}

async function saveDeletion() {
  const mode = document.getElementById('deletionMode').value;
  const days = parseInt(document.getElementById('graceDays')?.value) || 7;
  await api('/api/config', 'POST', { deletion_mode: mode, grace_days: days });
  renderDeletionUI(mode, days);
}
async function addPodcastManual() {
  const name=document.getElementById('newName').value.trim();
  const url=document.getElementById('newUrl').value.trim();
  const back=parseInt(document.getElementById('newBack').value)||3;
  if(!name||!url){alert('Please enter both a name and RSS URL.');return;}
  await api('/api/podcasts','POST',{name,url,back_episodes:back});
  document.getElementById('newName').value='';
  document.getElementById('newUrl').value='';
  document.getElementById('newBack').value='3';
  refresh();
}
async function removePodcast(i) {
  if (!confirm('Remove this podcast?\n(Files already on your iPod are not deleted)')) return;
  // Also clear sync history for this podcast so it re-downloads if re-added
  const name = appState.config.podcasts[i]?.name;
  if (name) await api('/api/reset-sync-state', 'POST', { name });
  await api('/api/podcasts/' + i, 'DELETE');
  refresh();
}

async function resetOnePodcast(i) {
  const name = appState.config.podcasts[i]?.name;
  if (!name) return;
  if (!confirm(`Reset sync history for "${name}"?\n\nThis means all episodes will re-download on the next sync. Files already on your iPod won't be touched.`)) return;
  await api('/api/reset-sync-state', 'POST', { name });
  log(`Sync history cleared for ${name}`);
  refresh();
}

async function resetAllSyncHistory() {
  if (!confirm('Reset ALL sync history?\n\nEvery episode across all podcasts will re-download on the next sync. Files already on your iPod won\'t be touched.')) return;
  await api('/api/reset-sync-state', 'POST', {});
  refresh();
}

function log(msg) {
  // Local helper to add a client-side note to the visible log
  // (real log entries come from the server via refresh)
  console.log(msg);
}

// ── Discover ────────────────────────────────────────────────────────────────

let currentGenre = '';   // '' = all
let discoverResults = []; // full result set for current view

function getPerPage() { return parseInt(localStorage.getItem('ps_perpage') || '20'); }
function savePerPage() {
  const v = document.getElementById('perPageSelect').value;
  localStorage.setItem('ps_perpage', v);
  // Re-render current results with new limit
  if (discoverResults.length) renderResults(discoverResults, null, currentGenre);
}

function initPerPage() {
  const sel = document.getElementById('perPageSelect');
  if (sel) sel.value = String(getPerPage());
}

function parsePodcasts(items) {
  return (items || [])
    .filter(r => r.feedUrl)
    .map(r => {
      // iTunes uses several possible rating field names for podcasts
      const rawRating = r.averageUserRating ?? r.collectionAverageUserRating ?? null;
      const rawCount  = r.userRatingCount  ?? r.collectionUserRatingCount  ?? 0;
      const rating = rawRating != null ? parseFloat(rawRating.toFixed(1)) : null;
      return {
        name: r.collectionName || '',
        author: r.artistName || '',
        description: '',
        artwork: r.artworkUrl600 || r.artworkUrl100 || '',
        feed_url: r.feedUrl || '',
        genre: r.primaryGenreName || '',
        episode_count: r.trackCount || 0,
        collection_id: r.collectionId || 0,
        rating: rating,
        rating_count: rawCount,
      };
    });
}

async function fetchPodcasts(term, limit) {
  const url = 'https://itunes.apple.com/search?term=' + encodeURIComponent(term)
    + '&media=podcast&limit=' + limit + '&entity=podcast';
  const resp = await fetch(url);
  const data = await resp.json();
  return parsePodcasts(data.results);
}

async function selectGenre(btn, genre) {
  // Update active button
  document.querySelectorAll('.genre-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  currentGenre = genre;
  // Clear search box so it's obvious genre browsing is active
  document.getElementById('searchInput').value = '';
  await loadTopPodcasts();
}

async function loadTopPodcasts() {
  const el = document.getElementById('searchResults');
  el.innerHTML = '<div class="searching-spinner">Loading…</div>';
  try {
    const limit = Math.min(50, getPerPage() + 10); // fetch slightly more than needed
    const term = currentGenre || 'podcast';
    const results = await fetchPodcasts(term, limit);
    discoverResults = results;
    if (!results.length) {
      el.innerHTML = '<div class="empty-state">Could not load podcasts. Check your internet connection.</div>';
      return;
    }
    const label = currentGenre
      ? '🎧 Top in ' + currentGenre.charAt(0).toUpperCase() + currentGenre.slice(1)
      : '🔥 Popular right now';
    renderResults(results, null, label);
  } catch(e) {
    el.innerHTML = '<div class="empty-state">Could not load podcasts. Check your internet connection.</div>';
  }
}

async function doSearch() {
  const q = document.getElementById('searchInput').value.trim();
  if (!q) return;
  // Deselect genre when searching
  document.querySelectorAll('.genre-btn').forEach(b => b.classList.remove('active'));
  document.querySelector('.genre-btn').classList.add('active'); // reset to All
  currentGenre = '';
  const el = document.getElementById('searchResults');
  el.innerHTML = '<div class="searching-spinner">🔍 Searching…</div>';
  try {
    const results = await fetchPodcasts(q, 50);
    discoverResults = results;
    renderResults(results, q, null);
  } catch(e) {
    el.innerHTML = '<div class="empty-state">Search failed — make sure you have an internet connection.</div>';
  }
}

function starBar(rating) {
  if (!rating) return '';
  // Build visual star bar using filled/half/empty unicode stars
  const full  = Math.floor(rating);
  const half  = (rating - full) >= 0.4 ? 1 : 0;
  const empty = 5 - full - half;
  return '★'.repeat(full) + (half ? '⯪' : '') + '☆'.repeat(empty);
}

function buildCard(r, i) {
  const added = addedFeeds.has(r.feed_url);
  const stars = r.rating ? `<div class="card-rating">
    <span class="stars">${starBar(r.rating)}</span>
    <span class="rating-num">${r.rating}</span>
    ${r.rating_count ? `<span class="rating-count">(${r.rating_count.toLocaleString()})</span>` : ''}
  </div>` : '';
  return `<div class="result-card" onclick="openModal(${i})">
    ${r.artwork ? `<img class="result-art" src="${esc(r.artwork)}" alt="" loading="lazy">` : `<div class="result-art-placeholder">🎙</div>`}
    <div class="result-body">
      <div class="result-name">${esc(r.name)}</div>
      <div class="result-author">${esc(r.author)}</div>
      ${r.genre ? `<div class="result-genre">${esc(r.genre)}</div>` : ''}
      ${stars}
    </div>
    <button class="add-btn${added ? ' added' : ''}" onclick="event.stopPropagation();quickAdd(${i},this)" ${added ? 'disabled' : ''}>
      ${added ? '✓ Added' : '+ Add'}
    </button>
  </div>`;
}

function renderResults(results, searchQuery, label) {
  window._sr = results; // full list for modal indexing
  const el = document.getElementById('searchResults');
  const perPage = getPerPage();
  const shown = results.slice(0, perPage);
  const heading = searchQuery
    ? `${results.length} results for “${esc(searchQuery)}”`
    : (label || '');
  el.innerHTML = `
    <div class="section-label" style="margin-bottom:14px">${heading}</div>
    <div class="results-grid">${shown.map((r, i) => buildCard(r, i)).join('')}</div>
    ${results.length > perPage ? `<div style="text-align:center;margin-top:20px;font-family:var(--mono);font-size:0.75rem;color:var(--muted)">Showing ${perPage} of ${results.length} — increase limit in Settings</div>` : ''}`;
}

function renderModalRating(r) {
  if (!r.rating) return '';
  const count = r.rating_count ? ` <span style="color:var(--muted);font-size:0.8rem">(${r.rating_count.toLocaleString()} ratings)</span>` : '';
  return `<div class="modal-rating">
    <span class="modal-stars">${starBar(r.rating)}</span>
    <span class="modal-rating-num">${r.rating}</span>${count}
  </div>`;
}

async function openModal(i) {
  const r = window._sr[i];
  currentModal = r;

  // Render immediately with known data
  document.getElementById('modalArt').innerHTML = r.artwork
    ? `<img class="modal-art" src="${esc(r.artwork)}" alt="">`
    : `<div class="modal-art-placeholder">🎙</div>`;
  document.getElementById('modalTitle').textContent  = r.name;
  document.getElementById('modalAuthor').textContent = r.author;
  document.getElementById('modalDesc').innerHTML = '<em style="color:var(--muted);font-size:0.85rem">Loading description…</em>';
  document.getElementById('modalRating').innerHTML = renderModalRating(r);

  const badges = [];
  if (r.genre)         badges.push(`<span class="badge">${esc(r.genre)}</span>`);
  if (r.episode_count) badges.push(`<span class="badge">${r.episode_count.toLocaleString()} episodes</span>`);
  document.getElementById('modalBadges').innerHTML = badges.join('');

  const btn = document.getElementById('modalAddBtn');
  const added = addedFeeds.has(r.feed_url);
  btn.textContent = added ? '✓ Already Added' : '+ Add to My Podcasts';
  btn.disabled = added;
  document.getElementById('modalBack').value = 3;
  document.getElementById('modalOverlay').classList.add('open');

  // ── Fetch description from RSS feed via CORS proxies ──────────────────────
  // iTunes API never returns descriptions; RSS feed is the only reliable source.
  // We try multiple proxies in sequence until one works.
  (async () => {
    const rssUrl = r.feed_url;

    function extractDesc(xml) {
      const parser = new DOMParser();
      const doc = parser.parseFromString(xml, 'text/xml');
      // Walk common description locations
      const candidates = [
        doc.querySelector('channel > description'),
        doc.querySelector('channel > subtitle'),
        ...Array.from(doc.getElementsByTagNameNS('http://www.itunes.com/dtds/podcast-1.0.dtd', 'summary')),
        ...Array.from(doc.getElementsByTagNameNS('http://www.itunes.com/dtds/podcast-1.0.dtd', 'subtitle')),
      ];
      for (const el of candidates) {
        if (el) {
          const txt = (el.textContent || '').replace(/<[^>]*>/g, '').replace(/\s+/g, ' ').trim();
          if (txt.length > 10) return txt;
        }
      }
      return null;
    }

    // Proxy strategies: each returns raw XML differently
    const proxies = [
      async (url) => {
        const r = await fetch('https://api.allorigins.win/get?url=' + encodeURIComponent(url), { signal: AbortSignal.timeout(6000) });
        const d = await r.json(); return d.contents || '';
      },
      async (url) => {
        const r = await fetch('https://corsproxy.io/?' + encodeURIComponent(url), { signal: AbortSignal.timeout(6000) });
        return await r.text();
      },
      async (url) => {
        const r = await fetch('https://api.rss2json.com/v1/api.json?rss_url=' + encodeURIComponent(url), { signal: AbortSignal.timeout(6000) });
        const d = await r.json();
        // rss2json returns description in feed object
        return d.feed?.description || '';
      },
    ];

    let desc = null;
    for (const proxy of proxies) {
      try {
        const raw = await proxy(rssUrl);
        if (!raw) continue;
        // rss2json gives plain text directly
        if (typeof raw === 'string' && !raw.trim().startsWith('<') && raw.length > 10) {
          desc = raw.replace(/<[^>]*>/g, '').replace(/\s+/g, ' ').trim();
        } else {
          desc = extractDesc(raw);
        }
        if (desc && desc.length > 10) break;
      } catch(e) { continue; }
    }

    const el = document.getElementById('modalDesc');
    if (el) el.textContent = (desc && desc.length > 10) ? desc : 'No description available for this podcast.';
  })();
}

function closeModal(e) {
  if (!e || e.target === document.getElementById('modalOverlay'))
    document.getElementById('modalOverlay').classList.remove('open');
}

async function addFromModal() {
  if (!currentModal) return;
  const back = parseInt(document.getElementById('modalBack').value)||3;
  await api('/api/podcasts','POST',{name:currentModal.name,url:currentModal.feed_url,back_episodes:back,artwork:currentModal.artwork||'',author:currentModal.author||''});
  addedFeeds.add(currentModal.feed_url);
  const btn=document.getElementById('modalAddBtn');
  btn.textContent='✓ Added!'; btn.disabled=true;
  refresh();
  setTimeout(()=>document.getElementById('modalOverlay').classList.remove('open'),700);
}

async function quickAdd(i, btn) {
  const r = window._sr[i];
  await api('/api/podcasts','POST',{name:r.name,url:r.feed_url,back_episodes:3,artwork:r.artwork||'',author:r.author||''});
  addedFeeds.add(r.feed_url);
  btn.textContent='✓ Added'; btn.classList.add('added'); btn.disabled=true;
  refresh();
}

document.addEventListener('keydown', e=>{ if(e.key==='Escape') document.getElementById('modalOverlay').classList.remove('open'); });

async function scanDrives() {
  const btn = document.getElementById('scanBtn');
  const out = document.getElementById('scanResult');
  btn.textContent = '🔎 Scanning…'; btn.disabled = true;
  out.style.display = 'block';
  out.textContent = 'Looking for drives…';
  try {
    const r = await api('/api/scan', 'POST');
    const all = r.all || [];
    const rb  = r.rockbox || [];
    if (rb.length) {
      out.innerHTML = '<span style="color:var(--success)">✓ Found Rockbox device(s):</span><br>' +
        rb.map(p => `<span style="color:var(--accent);cursor:pointer" onclick="pickDrive('${p}')">${p} ← click to use</span>`).join('<br>');
    } else if (all.length) {
      out.innerHTML = '<span style="color:var(--warn)">No Rockbox folder found. Drives visible to Python:</span><br>' +
        all.map(p => `<span>${p}</span>`).join('<br>') +
        '<br><span style="color:var(--muted)">If your iPod is listed above, type its path manually and click Save.</span>';
    } else {
      out.innerHTML = '<span style="color:var(--error)">No drives found at all.<br>Make sure your iPod is plugged in and showing up as a USB drive on your computer.</span>';
    }
  } catch(e) {
    out.textContent = 'Scan failed: ' + e;
  } finally {
    btn.textContent = '🔎 Show all detected drives'; btn.disabled = false;
  }
}

async function pickDrive(path) {
  document.getElementById('ipodPath').value = path;
  await api('/api/config', 'POST', {ipod_path: path});
  refresh();
  document.getElementById('scanResult').style.display = 'none';
}

refresh();
setInterval(refresh, 5000);
initPerPage();
// Pre-load top podcasts in background so they're ready when user opens Discover
setTimeout(() => { topPodcastsLoaded = true; loadTopPodcasts(); }, 1200);
</script>
</body>
</html>
"""

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def add_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def send_json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.add_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.add_cors_headers()
        self.end_headers()

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            return json.loads(self.rfile.read(length))
        return {}

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            body = HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(body))
            self.add_cors_headers()
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/state":
            # Try live auto-detect so UI always reflects reality
            connected = is_ipod_connected()
            if not connected and not config.get("ipod_path","").strip():
                found = detect_ipod_auto()
                if found:
                    config["ipod_path"] = found
                    save_config()
                    connected = True
            self.send_json({
                "config": config,
                "ipod_connected": connected,
                "sync_running": sync_running,
                "log": sync_log[-100:],
            })
        elif self.path.startswith("/api/search"):
            from urllib.parse import urlparse, parse_qs
            qs = parse_qs(urlparse(self.path).query)
            q = qs.get("q", [""])[0]
            if q:
                results = search_podcasts(q)
                self.send_json({"results": results})
            else:
                self.send_json({"results": []})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        body = self.read_body()
        if self.path == "/api/sync":
            if not sync_running:
                threading.Thread(target=run_sync, daemon=True).start()
            self.send_json({"ok": True})
        elif self.path == "/api/detect":
            path = detect_ipod_auto()
            self.send_json({"path": path})
        elif self.path == "/api/scan":
            # Return all mounted drives/volumes for manual selection
            candidates = []
            import platform
            if sys.platform == "darwin":
                vols = Path("/Volumes")
                if vols.exists():
                    candidates = [str(p) for p in vols.iterdir() if p.is_dir()]
            elif sys.platform == "win32":
                import string
                candidates = [f"{d}:\\" for d in string.ascii_uppercase if Path(f"{d}:\\").exists()]
            else:
                for base in [Path("/media"), Path("/mnt"), Path("/run/media")]:
                    if base.exists():
                        for p in base.rglob("*"):
                            if p.is_dir() and p.parent != base or base == Path("/mnt"):
                                candidates.append(str(p))
            rockbox = [c for c in candidates if (Path(c) / ".rockbox").exists()]
            self.send_json({"all": candidates, "rockbox": rockbox})
        elif self.path == "/api/config":
            for k, v in body.items():
                config[k] = v
            save_config()
            self.send_json({"ok": True})
        elif self.path == "/api/reset-sync-state":
            # Clear all or one podcast's sync history so episodes re-download
            podcast_name = body.get("name")  # if set, clear only that podcast
            state = load_sync_state()
            if podcast_name:
                if podcast_name in state:
                    del state[podcast_name]
                    log(f"Sync history cleared for: {podcast_name}")
                else:
                    log(f"No sync history found for: {podcast_name}")
            else:
                state = {}
                log("All sync history cleared — episodes will re-download on next sync")
            save_sync_state(state)
            self.send_json({"ok": True})
        elif self.path == "/api/podcasts":
            config.setdefault("podcasts", []).append({
                "name": body["name"],
                "url": body["url"],
                "back_episodes": body.get("back_episodes", 3),
                "artwork": body.get("artwork", ""),
                "author": body.get("author", ""),
            })
            save_config()
            self.send_json({"ok": True})
        else:
            self.send_response(404)
            self.end_headers()

    def do_DELETE(self):
        if self.path.startswith("/api/podcasts/"):
            try:
                idx = int(self.path.split("/")[-1])
                config["podcasts"].pop(idx)
                save_config()
                self.send_json({"ok": True})
            except (ValueError, IndexError):
                self.send_json({"error": "invalid index"}, 400)
        else:
            self.send_response(404)
            self.end_headers()

# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    load_config()
    log("PodcastSync starting…")

    watcher_thread = threading.Thread(target=ipod_watcher, daemon=True)
    watcher_thread.start()

    server = None
    port = 5000
    for p in range(5000, 5010):
        try:
            # Bind to 0.0.0.0 so both localhost and 127.0.0.1 work.
            # Brave blocks 127.0.0.1 but allows localhost.
            server = HTTPServer(("0.0.0.0", p), Handler)
            port = p
            break
        except OSError:
            continue
    if server is None:
        print("ERROR: Could not bind to any port 5000-5009.")
        sys.exit(1)

    url = f"http://localhost:{port}"
    log(f"Server running at {url}")

    def open_browser_at(u):
        time.sleep(1.2)
        try:
            import webbrowser
            webbrowser.open(u)
        except Exception:
            pass

    threading.Thread(target=open_browser_at, args=(url,), daemon=True).start()

    print("\n" + "="*50)
    print(f"  PodcastSync is running!")
    print(f"  Open: {url}")
    print( "  Press Ctrl+C to stop.")
    print("="*50 + "\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
