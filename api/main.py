import asyncio

from fastapi import FastAPI, HTTPException, Request
from telegram import Update

from bot import create_application, init_db

app = FastAPI()
_tg_app = None
_init_lock = asyncio.Lock()


async def get_tg_app():
    global _tg_app
    if _tg_app is not None:
        return _tg_app

    async with _init_lock:
        if _tg_app is None:
            init_db()
            _tg_app = create_application()
            await _tg_app.initialize()
            await _tg_app.start()
    return _tg_app


@app.get("/")
async def health():
    return {"ok": True}


@app.post("/webhook")
async def webhook(request: Request):
    payload = await request.json()
    tg_app = await get_tg_app()
    try:
        update = Update.de_json(payload, tg_app.bot)
        await tg_app.process_update(update)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {"ok": True}
