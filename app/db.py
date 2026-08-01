import sqlite3
import json
import logging
from contextlib import contextmanager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("bot")

DB_FILE = "bot_data.db"


def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY,
            region_key TEXT DEFAULT '',
            region_label TEXT DEFAULT '',
            selected_cities TEXT DEFAULT '[]',
            qualification TEXT DEFAULT '',
            gender TEXT DEFAULT '',
            specialization TEXT DEFAULT '',
            full_name TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            cv_file_id TEXT DEFAULT '',
            cv_file_name TEXT DEFAULT '',
            cv_text TEXT DEFAULT '',
            cv_language TEXT DEFAULT 'العربية',
            service_type TEXT DEFAULT '',
            email_subject TEXT DEFAULT '',
            email_body TEXT DEFAULT '',
            state TEXT DEFAULT '',
            state_context TEXT DEFAULT ''
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            service_type TEXT,
            region_label TEXT,
            selected_cities TEXT,
            qualification TEXT,
            gender TEXT,
            specialization TEXT,
            full_name TEXT,
            phone TEXT,
            cv_file_id TEXT,
            cv_file_name TEXT,
            email_subject TEXT,
            email_body TEXT,
            training_total_contacts INTEGER DEFAULT 0,
            training_email_contacts INTEGER DEFAULT 0,
            training_website_contacts INTEGER DEFAULT 0,
            training_unknown_contacts INTEGER DEFAULT 0,
            submitted_at TEXT
        )
    """)
    conn.commit()

    cols = [row[1] for row in c.execute("PRAGMA table_info(users)").fetchall()]
    if "state_context" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN state_context TEXT DEFAULT ''")
        conn.commit()
    if "cv_text" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN cv_text TEXT DEFAULT ''")
        conn.commit()
    if "cv_language" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN cv_language TEXT DEFAULT 'العربية'")
        conn.commit()

    conn.close()
    logger.info("Database initialized")


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        logger.exception("DB transaction failed, rolled back")
        raise
    finally:
        conn.close()


def get_or_create_user(chat_id: int) -> dict:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,)).fetchone()
        if row is None:
            conn.execute("INSERT INTO users (chat_id) VALUES (?)", (chat_id,))
            row = conn.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,)).fetchone()
        user = dict(row)
        user["selected_cities"] = json.loads(user["selected_cities"])
        return user


def update_user(chat_id: int, **fields):
    if not fields:
        return
    if "selected_cities" in fields:
        fields["selected_cities"] = json.dumps(fields["selected_cities"], ensure_ascii=False)

    columns = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [chat_id]

    with get_db() as conn:
        conn.execute(f"UPDATE users SET {columns} WHERE chat_id = ?", values)


def get_user_state(chat_id: int) -> str:
    with get_db() as conn:
        row = conn.execute("SELECT state FROM users WHERE chat_id = ?", (chat_id,)).fetchone()
        return row["state"] if row else ""


def get_user_state_full(chat_id: int):
    with get_db() as conn:
        row = conn.execute(
            "SELECT state, state_context FROM users WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        if not row:
            return "", ""
        return row["state"], row["state_context"]


def set_user_state(chat_id: int, state: str, context: str = ""):
    update_user(chat_id, state=state, state_context=context)


def save_application(application: dict):
    application = dict(application)
    application["selected_cities"] = json.dumps(application.get("selected_cities", []), ensure_ascii=False)
    columns = ", ".join(application.keys())
    placeholders = ", ".join("?" for _ in application)
    with get_db() as conn:
        conn.execute(
            f"INSERT INTO applications ({columns}) VALUES ({placeholders})",
            list(application.values())
        )


def get_user_requests(chat_id: int) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM applications WHERE chat_id = ? ORDER BY id DESC",
            (chat_id,)
        ).fetchall()
        return [dict(r) for r in rows]
