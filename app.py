import logging
import os
import re
import threading
import time
from datetime import date
from urllib.parse import urlparse

from flask import Flask, Response, after_this_request, jsonify, render_template, request, send_file
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
    key_func=get_remote_address,
    app=app,
    default_limits=["120 per hour"],
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
    return "http://127.0.0.1:5000"


def base_ydl_opts():
    opts = {"quiet": True}
    cookie_file = os.getenv("TWITTER_COOKIES_FILE", "").strip()
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
    if hostname not in {
        "x.com", "www.x.com",
        "twitter.com", "www.twitter.com",
        "mobile.twitter.com",
    }:
        return None, "Please enter a valid Twitter/X post URL."

    path = parsed.path or ""
    if not any(pattern.match(path) for pattern in TWITTER_STATUS_PATTERNS):
        return None, "Please paste a direct post URL like https://x.com/user/status/123456789"

    normalized = f"https://{hostname.replace('www.', '')}{parsed.path}"
    if parsed.query:
        normalized = f"{normalized}?{parsed.query}"
    return normalized, None


def map_yt_dlp_error(exc):
    text = str(exc).lower()
    if "ffmpeg" in text and ("not found" in text or "not installed" in text):
        return "High-quality merging requires FFmpeg on the server. Please choose Best quality or Audio only."
    if "private" in text or "protected" in text:
        return "This post is private or protected, so it cannot be downloaded."
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
    return render_template("index.html", site_url=get_site_base_url(), updated=get_updated_label())


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


@app.route("/sitemap.xml")
def sitemap():
    site_url = get_site_base_url()
    today = date.today().isoformat()
    urls = [
        {"loc": f"{site_url}/", "priority": "1.0", "changefreq": "daily", "lastmod": today},
        {"loc": f"{site_url}/faq", "priority": "0.8", "changefreq": "monthly", "lastmod": today},
        {"loc": f"{site_url}/contato", "priority": "0.4", "changefreq": "monthly", "lastmod": today},
        {"loc": f"{site_url}/termos", "priority": "0.3", "changefreq": "monthly", "lastmod": today},
        {"loc": f"{site_url}/privacidade", "priority": "0.3", "changefreq": "monthly", "lastmod": today},
    ]
    body = "".join(
        (
            "<url>"
            f"<loc>{u['loc']}</loc>"
            f"<lastmod>{u['lastmod']}</lastmod>"
            f"<changefreq>{u['changefreq']}</changefreq>"
            f"<priority>{u['priority']}</priority>"
            "</url>"
        )
        for u in urls
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"{body}"
        "</urlset>"
    )
    return Response(xml, mimetype="application/xml")


@app.route("/info", methods=["POST"])
@limiter.limit("60 per minute")
def get_info():
    payload = request.get_json(silent=True) or {}
    raw_url = payload.get("url", "")

    url, error = normalize_and_validate_tweet_url(raw_url)
    if error:
        return jsonify({"error": error}), 400

    cached = get_cached_metadata(url)
    if cached:
        return jsonify(cached)

    try:
        with yt_dlp.YoutubeDL(base_ydl_opts()) as ydl:
            info = ydl.extract_info(url, download=False)

        data = {
            "title": sanitize_filename(info.get("title", "video")),
            "thumbnail": info.get("thumbnail", ""),
            "duration": info.get("duration_string", ""),
            "uploader": info.get("uploader", ""),
            "formats": build_formats(info),
        }
        set_cached_metadata(url, data)
        return jsonify(data)
    except Exception as exc:  # noqa: BLE001
        logger.exception("/info failed for url=%s", url)
        return jsonify({"error": map_yt_dlp_error(exc)}), 400


@app.route("/download", methods=["POST"])
@limiter.limit("20 per minute")
def download():
    payload = request.get_json(silent=True) or {}
    raw_url = payload.get("url", "")
    fmt = payload.get("format_id", "best[ext=mp4]/best")

    url, error = normalize_and_validate_tweet_url(raw_url)
    if error:
        return jsonify({"error": error}), 400

    def run_download(selected_format):
        opts = base_ydl_opts()
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
    except Exception as primary_exc:  # noqa: BLE001
        # Fallback for unavailable/merge-heavy formats: try a compatible best MP4 output.
        if fmt != "best[ext=mp4]/best":
            try:
                logger.warning(
                    "Primary download failed for format=%s, falling back to best MP4. Error=%s",
                    fmt,
                    primary_exc,
                )
                path = run_download("best[ext=mp4]/best")
            except Exception as fallback_exc:  # noqa: BLE001
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
        except Exception:  # noqa: BLE001
            logger.warning("Could not schedule temporary file cleanup for %s", path)
        return response

    return send_file(path, as_attachment=True, download_name=filename)

@app.route('/sitemap.xml')
def sitemap():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url>
        <loc>https://www.savelinkx.com/</loc>
        <lastmod>2026-04-27</lastmod>
        <changefreq>daily</changefreq>
        <priority>1.0</priority>
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
    # MUDANÇA AQUI: host de "127.0.0.1" para "0.0.0.0"
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=debug_enabled)
