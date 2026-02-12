const { ipcRenderer } = require("electron");
const dgram = require("dgram");

let baseUrl = "http://172.16.0.162:8032";
const DISCOVERY_PORT = 9999;
let isCalling = false;

// ✅ set your lab station identity here (own stage)
const stage = "lab";
const counter = "Lab1"; // renamed from Nurse1
const dept = "welfare";

console.log("lab_renderer.js loaded ✅");

let localServedCount = 0;

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.innerText = value;
}

function pad4(n) {
  const x = Number(n) || 0;
  return String(x).padStart(4, "0");
}

function startDiscoveryListener() {
  try {
    const sock = dgram.createSocket("udp4");

    sock.on("error", (err) => {
      console.log("Discovery socket error:", err);
      try { sock.close(); } catch {}
    });

    sock.on("message", async (msg, rinfo) => {
      try {
        const data = JSON.parse(msg.toString());
        if (data?.service !== "Test-QMS") return;

        const ip = data.ip || rinfo.address;
        const port = data.port || 8032;

        const detected = `http://${ip}:${port}`;
        if (detected !== baseUrl) {
          baseUrl = detected;
          localServedCount = 0;
          console.log("✅ Auto-detected server:", baseUrl);
          refresh();
        }
      } catch {
        // ignore
      }
    });

    sock.bind(DISCOVERY_PORT, "0.0.0.0", () => {
      sock.setBroadcast(true);
      console.log(`Listening for QMS discovery on UDP ${DISCOVERY_PORT}`);
    });
  } catch (e) {
    console.log("Discovery listener failed:", e);
  }
}

async function refresh() {
  try {
    const res = await fetch(`${baseUrl}/api/queue?dept=${dept}&stage=${stage}`, { cache: "no-store" });
    const data = await res.json();

    setText("totalWaiting", pad4(data.waiting_count ?? 0));
    setText("currentToken", data.last_called ?? "----");

    // "called_count" in DB = how many are CALLED right now historically; we use local counter for served.
    setText("totalCalled", pad4(localServedCount));

    setText("status", "");
  } catch (e) {
    setText("status", `❌ Server not reachable: ${baseUrl}`);
    console.log("refresh error:", e);
  }
}

async function nextToken() {
  if (isCalling) return;   // ⛔ block double click

  isCalling = true;

  const btn = document.getElementById("nextBtn");
  if (btn) btn.disabled = true;

  try {
    const res = await fetch(`${baseUrl}/api/call-next`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dept, stage, counter, mode: "auto" })
    });

    const data = await res.json();

    if (data.token_no === null) {
      alert("No waiting tokens in lab queue.");
    } else {
      localServedCount++;
      await refresh();
    }

  } catch (e) {
    console.log("nextToken error:", e);
    setText("status", "❌ Failed calling next token");
  }

  // ⏳ 2 second cooldown
  setTimeout(() => {
    isCalling = false;
    if (btn) btn.disabled = false;
  }, 2000);
}


async function recallToken() {
  try {
    const res = await fetch(`${baseUrl}/api/recall-last`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dept, stage, counter })
    });

    const data = await res.json();

    if (data.token_no === null) {
      alert("Nothing to recall yet.");
    } else {
      setText("status", `Recalled: ${data.token_no}`);
      await refresh();
    }
  } catch (e) {
    console.log("recallToken error:", e);
    setText("status", "❌ Failed recalling");
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  document.getElementById("nextBtn")?.addEventListener("click", nextToken);
  document.getElementById("recallBtn")?.addEventListener("click", recallToken);

  startDiscoveryListener();

  refresh();
  setInterval(refresh, 1000);
});
