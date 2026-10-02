#!/usr/bin/env python3
"""
pull_video.py — download an online video and make it artifact-ready.

Pulls a video from a URL (YouTube, Vimeo, X, direct .mp4 links, and the
~1,800 other sites yt-dlp supports), optionally trims it, then re-encodes
it to a browser-safe H.264/AAC MP4 small enough to upload to a claude.ai
Artifact's asset store (15 MB per file). Also grabs a poster frame and
prints the <video> tag to drop into an artifact page.

Usage:
    python pull_video.py URL [--name hero] [--start 0:05 --end 0:20]
                             [--max-mb 14] [--height 720] [--mute] [--loop]
                             [--cookies-from safari]

Instagram / Facebook links: public posts often work as-is; if the site asks
for a login, the script automatically retries with your Safari session.

Output lands in ~/Downloads/Video Puller/ (change with --out):
    <name>.mp4         the web-ready video
    <name>-poster.jpg  a still for the <video poster=...>
    <name>.html        the embed snippet

Only pull videos you own or have the rights to use.
"""

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import imageio_ffmpeg
import yt_dlp

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
OUT_DIR = Path.home() / "Downloads" / "Video Puller"

SAFARI_HELP = """
Couldn't read Safari's cookies: macOS blocks this until you allow it.
  1. Open System Settings → Privacy & Security → Full Disk Access.
  2. Turn on the app you run this from (Terminal, or Claude).
  3. Quit and reopen that app, make sure you're logged in to Instagram/Facebook
     in Safari, then run the command again.
"""


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "video"


def to_seconds(ts: str | None) -> float | None:
    """'1:02:03', '2:05', or '75.5' -> seconds."""
    if ts is None:
        return None
    total = 0.0
    for part in ts.split(":"):
        total = total * 60 + float(part)
    return total


def download(url: str, workdir: Path, max_height: int, browser: str | None) -> tuple[Path, dict]:
    """Fetch the best single-file-or-merged stream at or under max_height."""
    opts = {
        "outtmpl": str(workdir / "source.%(ext)s"),
        "format": f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b",
        "merge_output_format": "mp4",
        "ffmpeg_location": FFMPEG,
        "noplaylist": True,
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
    }
    if browser:  # reuse your own logged-in session (Instagram, Facebook, private Vimeo)
        opts["cookiesfrombrowser"] = (browser,)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception as err:  # cookie-read failures surface as PermissionError, not DownloadError
        if browser == "safari" and re.search(r"permission|not permitted|binarycookies", str(err), re.I):
            sys.exit(SAFARI_HELP)
        needs_login = re.search(r"login|log in|cookies|private|rate.?limit|not available", str(err), re.I)
        if browser or not needs_login or not isinstance(err, yt_dlp.utils.DownloadError):
            raise
        print("  Site wants a login; retrying with your Safari session …")
        return download(url, workdir, max_height, "safari")
    files = [p for p in workdir.iterdir() if p.name.startswith("source.")]
    if not files:
        sys.exit("Download finished but no file was produced.")
    return files[0], info


IG_APP_ID = "936619743392459"  # the public app id instagram.com's own web client sends
SAFARI_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")


def fetch_info(url: str, browser: str | None = None) -> dict:
    """Metadata only (no download), with the same Safari fallback as download()."""
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
    if browser:
        opts["cookiesfrombrowser"] = (browser,)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as err:
        if browser == "safari" and re.search(r"permission|not permitted|binarycookies", str(err), re.I):
            sys.exit(SAFARI_HELP)
        if browser or not isinstance(err, yt_dlp.utils.DownloadError):
            raise
        return fetch_info(url, "safari")


def instagram_profile(username: str) -> dict:
    """Posts / followers / following for an Instagram account. Needs a login, so it
    tries logged-out first, then your Safari session. Returns {} if it can't."""
    import urllib.request

    if not re.fullmatch(r"[A-Za-z0-9._]{1,30}", username):
        return {}
    api =f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
    headers = {"X-IG-App-ID": IG_APP_ID, "User-Agent": SAFARI_UA}

    def call(extra: dict) -> dict:
        req = urllib.request.Request(api, headers={**headers, **extra})
        user = json.load(urllib.request.urlopen(req, timeout=15))["data"]["user"]
        return {
            "posts": user["edge_owner_to_timeline_media"]["count"],
            "followers": user["edge_followed_by"]["count"],
            "following": user["edge_follow"]["count"],
            "verified": bool(user.get("is_verified")),
        }

    try:
        return call({})
    except Exception:
        pass
    try:
        from yt_dlp.cookies import extract_cookies_from_browser
        jar = extract_cookies_from_browser("safari")
        cookie = "; ".join(f"{c.name}={c.value}" for c in jar if c.domain.endswith("instagram.com"))
        csrf = next((c.value for c in jar if c.name == "csrftoken" and c.domain.endswith("instagram.com")), "")
        if cookie:
            return call({"Cookie": cookie, "X-CSRFToken": csrf})
    except Exception:
        pass
    return {}


def social_stats(info: dict) -> dict:
    """Map yt-dlp metadata to the builder's fields. Missing values are left out."""
    site = (info.get("extractor_key") or info.get("extractor") or "").lower()
    platform = "ig" if "instagram" in site else "fb" if "facebook" in site else None

    handle = None
    uid = info.get("uploader_id")
    order = ("uploader_id", "channel", "uploader") if isinstance(uid, str) and uid.startswith("@") \
        else ("channel", "uploader_id", "uploader")  # Instagram puts the username in `channel`
    for key in order:
        v = info.get(key)
        if isinstance(v, str) and v and not v.isdigit() and " " not in v.strip():
            handle = v.strip()
            break
    name = info.get("uploader") or info.get("channel") or handle or ""

    stats = {
        "platform": platform,
        "handle": ("@" + handle.lstrip("@")) if handle else None,
        "name": name or None,
        "likes": info.get("like_count"),
        "comments": info.get("comment_count"),
        "shares": info.get("repost_count"),
        "views": info.get("view_count"),
        "followers": info.get("channel_follower_count"),
        "verified": info.get("channel_is_verified"),
    }
    if platform == "ig" and handle:
        prof = instagram_profile(handle.lstrip("@"))
        stats.update({k: v for k, v in prof.items() if v is not None})
    return {k: v for k, v in stats.items() if v is not None}


def probe_duration(path: Path) -> float:
    """Read duration from ffmpeg's banner (imageio-ffmpeg ships no ffprobe)."""
    out = subprocess.run([FFMPEG, "-i", str(path)], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out)
    if not m:
        sys.exit("Could not read the video's duration.")
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


def has_audio(path: Path) -> bool:
    out = subprocess.run([FFMPEG, "-i", str(path)], capture_output=True, text=True).stderr
    return "Audio:" in out


def encode(src: Path, dst: Path, start: float | None, end: float | None,
           height: int, max_mb: float, mute: bool) -> None:
    """Two-pass-free size targeting: pick a bitrate from the budget, then cap it."""
    full = probe_duration(src)
    s = start or 0.0
    e = min(end, full) if end else full
    duration = max(e - s, 0.5)

    audio = not mute and has_audio(src)
    audio_kbps = 96 if audio else 0
    budget_kbps = (max_mb * 8 * 1024) / duration * 0.92  # leave container headroom
    video_kbps = int(max(budget_kbps - audio_kbps, 150))
    video_kbps = min(video_kbps, 4000)  # no point going higher for the web

    cmd = [FFMPEG, "-y", "-loglevel", "error"]
    if start is not None:
        cmd += ["-ss", str(s)]
    cmd += ["-i", str(src)]
    if end is not None:
        cmd += ["-t", str(duration)]
    cmd += [
        # cap the SHORT side, so vertical (9:16) social videos aren't shrunk to a sliver
        "-vf", f"scale='if(gt(iw,ih),-2,min({height},iw))':'if(gt(iw,ih),min({height},ih),-2)'",
        "-c:v", "libx264", "-preset", "medium", "-profile:v", "high",
        "-pix_fmt", "yuv420p",
        "-b:v", f"{video_kbps}k", "-maxrate", f"{int(video_kbps * 1.5)}k",
        "-bufsize", f"{video_kbps * 2}k",
        "-movflags", "+faststart",  # lets the browser start playing before it's fully loaded
    ]
    cmd += ["-c:a", "aac", "-b:a", f"{audio_kbps}k"] if audio else ["-an"]
    cmd.append(str(dst))
    subprocess.run(cmd, check=True)

    size_mb = dst.stat().st_size / 1024 / 1024
    if size_mb > max_mb:
        print(f"  {size_mb:.1f} MB is over budget; retrying at 480p…")
        encode(src, dst, start, end, 480, max_mb * 0.9, mute)


def poster(video: Path, dst: Path) -> None:
    at = min(1.0, probe_duration(video) / 2)
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-ss", str(at), "-i", str(video),
                    "-frames:v", "1", "-q:v", "3", str(dst)], check=True)


def snippet(name: str, title: str, loop: bool) -> str:
    attrs = "autoplay muted loop playsinline" if loop else "controls playsinline preload=\"metadata\""
    return (
        f'<video {attrs}\n'
        f'       src="{name}.mp4" poster="{name}-poster.jpg"\n'
        f'       aria-label="{title}"\n'
        f'       style="width:100%;height:auto;border-radius:12px;display:block"></video>\n'
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--name", help="output file name (default: from the video title)")
    ap.add_argument("--start", help="trim start, e.g. 0:05")
    ap.add_argument("--end", help="trim end, e.g. 0:20")
    ap.add_argument("--height", type=int, default=720, help="max short side in px (default 720)")
    ap.add_argument("--cookies-from", metavar="BROWSER",
                    help="safari/chrome/firefox: use that browser's login up front "
                         "(otherwise Safari is tried automatically if a site asks for a login)")
    ap.add_argument("--max-mb", type=float, default=14.0, help="size budget in MB (default 14; asset limit is 15)")
    ap.add_argument("--out", help="folder to save into (default: ~/Downloads/Video Puller)")
    ap.add_argument("--no-reveal", action="store_true", help="don't show the file in Finder when done")
    ap.add_argument("--mute", action="store_true", help="drop audio (good for background/hero loops)")
    ap.add_argument("--loop", action="store_true", help="emit an autoplaying muted loop snippet instead of controls")
    ap.add_argument("--stats-only", action="store_true",
                    help="just print the post's likes/comments/handle/profile counts, no download")
    args = ap.parse_args()

    if args.stats_only:
        print(json.dumps(social_stats(fetch_info(args.url, args.cookies_from)), indent=2))
        return

    global OUT_DIR
    if args.out:
        OUT_DIR = Path(args.out).expanduser()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        print(f"Downloading {args.url} …")
        src, info = download(args.url, Path(tmp), 1920, args.cookies_from)
        title = info.get("title") or "Video"
        name = slugify(args.name or title)
        out = OUT_DIR / f"{name}.mp4"

        print("Encoding web-ready MP4 …")
        encode(src, out, to_seconds(args.start), to_seconds(args.end),
               args.height, args.max_mb, args.mute or args.loop)

    poster(out, OUT_DIR / f"{name}-poster.jpg")
    html = snippet(name, title, args.loop)
    (OUT_DIR / f"{name}.html").write_text(html)

    size_mb = out.stat().st_size / 1024 / 1024
    print(json.dumps({
        "video": str(out),
        "poster": str(OUT_DIR / f"{name}-poster.jpg"),
        "size_mb": round(size_mb, 2),
        "duration_s": round(probe_duration(out), 1),
        "title": title,
        "source": info.get("webpage_url", args.url),
        "stats": social_stats(info),
    }, indent=2))
    print("\nEmbed snippet:\n" + html)
    if sys.platform == "darwin" and not args.no_reveal:
        subprocess.run(["open", "-R", str(out)])  # show it in Finder, ready to upload


if __name__ == "__main__":
    main()
