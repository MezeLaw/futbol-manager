import os
import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.environ["EVOLUTION_API_URL"].rstrip("/")
API_KEY = os.environ["EVOLUTION_API_KEY"]
INSTANCE = os.environ["EVOLUTION_INSTANCE"]

_HEADERS = {"apikey": API_KEY, "Content-Type": "application/json"}


def _client() -> httpx.Client:
    return httpx.Client(base_url=BASE_URL, headers=_HEADERS, timeout=30)


def send_poll(group_jid: str, question: str, options: list[str]) -> tuple[str, bytes]:
    """Envía una poll nativa al grupo. Retorna (message_id, message_secret)."""
    payload = {
        "number": group_jid,
        "name": question,
        "selectableCount": 1,
        "values": options,
    }
    with _client() as c:
        r = c.post(f"/message/sendPoll/{INSTANCE}", json=payload)
        r.raise_for_status()
        data = r.json()
    poll_id = data["key"]["id"]
    secret_dict = data.get("message", {}).get("messageContextInfo", {}).get("messageSecret", {})
    message_secret = bytes([secret_dict[str(i)] for i in range(len(secret_dict))])
    return poll_id, message_secret


def send_text(group_jid: str, text: str) -> str:
    """Envía un mensaje de texto al grupo. Retorna el message_id."""
    payload = {"number": group_jid, "text": text}
    with _client() as c:
        r = c.post(f"/message/sendText/{INSTANCE}", json=payload)
        r.raise_for_status()
        data = r.json()
    return data["key"]["id"]


def delete_message(group_jid: str, message_id: str) -> None:
    """Elimina un mensaje enviado por el bot."""
    payload = {"id": message_id, "remoteJid": group_jid, "fromMe": True}
    with _client() as c:
        r = c.request("DELETE", f"/chat/deleteMessageForEveryone/{INSTANCE}", json=payload)
        r.raise_for_status()


def set_group_announcement(group_jid: str, announcement: bool) -> None:
    """Cambia el modo del grupo: announcement=True → solo admins escriben."""
    setting = "announcement" if announcement else "not_announcement"
    with _client() as c:
        r = c.post(
            f"/group/updateSetting/{INSTANCE}",
            params={"groupJid": group_jid},
            json={"action": setting},
        )
        r.raise_for_status()


def phone_to_jid(phone: str) -> str:
    return phone.replace("+", "").replace(" ", "").replace("-", "") + "@s.whatsapp.net"


def get_group_participants(group_jid: str) -> list[dict]:
    """Retorna lista de participantes del grupo con id (@lid) y phoneNumber (@s.whatsapp.net)."""
    with _client() as c:
        r = c.get(f"/group/participants/{INSTANCE}", params={"groupJid": group_jid})
        r.raise_for_status()
    return r.json()
