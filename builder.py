import json, shutil, re
from pathlib import Path
from html import escape
from db import get_all_websites
from templates import get_template

PUBLISHED=Path("published_site")

def copy_manifest(src: Path, target: Path):
    """Copy and normalize manifest.json for a site under /w/<ID>/."""
    manifest_src=src/"manifest.json"
    if not manifest_src.exists():
        return
    try:
        manifest=json.loads(manifest_src.read_text(encoding="utf-8"))
    except Exception as e:
        raise RuntimeError(f"manifest.json template rusak: {manifest_src} - {e}")
    if not isinstance(manifest, dict):
        raise RuntimeError(f"manifest.json harus berupa object JSON: {manifest_src}")
    manifest["start_url"]="./"
    manifest["scope"]="./"
    if not manifest.get("display"):
        manifest["display"]="standalone"
    (target/"manifest.json").write_text(
        json.dumps(manifest,ensure_ascii=False,indent=2),
        encoding="utf-8"
    )

def render_one(site,out_root):
    template=get_template(site["category"],site["template_id"])
    if not template: raise RuntimeError(f"Template {site['template_id']} tidak ditemukan")
    data=json.loads(site["data_json"])
    target=out_root/"w"/site["id"]
    target.mkdir(parents=True,exist_ok=True)
    src=Path(template["path"])
    html_path=src/"index.html"
    if not html_path.exists():
        raise RuntimeError(f"Template {site['template_id']} tidak memiliki index.html")
    html=html_path.read_text(encoding="utf-8")

    gallery=[]
    for original in data.get("gallery",[]) or []:
        p=Path(original)
        if p.exists():
            dest=target/"media"/p.name;dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(p,dest);gallery.append(f"media/{p.name}")
    data["gallery"]=gallery

    music=data.get("music")
    if music:
        p=Path(music)
        if p.exists():
            dest=target/"media"/p.name;dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(p,dest);data["music"]=f"media/{p.name}"
        else:data["music"]=""
    else:data["music"]=""

    def replacement(m):
        key=m.group(1).strip()
        value=data.get(key,"")
        if key=="gallery":
            return "".join(f'<img src="{escape(x)}" alt="Foto" loading="lazy">' for x in value)
        if isinstance(value,list):
            return ", ".join(map(str,value))
        return escape(str(value)).replace("\n","<br>")

    html=re.sub(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}",replacement,html)

    for name in ("style.css","script.js"):
        p=src/name
        if p.exists():shutil.copy2(p,target/name)
    assets=src/"assets"
    if assets.exists():shutil.copytree(assets,target/"assets",dirs_exist_ok=True)
    copy_manifest(src,target)
    (target/"index.html").write_text(html,encoding="utf-8")

def write_netlify_redirects(root: Path):
    """Create exact rewrites for every generated /w/<ID>/ site."""
    lines=[]
    for site in get_all_websites():
        sid=str(site["id"])
        lines.append(f"/w/{sid} /w/{sid}/index.html 200!")
        lines.append(f"/w/{sid}/ /w/{sid}/index.html 200!")
    (root/"_redirects").write_text("\n".join(lines)+("\n" if lines else ""),encoding="utf-8")

def build_all():
    if PUBLISHED.exists():shutil.rmtree(PUBLISHED)
    PUBLISHED.mkdir(parents=True)
    for site in get_all_websites():render_one(site,PUBLISHED)
    write_netlify_redirects(PUBLISHED)
    return PUBLISHED
