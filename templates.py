import hashlib
import json
import re
from pathlib import Path

ROOT = Path("templates")


def _safe_root():
    ROOT.mkdir(parents=True, exist_ok=True)
    return ROOT.resolve()


def _display_name(name):
    name = re.sub(r"[_\-]+", " ", str(name).strip())
    name = re.sub(r"\s+", " ", name).strip()
    return name or "Template"


def _key(category, template_id):
    return hashlib.sha1(f"{category}/{template_id}".encode("utf-8")).hexdigest()[:10]


def load_manifest(path):
    """Read the manifest from the template folder every time it is requested."""
    path = Path(path)
    manifest_path = path / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError(f"Template tidak memiliki manifest.json: {path}")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"manifest.json template rusak: {manifest_path} - {e}")
    if not isinstance(data, dict):
        raise RuntimeError(f"manifest.json harus berupa object JSON: {manifest_path}")
    return data


def normalize_fields(manifest):
    """Manifest is the only source of wizard fields; preserve manifest order."""
    raw_fields = manifest.get("fields", [])
    if isinstance(raw_fields, dict):
        raw_fields = [
            dict(value, id=key) if isinstance(value, dict) else
            {"id": key, "label": str(value)}
            for key, value in raw_fields.items()
        ]
    if not isinstance(raw_fields, list):
        raise RuntimeError("manifest.json: 'fields' harus berupa array atau object.")

    result, seen = [], set()
    for raw in raw_fields:
        if isinstance(raw, str):
            item = {"id": raw, "label": raw}
        elif isinstance(raw, dict):
            item = dict(raw)
        else:
            continue

        fid = str(item.get("id") or item.get("name") or "").strip()
        if not fid:
            continue
        if fid in seen:
            raise RuntimeError(f"manifest.json memiliki ID field duplikat: {fid}")
        seen.add(fid)

        item["id"] = fid
        item["label"] = str(item.get("label") or fid)
        item["type"] = str(item.get("type") or "text").lower().strip()
        item["required"] = bool(item.get("required", False))
        item["description"] = str(item.get("description") or "").strip()

        for key in ("example", "placeholder"):
            if key in item:
                item[key] = str(item[key])

        if item["type"] == "text_list":
            try:
                item["min"] = max(0, int(item.get("min", 1)))
                item["max"] = max(item["min"], int(item.get("max", item["min"])))
            except (TypeError, ValueError):
                raise RuntimeError(f"Field '{fid}': min/max text_list harus angka.")

        result.append(item)
    return result


def _make_template(category_dir, template_dir):
    manifest = load_manifest(template_dir)
    return {
        "id": template_dir.name,
        "key": _key(category_dir.name, template_dir.name),
        "name": _display_name(template_dir.name),
        "description": str(manifest.get("description", "")).strip(),
        "category": category_dir.name,
        "category_name": _display_name(category_dir.name),
        "path": str(template_dir),
        "fields": normalize_fields(manifest),
        "manifest": manifest,
    }


def list_categories():
    root = _safe_root()
    return [p for p in sorted(root.iterdir(), key=lambda x: x.name.lower())
            if p.is_dir() and not p.name.startswith(".")]


def list_templates(category=None):
    root = _safe_root()
    result = []
    categories = [root / category] if category else list_categories()

    for category_dir in categories:
        if not category_dir.is_dir():
            continue
        for template_dir in sorted(category_dir.iterdir(), key=lambda x: x.name.lower()):
            if not template_dir.is_dir() or template_dir.name.startswith("."):
                continue
            if not (template_dir / "index.html").is_file():
                continue
            try:
                result.append(_make_template(category_dir, template_dir))
            except RuntimeError:
                # Invalid templates are not shown as usable templates.
                continue
    return result


def get_template(category, template_id):
    wanted = str(template_id)
    for t in list_templates(category):
        if t["id"] == wanted or t["key"] == wanted or t["name"] == wanted:
            return t
        if str(t["manifest"].get("id", "")).strip() == wanted:
            return t
    return None


def get_template_by_key(key):
    for t in list_templates():
        if t["key"] == str(key):
            return t
    return None


def get_template_fields(category, template_id):
    """Fresh manifest read used by the wizard immediately before collecting data."""
    template = get_template(category, template_id)
    if not template:
        raise RuntimeError("Template tidak ditemukan.")
    # Re-read the actual file rather than trusting an earlier template object.
    path = Path(template["path"])
    manifest = load_manifest(path)
    fields = normalize_fields(manifest)
    return template, manifest, fields


def category_display_name(category):
    return _display_name(category)


def category_key_from_name(name):
    return name if any(p.name == name for p in list_categories()) else None


def validate_template_dir(path):
    path = Path(path)
    if not path.is_dir():
        return False, "Folder template tidak ditemukan."
    if not (path / "index.html").is_file():
        return False, "Template wajib memiliki index.html."
    try:
        manifest = load_manifest(path)
        fields = normalize_fields(manifest)
        field_ids = {f["id"] for f in fields}
        html = (path / "index.html").read_text(encoding="utf-8")
        placeholders = set(re.findall(r"\\{\\{\\s*([A-Za-z0-9_.-]+)\\s*\\}\\}", html))
        unknown = sorted(placeholders - field_ids)
        if unknown:
            return False, "Placeholder tidak terdaftar di manifest.json: " + ", ".join(unknown)
    except Exception as e:
        return False, str(e)
    return True, "OK"
