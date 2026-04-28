import os
import httpx
from datetime import date, timedelta
from dotenv import load_dotenv
from fastmcp import FastMCP

load_dotenv()

BOT_URL = os.environ.get("BOT_API_URL", "http://localhost:8000").rstrip("/")

mcp = FastMCP("futbol-manager")


def _bot(method: str, path: str, **kwargs):
    with httpx.Client(base_url=BOT_URL, timeout=30) as c:
        r = getattr(c, method)(path, **kwargs)
        r.raise_for_status()
        return r.json()


@mcp.tool()
def crear_encuesta(dia: str = "proximo") -> str:
    """
    Envía una encuesta de asistencia al grupo de WhatsApp.

    Args:
        dia: 'proximo' (default, usa date.today()+2 días),
             o una fecha en formato YYYY-MM-DD.
    """
    if dia == "proximo":
        fecha = (date.today() + timedelta(days=2)).isoformat()
    else:
        fecha = dia
    result = _bot("post", "/encuesta", json={"fecha": fecha})
    return f"Encuesta enviada para el {result['fecha']}."


@mcp.tool()
def ver_partidos() -> str:
    """Lista los últimos partidos con sus confirmados."""
    data = _bot("get", "/partidos")
    if not data:
        return "No hay partidos registrados."
    lines = []
    for p in data:
        lines.append(f"- {p['fecha']}: {p['confirmados']} confirmados")
    return "\n".join(lines)
