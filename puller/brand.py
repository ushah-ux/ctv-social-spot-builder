"""
Pull a brand's look from a social post: profile picture, logo (from the brand's
website), and background candidates (blurred video frame, channel banner,
website image). Everything is downloaded into a local folder so the builder can
draw it on its canvas.

    from brand import brand_kit
    kit = brand_kit("https://www.instagram.com/reel/…", out_dir, site="https://brand.com")
"""

import hashlib
import ipaddress
import json
import re
import socket
import subprocess
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

import pull_video as pv

UA = pv.SAFARI_UA
MAX_IMAGE = 8 * 1024 * 1024
MAX_HTML = 3 * 1024 * 1024
IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif",
               "image/svg+xml": "svg", "image/x-icon": "ico", "image/vnd.microsoft.icon": "ico"}


# ---------- safe fetching ----------

def _public_host(url: str) -> bool:
    """Only fetch from the public internet (never this Mac or the office network)."""
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    try:
        for info in socket.getaddrinfo(p.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
    except (socket.gaierror, ValueError):
        return False
    return True


def _get(url: str, limit: int) -> tuple[bytes, str]:
    if not _public_host(url):
        raise ValueError("not a public web address")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=15) as r:
        if not _public_host(r.geturl()):
            raise ValueError("redirected to a non-public address")
        data = r.read(limit + 1)
        if len(data) > limit:
            raise ValueError("file too large")
        return data, (r.headers.get_content_type() or "").lower()


def _save_image(url: str, out: Path, prefix: str) -> Path | None:
    try:
        data, ctype = _get(url, MAX_IMAGE)
    except Exception:
        return None
    ext = IMAGE_TYPES.get(ctype)
    if not ext and (data[:5] in (b"<?xml", b"<svg ") or b"<svg" in data[:300]):
        ext = "svg"
    if not ext:
        return None
    name = f"{prefix}-{hashlib.sha1(url.encode()).hexdigest()[:12]}.{ext}"
    path = out / name
    path.write_bytes(data)
    return path


def _to_png(src: Path) -> Path | None:
    """webp/ico/gif → png so every browser can draw it."""
    if src.suffix in (".png", ".jpg", ".svg"):
        return src
    dst = src.with_suffix(".png")
    r = subprocess.run([pv.FFMPEG, "-y", "-loglevel", "error", "-i", str(src), "-frames:v", "1", str(dst)])
    return dst if r.returncode == 0 and dst.exists() else None


def _content_crop(src: Path) -> str:
    """ffmpeg crop filter that trims black bars (letterboxed banners, padded frames)."""
    r = subprocess.run([pv.FFMPEG, "-hide_banner", "-i", str(src), "-vf", "cropdetect=limit=24:round=2",
                        "-frames:v", "1", "-f", "null", "-"], capture_output=True, text=True)
    m = re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", r.stderr)
    if not m:
        return ""
    w, h, x, y = map(int, m[-1])
    return f"crop={w}:{h}:{x}:{y}," if w > 64 and h > 64 else ""


def _blurred_bg(src: Path) -> Path | None:
    """Trim black bars, cover-crop to 1920×1080, blur heavily and darken slightly,
    like the builder's default background."""
    dst = src.with_name(src.stem + "-bg.jpg")
    vf = (_content_crop(src) + "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,"
          "gblur=sigma=60:steps=3,eq=brightness=-0.05:saturation=1.15")
    r = subprocess.run([pv.FFMPEG, "-y", "-loglevel", "error", "-i", str(src), "-vf", vf,
                        "-frames:v", "1", "-q:v", "3", str(dst)])
    return dst if r.returncode == 0 and dst.exists() else None


def _save_svg(markup: str, out: Path) -> Path | None:
    """Make an inline <svg> standalone: add xmlns, and width/height from viewBox so
    browsers can draw it at a real size. Scripts and event handlers are stripped."""
    m = re.match(r"<svg\b([^>]*)>", markup, re.S)
    if not m:
        return None
    attrs = m.group(1)
    attrs = re.sub(r"\s(on\w+|class|style)\s*=\s*(\"[^\"]*\"|'[^']*')", "", attrs)
    if "xmlns=" not in attrs:
        attrs += ' xmlns="http://www.w3.org/2000/svg"'
    vb = re.search(r'viewBox\s*=\s*["\']\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)', attrs)
    if vb and "width=" not in attrs:
        w, h = float(vb.group(1)), float(vb.group(2))
        scale = 600 / max(w, h) if max(w, h) < 600 else 1
        attrs += f' width="{w * scale:.0f}" height="{h * scale:.0f}"'
    body = markup[m.end():]
    body = re.sub(r"<script\b.*?</script>", "", body, flags=re.S | re.I)
    body = re.sub(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*')", "", body)
    svg = f"<svg{attrs}>{body}"
    # currentColor has no colour outside the page: default it to black
    svg = svg.replace("currentColor", "#000000")
    name = f"logo-{hashlib.sha1(svg.encode()).hexdigest()[:12]}.svg"
    (out / name).write_text(svg)
    return out / name


# ---------- website logo finder ----------

class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.logos, self.icons, self.og, self.jsonld, self.svgs = [], [], [], [], []
        self._in_jsonld, self._in_header, self._buf = False, 0, []
        self._a_logo = 0           # depth inside an <a> that looks like the home/logo link
        self._svg, self._svg_depth = None, 0

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if self._svg is not None:
            self._svg.append(self.get_starttag_text())
            self._svg_depth += 1
            return
        hint = " ".join([a.get("class", ""), a.get("id", ""), a.get("aria-label", ""), a.get("title", "")]).lower()
        if tag == "a" and ("logo" in hint or "brand" in hint or a.get("href", "").rstrip("/") in ("", "/")):
            self._a_logo += 1
        if tag == "svg" and (("logo" in hint) or (self._a_logo and self._in_header)):
            self._svg, self._svg_depth = [self.get_starttag_text()], 1
            return
        if tag in ("header", "nav"):
            self._in_header += 1
        if tag == "script" and a.get("type") == "application/ld+json":
            self._in_jsonld, self._buf = True, []
        if tag == "img":
            src = a.get("src") or a.get("data-src") or ""
            hay = " ".join([a.get("class", ""), a.get("id", ""), a.get("alt", ""), src]).lower()
            if src and "logo" in hay:
                score = 2 if self._in_header else 0
                score += 1 if src.lower().split("?")[0].endswith((".svg", ".png")) else 0
                self.logos.append((score, src))
        if tag == "link":
            rel = a.get("rel", "").lower()
            if "icon" in rel and a.get("href"):
                # rank by pixel size (from sizes="180x180" or a 180x180 in the file name); apple icons win ties
                m = re.search(r"(\d+)x\d+", a.get("sizes", "") + " " + a["href"])
                size = int(m.group(1)) if m else (180 if "apple-touch-icon" in rel else 32)
                self.icons.append((size + (1 if "apple-touch-icon" in rel else 0), a["href"]))
        if tag == "meta":
            prop = (a.get("property") or a.get("name") or "").lower()
            if prop in ("og:image", "twitter:image") and a.get("content"):
                self.og.append(a["content"])

    def handle_startendtag(self, tag, attrs):
        if self._svg is not None:
            self._svg.append(self.get_starttag_text())
            return
        self.handle_starttag(tag, attrs)
        if tag == "svg":  # self-closing <svg/> — nothing to keep
            self._svg = None

    def handle_endtag(self, tag):
        if self._svg is not None:
            self._svg.append(f"</{tag}>")
            self._svg_depth -= 1
            if self._svg_depth == 0:
                markup = "".join(self._svg)
                if len(markup) > 200 and "<path" in markup:
                    self.svgs.append(markup)
                self._svg = None
            return
        if tag == "a" and self._a_logo:
            self._a_logo -= 1
        if tag in ("header", "nav") and self._in_header:
            self._in_header -= 1
        if tag == "script" and self._in_jsonld:
            self._in_jsonld = False
            self.jsonld.append("".join(self._buf))

    def handle_data(self, data):
        if self._svg is not None:
            self._svg.append(data.replace("&", "&amp;").replace("<", "&lt;"))
            return
        if self._in_jsonld:
            self._buf.append(data)


def _jsonld_logos(blobs: list[str]) -> list[str]:
    found = []

    def walk(x):
        if isinstance(x, dict):
            lg = x.get("logo")
            if isinstance(lg, str):
                found.append(lg)
            elif isinstance(lg, dict) and isinstance(lg.get("url"), str):
                found.append(lg["url"])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    for b in blobs:
        try:
            walk(json.loads(b))
        except Exception:
            pass
    return found


def site_images(site: str) -> dict:
    """{'logos': [...], 'icons': [...], 'og': [...]} as absolute URLs (best first)."""
    try:
        html, ctype = _get(site, MAX_HTML)
    except Exception:
        return {"logos": [], "icons": [], "og": [], "svgs": []}
    p = _Page()
    try:
        p.feed(html.decode("utf-8", "replace"))
    except Exception:
        pass
    absu = lambda u: urllib.parse.urljoin(site, u.strip())
    logos = _jsonld_logos(p.jsonld) + [u for _, u in sorted(p.logos, key=lambda t: -t[0])]
    text = html.decode("utf-8", "replace").replace("\\/", "/")
    for m in re.finditer(r'(?:https?:)?//?[^\s"\'()<>]*logo[^\s"\'()<>]*?\.(?:svg|png|webp)(?:\?[^\s"\'()<>]*)?', text, re.I):
        u = m.group(0)
        if not re.search(r"(facebook|instagram|twitter|tiktok|youtube|pinterest|visa|mastercard|amex|paypal|klarna|affirm)", u, re.I):
            logos.append(u)
    icons = [u for _, u in sorted(p.icons, key=lambda t: -t[0])]
    dedupe = lambda xs: list(dict.fromkeys(absu(x) for x in xs if x and not x.startswith("data:")))
    return {"logos": dedupe(logos)[:3], "icons": dedupe(icons)[:1], "og": dedupe(p.og)[:1], "svgs": p.svgs[:2]}


# ---------- social profile ----------

def _youtube_channel(info: dict) -> dict:
    url = info.get("channel_url")
    if not url:
        return {}
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "extract_flat": True, "playlistend": 1}) as y:
            c = y.extract_info(url, download=False)
    except Exception:
        return {}
    thumbs = {t.get("id"): t.get("url") for t in c.get("thumbnails", [])}
    return {"pic": thumbs.get("avatar_uncropped"), "banner": thumbs.get("banner_uncropped")}


SOCIAL = re.compile(r"(instagram|facebook|fb\.me|tiktok|youtube|youtu\.be|twitter|x\.com|threads\.net|pinterest|"
                    r"linkedin|snapchat|linktr\.ee|lnk\.bio|beacons\.ai|bit\.ly|tinyurl|t\.co/|apple\.com/app|"
                    r"apps\.apple|play\.google)", re.I)


def _page_meta(url: str) -> dict:
    """og:image / og:title / og:site_name / description of any public page."""
    try:
        raw, _ = _get(url, MAX_HTML)
    except Exception:
        return {}
    import html as htmllib
    page = raw.decode("utf-8", "replace")
    out = {}
    for key in ("og:image", "og:title", "og:site_name", "description", "og:description"):
        m = (re.search(rf'<meta[^>]+(?:property|name)="{key}"[^>]*content="([^"]*)"', page, re.I)
             or re.search(rf'<meta[^>]+content="([^"]*)"[^>]*(?:property|name)="{key}"', page, re.I))
        if m:
            out[key] = htmllib.unescape(m.group(1)).strip()
    m = re.search(r"<title[^>]*>([^<]{1,200})</title>", page, re.I)
    if m:
        out["title"] = htmllib.unescape(m.group(1)).strip()
    return out


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower().replace("&", "and"))


def find_website(name: str | None, handle: str | None, texts: list[str]) -> str | None:
    """The brand's website: a link in the bio/caption/description, else a checked guess
    from the brand name or handle (e.g. "Bed Bath & Beyond" -> bedbathandbeyond.com)."""
    for t in texts:
        for u in re.findall(r"(?:https?://)?(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}(?:/[^\s)\]]*)?", t or "", re.I):
            if SOCIAL.search(u) or "@" in u or not re.search(r"\.(com|net|org|co|us|io|shop|store|ca|uk|au)\b", u, re.I):
                continue
            url = u if u.startswith("http") else "https://" + u
            if _page_meta(url):
                return url

    base = _compact(name)
    h = _compact((handle or "").lstrip("@"))
    guesses = []
    for c in (base, h, re.sub(r"(official|home|hq|usa|us|inc|co|shop|store|brand|global)$", "", h)):
        if c and len(c) >= 3 and c not in guesses:
            guesses.append(c)
    words = [w for w in re.split(r"[^a-z0-9]+", (name or "").lower().replace("&", " ")) if len(w) > 2]
    for g in guesses:
        url = f"https://www.{g}.com/"
        meta = _page_meta(url)
        if not meta:
            continue
        label = _compact(" ".join([meta.get("og:site_name", ""), meta.get("og:title", ""), meta.get("title", "")]))
        # accept when the site's own title mentions the brand (or the name maps exactly onto the domain)
        if g == base or (words and sum(w in label for w in words) >= max(1, len(words) // 2)) or (h and h in label):
            return url
    return None


def _host(url: str) -> str:
    return urllib.parse.urlparse(url).hostname.removeprefix("www.") if url else ""


def brand_kit(url: str, out: Path, site: str | None = None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    info = pv.fetch_info(url)
    stats = pv.social_stats(info)
    site_key = (info.get("extractor_key") or "").lower()
    name, handle = stats.get("name"), stats.get("handle")

    pic = banner = None
    texts = [info.get("description") or ""]
    if "instagram" in site_key and handle:
        prof = pv.instagram_profile(handle.lstrip("@"))
        pic = prof.get("_pic")
        name = prof.get("_name") or name
        site = site or prof.get("_site")
        texts.insert(0, prof.get("_bio") or "")
    elif "youtube" in site_key:
        ch = _youtube_channel(info)
        pic, banner = ch.get("pic"), ch.get("banner")
    else:  # TikTok, Facebook, others: read the account page's share tags
        for page in (info.get("uploader_url"), info.get("channel_url")):
            if page and (meta := _page_meta(page)):
                pic = pic or meta.get("og:image")
                texts.insert(0, meta.get("description") or meta.get("og:description") or "")
                break

    site = site or find_website(name, handle, texts)
    # logos live on the brand's homepage, even when the link is a campaign/product page
    home = None
    if site:
        pu = urllib.parse.urlparse(site if "://" in site else "https://" + site)
        home = f"{pu.scheme}://{pu.hostname}/"

    items = []  # {kind: avatar|logo|background, label, file}

    def add(kind, label, path):
        if path and path.exists() and not any(i["file"] == path.name and i["kind"] == kind for i in items):
            items.append({"kind": kind, "label": label, "file": path.name})

    # Logos: the website's own logo first (wordmarks), then the profile picture, then the site icon
    icon = None
    if home:
        imgs = site_images(home)
        if not (imgs["logos"] or imgs["svgs"]) and site != home:
            imgs = site_images(site)
        where = _host(home)
        n = 0
        for u in imgs["logos"]:
            if n >= 3:
                break
            if (p := _save_image(u, out, "logo")) and (p := _to_png(p)):
                n += 1
                add("logo", f"Logo from {where}" + (f" ({n})" if n > 1 else ""), p)
        for markup in imgs["svgs"]:
            if n >= 4:
                break
            if (p := _save_svg(markup, out)):
                n += 1
                add("logo", f"Logo from {where}" + (f" ({n})" if n > 1 else ""), p)
        for u in imgs["icons"]:
            if (p := _save_image(u, out, "icon")):
                icon = _to_png(p)
        for u in imgs["og"]:
            if (p := _save_image(u, out, "og")) and (p := _to_png(p)):
                add("background", f"Image from {where}, blurred", _blurred_bg(p))

    if pic and (p := _save_image(pic, out, "pic")):
        p = _to_png(p)
        small = "s100x100" in pic or "s150x150" in pic
        add("avatar", "Profile picture", p)
        add("logo", "Profile picture" + (" (small)" if small else ""), p)
    if icon:
        add("logo", "Website icon", icon)

    if info.get("thumbnail") and (p := _save_image(info["thumbnail"], out, "thumb")) and (p := _to_png(p)):
        add("background", "Video frame, blurred", _blurred_bg(p))
    if banner and (p := _save_image(banner, out, "banner")) and (p := _to_png(p)):
        add("background", "Channel banner, blurred", _blurred_bg(p))

    return {"handle": handle, "name": name, "platform": stats.get("platform"), "site": site, "items": items}


if __name__ == "__main__":  # used by server.py: brand.py URL OUT_DIR [SITE]
    import sys
    if len(sys.argv) < 3:
        sys.exit("usage: brand.py URL OUT_DIR [SITE]")
    print(json.dumps(brand_kit(sys.argv[1], Path(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else None)))
