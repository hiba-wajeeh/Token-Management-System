import sqlite3
from datetime import datetime, date
import os, sys
import psycopg
from psycopg.rows import dict_row
import configparser
from datetime import date


def app_dir():
    if getattr(sys, "frozen", False):
        # Running as EXE
        return os.path.dirname(sys.executable)
    # Running as normal Python
    return os.path.dirname(os.path.abspath(__file__))


DB_PATH = os.path.join(app_dir(), "qms.sqlite")
cfg = configparser.ConfigParser()
cfg.read(os.path.join(app_dir(), "config.ini"))

PG_HOST = cfg.get("postgres", "host")
PG_PORT = cfg.getint("postgres", "port")
PG_DB   = cfg.get("postgres", "db")
PG_USER = cfg.get("postgres", "user")
PG_PASS = cfg.get("postgres", "password")

def vacuum_db(conn: sqlite3.Connection):
    conn.execute("VACUUM")

def wal_checkpoint_truncate(conn: sqlite3.Connection):
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")

def connect():
    return psycopg.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PASS,
        row_factory=dict_row,
        autocommit=False
    )


def init_db(conn, appt_start: int, walkin_start: int):
    cur = conn.cursor()

    # ------------------ state table ------------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        session_date DATE NOT NULL,
        recall_seq INTEGER NOT NULL DEFAULT 0,
        last_recall_counter TEXT,
        next_appt_token INTEGER NOT NULL,
        next_walkin_token INTEGER NOT NULL
    )
    """)

    # ------------------ tokens table ------------------
    cur.execute("""
    CREATE TABLE IF NOT EXISTS tokens (
        id SERIAL PRIMARY KEY,
        token_no INTEGER NOT NULL,
        dept TEXT NOT NULL,

        -- NEW: stage pipeline
        stage TEXT NOT NULL DEFAULT 'reception',   -- reception, nursing (future: doctor, etc.)

        priority INTEGER NOT NULL,      -- 1=appointment, 2=walkin
        status TEXT NOT NULL,           -- WAITING, CALLED, SERVED
        created_at TIMESTAMP NOT NULL,
        called_at TIMESTAMP,
        called_by TEXT,
        served_at TIMESTAMP,
        transferred_at TIMESTAMP
    )
    """)

    # ------------------ schema migrations (safe) ------------------
    # If DB already existed before stages, ensure columns exist.
    # (Postgres supports ADD COLUMN IF NOT EXISTS)
    cur.execute("ALTER TABLE tokens ADD COLUMN IF NOT EXISTS stage TEXT NOT NULL DEFAULT 'reception'")
    cur.execute("ALTER TABLE tokens ADD COLUMN IF NOT EXISTS served_at TIMESTAMP")
    cur.execute("ALTER TABLE tokens ADD COLUMN IF NOT EXISTS transferred_at TIMESTAMP")


    # ------------------ ensure single state row ------------------
        # ------------------ ensure single state row ------------------
    cur.execute("SELECT COUNT(*) AS count FROM state WHERE id = 1")
    row = cur.fetchone()
    if row["count"] == 0:
        cur.execute("""
            INSERT INTO state (id, session_date, next_appt_token, next_walkin_token)
            VALUES (1, %s, %s, %s)
        """, (date.today(), appt_start, walkin_start))

    conn.commit()

def daily_cleanup_if_needed(conn, appt_start: int, walkin_start: int):
    cur = conn.cursor()

    cur.execute("SELECT session_date FROM state WHERE id = 1")
    row = cur.fetchone()
    if not row:
        return False

    today = date.today()

    # row is a dict because of dict_row
    if row["session_date"] != today:
        cur.execute("DELETE FROM tokens")

        cur.execute("""
            UPDATE state
            SET session_date = %s,
                next_appt_token = %s,
                next_walkin_token = %s
            WHERE id = 1
        """, (today, appt_start, walkin_start))

        conn.commit()
        return True

    return False

def create_token_atomic(conn, dept, visit_type, appt_start, walkin_start):
    vt = (visit_type or "walkin").lower().strip()
    if vt not in ("appointment", "walkin"):
        vt = "walkin"

    priority = 1 if vt == "appointment" else 2
    col = "next_appt_token" if vt == "appointment" else "next_walkin_token"
    fallback_start = appt_start if vt == "appointment" else walkin_start

    cur = conn.cursor()

    # 🔒 ROW LOCK (this replaces BEGIN IMMEDIATE)
    cur.execute(f"""
        SELECT {col}
        FROM state
        WHERE id = 1
        FOR UPDATE
    """)
    row = cur.fetchone()
    next_no = row[col] if row and row[col] is not None else fallback_start

    now = datetime.now()

    cur.execute("""
        INSERT INTO tokens (token_no, dept, stage, priority, status, created_at)
        VALUES (%s, %s, %s, %s, 'WAITING', %s)
    """, (next_no, dept, 'reception', priority, now))

    cur.execute(f"""
        UPDATE state
        SET {col} = %s
        WHERE id = 1
    """, (next_no + 1,))

    conn.commit()
    return int(next_no)

def call_next_atomic(conn, dept, counter, visit_type=None, stage: str = 'reception'):
    vt = (visit_type or "auto").lower().strip()
    cur = conn.cursor()

    if vt == "appointment":
        sql = """
            SELECT id, token_no
            FROM tokens
            WHERE dept=%s AND stage=%s AND status='WAITING' AND priority=1
            ORDER BY created_at ASC
            LIMIT 1
            FOR UPDATE
        """
        params = (dept, stage)
    elif vt == "walkin":
        sql = """
            SELECT id, token_no
            FROM tokens
            WHERE dept=%s AND stage=%s AND status='WAITING' AND priority=2
            ORDER BY created_at ASC
            LIMIT 1
            FOR UPDATE
        """
        params = (dept, stage)
    else:
        if stage == "nursing":
            sql = """
                SELECT id, token_no
                FROM tokens
                WHERE dept=%s
                AND stage=%s
                AND status='WAITING'
                AND transferred_at IS NOT NULL
                ORDER BY transferred_at ASC
                LIMIT 1
                FOR UPDATE
            """
            params = (dept, stage)

        else:
            # 🧾 Reception = priority-aware
            sql = """
                SELECT id, token_no
                FROM tokens
                WHERE dept=%s AND stage=%s AND status='WAITING'
                ORDER BY priority ASC, created_at ASC
                LIMIT 1
                FOR UPDATE
            """
            params = (dept, stage)


    cur.execute(sql, params)
    row = cur.fetchone()
    if not row:
        conn.commit()
        return None

    now = datetime.now()

    cur.execute("""
        UPDATE tokens
        SET status='CALLED', called_at=%s, called_by=%s
        WHERE id=%s
    """, (now, counter, row["id"]))

    conn.commit()
    return int(row["token_no"])

def transfer_last_called_to_stage(conn, dept: str, counter: str, from_stage: str, to_stage: str) -> bool:
    """
    When Reception clicks NEXT again, we "finish" the previous CALLED token at reception
    and push it to nursing WAITING queue.
    Returns True if something was transferred.
    """
    cur = conn.cursor()

    cur.execute("""
        SELECT id
        FROM tokens
        WHERE dept=%s AND stage=%s AND status='CALLED'
          AND called_at IS NOT NULL
          AND called_by=%s
        ORDER BY called_at DESC
        LIMIT 1
        FOR UPDATE
    """, (dept, from_stage, counter))

    row = cur.fetchone()
    if not row:
        conn.commit()
        return False

    now = datetime.now()

    cur.execute("""
        UPDATE tokens
        SET stage=%s,
            status='WAITING',
            called_at=NULL,
            called_by=NULL,
            transferred_at=%s
        WHERE id=%s
    """, (to_stage, now, row["id"]))

    conn.commit()
    return True


def complete_last_called(conn, dept: str, stage: str, counter: str) -> bool:
    """
    When Nursing clicks NEXT again, we mark the previous CALLED token as SERVED
    so it disappears from nursing queue.
    """
    cur = conn.cursor()

    cur.execute("""
        SELECT id
        FROM tokens
        WHERE dept=%s AND stage=%s AND status='CALLED'
          AND called_at IS NOT NULL
          AND called_by=%s
        ORDER BY called_at DESC
        LIMIT 1
        FOR UPDATE
    """, (dept, stage, counter))

    row = cur.fetchone()
    if not row:
        conn.commit()
        return False

    now = datetime.now()

    cur.execute("""
        UPDATE tokens
        SET status='SERVED',
            served_at=%s
        WHERE id=%s
    """, (now, row["id"]))

    conn.commit()
    return True

    
def create_indexes(conn):
    cur = conn.cursor()
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tokens_dept_stage_status_priority_created ON tokens(dept, stage, status, priority, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tokens_dept_stage_status_created ON tokens(dept, stage, status, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tokens_dept_called_by_called_at ON tokens(dept, called_by, called_at)")
    conn.commit()

def get_queue(conn, dept: str, stage: str = 'reception'):
    cur = conn.cursor()
    cur.execute("""
        SELECT token_no, priority, status, created_at, called_at, called_by
        FROM tokens
        WHERE dept=%s AND stage=%s
        ORDER BY created_at ASC
    """, (dept, stage))
    rows = cur.fetchall()  # already dicts because of dict_row

    waiting = [r for r in rows if r["status"] == "WAITING"]
    called  = [r for r in rows if r["status"] == "CALLED"]

    waiting_appt   = [r for r in waiting if r["priority"] == 1]
    waiting_walkin = [r for r in waiting if r["priority"] == 2]

    # last_called based on most recent CALL time
    cur.execute("""
        SELECT token_no
        FROM tokens
        WHERE dept=%s AND stage=%s AND status='CALLED' AND called_at IS NOT NULL
        ORDER BY called_at DESC
        LIMIT 1
    """, (dept, stage))
    row = cur.fetchone()
    last_called = int(row["token_no"]) if row else None

    return {
        "dept": dept,
        "waiting_count": len(waiting),
        "waiting_list": [r["token_no"] for r in waiting],
        "last_called": last_called,
        "waiting_appt_count": len(waiting_appt),
        "waiting_walkin_count": len(waiting_walkin),
        "waiting_appt_list": [r["token_no"] for r in waiting_appt],
        "waiting_walkin_list": [r["token_no"] for r in waiting_walkin],
        "called_count": len(called),
    }

def get_last_called(conn, dept: str, stage: str = 'reception'):
    cur = conn.cursor()
    cur.execute("""
        SELECT token_no, called_by
        FROM tokens
        WHERE dept=%s AND stage=%s AND status='CALLED' AND called_at IS NOT NULL
        ORDER BY called_at DESC
        LIMIT 1
    """, (dept, stage))
    row = cur.fetchone()
    if not row:
        return None
    return {"token_no": int(row["token_no"]), "called_by": row["called_by"]}

def get_last_printed(conn, dept: str, stage: str = 'reception'):
    cur = conn.cursor()
    cur.execute("""
        SELECT token_no
        FROM tokens
        WHERE dept=%s AND stage=%s
        ORDER BY created_at DESC
        LIMIT 1
    """, (dept, stage))
    row = cur.fetchone()
    return {"token_no": int(row["token_no"])} if row else None

def get_last_called_for_counters(conn, dept: str, counters: list[str], stage: str | None = None) -> dict:
    """
    Returns { "Counter1": 1005, "Counter2": None, ... } for the latest CALLED token per counter.
    """
    if not counters:
        return {}

    cur = conn.cursor()

    if stage:
        cur.execute("""
            SELECT called_by, token_no, called_at
            FROM tokens
            WHERE dept=%s
              AND stage=%s
              AND status='CALLED'
              AND called_at IS NOT NULL
              AND called_by = ANY(%s)
            ORDER BY called_at DESC
            LIMIT 200
        """, (dept, stage, counters))   # ✅ 3 params for 3 placeholders
    else:
        cur.execute("""
            SELECT called_by, token_no, called_at
            FROM tokens
            WHERE dept=%s
              AND status='CALLED'
              AND called_at IS NOT NULL
              AND called_by = ANY(%s)
            ORDER BY called_at DESC
            LIMIT 200
        """, (dept, counters))         # ✅ 2 params for 2 placeholders

    result = {c: None for c in counters}
    for row in cur.fetchall():
        c = row["called_by"]
        if c in result and result[c] is None:
            result[c] = int(row["token_no"])
    return result

def get_serving_now(conn, dept: str, counters: list[str], stage: str = 'reception'):
    result = {}
    for c in counters:
        result[c] = get_last_called_for_counter(conn, dept, c, stage)
    return result

def record_recall(conn, counter: str):
    cur = conn.cursor()
    cur.execute("""
        UPDATE state
        SET recall_seq = recall_seq + 1,
            last_recall_counter = %s
        WHERE id = 1
    """, (counter,))
    conn.commit()

def get_last_called_for_counters(conn, dept: str, counters: list[str], stage: str = 'reception') -> dict:
    """
    Returns { "Counter1": 1005, "Counter2": None, ... } for the latest CALLED token per counter.
    Uses ONE query and then picks the latest row per counter in Python.
    """
    if not counters:
        return {}

    cur = conn.cursor()
    cur.execute("""
        SELECT called_by, token_no, called_at
        FROM tokens
        WHERE dept=%s
          AND stage=%s
          AND status='CALLED'
          AND called_at IS NOT NULL
          AND called_by = ANY(%s)
        ORDER BY called_at DESC
        LIMIT 200
    """, (dept,stage, counters))

    result = {c: None for c in counters}
    for row in cur.fetchall():
        c = row["called_by"]
        if c in result and result[c] is None:
            result[c] = int(row["token_no"])
    return result
