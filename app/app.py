import sys
import threading
import configparser
import requests
import os
import socket
import json

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QPixmap
from PyQt5.QtWidgets import (
    QApplication, QWidget, QPushButton, QLabel,
    QVBoxLayout, QHBoxLayout, QFrame, QMessageBox
)


from printing import print_token
from audio import announce_token

# ===================== DISCOVERY =====================

DISCOVERY_PORT = 9999
SERVER_BASE = None   # filled dynamically

import socket

def is_local_server_running(port=8032):
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=0.5)
        s.close()
        return True
    except:
        return False

def listen_for_server():
    global SERVER_BASE
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", DISCOVERY_PORT))

    while True:
        data, _ = sock.recvfrom(2048)
        try:
            payload = json.loads(data.decode())
            if payload.get("service") == "Test-QMS":
                ip = payload["ip"]
                port = payload["port"]
                new_base = f"http://{ip}:{port}"

                global SERVER_BASE, SERVER_ONLINE
                if SERVER_BASE != new_base:
                    SERVER_BASE = new_base
                    SERVER_ONLINE = True
                    print(f"✅ Server discovered: {SERVER_BASE}")

        except Exception:
            pass


# ===================== CONFIG =====================

def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


cfg = configparser.ConfigParser()
cfg.read(os.path.join(app_dir(), "config.ini"))

PRINTER_NAME = cfg.get("printer", "name", fallback="")
USE_TTS = cfg.getboolean("audio", "use_tts", fallback=True)

GREEN = "#16a34a"
GREEN_DARK = "#0f7a35"
BORDER = "#d1d5db"

class TabletUI(QWidget):
    statusChanged = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._server_failures = 0
        self._bootstrapped = False
        self.setWindowTitle("PAD QMS - Reception Kiosk")
        self.setStyleSheet("background: white;")
        self.last_announced = {}      # per-counter
        self.last_recall_seq = 0      # stable baseline
        self._mode = "choose_service"  # or "doctor_appointment"

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        top = QFrame()
        top.setStyleSheet(f"QFrame {{ background: {GREEN}; }}")
        topLay = QVBoxLayout(top)
        topLay.setAlignment(Qt.AlignCenter)
        minBtn = QPushButton("—")
        minBtn.setFixedSize(44, 44)
        minBtn.setCursor(Qt.PointingHandCursor)
        minBtn.setToolTip("Minimize (testing)")
        minBtn.setStyleSheet("""
            QPushButton {
            background: rgba(255,255,255,0.20);
            color: white;
            border: 2px solid rgba(255,255,255,0.35);
            border-radius: 10px;
            font-size: 22px;
            font-weight: 900;
            }
            QPushButton:hover { background: rgba(255,255,255,0.30); }
            QPushButton:pressed { background: rgba(255,255,255,0.40); }
        """)
        minBtn.clicked.connect(self.showMinimized)

        # Top bar layout
        topBar = QHBoxLayout()
        topBar.addStretch()
        topBar.addWidget(minBtn)
        topLay.addLayout(topBar)
        center = QVBoxLayout()
        center.setSpacing(32)
        center.setAlignment(Qt.AlignCenter)

        # ===== LOGO =====
        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        pixmap = QPixmap(os.path.join(app_dir(), "logo.png"))
        if not pixmap.isNull():
            logo.setPixmap(pixmap.scaledToHeight(200, Qt.SmoothTransformation))
        logo.setStyleSheet("background: transparent;")

        self.header = QLabel("Reception")
        self.header.setAlignment(Qt.AlignCenter)
        self.header.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 58px;
                font-weight: 900;
            }
        """)

        self.sub = QLabel("Tap to select service")
        self.sub.setAlignment(Qt.AlignCenter)
        self.sub.setStyleSheet("""
            QLabel {
                color: rgba(255,255,255,0.95);
                font-size: 26px;
                font-weight: 600;
            }
        """)

        # Main doctor button (was PRINT TOKEN)
        self.doctorBtn = QPushButton(
            "DOCTOR\n"
            "ڈاکٹر\n"
            "طبيب"
        )
        self.doctorBtn.setMinimumHeight(140)
        self.doctorBtn.setMinimumWidth(560)
        self.doctorBtn.setCursor(Qt.PointingHandCursor)
        self.doctorBtn.setStyleSheet(f"""
            QPushButton {{
                background: white;
                color: {GREEN_DARK};
                border: 4px solid rgba(255,255,255,0.7);
                border-radius: 26px;
                font-size: 34px;
                font-weight: 900;
            }}
        """)
        self.doctorBtn.clicked.connect(self._start_doctor_flow)

        # Secondary lab button
        self.labBtn = QPushButton(
            "LAB\n"
            "لیب\n"
            "مختبر"
        )
        self.labBtn.setMinimumHeight(120)
        self.labBtn.setMinimumWidth(420)
        self.labBtn.setCursor(Qt.PointingHandCursor)
        self.labBtn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.08);
                color: white;
                border: 3px solid rgba(255,255,255,0.55);
                border-radius: 22px;
                font-size: 30px;
                font-weight: 800;
            }}
            QPushButton:hover {{
                background: rgba(255,255,255,0.14);
            }}
        """)
        self.labBtn.clicked.connect(self._print_lab)

        # Hidden print button used after choosing Lab
        self.printBtn = QPushButton(
            "PRINT TOKEN\n"
            "ٹوکن پرنٹ کریں\n"
            "اطبع التذكرة"
        )
        self.printBtn.setMinimumHeight(140)
        self.printBtn.setMinimumWidth(560)
        self.printBtn.setCursor(Qt.PointingHandCursor)
        self.printBtn.setStyleSheet(f"""
            QPushButton {{
                background: white;
                color: {GREEN_DARK};
                border: 4px solid rgba(255,255,255,0.7);
                border-radius: 26px;
                font-size: 34px;
                font-weight: 900;
            }}
        """)
        self.printBtn.hide()

        center.addWidget(logo)
        center.addWidget(self.header)
        center.addWidget(self.sub)
        center.addWidget(self.doctorBtn)
        center.addWidget(self.labBtn)
        center.addWidget(self.printBtn)

        topLay.addLayout(center)
        root.addWidget(top)

        # Show main window full-screen
        self.showFullScreen()

        # ---- polling for audio ----
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_audio)
        self.timer.start(1500)
    # ===================== PRINT =====================
    # Inline doctor/lab and appointment flow
    def _set_printing_state(self, printing: bool):
        if printing:
            self.doctorBtn.setEnabled(False)
            self.labBtn.setEnabled(False)
            self.printBtn.setEnabled(False)
            self.printBtn.setText("PRINTING…\nپرنٹ ہو رہا ہے…")
        else:
            # Reset to initial doctor/lab view
            self._mode = "choose_service"
            self.header.setText("Reception")
            self.sub.setText("Tap to select service")
            self.doctorBtn.setEnabled(True)
            self.labBtn.setEnabled(True)
            self.printBtn.setEnabled(True)
            self.printBtn.hide()
            self.doctorBtn.setText(
                "DOCTOR\n"
                "ڈاکٹر\n"
                "طبيب"
            )
            self.labBtn.setText(
                "LAB\n"
                "لیب\n"
                "مختبر"
            )

    def _do_print(self, visit_type: str):
        if not SERVER_BASE:
            print("❌ Server not discovered yet")
            return

        self._set_printing_state(True)

        try:
            r = requests.post(
                f"{SERVER_BASE}/api/print-token",
                json={"dept": "welfare", "visit_type": visit_type},
                timeout=3
            )
            data = r.json()
            token_no = data.get("token_no")

            if token_no:
                print_token(PRINTER_NAME, token_no, "welfare")

        except Exception as e:
            print("Print failed:", e)

        finally:
            QTimer.singleShot(1200, lambda: self._set_printing_state(False))

    def _start_doctor_flow(self):
        """Switch UI to ask appointment question for doctor, inline."""
        if self._mode != "choose_service":
            return
        self._mode = "doctor_appointment"
        self.header.setText("Doctor")
        self.sub.setText("Do you have an appointment?\nکیا آپ کی اپائنٹمنٹ ہے؟\nهل لديك موعد؟")

        # Reuse the two main buttons as Yes / No
        self.doctorBtn.setText("Yes / ہاں / نعم")
        self.labBtn.setText("No / نہیں / لا")

        # Update connections for this mode
        try:
            self.doctorBtn.clicked.disconnect()
        except TypeError:
            pass
        try:
            self.labBtn.clicked.disconnect()
        except TypeError:
            pass

        self.doctorBtn.clicked.connect(lambda: self._do_print("appointment"))
        self.labBtn.clicked.connect(lambda: self._do_print("walkin"))

    def _print_lab(self):
        """Lab flow: hide doctor/lab, show single PRINT TOKEN button."""
        if self._mode != "choose_service":
            return
        self._mode = "lab_confirm"

        self.header.setText("Lab")
        self.sub.setText("Tap to print your lab token")

        # Hide doctor/lab buttons and show print button
        self.doctorBtn.hide()
        self.labBtn.hide()
        self.printBtn.show()

        try:
            self.printBtn.clicked.disconnect()
        except TypeError:
            pass
        self.printBtn.clicked.connect(lambda: self._do_print("lab"))

        # ===================== AUDIO =====================

    def poll_audio(self):
        if not SERVER_BASE:
            return

        try:
            r = requests.get(f"{SERVER_BASE}/api/status?dept=welfare", timeout=2)
            data = r.json()

            recall_seq = data.get("recall_seq", 0)
            serving = data.get("serving", {})

            # 🔒 FIRST POLL → just sync state, no audio
            if not self._bootstrapped:
                self.last_recall_seq = recall_seq
                for counter, token in serving.items():
                    if token:
                        self.last_announced[counter] = token
                self._bootstrapped = True
                return

            # ---------- 1️⃣ NEXT TOKEN ----------
            for counter, token in serving.items():
                if not token:
                    continue

                last = self.last_announced.get(counter)
                if token != last:
                    self.last_announced[counter] = token
                    announce_token(USE_TTS, token, counter)
                    return

            # ---------- 2️⃣ RECALL ----------
            if recall_seq != self.last_recall_seq:
                self.last_recall_seq = recall_seq

                recall_counter = data.get("recall_counter")
                if recall_counter:
                    token = serving.get(recall_counter)
                    if token:
                        announce_token(USE_TTS, token, recall_counter)

                return


        except Exception as e:
            print("poll_audio error:", e)

if __name__ == "__main__":
    # ✅ 1) Try localhost first (same PC)
    if is_local_server_running(8032):
        SERVER_BASE = "http://127.0.0.1:8032"
        print("✅ Local server detected directly:", SERVER_BASE)
    else:
        # ✅ 2) If not local, then listen for UDP discovery (tablets / other PCs)
        threading.Thread(target=listen_for_server, daemon=True).start()

    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    w = TabletUI()
    w.show()
    sys.exit(app.exec_())
