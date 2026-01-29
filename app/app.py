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

from PyQt5.QtWidgets import QDialog

class VisitTypeDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.choice = "walkin"

        # BIG tablet-friendly size
        self.setModal(True)
        self.setFixedSize(900, 520)

        self.setStyleSheet("""
            QDialog {
                background: white;
                border-radius: 22px;
            }
            QFrame#card {
                background: #f8fafc;
                border: 2px solid #e2e8f0;
                border-radius: 22px;
            }
            QLabel#q_en {
                font-size: 44px;
                font-weight: 900;
                color: #0f172a;
            }
            QLabel#q_ur {
                font-size: 42px;
                font-weight: 900;
                color: #0f172a;
            }
            QLabel#q_ar {
                font-size: 42px;
                font-weight: 900;
                color: #0f172a;
            }
            QPushButton {
                font-size: 34px;
                font-weight: 900;
                padding: 20px 22px;
                border-radius: 22px;
                border: 3px solid #e2e8f0;
            }
            QPushButton#yesBtn {
                background: #16a34a;
                color: white;
                border: 3px solid #16a34a;
            }
            QPushButton#yesBtn:hover { background: #15803d; border-color: #15803d; }
            QPushButton#noBtn {
                background: white;
                color: #0f7a35;
                border: 3px solid #0f7a35;
            }
            QPushButton#noBtn:hover { background: #f0fdf4; }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 26, 26, 26)
        root.setSpacing(18)

        card = QFrame()
        card.setObjectName("card")
        cardLay = QVBoxLayout(card)
        cardLay.setContentsMargins(28, 28, 28, 28)
        cardLay.setSpacing(18)
        cardLay.setAlignment(Qt.AlignCenter)

        # BIG question text (3 lines)
        q_en = QLabel("Do you have an appointment?")
        q_en.setObjectName("q_en")
        q_en.setAlignment(Qt.AlignCenter)

        q_ur = QLabel("کیا آپ کی اپائنٹمنٹ ہے؟")
        q_ur.setObjectName("q_ur")
        q_ur.setAlignment(Qt.AlignCenter)

        q_ar = QLabel("هل لديك موعد؟")
        q_ar.setObjectName("q_ar")
        q_ar.setAlignment(Qt.AlignCenter)

        cardLay.addWidget(q_en)
        cardLay.addWidget(q_ur)
        cardLay.addWidget(q_ar)

        # HUGE buttons row
        btnRow = QHBoxLayout()
        btnRow.setSpacing(18)

        yesBtn = QPushButton("Yes / ہاں / نعم")
        yesBtn.setObjectName("yesBtn")
        yesBtn.setCursor(Qt.PointingHandCursor)
        yesBtn.setMinimumHeight(120)

        noBtn = QPushButton("No / نہیں / لا")
        noBtn.setObjectName("noBtn")
        noBtn.setCursor(Qt.PointingHandCursor)
        noBtn.setMinimumHeight(120)

        yesBtn.clicked.connect(self._choose_yes)
        noBtn.clicked.connect(self._choose_no)

        btnRow.addWidget(yesBtn, 1)
        btnRow.addWidget(noBtn, 1)

        cardLay.addSpacing(10)
        cardLay.addLayout(btnRow)

        root.addWidget(card)

    def _choose_yes(self):
        self.choice = "appointment"
        self.accept()

    def _choose_no(self):
        self.choice = "walkin"
        self.accept()


# Replace your ask_visit_type with this
def ask_visit_type(self) -> str:
    dlg = VisitTypeDialog(self)
    dlg.exec_()
    return dlg.choice

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


        # Full screen kiosk
        self.showFullScreen()

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

        header = QLabel("Reception")
        header.setAlignment(Qt.AlignCenter)
        header.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 58px;
                font-weight: 900;
            }
        """)

        sub = QLabel("Tap to print your token")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet("""
            QLabel {
                color: rgba(255,255,255,0.95);
                font-size: 26px;
                font-weight: 600;
            }
        """)

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
        self.printBtn.clicked.connect(self.print_token)

        center.addWidget(logo)
        center.addWidget(header)
        center.addWidget(sub)
        center.addWidget(self.printBtn)

        topLay.addLayout(center)
        root.addWidget(top)

        # ---- polling for audio ----
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll_audio)
        self.timer.start(1500)
    # ===================== PRINT =====================
    def ask_visit_type(self) -> str:
        dlg = VisitTypeDialog(self)
        dlg.exec_()
        return dlg.choice
    def set_printing_state(self, printing: bool):
            if printing:
                self.printBtn.setEnabled(False)
                self.printBtn.setText("PRINTING…\nپرنٹ ہو رہا ہے…")
            else:
                self.printBtn.setEnabled(True)
                self.printBtn.setText(
                    "PRINT TOKEN\n"
                    "ٹوکن پرنٹ کریں\n"
                    "اطبع التذكرة"
                )
    def print_token(self):
        if not SERVER_BASE:
            print("❌ Server not discovered yet")
            return

        visit_type = self.ask_visit_type()  # "appointment" or "walkin"

        self.set_printing_state(True)

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
            QTimer.singleShot(1200, lambda: self.set_printing_state(False))

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
    threading.Thread(target=listen_for_server, daemon=True).start()

    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))
    w = TabletUI()
    w.show()
    sys.exit(app.exec_())
