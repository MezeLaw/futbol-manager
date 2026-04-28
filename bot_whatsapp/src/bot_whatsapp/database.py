import sqlite3
from pathlib import Path
from datetime import date

DB_PATH = Path(__file__).parent.parent.parent.parent / "data" / "futbol.db"


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS partidos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fecha DATE NOT NULL UNIQUE,
                poll_message_id TEXT,
                lista_message_id TEXT,
                message_secret BLOB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS votos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                partido_id INTEGER NOT NULL REFERENCES partidos(id),
                player_jid TEXT NOT NULL,
                respuesta TEXT NOT NULL CHECK(respuesta IN ('SI', 'NO')),
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(partido_id, player_jid)
            );
        """)
        try:
            conn.execute("ALTER TABLE partidos ADD COLUMN message_secret BLOB")
        except Exception:
            pass


def upsert_partido(fecha: date, poll_message_id: str | None = None, message_secret: bytes | None = None) -> int:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO partidos (fecha, poll_message_id, message_secret)
            VALUES (?, ?, ?)
            ON CONFLICT(fecha) DO UPDATE SET
                poll_message_id = COALESCE(excluded.poll_message_id, poll_message_id),
                message_secret = COALESCE(excluded.message_secret, message_secret)
            """,
            (fecha.isoformat(), poll_message_id, message_secret),
        )
        row = conn.execute("SELECT id FROM partidos WHERE fecha = ?", (fecha.isoformat(),)).fetchone()
        return row["id"]


def get_message_secret(poll_message_id: str) -> bytes | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT message_secret FROM partidos WHERE poll_message_id = ?",
            (poll_message_id,),
        ).fetchone()
    if row and row["message_secret"]:
        return bytes(row["message_secret"])
    return None


def set_lista_message_id(partido_id: int, message_id: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE partidos SET lista_message_id = ? WHERE id = ?",
            (message_id, partido_id),
        )


def upsert_voto(partido_id: int, player_jid: str, respuesta: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO votos (partido_id, player_jid, respuesta)
            VALUES (?, ?, ?)
            ON CONFLICT(partido_id, player_jid) DO UPDATE SET
                respuesta = excluded.respuesta,
                updated_at = CURRENT_TIMESTAMP
            """,
            (partido_id, player_jid, respuesta),
        )


def get_votos(partido_id: int) -> dict[str, list[str]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT player_jid, respuesta FROM votos WHERE partido_id = ?",
            (partido_id,),
        ).fetchall()
    result: dict[str, list[str]] = {"SI": [], "NO": []}
    for row in rows:
        result[row["respuesta"]].append(row["player_jid"])
    return result


def get_partido_by_poll_id(poll_message_id: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM partidos WHERE poll_message_id = ?",
            (poll_message_id,),
        ).fetchone()


def get_latest_partido() -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM partidos ORDER BY fecha DESC LIMIT 1"
        ).fetchone()


def get_partidos() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM partidos ORDER BY fecha DESC LIMIT 10"
        ).fetchall()
