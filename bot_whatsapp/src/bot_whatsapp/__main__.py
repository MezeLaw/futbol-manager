import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

import uvicorn
from fastapi import FastAPI, Request, HTTPException

from . import database, handlers
from .scheduler import start_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    scheduler = start_scheduler()
    yield
    scheduler.shutdown()


app = FastAPI(title="Futbol Bot", lifespan=lifespan)


@app.post("/webhook")
async def webhook(request: Request):
    payload = await request.json()
    try:
        handlers.handle_webhook(payload)
    except Exception as e:
        log.exception("Error procesando webhook: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    return {"ok": True}


@app.get("/partidos")
async def listar_partidos():
    from . import database
    partidos = database.get_partidos()
    result = []
    for p in partidos:
        votos = database.get_votos(p["id"])
        result.append({"fecha": p["fecha"], "confirmados": len(votos["SI"])})
    return result


@app.post("/encuesta")
async def encuesta_manual(body: dict):
    """Endpoint interno para que el MCP dispare encuestas."""
    from datetime import date
    fecha_str = body.get("fecha")
    if fecha_str:
        fecha = date.fromisoformat(fecha_str)
    else:
        from datetime import timedelta
        fecha = date.today() + timedelta(days=2)
    handlers.send_encuesta(fecha)
    return {"ok": True, "fecha": fecha.isoformat()}


def main():
    uvicorn.run("bot_whatsapp.__main__:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
