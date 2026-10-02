#!/usr/bin/env python3
"""
Local server for the CTV Social Spot Builder.

Serves web/ and adds three endpoints the builder uses to pull videos by link:
  GET  /api/health            -> {"ok": true}
  POST /api/pull   {url, start?, end?, mute?} -> {id, filename, title, size_bytes, stats, video_url}
  POST /api/stats  {url}      -> {platform, handle, likes, comments, ...}
  GET  /api/video/<id>.mp4    -> the pulled MP4
  POST /api/brand  {url, site?} -> {handle, name, site, items: [{kind, label, url}]}
  GET  /api/asset/<dir>/<file> -> a pulled brand image
  AI backgrounds (Gemini API, see aibg.py):
  GET  /api/ai/status / POST /api/ai/key {key} / POST /api/ai/forget
  POST /api/ai/background {prompt, count?, ref?} -> {items: [{label, url, soft_url}]}
  QR tracking (QR Code Generator PRO, see qrcg.py):
  GET  /api/qr/status         -> {connected}
  POST /api/qr/key  {key}     -> save + validate the API key (stored on this Mac only)
  POST /api/qr/forget         -> remove the saved key
  POST /api/qr/create {url, title} -> {id, shortUrl, title, url}
  GET  /api/qr/scans/<id>     -> {total, unique}

Listens on 127.0.0.1 only. Run with ./start.sh (or: python server.py).
"""

import json
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import aibg
import qrcg

try:
    import imageio_ffmpeg
    aibg.FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:  # only needed for AI backgrounds
    pass

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
CACHE = ROOT / "cache"
PULLER = ROOT / "puller" / "pull_video.py"
BRAND = ROOT / "puller" / "brand.py"
PORT = int(os.environ.get("PORT", "8765"))

ID_RE = re.compile(r"^v-[0-9-]{1,40}$")
ASSET_RE = re.compile(r"^b-[0-9-]{1,40}/[a-z]+-[0-9a-f]{12}(-bg)?\.(png|jpg|svg)$")
ASSET_TYPES = {"png": "image/png", "jpg": "image/jpeg", "svg": "image/svg+xml"}
QR_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
TS_RE = re.compile(r"^\d{1,2}(:\d{1,2}){0,2}(\.\d+)?$")
LOCAL_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
_lock = threading.Lock()


def run_puller(args: list[str], timeout: int) -> str:
    run = subprocess.run([sys.executable, str(PULLER), *args], capture_output=True, text=True, timeout=timeout)
    out = run.stdout + run.stderr
    if run.returncode != 0:
        if "Full Disk Access" in out:
            if os.environ.get("SPOT_BUILDER_SERVICE") == "1":  # installed, running in the background
                fix = ("click +, add " + os.environ.get("SPOT_BUILDER_FDA_APP", "Python")
                       + ", then run Install Spot Builder.command again")
            else:
                fix = "turn on Terminal, then start the builder again"
            raise PullError("safari_cookies_blocked",
                            "This post needs your Safari login, and macOS is blocking it. Open System Settings › "
                            "Privacy & Security › Full Disk Access, " + fix + ".")
        tail = [l for l in out.strip().splitlines() if l.strip()][-1:] or ["unknown error"]
        raise PullError("pull_failed", re.sub(r"^.*?ERROR:\s*", "", tail[0])[:300])
    return run.stdout


class PullError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def check_url(url) -> str:
    if not isinstance(url, str) or not re.match(r"^https?://", url):
        raise PullError("bad_request", "Paste a full link that starts with https://")
    return url


def prune_cache() -> None:
    cutoff = time.time() - 86400
    for f in CACHE.glob("*"):
        if f.is_file() and f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)
    prune_dirs()


def pull(body: dict) -> dict:
    url = check_url(body.get("url"))
    args = [url, "--out", str(CACHE), "--no-reveal"]
    for key in ("start", "end"):
        v = body.get(key)
        if v:
            if not isinstance(v, str) or not TS_RE.match(v):
                raise PullError("bad_request", "Write times like 0:05 or 1:20.")
            args += [f"--{key}", v]
    if body.get("mute"):
        args.append("--mute")

    CACHE.mkdir(exist_ok=True)
    with _lock:  # one name per second; keep pulls from colliding
        prune_cache()
        name = "v-" + time.strftime("%Y%m%d-%H%M%S")
        time.sleep(1)
    out = run_puller(args + ["--name", name], timeout=300)
    info, _ = json.JSONDecoder().raw_decode(out[out.index('{\n  "video"'):])
    video = Path(info["video"])
    return {
        "id": video.stem,
        "filename": video.name,
        "title": info.get("title"),
        "duration_s": info.get("duration_s"),
        "size_bytes": video.stat().st_size,
        "stats": info.get("stats", {}),
        "video_url": f"/api/video/{video.stem}.mp4",
    }


def brand(body: dict) -> dict:
    url = check_url(body.get("url"))
    site = body.get("site") or None
    if site is not None:
        site = check_url(site if re.match(r"^https?://", site) else "https://" + str(site))
    CACHE.mkdir(exist_ok=True)
    with _lock:
        prune_cache()
        folder = "b-" + time.strftime("%Y%m%d-%H%M%S")
        time.sleep(1)
    out = CACHE / folder
    run = subprocess.run([sys.executable, str(BRAND), url, str(out)] + ([site] if site else []),
                         capture_output=True, text=True, timeout=150)
    if run.returncode != 0:
        msg = run.stdout + run.stderr
        if "Full Disk Access" in msg:
            raise PullError("safari_cookies_blocked", "This post needs your Safari login, and macOS is blocking it. "
                            "See 'If a pull is blocked' in the help.")
        tail = [l for l in msg.strip().splitlines() if l.strip()][-1:] or ["unknown error"]
        raise PullError("pull_failed", re.sub(r"^.*?ERROR:\s*", "", tail[0])[:300])
    kit = json.loads(run.stdout.strip().splitlines()[-1])
    kit["items"] = [{**i, "url": f"/api/asset/{folder}/{i.pop('file')}"} for i in kit["items"]]
    return kit


def ai_background(body: dict) -> dict:
    if not aibg.FFMPEG:
        raise PullError("server_error", "The video tools aren't installed. Run the installer again.")
    ref = None
    r = body.get("ref")
    if isinstance(r, str):  # an asset we served, e.g. the blurred video frame → use its sharp original
        m = re.fullmatch(r"/api/asset/(b-[0-9-]{1,40})/([a-z]+-[0-9a-f]{12})-bg\.jpg", r)
        if m:
            for ext in ("png", "jpg"):
                cand = CACHE / m.group(1) / f"{m.group(2)}.{ext}"
                if cand.is_file():
                    ref = cand
                    break
    CACHE.mkdir(exist_ok=True)
    with _lock:
        prune_cache()
        folder = "b-" + time.strftime("%Y%m%d-%H%M%S")
        time.sleep(1)
    items = aibg.generate(body.get("prompt") if isinstance(body.get("prompt"), str) else "",
                          CACHE / folder, body.get("count") or 2, ref)
    return {"items": [{"label": i["label"], "url": f"/api/asset/{folder}/{i['file']}",
                       "soft_url": f"/api/asset/{folder}/{i['soft']}"} for i in items]}


def prune_dirs() -> None:
    cutoff = time.time() - 86400
    for d in CACHE.glob("b-*"):
        if d.is_dir() and d.stat().st_mtime < cutoff:
            for f in d.iterdir():
                f.unlink(missing_ok=True)
            d.rmdir()


def stats(body: dict) -> dict:
    return json.loads(run_puller([check_url(body.get("url")), "--stats-only"], timeout=120))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB), **kw)

    def log_message(self, fmt, *args):
        if "/api/" in (self.path or ""):
            sys.stderr.write("  " + fmt % args + "\n")

    # Only answer requests addressed to this machine (blocks DNS-rebinding tricks)
    def _local(self) -> bool:
        if self.headers.get("Host") not in LOCAL_HOSTS:
            self.send_error(403, "Use http://localhost:%d" % PORT)
            return False
        return True

    def _json(self, status: int, data: dict) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._local():
            return
        if self.path == "/api/health":
            return self._json(200, {"ok": True})
        if self.path == "/api/ai/status":
            return self._json(200, {"connected": aibg.connected()})
        if self.path == "/api/qr/status":
            return self._json(200, {"connected": qrcg.connected()})
        m = re.fullmatch(r"/api/qr/scans/([^/]+)", self.path)
        if m:
            if not QR_ID_RE.match(m.group(1)):
                return self._json(400, {"code": "bad_request", "message": "Unknown QR code."})
            try:
                return self._json(200, qrcg.scans(m.group(1)))
            except qrcg.QrcgError as e:
                return self._json(502, {"code": e.code, "message": str(e)})
        m = re.fullmatch(r"/api/asset/(.+)", self.path)
        if m:
            rel = m.group(1)
            f = CACHE / rel
            if not ASSET_RE.match(rel) or not f.is_file():
                return self._json(404, {"code": "not_found", "message": "That image is gone; pull the brand look again."})
            data = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ASSET_TYPES[rel.rsplit(".", 1)[1]])
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")  # inert SVGs
            self.end_headers()
            self.wfile.write(data)
            return
        m = re.fullmatch(r"/api/video/([^/]+)\.mp4", self.path)
        if m:
            vid = m.group(1)
            f = CACHE / f"{vid}.mp4"
            if not ID_RE.match(vid) or not f.is_file():
                return self._json(404, {"code": "not_found", "message": "That video is gone; pull it again."})
            data = f.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        return super().do_GET()

    def do_POST(self):
        if not self._local():
            return
        # Only the builder itself may call the API: same-origin JSON requests
        origin = self.headers.get("Origin")
        if origin and origin.split("://", 1)[-1] not in LOCAL_HOSTS:
            return self._json(403, {"code": "forbidden", "message": "Cross-site request refused."})
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._json(415, {"code": "bad_request", "message": "Send JSON."})
        try:
            length = min(int(self.headers.get("Content-Length") or 0), 64 * 1024)
            body = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/pull":
                return self._json(200, pull(body))
            if self.path == "/api/stats":
                return self._json(200, stats(body))
            if self.path == "/api/brand":
                return self._json(200, brand(body))
            if self.path == "/api/ai/key":
                aibg.save_key(body.get("key"))
                return self._json(200, {"connected": True})
            if self.path == "/api/ai/forget":
                aibg.forget_key()
                return self._json(200, {"connected": False})
            if self.path == "/api/ai/background":
                return self._json(200, ai_background(body))
            if self.path == "/api/qr/key":
                qrcg.save_key(body.get("key"))
                return self._json(200, {"connected": True})
            if self.path == "/api/qr/forget":
                qrcg.forget_key()
                return self._json(200, {"connected": False})
            if self.path == "/api/qr/create":
                url = check_url(body.get("url"))
                title = body.get("title") if isinstance(body.get("title"), str) else ""
                return self._json(200, qrcg.create(url, title.strip() or "CTV spot"))
            return self._json(404, {"code": "not_found", "message": "Unknown endpoint."})
        except aibg.AiError as e:
            return self._json(400 if e.code == "bad_request" else 502, {"code": e.code, "message": str(e)})
        except qrcg.QrcgError as e:
            return self._json(400 if e.code == "bad_request" else 502, {"code": e.code, "message": str(e)})
        except PullError as e:
            return self._json(400 if e.code == "bad_request" else 502, {"code": e.code, "message": str(e)})
        except subprocess.TimeoutExpired:
            return self._json(504, {"code": "timeout", "message": "That took too long. Try a shorter clip or try again."})
        except Exception as e:  # keep the server up whatever happens
            return self._json(500, {"code": "server_error", "message": str(e)[:300]})


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}/"
    print(f"CTV Social Spot Builder is running at {url}\nLeave this window open; press Ctrl+C to stop.")
    if os.environ.get("NO_BROWSER") != "1":
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
