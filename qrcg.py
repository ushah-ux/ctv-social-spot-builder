"""
QR Code Generator PRO (qr-code-generator.com) API client for the builder.

Creates dynamic URL codes (their qrco.de short link tracks every scan) and reads
scan counts. API docs: https://dev.qrcg.com/  ·  key: Account Settings › API.

The API key is stored only on this Mac, in ~/.config/ctv-spot-builder/qrcg_api_key
(readable by you alone). It is never sent to the browser.
"""

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

API = os.environ.get("QRCG_API_BASE", "https://api.qrcg.com/v3-preview")
KEY_FILE = Path.home() / ".config" / "ctv-spot-builder" / "qrcg_api_key"
UA = "CTV-Social-Spot-Builder/1.0"


class QrcgError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _key() -> str | None:
    try:
        k = KEY_FILE.read_text().strip()
        return k or None
    except OSError:
        return None


def connected() -> bool:
    return _key() is not None


def _call(method: str, path: str, body: dict | None = None, key: str | None = None) -> dict:
    key = key or _key()
    if not key:
        raise QrcgError("qr_not_connected", "Connect your QR Code Generator account first.")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": f"Key {key}",
        "User-Agent": UA,  # the API refuses requests without one
        "Accept": "application/json",
        **({"Content-Type": "application/json"} if data else {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = (json.loads(e.read() or b"{}").get("message") or "")[:200]
        except Exception:
            pass
        if e.code == 401:
            raise QrcgError("qr_bad_key", "QR Code Generator didn't accept that API key. Copy it again from Account Settings › API.")
        if e.code in (402, 403):
            raise QrcgError("qr_no_access", "Your QR Code Generator plan doesn't allow this (API access or dynamic-code limit). "
                            + detail)
        if e.code == 429:
            raise QrcgError("qr_rate_limited", "Too many requests to QR Code Generator. Wait a minute and try again.")
        if e.code == 404:
            raise QrcgError("qr_not_found", "That QR code isn't in your QR Code Generator account any more.")
        raise QrcgError("qr_error", f"QR Code Generator error {e.code}. {detail}".strip())
    except (urllib.error.URLError, TimeoutError):
        raise QrcgError("qr_offline", "Couldn't reach QR Code Generator. Check your internet connection.")


def save_key(key: str) -> None:
    key = (key or "").strip()
    if not key or len(key) > 512 or any(c.isspace() for c in key):
        raise QrcgError("bad_request", "Paste the API key exactly as shown in Account Settings › API.")
    _call("GET", "/account/entitlements", key=key)  # validate before saving
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.touch(mode=0o600, exist_ok=True)
    os.chmod(KEY_FILE, 0o600)
    KEY_FILE.write_text(key)


def forget_key() -> None:
    KEY_FILE.unlink(missing_ok=True)


def create(url: str, title: str) -> dict:
    qr = _call("POST", "/qrcodes", {"type": "url", "url": url, "title": title[:150]})
    if not qr.get("shortUrl") or qr.get("id") is None:
        raise QrcgError("qr_error", "QR Code Generator didn't return a short link for the new code.")
    return {"id": qr["id"], "shortUrl": qr["shortUrl"], "title": qr.get("title", title), "url": url}


def scans(qr_id: str) -> dict:
    t = _call("GET", f"/qrcodes/{qr_id}/scans/total")
    return {"total": int(t.get("total") or 0), "unique": int(t.get("unique") or 0)}
