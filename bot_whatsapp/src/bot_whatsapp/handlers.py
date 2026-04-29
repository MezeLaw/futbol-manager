import os
import hmac as _hmac
import hashlib
import logging
import random
import threading
from datetime import date

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import client, database

log = logging.getLogger(__name__)

GROUP_JID = os.environ["GROUP_JID"]
REOPEN_MODE = os.environ.get("REOPEN_MODE", "players")
REOPEN_PLAYERS = int(os.environ.get("REOPEN_PLAYERS", "12"))
POLL_OPTIONS = ["SI", "NO"]
_DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


def _build_lista(si: list[tuple[str, str]], no: list[tuple[str, str]], titulares: int = REOPEN_PLAYERS, question: str = "") -> str:
    confirmados = si[:titulares]
    suplentes = si[titulares:]

    lines = [f"*{question}*\n"]

    lines.append(f"*Confirmados ({len(confirmados)}/{titulares}):*")
    for i, (_, name) in enumerate(confirmados, 1):
        lines.append(f"  {i}. {name}")

    if suplentes:
        lines.append(f"\n*Suplentes ({len(suplentes)}):*")
        for i, (_, name) in enumerate(suplentes, 1):
            lines.append(f"  {i}. {name}")

    if no:
        lines.append(f"\n*NO 🏳️‍🌈 ({len(no)}):*")
        for _, name in no:
            lines.append(f"  - {name}")

    return "\n".join(lines)


def _dict_to_bytes(d: dict) -> bytes:
    return bytes([d[str(i)] for i in range(len(d))])


def _decrypt_poll_vote(
    message_secret: bytes,
    enc_payload: bytes,
    enc_iv: bytes,
    poll_msg_id: str,
    poll_creator_jid: str,
    voter_jid: str,
) -> str | None:
    # Baileys algorithm from process-message.js:
    # key0 = hmacSign(pollEncKey, zeros(32)) → HMAC(key=zeros, data=secret)
    # sign = pollMsgId + pollCreatorJid + voterJid + "Poll Vote" + 0x01
    # decKey = hmacSign(sign, key0) → HMAC(key=key0, data=sign)
    # aad = pollMsgId + "\0" + voterJid
    key0 = _hmac.new(b"\x00" * 32, message_secret, hashlib.sha256).digest()
    sign = (
        poll_msg_id.encode()
        + poll_creator_jid.encode()
        + voter_jid.encode()
        + b"Poll Vote"
        + b"\x01"
    )
    dec_key = _hmac.new(key0, sign, hashlib.sha256).digest()
    aad = (poll_msg_id + "\x00" + voter_jid).encode()

    try:
        decrypted = AESGCM(dec_key).decrypt(enc_iv, enc_payload, aad)
    except Exception as e:
        log.warning("AES-GCM decrypt failed: %s", e)
        return None

    if not decrypted:
        return None

    # Parse protobuf PollVoteMessage.selectedOptions (field 1, repeated bytes)
    selected_hashes: set[bytes] = set()
    i = 0
    while i < len(decrypted):
        tag = decrypted[i]; i += 1
        field_num = tag >> 3
        wire_type = tag & 0x7
        if wire_type == 2:
            length = 0; shift = 0
            while i < len(decrypted):
                b = decrypted[i]; i += 1
                length |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
            value = bytes(decrypted[i:i + length]); i += length
            if field_num == 1:
                selected_hashes.add(value)
        else:
            break

    for option in POLL_OPTIONS:
        if hashlib.sha256(option.encode()).digest() in selected_hashes:
            return option

    return None


def send_encuesta(fecha_partido: date, question: str | None = None, titulares: int | None = None, horario: str | None = None) -> None:
    """Pone el grupo en announcement y envía la poll. Guarda IDs en DB."""
    if question is None:
        dia = f"{_DIAS[fecha_partido.weekday()]} {fecha_partido.strftime('%d/%m')}"
        hora_part = f" a las {horario}hs" if horario else ""
        question = f"¿Jugás el {dia}{hora_part} en La Masia?"
    _titulares = titulares if titulares is not None else REOPEN_PLAYERS

    client.set_group_announcement(GROUP_JID, announcement=True)

    poll_id, message_secret = client.send_poll(GROUP_JID, question, POLL_OPTIONS)
    partido_id = database.insert_partido(fecha_partido, poll_id, message_secret, _titulares, question)
    log.info("Encuesta enviada. partido_id=%s poll_id=%s titulares=%s", partido_id, poll_id, _titulares)


def _get_message_text(msg: dict) -> str:
    m = msg.get("message") or {}
    return (
        m.get("conversation")
        or m.get("extendedTextMessage", {}).get("text")
        or ""
    ).strip()


def _handle_command(msg: dict) -> None:
    key = msg.get("key", {})
    if not key.get("fromMe"):
        return
    remote_jid = key.get("remoteJid", "")
    if not remote_jid.endswith("@g.us"):
        return

    raw_text = _get_message_text(msg)
    text = raw_text.lower()
    if not text.startswith("!"):
        return

    message_id = key.get("id", "")
    try:
        client.delete_message(remote_jid, message_id)
    except Exception as e:
        log.warning("No se pudo borrar comando %s: %s", text, e)

    if text.startswith("!votacion"):
        args = raw_text[len("!votacion"):].strip()
        question = None
        titulares = None
        if args:
            parts = [p.strip() for p in args.split("|")]
            question = parts[0] or None
            if len(parts) > 1 and parts[1].isdigit():
                titulares = int(parts[1])
        send_encuesta(date.today(), question=question, titulares=titulares)
    elif text == "!lista":
        partido = database.get_latest_partido()
        if not partido:
            client.send_text(remote_jid, "No hay partido registrado.")
            return
        si = database.get_votos_si(partido["id"])
        no = database.get_votos_no(partido["id"])
        titulares = partido["titulares"] or REOPEN_PLAYERS
        question = partido["question"] or ""
        client.send_text(remote_jid, _build_lista(si, no, titulares, question))
    elif text == "!cerrar":
        client.set_group_announcement(remote_jid, announcement=True)
        log.info("Grupo cerrado por comando manual")
    elif text == "!abrir":
        client.set_group_announcement(remote_jid, announcement=False)
        log.info("Grupo abierto por comando manual")


def _handle_poll_vote(msg: dict) -> None:
    key = msg.get("key", {})
    remote_jid = key.get("remoteJid", "")
    if not remote_jid.endswith("@g.us"):
        return

    voter_jid = key.get("participant", "")
    poll_update = msg.get("message", {}).get("pollUpdateMessage", {})
    creation_key = poll_update.get("pollCreationMessageKey", {})
    poll_id = creation_key.get("id", "")
    poll_creator_jid = creation_key.get("participant", "")
    vote = poll_update.get("vote", {})
    enc_payload = vote.get("encPayload")
    enc_iv = vote.get("encIv")

    if not all([voter_jid, poll_id, poll_creator_jid, enc_payload, enc_iv]):
        return

    message_secret = database.get_message_secret(poll_id)
    if not message_secret:
        log.warning("messageSecret no encontrado para poll %s", poll_id)
        return

    respuesta = _decrypt_poll_vote(
        message_secret,
        _dict_to_bytes(enc_payload),
        _dict_to_bytes(enc_iv),
        poll_id,
        poll_creator_jid,
        voter_jid,
    )
    if respuesta is None:
        log.warning("No se pudo desencriptar voto de %s", voter_jid)
        return

    partido = database.get_partido_by_poll_id(poll_id)
    if not partido:
        log.warning("Poll desconocida: %s", poll_id)
        return

    player_name = msg.get("pushName") or None
    database.upsert_voto(partido["id"], voter_jid, respuesta, player_name)
    log.info("Voto registrado: %s (%s) → %s", voter_jid, player_name, respuesta)

    _schedule_refresh(partido["id"])


def _as_list(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return []


def handle_webhook(payload: dict) -> None:
    """Procesa eventos entrantes de Evolution API."""
    event = payload.get("event")
    data = _as_list(payload.get("data"))

    if event == "messages.upsert":
        for msg in data:
            if msg.get("messageType") == "pollUpdateMessage":
                _handle_poll_vote(msg)
            else:
                _handle_command(msg)
        return

    if event != "messages.update":
        return

    for update in data:
        poll_updates = update.get("update", {}).get("pollUpdates")
        if not poll_updates:
            continue

        poll_message_id = update.get("key", {}).get("id")
        partido = database.get_partido_by_poll_id(poll_message_id)
        if not partido:
            log.warning("Poll desconocida: %s", poll_message_id)
            continue

        partido_id = partido["id"]

        for vote_event in poll_updates:
            player_jid = vote_event.get("pollUpdateMessageKey", {}).get("participant", "")
            selected = vote_event.get("vote", {}).get("selectedOptions", [])
            respuesta = selected[0] if selected else "NO"
            if respuesta not in ("SI", "NO"):
                continue
            database.upsert_voto(partido_id, player_jid, respuesta)
            log.info("Voto registrado: %s → %s", player_jid, respuesta)

        _schedule_refresh(partido_id)


_timers: dict[int, threading.Timer] = {}
_timers_lock = threading.Lock()


def _schedule_refresh(partido_id: int) -> None:
    delay = random.uniform(10, 20)
    with _timers_lock:
        existing = _timers.get(partido_id)
        if existing:
            existing.cancel()
        t = threading.Timer(delay, _refresh_lista, args=[partido_id])
        _timers[partido_id] = t
        t.start()
    log.info("Refresh programado en %.1fs para partido %d", delay, partido_id)


def _refresh_lista(partido_id: int) -> None:
    with _timers_lock:
        _timers.pop(partido_id, None)

    partido = database.get_partido_by_id(partido_id)
    lista_message_id = partido["lista_message_id"] if partido else None
    titulares = (partido["titulares"] or REOPEN_PLAYERS) if partido else REOPEN_PLAYERS
    question = (partido["question"] or "") if partido else ""

    si = database.get_votos_si(partido_id)
    no = database.get_votos_no(partido_id)
    texto = _build_lista(si, no, titulares, question)

    if lista_message_id:
        try:
            client.delete_message(GROUP_JID, lista_message_id)
        except Exception as e:
            log.warning("No se pudo borrar lista anterior: %s", e)

    new_id = client.send_text(GROUP_JID, texto)
    database.set_lista_message_id(partido_id, new_id)

    if REOPEN_MODE == "players" and len(si) >= titulares:
        client.set_group_announcement(GROUP_JID, announcement=False)
        log.info("Grupo reabierto: %d confirmados", len(si))
