import csv
import os
import hmac as _hmac
import hashlib
import logging
import random
import threading
from datetime import date
from itertools import combinations

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import client, database

log = logging.getLogger(__name__)

GROUP_JID = os.environ["GROUP_JID"]
REOPEN_MODE = os.environ.get("REOPEN_MODE", "players")
REOPEN_PLAYERS = int(os.environ.get("REOPEN_PLAYERS", "12"))
POLL_OPTIONS = ["SI", "NO"]
_DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


_AREAS = ("fis", "vel", "pot", "tec", "col", "arq")
_AREA_LABELS = ("Fis", "Vel", "Pot", "Tec", "Col", "Arq")
# arquero pesa doble porque requiere un buen representante por equipo
_AREA_WEIGHTS = (1.0, 1.0, 1.0, 1.0, 1.0, 2.0)

_SETSCORE_HELP = (
    "Uso: !setscore @jugador <Fis> <Vel> <Pot> <Tec> <Col> <Arq>\n"
    "Ej:  !setscore @Martin 7.5 8 6 7 9 4\n"
    "Rangos: 0.0 – 10.0 por área"
)


def _balance_teams(players: list[dict], half: int) -> tuple[list[dict], list[dict]]:
    n = len(players)
    indices = list(range(n))
    best_teams: tuple[list[dict], list[dict]] | None = None
    best_score = float("inf")

    for combo in combinations(indices, half):
        combo_set = set(combo)
        a = [players[i] for i in combo]
        b = [players[i] for i in indices if i not in combo_set]
        score = sum(
            w * (sum(p[area] for p in a) / half - sum(p[area] for p in b) / (n - half)) ** 2
            for area, w in zip(_AREAS, _AREA_WEIGHTS)
        )
        if score < best_score:
            best_score = score
            best_teams = (a, b)

    return best_teams  # type: ignore[return-value]


def _format_equipos(team_a: list[dict], team_b: list[dict]) -> str:
    def avg(team: list[dict], area: str) -> float:
        return sum(p[area] for p in team) / len(team)

    def prom_total(team: list[dict]) -> float:
        return sum(avg(team, a) for a in _AREAS)

    lines = ["⚽ *Equipos*\n"]
    for label, team in (("A", team_a), ("B", team_b)):
        stats = " | ".join(f"{lbl}:{avg(team, area):.1f}" for area, lbl in zip(_AREAS, _AREA_LABELS))
        lines.append(f"*Equipo {label}* (prom: {prom_total(team):.1f})")
        for i, p in enumerate(team, 1):
            lines.append(f"  {i}. {p['name']}")
        lines.append(f"  {stats}\n")

    return "\n".join(lines)


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
    elif text == "!cargararchivo":
        path = database.CSV_PATH
        if not path.exists():
            client.send_text(remote_jid, f"Archivo no encontrado: {path}\nCrealo en data/jugadores.csv")
            return
        loaded = 0
        errors: list[str] = []
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for i, row in enumerate(reader, 1):
                try:
                    jid = f"{row['numero'].strip()}@s.whatsapp.net"
                    apodo = row["apodo"].strip()
                    fis, vel, pot, tec, col, arq = (float(row[k]) for k in ("fis", "vel", "pot", "tec", "col", "arq"))
                    database.upsert_jugador(jid, apodo, fis, vel, pot, tec, col, arq)
                    loaded += 1
                except (KeyError, ValueError) as e:
                    errors.append(f"Fila {i}: {e}")
        reply = f"✓ {loaded} jugadores cargados."
        if errors:
            reply += "\n⚠ Errores:\n" + "\n".join(errors)
        client.send_text(remote_jid, reply)
        log.info("CSV cargado: %d jugadores, %d errores", loaded, len(errors))
    elif text.startswith("!setscore"):
        m = msg.get("message", {})
        mentioned = (
            m.get("extendedTextMessage", {})
            .get("contextInfo", {})
            .get("mentionedJid", [])
        )
        args = raw_text[len("!setscore"):].strip().split()
        if not mentioned or len(args) < 6:
            client.send_text(remote_jid, _SETSCORE_HELP)
            return
        try:
            fis, vel, pot, tec, col, arq = (float(x) for x in args[-6:])
            for v in (fis, vel, pot, tec, col, arq):
                if not (0.0 <= v <= 10.0):
                    raise ValueError(f"Score fuera de rango: {v}")
        except ValueError as e:
            client.send_text(remote_jid, f"⚠ {e}\n{_SETSCORE_HELP}")
            return
        jid = mentioned[0]
        existing = database.get_jugador_by_jid(jid)
        apodo = existing["apodo"] if existing else jid.split("@")[0]
        database.upsert_jugador(jid, apodo, fis, vel, pot, tec, col, arq)
        client.send_text(
            remote_jid,
            f"✓ {apodo} → Fis:{fis} | Vel:{vel} | Pot:{pot} | Tec:{tec} | Col:{col} | Arq:{arq}",
        )
        log.info("Score actualizado: %s (%s)", apodo, jid)
    elif text == "!jugadores":
        jugadores = database.get_all_jugadores()
        if not jugadores:
            client.send_text(remote_jid, "No hay jugadores cargados. Usá !cargarArchivo.")
            return
        lines = [f"Plantel ({len(jugadores)} jugadores)\n"]
        for j in jugadores:
            total = j["fis"] + j["vel"] + j["pot"] + j["tec"] + j["col"] + j["arq"]
            scores = f"Fis:{j['fis']} Vel:{j['vel']} Pot:{j['pot']} Tec:{j['tec']} Col:{j['col']} Arq:{j['arq']}"
            lines.append(f"{j['apodo']:<14}{scores} | {total:.1f}")
        client.send_text(remote_jid, "\n".join(lines))
    elif text == "!equipos":
        partido = database.get_latest_partido()
        if not partido:
            client.send_text(remote_jid, "No hay partido registrado.")
            return
        titulares_count = partido["titulares"] or REOPEN_PLAYERS
        si = database.get_votos_si(partido["id"])
        titulares = si[:titulares_count]
        if len(titulares) < 2:
            client.send_text(remote_jid, "No hay suficientes jugadores confirmados.")
            return
        half = len(titulares) // 2
        jids = [jid for jid, _ in titulares]
        scores_map = database.get_jugadores_by_jids(jids)
        players = [
            {
                "jid": jid,
                # pushName → apodo en DB → número de teléfono
                "name": (
                    name if name != jid.split("@")[0]
                    else (scores_map[jid]["apodo"] if jid in scores_map else name)
                ),
                **({k: scores_map[jid][k] for k in _AREAS} if jid in scores_map else {k: 5.0 for k in _AREAS}),
            }
            for jid, name in titulares
        ]
        team_a, team_b = _balance_teams(players, half)
        client.send_text(remote_jid, _format_equipos(team_a, team_b))
        log.info("Equipos generados: %d vs %d", len(team_a), len(team_b))
    elif text.startswith("!agregar"):
        apodo = raw_text[len("!agregar"):].strip()
        if not apodo:
            client.send_text(remote_jid, "Uso: !agregar <apodo>")
            return
        partido = database.get_latest_partido()
        if not partido:
            client.send_text(remote_jid, "No hay partido registrado.")
            return
        jugador = database.get_jugador_by_apodo(apodo)
        jid = jugador["jid"] if jugador else f"manual:{apodo.lower()}"
        nombre = jugador["apodo"] if jugador else apodo
        database.upsert_voto(partido["id"], jid, "SI", nombre)
        _schedule_refresh(partido["id"])
        client.send_text(remote_jid, f"✓ {nombre} agregado a la lista.")
        log.info("Jugador agregado manualmente: %s (%s)", nombre, jid)
    elif text.startswith("!eliminar"):
        apodo = raw_text[len("!eliminar"):].strip()
        if not apodo:
            client.send_text(remote_jid, "Uso: !eliminar <apodo>")
            return
        partido = database.get_latest_partido()
        if not partido:
            client.send_text(remote_jid, "No hay partido registrado.")
            return
        jugador = database.get_jugador_by_apodo(apodo)
        jid = jugador["jid"] if jugador else f"manual:{apodo.lower()}"
        nombre = jugador["apodo"] if jugador else apodo
        eliminado = database.delete_voto(partido["id"], jid)
        if eliminado:
            _schedule_refresh(partido["id"])
            client.send_text(remote_jid, f"✓ {nombre} eliminado de la lista.")
            log.info("Jugador eliminado manualmente: %s (%s)", nombre, jid)
        else:
            client.send_text(remote_jid, f"⚠ {nombre} no estaba en la lista del partido actual.")


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
