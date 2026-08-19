import aiohttp, io, zipfile, asyncio
from pathlib import Path
from config import NETLIFY_AUTH_TOKEN, NETLIFY_SITE_ID

NETLIFY_API="https://api.netlify.com/api/v1"

async def deploy_directory(directory:Path):
    if not directory.exists():
        raise RuntimeError(f"Directory deploy tidak ditemukan: {directory}")
    if not (directory/"_redirects").exists():
        raise RuntimeError("File _redirects tidak ditemukan. Build website terlebih dahulu.")

    buf=io.BytesIO()
    with zipfile.ZipFile(buf,"w",zipfile.ZIP_DEFLATED) as z:
        for p in directory.rglob("*"):
            if p.is_file():
                z.write(p,p.relative_to(directory).as_posix())
    payload=buf.getvalue()
    if not payload:
        raise RuntimeError("ZIP deploy kosong.")

    headers={"Authorization":f"Bearer {NETLIFY_AUTH_TOKEN}","Content-Type":"application/zip"}
    timeout=aiohttp.ClientTimeout(total=180)
    async with aiohttp.ClientSession(headers=headers,timeout=timeout) as s:
        async with s.post(f"{NETLIFY_API}/sites/{NETLIFY_SITE_ID}/deploys",data=payload) as r:
            if r.status>=300:raise RuntimeError(f"Netlify deploy {r.status}: {await r.text()}")
            deploy=await r.json()
        did=deploy.get("id")
        if not did:raise RuntimeError(f"Netlify tidak mengembalikan deploy ID: {deploy}")
        for _ in range(180):
            await asyncio.sleep(1)
            async with s.get(f"{NETLIFY_API}/sites/{NETLIFY_SITE_ID}/deploys/{did}") as r:
                if r.status>=300:raise RuntimeError(f"Netlify status {r.status}: {await r.text()}")
                d=await r.json()
            if d.get("state")=="ready":return d
            if d.get("state") in {"error","failed"}:
                raise RuntimeError(f"Netlify deploy gagal: {d}")
    raise TimeoutError("Netlify deploy belum ready setelah 180 detik.")
