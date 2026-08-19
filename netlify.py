import aiohttp, io, zipfile, asyncio
from pathlib import Path
from config import NETLIFY_AUTH_TOKEN, NETLIFY_SITE_ID

async def deploy_directory(directory:Path):
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,"w",zipfile.ZIP_DEFLATED) as z:
        for p in directory.rglob("*"):
            if p.is_file():
                z.write(p,p.relative_to(directory).as_posix())
    payload=buf.getvalue()
    headers={"Authorization":f"Bearer {NETLIFY_AUTH_TOKEN}","Content-Type":"application/zip"}
    async with aiohttp.ClientSession(headers=headers) as s:
        async with s.post(f"https://api.netlify.com/api/v1/sites/{NETLIFY_SITE_ID}/deploys",data=payload) as r:
            if r.status>=300:raise RuntimeError(f"Netlify deploy {r.status}: {await r.text()}")
            deploy=await r.json()
        did=deploy["id"]
        for _ in range(60):
            await asyncio.sleep(1)
            async with s.get(f"https://api.netlify.com/api/v1/sites/{NETLIFY_SITE_ID}/deploys/{did}") as r:
                if r.status>=300:raise RuntimeError(f"Netlify status {r.status}: {await r.text()}")
                d=await r.json()
            if d.get("state")=="ready":return d
            if d.get("state") in {"error","failed"}:
                raise RuntimeError(f"Netlify deploy failed: {d}")
    raise TimeoutError("Netlify deploy belum ready setelah 60 detik.")
