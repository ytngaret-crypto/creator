import threading, asyncio
import uvicorn
from fastapi import FastAPI
from config import PORT
from db import init_db
from main import run

app=FastAPI(title="Web Creator Bot")

@app.get("/")
def health():return {"status":"online","service":"Web Creator Bot"}

def start_bot():asyncio.run(run())

if __name__=="__main__":
    init_db()
    threading.Thread(target=start_bot,daemon=True).start()
    uvicorn.run(app,host="0.0.0.0",port=PORT)
