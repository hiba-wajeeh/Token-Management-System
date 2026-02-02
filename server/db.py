import sqlite3
from datetime import datetime, date
import os, sys

def _app_base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

DB_PATH = os.path.join(_app_base_dir(), "qms.sqlite")

def vacuum_db(conn: sqlite3.Connection):
    conn.execute("VACUUM")

def wal_checkpoint_truncate(conn: sqlite3.Connection):
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")

def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn

def init_db(conn: sqlite3.Connection, appt_start: int, walkin_start: int):
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        session_date TEXT NOT NULL,
        recall_seq INTEGER NOT NULL DEFAULT 0,
        last_recall_counter TEXT,
        next_appt_token INTEGER NOT NULL,
        next_walkin_token INTEGER NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token_no INTEGER NOT NULL,
        dept TEXT NOT NULL,
        priority INTEGER NOT NULL,      -- 1=appointment, 2=walkin
        status TEXT NOT NULL,           -- WAITING, CALLED, SERVED
        created_at TEXT NOT NULL,
        called_at TEXT,
        called_by TEXT
    )
    """)

    # ensure state row exists
    cur.execute("SELECT COUNT(*) AS c FROM state WHERE id=1")
    if cur.fetchone()["c"] == 0:
        cur.execute(
            "INSERT INTO state (id, session_date, next_appt_token, next_walkin_token) VALUES (1, ?, ?, ?)",
            (date.today().isoformat(), appt_start, walkin_start)
        )
    conn.commit()

def daily_cleanup_if_needed(conn: sqlite3.Connection, appt_start: int, walkin_start: int):
    cur = conn.cursor()
    cur.execute("SELECT session_date FROM state WHERE id=1")
    row = cur.fetchone()
    if not row:
        return False

    today = date.today().isoformat()
    if row["session_date"] != today:
        cur.execute("DELETE FROM tokens")
        cur.execute("""
            UPDATE state
            SET session_date=?,
                next_appt_token=?,
                next_walkin_token=?
            WHERE id=1
        """, (today, appt_start, walkin_start))
        conn.commit()
        return True

    return False

def create_token_atomic(conn: sqlite3.Connection, dept: str, visit_type: str, appt_start: int, walkin_start: int) -> int:
    """
    visit_type: "appointment" or "walkin"
    appointment => token 1001+, priority=1
    walkin      => token 2001+, priority=2
    """
    vt = (visit_type or "walkin").lower().strip()
    if vt not in ("appointment", "walkin"):
        vt = "walkin"

    priority = 1 if vt == "appointment" else 2
    col = "next_appt_token" if vt == "appointment" else "next_walkin_token"
    fallback_start = appt_start if vt == "appointment" else walkin_start

    cur = conn.cursor()
    cur.execute("BEGIN IMMEDIATE")

    cur.execute(f"SELECT {col} AS next_no FROM state WHERE id=1")
    next_no = cur.fetchone()["next_no"]
    if next_no is None:
        next_no = fallback_start

    now = datetime.now().isoformat(timespec="seconds")
    cur.execute("""
        INSERT INTO tokens (token_no, dept, priority, status, created_at)
        VALUES (?, ?, ?, 'WAITING', ?)
    """, (int(next_no), dept, int(priority), now))

    cur.execute(f"UPDATE state SET {col}=? WHERE id=1", (int(next_no) + 1,))
    conn.commit()
    return int(next_no)

def call_next_atomic(conn: sqlite3.Connection, dept: str, counter: str, visit_type: str | None = None):
    """
    visit_type:
      None/"auto"      => appointment first always (priority asc)
      "appointment"    => only appointments
      "walkin"         => only walkins
    """
    vt = (visit_type or "auto").lower().strip()
    cur = conn.cursor()
    cur.execute("BEGIN IMMEDIATE")

    if vt == "appointment":
        cur.execute("""
            SELECT id, token_no
            FROM tokens
            WHERE dept=? AND status='WAITING' AND priority=1
            ORDER BY created_at ASC
            LIMIT 1
        """, (dept,))
    elif vt == "walkin":
        cur.execute("""
            SELECT id, token_no
            FROM tokens
            WHERE dept=? AND status='WAITING' AND priority=2
            ORDER BY created_at ASC
            LIMIT 1
        """, (dept,))
    else:
        cur.execute("""
            SELECT id, token_no
            FROM tokens
            WHERE dept=? AND status='WAITING'
            ORDER BY priority ASC, created_at ASC
            LIMIT 1
        """, (dept,))

    row = cur.fetchone()
    if not row:
        conn.commit()
        return None

    now = datetime.now().isoformat(timespec="seconds")
    cur.execute("""
        UPDATE tokens
        SET status='CALLED', called_at=?, called_by=?
        WHERE id=?
    """, (now, counter, row["id"]))
    conn.commit()
    return int(row["token_no"])
    
def create_indexes(conn):
    cur = conn.cursor()
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tokens_dept_status_priority_created ON tokens(dept, status, priority, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tokens_dept_status_created ON tokens(dept, status, created_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tokens_dept_called_by_called_at ON tokens(dept, called_by, called_at)")
    conn.commit()

def get_queue(conn: sqlite3.Connection, dept: str):
    cur = conn.cursor()
    cur.execute("""
        SELECT token_no, priority, status, created_at, called_at, called_by
        FROM tokens
        WHERE dept=?
        ORDER BY created_at ASC
    """, (dept,))
    rows = [dict(r) for r in cur.fetchall()]

    waiting = [r for r in rows if r["status"] == "WAITING"]
    called  = [r for r in rows if r["status"] == "CALLED"]

    waiting_appt   = [r for r in waiting if r["priority"] == 1]
    waiting_walkin = [r for r in waiting if r["priority"] == 2]

    # ✅ Correct "last_called" based on most recent CALL time (not created time)
    cur.execute("""
        SELECT token_no
        FROM tokens
        WHERE dept=? AND status='CALLED' AND called_at IS NOT NULL
        ORDER BY called_at DESC
        LIMIT 1
    """, (dept,))
    row = cur.fetchone()
    last_called = int(row["token_no"]) if row else None

    return {
        "dept": dept,

        "waiting_count": len(waiting),
        "waiting_list": [r["token_no"] for r in waiting],

        # ✅ fixed
        "last_called": last_called,

        "waiting_appt_count": len(waiting_appt),
        "waiting_walkin_count": len(waiting_walkin),
        "waiting_appt_list": [r["token_no"] for r in waiting_appt],
        "waiting_walkin_list": [r["token_no"] for r in waiting_walkin],

        "called_count": len(called),
    }

def get_last_called(conn: sqlite3.Connection, dept: str):
    cur = conn.cursor()
    cur.execute("""
        SELECT token_no, called_by
        FROM tokens
        WHERE dept=? AND status='CALLED' AND called_at IS NOT NULL
        ORDER BY called_at DESC
        LIMIT 1
    """, (dept,))
    row = cur.fetchone()
    if not row:
        return None
    return {"token_no": int(row["token_no"]), "called_by": row["called_by"]}

def get_last_printed(conn, dept):
    cur = conn.cursor()
    cur.execute("""
        SELECT token_no
        FROM tokens
        WHERE dept = ?
        ORDER BY created_at DESC
        LIMIT 1
    """, (dept,))
    row = cur.fetchone()
    return {"token_no": row[0]} if row else None

def get_last_called_for_counter(conn, dept: str, counter: str):
    cur = conn.cursor()
    cur.execute("""
        SELECT token_no
        FROM tokens
        WHERE dept=? AND status='CALLED'
          AND called_at IS NOT NULL
          AND called_by=?
        ORDER BY called_at DESC
        LIMIT 1
    """, (dept, counter))
    row = cur.fetchone()
    return int(row["token_no"]) if row else None

def get_serving_now(conn, dept: str, counters: list[str]):
    result = {}
    for c in counters:
        result[c] = get_last_called_for_counter(conn, dept, c)
    return result

def record_recall(conn: sqlite3.Connection, counter: str):
    cur = conn.cursor()
    cur.execute("""
        UPDATE state
        SET recall_seq = recall_seq + 1,
            last_recall_counter = ?
        WHERE id = 1
    """, (counter,))
    conn.commit()

def get_last_called_for_counters(conn, dept: str, counters: list[str]) -> dict:
    """
    Returns { "Counter1": 1005, "Counter2": None, ... } for the latest CALLED token per counter.
    Uses ONE query and then picks the latest row per counter in Python.
    """
    if not counters:
        return {}

    placeholders = ",".join(["?"] * len(counters))

    cur = conn.cursor()
    cur.execute(
        f"""
        SELECT called_by, token_no, called_at
        FROM tokens
        WHERE dept=?
          AND status='CALLED'
          AND called_at IS NOT NULL
          AND called_by IN ({placeholders})
        ORDER BY called_at DESC
        LIMIT 50
        """,
        [dept, *counters]
    )

    result = {c: None for c in counters}
    for row in cur.fetchall():
        c = row["called_by"]
        if c in result and result[c] is None:
            result[c] = int(row["token_no"])

    return result
