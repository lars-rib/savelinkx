import logging
import os
import re
import threading
import time
from datetime import date
from urllib.parse import urlparse

from flask import Flask, Response, after_this_request, jsonify, redirect, render_template, request, send_file
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import yt_dlp

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", os.urandom(24))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

METADATA_CACHE_TTL_SECONDS = int(os.getenv("METADATA_CACHE_TTL_SECONDS", "180"))
metadata_cache = {}

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["120 per hour"],
    storage_uri="memory://",
)

MONTHS_EN = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December",
}

TWITTER_STATUS_PATTERNS = (
    re.compile(r"^/(?:[A-Za-z0-9_]+)/status/(\d+)(?:/.*)?$", re.IGNORECASE),
    re.compile(r"^/i/status/(\d+)(?:/.*)?$", re.IGNORECASE),
    re.compile(r"^/i/web/status/(\d+)(?:/.*)?$", re.IGNORECASE),
)

TWITTER_HOSTS = {
    "x.com", "www.x.com",
    "twitter.com", "www.twitter.com",
    "mobile.twitter.com",
}

# Canonical desktop/app links carry a numeric video id; /t/<code> and the
# vm./vt. short hosts only carry an opaque code that yt-dlp resolves via redirect.
TIKTOK_STATUS_PATTERNS = (
    re.compile(r"^/@[\w.\-]+/video/(\d+)(?:/.*)?$", re.IGNORECASE),
    re.compile(r"^/@[\w.\-]+/photo/(\d+)(?:/.*)?$", re.IGNORECASE),
    re.compile(r"^/v/(\d+)(?:\.html)?/?$", re.IGNORECASE),
    re.compile(r"^/t/([A-Za-z0-9]+)/?$", re.IGNORECASE),
)

TIKTOK_HOSTS = {
    "tiktok.com", "www.tiktok.com", "m.tiktok.com",
}

# Short-link hosts: any non-empty alphanumeric path code is accepted; yt-dlp
# follows the redirect to the canonical video URL.
TIKTOK_SHORT_HOSTS = {
    "vm.tiktok.com", "vt.tiktok.com",
}
TIKTOK_SHORT_CODE_PATTERN = re.compile(r"^/([A-Za-z0-9]+)/?$")

# Posts (/p/), Reels (/reel/, /reels/) and IGTV (/tv/), optionally prefixed by
# the author's username. Stories are intentionally omitted: they require login.
INSTAGRAM_STATUS_PATTERNS = (
    re.compile(r"^/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)/?$", re.IGNORECASE),
    re.compile(r"^/[A-Za-z0-9._]+/(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)/?$", re.IGNORECASE),
    re.compile(r"^/stories/[A-Za-z0-9._]+/(\d+)/?$", re.IGNORECASE),
)

INSTAGRAM_HOSTS = {
    "instagram.com", "www.instagram.com", "m.instagram.com",
}

YOUTUBE_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "youtu.be", "www.youtu.be",
}

YOUTUBE_SHORT_PATTERN = re.compile(r"^/([A-Za-z0-9_-]{11})(?:/.*)?$")

FACEBOOK_HOSTS = {
    "facebook.com", "www.facebook.com", "m.facebook.com",
    "fb.watch", "www.fb.watch",
    "fb.com", "www.fb.com",
}


def normalize_and_validate_facebook_url(raw_url):
    url = (raw_url or "").strip()
    if not url:
        return None, "No URL provided"

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None, "Please enter a valid URL starting with http:// or https://"

    hostname = (parsed.hostname or "").lower()
    if hostname not in FACEBOOK_HOSTS:
        return None, "Please enter a valid Facebook URL."

    path = parsed.path or ""
    query = parsed.query or ""
    path_lower = path.lower()

    valid = (
        "/videos/" in path_lower or
        "/video" in path_lower or
        "/watch" in path_lower or
        "/reel/" in path_lower or
        "/stories/" in path_lower or
        "/share/v/" in path_lower or
        "/share/r/" in path_lower or
        "/posts/" in path_lower or
        "v=" in query
    )

    if not valid:
        return None, "Please enter a valid Facebook video or story URL. Paste a link containing /videos/, /reel/, /watch/, or /stories/."

    normalized = f"https://www.facebook.com{path}"
    if query:
        normalized = f"{normalized}?{query}"
    return normalized, None


def normalize_and_validate_youtube_url(raw_url):
    url = (raw_url or "").strip()
    if not url:
        return None, "No URL provided"

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None, "Please enter a valid URL starting with http:// or https://"

    hostname = (parsed.hostname or "").lower()
    path = parsed.path or ""
    query = parsed.query or ""

    if hostname in ("youtu.be", "www.youtu.be"):
        m = YOUTUBE_SHORT_PATTERN.match(path)
        if not m:
            return None, "Please enter a valid YouTube link."
        normalized = f"https://www.youtube.com/watch?v={m.group(1)}"
        return normalized, None

    if hostname in YOUTUBE_HOSTS:
        has_v = "v=" in query
        has_list = "list=" in query
        path_lower = path.lower()
        valid = (
            path_lower.startswith("/watch") or
            path_lower.startswith("/shorts/") or
            path_lower.startswith("/playlist") or
            path_lower.startswith("/embed/") or
            path_lower.startswith("/live/") or
            path_lower.startswith("/@") or
            path_lower.startswith("/channel/") or
            path_lower.startswith("/c/")
        )
        if not valid and not has_v and not has_list:
            return None, "Please enter a valid YouTube video or playlist URL."

        normalized = f"https://www.youtube.com{path}"
        if query:
            normalized = f"{normalized}?{query}"
        return normalized, None

    return None, "Please enter a valid YouTube URL."


@app.before_request
def log_request_started():
    request._started_at = time.time()


@app.after_request
def log_request_finished(response):
    started_at = getattr(request, "_started_at", time.time())
    elapsed_ms = int((time.time() - started_at) * 1000)
    logger.info("%s %s -> %s (%sms)", request.method, request.path, response.status_code, elapsed_ms)
    return response


def get_updated_label():
    today = date.today()
    return f"Updated {MONTHS_EN[today.month]} {today.year}"


def get_site_base_url():
    env_url = os.getenv("SITE_URL", "").strip().rstrip("/")
    if env_url:
        return env_url
    return "https://www.savelinkx.com"


COOKIE_ENV_BY_PLATFORM = {
    "twitter": "TWITTER_COOKIES_FILE",
    "tiktok": "TIKTOK_COOKIES_FILE",
    "instagram": "INSTAGRAM_COOKIES_FILE",
    "youtube": "YOUTUBE_COOKIES_FILE",
    "facebook": "FACEBOOK_COOKIES_FILE",
}


def base_ydl_opts(platform=None):
    opts = {"quiet": True}
    cookie_env = COOKIE_ENV_BY_PLATFORM.get(platform, "TWITTER_COOKIES_FILE")
    cookie_file = os.getenv(cookie_env, "").strip()
    if cookie_file and os.path.exists(cookie_file):
        opts["cookiefile"] = cookie_file
    return opts


def sanitize_filename(name):
    return re.sub(r'[\/*?:"<>|]', "_", name)


def normalize_and_validate_tweet_url(raw_url):
    url = (raw_url or "").strip()
    if not url:
        return None, "No URL provided"

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None, "Please enter a valid URL starting with http:// or https://"

    hostname = (parsed.hostname or "").lower()
    if hostname not in TWITTER_HOSTS:
        return None, "Please enter a valid Twitter/X post URL."

    path = parsed.path or ""
    if not any(pattern.match(path) for pattern in TWITTER_STATUS_PATTERNS):
        return None, "Please paste a direct post URL like https://x.com/user/status/123456789"

    normalized = f"https://{hostname.replace('www.', '')}{parsed.path}"
    if parsed.query:
        normalized = f"{normalized}?{parsed.query}"
    return normalized, None


def normalize_and_validate_tiktok_url(raw_url):
    url = (raw_url or "").strip()
    if not url:
        return None, "No URL provided"

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None, "Please enter a valid URL starting with http:// or https://"

    hostname = (parsed.hostname or "").lower()
    path = parsed.path or ""

    if hostname in TIKTOK_SHORT_HOSTS:
        if not TIKTOK_SHORT_CODE_PATTERN.match(path):
            return None, "Please paste a valid TikTok link like https://vm.tiktok.com/XXXXXXX/"
        # Preserve the short host and code; yt-dlp follows the redirect.
        normalized = f"https://{hostname}{path}"
        return normalized, None

    if hostname in TIKTOK_HOSTS:
        if not any(pattern.match(path) for pattern in TIKTOK_STATUS_PATTERNS):
            return None, "Please paste a direct TikTok video link like https://www.tiktok.com/@user/video/1234567890"
        normalized = f"https://www.tiktok.com{path}"
        return normalized, None

    return None, "Please enter a valid TikTok video URL."


def normalize_and_validate_instagram_url(raw_url):
    url = (raw_url or "").strip()
    if not url:
        return None, "No URL provided"

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None, "Please enter a valid URL starting with http:// or https://"

    hostname = (parsed.hostname or "").lower()
    if hostname not in INSTAGRAM_HOSTS:
        return None, "Please enter a valid Instagram post or Reel URL."

    path = parsed.path or ""
    if not any(pattern.match(path) for pattern in INSTAGRAM_STATUS_PATTERNS):
        return None, "Please paste a direct Instagram post, Reel, or Story link like https://www.instagram.com/reel/Abc123/"

    normalized = f"https://www.instagram.com{path}"
    if not normalized.endswith("/"):
        normalized += "/"
    return normalized, None


# Ordered so the dispatcher tries the most specific host match first.
PLATFORM_VALIDATORS = (
    ("twitter", TWITTER_HOSTS, normalize_and_validate_tweet_url),
    ("tiktok", TIKTOK_HOSTS | TIKTOK_SHORT_HOSTS, normalize_and_validate_tiktok_url),
    ("instagram", INSTAGRAM_HOSTS, normalize_and_validate_instagram_url),
    ("youtube", YOUTUBE_HOSTS, normalize_and_validate_youtube_url),
    ("facebook", FACEBOOK_HOSTS, normalize_and_validate_facebook_url),
)


def detect_and_normalize_url(raw_url):
    """Detect the platform of a URL and normalize it.

    Returns (normalized_url, platform, error). On success error is None; on
    failure normalized_url and platform are None.
    """
    url = (raw_url or "").strip()
    if not url:
        return None, None, "No URL provided"

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None, None, "Please enter a valid URL starting with http:// or https://"

    hostname = (parsed.hostname or "").lower()
    for platform, hosts, validator in PLATFORM_VALIDATORS:
        if hostname in hosts:
            normalized, error = validator(url)
            if error:
                return None, None, error
            return normalized, platform, None

    return None, None, "Unsupported link. Please paste a Facebook, YouTube, Twitter/X, TikTok, or Instagram URL."


def map_yt_dlp_error(exc):
    text = str(exc).lower()
    if "no video could be found" in text or "no video formats found" in text:
        return "This post doesn't contain a video. Please paste a link to a post with a video."
    if "ffmpeg" in text and ("not found" in text or "not installed" in text):
        return "High-quality merging requires FFmpeg on the server. Please choose Best quality or Audio only."
    if "private" in text or "protected" in text:
        return "This post is private or protected, so it cannot be downloaded."
    # Checked after the specific cases above: yt-dlp appends a generic
    # "use --cookies" hint to many errors, so it must not shadow them.
    if "empty media response" in text:
        return "This content requires login and cannot be downloaded. Only public posts are supported."
    if "not available" in text or "unavailable" in text or "deleted" in text:
        return "This post is unavailable or has been removed."
    if "geo" in text or "country" in text or "region" in text:
        return "This content is not available in your region."
    if "login" in text or "sign in" in text or "authentication" in text:
        return "This post requires login and cannot be downloaded publicly."
    if "429" in text or "too many requests" in text:
        return "Too many requests right now. Please try again in a minute."
    return "Could not process this link right now. Please try again."


def build_formats(info):
    formats = []
    seen = set()
    for item in info.get("formats", []):
        ext = item.get("ext", "")
        format_id = item.get("format_id")
        if not format_id:
            continue
        if ext not in ("mp4", "webm", "mp3", "m4a"):
            continue

        height = item.get("height")
        if height:
            if height >= 2160:
                quality = "4K (2160p)"
            elif height >= 1440:
                quality = "1440p"
            elif height >= 1080:
                quality = "1080p"
            elif height >= 720:
                quality = "720p"
            elif height >= 480:
                quality = "480p"
            else:
                quality = f"{height}p"
            label = f"{ext.upper()} - {quality}"
        else:
            note = (item.get("format_note") or "").strip()
            label = f"{ext.upper()} - {note}" if note else f"{ext.upper()} - audio"

        if label in seen:
            continue
        seen.add(label)
        formats.append({"id": format_id, "label": label})
    return formats


def get_cached_metadata(url):
    now = time.time()
    record = metadata_cache.get(url)
    if not record:
        return None
    if now - record["ts"] > METADATA_CACHE_TTL_SECONDS:
        metadata_cache.pop(url, None)
        return None
    return record["data"]


def set_cached_metadata(url, data):
    metadata_cache[url] = {"ts": time.time(), "data": data}


@app.route("/")
def index():
    return render_template("index_youtube.html", site_url=get_site_base_url(), updated=get_updated_label())


@app.route("/pt/")
def index_pt():
    return render_template("index_youtube_pt.html", site_url=get_site_base_url(), updated=get_updated_label())


@app.route("/es/")
def index_es():
    return render_template("index_youtube_es.html", site_url=get_site_base_url(), updated=get_updated_label())


@app.route("/x/")
def index_twitter():
    return render_template("index.html", site_url=get_site_base_url(), updated=get_updated_label())


@app.route("/x/pt/")
def index_twitter_pt():
    return render_template("index_pt.html", site_url=get_site_base_url(), updated=get_updated_label())


@app.route("/x/es/")
def index_twitter_es():
    return render_template("index_es.html", site_url=get_site_base_url(), updated=get_updated_label())


@app.route("/tiktok/")
def index_tiktok():
    return render_template("index_tiktok.html", site_url=get_site_base_url(), updated=get_updated_label())


@app.route("/tiktok/pt/")
def index_tiktok_pt():
    return render_template("index_tiktok_pt.html", site_url=get_site_base_url(), updated=get_updated_label())


@app.route("/tiktok/es/")
def index_tiktok_es():
    return render_template("index_tiktok_es.html", site_url=get_site_base_url(), updated=get_updated_label())


@app.route("/instagram/")
def index_instagram():
    return render_template("index_instagram.html", site_url=get_site_base_url(), updated=get_updated_label())


@app.route("/instagram/pt/")
def index_instagram_pt():
    return render_template("index_instagram_pt.html", site_url=get_site_base_url(), updated=get_updated_label())


@app.route("/instagram/es/")
def index_instagram_es():
    return render_template("index_instagram_es.html", site_url=get_site_base_url(), updated=get_updated_label())


@app.route("/facebook/")
def index_facebook():
    return render_template("index_facebook.html", site_url=get_site_base_url(), updated=get_updated_label())


@app.route("/facebook/pt/")
def index_facebook_pt():
    return render_template("index_facebook_pt.html", site_url=get_site_base_url(), updated=get_updated_label())


@app.route("/facebook/es/")
def index_facebook_es():
    return render_template("index_facebook_es.html", site_url=get_site_base_url(), updated=get_updated_label())


@app.route("/youtube/")
def youtube_redirect():
    return redirect("/", 301)


@app.route("/youtube/pt/")
def youtube_pt_redirect():
    return redirect("/pt/", 301)


@app.route("/youtube/es/")
def youtube_es_redirect():
    return redirect("/es/", 301)


@app.route("/termos")
def termos():
    return render_template("termos.html", site_url=get_site_base_url(), updated=get_updated_label())


@app.route("/privacidade")
def privacidade():
    return render_template("privacidade.html", site_url=get_site_base_url(), updated=get_updated_label())


@app.route("/contato")
def contato():
    return render_template("contato.html", site_url=get_site_base_url(), updated=get_updated_label())


@app.route("/faq")
def faq():
    return render_template("faq.html", site_url=get_site_base_url(), updated=get_updated_label())


@app.route("/robots.txt")
def robots():
    site_url = get_site_base_url()
    content = (
        "User-agent: *\n"
        "Allow: /\n\n"
        f"Sitemap: {site_url}/sitemap.xml\n"
    )
    return Response(content, mimetype="text/plain")


@app.route("/info", methods=["POST"])
@limiter.limit("60 per minute")
def get_info():
    payload = request.get_json(silent=True) or {}
    raw_url = payload.get("url", "")

    url, platform, error = detect_and_normalize_url(raw_url)
    if error:
        return jsonify({"error": error}), 400

    cached = get_cached_metadata(url)
    if cached:
        return jsonify(cached)

    try:
        with yt_dlp.YoutubeDL(base_ydl_opts(platform)) as ydl:
            info = ydl.extract_info(url, download=False)

        if info.get("_type") == "playlist":
            entries = []
            for entry in info.get("entries", []):
                if entry is None:
                    continue
                entries.append({
                    "title": entry.get("title", "video"),
                    "url": entry.get("webpage_url") or entry.get("original_url", ""),
                    "thumbnail": entry.get("thumbnail", ""),
                    "duration": entry.get("duration_string", "") or "",
                    "uploader": entry.get("uploader", "") or "",
                })
            data = {
                "is_playlist": True,
                "title": info.get("title", "Playlist"),
                "uploader": info.get("uploader", ""),
                "entries": entries,
                "count": len(entries),
            }
            set_cached_metadata(url, data)
            return jsonify(data)

        data = {
            "title": sanitize_filename(info.get("title", "video")),
            "thumbnail": info.get("thumbnail", ""),
            "duration": info.get("duration_string", ""),
            "uploader": info.get("uploader", ""),
            "formats": build_formats(info),
        }
        set_cached_metadata(url, data)
        return jsonify(data)
    except Exception as exc:
        logger.exception("/info failed for url=%s", url)
        return jsonify({"error": map_yt_dlp_error(exc)}), 400


@app.route("/download", methods=["POST"])
@limiter.limit("20 per minute")
def download():
    payload = request.get_json(silent=True) or {}
    raw_url = payload.get("url", "")
    fmt = payload.get("format_id", "best[ext=mp4]/best")

    url, platform, error = detect_and_normalize_url(raw_url)
    if error:
        return jsonify({"error": error}), 400

    def run_download(selected_format):
        opts = base_ydl_opts(platform)
        opts.update({
            "format": selected_format,
            "outtmpl": os.path.join(DOWNLOAD_DIR, "%(title).120s.%(ext)s"),
            "merge_output_format": "mp4",
        })
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            resolved_path = ydl.prepare_filename(info)
            base = os.path.splitext(resolved_path)[0]
            ext_candidates = (".mp4", ".mkv", ".webm", ".m4a", ".mp3")
            for ext in ext_candidates:
                candidate = base + ext
                if os.path.exists(candidate):
                    return candidate
            return resolved_path

    try:
        path = run_download(fmt)
    except Exception as primary_exc:
        if fmt != "best[ext=mp4]/best":
            try:
                logger.warning(
                    "Primary download failed for format=%s, falling back to best MP4. Error=%s",
                    fmt,
                    primary_exc,
                )
                path = run_download("best[ext=mp4]/best")
            except Exception as fallback_exc:
                logger.exception("/download fallback failed for url=%s format=%s", url, fmt)
                return jsonify({"error": map_yt_dlp_error(fallback_exc)}), 400
        else:
            logger.exception("/download failed for url=%s format=%s", url, fmt)
            return jsonify({"error": map_yt_dlp_error(primary_exc)}), 400

    if not os.path.exists(path):
        return jsonify({"error": "File not found after processing. Please try again."}), 500

    extension = os.path.splitext(path)[1].lower()
    if extension in {".m4a", ".mp3"}:
        default_name = f"audio{extension}"
    else:
        default_name = f"video{extension or '.mp4'}"

    filename = sanitize_filename(os.path.basename(path)) or default_name

    @after_this_request
    def remove_file(response):
        try:
            threading.Timer(5, os.remove, args=[path]).start()
        except Exception:
            logger.warning("Could not schedule temporary file cleanup for %s", path)
        return response

    return send_file(path, as_attachment=True, download_name=filename)


@app.route('/sitemap.xml')
def sitemap():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url>
        <loc>https://www.savelinkx.com/</loc>
        <lastmod>2026-07-17</lastmod>
        <changefreq>daily</changefreq>
        <priority>1.0</priority>
      </url>
      <url>
        <loc>https://www.savelinkx.com/pt/</loc>
        <lastmod>2026-07-17</lastmod>
        <changefreq>daily</changefreq>
        <priority>0.9</priority>
      </url>
      <url>
        <loc>https://www.savelinkx.com/es/</loc>
        <lastmod>2026-07-17</lastmod>
        <changefreq>daily</changefreq>
        <priority>0.9</priority>
      </url>
      <url>
        <loc>https://www.savelinkx.com/tiktok/</loc>
        <lastmod>2026-07-15</lastmod>
        <changefreq>daily</changefreq>
        <priority>0.9</priority>
      </url>
      <url>
        <loc>https://www.savelinkx.com/tiktok/pt/</loc>
        <lastmod>2026-07-15</lastmod>
        <changefreq>daily</changefreq>
        <priority>0.9</priority>
      </url>
      <url>
        <loc>https://www.savelinkx.com/tiktok/es/</loc>
        <lastmod>2026-07-15</lastmod>
        <changefreq>daily</changefreq>
        <priority>0.9</priority>
      </url>
      <url>
        <loc>https://www.savelinkx.com/instagram/</loc>
        <lastmod>2026-07-16</lastmod>
        <changefreq>daily</changefreq>
        <priority>0.9</priority>
      </url>
      <url>
        <loc>https://www.savelinkx.com/instagram/pt/</loc>
        <lastmod>2026-07-16</lastmod>
        <changefreq>daily</changefreq>
        <priority>0.9</priority>
      </url>
      <url>
         <loc>https://www.savelinkx.com/instagram/es/</loc>
         <lastmod>2026-07-16</lastmod>
         <changefreq>daily</changefreq>
         <priority>0.9</priority>
       </url>
       <url>
         <loc>https://www.savelinkx.com/facebook/</loc>
         <lastmod>2026-07-17</lastmod>
         <changefreq>daily</changefreq>
         <priority>0.9</priority>
       </url>
       <url>
         <loc>https://www.savelinkx.com/facebook/pt/</loc>
         <lastmod>2026-07-17</lastmod>
         <changefreq>daily</changefreq>
         <priority>0.9</priority>
       </url>
       <url>
         <loc>https://www.savelinkx.com/facebook/es/</loc>
         <lastmod>2026-07-17</lastmod>
         <changefreq>daily</changefreq>
         <priority>0.9</priority>
       </url>
       <url>
         <loc>https://www.savelinkx.com/x/</loc>
        <lastmod>2026-07-17</lastmod>
        <changefreq>daily</changefreq>
        <priority>0.9</priority>
      </url>
      <url>
        <loc>https://www.savelinkx.com/x/pt/</loc>
        <lastmod>2026-07-17</lastmod>
        <changefreq>daily</changefreq>
        <priority>0.9</priority>
      </url>
      <url>
        <loc>https://www.savelinkx.com/x/es/</loc>
        <lastmod>2026-07-17</lastmod>
        <changefreq>daily</changefreq>
        <priority>0.9</priority>
      </url>
      <url>
        <loc>https://www.savelinkx.com/faq</loc>
        <lastmod>2026-04-27</lastmod>
        <changefreq>monthly</changefreq>
        <priority>0.8</priority>
      </url>
      <url>
        <loc>https://www.savelinkx.com/termos</loc>
        <lastmod>2026-04-27</lastmod>
        <changefreq>monthly</changefreq>
        <priority>0.3</priority>
      </url>
      <url>
        <loc>https://www.savelinkx.com/privacidade</loc>
        <lastmod>2026-04-27</lastmod>
        <changefreq>monthly</changefreq>
        <priority>0.3</priority>
      </url>
    </urlset>"""
    return xml, 200, {'Content-Type': 'application/xml'}


if __name__ == "__main__":
    debug_enabled = os.getenv("FLASK_DEBUG", "0") == "1"
    # Configurado para 0.0.0.0 para funcionar no Railway
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=debug_enabled)
    