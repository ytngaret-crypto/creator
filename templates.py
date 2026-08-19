
import json
import re
from pathlib import Path
import hashlib

ROOT = Path("templates")


def _safe_root():
    ROOT.mkdir(parents=True, exist_ok=True)
    return ROOT.resolve()


def _display_name(name):
    name = str(name).strip()
    name = re.sub(r"[_\-]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name or "Template"


def _key(category, template_id):
    raw = f"{category}/{template_id}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:10]


def _load_manifest(path):
    manifest_path = path / "manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _fields(manifest):
    fields = manifest.get("fields", [])
    if isinstance(fields, dict):
        fields = [
            dict(v, id=k) if isinstance(v, dict) else {"id": k, "label": str(v)}
            for k, v in fields.items()
        ]
    if not isinstance(fields, list):
        return []

    result = []
    for f in fields:
        if not isinstance(f, dict):
            continue
        item = dict(f)
        item.setdefault("id", item.get("name", "field"))
        item.setdefault("label", item["id"])
        item.setdefault("type", "text")
        item.setdefault("required", False)
        item.setdefault("description", "")
        result.append(item)
    return result


def _make_template(category_dir, template_dir):
    category = category_dir.name
    folder_id = template_dir.name
    manifest = _load_manifest(template_dir)

    # Folder name is the display name. manifest name is only fallback metadata.
    display = _display_name(folder_id)
    description = str(manifest.get("description", "")).strip()

    return {
        "id": folder_id,
        "key": _key(category, folder_id),
        "name": display,
        "description": description,
        "category": category,
        "category_name": _display_name(category),
        "path": str(template_dir),
        "fields": _fields(manifest),
        "manifest": manifest,
    }


def list_categories():
    root = _safe_root()
    return [
        p for p in sorted(root.iterdir(), key=lambda x: x.name.lower())
        if p.is_dir() and not p.name.startswith(".")
    ]


def list_templates(category=None):
    root = _safe_root()
    result = []

    categories = [root / category] if category else list_categories()

    for category_dir in categories:
        if not category_dir.exists() or not category_dir.is_dir():
            continue

        for template_dir in sorted(category_dir.iterdir(), key=lambda x: x.name.lower()):
            if not template_dir.is_dir() or template_dir.name.startswith("."):
                continue
            if not (template_dir / "index.html").exists():
                continue
            result.append(_make_template(category_dir, template_dir))

    return result


def get_template(category, template_id):
    wanted = str(template_id)

    for t in list_templates(category):
        if (
            t["id"] == wanted
            or t["key"] == wanted
            or t["name"] == wanted
        ):
            return t

        manifest_id = str(t["manifest"].get("id", "")).strip()
        if manifest_id and manifest_id == wanted:
            return t

    return None


def get_template_by_key(key):
    for t in list_templates():
        if t["key"] == str(key):
            return t
    return None


def category_display_name(category):
    return _display_name(category)


def category_key_from_name(name):
    for p in list_categories():
        if p.name == name:
            return p.name
    return None


def validate_template_dir(path):
    path = Path(path)
    if not path.is_dir():
        return False, "Folder template tidak ditemukan."

    if not (path / "index.html").is_file():
        return False, "Template wajib memiliki index.html."

    manifest_path = path / "manifest.json"
    if manifest_path.exists():
        try:
            json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as e:
            return False, f"manifest.json tidak valid: {e}"

    return True, "OK"
