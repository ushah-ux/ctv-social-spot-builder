# CTV Social Spot Builder

Turn a vertical social video into a 16:9 CTV spot: the video plays inside a phone, framed by your brand's background, logo, handle, profile stats and a QR call to action. Edit every layer, then export an MP4 or a PNG still.

Paste an Instagram, Facebook, YouTube or TikTok link and the builder pulls the video **and** fills in likes, comments, handle and (when the platform shares them) profile counts.

## Quick start (macOS)

1. Download the repo (**Code › Download ZIP**, then unzip) or `git clone` it.
2. Double-click **`Start Spot Builder.command`**.
   The first time, macOS may block it: right-click it › **Open** › **Open**. You only do this once.
3. The builder opens at **http://localhost:8765**. Keep the Terminal window that opens; close it (or press Ctrl+C) to stop.

The first start sets up a private Python environment (about a minute). After that it starts in seconds. Double-clicking again while it's running just reopens the page.

Prefer Terminal? `bash start.sh` in the repo folder does the same thing.

Needs Python 3.10+ ([python.org/downloads/macos](https://www.python.org/downloads/macos/)) and a current Chrome, Edge or Safari.

## How it works

| Piece | What it does |
|---|---|
| `web/index.html` | The builder: a single page, no build step. Canvas preview, layer controls, MediaRecorder export. |
| `server.py` | Local helper (Python standard library). Serves `web/` and adds `/api/pull`, `/api/stats` and `/api/video/<id>.mp4`. Listens on `127.0.0.1` only and refuses cross-site requests. |
| `puller/pull_video.py` | Downloads with [yt-dlp](https://github.com/yt-dlp/yt-dlp), converts to a browser-safe H.264 MP4 with a bundled ffmpeg, and reads the post's stats. Also works on its own from the command line. |

Pulled videos are kept in `cache/` (git-ignored) and deleted after a day.

### Without the helper

`web/index.html` also works as a static page (opened directly, or on GitHub Pages or any web host). Everything except **Pull from a link** works there. Users upload their video file instead, and the **Pull not working? Help** pop-up explains how to start the helper.

## Instagram and Facebook

- Public posts usually download without a login.
- If a post needs one, the puller automatically retries with your **Safari** login. Be logged in to Instagram/Facebook in Safari.
- macOS blocks reading Safari's cookies until you allow it: **System Settings › Privacy & Security › Full Disk Access**, turn on **Terminal**, then restart `./start.sh`.
- Instagram profile counts (posts, followers, following) need that login too. Shares are rarely shared by any platform. Anything missing stays as it was, so you can type it in.

## Command line

```bash
.venv/bin/python puller/pull_video.py "https://www.instagram.com/reel/XXXX/" --end 0:15
.venv/bin/python puller/pull_video.py "https://www.instagram.com/reel/XXXX/" --stats-only
```

Videos are saved to `~/Downloads/Video Puller/`. Run with `--help` for trimming, muting and size options.

## Notes

- Only pull videos you own or have permission to use, and follow each platform's terms.
- The default layers use Kirkland's branding (logo, background, handle). Keep this repo **private** unless you replace them with assets you're allowed to publish.
- Export records in real time in the browser: a 15-second spot takes about 15 seconds. Chrome and Safari export MP4; browsers that can't fall back to WebM.
