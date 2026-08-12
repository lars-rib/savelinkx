import logging
import os
import re
import tempfile
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

MONTHS = {
    "en": {
        1: "January", 2: "February", 3: "March", 4: "April",
        5: "May", 6: "June", 7: "July", 8: "August",
        9: "September", 10: "October", 11: "November", 12: "December",
    },
    "pt": {
        1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
        5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
        9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
    },
    "es": {
        1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
        5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
        9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
    },
}

UPDATED_PREFIX = {
    "en": "Updated",
    "pt": "Atualizado",
    "es": "Actualizado",
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
    "fb.com", "www.fb.com",
}
# fb.watch share links carry the video id as the entire path ("/AbC123/"), so
# they match none of the /reel/ or /videos/ markers the long-form check looks
# for. They also have to keep their own host — rewriting one onto facebook.com
# yields a dead URL. Same shape as the vm./vt.tiktok.com handling above.
FACEBOOK_SHORT_HOSTS = {"fb.watch", "www.fb.watch"}
FACEBOOK_SHORT_CODE_PATTERN = re.compile(r"^/([A-Za-z0-9_-]+)/?$")

VIMEO_HOSTS = {"vimeo.com", "www.vimeo.com", "player.vimeo.com"}
VIMEO_PATTERN = re.compile(r"^/(\d+)(?:/.*)?$")

DAILYMOTION_HOSTS = {"dailymotion.com", "www.dailymotion.com", "dai.ly", "www.dai.ly"}
DAILYMOTION_PATTERN = re.compile(r"^/video/([A-Za-z0-9]+)(?:/.*)?$")
DAILYMOTION_SHORT_PATTERN = re.compile(r"^/([A-Za-z0-9]+)(?:/.*)?$")

REDDIT_HOSTS = {"reddit.com", "www.reddit.com", "old.reddit.com", "v.redd.it", "www.v.redd.it"}

PINTEREST_HOSTS = {"pinterest.com", "www.pinterest.com", "pin.it", "www.pin.it"}
PINTEREST_PIN_PATTERN = re.compile(r"^/pin/(\d+)(?:/.*)?$")

# Kwai has no dedicated yt-dlp extractor — it is served by the *generic*
# extractor, which finds the CDN URL embedded in the page. Verified working end
# to end (h264+aac mp4). Two consequences worth knowing: the generic path is
# more fragile than a real extractor (a page-structure change on Kwai's side
# breaks it), and it reports a single format, so there is no quality selector
# for Kwai — do not promise one in the page copy.
KWAI_HOSTS = {"kwai.com", "www.kwai.com", "m.kwai.com"}
KWAI_VIDEO_PATTERN = re.compile(r"^/@[\w.\-]+/video/(\d+)/?$", re.IGNORECASE)

LINKEDIN_HOSTS = {"linkedin.com", "www.linkedin.com"}
# Mirrors yt-dlp's LinkedInIE._VALID_URL so we never accept a link the
# extractor will reject: a post permalink ends in "-<activity id>-<4 chars>",
# and the feed form carries the activity urn directly.
LINKEDIN_POST_PATTERN = re.compile(r"^/posts/[^/?#]+-(\d+)-\w{4}/?$", re.IGNORECASE)
LINKEDIN_FEED_PATTERN = re.compile(r"^/feed/update/urn:li:activity:(\d+)/?$", re.IGNORECASE)


def normalize_and_validate_facebook_url(parsed):
    hostname = (parsed.hostname or "").lower()
    path = parsed.path or ""
    query = parsed.query or ""
    if hostname in FACEBOOK_SHORT_HOSTS:
        if not FACEBOOK_SHORT_CODE_PATTERN.match(path):
            return None, "Please paste a valid Facebook link like https://fb.watch/AbC123/"
        if not path.endswith("/"):
            path += "/"
        return f"https://{hostname}{path}", None
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


def normalize_and_validate_youtube_url(parsed):
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


def normalize_and_validate_vimeo_url(parsed):
    path = parsed.path or ""
    if not VIMEO_PATTERN.match(path):
        return None, "Please enter a valid Vimeo URL like https://vimeo.com/123456789"
    normalized = f"https://vimeo.com/{VIMEO_PATTERN.match(path).group(1)}"
    return normalized, None


def normalize_and_validate_dailymotion_url(parsed):
    hostname = (parsed.hostname or "").lower()
    path = parsed.path or ""
    if hostname in ("dai.ly", "www.dai.ly"):
        m = DAILYMOTION_SHORT_PATTERN.match(path)
        if not m:
            return None, "Please enter a valid Dailymotion link."
        return f"https://www.dailymotion.com/video/{m.group(1)}", None
    if not DAILYMOTION_PATTERN.match(path):
        return None, "Please enter a valid Dailymotion URL like https://www.dailymotion.com/video/x123abc"
    return f"https://www.dailymotion.com{path}", None


def normalize_and_validate_reddit_url(parsed):
    hostname = (parsed.hostname or "").lower()
    path = parsed.path or ""
    query = parsed.query or ""
    if hostname in ("v.redd.it", "www.v.redd.it"):
        return f"https://www.reddit.com{path}?{query}" if query else f"https://www.reddit.com{path}", None
    if "/comments/" not in path.lower():
        return None, "Please enter a valid Reddit post URL containing a video."
    return f"https://www.reddit.com{path}" + (f"?{query}" if query else ""), None


def normalize_and_validate_kwai_url(parsed):
    path = parsed.path or ""
    if not KWAI_VIDEO_PATTERN.match(path):
        return None, "Please paste a Kwai video link like https://www.kwai.com/@user/video/1234567890"
    return f"https://www.kwai.com{path}", None


def normalize_and_validate_linkedin_url(parsed):
    path = parsed.path or ""
    if not (LINKEDIN_POST_PATTERN.match(path) or LINKEDIN_FEED_PATTERN.match(path)):
        return None, "Please paste a LinkedIn post link like https://www.linkedin.com/posts/name-activity-1234567890-abcd/"
    return f"https://www.linkedin.com{path}", None


def normalize_and_validate_pinterest_url(parsed):
    hostname = (parsed.hostname or "").lower()
    path = parsed.path or ""
    if hostname in ("pin.it", "www.pin.it"):
        return f"https://www.pinterest.com{path}", None
    if not PINTEREST_PIN_PATTERN.match(path) and "/pin/" not in path.lower():
        return None, "Please enter a valid Pinterest Pin URL like https://www.pinterest.com/pin/123456789/"
    return f"https://www.pinterest.com{path}", None


@app.before_request
def log_request_started():
    request._started_at = time.time()


@app.after_request
def log_request_finished(response):
    started_at = getattr(request, "_started_at", time.time())
    elapsed_ms = int((time.time() - started_at) * 1000)
    logger.info("%s %s -> %s (%sms)", request.method, request.path, response.status_code, elapsed_ms)
    return response


@app.after_request
def add_security_headers(response):
    # None of these were being sent. Deliberately omitted: HSTS, because a
    # browser caches it for its whole max-age and it cannot be taken back
    # quickly; and CSP, because the pages carry inline <script> and <style>
    # throughout, so a meaningful policy needs its own pass to avoid breaking
    # the download flow. Both are worth adding later, on purpose.
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


def get_updated_label(lang="en"):
    today = date.today()
    return f"{UPDATED_PREFIX[lang]} {MONTHS[lang][today.month]} {today.year}"


def get_site_base_url():
    env_url = os.getenv("SITE_URL", "").strip().rstrip("/")
    if env_url:
        return env_url
    return "https://www.savelinkx.com"


# Long-tail landing pages each get a "Related downloaders" strip that links to
# the parent platform + the sibling long-tail pages. Internal linking both
# helps users discover the other tools and passes PageRank between the cluster.
# Localized to the request's language. Templates render the strip via a
# {% if related_tools %} block so non-long-tail pages stay clean.
RELATED_TOOLS_BY_SLUG = {
    "tiktok-mp3": {
        "en": {
            "section_title": "Related downloaders",
            "tools": [
                {"label": "TikTok Video Downloader", "url": "/tiktok/"},
                {"label": "TikTok Without Watermark", "url": "/tiktok-no-watermark/"},
                {"label": "Instagram Reels No Watermark", "url": "/instagram-reels-no-watermark/"},
                {"label": "YouTube Shorts Downloader", "url": "/youtube-shorts-downloader/"},
            ],
        },
        "pt": {
            "section_title": "Outras ferramentas",
            "tools": [
                {"label": "Baixar Vídeos do TikTok", "url": "/tiktok/pt/"},
                {"label": "TikTok Sem Marca D'água", "url": "/tiktok-no-watermark/pt/"},
                {"label": "Reels do Instagram Sem Marca", "url": "/instagram-reels-no-watermark/pt/"},
                {"label": "Baixar Shorts do YouTube", "url": "/youtube-shorts-downloader/pt/"},
            ],
        },
        "es": {
            "section_title": "Otras herramientas",
            "tools": [
                {"label": "Descargador de Videos de TikTok", "url": "/tiktok/es/"},
                {"label": "TikTok Sin Marca de Agua", "url": "/tiktok-no-watermark/es/"},
                {"label": "Reels de Instagram Sin Marca", "url": "/instagram-reels-no-watermark/es/"},
                {"label": "Descargador de Shorts de YouTube", "url": "/youtube-shorts-downloader/es/"},
            ],
        },
    },
    "twitter-gif": {
        "en": {
            "section_title": "Related downloaders",
            "tools": [
                {"label": "X (Twitter) Video Downloader", "url": "/"},
                {"label": "TikTok Without Watermark", "url": "/tiktok-no-watermark/"},
                {"label": "Instagram Reels No Watermark", "url": "/instagram-reels-no-watermark/"},
                {"label": "Facebook Story Saver", "url": "/facebook-story-saver/"},
            ],
        },
        "pt": {
            "section_title": "Outras ferramentas",
            "tools": [
                {"label": "Baixar Vídeos do X (Twitter)", "url": "/pt/"},
                {"label": "TikTok Sem Marca D'água", "url": "/tiktok-no-watermark/pt/"},
                {"label": "Reels do Instagram Sem Marca", "url": "/instagram-reels-no-watermark/pt/"},
                {"label": "Salvar Stories do Facebook", "url": "/facebook-story-saver/pt/"},
            ],
        },
        "es": {
            "section_title": "Otras herramientas",
            "tools": [
                {"label": "Descargador de Videos de X (Twitter)", "url": "/es/"},
                {"label": "TikTok Sin Marca de Agua", "url": "/tiktok-no-watermark/es/"},
                {"label": "Reels de Instagram Sin Marca", "url": "/instagram-reels-no-watermark/es/"},
                {"label": "Guardar Stories de Facebook", "url": "/facebook-story-saver/es/"},
            ],
        },
    },
    "reddit-video-with-sound": {
        "en": {
            "section_title": "Related downloaders",
            "tools": [
                {"label": "Reddit Video Downloader", "url": "/reddit/"},
                {"label": "TikTok to MP3", "url": "/tiktok-mp3/"},
                {"label": "YouTube Shorts Downloader", "url": "/youtube-shorts-downloader/"},
                {"label": "TikTok Without Watermark", "url": "/tiktok-no-watermark/"},
            ],
        },
        "pt": {
            "section_title": "Outras ferramentas",
            "tools": [
                {"label": "Baixar Vídeos do Reddit", "url": "/reddit/pt/"},
                {"label": "TikTok para MP3", "url": "/tiktok-mp3/pt/"},
                {"label": "Baixar Shorts do YouTube", "url": "/youtube-shorts-downloader/pt/"},
                {"label": "TikTok Sem Marca D'água", "url": "/tiktok-no-watermark/pt/"},
            ],
        },
        "es": {
            "section_title": "Otras herramientas",
            "tools": [
                {"label": "Descargador de Videos de Reddit", "url": "/reddit/es/"},
                {"label": "TikTok a MP3", "url": "/tiktok-mp3/es/"},
                {"label": "Descargador de Shorts de YouTube", "url": "/youtube-shorts-downloader/es/"},
                {"label": "TikTok Sin Marca de Agua", "url": "/tiktok-no-watermark/es/"},
            ],
        },
    },
    "tiktok-no-watermark": {
        "en": {
            "section_title": "Related downloaders",
            "tools": [
                {"label": "TikTok Video Downloader", "url": "/tiktok/"},
                {"label": "TikTok to MP3", "url": "/tiktok-mp3/"},
                {"label": "Instagram Reels No Watermark", "url": "/instagram-reels-no-watermark/"},
                {"label": "YouTube Shorts Downloader", "url": "/youtube-shorts-downloader/"},
            ],
        },
        "pt": {
            "section_title": "Outras ferramentas",
            "tools": [
                {"label": "Baixar Vídeos do TikTok", "url": "/tiktok/pt/"},
                {"label": "TikTok para MP3", "url": "/tiktok-mp3/pt/"},
                {"label": "Reels do Instagram Sem Marca", "url": "/instagram-reels-no-watermark/pt/"},
                {"label": "Baixar Shorts do YouTube", "url": "/youtube-shorts-downloader/pt/"},
            ],
        },
        "es": {
            "section_title": "Otras herramientas",
            "tools": [
                {"label": "Descargador de Videos de TikTok", "url": "/tiktok/es/"},
                {"label": "TikTok a MP3", "url": "/tiktok-mp3/es/"},
                {"label": "Reels de Instagram Sin Marca", "url": "/instagram-reels-no-watermark/es/"},
                {"label": "Descargador de Shorts de YouTube", "url": "/youtube-shorts-downloader/es/"},
            ],
        },
    },
    "instagram-reels-no-watermark": {
        "en": {
            "section_title": "Related downloaders",
            "tools": [
                {"label": "Instagram Video Downloader", "url": "/instagram/"},
                {"label": "TikTok Without Watermark", "url": "/tiktok-no-watermark/"},
                {"label": "Facebook Story Saver", "url": "/facebook-story-saver/"},
                {"label": "TikTok to MP3", "url": "/tiktok-mp3/"},
            ],
        },
        "pt": {
            "section_title": "Outras ferramentas",
            "tools": [
                {"label": "Baixar Vídeos do Instagram", "url": "/instagram/pt/"},
                {"label": "TikTok Sem Marca D'água", "url": "/tiktok-no-watermark/pt/"},
                {"label": "Salvar Stories do Facebook", "url": "/facebook-story-saver/pt/"},
                {"label": "TikTok para MP3", "url": "/tiktok-mp3/pt/"},
            ],
        },
        "es": {
            "section_title": "Otras herramientas",
            "tools": [
                {"label": "Descargador de Videos de Instagram", "url": "/instagram/es/"},
                {"label": "TikTok Sin Marca de Agua", "url": "/tiktok-no-watermark/es/"},
                {"label": "Guardar Stories de Facebook", "url": "/facebook-story-saver/es/"},
                {"label": "TikTok a MP3", "url": "/tiktok-mp3/es/"},
            ],
        },
    },
    "facebook-story-saver": {
        "en": {
            "section_title": "Related downloaders",
            "tools": [
                {"label": "Facebook Video Downloader", "url": "/facebook/"},
                {"label": "Instagram Reels No Watermark", "url": "/instagram-reels-no-watermark/"},
                {"label": "TikTok Without Watermark", "url": "/tiktok-no-watermark/"},
                {"label": "Twitter GIF Downloader", "url": "/twitter-gif/"},
            ],
        },
        "pt": {
            "section_title": "Outras ferramentas",
            "tools": [
                {"label": "Baixar Vídeos do Facebook", "url": "/facebook/pt/"},
                {"label": "Reels do Instagram Sem Marca", "url": "/instagram-reels-no-watermark/pt/"},
                {"label": "TikTok Sem Marca D'água", "url": "/tiktok-no-watermark/pt/"},
                {"label": "Baixar GIF do Twitter", "url": "/twitter-gif/pt/"},
            ],
        },
        "es": {
            "section_title": "Otras herramientas",
            "tools": [
                {"label": "Descargador de Videos de Facebook", "url": "/facebook/es/"},
                {"label": "Reels de Instagram Sin Marca", "url": "/instagram-reels-no-watermark/es/"},
                {"label": "TikTok Sin Marca de Agua", "url": "/tiktok-no-watermark/es/"},
                {"label": "Descargador de GIF de Twitter", "url": "/twitter-gif/es/"},
            ],
        },
    },
    "facebook-reels-downloader": {
        "en": {
            "section_title": "Related downloaders",
            "tools": [
                {"label": "Facebook Story Saver", "url": "/facebook-story-saver/"},
                {"label": "Instagram Reels No Watermark", "url": "/instagram-reels-no-watermark/"},
                {"label": "TikTok Without Watermark", "url": "/tiktok-no-watermark/"},
            ],
        },
        "pt": {
            "section_title": "Outras ferramentas",
            "tools": [
                {"label": "Salvar Stories do Facebook", "url": "/facebook-story-saver/pt/"},
                {"label": "Reels do Instagram Sem Marca", "url": "/instagram-reels-no-watermark/pt/"},
                {"label": "TikTok Sem Marca D'água", "url": "/tiktok-no-watermark/pt/"},
            ],
        },
        "es": {
            "section_title": "Otras herramientas",
            "tools": [
                {"label": "Guardar Stories de Facebook", "url": "/facebook-story-saver/es/"},
                {"label": "Reels de Instagram Sin Marca", "url": "/instagram-reels-no-watermark/es/"},
                {"label": "TikTok Sin Marca de Agua", "url": "/tiktok-no-watermark/es/"},
            ],
        },
    },
    "youtube-shorts-downloader": {
        "en": {
            "section_title": "Related downloaders",
            "tools": [
                {"label": "YouTube Video Downloader", "url": "/youtube/"},
                {"label": "TikTok to MP3", "url": "/tiktok-mp3/"},
                {"label": "TikTok Without Watermark", "url": "/tiktok-no-watermark/"},
                {"label": "Instagram Reels No Watermark", "url": "/instagram-reels-no-watermark/"},
            ],
        },
        "pt": {
            "section_title": "Outras ferramentas",
            "tools": [
                {"label": "Baixar Vídeos do YouTube", "url": "/youtube/pt/"},
                {"label": "TikTok para MP3", "url": "/tiktok-mp3/pt/"},
                {"label": "TikTok Sem Marca D'água", "url": "/tiktok-no-watermark/pt/"},
                {"label": "Reels do Instagram Sem Marca", "url": "/instagram-reels-no-watermark/pt/"},
            ],
        },
        "es": {
            "section_title": "Otras herramientas",
            "tools": [
                {"label": "Descargador de Videos de YouTube", "url": "/youtube/es/"},
                {"label": "TikTok a MP3", "url": "/tiktok-mp3/es/"},
                {"label": "TikTok Sin Marca de Agua", "url": "/tiktok-no-watermark/es/"},
                {"label": "Reels de Instagram Sin Marca", "url": "/instagram-reels-no-watermark/es/"},
            ],
        },
    },
    "video-to-mp3": {
        "en": {
            "section_title": "Related downloaders",
            "tools": [
                {"label": "TikTok to MP3", "url": "/tiktok-mp3/"},
                {"label": "Facebook Reels Downloader", "url": "/facebook-reels-downloader/"},
                {"label": "TikTok Without Watermark", "url": "/tiktok-no-watermark/"},
            ],
        },
        "pt": {
            "section_title": "Outras ferramentas",
            "tools": [
                {"label": "TikTok para MP3", "url": "/tiktok-mp3/pt/"},
                {"label": "Baixar Reels do Facebook", "url": "/facebook-reels-downloader/pt/"},
                {"label": "TikTok Sem Marca D'água", "url": "/tiktok-no-watermark/pt/"},
            ],
        },
        "es": {
            "section_title": "Otras herramientas",
            "tools": [
                {"label": "TikTok a MP3", "url": "/tiktok-mp3/es/"},
                {"label": "Descargar Reels de Facebook", "url": "/facebook-reels-downloader/es/"},
                {"label": "TikTok Sin Marca de Agua", "url": "/tiktok-no-watermark/es/"},
            ],
        },
    },
    "instagram-audio": {
        "en": {
            "section_title": "Related downloaders",
            "tools": [
                {"label": "Instagram Reels No Watermark", "url": "/instagram-reels-no-watermark/"},
                {"label": "Video to MP3 Converter", "url": "/video-to-mp3/"},
                {"label": "TikTok to MP3", "url": "/tiktok-mp3/"},
            ],
        },
        "pt": {
            "section_title": "Outras ferramentas",
            "tools": [
                {"label": "Reels do Instagram Sem Marca", "url": "/instagram-reels-no-watermark/pt/"},
                {"label": "Vídeo para MP3", "url": "/video-to-mp3/pt/"},
                {"label": "TikTok para MP3", "url": "/tiktok-mp3/pt/"},
            ],
        },
        "es": {
            "section_title": "Otras herramientas",
            "tools": [
                {"label": "Reels de Instagram Sin Marca", "url": "/instagram-reels-no-watermark/es/"},
                {"label": "Video a MP3", "url": "/video-to-mp3/es/"},
                {"label": "TikTok a MP3", "url": "/tiktok-mp3/es/"},
            ],
        },
    },
    "pinterest-no-watermark": {
        "en": {
            "section_title": "Related downloaders",
            "tools": [
                {"label": "TikTok Without Watermark", "url": "/tiktok-no-watermark/"},
                {"label": "Instagram Reels No Watermark", "url": "/instagram-reels-no-watermark/"},
                {"label": "Video to MP3 Converter", "url": "/video-to-mp3/"},
            ],
        },
        "pt": {
            "section_title": "Outras ferramentas",
            "tools": [
                {"label": "TikTok Sem Marca D'água", "url": "/tiktok-no-watermark/pt/"},
                {"label": "Reels do Instagram Sem Marca", "url": "/instagram-reels-no-watermark/pt/"},
                {"label": "Vídeo para MP3", "url": "/video-to-mp3/pt/"},
            ],
        },
        "es": {
            "section_title": "Otras herramientas",
            "tools": [
                {"label": "TikTok Sin Marca de Agua", "url": "/tiktok-no-watermark/es/"},
                {"label": "Reels de Instagram Sin Marca", "url": "/instagram-reels-no-watermark/es/"},
                {"label": "Video a MP3", "url": "/video-to-mp3/es/"},
            ],
        },
    },
}


@app.context_processor
def inject_related_tools():
    path = request.path
    lang = "en"
    base = path
    for lg in ("pt", "es"):
        if path.endswith("/%s/" % lg):
            lang = lg
            base = path[: -(len(lg) + 1)]
            break
    for slug, by_lang in RELATED_TOOLS_BY_SLUG.items():
        if base == "/%s/" % slug:
            return {"related_tools": by_lang[lang]}
    return {}


@app.context_processor
def inject_analytics():
    # Cloudflare Web Analytics beacon token. Empty (default) renders nothing;
    # set CF_BEACON_TOKEN in the systemd unit to activate — no code change
    # needed. Token comes from CF dashboard -> Analytics -> Web Analytics.
    return {"cf_beacon_token": os.getenv("CF_BEACON_TOKEN", "").strip()}


# Fixed platform order for the selector, rendered identically on every page so
# the pills never reorder when navigating. (name, url-slug); "" = the X/Twitter
# homepage at "/". Active platform + language are derived from the request path
# so the same list works everywhere without per-template blocks.
_NAV_PLATFORMS = [
    ("X (Twitter)", ""),
    ("TikTok", "tiktok"),
    ("Instagram", "instagram"),
    ("YouTube", "youtube"),
    ("Facebook", "facebook"),
    ("Reddit", "reddit"),
    ("Vimeo", "vimeo"),
    ("Dailymotion", "dailymotion"),
    ("Pinterest", "pinterest"),
    ("LinkedIn", "linkedin"),
    ("Kwai", "kwai"),
]
# base EN path -> active platform slug (landing pages map to their parent)
_PATH_TO_SLUG = {
    "/": "", "/tiktok/": "tiktok", "/instagram/": "instagram",
    "/youtube/": "youtube", "/facebook/": "facebook", "/reddit/": "reddit",
    "/vimeo/": "vimeo", "/dailymotion/": "dailymotion", "/pinterest/": "pinterest",
    "/linkedin/": "linkedin", "/kwai/": "kwai",
    "/tiktok-mp3/": "tiktok", "/twitter-gif/": "", "/reddit-video-with-sound/": "reddit",
    "/tiktok-no-watermark/": "tiktok", "/instagram-reels-no-watermark/": "instagram",
    "/facebook-story-saver/": "facebook", "/youtube-shorts-downloader/": "youtube",
    "/facebook-reels-downloader/": "facebook", "/instagram-audio/": "instagram",
    "/pinterest-no-watermark/": "pinterest",
}

# The long-tail landing pages, surfaced site-wide by the "popular tools" strip
# in base.html. Before this they were linked only from the sitemap and from
# each other, so almost no internal links pointed at them — the homepage
# reached just one of the seven. Slug order here is the render order.
_LONGTAIL_TOOLS = [
    ("tiktok-no-watermark", {
        "en": "TikTok No Watermark", "pt": "TikTok Sem Marca D'água", "es": "TikTok Sin Marca de Agua"}),
    ("instagram-reels-no-watermark", {
        "en": "Reels No Watermark", "pt": "Reels Sem Marca", "es": "Reels Sin Marca"}),
    ("tiktok-mp3", {
        "en": "TikTok to MP3", "pt": "TikTok para MP3", "es": "TikTok a MP3"}),
    ("twitter-gif", {
        "en": "Twitter GIF", "pt": "GIF do Twitter", "es": "GIF de Twitter"}),
    ("reddit-video-with-sound", {
        "en": "Reddit Video + Sound", "pt": "Vídeo do Reddit com Som", "es": "Video de Reddit con Sonido"}),
    ("facebook-story-saver", {
        "en": "Facebook Story Saver", "pt": "Stories do Facebook", "es": "Stories de Facebook"}),
    ("facebook-reels-downloader", {
        "en": "Facebook Reels", "pt": "Reels do Facebook", "es": "Reels de Facebook"}),
    ("youtube-shorts-downloader", {
        "en": "YouTube Shorts", "pt": "Shorts do YouTube", "es": "Shorts de YouTube"}),
    ("video-to-mp3", {
        "en": "Video to MP3", "pt": "Vídeo para MP3", "es": "Video a MP3"}),
    ("instagram-audio", {
        "en": "Instagram Audio", "pt": "Áudio do Instagram", "es": "Audio de Instagram"}),
    ("pinterest-no-watermark", {
        "en": "Pinterest No Watermark", "pt": "Pinterest Sem Marca D'água", "es": "Pinterest Sin Marca de Agua"}),
]
_LONGTAIL_LABEL = {
    "en": "Popular tools", "pt": "Ferramentas populares", "es": "Herramientas populares",
}


@app.context_processor
def inject_nav():
    path = request.path if request.path.endswith("/") else request.path + "/"
    lang, base = "en", path
    for lg in ("pt", "es"):
        if path.endswith("/%s/" % lg):
            lang, base = lg, path[: -(len(lg) + 1)]
            break
    active = _PATH_TO_SLUG.get(base)  # None on non-platform pages (faq, etc.)

    def purl(slug):
        if slug == "":
            return "/" if lang == "en" else "/%s/" % lang
        return "/%s/" % slug if lang == "en" else "/%s/%s/" % (slug, lang)

    nav_platforms = [
        {"name": name, "url": purl(slug), "active": (slug == active)}
        for name, slug in _NAV_PLATFORMS
    ]

    def turl(slug):
        return "/%s/" % slug if lang == "en" else "/%s/%s/" % (slug, lang)

    longtail_tools = [
        {"name": names.get(lang, names["en"]), "url": turl(slug),
         "active": (base == "/%s/" % slug)}
        for slug, names in _LONGTAIL_TOOLS
    ]
    return {
        "nav_platforms": nav_platforms,
        "longtail_tools": longtail_tools,
        "longtail_label": _LONGTAIL_LABEL.get(lang, _LONGTAIL_LABEL["en"]),
    }


COOKIE_ENV_BY_PLATFORM = {
    "twitter": "TWITTER_COOKIES_FILE",
    "tiktok": "TIKTOK_COOKIES_FILE",
    "instagram": "INSTAGRAM_COOKIES_FILE",
    "youtube": "YOUTUBE_COOKIES_FILE",
    "facebook": "FACEBOOK_COOKIES_FILE",
    "vimeo": "VIMEO_COOKIES_FILE",
    "dailymotion": "DAILYMOTION_COOKIES_FILE",
    "reddit": "REDDIT_COOKIES_FILE",
    "pinterest": "PINTEREST_COOKIES_FILE",
    "linkedin": "LINKEDIN_COOKIES_FILE",
    "kwai": "KWAI_COOKIES_FILE",
}


# Platforms that block the Contabo datacenter IP and must egress through the
# local Cloudflare WARP SOCKS proxy instead. YouTube: bot detection (1/6 direct
# -> 10/10 via WARP). Reddit: "account authentication required" direct -> works
# via WARP. Both verified on the VPS. WARP egresses IPv6, so a platform whose
# API is IPv4-only or blocks WARP won't be helped here (e.g. Dailymotion).
WARP_PLATFORMS = {"youtube", "reddit"}
WARP_PROXY = os.getenv("WARP_PROXY", "socks5://127.0.0.1:40000").strip()


def base_ydl_opts(platform=None, cookie_file_override=None):
    opts = {"quiet": True, "age_limit": 99}
    if platform in WARP_PLATFORMS and WARP_PROXY:
        opts["proxy"] = WARP_PROXY
    if platform == "youtube":
        opts["remote_components"] = ["ejs:github"]
        # Deliberately NOT pinning player_client. The ios/android/web set was a
        # pre-PO-token workaround and now caps quality at 360p; yt-dlp's default
        # client set combined with the bgutil PO token provider (HTTP server on
        # 127.0.0.1:4416) yields the full ladder up to 2160p. Re-pinning clients
        # here will silently degrade quality — measure before changing.
        opts["playlistend"] = 200
    if cookie_file_override and os.path.exists(cookie_file_override):
        opts["cookiefile"] = cookie_file_override
    else:
        cookie_env = COOKIE_ENV_BY_PLATFORM.get(platform, "TWITTER_COOKIES_FILE")
        cookie_file = os.getenv(cookie_env, "").strip()
        if cookie_file and os.path.exists(cookie_file):
            opts["cookiefile"] = cookie_file
    return opts


def get_uploaded_cookie_path():
    f = request.files.get("cookie_file")
    if not f or not f.filename:
        return None
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
    f.save(tmp.name)
    tmp.close()
    return tmp.name


def sanitize_filename(name):
    return re.sub(r'[\/*?:"<>|]', "_", name)


def normalize_and_validate_tweet_url(parsed):
    hostname = (parsed.hostname or "").lower()
    path = parsed.path or ""
    if not any(pattern.match(path) for pattern in TWITTER_STATUS_PATTERNS):
        return None, "Please paste a direct post URL like https://x.com/user/status/123456789"
    normalized = f"https://{hostname.replace('www.', '')}{parsed.path}"
    if parsed.query:
        normalized = f"{normalized}?{parsed.query}"
    return normalized, None


def normalize_and_validate_tiktok_url(parsed):
    hostname = (parsed.hostname or "").lower()
    path = parsed.path or ""
    if hostname in TIKTOK_SHORT_HOSTS:
        if not TIKTOK_SHORT_CODE_PATTERN.match(path):
            return None, "Please paste a valid TikTok link like https://vm.tiktok.com/XXXXXXX/"
        normalized = f"https://{hostname}{path}"
        return normalized, None
    if hostname in TIKTOK_HOSTS:
        if not any(pattern.match(path) for pattern in TIKTOK_STATUS_PATTERNS):
            return None, "Please paste a direct TikTok video link like https://www.tiktok.com/@user/video/1234567890"
        normalized = f"https://www.tiktok.com{path}"
        return normalized, None
    return None, "Please enter a valid TikTok video URL."


def normalize_and_validate_instagram_url(parsed):
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
    ("facebook", FACEBOOK_HOSTS | FACEBOOK_SHORT_HOSTS, normalize_and_validate_facebook_url),
    ("vimeo", VIMEO_HOSTS, normalize_and_validate_vimeo_url),
    ("dailymotion", DAILYMOTION_HOSTS, normalize_and_validate_dailymotion_url),
    ("reddit", REDDIT_HOSTS, normalize_and_validate_reddit_url),
    ("pinterest", PINTEREST_HOSTS, normalize_and_validate_pinterest_url),
    ("linkedin", LINKEDIN_HOSTS, normalize_and_validate_linkedin_url),
    ("kwai", KWAI_HOSTS, normalize_and_validate_kwai_url),
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
            normalized, error = validator(parsed)
            if error:
                return None, None, error
            return normalized, platform, None

    return None, None, "Unsupported link. Please paste a Facebook, YouTube, Twitter/X, TikTok, Instagram, Vimeo, Dailymotion, Reddit, Pinterest, LinkedIn, or Kwai URL."


def map_yt_dlp_error(exc):
    text = str(exc).lower()
    if "sign in to confirm" in text or "not a bot" in text:
        return "YouTube blocked this request from our server. Most videos still work — try another link or try again later."
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
        if "login required" in text or "requires login" in text or "sign in to" in text or "you must be logged in" in text or "http error 403" in text or "unreachable" in text:
            return "This content requires login and cannot be downloaded publicly."
        if "404" in text or "not found" in text:
            return "This content could not be found. The link may be broken or the post may have been removed."
        return "This content is not available right now. It may be restricted, private, or the link may be incorrect. Try a different link."
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

        filesize = item.get("filesize") or item.get("filesize_approx") or 0

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
            # A missing height does not mean audio-only: the generic extractor
            # (Kwai) reports a full video with unknown resolution. Only call it
            # audio when the format actually has no video stream, otherwise a
            # perfectly good MP4 gets mislabelled and users skip it.
            note = (item.get("format_note") or "").strip()
            if note:
                label = f"{ext.upper()} - {note}"
            elif item.get("vcodec") == "none" or ext in ("mp3", "m4a"):
                label = f"{ext.upper()} - audio"
            else:
                label = f"{ext.upper()} - original quality"

        if label in seen:
            continue
        seen.add(label)
        formats.append({"id": format_id, "label": label, "size": filesize})
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
    return render_template("index.html", site_url=get_site_base_url(), updated=get_updated_label("en"))


@app.route("/pt/")
def index_pt():
    return render_template("index_pt.html", site_url=get_site_base_url(), updated=get_updated_label("pt"))


@app.route("/es/")
def index_es():
    return render_template("index_es.html", site_url=get_site_base_url(), updated=get_updated_label("es"))


# X/Twitter was the historic homepage; it now lives at "/" again. The old /x/
# URLs (a temporary OpenCode arrangement) 301 back to "/" so any external links
# and Google's index consolidate on the homepage.
@app.route("/x/")
def index_twitter():
    return redirect("/", 301)


@app.route("/x/pt/")
def index_twitter_pt():
    return redirect("/pt/", 301)


@app.route("/x/es/")
def index_twitter_es():
    return redirect("/es/", 301)


@app.route("/tiktok/")
def index_tiktok():
    return render_template("index_tiktok.html", site_url=get_site_base_url(), updated=get_updated_label("en"))


@app.route("/tiktok/pt/")
def index_tiktok_pt():
    return render_template("index_tiktok_pt.html", site_url=get_site_base_url(), updated=get_updated_label("pt"))


@app.route("/tiktok/es/")
def index_tiktok_es():
    return render_template("index_tiktok_es.html", site_url=get_site_base_url(), updated=get_updated_label("es"))


@app.route("/instagram/")
def index_instagram():
    return render_template("index_instagram.html", site_url=get_site_base_url(), updated=get_updated_label("en"))


@app.route("/instagram/pt/")
def index_instagram_pt():
    return render_template("index_instagram_pt.html", site_url=get_site_base_url(), updated=get_updated_label("pt"))


@app.route("/instagram/es/")
def index_instagram_es():
    return render_template("index_instagram_es.html", site_url=get_site_base_url(), updated=get_updated_label("es"))


@app.route("/facebook/")
def index_facebook():
    return render_template("index_facebook.html", site_url=get_site_base_url(), updated=get_updated_label("en"))


@app.route("/facebook/pt/")
def index_facebook_pt():
    return render_template("index_facebook_pt.html", site_url=get_site_base_url(), updated=get_updated_label("pt"))


@app.route("/facebook/es/")
def index_facebook_es():
    return render_template("index_facebook_es.html", site_url=get_site_base_url(), updated=get_updated_label("es"))


@app.route("/vimeo/")
def index_vimeo():
    return render_template("index_vimeo.html", site_url=get_site_base_url(), updated=get_updated_label("en"))


@app.route("/vimeo/pt/")
def index_vimeo_pt():
    return render_template("index_vimeo_pt.html", site_url=get_site_base_url(), updated=get_updated_label("pt"))


@app.route("/vimeo/es/")
def index_vimeo_es():
    return render_template("index_vimeo_es.html", site_url=get_site_base_url(), updated=get_updated_label("es"))


@app.route("/dailymotion/")
def index_dailymotion():
    return render_template("index_dailymotion.html", site_url=get_site_base_url(), updated=get_updated_label("en"))


@app.route("/dailymotion/pt/")
def index_dailymotion_pt():
    return render_template("index_dailymotion_pt.html", site_url=get_site_base_url(), updated=get_updated_label("pt"))


@app.route("/dailymotion/es/")
def index_dailymotion_es():
    return render_template("index_dailymotion_es.html", site_url=get_site_base_url(), updated=get_updated_label("es"))


@app.route("/reddit/")
def index_reddit():
    return render_template("index_reddit.html", site_url=get_site_base_url(), updated=get_updated_label("en"))


@app.route("/reddit/pt/")
def index_reddit_pt():
    return render_template("index_reddit_pt.html", site_url=get_site_base_url(), updated=get_updated_label("pt"))


@app.route("/reddit/es/")
def index_reddit_es():
    return render_template("index_reddit_es.html", site_url=get_site_base_url(), updated=get_updated_label("es"))


@app.route("/pinterest/")
def index_pinterest():
    return render_template("index_pinterest.html", site_url=get_site_base_url(), updated=get_updated_label("en"))


@app.route("/pinterest/pt/")
def index_pinterest_pt():
    return render_template("index_pinterest_pt.html", site_url=get_site_base_url(), updated=get_updated_label("pt"))


@app.route("/pinterest/es/")
def index_pinterest_es():
    return render_template("index_pinterest_es.html", site_url=get_site_base_url(), updated=get_updated_label("es"))


@app.route("/linkedin/")
def index_linkedin():
    return render_template("index_linkedin.html", site_url=get_site_base_url(), updated=get_updated_label("en"))


@app.route("/linkedin/pt/")
def index_linkedin_pt():
    return render_template("index_linkedin_pt.html", site_url=get_site_base_url(), updated=get_updated_label("pt"))


@app.route("/linkedin/es/")
def index_linkedin_es():
    return render_template("index_linkedin_es.html", site_url=get_site_base_url(), updated=get_updated_label("es"))


@app.route("/kwai/")
def index_kwai():
    return render_template("index_kwai.html", site_url=get_site_base_url(), updated=get_updated_label("en"))


@app.route("/kwai/pt/")
def index_kwai_pt():
    return render_template("index_kwai_pt.html", site_url=get_site_base_url(), updated=get_updated_label("pt"))


@app.route("/kwai/es/")
def index_kwai_es():
    return render_template("index_kwai_es.html", site_url=get_site_base_url(), updated=get_updated_label("es"))


# --- Long-tail landing pages (WS2) ---------------------------------------
# Intent-specific SEO entry points that reuse an existing platform's /info
# validator (platform detection is by pasted URL, not by page), so no backend
# validator changes are needed. Each is a thin child of base.html.
_LANDING_PAGES = {
    "tiktok-mp3": "index_tiktok_mp3",
    "twitter-gif": "index_twitter_gif",
    "reddit-video-with-sound": "index_reddit_video_with_sound",
    "tiktok-no-watermark": "index_tiktok_no_watermark",
    "instagram-reels-no-watermark": "index_instagram_reels_no_watermark",
    "facebook-story-saver": "index_facebook_story_saver",
    "facebook-reels-downloader": "index_facebook_reels_downloader",
    "youtube-shorts-downloader": "index_youtube_shorts_downloader",
    "video-to-mp3": "index_video_to_mp3",
    "instagram-audio": "index_instagram_audio",
    "pinterest-no-watermark": "index_pinterest_no_watermark",
}


def _make_landing(template, lang="en"):
    def view():
        return render_template(f"{template}.html", site_url=get_site_base_url(), updated=get_updated_label(lang))
    return view


for _slug, _tmpl in _LANDING_PAGES.items():
    for _suffix, _lang in (("", "en"), ("pt/", "pt"), ("es/", "es")):
        app.add_url_rule(
            f"/{_slug}/{_suffix}",
            endpoint=f"{_tmpl}{('_' + _lang) if _lang != 'en' else ''}",
            view_func=_make_landing(f"{_tmpl}{('_' + _lang) if _lang != 'en' else ''}", _lang),
        )


# YouTube lives at /youtube/ (not "/"). The templates carry
# <meta name="robots" content="noindex, follow"> so Google does not index the
# YouTube downloader pages, keeping that liability off the homepage and out of
# search — while the pages stay reachable for direct/linked visitors.
@app.route("/youtube/")
def index_youtube():
    return render_template("index_youtube.html", site_url=get_site_base_url(), updated=get_updated_label("en"))


@app.route("/youtube/pt/")
def index_youtube_pt():
    return render_template("index_youtube_pt.html", site_url=get_site_base_url(), updated=get_updated_label("pt"))


@app.route("/youtube/es/")
def index_youtube_es():
    return render_template("index_youtube_es.html", site_url=get_site_base_url(), updated=get_updated_label("es"))


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
    raw_url = payload.get("url", "") or request.form.get("url", "")

    url, platform, error = detect_and_normalize_url(raw_url)
    if error:
        return jsonify({"error": error}), 400

    cookie_path = get_uploaded_cookie_path()

    cached = get_cached_metadata(url)
    if cached:
        if cookie_path:
            try: os.remove(cookie_path)
            except Exception: pass
        return jsonify(cached)

    try:
        with yt_dlp.YoutubeDL(base_ydl_opts(platform, cookie_path)) as ydl:
            info = ydl.extract_info(url, download=False)
        if cookie_path:
            try: os.remove(cookie_path)
            except Exception: pass

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
        if cookie_path:
            try: os.remove(cookie_path)
            except Exception: pass
        logger.exception("/info failed for url=%s", url)
        err_text = str(exc).lower()
        needs_cookies = "login" in err_text or "private" in err_text or "empty media response" in err_text
        resp = jsonify({"error": map_yt_dlp_error(exc)})
        resp.status_code = 400
        if needs_cookies and not cookie_path:
            resp = jsonify({"error": map_yt_dlp_error(exc), "requires_cookies": True})
            resp.status_code = 400
        return resp


@app.route("/download", methods=["POST"])
@limiter.limit("20 per minute")
def download():
    payload = request.get_json(silent=True) or {}
    raw_url = payload.get("url", "") or request.form.get("url", "")
    fmt = payload.get("format_id", "best[ext=mp4]/best")

    url, platform, error = detect_and_normalize_url(raw_url)
    if error:
        return jsonify({"error": error}), 400

    cookie_path = get_uploaded_cookie_path()

    def run_download(selected_format):
        opts = base_ydl_opts(platform, cookie_path)
        is_audio = "bestaudio" in selected_format or selected_format.endswith("bestaudio")
        opts.update({
            "format": selected_format,
            "outtmpl": os.path.join(DOWNLOAD_DIR, "%(title).120s.%(ext)s"),
        })
        if not is_audio:
            opts["merge_output_format"] = "mp4"
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
                is_audio = "bestaudio" in fmt or fmt.endswith("bestaudio")
                fallback = "bestaudio[ext=m4a]/bestaudio" if is_audio else "best[ext=mp4]/best"
                logger.warning(
                    "Primary download failed for format=%s, falling back. Error=%s",
                    fmt,
                    primary_exc,
                )
                path = run_download(fallback)
            except Exception as fallback_exc:
                logger.exception("/download fallback failed for url=%s format=%s", url, fmt)
                try: os.remove(cookie_path)
                except: pass
                return jsonify({"error": map_yt_dlp_error(fallback_exc)}), 400
        else:
            logger.exception("/download failed for url=%s format=%s", url, fmt)
            try: os.remove(cookie_path)
            except: pass
            err_text = str(primary_exc).lower()
            needs_cookies = "login" in err_text or "private" in err_text or "empty media response" in err_text
            if needs_cookies and not cookie_path:
                return jsonify({"error": map_yt_dlp_error(primary_exc), "requires_cookies": True}), 400
            return jsonify({"error": map_yt_dlp_error(primary_exc)}), 400

    if cookie_path:
        try: os.remove(cookie_path)
        except Exception: pass

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


@app.route("/subtitles", methods=["POST"])
@limiter.limit("20 per minute")
def get_subtitles():
    payload = request.get_json(silent=True) or {}
    raw_url = payload.get("url", "")
    lang = payload.get("lang", "en")

    url, platform, error = detect_and_normalize_url(raw_url)
    if error:
        return jsonify({"error": error}), 400

    try:
        opts = base_ydl_opts(platform)
        opts["writesubtitles"] = True
        opts["writeautomaticsub"] = True
        opts["subtitleslangs"] = [lang]
        opts["skip_download"] = True
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

        subs = info.get("subtitles", {}).get(lang) or info.get("automatic_captions", {}).get(lang)
        if not subs:
            return jsonify({"error": f"No subtitles found for language: {lang}"}), 404

        sub = subs[-1]
        sub_url = sub.get("url", "")
        if not sub_url:
            return jsonify({"error": "Subtitle URL not available"}), 500

        import requests as req
        sub_resp = req.get(sub_url, timeout=30)
        content = sub_resp.text

        title = sanitize_filename(info.get("title", "video"))
        return Response(
            content,
            mimetype="text/plain",
            headers={"Content-Disposition": f"attachment; filename={title}.{lang}.srt"}
        )
    except Exception as exc:
        logger.exception("/subtitles failed for url=%s", url)
        return jsonify({"error": map_yt_dlp_error(exc)}), 400


# Each entry: (path, lastmod ISO date, changefreq, priority). Dates reflect
# the last meaningful content change for that page. Add a new row when adding
# a page. Note: /youtube/ is intentionally excluded — YouTube pages are
# noindex, follow to keep the platform off Google's index (per AGENTS.md).
# Only final, indexable, self-canonical URLs belong here. Deliberately absent:
#   /x/, /x/pt/, /x/es/   -> redirect to and canonicalise on "/" (Google would
#                            report "Duplicate, submitted URL not selected as
#                            canonical"); they stay live as entry points.
#   /youtube/*, /youtube-shorts-downloader/*
#                         -> carry <meta robots="noindex"> by design, so
#                            submitting them triggers "Submitted URL marked
#                            noindex" in Search Console.
SITEMAP_PAGES = (
    ("/", "2026-07-17", "daily", "1.0"),
    ("/pt/", "2026-07-17", "daily", "0.9"),
    ("/es/", "2026-07-17", "daily", "0.9"),
    ("/tiktok/", "2026-07-15", "daily", "0.9"),
    ("/tiktok/pt/", "2026-07-15", "daily", "0.9"),
    ("/tiktok/es/", "2026-07-15", "daily", "0.9"),
    ("/instagram/", "2026-07-16", "daily", "0.9"),
    ("/instagram/pt/", "2026-07-16", "daily", "0.9"),
    ("/instagram/es/", "2026-07-16", "daily", "0.9"),
    ("/facebook/", "2026-07-17", "daily", "0.9"),
    ("/facebook/pt/", "2026-07-17", "daily", "0.9"),
    ("/facebook/es/", "2026-07-17", "daily", "0.9"),
    ("/vimeo/", "2026-07-17", "daily", "0.9"),
    ("/vimeo/pt/", "2026-07-17", "daily", "0.9"),
    ("/vimeo/es/", "2026-07-17", "daily", "0.9"),
    ("/dailymotion/", "2026-07-17", "daily", "0.9"),
    ("/dailymotion/pt/", "2026-07-17", "daily", "0.9"),
    ("/dailymotion/es/", "2026-07-17", "daily", "0.9"),
    ("/reddit/", "2026-07-17", "daily", "0.9"),
    ("/reddit/pt/", "2026-07-17", "daily", "0.9"),
    ("/reddit/es/", "2026-07-17", "daily", "0.9"),
    ("/pinterest/", "2026-07-17", "daily", "0.9"),
    ("/pinterest/pt/", "2026-07-17", "daily", "0.9"),
    ("/pinterest/es/", "2026-07-17", "daily", "0.9"),
    ("/linkedin/", "2026-08-12", "daily", "0.9"),
    ("/linkedin/pt/", "2026-08-12", "daily", "0.9"),
    ("/linkedin/es/", "2026-08-12", "daily", "0.9"),
    ("/kwai/", "2026-08-12", "daily", "0.9"),
    ("/kwai/pt/", "2026-08-12", "daily", "0.9"),
    ("/kwai/es/", "2026-08-12", "daily", "0.9"),
    ("/tiktok-mp3/", "2026-07-23", "daily", "0.8"),
    ("/tiktok-mp3/pt/", "2026-07-23", "daily", "0.8"),
    ("/tiktok-mp3/es/", "2026-07-23", "daily", "0.8"),
    ("/twitter-gif/", "2026-07-23", "daily", "0.8"),
    ("/twitter-gif/pt/", "2026-07-23", "daily", "0.8"),
    ("/twitter-gif/es/", "2026-07-23", "daily", "0.8"),
    ("/reddit-video-with-sound/", "2026-07-23", "daily", "0.8"),
    ("/reddit-video-with-sound/pt/", "2026-07-23", "daily", "0.8"),
    ("/reddit-video-with-sound/es/", "2026-07-23", "daily", "0.8"),
    ("/tiktok-no-watermark/", "2026-08-03", "daily", "0.8"),
    ("/tiktok-no-watermark/pt/", "2026-08-03", "daily", "0.8"),
    ("/tiktok-no-watermark/es/", "2026-08-03", "daily", "0.8"),
    ("/instagram-reels-no-watermark/", "2026-08-03", "daily", "0.8"),
    ("/instagram-reels-no-watermark/pt/", "2026-08-03", "daily", "0.8"),
    ("/instagram-reels-no-watermark/es/", "2026-08-03", "daily", "0.8"),
    ("/facebook-story-saver/", "2026-08-03", "daily", "0.8"),
    ("/facebook-story-saver/pt/", "2026-08-03", "daily", "0.8"),
    ("/facebook-story-saver/es/", "2026-08-03", "daily", "0.8"),
    ("/facebook-reels-downloader/", "2026-08-12", "daily", "0.8"),
    ("/facebook-reels-downloader/pt/", "2026-08-12", "daily", "0.8"),
    ("/facebook-reels-downloader/es/", "2026-08-12", "daily", "0.8"),
    ("/video-to-mp3/", "2026-08-12", "daily", "0.8"),
    ("/video-to-mp3/pt/", "2026-08-12", "daily", "0.8"),
    ("/video-to-mp3/es/", "2026-08-12", "daily", "0.8"),
    ("/instagram-audio/", "2026-08-12", "daily", "0.8"),
    ("/instagram-audio/pt/", "2026-08-12", "daily", "0.8"),
    ("/instagram-audio/es/", "2026-08-12", "daily", "0.8"),
    ("/pinterest-no-watermark/", "2026-08-12", "daily", "0.8"),
    ("/pinterest-no-watermark/pt/", "2026-08-12", "daily", "0.8"),
    ("/pinterest-no-watermark/es/", "2026-08-12", "daily", "0.8"),
    ("/faq", "2026-04-27", "monthly", "0.8"),
    ("/termos", "2026-04-27", "monthly", "0.3"),
    ("/privacidade", "2026-04-27", "monthly", "0.3"),
    ("/contato", "2026-04-27", "monthly", "0.3"),
)


def _sitemap_url_xml(path, lastmod, changefreq, priority, base_url):
    return (
        f"  <url>\n"
        f"    <loc>{base_url}{path}</loc>\n"
        f"    <lastmod>{lastmod}</lastmod>\n"
        f"    <changefreq>{changefreq}</changefreq>\n"
        f"    <priority>{priority}</priority>\n"
        f"  </url>"
    )


@app.route('/sitemap.xml')
def sitemap():
    base_url = get_site_base_url()
    entries = "\n".join(
        _sitemap_url_xml(path, lastmod, changefreq, priority, base_url)
        for path, lastmod, changefreq, priority in SITEMAP_PAGES
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f'{entries}\n'
        '</urlset>'
    )
    return xml, 200, {'Content-Type': 'application/xml'}


@app.route("/download-playlist", methods=["POST"])
@limiter.limit("5 per minute")
def download_playlist():
    import tempfile
    import zipfile

    payload = request.get_json(silent=True) or {}
    raw_url = payload.get("url", "")
    url, platform, error = detect_and_normalize_url(raw_url)
    if error:
        return jsonify({"error": error}), 400

    try:
        opts = base_ydl_opts(platform)
        opts["format"] = "best[ext=mp4]/best"
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if info.get("_type") != "playlist":
            return jsonify({"error": "This URL is not a playlist."}), 400

        entries = [e for e in info.get("entries", []) if e]
        if not entries:
            return jsonify({"error": "No videos found in playlist."}), 400

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
        tmp.close()
        written = 0
        with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, entry in enumerate(entries):
                try:
                    video_url = entry.get("webpage_url") or entry.get("original_url", "")
                    dl_opts = base_ydl_opts(platform)
                    dl_opts["format"] = "best[ext=mp4]/best"
                    dl_opts["outtmpl"] = os.path.join(DOWNLOAD_DIR, f"pl_{i:03d}.%(ext)s")
                    dl_opts["quiet"] = True
                    with yt_dlp.YoutubeDL(dl_opts) as ydl2:
                        vinfo = ydl2.extract_info(video_url, download=True)
                        fpath = ydl2.prepare_filename(vinfo)
                        base = os.path.splitext(fpath)[0]
                        for ext in (".mp4", ".mkv", ".webm"):
                            if os.path.exists(base + ext):
                                zf.write(base + ext, f"{i+1:02d}_{sanitize_filename(vinfo.get('title','video'))}{ext}")
                                os.remove(base + ext)
                                written += 1
                                break
                except Exception:
                    continue

        if written > 0:
            @after_this_request
            def cleanup(response):
                try:
                    os.remove(tmp.name)
                except Exception:
                    pass
                return response

            return send_file(tmp.name, as_attachment=True, download_name=f"{sanitize_filename(info.get('title','playlist'))}.zip")
        else:
            os.remove(tmp.name)
            return jsonify({"error": "Could not download any videos from the playlist."}), 400
    except Exception as exc:
        return jsonify({"error": map_yt_dlp_error(exc)}), 400


if __name__ == "__main__":
    debug_enabled = os.getenv("FLASK_DEBUG", "0") == "1"
    # Local dev only — production runs gunicorn under systemd on the Contabo VPS.
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=debug_enabled)
    