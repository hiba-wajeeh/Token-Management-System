const { app, BrowserWindow, ipcMain } = require("electron");
const path = require("path");
const fs = require("fs");

const CONFIG_PATH = path.join(app.getPath("userData"), "config.json");

function loadConfig() {
  try {
    return JSON.parse(fs.readFileSync(CONFIG_PATH, "utf-8"));
  } catch {
    // default: point to localhost (for testing on same PC)
    return { serverUrl: "http://127.0.0.1:8032" };
  }
}

function saveConfig(cfg) {
  fs.writeFileSync(CONFIG_PATH, JSON.stringify(cfg, null, 2), "utf-8");
}
function createWindow() {
  const win = new BrowserWindow({
    width: 560,
    height: 320,
    resizable: false,
    maximizable: false,
    minimizable: true,
    fullscreenable: false,
    autoHideMenuBar: true,
    title: "Reception Client",
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false
    }
  });

  win.loadFile(path.join(__dirname, "renderer", "index.html"));

  // ✅ For production keep this OFF, for debugging turn ON
  // win.webContents.openDevTools({ mode: "detach" });
}


app.whenReady().then(createWindow);

// IPC for renderer <-> main
ipcMain.handle("config:get", () => loadConfig());
ipcMain.handle("config:set", (evt, cfg) => saveConfig(cfg));
