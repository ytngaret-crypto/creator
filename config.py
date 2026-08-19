import os
from dotenv import load_dotenv
load_dotenv()

BOT_TOKEN=os.getenv("BOT_TOKEN","").strip()
FORCE_JOIN_CHANNEL=os.getenv("FORCE_JOIN_CHANNEL","").strip()
NETLIFY_AUTH_TOKEN=os.getenv("NETLIFY_AUTH_TOKEN","").strip()
NETLIFY_SITE_ID=os.getenv("NETLIFY_SITE_ID","").strip()
PUBLIC_BASE_URL=os.getenv("PUBLIC_BASE_URL","").rstrip("/")
PORT=int(os.getenv("PORT","8000"))
ADMIN_IDS={int(x.strip()) for x in os.getenv("ADMIN_IDS","").split(",") if x.strip().isdigit()}

missing=[k for k,v in {
 "BOT_TOKEN":BOT_TOKEN,
 "NETLIFY_AUTH_TOKEN":NETLIFY_AUTH_TOKEN,
 "NETLIFY_SITE_ID":NETLIFY_SITE_ID,
 "PUBLIC_BASE_URL":PUBLIC_BASE_URL
}.items() if not v]
if missing:
    raise RuntimeError("Environment belum lengkap: "+", ".join(missing))
