import json, shutil, re
from pathlib import Path
from html import escape
from db import get_all_websites
from templates import get_template

PUBLISHED = Path("published_site")
PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}")


def _read_manifest(src: Path):
    path = src / "manifest.json"
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"manifest.json template rusak: {path} - {e}")
    if not isinstance(value, dict):
        raise RuntimeError(f"manifest.json harus berupa object JSON: {path}")
    return value


def copy_manifest(src: Path, target: Path):
    """Copy manifest.json and make its URL scope safe inside /w/<ID>/."""
    manifest = _read_manifest(src)
    if not manifest:
        return
    manifest["start_url"] = "./"
    manifest["scope"] = "./"
    manifest.setdefault("display", "standalone")
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _field_aliases(manifest):
    """Return template field ids grouped by their semantic type."""
    aliases = {"photos": [], "audio": []}
    for field in manifest.get("fields", []) or []:
        if not isinstance(field, dict):
            continue
        fid = str(field.get("id", "")).strip()
        typ = str(field.get("type", "text")).lower().strip()
        if not fid:
            continue
        if typ in {"photos", "photo", "images", "image", "gallery"}:
            aliases["photos"].append(fid)
        elif typ in {"audio", "music", "song", "voice"}:
            aliases["audio"].append(fid)
    return aliases


def _normalise_data(src: Path, raw_data):
    """Make template field ids and canonical placeholders available together.

    Templates are allowed to call their fields by their manifest ids, while the
    builder also exposes {{gallery}} and {{music}} for backwards compatibility.
    """
    manifest = _read_manifest(src)
    data = dict(raw_data or {})
    aliases = _field_aliases(manifest)

    # Photo/audio fields may have arbitrary ids in a template manifest.
    photo_ids = aliases["photos"]
    audio_ids = aliases["audio"]

    if "gallery" not in data:
        for fid in photo_ids:
            if data.get(fid):
                data["gallery"] = data[fid]
                break
    if "music" not in data:
        for fid in audio_ids:
            if data.get(fid):
                data["music"] = data[fid]
                break

    # Also mirror canonical values back to the actual manifest field id.
    if data.get("gallery"):
        for fid in photo_ids:
            data.setdefault(fid, data["gallery"])
    if data.get("music"):
        for fid in audio_ids:
            data.setdefault(fid, data["music"])

    # Common legacy aliases used by memory templates.
    if "title" not in data and data.get("judul") is not None:
        data["title"] = data["judul"]
    if "recipient" not in data and data.get("nama") is not None:
        data["recipient"] = data["nama"]
    if "message" not in data and data.get("pesan") is not None:
        data["message"] = data["pesan"]

    return manifest, data


def _copy_media(data, target):
    """Copy uploaded media and return browser-relative paths."""
    media_dir = target / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    gallery = []
    for original in data.get("gallery", []) or []:
        if not original:
            continue
        p = Path(str(original))
        if p.exists() and p.is_file():
            dest = media_dir / p.name
            shutil.copy2(p, dest)
            gallery.append(f"media/{p.name}")
    data["gallery"] = gallery

    music = data.get("music")
    if music:
        p = Path(str(music))
        if p.exists() and p.is_file():
            dest = media_dir / p.name
            shutil.copy2(p, dest)
            data["music"] = f"media/{p.name}"
        else:
            data["music"] = ""
    else:
        data["music"] = ""

    # Keep arbitrary manifest field ids synchronized with the normalized paths.
    return data


def _render_placeholders(text, data):
    def replacement(match):
        key = match.group(1).strip()
        value = data.get(key, "")
        if key in {"gallery", "photos", "images"}:
            return "".join(
                f'<img src="{escape(str(x))}" alt="Foto" loading="lazy">'
                for x in (value or [])
            )
        if isinstance(value, list):
            return ", ".join(map(str, value))
        return escape(str(value)).replace("\n", "<br>")

    return PLACEHOLDER_RE.sub(replacement, text)


def render_one(site, out_root):
    template = get_template(site["category"], site["template_id"])
    if not template:
        raise RuntimeError(f"Template {site['template_id']} tidak ditemukan")

    try:
        raw_data = json.loads(site["data_json"])
    except Exception as e:
        raise RuntimeError(f"Data website {site['id']} rusak: {e}")

    target = out_root / "w" / str(site["id"])
    target.mkdir(parents=True, exist_ok=True)
    src = Path(template["path"])
    html_path = src / "index.html"
    if not html_path.exists():
        raise RuntimeError(f"Template {site['template_id']} tidak memiliki index.html")

    manifest, data = _normalise_data(src, raw_data)
    data = _copy_media(data, target)

    # After media normalization, mirror the canonical paths to actual field ids.
    for field in manifest.get("fields", []) or []:
        if not isinstance(field, dict):
            continue
        fid = str(field.get("id", "")).strip()
        typ = str(field.get("type", "text")).lower().strip()
        if not fid:
            continue
        if typ in {"photos", "photo", "images", "image", "gallery"}:
            data[fid] = data.get("gallery", [])
        elif typ in {"audio", "music", "song", "voice"}:
            data[fid] = data.get("music", "")

    # Render both HTML and JS. Some templates keep data attributes/audio setup
    # in script.js rather than directly inside index.html.
    html = _render_placeholders(html_path.read_text(encoding="utf-8"), data)
    (target / "index.html").write_text(html, encoding="utf-8")

    for name in ("style.css", "script.js"):
        p = src / name
        if p.exists():
            content = p.read_text(encoding="utf-8")
            (target / name).write_text(_render_placeholders(content, data), encoding="utf-8")

    assets = src / "assets"
    if assets.exists():
        shutil.copytree(assets, target / "assets", dirs_exist_ok=True)

    copy_manifest(src, target)


def write_netlify_redirects(root: Path):
    """Create exact rewrites for every generated /w/<ID> site."""
    lines = []
    for site in get_all_websites():
        sid = str(site["id"])
        lines.append(f"/w/{sid} /w/{sid}/index.html 200!")
        lines.append(f"/w/{sid}/ /w/{sid}/index.html 200!")
    (root / "_redirects").write_text(
        "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
    )


def build_all():
    if PUBLISHED.exists():
        shutil.rmtree(PUBLISHED)
    PUBLISHED.mkdir(parents=True)
    for site in get_all_websites():
        render_one(site, PUBLISHED)
    write_netlify_redirects(PUBLISHED)
    return PUBLISHED
