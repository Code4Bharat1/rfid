#!/usr/bin/env python3
"""
RFID Cube Admin + Continuous Tech News

Features:
- Upload videos and map RFID UIDs
- Idle page shows tech news headlines continuously
- RSS primary, NewsAPI / GNews fallback
- Persistent rotation index (STATE_FILE)
- VLC plays videos in detached, silent mode (no terminal flash)
"""

import os, json, time, threading, random, subprocess
from pathlib import Path
from typing import List, Dict, Any
import feedparser
import requests
from flask import Flask, request, render_template, redirect, url_for, jsonify

# === Config ===
BASE_DIR = Path(__file__).resolve().parent
VIDEO_DIR = BASE_DIR / "videos"
MAP_FILE = BASE_DIR / "video_map.json"
NEWS_CACHE = BASE_DIR / "news_cache.json"
STATE_FILE = BASE_DIR / "news_state.json"
NEWS_LOG = BASE_DIR / "news.log"

VIDEO_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, template_folder=str(BASE_DIR / "templates"))

# RSS feeds (primary)
DEFAULT_FEEDS = [
    "https://openai.com/blog/rss",
    "https://github.blog/changelog/feed/",
    "https://feeds.feedburner.com/PythonInsider",
    "https://feed.infoq.com/ai-ml/",
    "https://hnrss.org/frontpage?q=ai+OR+framework+OR+release+OR+open+source",
    "https://arxiv.org/rss/cs.AI",
    "https://venturebeat.com/category/ai/feed/",
    "https://www.theverge.com/rss/index.xml",
    "https://www.zdnet.com/news/rss.xml",
    "https://www.techradar.com/rss"
]

ALLOWED_KEYWORDS = [
    "AI","Machine","ML","GPT","Llama","Claude",
    "Python","JavaScript","Rust","Go","TypeScript","Framework",
    "Release","Launch","Tool","Open Source","API","SDK",
    "Cloud","Data","Security","Technology","Industry"
]

HTTP_TIMEOUT = 8.0
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "").strip()
GNEWS_KEY = os.environ.get("GNEWS_KEY", "").strip()

# === Logging ===
def log_news_error(msg):
    try:
        with open(NEWS_LOG, "a") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except:
        pass

# === Rotation helpers ===
def get_rotation_index() -> int:
    if STATE_FILE.exists():
        try:
            return int(json.loads(STATE_FILE.read_text(encoding="utf-8")).get("index",0))
        except: return 0
    return 0

def set_rotation_index(idx: int):
    try:
        STATE_FILE.write_text(json.dumps({"index": int(idx)}), encoding="utf-8")
    except Exception as e:
        log_news_error(f"Failed to write state file: {e}")

def increment_rotation_index(total: int):
    if total <= 0:
        set_rotation_index(0)
        return
    idx = (get_rotation_index() + 1) % total
    set_rotation_index(idx)

# === News fetch helpers ===
def filter_and_dedupe(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out, seen = [], set()
    for it in items:
        title = it.get("title","").strip()
        if not title: continue
        if not any(k.lower() in title.lower() for k in ALLOWED_KEYWORDS):
            continue
        if title in seen: continue
        seen.add(title)
        out.append(it)
    return out

def fetch_from_rss(feeds=DEFAULT_FEEDS, limit_per_feed=6) -> List[Dict[str, Any]]:
    items = []
    for url in feeds:
        try:
            d = feedparser.parse(url)
            source = (d.feed.get("title") or url).strip()
            for e in d.entries[:limit_per_feed]:
                items.append({
                    "title": f"[{source}] {e.get('title','').strip()}",
                    "link": e.get("link",""),
                    "source": source,
                    "published": e.get("published","") or e.get("updated","")
                })
        except Exception as ex:
            log_news_error(f"RSS fetch error {url}: {ex}")
    return filter_and_dedupe(items)

def fetch_from_newsapi(api_key: str, page_size: int = 20) -> List[Dict[str, Any]]:
    if not api_key: return []
    url = "https://newsapi.org/v2/top-headlines"
    params = {"category":"technology","pageSize":page_size,"language":"en","apiKey":api_key}
    try:
        r = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        articles = r.json().get("articles",[])
        out = []
        for a in articles:
            t = a.get("title","").strip()
            if not t: continue
            out.append({"title": f"[NewsAPI] {t}", "link":a.get("url",""), "source":a.get("source",{}).get("name","NewsAPI"), "published":a.get("publishedAt","")})
        return filter_and_dedupe(out)
    except Exception as e:
        log_news_error(f"NewsAPI fetch error: {e}")
        return []

def fetch_from_gnews(api_key: str, max_items:int=20) -> List[Dict[str, Any]]:
    if not api_key: return []
    url = "https://gnews.io/api/v4/top-headlines"
    params = {"topic":"technology","lang":"en","max":max_items,"token":api_key}
    try:
        r = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        articles = r.json().get("articles",[])
        out = []
        for a in articles:
            t = a.get("title","").strip()
            if not t: continue
            out.append({"title": f"[GNews] {t}", "link":a.get("url",""), "source":a.get("source",{}).get("name","GNews"), "published":a.get("publishedAt","")})
        return filter_and_dedupe(out)
    except Exception as e:
        log_news_error(f"GNews fetch error: {e}")
        return []

def fetch_and_cache_all() -> Dict[str, Any]:
    items = fetch_from_rss()
    if not items and NEWSAPI_KEY:
        items = fetch_from_newsapi(NEWSAPI_KEY)
    if not items and GNEWS_KEY:
        items = fetch_from_gnews(GNEWS_KEY)
    if not items:
        items = [{"title":"Waiting for tech news...","link":"","source":"System","published":""}]
    cache = {"generated":int(time.time()), "items":items}
    try:
        NEWS_CACHE.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    except Exception as e:
        log_news_error(f"Cache write error: {e}")
    return cache

# Background fetch thread
def news_background_loop(interval_sec=300):
    while True:
        try:
            fetch_and_cache_all()
        except Exception as e:
            log_news_error(f"Background loop error: {e}")
        time.sleep(interval_sec)

threading.Thread(target=news_background_loop, args=(300,), daemon=True).start()

# === VLC helper ===
def play_vlc(path:str, loop=True):
    args = [
        "cvlc","--quiet","--no-osd","--no-video-title-show","--intf","dummy",
        "--fullscreen","--video-filter=transform","--transform-type=270",
        "--aspect-ratio=9:16","--autoscale",str(path)
    ]
    if loop: args.insert(1,"--loop")
    return subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)

# === Map helpers ===
def load_map(): return json.loads(MAP_FILE.read_text(encoding="utf-8")) if MAP_FILE.exists() else {}
def save_map(d): MAP_FILE.write_text(json.dumps(d, indent=2), encoding="utf-8")

# === Flask routes ===
@app.route("/")
def index():
    video_map = load_map()
    files = sorted([f.name for f in VIDEO_DIR.iterdir() if f.is_file()])
    return render_template("index.html", video_map=video_map, files=files)

@app.route("/upload", methods=["POST"])
def upload():
    f = request.files.get("file")
    if f:
        f.save(str(VIDEO_DIR / f.filename))
    return redirect(url_for("index"))

@app.route("/map", methods=["POST"])
def map_uid():
    uid = request.form.get("uid","").strip().upper()
    fname = request.form.get("file","").strip()
    if uid and fname:
        vm = load_map(); vm[uid]=fname; save_map(vm)
    return redirect(url_for("index"))

@app.route("/delete/<uid>")
def delete(uid):
    vm = load_map()
    if uid in vm: del vm[uid]; save_map(vm)
    return redirect(url_for("index"))

@app.route("/api/news")
def api_news():
    if NEWS_CACHE.exists():
        try: return jsonify(json.loads(NEWS_CACHE.read_text(encoding="utf-8")))
        except: pass
    return jsonify({"generated":0,"items":[{"title":"No news available"}]})

@app.route("/idle")
def idle():
    try:
        news = json.loads(NEWS_CACHE.read_text(encoding="utf-8")) if NEWS_CACHE.exists() else {"items":[{"title":"Waiting for tech news..."}]}
        items = news.get("items") or [{"title":"Waiting for tech news..."}]
        total = len(items)
        idx = get_rotation_index()
        headline = items[idx % total]
        news_out = {"generated": news.get("generated",0), "items":[headline]}
        increment_rotation_index(total)
    except Exception as e:
        log_news_error(f"Idle load error: {e}")
        news_out = {"generated":0,"items":[{"title":"Error loading headlines"}]}
    return render_template("idle.html", news=news_out, rotate_ms=30000)

if __name__=="__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)