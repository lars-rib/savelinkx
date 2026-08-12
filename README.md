# SaveLinkX

A self-hostable web app for downloading public videos from social platforms, built on
[Flask](https://flask.palletsprojects.com/) and [yt-dlp](https://github.com/yt-dlp/yt-dlp).
Paste a link, pick a quality, get an MP4 — no account, no installs for the end user.

Runs as a single Python process behind gunicorn. No database, no external services,
no telemetry beyond an optional Cloudflare Web Analytics beacon.

Live instance: **[savelinkx.com](https://www.savelinkx.com)**

## Features

- **9 platforms** — X (Twitter), TikTok, Instagram, YouTube, Facebook, Reddit, Vimeo,
  Dailymotion, Pinterest. Platform detection is by pasted URL, not by page.
- **Quality selector** — real formats reported by yt-dlp, with file sizes, plus
  audio-only MP3/M4A extraction.
- **Playlists** — list entries individually or download the whole thing as a ZIP.
- **Subtitles** — fetch subtitles as `.srt` in the language of your choice.
- **Thumbnails** — save the poster image on its own.
- **Trilingual UI** — English, Portuguese and Spanish, with `hreflang` wired up.
- **Progressive Web App** — installable, with a service worker.
- **Rate limited** — per-endpoint limits via Flask-Limiter (in-memory by default).
- **Optional cookie upload** — hand the server a `cookies.txt` when a platform demands
  a logged-in session for a specific video.

## Requirements

- Python 3.12
- **ffmpeg** on `PATH` — required to merge separate video and audio streams. Without
  it you are limited to pre-muxed formats.

## Quick start with Docker

```bash
docker compose up -d
```

The app is then on <http://localhost:5000>. ffmpeg is baked into the image.

## Manual install

```bash
git clone https://github.com/lars-rib/savelinkx.git
cd savelinkx
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python app.py
```

For production, use gunicorn — a long timeout matters because large merges are slow:

```bash
gunicorn app:app --workers 1 --timeout 300 --bind 0.0.0.0:5000
```

## Configuration

Everything is optional; the app boots with no environment set at all.

| Variable | Default | Purpose |
| --- | --- | --- |
| `PORT` | `5000` | Port to bind. |
| `SECRET_KEY` | random | Flask secret. Set it to keep sessions stable across restarts. |
| `SITE_URL` | request host | Canonical base URL used in `<link rel="canonical">`, `hreflang` and the sitemap. |
| `FLASK_DEBUG` | off | Debug mode. Never enable in production. |
| `LOG_LEVEL` | `INFO` | Standard Python log level. |
| `METADATA_CACHE_TTL_SECONDS` | — | How long to cache extracted video metadata. |
| `CF_BEACON_TOKEN` | — | Cloudflare Web Analytics token. Empty renders no script. |
| `WARP_PROXY` | — | Proxy URL for extractors that block datacenter IPs. |

### Per-platform cookies

Some platforms refuse anonymous requests from datacenter IPs. Point these at a
Netscape-format `cookies.txt` to authenticate server-side:

`TWITTER_COOKIES_FILE`, `TIKTOK_COOKIES_FILE`, `INSTAGRAM_COOKIES_FILE`,
`YOUTUBE_COOKIES_FILE`, `FACEBOOK_COOKIES_FILE`, `VIMEO_COOKIES_FILE`,
`DAILYMOTION_COOKIES_FILE`, `REDDIT_COOKIES_FILE`, `PINTEREST_COOKIES_FILE`

> Cookie files are live account credentials. They are gitignored — keep them that way.

## Notes on running this yourself

- **Keep yt-dlp current.** Extractors break whenever a platform changes its player.
  `requirements.txt` deliberately leaves `yt-dlp` unpinned, but `pip install -r` will
  not upgrade an already-installed copy — run `pip install -U yt-dlp` when extraction
  starts failing.
- **`curl_cffi`** is required for TikTok from a datacenter IP; it impersonates a real
  browser TLS fingerprint.
- **One worker.** Downloads are long-lived streaming responses. Scale with more
  instances rather than more threads per instance.

## Disclaimer

This is a link processing tool and does not host any files. Please respect copyright
law and use downloaded videos for personal use only. This project is not affiliated
with any of the platforms it supports. You are responsible for how you use it and for
complying with the terms of service of the sites you point it at.

## License

[MIT](LICENSE)
