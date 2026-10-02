# CTV Social Spot Builder

Turn a vertical social video into a 16:9 CTV spot. The video plays inside a phone, framed by your brand's background, logo, handle, profile stats and a QR call to action. Paste an Instagram, Facebook, YouTube or TikTok link and the builder pulls the video **and** fills in the likes, comments, handle and profile counts for you.

---

## 🚀 Get started (Mac, about 5 minutes, one time only)

**1. Download it**
Click **[Download the builder (ZIP)](https://github.com/ushah-ux/ctv-social-spot-builder/archive/refs/heads/main.zip)**, then double-click the ZIP in your Downloads folder to unzip it.

**2. Install it**
Open the **ctv-social-spot-builder-main** folder and double-click **Install Spot Builder.command**.

> **macOS says it "can't be opened"?** That's normal the first time. Right-click the file › **Open** › **Open**.
> On newer macOS: open **System Settings › Privacy & Security**, scroll down and click **Open Anyway**.

A Terminal window shows the progress (about a minute). If it says you need Python, it opens the download page: install Python, then double-click the installer again.

**3. Bookmark it**
The builder opens in your browser at **http://localhost:8765**. **Bookmark that page.** That's your builder from now on.

✅ **Done.** You never have to do this again. The builder starts by itself whenever you log in to your Mac, so the bookmark always works. You can delete the downloaded folder.

---

## Using it

1. Open your bookmark (**http://localhost:8765**).
2. Paste a post's link into **Pull from a link** and click **Pull video + stats**. Or upload a video file yourself with **Upload vertical video**.
3. Adjust the layers, then click **Export video**.

**If a pull is blocked**
- **The post needs a login:** log in to Instagram or Facebook in **Safari**. The builder uses your Safari login automatically.
- **"macOS is blocking it":** open **System Settings › Privacy & Security › Full Disk Access**, click **+**, and add the Python app the installer showed you (usually in `/Library/Frameworks/Python.framework/Versions/…/Resources/Python.app`). Then double-click the installer again.
- **Private post:** only works if your Safari login can see it.
- **Some stats stay empty:** Instagram only shares profile counts (posts, followers, following) with a login, and shares are rarely available on any platform. Anything missing keeps its current value, so you can type it in.
- **Rate-limited:** wait a few minutes and try again, or save the video yourself and upload it.

Only use videos you own or have permission to use.

## Tracking QR code scans

The builder can create a **tracked QR code** in our QR Code Generator PRO account, so you can see how many people scanned the spot.

**Connect once:** in the **QR card**, click **Connect account**, paste the API key from [QR Code Generator › Account Settings › API](https://app.qr-code-generator.com/api/) and click **Connect**. The key is saved only on your Mac.

**For each spot:**
1. In **QR tracking**, choose **Tracked (counts scans)**, then enter where the QR code should go (e.g. the store-locator page) and a name for it.
2. Leave **Add UTM tags** on, so the brand's own analytics can also see these visits.
3. Click **Create tracked QR**. The QR code in the spot now uses a `qrco.de` short link that counts each scan, then opens your page.
4. **Scan the QR code with your phone** to test it before exporting.

The scan count (total and unique) shows in the QR card. Click **Refresh** to update it. Full statistics (times, locations, devices, CSV export) are in your QR Code Generator dashboard under the code's **Details › Show full statistics**.

Each tracked code uses one of the account's dynamic QR codes, so make one per spot, not per draft.

**Static + UTM (no scan count):** choose **Static + UTM** instead to make a regular QR code with UTM tags (`utm_source=ctv`, `utm_medium=qr`, `utm_campaign=<campaign name>`). It's free and unlimited and needs no account, but QR Code Generator can't count scans of static codes. Visits from it show up in the **brand's website analytics** instead (e.g. Google Analytics, filtered by that campaign). Use it for spots that don't need scan reporting.

## Updating and removing

- **Update:** download the newest ZIP (step 1) and double-click **Install Spot Builder.command** again.
- **Remove:** double-click **Uninstall Spot Builder.command**.
- **Don't want it running in the background?** Skip the installer and double-click **Start Spot Builder.command** whenever you need it. It runs while its Terminal window is open.

Works in current Chrome, Edge and Safari. Needs Python 3.10 or newer (the installer checks).

---

## For developers

### How it works

| Piece | What it does |
|---|---|
| `web/index.html` | The builder: a single page, no build step. Canvas preview, layer controls, MediaRecorder export. |
| `server.py` | Local helper (Python standard library). Serves `web/` and adds `/api/pull`, `/api/stats` and `/api/video/<id>.mp4`. Listens on `127.0.0.1` only and refuses cross-site requests. |
| `qrcg.py` | Client for the [QR Code Generator PRO API](https://dev.qrcg.com/): creates dynamic URL codes and reads scan totals. The API key lives in `~/.config/ctv-spot-builder/qrcg_api_key` (mode 600), never in the browser. Endpoints: `/api/qr/status`, `/api/qr/key`, `/api/qr/forget`, `/api/qr/create`, `/api/qr/scans/<id>`. |
| `puller/pull_video.py` | Downloads with [yt-dlp](https://github.com/yt-dlp/yt-dlp), converts to a browser-safe H.264 MP4 with a bundled ffmpeg, and reads the post's stats. Also works on its own from the command line. |

Pulled videos are kept in `cache/` (git-ignored) and deleted after a day.

### Without the helper

`web/index.html` also works as a static page (opened directly, or on GitHub Pages or any web host). Everything except **Pull from a link** works there. Users upload their video file instead, and the **Pull not working? Help** pop-up explains how to start the helper.

### Command line

```bash
.venv/bin/python puller/pull_video.py "https://www.instagram.com/reel/XXXX/" --end 0:15
.venv/bin/python puller/pull_video.py "https://www.instagram.com/reel/XXXX/" --stats-only
```

Videos are saved to `~/Downloads/Video Puller/`. Set up the environment first with `bash start.sh`, which also runs the builder on demand. Run with `--help` for trimming, muting and size options.

## Notes

- Only pull videos you own or have permission to use, and follow each platform's terms.
- The default layers use Kirkland's branding (logo, background, handle). Keep this repo **private** unless you replace them with assets you're allowed to publish.
- Export records in real time in the browser: a 15-second spot takes about 15 seconds. Chrome and Safari export MP4; browsers that can't fall back to WebM.
