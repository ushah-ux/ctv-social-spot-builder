#!/usr/bin/env python3
"""
Local server for the CTV Social Spot Builder.

Serves web/ and adds three endpoints the builder uses to pull videos by link:
  GET  /api/health            -> {"ok": true}
  POST /api/pull   {url, start?, end?, mute?} -> {id, filename, title, size_bytes, stats, video_url}
  POST /api/stats  {url}      -> {platform, handle, likes, comments, ...}
  GET  /api/video/<id>.mp4    -> the pulled MP4

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

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
CACHE = ROOT / "cache"
PULLER = ROOT / "puller" / "pull_video.py"
PORT = int(os.environ.get("PORT", "8765"))

ID_RE = re.compile(r"^v-[0-9-]{1,40}$")
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
            return self._json(404, {"code": "not_found", "message": "Unknown endpoint."})
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
