import time
import threading
import socket
import json
import requests
import configparser
import os
import sys

from audio import announce_token

DISCOVERY_PORT = 9999
SERVER_BASE = None


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


# ===================== CONFIG =====================
cfg = configparser.ConfigParser()
cfg.read(os.path.join(app_dir(), "config.ini"))
USE_TTS = cfg.getboolean("audio", "use_tts", fallback=True)


# ===================== DISCOVERY =====================
def listen_for_server():
    global SERVER_BASE
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", DISCOVERY_PORT))

    while True:
        data, _ = sock.recvfrom(2048)
        try:
            payload = json.loads(data.decode())

            if payload.get("service") in ("Reception-QMS"):
                ip = payload["ip"]
                port = payload["port"]
                new_base = f"http://{ip}:{port}"

                if SERVER_BASE != new_base:
                    SERVER_BASE = new_base
                    print(f"✅ Server discovered: {SERVER_BASE}")

        except Exception:
            pass


# ===================== AUDIO POLLER =====================
def poll_lab_audio():
    global SERVER_BASE
    last_serving = {}
    last_recall_seq = 0

    while True:
        try:
            if not SERVER_BASE:
                time.sleep(0.5)
                continue

            # Lab uses its own 'lab' stage
            url = f"{SERVER_BASE}/api/status?dept=welfare&stage=lab"
            status = requests.get(url, timeout=2).json()

            serving = status.get("serving", {}) or {}

            # Only care about Lab counters here (e.g. Lab1)
            serving = {
                k: v for k, v in serving.items()
                if str(k).lower().startswith("lab")
            }
            # 🔁 recall handling (speak again even if token didn't change)
            recall_seq = status.get("nursing_recall_seq", 0) or 0
            if recall_seq != last_recall_seq:
                last_recall_seq = recall_seq
                rc = status.get("nursing_recall_counter") or "Lab1"
                token = serving.get(rc)
                if token:
                    print(f"🔁 Lab recall: {rc} -> {token}")
                    announce_token(USE_TTS, token, rc)

            for counter, token in serving.items():
                if token and last_serving.get(counter) != token:
                    last_serving[counter] = token
                    print(f"🔊 Lab call: {counter} -> {token}")
                    announce_token(USE_TTS, token, counter)

            time.sleep(0.7)

        except Exception as e:
            print("❌ Lab audio poll error:", e)
            time.sleep(1)


def main():
    threading.Thread(target=listen_for_server, daemon=True).start()
    poll_lab_audio()


if __name__ == "__main__":
    main()

