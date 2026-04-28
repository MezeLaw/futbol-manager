import os
import logging
from datetime import date, timedelta

from . import client, database

log = logging.getLogger(__name__)

GROUP_JID = os.environ["GROUP_JID"]
REOPEN_MODE = os.environ.get("REOPEN_MODE", "players")
REOPEN_PLAYERS = int(os.environ.get("REOPEN_PLAYERS", "12"))


def _next_game_date(poll_day: str) -> date:
    """Dado 'lunes' o 'viernes' calcula la fecha del próximo partido."""
    today = date.today()
    # lunes → miércoles (+2), viernes → domingo (+2)
    return today + timedelta(days=2)


def _build_lista(votos: dict[str, list[str]]) -> str:
    si = votos["SI"]
    no = votos["NO"]
    lines = [f"*Confirmados ({len(si)}):*"]
    for i, jid in enumerate(si, 1):
        lines.append(f"  {i}. {jid.split('@')[0]}")
    if no:
        lines.append(f"\n*No pueden ({len(no)}):*")
        for jid in no:
            lines.append(f"  - {jid.split('@')[0]}")
    return "\n".join(lines)


def send_encuesta(fecha_partido: date) -> None:
    """Pone el grupo en announcement y envía la poll. Guarda IDs en DB."""
    database.init_db()
    dia = fecha_partido.strftime("%A %d/%m").capitalize()
    question = f"¿Jugás el {dia} en La Masia?"

    client.set_group_announcement(GROUP_JID, announcement=True)

    poll_id = client.send_poll(GROUP_JID, question, ["SI", "NO"])
    partido_id = database.upsert_partido(fecha_partido, poll_message_id=poll_id)
    log.info("Encuesta enviada. partido_id=%s poll_id=%s", partido_id, poll_id)


def handle_webhook(payload: dict) -> None:
    """Procesa eventos entrantes de Evolution API."""
    event = payload.get("event")
    if event != "messages.update":
        return

    for update in payload.get("data", []):
        poll_updates = update.get("update", {}).get("pollUpdates")
        if not poll_updates:
            continue

        poll_message_id = update.get("key", {}).get("id")
        partido = database.get_partido_by_poll_id(poll_message_id)
        if not partido:
            log.warning("Poll desconocida: %s", poll_message_id)
            continue

        partido_id = partido["id"]
        fecha_partido = date.fromisoformat(partido["fecha"])

        for vote_event in poll_updates:
            player_jid = vote_event.get("pollUpdateMessageKey", {}).get("participant", "")
            selected = vote_event.get("vote", {}).get("selectedOptions", [])
            respuesta = selected[0] if selected else "NO"
            if respuesta not in ("SI", "NO"):
                continue
            database.upsert_voto(partido_id, player_jid, respuesta)
            log.info("Voto registrado: %s → %s", player_jid, respuesta)

        _refresh_lista(partido_id, fecha_partido, partido["lista_message_id"])


def _refresh_lista(partido_id: int, fecha_partido: date, lista_message_id: str | None) -> None:
    votos = database.get_votos(partido_id)
    texto = _build_lista(votos)

    if lista_message_id:
        try:
            client.delete_message(GROUP_JID, lista_message_id)
        except Exception as e:
            log.warning("No se pudo borrar lista anterior: %s", e)

    new_id = client.send_text(GROUP_JID, texto)
    database.set_lista_message_id(partido_id, new_id)

    confirmados = len(votos["SI"])
    if REOPEN_MODE == "players" and confirmados >= REOPEN_PLAYERS:
        client.set_group_announcement(GROUP_JID, announcement=False)
        log.info("Grupo reabierto: %d confirmados", confirmados)
