import json
import re
import shutil
from html import escape
from pathlib import Path

from db import get_all_websites
from templates import get_template, load_manifest, normalize_fields

PUBLISHED = Path("published_site")
PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}")
PHOTO_TYPES = {"photo", "photos", "image", "images", "gallery"}
AUDIO_TYPES = {"audio", "music", "song", "voice"}


def _field_specs(manifest):
    return [(f["id"], f["type"]) for f in normalize_fields(manifest)]


def _copy_one_media(value, media_dir, many=False):
    values = value if isinstance(value, list) else ([value] if value else [])
    if not many:
        values = values[:1]

    result = []
    for original in values:
        if not original:
            continue
        source = Path(str(original))
        if not source.is_file():
            continue

        # Keep each generated website self-contained. Avoid collisions if
        # different uploaded files happen to have the same basename.
        dest_name = source.name
        dest = media_dir / dest_name
        if dest.exists() and dest.resolve() != source.resolve():
            stem, suffix = source.stem, source.suffix
            n = 2
            while dest.exists():
                dest = media_dir / f"{stem}_{n}{suffix}"
                n += 1

        shutil.copy2(source, dest)
        result.append(f"media/{dest.name}")

    return result if many else (result[0] if result else "")


def _copy_media(manifest, data, target):
    media_dir = target / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    for fid, typ in _field_specs(manifest):
        if typ in PHOTO_TYPES:
            data[fid] = _copy_one_media(
                data.get(fid), media_dir,
                many=typ in {"photos", "gallery", "images"}
            )
        elif typ in AUDIO_TYPES:
            data[fid] = _copy_one_media(data.get(fid), media_dir, many=False)
    return data


def _render_placeholders(text, data, manifest):
    specs = dict(_field_specs(manifest))

    def replacement(match):
        key = match.group(1).strip()
        if key not in specs:
            raise RuntimeError(
                f"Placeholder '{{{{{key}}}}}' tidak terdaftar di manifest.json."
            )

        value = data.get(key, "")
        typ = specs[key]

        if typ in {"photos", "images", "gallery"} or (
            typ in PHOTO_TYPES and isinstance(value, list)
        ):
            values = value if isinstance(value, list) else ([value] if value else [])
            return "".join(
                f'<img src="{escape(str(x), quote=True)}" alt="Foto" loading="lazy">'
                for x in values if x
            )

        if isinstance(value, list):
            # text_list and other list values become safe HTML lines.
            return "<br>".join(escape(str(x)) for x in value if x)

        return escape(str(value)).replace("\n", "<br>")

    return PLACEHOLDER_RE.sub(replacement, text)


def _copy_static_files(src, target, data, manifest):
    # Only process the standard template files. Assets are copied byte-for-byte
    # so CSS/JS/images cannot be accidentally altered by placeholder rendering.
    for name in ("style.css", "script.js"):
        source = src / name
        if source.is_file():
            content = source.read_text(encoding="utf-8")
            target.joinpath(name).write_text(
                _render_placeholders(content, data, manifest),
                encoding="utf-8"
            )

    assets = src / "assets"
    if assets.is_dir():
        shutil.copytree(assets, target / "assets", dirs_exist_ok=True)


def copy_manifest(src, target):
    manifest = load_manifest(src)
    manifest["start_url"] = "./"
    manifest["scope"] = "./"
    manifest.setdefault("display", "standalone")
    (target / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def render_one(site, out_root):
    template = get_template(site["category"], site["template_id"])
    if not template:
        raise RuntimeError(
            f"Template '{site['category']}/{site['template_id']}' tidak ditemukan."
        )

    src = Path(template["path"])
    manifest = load_manifest(src)
    normalize_fields(manifest)

    html_path = src / "index.html"
    if not html_path.is_file():
        raise RuntimeError(f"Template tidak memiliki index.html: {src}")

    try:
        raw_data = json.loads(site["data_json"])
    except Exception as e:
        raise RuntimeError(f"Data website {site['id']} rusak: {e}")
    if not isinstance(raw_data, dict):
        raise RuntimeError(f"Data website {site['id']} harus berupa object.")

    # Fresh manifest + direct field IDs. No gallery/music alias normalization.
    data = dict(raw_data)
    target = out_root / "w" / str(site["id"])
    target.mkdir(parents=True, exist_ok=True)

    data = _copy_media(manifest, data, target)
    html = html_path.read_text(encoding="utf-8")
    html = _render_placeholders(html, data, manifest)
    (target / "index.html").write_text(html, encoding="utf-8")

    _copy_static_files(src, target, data, manifest)
    copy_manifest(src, target)


def write_netlify_redirects(root):
    # Explicit rewrites make /w/<id>/ and /w/<id> work while preserving all
    # generated assets under the same site directory.
    lines = []
    for site in get_all_websites():
        sid = str(site["id"])
        site_dir = root / "w" / sid
        if not (site_dir / "index.html").is_file():
            continue
        lines.append(f"/w/{sid} /w/{sid}/index.html 200!")
        lines.append(f"/w/{sid}/ /w/{sid}/index.html 200!")
    (root / "_redirects").write_text(
        "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
    )


def build_all():
    """Build every saved website into a staging directory, then publish atomically.

    A broken old website must never erase the last known-good published tree.
    """
    staging = PUBLISHED.parent / f".{PUBLISHED.name}_staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)

    try:
        for site in get_all_websites():
            render_one(site, staging)

        write_netlify_redirects(staging)

        old = PUBLISHED
        backup = PUBLISHED.parent / f".{PUBLISHED.name}_backup"
        if backup.exists():
            shutil.rmtree(backup)

        if old.exists():
            old.rename(backup)
        try:
            staging.rename(old)
        except Exception:
            if backup.exists() and not old.exists():
                backup.rename(old)
            raise
        if backup.exists():
            shutil.rmtree(backup)
        return old
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
