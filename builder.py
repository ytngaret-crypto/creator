import json, shutil, re
from pathlib import Path
from html import escape
from db import get_all_websites
from templates import get_template

PUBLISHED = Path("published_site")
PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}")

PHOTO_TYPES = {"photo", "photos", "image", "images", "gallery"}
AUDIO_TYPES = {"audio", "music", "song", "voice"}


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


def _field_specs(manifest):
    fields = manifest.get("fields", [])
    if isinstance(fields, dict):
        fields = [
            dict(v, id=k) if isinstance(v, dict) else {"id": k, "label": str(v)}
            for k, v in fields.items()
        ]
    if not isinstance(fields, list):
        return []
    result = []
    for raw in fields:
        if not isinstance(raw, dict):
            continue
        fid = str(raw.get("id") or raw.get("name") or "").strip()
        if not fid:
            continue
        result.append((fid, str(raw.get("type", "text")).lower().strip()))
    return result


def _field_aliases(manifest):
    aliases = {"photos": [], "audio": []}
    for fid, typ in _field_specs(manifest):
        if typ in PHOTO_TYPES:
            aliases["photos"].append(fid)
        elif typ in AUDIO_TYPES:
            aliases["audio"].append(fid)
    return aliases


def _normalise_data(src: Path, raw_data):
    """Keep exactly the field IDs declared by the template manifest.

    No template-specific fields are invented. Canonical gallery/music aliases
    remain only for old templates that explicitly use those names.
    """
    manifest = _read_manifest(src)
    data = dict(raw_data or {})
    aliases = _field_aliases(manifest)

    # Backward compatibility for old templates using gallery/music.
    if "gallery" not in data:
        for fid in aliases["photos"]:
            value = data.get(fid)
            if value:
                data["gallery"] = value
                break
    if "music" not in data:
        for fid in aliases["audio"]:
            value = data.get(fid)
            if value:
                data["music"] = value
                break

    return manifest, data


def _copy_one_media(value, media_dir, many=False):
    if many:
        values = value if isinstance(value, list) else ([value] if value else [])
        result = []
        for original in values:
            if not original:
                continue
            p = Path(str(original))
            if p.exists() and p.is_file():
                dest = media_dir / p.name
                shutil.copy2(p, dest)
                result.append(f"media/{p.name}")
        return result

    if isinstance(value, list):
        value = value[0] if value else ""
    if not value:
        return ""

    p = Path(str(value))
    if p.exists() and p.is_file():
        dest = media_dir / p.name
        shutil.copy2(p, dest)
        return f"media/{p.name}"
    return ""


def _copy_media(manifest, data, target):
    """Copy media for every photo/audio field declared by the template."""
    media_dir = target / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    for fid, typ in _field_specs(manifest):
        if typ in PHOTO_TYPES:
            # photo = one file; photos/gallery/images = a collection.
            many = typ in {"photos", "gallery", "images"}
            data[fid] = _copy_one_media(data.get(fid), media_dir, many=many)
        elif typ in AUDIO_TYPES:
            data[fid] = _copy_one_media(data.get(fid), media_dir, many=False)

    # Legacy canonical aliases are normalized too.
    if "gallery" in data:
        data["gallery"] = _copy_one_media(data.get("gallery"), media_dir, many=True)
    if "music" in data:
        data["music"] = _copy_one_media(data.get("music"), media_dir, many=False)

    # If a legacy template has an arbitrary photo/audio ID, keep the canonical
    # alias synchronized with its first matching field.
    photo_ids = [fid for fid, typ in _field_specs(manifest) if typ in PHOTO_TYPES]
    audio_ids = [fid for fid, typ in _field_specs(manifest) if typ in AUDIO_TYPES]
    if "gallery" not in data and photo_ids:
        first = data.get(photo_ids[0])
        data["gallery"] = first if isinstance(first, list) else ([first] if first else [])
    if "music" not in data and audio_ids:
        data["music"] = data.get(audio_ids[0], "") or ""

    return data


def _render_placeholders(text, data, manifest=None):
    photo_ids = set()
    if manifest:
        photo_ids = {
            fid for fid, typ in _field_specs(manifest)
            if typ in PHOTO_TYPES
        }

    def replacement(match):
        key = match.group(1).strip()
        value = data.get(key, "")

        # Legacy gallery/photos placeholders produce ready-to-render images.
        if key in {"gallery", "photos", "images"}:
            values = value if isinstance(value, list) else ([value] if value else [])
            return "".join(
                f'<img src="{escape(str(x), quote=True)}" alt="Foto" loading="lazy">'
                for x in values
            )

        # A plural photo field is also convenient as a direct HTML placeholder.
        if key in photo_ids and isinstance(value, list):
            return "".join(
                f'<img src="{escape(str(x), quote=True)}" alt="Foto" loading="lazy">'
                for x in value
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
    data = _copy_media(manifest, data, target)

    # Render every template field by its real manifest ID.
    html = _render_placeholders(
        html_path.read_text(encoding="utf-8"), data, manifest
    )
    (target / "index.html").write_text(html, encoding="utf-8")

    for name in ("style.css", "script.js"):
        p = src / name
        if p.exists():
            content = p.read_text(encoding="utf-8")
            (target / name).write_text(
                _render_placeholders(content, data, manifest),
                encoding="utf-8",
            )

    assets = src / "assets"
    if assets.exists():
        shutil.copytree(assets, target / "assets", dirs_exist_ok=True)

    copy_manifest(src, target)


def copy_manifest(src: Path, target: Path):
    """Copy manifest and keep PWA scope relative to /w/<ID>/."""
    manifest = _read_manifest(src)
    if not manifest:
        return
    manifest["start_url"] = "./"
    manifest["scope"] = "./"
    manifest.setdefault("display", "standalone")
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


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
