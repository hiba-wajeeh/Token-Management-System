const { ipcRenderer } = require("electron");
const dgram = require("dgram");


let baseUrl = "http://172.16.0.175:8032";
const DISCOVERY_PORT = 9999;
const counter = "Counter4";
console.log("renderer.js loaded ✅");


let localCalledCount = 0;

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.innerText = value;
}

function pad4(n) {
  const x = Number(n) || 0;
  return String(x).padStart(4, "0");
}

async function loadSavedServerUrl() {
  try {
    const cfg = await ipcRenderer.invoke("config:get");
    if (cfg?.serverUrl) {
      baseUrl = cfg.serverUrl;
      console.log("Loaded saved serverUrl:", baseUrl);
    }
  } catch (e) {
    console.log("config:get failed:", e);
  }
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

        // We only care about our service
        if (data?.service !== "Reception-QMS") return;

        // Prefer payload ip if you add it in discovery.py, else use sender ip
        const ip = data.ip || rinfo.address;
        const port = data.port || 8032;

        const detected = `http://${ip}:${port}`;

        if (detected !== baseUrl) {
          baseUrl = detected;
          localCalledCount = 0; // reset local-only counter when server changes

          console.log("✅ Auto-detected server:", baseUrl);

          // Persist for next launch
          try {
            await ipcRenderer.invoke("config:set", { serverUrl: baseUrl });
          } catch (e) {
            console.log("config:set failed:", e);
          }

          refresh();
        }
      } catch {
        // ignore non-json packets
      }
    });

    sock.bind(DISCOVERY_PORT, "0.0.0.0", () => {
      sock.setBroadcast(true);
      console.log(`Listening for PAD-QMS discovery on UDP ${DISCOVERY_PORT}`);
    });
  } catch (e) {
    console.log("Discovery listener failed:", e);
  }
}

async function refresh() {
  try {
    const res = await fetch(`${baseUrl}/api/queue?dept=welfare`, { cache: "no-store" });
    const data = await res.json();

    // Total waiting
    setText("totalWaiting", pad4(data.waiting_count ?? 0));

    // NEW: split waiting
    setText("apptWaiting", pad4(data.waiting_appt_count ?? 0));
    setText("walkinWaiting", pad4(data.waiting_walkin_count ?? 0));

    // Current token number
    setText("currentToken", data.last_called ?? "----");

    // Total called
    const called = (data.called_count ?? localCalledCount);
    setText("totalCalled", pad4(called));

    // Optional: show a short preview list (first 6)
    setText("apptList", (data.waiting_appt_list ?? []).slice(0, 6).join(", ") || "-");
    setText("walkinList", (data.waiting_walkin_list ?? []).slice(0, 6).join(", ") || "-");

  } catch (e) {
    setText("status", `❌ Server not reachable: ${baseUrl}`);
    console.log("refresh error:", e);
  }
}

async function nextWalkin() {
  try {
    const res = await fetch(`${baseUrl}/api/call-next`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dept: "welfare", counter, mode: "walkin" })
    });

    const data = await res.json();
    if (data.token_no === null) {
      alert("No waiting walk-ins.");
      setText("status", "Last patient sent to nursing ✔");
      return;
    }
    localCalledCount++;
    await refresh();
  } catch (e) {
    console.log("nextWalkin error:", e);
    setText("status", "❌ Failed calling walk-in next");
  }
}



async function nextToken() {
  console.log(counter)

  try {
    const res = await fetch(`${baseUrl}/api/call-next`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dept: "welfare", counter })
    });

    if (!res.ok) {
      const text = await res.text();
      console.log("Server error body:", text);
      alert("Server error calling next token. Check server console.");
      return;
    }

    const data = await res.json();

    if (data.token_no === null) {
      alert("No waiting tokens.");
      setText("status", "Last patient sent to nursing ✔");
      return;
    }

    localCalledCount++;
    await refresh();
  } catch (e) {
    console.log("nextToken error:", e);
    setText("status", "❌ Failed calling next token");
  }
}

async function recallToken() {

  try {
    const res = await fetch(`${baseUrl}/api/recall-last`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dept: "welfare", counter })
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
  document.getElementById("walkinBtn")?.addEventListener("click", nextWalkin);


  // 1) load last saved server ip (fast startup)
  // await loadSavedServerUrl();

  // 2) listen for auto-discovery (auto switch)
  startDiscoveryListener();

  // 3) start refresh loop (ONLY once)
  refresh();
  setInterval(refresh, 1000);
});
