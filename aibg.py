"""
AI-generated backgrounds with the Gemini API (Google AI Studio key).

Docs: https://ai.google.dev/gemini-api/docs/image-generation
The API key is stored only on this Mac, in ~/.config/ctv-spot-builder/gemini_api_key
(readable by you alone). It is never sent to the browser.
Gemini adds an invisible SynthID watermark to every generated image.
"""

import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = os.environ.get("GEMINI_API_BASE", "https://generativelanguage.googleapis.com/v1beta")
MODELS = [m for m in [os.environ.get("GEMINI_IMAGE_MODEL"), "gemini-3.1-flash-image", "gemini-2.5-flash-image"] if m]
KEY_FILE = Path.home() / ".config" / "ctv-spot-builder" / "gemini_api_key"
FFMPEG = None  # set by server.py (bundled ffmpeg from imageio-ffmpeg)

# Added to every prompt: the phone covers the middle, and the spot must stay brand-safe.
GUARDRAILS = ("Wide 16:9 background plate for a TV ad. Keep the centre calm and uncluttered (a phone will be "
              "placed there); put visual interest toward the edges. Photographic, soft natural light, shallow depth "
              "of field. No text, no letters, no logos, no brand marks, no watermarks, no people, no faces.")


class AiError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _key() -> str | None:
    try:
        return KEY_FILE.read_text().strip() or None
    except OSError:
        return None


def connected() -> bool:
    return _key() is not None


def _request(method: str, path: str, body: dict | None = None, key: str | None = None, timeout: int = 120) -> dict:
    key = key or _key()
    if not key:
        raise AiError("ai_not_connected", "Connect your Gemini API key first.")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "x-goog-api-key": key, "User-Agent": "CTV-Social-Spot-Builder/1.0",
        **({"Content-Type": "application/json"} if data else {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read() or b"{}").get("error", {}).get("message", "")[:240]
        except Exception:
            detail = ""
        if e.code in (401, 403) or "API key not valid" in detail:
            raise AiError("ai_bad_key", "Google didn't accept that Gemini API key (or it isn't allowed to use image "
                          "generation). Check it in Google AI Studio. " + detail)
        if e.code == 429:
            raise AiError("ai_rate_limited", "Gemini's usage limit was reached. Wait a minute, or ask your admin to "
                          "raise the project's quota.")
        if e.code == 404:
            raise AiError("ai_model_missing", detail or "That Gemini image model isn't available to this key.")
        if e.code == 400 and ("safety" in detail.lower() or "blocked" in detail.lower()):
            raise AiError("ai_blocked", "Gemini declined that prompt. Try describing the scene differently.")
        raise AiError("ai_error", f"Gemini error {e.code}. {detail}".strip())
    except (urllib.error.URLError, TimeoutError):
        raise AiError("ai_offline", "Couldn't reach Gemini. Check your internet connection.")


def save_key(key: str) -> None:
    key = (key or "").strip()
    if not key or len(key) > 256 or any(c.isspace() for c in key):
        raise AiError("bad_request", "Paste the API key exactly as shown in Google AI Studio.")
    _request("GET", "/models?pageSize=1", key=key, timeout=20)  # validate before saving
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEY_FILE.touch(mode=0o600, exist_ok=True)
    os.chmod(KEY_FILE, 0o600)
    KEY_FILE.write_text(key)


def forget_key() -> None:
    KEY_FILE.unlink(missing_ok=True)


def _find_image(x):
    """First base64 image anywhere in a Gemini response (works for generateContent and newer shapes)."""
    if isinstance(x, dict):
        mime = x.get("mimeType") or x.get("mime_type") or ""
        if isinstance(x.get("data"), str) and len(x["data"]) > 1000 and (mime.startswith("image/") or not mime):
            return x["data"]
        for v in x.values():
            if (found := _find_image(v)):
                return found
    elif isinstance(x, list):
        for v in x:
            if (found := _find_image(v)):
                return found
    return None


def _generate_one(prompt: str, ref: bytes | None) -> bytes:
    parts = [{"text": prompt}]
    if ref:
        parts.append({"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(ref).decode()}})
    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "16:9"}},
    }
    last = None
    for model in MODELS:
        try:
            resp = _request("POST", f"/models/{model}:generateContent", body)
        except AiError as e:
            if e.code == "ai_model_missing":
                last = e
                continue
            raise
        img = _find_image(resp)
        if img:
            return base64.b64decode(img)
        reason = json.dumps(resp.get("promptFeedback") or resp.get("candidates", [{}])[0].get("finishReason", ""))
        raise AiError("ai_blocked", "Gemini didn't return an image (" + reason.strip('"')[:80] + "). "
                      "Try describing the scene differently.")
    raise last or AiError("ai_model_missing", "No Gemini image model is available to this key.")


def _finish(raw: Path) -> tuple[Path, Path]:
    """1920×1080 sharp version + a softly blurred version."""
    sharp = raw.with_name(raw.stem + ".jpg") if raw.suffix != ".jpg" else raw.with_name(raw.stem + "s.jpg")
    soft = raw.with_name(raw.stem + "-bg.jpg")
    cover = "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(raw), "-vf", cover, "-q:v", "2", str(sharp)], check=True)
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(raw), "-vf", cover + ",gblur=sigma=18", "-q:v", "3",
                    str(soft)], check=True)
    return sharp, soft


def generate(prompt: str, out: Path, count: int = 2, ref: Path | None = None) -> list[dict]:
    import hashlib
    from concurrent.futures import ThreadPoolExecutor

    prompt = (prompt or "").strip()[:1200]
    if not prompt:
        raise AiError("bad_request", "Describe the background you want.")
    full = prompt + "\n\n" + GUARDRAILS + (
        "\n\nMatch the mood, lighting and colour palette of the attached video frame, but create a new scene."
        if ref else "")
    ref_bytes = None
    if ref and ref.is_file():
        tmp = ref.with_name(ref.stem + "-ref.jpg")
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(ref), "-vf", "scale=1024:-2", "-q:v", "4", str(tmp)],
                       check=True)
        ref_bytes = tmp.read_bytes()

    out.mkdir(parents=True, exist_ok=True)
    count = max(1, min(int(count or 1), 3))
    with ThreadPoolExecutor(max_workers=count) as pool:
        images = list(pool.map(lambda _: _generate_one(full, ref_bytes), range(count)))

    items = []
    for i, data in enumerate(images, 1):
        h = hashlib.sha1(data).hexdigest()[:12]
        raw = out / f"ai-{h}.png"
        raw.write_bytes(data)
        sharp, soft = _finish(raw)
        items.append({"label": f"AI background {i}", "file": sharp.name, "soft": soft.name})
    return items


if __name__ == "__main__":  # quick manual test: python aibg.py "prompt" OUT_DIR
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
    print(json.dumps(generate(sys.argv[1], Path(sys.argv[2]), 1)))
