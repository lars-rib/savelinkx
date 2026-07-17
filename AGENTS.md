# SaveLinkX — agent instructions

SaveLinkX (savelinkx.com) is a free multi-platform video downloader: Flask + yt-dlp
backend, hand-written HTML/CSS/JS frontend (no build step, no JS framework, no
database). Single-file backend (`app.py`), Jinja2 templates in `templates/`.

## Architecture

- **`app.py`** — all routes, the platform-detection dispatcher, and yt-dlp glue.
- **`templates/base.html`** — shared page chrome (CSS + JS) for every downloader
  landing page. Each language/platform page `{% extends "base.html" %}` and only
  supplies its unique `<head>` (title/meta/schema), copy blocks, FAQ, and a
  `{% block page_config %}` that fills `window.PAGE` (platform, lang, format
  ladder, UI strings). **Never duplicate CSS/JS into a page template again** —
  add to `base.html` and override via a block if a page genuinely differs.
- **Platform dispatcher** (`app.py`, `detect_and_normalize_url`): tries each
  entry in `PLATFORM_VALIDATORS` (host set → validator function) and returns
  `(normalized_url, platform, error)`. `/info` and `/download` both call this;
  `platform` selects the cookie-file env var in `base_ydl_opts(platform)`.
  **Adding a platform** = one validator function + one `PLATFORM_VALIDATORS`
  entry + N routes + N templates extending `base.html`. No other wiring needed.
- **`map_yt_dlp_error`** translates yt-dlp exception text into user-facing
  messages. Order matters: check specific substrings (`"private"`, `"no video
  could be found"`) before generic ones (`"empty media response"`) — yt-dlp
  appends a generic "use --cookies" hint to many unrelated errors, so a broad
  check placed too early silently shadows the specific, more helpful messages.
  This exact bug shipped once and was fixed in commit `f815b6e`.

## Live platforms

- **X/Twitter**: `/`, `/pt/`, `/es/`
- **TikTok**: `/tiktok/`, `/tiktok/pt/`, `/tiktok/es/` (needs `curl_cffi` for
  browser impersonation — TikTok blocks unimpersonated datacenter IPs)
- **Instagram**: `/instagram/`, `/instagram/pt/`, `/instagram/es/` — public
  posts/Reels/IGTV only; Stories are rejected up front (`/stories/` path)
  since they require login. **Verified working from the production VPS
  (datacenter IP) on 2026-07-16** — no cookies needed so far.
- **Deliberately excluded: YouTube** — downloader sites for YouTube risk a
  Google-wide ranking penalty for the whole domain.

## Known tech debt (read before adding platform #4)

The per-language UI strings (12 keys: `enter_link`, `preparing`, `checking`,
`fetch`, `err_unknown`, `downloading_label`, `downloading_msg`, `err_download`,
`ok_audio`, `ok_video`, `download_now`, `err_prefix`) and the MP4 quality-ladder
`formats` array are copy-pasted into every page's `page_config` block — 9 pages,
but only 3 distinct language sets. Adding a 4th platform means 3 more copies.
**Before adding platform #4, hoist `strings` and the shared format ladder into
`base.html` or a per-language include**, and let each page override only what
genuinely differs (e.g. TikTok's "(No Watermark)" format label).

Each platform validator (`normalize_and_validate_*_url`) also re-implements the
same empty/scheme/hostname preamble that `detect_and_normalize_url` already
does. A table-driven validator (hosts, path patterns, error strings as data)
would make a new platform a data row instead of a new function — worth doing
once a 4th platform is added.

## Deployment — READ THIS BEFORE RUNNING ANY DEPLOY COMMAND

Two machines are involved and their roles are **not interchangeable**:

- **Windows dev PC** (`C:\Users\lorra_lqkw536\Desktop\Projects\Savelinkx`) —
  where code changes are made. **Pushes** to GitHub.
- **Contabo VPS** (Ubuntu 24.04, `root@207.180.212.246`, hostname
  `vmi3306130`, app at `/opt/savelinkx`) — production. **Only ever pulls**
  from GitHub, never pushes. The repo is public, so the server needs no git
  credentials to pull.

Full deploy, run **on the VPS via SSH**:
```bash
cd /opt/savelinkx
git pull origin main
source venv/bin/activate && pip install -r requirements.txt && deactivate
systemctl restart savelinkx
```
Skip the `pip install` step if the change is template/route-only with no new
dependency — restart alone picks up the new code.

**Passwordless SSH is already set up** on this Windows machine: `ssh savelinkx`
connects with no password prompt (alias in `~/.ssh/config`, reusing an
existing key — `~/.ssh/shira_vps` — that already had access to this same VPS
from another project on the same box). An agent or script can run deploy
commands non-interactively, e.g. `ssh savelinkx "cd /opt/savelinkx && git pull origin main && systemctl restart savelinkx"`.
This alias only exists on this specific Windows machine's `~/.ssh/config` —
it is not committed to the repo (SSH keys/config must never be committed).

The app runs as systemd service `savelinkx` (gunicorn on 127.0.0.1:5000),
fronted by nginx with Let's Encrypt SSL. Other services on this VPS run in
Docker — unrelated, do not touch. `git status` before any destructive
operation on the server, same as anywhere else.

**A recurring mistake in this project's history: running the deploy commands
in the wrong shell** (Windows PowerShell instead of the SSH session, or vice
versa) — `cd /opt/savelinkx` fails immediately on Windows with a clear error,
so if that happens, the fix is reconnecting via `ssh root@207.180.212.246`
first, not troubleshooting the path.

## Git workflow

Feature branches + PRs, not direct pushes to `main` (early commits in this
repo's history went straight to `main` — that was a mistake, don't repeat it).
`gh` CLI is not installed on the dev machine; PRs are created via the GitHub
REST API using the stored git credential when `gh` isn't available.

## SEO / indexing

Google Search Console property is a **Domain property** for `savelinkx.com`
(covers www + non-www + all paths). Sitemap must be submitted as the **full
URL** `https://www.savelinkx.com/sitemap.xml` — a Domain property rejects a
bare path like `sitemap.xml`. After adding pages, use **URL Inspection →
Request Indexing** per new URL to speed up crawling.

## Testing without a live venv

The dev machine doesn't keep yt-dlp/Flask installed permanently. To verify
changes before deploying: create a throwaway venv, install
`Flask Flask-Limiter yt-dlp curl_cffi`, use `app.app.test_client()` to hit
routes offline, and — for anything touching extraction — run one real
`POST /info` against a known-public URL for each affected platform. Delete the
venv afterward; it's `.gitignore`d but no need to leave it around.
