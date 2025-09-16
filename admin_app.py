#!/usr/bin/env python3
"""
RFID Cube Admin + Continuous Tech News (extended)
Implements many free/paid news sources:
- RSS (feedparser)
- NewsAPI.org
- GNews (gnews.io)
- Mediastack
- NewsData.io
- TheNewsAPI (thenewsapi.com)
- ContextualWeb / RapidAPI (via RapidAPI)
- Webz.io
- The Guardian Open Platform
- New York Times API
- Newscatcher API
- GDELT 2.0 (basic)
- (CommonCrawl / News Crawl) - placeholder stub
All fetchers normalize title/link/source/published and shorten descriptions to 45 words.
"""

import os
import json
import time
import threading
import random
import subprocess
import re
from pathlib import Path
from typing import List, Dict, Any
import feedparser
import requests
from flask import Flask, request, render_template, redirect, url_for, jsonify, send_from_directory

# === Config & Paths ===
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


# Add some India-specific feeds you can expand
INDIA_FEEDS = [
    "https://gadgets.ndtv.com/rss/news",
    "https://timesofindia.indiatimes.com/rssfeeds/66949542.cms",
    "https://www.digit.in/rss-feed",
    # add more as needed
]

ALLOWED_KEYWORDS = ["AI", "Machine", "ML", "GPT", "Llama", "Claude",
                    "Python", "JavaScript", "Rust", "Go", "TypeScript", "Framework",
                    "Release", "Launch", "Tool", "Open Source", "API", "SDK",
                    "Cloud", "Data", "Security", "Technology", "Industry", "innovation", "model"
                    "Deep Learning", "Neural Network", "Transformers", "ChatGPT", "Autonomous"]

HTTP_TIMEOUT = 10.0

# === API KEYS (from environment) ===
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "").strip()
GNEWS_KEY = os.environ.get("GNEWS_KEY", "").strip()
MEDIASTACK_KEY = os.environ.get("MEDIASTACK_KEY", "").strip()
NEWSDATA_KEY = os.environ.get("NEWSDATA_KEY", "").strip()
THENEWSAPI_KEY = os.environ.get("THENEWSAPI_KEY", "").strip()
RAPIDAPI_KEY = os.environ.get("RAPIDAPI_KEY", "").strip()           # for contextaulweb/other via RapidAPI
RAPIDAPI_HOST = os.environ.get("RAPIDAPI_HOST", "").strip()
WEBZ_KEY = os.environ.get("WEBZ_KEY", "").strip()
GUARDIAN_KEY = os.environ.get("GUARDIAN_KEY", "").strip()
NYTIMES_KEY = os.environ.get("NYTIMES_KEY", "").strip()
NEWSCATCHER_KEY = os.environ.get("NEWSCATCHER_KEY", "").strip()
# GDELT doesn't use a simple key for basic usage
GDELT_ENABLED = True

# === Logging ===
def log_news_error(msg: str):
    try:
        with open(NEWS_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass

# === Utilities ===
def shorten_description(text: str, max_words: int = 45) -> str:
    if not text:
        return ""
    # Remove HTML tags and newlines
    text = re.sub(r'<[^>]+>', '', text)
    text = text.replace('\n', ' ').replace('\r', ' ').strip()
    words = text.split()
    if len(words) > max_words:
        return " ".join(words[:max_words]).rstrip('.,;:') + "..."
    return text

def normalize_article(title: str, description: str, link: str, source: str, published: str) -> Dict[str, Any]:
    title = (title or "").strip()
    description = shorten_description(description or title, 45)
    link = link or ""
    source = source or ""
    published = published or ""
    return {"title": title, "description": description, "link": link, "source": source, "published": published}

def dedupe_and_filter(items: List[Dict[str, Any]], max_items: int = 80) -> List[Dict[str, Any]]:
    out = []
    seen_titles = set()
    seen_links = set()
    random.shuffle(items)  # mix sources
    for it in items:
        t = (it.get("title") or "").strip()
        l = (it.get("link") or "").strip()
        if not t:
            continue
        # keyword filter
        if not any(k.lower() in t.lower() for k in ALLOWED_KEYWORDS):
            # keep some non-matching but recent tech-ish? currently skip
            continue
        if t in seen_titles or (l and l in seen_links):
            continue
        seen_titles.add(t)
        if l:
            seen_links.add(l)
        out.append(it)
        if len(out) >= max_items:
            break
    return out

# === Fetchers ===

def fetch_from_rss(feeds=DEFAULT_FEEDS + INDIA_FEEDS, limit_per_feed: int = 6) -> List[Dict[str, Any]]:
    items = []
    for url in feeds:
        try:
            d = feedparser.parse(url)
            source = (d.feed.get("title") or url).strip()
            for e in d.entries[:limit_per_feed]:
                title = e.get("title", "").strip()
                desc = e.get("description", "") or e.get("summary", "") or ""
                link = e.get("link", "") or ""
                published = e.get("published", "") or e.get("updated", "") or ""
                items.append(normalize_article(f"[RSS {source}] {title}", desc, link, source, published))
        except Exception as ex:
            log_news_error(f"RSS fetch error {url}: {ex}")
    return dedupe_and_filter(items, max_items=80)

def fetch_from_newsapi(api_key: str, page_size: int = 20) -> List[Dict[str, Any]]:
    if not api_key:
        return []
    url = "https://newsapi.org/v2/top-headlines"
    params = {"category": "technology", "pageSize": page_size, "language": "en", "apiKey": api_key}
    try:
        r = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        articles = r.json().get("articles", [])
        out = []
        for a in articles:
            t = a.get("title", "").strip()
            desc = a.get("description", "") or a.get("content", "")
            out.append(normalize_article(f"[NewsAPI] {t}", desc, a.get("url", ""), a.get("source", {}).get("name", "NewsAPI"), a.get("publishedAt", "")))
        return dedupe_and_filter(out, max_items=60)
    except Exception as e:
        log_news_error(f"NewsAPI fetch error: {e}")
        return []

def fetch_from_gnews(api_key: str, max_items: int = 20) -> List[Dict[str, Any]]:
    if not api_key:
        return []
    url = "https://gnews.io/api/v4/top-headlines"
    params = {"topic": "technology", "lang": "en", "max": max_items, "token": api_key}
    try:
        r = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        articles = r.json().get("articles", [])
        out = []
        for a in articles:
            t = a.get("title", "").strip()
            desc = a.get("description", "") or a.get("content", "")
            out.append(normalize_article(f"[GNews] {t}", desc, a.get("url", ""), a.get("source", {}).get("name", "GNews"), a.get("publishedAt", "")))
        return dedupe_and_filter(out, max_items=60)
    except Exception as e:
        log_news_error(f"GNews fetch error: {e}")
        return []

def fetch_from_mediastack(api_key: str, page_size: int = 20) -> List[Dict[str, Any]]:
    if not api_key:
        return []
    url = "http://api.mediastack.com/v1/news"
    params = {"access_key": api_key, "languages": "en", "countries": "us,in", "categories": "technology", "limit": page_size}
    try:
        r = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        news_list = data.get("data", [])
        out = []
        for a in news_list:
            out.append(normalize_article(f"[Mediastack] {a.get('title','')}", a.get('description',''), a.get('url',''), a.get('source','Mediastack'), a.get('published_at','')))
        return dedupe_and_filter(out, max_items=60)
    except Exception as e:
        log_news_error(f"Mediastack fetch error: {e}")
        return []

def fetch_from_newsdata(api_key: str, max_items: int = 20) -> List[Dict[str, Any]]:
    if not api_key:
        return []
    url = "https://newsdata.io/api/1/news"
    params = {"apikey": api_key, "language": "en", "category": "technology", "page": 1}
    try:
        r = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        articles = r.json().get("results", [])
        out = []
        for a in articles[:max_items]:
            out.append(normalize_article(f"[NewsData] {a.get('title','')}", a.get('description','') or a.get('content',''), a.get('link',''), a.get('source_id','NewsData'), a.get('pubDate','')))
        return dedupe_and_filter(out, max_items=60)
    except Exception as e:
        log_news_error(f"NewsData fetch error: {e}")
        return []

def fetch_from_thenewsapi(api_key: str, max_items: int = 20) -> List[Dict[str, Any]]:
    # thenewsapi.com example (formats may vary)
    if not api_key:
        return []
    url = "https://api.thenewsapi.com/v1/news/top"
    params = {"api_token": api_key, "locale": "en-US", "limit": max_items}
    try:
        r = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        articles = r.json().get("data", [])
        out = []
        for a in articles[:max_items]:
            out.append(normalize_article(f"[TheNewsAPI] {a.get('title','')}", a.get('description','') or a.get('snippet',''), a.get('url',''), a.get('source','TheNewsAPI'), a.get('published_at','')))
        return dedupe_and_filter(out, max_items=60)
    except Exception as e:
        log_news_error(f"TheNewsAPI fetch error: {e}")
        return []

def fetch_from_contextualweb_rapidapi(rapidapi_key: str, rapidapi_host: str, max_items: int = 20) -> List[Dict[str, Any]]:
    # ContextualWeb via RapidAPI (example)
    if not rapidapi_key or not rapidapi_host:
        return []
    url = "https://contextualwebsearch-websearch-v1.p.rapidapi.com/api/search/NewsSearchAPI"
    headers = {"x-rapidapi-key": rapidapi_key, "x-rapidapi-host": rapidapi_host}
    params = {"q": "technology OR AI OR machine learning", "pageNumber": "1", "pageSize": str(max_items), "autoCorrect": "true"}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        articles = data.get("value", []) or data.get("articles", []) or []
        out = []
        for a in articles[:max_items]:
            title = a.get("title") or a.get("name") or ""
            desc = a.get("description") or a.get("snippet") or ""
            link = a.get("url") or a.get("urlToImage") or ""
            src = a.get("provider", {}).get("name", "ContextualWeb")
            out.append(normalize_article(f"[ContextualWeb] {title}", desc, link, src, a.get("datePublished", "")))
        return dedupe_and_filter(out, max_items=60)
    except Exception as e:
        log_news_error(f"ContextualWeb (RapidAPI) fetch error: {e}")
        return []

def fetch_from_webz(webz_key: str, max_items: int = 20) -> List[Dict[str, Any]]:
    # webz.io (requires account and key) - example search endpoint
    if not webz_key:
        return []
    url = "https://api.webz.io/v1/news"
    params = {"query": "technology OR AI OR machine learning", "size": max_items, "source": "news", "apikey": webz_key}
    try:
        r = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        hits = data.get("hits", [])
        out = []
        for h in hits[:max_items]:
            out.append(normalize_article(f"[Webz] {h.get('title','')}", h.get('text',''), h.get('url',''), h.get('source','Webz'), h.get('publishedAt','')))
        return dedupe_and_filter(out, max_items=60)
    except Exception as e:
        log_news_error(f"Webz fetch error: {e}")
        return []

def fetch_from_guardian(api_key: str, max_items: int = 20) -> List[Dict[str, Any]]:
    if not api_key:
        return []
    url = "https://content.guardianapis.com/search"
    params = {"api-key": api_key, "section": "technology", "show-fields": "trailText,headline,short-url", "page-size": max_items}
    try:
        r = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        results = r.json().get("response", {}).get("results", [])
        out = []
        for rj in results:
            title = rj.get("webTitle", "")
            desc = (rj.get("fields") or {}).get("trailText", "")
            link = rj.get("webUrl", "")
            out.append(normalize_article(f"[Guardian] {title}", desc, link, "The Guardian", rj.get("webPublicationDate", "")))
        return dedupe_and_filter(out, max_items=60)
    except Exception as e:
        log_news_error(f"Guardian fetch error: {e}")
        return []

def fetch_from_nytimes(api_key: str, max_items: int = 20) -> List[Dict[str, Any]]:
    if not api_key:
        return []
    url = "https://api.nytimes.com/svc/topstories/v2/technology.json"
    params = {"api-key": api_key}
    try:
        r = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        results = r.json().get("results", [])
        out = []
        for rj in results[:max_items]:
            title = rj.get("title", "")
            desc = rj.get("abstract", "")
            link = rj.get("url", "")
            out.append(normalize_article(f"[NYTimes] {title}", desc, link, "NYTimes", rj.get("published_date", "")))
        return dedupe_and_filter(out, max_items=60)
    except Exception as e:
        log_news_error(f"NYTimes fetch error: {e}")
        return []

def fetch_from_newscatcher(api_key: str, max_items: int = 20) -> List[Dict[str, Any]]:
    if not api_key:
        return []
    url = "https://api.newscatcherapi.com/v2/latest_headlines"
    headers = {"x-api-key": api_key}
    params = {"topic": "technology", "lang": "en", "page_size": max_items}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        articles = r.json().get("articles", [])
        out = []
        for a in articles[:max_items]:
            out.append(normalize_article(f"[Newscatcher] {a.get('title','')}", a.get('summary','') or a.get('excerpt',''), a.get('link',''), a.get('clean_url','Newscatcher'), a.get('published_date','')))
        return dedupe_and_filter(out, max_items=60)
    except Exception as e:
        log_news_error(f"Newscatcher fetch error: {e}")
        return []

def fetch_from_gdelt(max_items: int = 30) -> List[Dict[str, Any]]:
    # Basic GDELT pull: GDELT 2.0 has "events" and "mentions" datasets; for news, the "GDELT 2.0 Global Knowledge Graph" or Mentions feed is used.
    # Here we use a simple GDELT JSON query for recent mentions with "technology" keyword (best-effort).
    try:
        url = "https://api.gdeltproject.org/api/v2/doc/doc"
        params = {"query": "technology OR AI OR machine learning", "mode": "artlist", "format": "json"}
        r = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        docs = r.json().get("articles", []) or r.json().get("docs", [])
        out = []
        for d in docs[:max_items]:
            title = d.get("title") or d.get("seendocumenttitle") or ""
            desc = d.get("description") or d.get("summary") or ""
            link = d.get("url") or d.get("domain") or ""
            out.append(normalize_article(f"[GDELT] {title}", desc, link, d.get("source", "GDELT"), d.get("seendate", "")))
        return dedupe_and_filter(out, max_items=60)
    except Exception as e:
        log_news_error(f"GDELT fetch error: {e}")
        return []

def fetch_from_commoncrawl_stub(max_items: int = 0) -> List[Dict[str, Any]]:
    # CommonCrawl / News Crawl requires specialized usage (index harvesting, WARC parsing).
    # Provide a stub that returns [] and logs a note so you can implement a custom crawler if needed.
    log_news_error("CommonCrawl/NewsCrawl fetcher called but is not implemented (requires custom index/WARC handling).")
    return []

# === Aggregator ===
def fetch_and_cache_all() -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []

    # 1) RSS (fast, primary)
    try:
        items.extend(fetch_from_rss())
    except Exception as e:
        log_news_error(f"Error fetching RSS combined: {e}")

    # 2) Priority API fetchers (if keys present) - add them to items list
    try:
        items.extend(fetch_from_newsapi(NEWSAPI_KEY))
    except Exception as e:
        log_news_error(f"Error fetching NewsAPI: {e}")

    try:
        items.extend(fetch_from_gnews(GNEWS_KEY))
    except Exception as e:
        log_news_error(f"Error fetching GNews: {e}")

    try:
        items.extend(fetch_from_mediastack(MEDIASTACK_KEY))
    except Exception as e:
        log_news_error(f"Error fetching Mediastack: {e}")

    try:
        items.extend(fetch_from_newsdata(NEWSDATA_KEY))
    except Exception as e:
        log_news_error(f"Error fetching NewsData: {e}")

    try:
        items.extend(fetch_from_thenewsapi(THENEWSAPI_KEY))
    except Exception as e:
        log_news_error(f"Error fetching TheNewsAPI: {e}")

    try:
        items.extend(fetch_from_contextualweb_rapidapi(RAPIDAPI_KEY, RAPIDAPI_HOST))
    except Exception as e:
        log_news_error(f"Error fetching ContextualWeb: {e}")

    try:
        items.extend(fetch_from_webz(WEBZ_KEY))
    except Exception as e:
        log_news_error(f"Error fetching Webz: {e}")

    try:
        items.extend(fetch_from_guardian(GUARDIAN_KEY))
    except Exception as e:
        log_news_error(f"Error fetching Guardian: {e}")

    try:
        items.extend(fetch_from_nytimes(NYTIMES_KEY))
    except Exception as e:
        log_news_error(f"Error fetching NYTimes: {e}")

    try:
        items.extend(fetch_from_newscatcher(NEWSCATCHER_KEY))
    except Exception as e:
        log_news_error(f"Error fetching Newscatcher: {e}")

    try:
        if GDELT_ENABLED:
            items.extend(fetch_from_gdelt())
    except Exception as e:
        log_news_error(f"Error fetching GDELT: {e}")

    # CommonCrawl is left as a stub; uncomment if/when implemented
    try:
        items.extend(fetch_from_commoncrawl_stub())
    except Exception as e:
        log_news_error(f"Error fetching CommonCrawl stub: {e}")

    # Normalize and dedupe + final filter
    combined = dedupe_and_filter(items, max_items=120)

    if not combined:
        combined = [{"title": "Waiting for tech news...", "description": "Loading the latest technology news and updates...", "link": "", "source": "System", "published": ""}]

    cache = {"generated": int(time.time()), "items": combined}
    try:
        NEWS_CACHE.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        log_news_error(f"Cache write error: {e}")
    return cache

# === Background fetch thread ===
def news_background_loop(interval_sec: int = 300):
    # Fetch immediately on startup
    try:
        fetch_and_cache_all()
    except Exception as e:
        log_news_error(f"Initial fetch error: {e}")
    while True:
        try:
            time.sleep(interval_sec)
            fetch_and_cache_all()
        except Exception as e:
            log_news_error(f"Background loop error: {e}")

threading.Thread(target=news_background_loop, args=(300,), daemon=True).start()

# === VLC helper ===
def play_vlc(path: str, loop: bool = True):
    args = [
        "cvlc", "--quiet", "--no-osd", "--no-video-title-show", "--intf", "dummy",
        "--fullscreen", "--video-filter=transform", "--transform-type=270",
        "--aspect-ratio=9:16", "--autoscale", str(path)
    ]
    if loop:
        args.insert(1, "--loop")
    return subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)

# === Map helpers ===
def load_map():
    return json.loads(MAP_FILE.read_text(encoding="utf-8")) if MAP_FILE.exists() else {}

def save_map(d):
    MAP_FILE.write_text(json.dumps(d, indent=2), encoding="utf-8")

# === Rotation helpers ===
def get_rotation_index() -> int:
    if STATE_FILE.exists():
        try:
            return int(json.loads(STATE_FILE.read_text(encoding="utf-8")).get("index", 0))
        except:
            return 0
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

# === Flask routes ===
@app.route("/")
def index():
    video_map = load_map()
    files = sorted([f.name for f in VIDEO_DIR.iterdir() if f.is_file()])
    return render_template("index.html", video_map=video_map, files=files)

@app.route("/upload", methods=["POST"])
def upload():
    f = request.files.get("file")
    if f and f.filename:
        f.save(str(VIDEO_DIR / f.filename))
    return redirect(url_for("index"))

@app.route("/map", methods=["POST"])
def map_uid():
    uid = request.form.get("uid", "").strip().upper()
    fname = request.form.get("file", "").strip()
    if uid and fname:
        vm = load_map(); vm[uid] = fname; save_map(vm)
    return redirect(url_for("index"))

@app.route("/delete/<uid>")
def delete(uid):
    vm = load_map()
    if uid in vm:
        del vm[uid]; save_map(vm)
    return redirect(url_for("index"))

@app.route("/api/news")
def api_news():
    if NEWS_CACHE.exists():
        try:
            data = json.loads(NEWS_CACHE.read_text(encoding="utf-8"))
            return jsonify(data)
        except Exception as e:
            log_news_error(f"API news error: {e}")
    return jsonify({"generated": 0, "items": [{"title": "No news available", "description": "", "link": "", "source": "System", "published": ""}]})

@app.route("/idle")
def idle():
    try:
        news = json.loads(NEWS_CACHE.read_text(encoding="utf-8")) if NEWS_CACHE.exists() else {"items": [{"title": "Waiting for tech news...", "description": "", "link": "", "source": "System", "published": ""}]}
        items = news.get("items") or [{"title": "Waiting for tech news...", "description": "", "link": "", "source": "System", "published": ""}]
        total = len(items)
        idx = get_rotation_index()
        headline = items[idx % total]
        news_out = {"generated": news.get("generated", 0), "items": [headline]}
        increment_rotation_index(total)
    except Exception as e:
        log_news_error(f"Idle load error: {e}")
        news_out = {"generated": 0, "items": [{"title": "Error loading headlines", "description": "", "link": "", "source": "System", "published": ""}]}
    return render_template("idle.html", news=news_out, rotate_ms=30000)

@app.route("/videos/<path:filename>")
def server_video(filename):
    return send_from_directory(VIDEO_DIR, filename)

@app.route("/api/map")
def api_map():
    return jsonify(load_map())

# === Run server ===
if __name__ == "__main__":
    print(f"[INFO] Starting Alliance RFID Admin on port 5969")
    print(f"[INFO] Video directory: {VIDEO_DIR}")
    print(f"[INFO] Templates directory: {BASE_DIR / 'templates'}")
    app.run(host="0.0.0.0", port=5969, debug=False)
