import json, shutil, re
from pathlib import Path
from html import escape
from db import get_all_websites
from templates import get_template

PUBLISHED=Path("published_site")

def media_name(src):
    return Path(src).name

def render_one(site,out_root):
    template=get_template(site["category"],site["template_id"])
    if not template: raise RuntimeError(f"Template {site['template_id']} tidak ditemukan")
    data=json.loads(site["data_json"])
    target=out_root/"w"/site["id"]
    target.mkdir(parents=True,exist_ok=True)
    src=Path(template["path"])
    html=(src/"index.html").read_text(encoding="utf-8")

    # Convert stored local media paths to paths inside this website.
    gallery=[]
    for original in data.get("gallery",[]) or []:
        p=Path(original)
        if p.exists():
            dest=target/"media"/p.name;dest.parent.mkdir(exist_ok=True)
            shutil.copy2(p,dest);gallery.append(f"media/{p.name}")
    data["gallery"]=gallery

    music=data.get("music")
    if music:
        p=Path(music)
        if p.exists():
            dest=target/"media"/p.name;dest.parent.mkdir(exist_ok=True)
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
    # Copy template assets, excluding manifest.
    for name in ("style.css","script.js"):
        p=src/name
        if p.exists():shutil.copy2(p,target/name)
    assets=src/"assets"
    if assets.exists():shutil.copytree(assets,target/"assets",dirs_exist_ok=True)
    (target/"index.html").write_text(html,encoding="utf-8")

def build_all():
    if PUBLISHED.exists():shutil.rmtree(PUBLISHED)
    PUBLISHED.mkdir(parents=True)
    for site in get_all_websites():render_one(site,PUBLISHED)
    return PUBLISHED
