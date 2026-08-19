import asyncio
import uvicorn
from fastapi import FastAPI

from config import PORT
from db import init_db
from main import bot, dp

app = FastAPI(title="Web Creator Bot")


@app.get("/")
async def health():
    return {"status": "online", "service": "Web Creator Bot"}


async def run_all():
    init_db()

    uv_config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=PORT,
        log_level="info",
    )
    web_server = uvicorn.Server(uv_config)

    # Telegram polling and Uvicorn run on the SAME asyncio main thread.
    # This avoids: RuntimeError: set_wakeup_fd only works in main thread
    bot_task = asyncio.create_task(
        dp.start_polling(
            bot,
            handle_signals=False,
            close_bot_session=False,
        )
    )
    web_task = asyncio.create_task(web_server.serve())

    done, pending = await asyncio.wait(
        {bot_task, web_task},
        return_when=asyncio.FIRST_EXCEPTION,
    )

    for task in pending:
        task.cancel()

    await asyncio.gather(*pending, return_exceptions=True)

    # Re-raise an actual task exception so Railway shows the real error.
    for task in done:
        exc = task.exception()
        if exc is not None:
            raise exc


if __name__ == "__main__":
    asyncio.run(run_all())
    
