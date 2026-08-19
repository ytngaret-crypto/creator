from pathlib import Path
import json

ROOT=Path(__file__).parent/"templates"

def list_templates(category=None):
    if not ROOT.exists(): return []
    cats=[ROOT/category] if category else [p for p in ROOT.iterdir() if p.is_dir()]
    result=[]
    for cat in cats:
        if not cat.exists(): continue
        for folder in cat.iterdir():
            if not folder.is_dir(): continue
            mf=folder/"manifest.json"
            if not mf.exists(): continue
            try:m=json.loads(mf.read_text(encoding="utf-8"))
            except Exception:continue
            m.setdefault("id",folder.name);m.setdefault("name",folder.name.title())
            m.setdefault("category",cat.name);m.setdefault("description","")
            m.setdefault("fields",[])
            m["path"]=str(folder)
            result.append(m)
    return result

def get_template(category,tid):
    return next((x for x in list_templates(category) if x["id"]==tid),None)
