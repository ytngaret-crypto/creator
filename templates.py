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
    """Normalize manifest fields without hard-coding any template.

    A template controls the wizard entirely through manifest.json:
    every field entry becomes one bot question, in the same order.
    """
    fields = manifest.get("fields", [])

    if isinstance(fields, dict):
        normalized = []
        for fid, value in fields.items():
            if isinstance(value, dict):
                item = dict(value)
                item.setdefault("id", fid)
            else:
                item = {"id": fid, "label": str(value)}
            normalized.append(item)
        fields = normalized

    if not isinstance(fields, list):
        return []

    result = []
    seen = set()

    for index, raw in enumerate(fields, 1):
        if isinstance(raw, str):
            item = {"id": raw, "label": raw}
        elif isinstance(raw, dict):
            item = dict(raw)
        else:
            continue

        fid = str(item.get("id") or item.get("name") or "").strip()
        if not fid:
            # Invalid fields are ignored rather than inventing an id that the
            # template cannot reference.
            continue
        if fid in seen:
            continue
        seen.add(fid)

        item["id"] = fid
        item["label"] = str(item.get("label") or fid)
        item["type"] = str(item.get("type") or "text").lower().strip()
        item["required"] = bool(item.get("required", False))
        item["description"] = str(item.get("description") or "").strip()

        # Optional examples/help text are kept exactly so the bot can show them.
        if "example" in item:
            item["example"] = str(item["example"])
        if "placeholder" in item:
            item["placeholder"] = str(item["placeholder"])

        result.append(item)

    return result


def _make_template(category_dir, template_dir):
    category = category_dir.name
    folder_id = template_dir.name
    manifest = _load_manifest(template_dir)

    # The folder name is the menu display name. This means the owner can
    # rename a template by renaming its GitHub folder.
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
        if t["id"] == wanted or t["key"] == wanted or t["name"] == wanted:
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
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                return False, "manifest.json harus berupa object JSON."

            raw_fields = manifest.get("fields", [])
            if raw_fields is not None and not isinstance(raw_fields, (list, dict)):
                return False, "manifest.json: 'fields' harus berupa array atau object."

            fields = _fields(manifest)
            if raw_fields and not fields:
                return False, "manifest.json memiliki fields tetapi tidak ada field yang valid."

            ids = [f["id"] for f in fields]
            if len(ids) != len(set(ids)):
                return False, "manifest.json memiliki ID field yang duplikat."
        except Exception as e:
            return False, f"manifest.json tidak valid: {e}"

    return True, "OK"
