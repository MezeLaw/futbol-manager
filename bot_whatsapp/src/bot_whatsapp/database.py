import sqlite3
from pathlib import Path
from datetime import date

DB_PATH = Path(__file__).parent.parent.parent.parent / "data" / "futbol.db"
CSV_PATH = DB_PATH.parent / "jugadores.csv"


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
                fecha DATE NOT NULL,
                poll_message_id TEXT,
                lista_message_id TEXT,
                message_secret BLOB,
                titulares INTEGER,
                question TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS votos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                partido_id INTEGER NOT NULL REFERENCES partidos(id),
                player_jid TEXT NOT NULL,
                player_name TEXT,
                respuesta TEXT NOT NULL CHECK(respuesta IN ('SI', 'NO')),
                last_si_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(partido_id, player_jid)
            );

            CREATE TABLE IF NOT EXISTS jugadores (
                jid TEXT PRIMARY KEY,
                apodo TEXT NOT NULL,
                fis REAL NOT NULL DEFAULT 5.0,
                vel REAL NOT NULL DEFAULT 5.0,
                pot REAL NOT NULL DEFAULT 5.0,
                tec REAL NOT NULL DEFAULT 5.0,
                col REAL NOT NULL DEFAULT 5.0,
                arq REAL NOT NULL DEFAULT 5.0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Migración: jugadores v1 (fis,tec,col,reg,arq) → v2 (fis,vel,pot,tec,col,arq)
        j_cols = {r[1] for r in conn.execute("PRAGMA table_info(jugadores)").fetchall()}
        if "reg" in j_cols and "vel" not in j_cols:
            conn.executescript("""
                CREATE TABLE jugadores_new (
                    jid TEXT PRIMARY KEY,
                    apodo TEXT NOT NULL,
                    fis REAL NOT NULL DEFAULT 5.0,
                    vel REAL NOT NULL DEFAULT 5.0,
                    pot REAL NOT NULL DEFAULT 5.0,
                    tec REAL NOT NULL DEFAULT 5.0,
                    col REAL NOT NULL DEFAULT 5.0,
                    arq REAL NOT NULL DEFAULT 5.0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO jugadores_new (jid, apodo, fis, tec, col, arq, updated_at)
                    SELECT jid, apodo, fis, tec, col, arq, updated_at FROM jugadores;
                DROP TABLE jugadores;
                ALTER TABLE jugadores_new RENAME TO jugadores;
            """)

        # Migración: eliminar UNIQUE en partidos.fecha (esquema viejo)
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='partidos'"
        ).fetchone()
        if row and "fecha DATE NOT NULL UNIQUE" in (row["sql"] or ""):
            cols = [r[1] for r in conn.execute("PRAGMA table_info(partidos)").fetchall()]
            col_list = ", ".join(
                c for c in ["id", "fecha", "poll_message_id", "lista_message_id",
                             "message_secret", "titulares", "question", "created_at"]
                if c in cols
            )
            conn.executescript(f"""
                CREATE TABLE partidos_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fecha DATE NOT NULL,
                    poll_message_id TEXT,
                    lista_message_id TEXT,
                    message_secret BLOB,
                    titulares INTEGER,
                    question TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO partidos_new ({col_list}) SELECT {col_list} FROM partidos;
                DROP TABLE partidos;
                ALTER TABLE partidos_new RENAME TO partidos;
            """)


def insert_partido(fecha: date, poll_message_id: str, message_secret: bytes, titulares: int, question: str) -> int:
    with get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO partidos (fecha, poll_message_id, message_secret, titulares, question)
            VALUES (?, ?, ?, ?, ?)
            """,
            (fecha.isoformat(), poll_message_id, message_secret, titulares, question),
        )
        return cursor.lastrowid


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


def upsert_voto(partido_id: int, player_jid: str, respuesta: str, player_name: str | None = None) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO votos (partido_id, player_jid, player_name, respuesta, last_si_at)
            VALUES (?, ?, ?, ?, CASE WHEN ? = 'SI' THEN CURRENT_TIMESTAMP ELSE NULL END)
            ON CONFLICT(partido_id, player_jid) DO UPDATE SET
                player_name = COALESCE(excluded.player_name, player_name),
                respuesta = excluded.respuesta,
                last_si_at = CASE WHEN excluded.respuesta = 'SI' THEN CURRENT_TIMESTAMP ELSE last_si_at END,
                updated_at = CURRENT_TIMESTAMP
            """,
            (partido_id, player_jid, player_name, respuesta, respuesta),
        )


def get_votos_si(partido_id: int) -> list[tuple[str, str]]:
    """Retorna lista de (jid, nombre) de quienes votaron SI, ordenados por last_si_at."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT player_jid, player_name FROM votos
            WHERE partido_id = ? AND respuesta = 'SI'
            ORDER BY last_si_at
            """,
            (partido_id,),
        ).fetchall()
    return [(r["player_jid"], r["player_name"] or r["player_jid"].split("@")[0]) for r in rows]


def get_votos_no(partido_id: int) -> list[tuple[str, str]]:
    """Retorna lista de (jid, nombre) de quienes votaron NO."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT player_jid, player_name FROM votos
            WHERE partido_id = ? AND respuesta = 'NO'
            ORDER BY updated_at
            """,
            (partido_id,),
        ).fetchall()
    return [(r["player_jid"], r["player_name"] or r["player_jid"].split("@")[0]) for r in rows]


def get_partido_by_id(partido_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM partidos WHERE id = ?",
            (partido_id,),
        ).fetchone()


def get_partido_by_poll_id(poll_message_id: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM partidos WHERE poll_message_id = ?",
            (poll_message_id,),
        ).fetchone()


def get_latest_partido() -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM partidos ORDER BY id DESC LIMIT 1"
        ).fetchone()


def get_partidos() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM partidos ORDER BY id DESC LIMIT 10"
        ).fetchall()


def upsert_jugador(jid: str, apodo: str, fis: float, vel: float, pot: float, tec: float, col: float, arq: float) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO jugadores (jid, apodo, fis, vel, pot, tec, col, arq)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(jid) DO UPDATE SET
                apodo = excluded.apodo,
                fis = excluded.fis,
                vel = excluded.vel,
                pot = excluded.pot,
                tec = excluded.tec,
                col = excluded.col,
                arq = excluded.arq,
                updated_at = CURRENT_TIMESTAMP
            """,
            (jid, apodo, fis, vel, pot, tec, col, arq),
        )


def get_jugador_by_jid(jid: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM jugadores WHERE jid = ?", (jid,)
        ).fetchone()


def get_all_jugadores() -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM jugadores ORDER BY apodo COLLATE NOCASE"
        ).fetchall()


def get_jugadores_by_jids(jids: list[str]) -> dict[str, sqlite3.Row]:
    if not jids:
        return {}
    placeholders = ",".join("?" * len(jids))
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM jugadores WHERE jid IN ({placeholders})", jids
        ).fetchall()
    return {r["jid"]: r for r in rows}


def get_jugador_by_apodo(apodo: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM jugadores WHERE apodo = ? COLLATE NOCASE", (apodo,)
        ).fetchone()


def delete_voto(partido_id: int, jid: str) -> bool:
    with get_conn() as conn:
        cursor = conn.execute(
            "DELETE FROM votos WHERE partido_id = ? AND player_jid = ?",
            (partido_id, jid),
        )
    return cursor.rowcount > 0
