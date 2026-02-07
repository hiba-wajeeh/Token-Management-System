# PAD Token Management System – Developer Setup (macOS)

This repository contains the **PAD Token Management System**, including:

* FastAPI backend (Python)
* PostgreSQL (local, via Homebrew)
* PyQt desktop application
* Electron / Node.js counter applications

This guide explains **how to set up and run everything from scratch on macOS**.

---

## Prerequisites

Make sure you have the following installed **before starting**:

* macOS (Apple Silicon or Intel)
* Homebrew → [https://brew.sh](https://brew.sh)
* Python **3.11+**
* Node.js **18+**
* Git

Quick check:

```bash
python3 --version
node --version
npm --version
brew --version
```

---

## 1️⃣ Clone the Repository

```bash
git clone <YOUR_REPOSITORY_URL>
cd Token-Management-System
```

---

## 2️⃣ Run the Bootstrap Script (DO THIS FIRST)

This script automatically:

* Installs PostgreSQL 18
* Initializes the database cluster
* Starts PostgreSQL
* Creates:

  * Database: `qms_test`
  * User: `qms_test_user`
* Updates `server/config.ini`
* Creates Python virtual environment (`.venv`)
* Installs all Python dependencies (FastAPI, psycopg, PyQt5, etc.)

Run:

```bash
./scripts/bootstrap_mac.sh
```

⏳ First run may take a few minutes.

---

## 3️⃣ Start the Backend Server

After the bootstrap script finishes, **follow the instructions printed at the end**, then run:

```bash
source .venv/bin/activate
./scripts/run_server.sh
```

✅ FastAPI backend is now running.

Default:

* Host/Port: defined in `server/config.ini`
* Example: `http://127.0.0.1:8032`

---

## 4️⃣ Start the Python Desktop App (Reception / Nursing / Audio)

Open a **NEW terminal** (keep the server running).

Activate the virtual environment **again**:

```bash
cd Token-Management-System
source .venv/bin/activate
cd app
python app.py
```

⚠️ Do **NOT** run with `sudo`.

---

## 5️⃣ Start Counter Applications (Electron / Node.js)

Each counter is a Node/Electron app.

Example:

```bash
cd Counter1
npm install
npm start
```

For additional counters:

```bash
cd Nursing
npm install
npm start
```

---

## Project Structure (Simplified)

```
Token-Management-System/
│
├── server/                 # FastAPI backend
│   ├── server5.py
│   ├── db.py
│   └── config.ini
│
├── app/                    # PyQt desktop app
│   └── app.py
│
├── Counter1/               # Electron / Node counter apps
│   ├── .....
│   ├── .....
│   └── ...
│
├── scripts/
│   ├── bootstrap_mac.sh    # FIRST thing to run
│   ├── run_server.sh
│   └── .............
│
├── requirements.txt
├── requirements-macos.txt
└── README.md
└── README-macos.md
```

---
## Common Issues

### PostgreSQL port 5432 already in use

```bash
sudo lsof -nP -iTCP:5432 -sTCP:LISTEN
```

Stop the conflicting service or change the port in:

```
/opt/homebrew/var/postgresql@18/postgresql.conf
```

---

### `psql` not found

PostgreSQL is keg-only. Fix PATH:

```bash
echo 'export PATH="/opt/homebrew/opt/postgresql@18/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

---

### PyQt5 install issues on macOS

Only this is required:

```txt
PyQt5==5.15.11
```

Do not pin `PyQt5-Qt5` manually.

---

## Recommended Daily Workflow

```bash
# Terminal 1 – Backend
source .venv/bin/activate
./scripts/run_server.sh

# Terminal 2 – Desktop App
source .venv/bin/activate
cd app
python app.py

# Terminal 3 – Counter App
cd counters/Counter1
npm start
```

---

## Notes

* Designed for **local development**
* Windows setup will be documented separately
* Remote DB connections are intentionally avoided for stability
