# server5.py
import configparser
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import db
import os, sys, threading, time
from datetime import datetime, timedelta
from fastapi.staticfiles import StaticFiles
from discovery import start_broadcast
# ------------------ models ------------------
from pydantic import BaseModel
from typing import Literal
# ------------------ helpers ------------------

def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

cfg = configparser.ConfigParser()
cfg.read(os.path.join(app_dir(), "config.ini"))

HOST = cfg.get("server", "host", fallback="0.0.0.0")
PORT = cfg.getint("server", "port", fallback=8032)
TOKEN_START = cfg.getint("qms", "token_start", fallback=1001)
# ------------------ in-memory nursing recall (no DB change) ------------------
# Nursing recall must NOT trigger reception tablet audio.
NURSING_RECALL_SEQ = 0
LAST_NURSING_RECALL_COUNTER = None

# ------------------ app ------------------

app = FastAPI(title="PAD QMS SERVER")
app.mount("/static", StaticFiles(directory="static"), name="static")



class PrintBody(BaseModel):
    dept: str = "welfare"
    visit_type: Literal["appointment", "walkin", "lab"] = "walkin"

class CallNextBody(BaseModel):
    dept: str = "welfare"
    stage: Literal["reception", "nursing"] = "reception"
    counter: str = "Counter1"
    mode: Literal["auto", "appointment", "walkin"] = "auto"

class RecallBody(BaseModel):
    dept: str = "welfare"
    stage: Literal["reception", "nursing"] = "reception"
    counter: str | None = None

# ------------------ startup ------------------

@app.on_event("startup")
def startup():
    # autodiscovery broadcast
    start_broadcast(PORT)

    # ✅ init db once at boot (tables/state/indexes)
    conn = db.connect()
    try:
        db.init_db(conn, appt_start=APPT_START, walkin_start=WALKIN_START, lab_start=LAB_START)
        db.create_indexes(conn)   # <-- Step 3 adds this function
        db.daily_cleanup_if_needed(conn, appt_start=APPT_START, walkin_start=WALKIN_START, lab_start=LAB_START)
    finally:
        conn.close()


@app.get("/", response_class=HTMLResponse)
def root():
    return "<h2>PAD QMS Server Running</h2>"

@app.get("/serving", response_class=HTMLResponse)
def serving_page():
    with open("web/serving.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/serving-nursing", response_class=HTMLResponse)
def serving_nursing_page():
    with open("web/serving_nursing.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/reception", response_class=HTMLResponse)
def reception_page():
    with open("web/reception.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/nursing", response_class=HTMLResponse)
def nursing_page():
    with open("web/nursing.html", "r", encoding="utf-8") as f:
        return f.read()


APPT_START = 1001
WALKIN_START = 2001
LAB_START = 3001

@app.post("/api/print-token")
def api_print_token(body: PrintBody):
    conn = db.connect()
    try:
        # init + daily cleanup must reset BOTH counters now
        db.init_db(conn, appt_start=APPT_START, walkin_start=WALKIN_START, lab_start=LAB_START)
        db.daily_cleanup_if_needed(conn, appt_start=APPT_START, walkin_start=WALKIN_START, lab_start=LAB_START)

        token_no = db.create_token_atomic(
            conn,
            dept=body.dept,
            visit_type=body.visit_type,
            appt_start=APPT_START,
            walkin_start=WALKIN_START,
            lab_start=LAB_START
        )
        return {"token_no": token_no, "dept": body.dept, "visit_type": body.visit_type}
    finally:
        conn.close()


@app.post("/api/call-next")
def api_call_next(body: CallNextBody):
    """
    Stage behavior:
      - reception: when you click NEXT, we first transfer the *previous* reception token
        (the one last CALLED by this counter) to nursing WAITING queue, then we CALL the next one.
      - nursing: when you click NEXT, we first mark the *previous* nursing token as SERVED,
        then we CALL the next one from nursing queue.
    """
    conn = db.connect()
    try:
        db.init_db(conn, appt_start=APPT_START, walkin_start=WALKIN_START, lab_start=LAB_START)
        db.daily_cleanup_if_needed(conn, appt_start=APPT_START, walkin_start=WALKIN_START, lab_start=LAB_START)

        if body.stage == "reception":
            # move previous token to nursing queue
            db.transfer_last_called_to_stage(
                conn,
                dept=body.dept,
                counter=body.counter,
                from_stage="reception",
                to_stage="nursing"
            )
        else:
            # nursing: finish previous one so it disappears
            db.complete_last_called(conn, dept=body.dept, stage="nursing", counter=body.counter)

        token_no = db.call_next_atomic(conn, body.dept, body.counter, body.mode, stage=body.stage)
        if token_no is None:
            return {"token_no": None, "stage": body.stage}

        return {"token_no": token_no, "dept": body.dept, "stage": body.stage, "counter": body.counter}
    finally:
        conn.close()

@app.post("/api/recall-last")
def api_recall_last(body: RecallBody):
    """
    Recall for a stage.
    Note: only reception recall updates the global recall_seq (so tablet audio stays correct).
    Nursing recall is "local" (returns the last called in nursing) without affecting recall_seq.
    """
    conn = db.connect()
    try:
        db.init_db(conn, appt_start=APPT_START, walkin_start=WALKIN_START, lab_start=LAB_START)

        last = db.get_last_called(conn, body.dept, stage=body.stage)
        if not last:
            return {"token_no": None, "stage": body.stage}

        counter = body.counter or last["called_by"]

        if body.stage == "reception":
            # ✅ record recall with counter (used by reception tablet audio)
            db.record_recall(conn, counter)
        else:
            # ✅ nursing recall is LOCAL ONLY (no DB change, no tablet audio)
            global NURSING_RECALL_SEQ, LAST_NURSING_RECALL_COUNTER
            NURSING_RECALL_SEQ += 1
            LAST_NURSING_RECALL_COUNTER = counter

        return {
            "token_no": last["token_no"],
            "dept": body.dept,
            "stage": body.stage,
            "counter": counter
        }
    finally:
        conn.close()

@app.get("/api/status")
def api_status(dept: str = "welfare", stage: str = "reception"):
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT recall_seq, last_recall_counter FROM state WHERE id=1")
        row = cur.fetchone()

        if stage == "nursing":
            counters = ["Nurse1"]
        else:
            counters = ["Counter1", "Counter2", "Counter3", "Counter4"]

        serving = db.get_last_called_for_counters(conn, dept, counters, stage=stage)

        return {
            "ok": True,
            "stage": stage,
            "recall_seq": row["recall_seq"] if row else 0,
            "recall_counter": row["last_recall_counter"] if row else None,
            "nursing_recall_seq": (NURSING_RECALL_SEQ if stage == "nursing" else 0),
            "nursing_recall_counter": (LAST_NURSING_RECALL_COUNTER if stage == "nursing" else None),
            "serving": serving
        }
    finally:
        conn.close()


@app.get("/api/queue")
def api_queue(dept: str = "welfare", stage: str = "reception"):
    conn = db.connect()
    try:
        db.init_db(conn, appt_start=APPT_START, walkin_start=WALKIN_START, lab_start=LAB_START)
        db.daily_cleanup_if_needed(conn, appt_start=APPT_START, walkin_start=WALKIN_START, lab_start=LAB_START)
        return db.get_queue(conn, dept, stage=stage)
    finally:
        conn.close()


if __name__ == "__main__":
    import uvicorn
    import logging

    # Reduce uvicorn noise
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        log_level="warning",   # 👈 only warnings & errors
        access_log=False       # 👈 disables request logs
    )
