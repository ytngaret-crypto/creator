import asyncio
import logging
import threading
import time

import uvicorn
from fastapi import FastAPI

from config import PORT
from db import init_db
from main import bot, dp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("web_creator")

app = FastAPI(title="Web Creator Bot")


@app.get("/")
async def health():
    return {"status": "online", "service": "Web Creator Bot"}


class RailwayUvicornServer(uvicorn.Server):
    """Run Uvicorn outside the main thread without installing signal handlers."""
    def install_signal_handlers(self):
        pass


def start_web_server():
    config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=PORT,
        log_level="info",
        access_log=False,
    )
    server = RailwayUvicornServer(config)
    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()

    # Wait briefly so a bind/startup error is visible before Telegram polling starts.
    for _ in range(100):
        if server.started:
            log.info("Web server ready on port %s", PORT)
            return server, thread
        if not thread.is_alive():
            raise RuntimeError("Uvicorn berhenti sebelum server siap.")
        time.sleep(0.05)

    if not server.started:
        log.warning("Web server masih starting; Telegram bot akan tetap dijalankan.")
    return server, thread


async def start_bot():
    # Remove an old webhook before switching to long polling.
    # This is safe and prevents webhook/polling conflicts.
    await bot.delete_webhook(drop_pending_updates=False)

    me = await bot.get_me()
    log.info("Telegram bot authenticated: @%s (id=%s)", me.username, me.id)
    log.info("Starting Telegram polling...")

    # Polling stays on Python's MAIN THREAD. Signal handling is disabled here
    # because Railway/Uvicorn owns the process lifecycle.
    await dp.start_polling(
        bot,
        handle_signals=False,
        close_bot_session=True,
    )


async def main():
    init_db()
    server = None
    try:
        server, _ = start_web_server()
        await start_bot()
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception("Telegram bot berhenti karena error.")
        raise
    finally:
        if server is not None:
            server.should_exit = True
        try:
            await bot.session.close()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
