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
- **Also live** (added later via OpenCode): Facebook, Vimeo, Dailymotion,
  Reddit, Pinterest — 9 platforms total, each in EN/PT/ES.
- **YouTube** — was originally excluded (downloader sites for YouTube risk a
  Google-wide ranking penalty for the whole domain). Now implemented but
  **partially blocked**; see the YouTube section below before touching it.

## YouTube: PO tokens + IP blocking (read this before debugging)

Two *independent* problems. Measured on the production VPS, 2026-07-19:

**1. Quality cap — SOLVED.** YouTube requires a GVS PO Token for anything above
360p. A `bgutil-ytdlp-pot-provider` Docker container supplies them:

```bash
docker run -d --name bgutil-pot --restart unless-stopped \
  -p 127.0.0.1:4416:4416 brainicism/bgutil-ytdlp-pot-provider:latest
# plugin side, inside the app venv:
venv/bin/pip install -U bgutil-ytdlp-pot-provider
```
The container is **localhost-bound only** (never expose 4416 publicly). yt-dlp
auto-discovers it at `http://127.0.0.1:4416`; confirm with
`venv/bin/yt-dlp -v <url> 2>&1 | grep pot` — you should see
`bgutil:http-... (external)` and "Generating a gvs PO Token".

**Do NOT pin `player_client`.** The old `["ios","android","web"]` set was a
pre-PO-token workaround and now *caps quality at 360p*. Measured, same video,
same provider: pinned → 360p / 4 formats; **default clients → 2160p / 36
formats**. `base_ydl_opts()` deliberately leaves it unset.

**YouTube cookies are NOT needed** — results are identical with and without
them. `/opt/savelinkx/cookies/youtube.txt` and the `YOUTUBE_COOKIES_FILE` env
var can be removed; a live Google session on a VPS is a ban risk for that
account and an unnecessary credential to hold. `cookies/` is gitignored.

**2. Bot detection — SOLVED with Cloudflare WARP (free).** YouTube blocks the
Contabo prefix by ASN: direct egress fails on ~5 of 6 videos with "Sign in to
confirm you're not a bot". Verified IP-level, not config: every player client
fails on a blocked video, cookies make no difference, and **IPv6 fails
identically** — the whole `/64` is flagged, so rotating within it is pointless.

Fix is `cloudflare-warp` in **proxy mode** (no system routing changes, so the
other Docker services are unaffected):
```bash
warp-cli --accept-tos registration new
warp-cli --accept-tos mode proxy
warp-cli --accept-tos proxy port 40000
warp-cli --accept-tos connect
```
`warp-svc` is enabled at boot and the mode persists. `base_ydl_opts()` sends
YouTube traffic through it via `YOUTUBE_PROXY` (default
`socks5://127.0.0.1:40000`); set that env var to swap or empty it to disable.

Measured on the VPS: **direct → 1/6 videos, capped 360p. WARP → 10/10 videos,
up to 2160p.** Only YouTube is proxied; all other platforms egress directly.

**If YouTube breaks again, check WARP first**: `warp-cli status` should say
Connected, and `curl -s --socks5 127.0.0.1:40000 https://www.cloudflare.com/cdn-cgi/trace | grep warp`
should print `warp=on`. Do not re-litigate with client/cookie/impersonation
permutations — that path burned 15+ commits; the variables that actually
matter are the PO provider and the proxy.

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

## Server operations (installed 2026-07-19)

The VPS is **shared with ~12 other Docker containers** (NocoDB, Paperclip,
Syncthing, netdata, etc.) on a single 72G disk. A runaway download here takes
down unrelated services, so treat disk as a shared resource.

**Cron jobs** (`crontab -l` as root):
- `*/15 * * * * /usr/local/bin/savelinkx-maint.sh` — purges
  `downloads/` files older than 15 min and logs a syslog warning if `/` goes
  past 85%. Needed because the app deletes served files with
  `threading.Timer(5, os.remove)`, and that timer dies if gunicorn restarts
  mid-download — a real leak was found (36MB orphan sitting for 5 days).
- `17 4 * * 1 /usr/local/bin/savelinkx-update-ytdlp.sh` — weekly yt-dlp
  upgrade + restart only if the version actually changed. **This is the main
  thing keeping extraction alive**: `requirements.txt` leaves yt-dlp unpinned,
  and `pip install -r` will NOT upgrade an already-installed package.

**Gunicorn**: `--workers 6 --timeout 300 --graceful-timeout 30
--max-requests 200 --max-requests-jitter 40`. Downloads are *blocking*, so
worker count is the concurrency ceiling — at the previous 2 workers, two
simultaneous 4K downloads hung the whole site. Each worker is ~48MB.
`--max-requests` recycles workers to contain memory creep from yt-dlp.

**Capacity (measured 2026-07-19)**: 4 cores, 7.8G RAM (~2.7G available),
disk 74% after reclaiming 12G of dangling Docker images. No upgrade needed.
Upgrade triggers: RAM available under ~1G sustained, disk past 85% *after*
the cleanup cron, or steady-state load above ~4 with no test traffic running.
Note that a high `load average` is often just someone running extraction
tests — check per-container CPU (`docker stats`) before concluding the box
is undersized.

**Reclaiming disk**: `docker image prune -f` and `docker builder prune -f` are
safe (dangling layers only). Avoid `docker system prune -a`, which would drop
images the 12 running containers may need to recreate.

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
