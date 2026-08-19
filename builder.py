import json, re, shutil, tempfile
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
    seen = set()
    for raw in fields:
        if not isinstance(raw, dict):
            continue
        fid = str(raw.get("id") or raw.get("name") or "").strip()
        if not fid or fid in seen:
            continue
        seen.add(fid)
        result.append((fid, str(raw.get("type", "text")).lower().strip()))
    return result


def _field_map(manifest):
    return {fid: typ for fid, typ in _field_specs(manifest)}


def _normalise_data(src: Path, raw_data):
    manifest = _read_manifest(src)
    data = dict(raw_data or {})
    specs = _field_map(manifest)

    # Backward compatibility only: if a template's actual manifest uses a
    # different photo/audio ID, do not invent fields in the HTML. The canonical
    # aliases are only useful when an old template explicitly uses them.
    photo_ids = [fid for fid, typ in specs.items() if typ in PHOTO_TYPES]
    audio_ids = [fid for fid, typ in specs.items() if typ in AUDIO_TYPES]
    if "gallery" not in data and photo_ids:
        value = data.get(photo_ids[0])
        data["gallery"] = value
    if "music" not in data and audio_ids:
        data["music"] = data.get(audio_ids[0], "")
    return manifest, data


def _copy_one_media(value, media_dir: Path, many=False):
    if many:
        values = value if isinstance(value, list) else ([value] if value else [])
        result = []
        for original in values:
            if not original:
                continue
            p = Path(str(original))
            if not p.is_file():
                raise RuntimeError(f"File media tidak ditemukan: {p}")
            # Avoid path traversal/name collisions while keeping the original extension.
            name = p.name.replace("\\", "_").replace("/", "_")
            dest = media_dir / name
            shutil.copy2(p, dest)
            result.append(f"media/{name}")
        return result

    if isinstance(value, list):
        value = value[0] if value else ""
    if not value:
        return ""
    p = Path(str(value))
    if not p.is_file():
        raise RuntimeError(f"File media tidak ditemukan: {p}")
    name = p.name.replace("\\", "_").replace("/", "_")
    dest = media_dir / name
    shutil.copy2(p, dest)
    return f"media/{name}"


def _copy_media(manifest, data, target):
    media_dir = target / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    specs = _field_specs(manifest)

    for fid, typ in specs:
        value = data.get(fid)
        if not value:
            continue
        if typ in PHOTO_TYPES:
            data[fid] = _copy_one_media(value, media_dir, many=typ in {"photos", "gallery", "images"})
        elif typ in AUDIO_TYPES:
            data[fid] = _copy_one_media(value, media_dir, many=False)

    # Keep old templates that literally use {{gallery}} / {{music}} working.
    if "gallery" in data and "gallery" not in {fid for fid, _ in specs}:
        data["gallery"] = _copy_one_media(data["gallery"], media_dir, many=True)
    if "music" in data and "music" not in {fid for fid, _ in specs}:
        data["music"] = _copy_one_media(data["music"], media_dir, many=False)

    return data


def _render_placeholders(text, data, manifest=None):
    photo_ids = {fid for fid, typ in _field_specs(manifest or {}) if typ in PHOTO_TYPES}

    def replacement(match):
        key = match.group(1).strip()
        value = data.get(key, "")
        if key in {"gallery", "photos", "images"} or key in photo_ids:
            if isinstance(value, list):
                return "".join(
                    f'<img src="{escape(str(x), quote=True)}" alt="Foto" loading="lazy">'
                    for x in value if x
                )
            if value:
                return f'<img src="{escape(str(value), quote=True)}" alt="Foto" loading="lazy">'
            return ""
        if isinstance(value, list):
            return ", ".join(escape(str(x)) for x in value)
        return escape(str(value)).replace("\n", "<br>")

    return PLACEHOLDER_RE.sub(replacement, text)


def _finalize_audio(html: str) -> str:
    """Turn generated music into background audio without browser controls.

    Autoplay with sound is still subject to browser policy. We therefore also
    retry on the first user interaction, while never exposing the audio UI.
    """
    # Only touch audio elements that actually have a generated source.
    def repl(match):
        tag = match.group(0)
        if not re.search(r'\bsrc=["\']media/[^"\']+["\']', tag, re.I):
            return tag
        tag = re.sub(r'\s+controls(?:\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s>]+))?', '', tag, flags=re.I)
        tag = re.sub(r'\s+autoplay(?:\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s>]+))?', '', tag, flags=re.I)
        tag = re.sub(r'\s+loop(?:\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s>]+))?', '', tag, flags=re.I)
        tag = re.sub(r'\s+playsinline(?:\s*=\s*(?:"[^"]*"|\'[^\']*\'|[^\s>]+))?', '', tag, flags=re.I)
        return tag[:-1] + ' autoplay loop playsinline>' if tag.endswith('>') else tag

    html = re.sub(r'<audio\b[^>]*>', repl, html, flags=re.I)
    if re.search(r'<audio\b[^>]*\bsrc=["\']media/[^"\']+["\'][^>]*>', html, re.I):
        script = r'''<script>
(function(){
  const audio=document.querySelector('audio[src^="media/"]');
  if(!audio) return;
  audio.autoplay=true; audio.loop=true; audio.playsInline=true;
  const play=()=>audio.play().catch(()=>{});
  play();
  ['click','touchstart','pointerdown','keydown','scroll'].forEach(e=>
    document.addEventListener(e,play,{passive:true,once:true})
  );
})();
</script>'''
        if '</body>' in html.lower():
            pos = html.lower().rfind('</body>')
            html = html[:pos] + script + html[pos:]
        else:
            html += script
    return html


def _validate_required(manifest, data):
    missing = []
    for raw in manifest.get("fields", []) if isinstance(manifest.get("fields", []), list) else []:
        if not isinstance(raw, dict) or not raw.get("required"):
            continue
        fid = str(raw.get("id") or raw.get("name") or "").strip()
        value = data.get(fid)
        if value is None or value == "" or value == []:
            missing.append(fid)
    if missing:
        raise RuntimeError("Field wajib belum lengkap: " + ", ".join(missing))


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

    # Re-read the manifest from the actual template folder at build time.
    manifest, data = _normalise_data(src, raw_data)
    _validate_required(manifest, data)
    data = _copy_media(manifest, data, target)

    html = _render_placeholders(html_path.read_text(encoding="utf-8"), data, manifest)
    html = _finalize_audio(html)
    (target / "index.html").write_text(html, encoding="utf-8")

    for name in ("style.css", "script.js"):
        p = src / name
        if p.exists():
            content = _render_placeholders(p.read_text(encoding="utf-8"), data, manifest)
            (target / name).write_text(content, encoding="utf-8")

    assets = src / "assets"
    if assets.exists():
        shutil.copytree(assets, target / "assets", dirs_exist_ok=True)
    copy_manifest(src, target)


def copy_manifest(src: Path, target: Path):
    manifest = _read_manifest(src)
    if not manifest:
        return
    manifest["start_url"] = "./"
    manifest["scope"] = "./"
    manifest.setdefault("display", "standalone")
    (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def write_netlify_redirects(root: Path):
    lines = []
    for site in get_all_websites():
        sid = str(site["id"])
        lines.extend([f"/w/{sid} /w/{sid}/index.html 200!", f"/w/{sid}/ /w/{sid}/index.html 200!"])
    (root / "_redirects").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def build_all():
    # Atomic-ish staging build: never destroy the currently published tree
    # until every website has built successfully.
    PUBLISHED.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="published_site_", dir=str(PUBLISHED.parent)))
    try:
        for site in get_all_websites():
            render_one(site, staging)
        write_netlify_redirects(staging)
        if PUBLISHED.exists():
            backup = PUBLISHED.parent / (PUBLISHED.name + ".old")
            if backup.exists():
                shutil.rmtree(backup)
            PUBLISHED.rename(backup)
            try:
                staging.rename(PUBLISHED)
            except Exception:
                backup.rename(PUBLISHED)
                raise
            shutil.rmtree(backup, ignore_errors=True)
        else:
            staging.rename(PUBLISHED)
        return PUBLISHED
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
