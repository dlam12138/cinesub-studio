"use strict";

const { app, BrowserWindow, dialog, ipcMain, shell } = require("electron");
const { spawn, spawnSync } = require("node:child_process");
const http = require("node:http");
const net = require("node:net");
const path = require("node:path");

const DEFAULT_PORT = 7860;
const READINESS_TIMEOUT_MS = 30000;
const READINESS_INTERVAL_MS = 500;

const repoRoot = path.resolve(__dirname, "..");
const appUrl = (port) => `http://127.0.0.1:${port}/`;

let backendProcess = null;
let mainWindow = null;
let isQuitting = false;

function parsePort() {
  const raw = process.env.CINESUB_DESKTOP_PORT;
  if (!raw) {
    return DEFAULT_PORT;
  }
  const port = Number.parseInt(raw, 10);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`Invalid CINESUB_DESKTOP_PORT: ${raw}`);
  }
  return port;
}

function commandWorks(command, args) {
  const result = spawnSync(command, args, {
    cwd: repoRoot,
    encoding: "utf-8",
    windowsHide: true
  });
  return result.status === 0;
}

function resolvePython() {
  const windowsVenv = path.join(repoRoot, ".venv", "Scripts", "python.exe");
  const unixVenv = path.join(repoRoot, ".venv", "bin", "python");
  const fs = require("node:fs");

  if (fs.existsSync(windowsVenv)) {
    return { command: windowsVenv, prefixArgs: [], label: windowsVenv };
  }
  if (fs.existsSync(unixVenv)) {
    return { command: unixVenv, prefixArgs: [], label: unixVenv };
  }
  if (commandWorks("python", ["--version"])) {
    return { command: "python", prefixArgs: [], label: "python from PATH" };
  }
  if (process.platform === "win32" && commandWorks("py", ["-3", "--version"])) {
    return { command: "py", prefixArgs: ["-3"], label: "py -3 launcher" };
  }
  return null;
}

function httpGetText(url, timeoutMs = 1000) {
  return new Promise((resolve) => {
    const req = http.get(url, { timeout: timeoutMs }, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => {
        body += chunk;
      });
      res.on("end", () => {
        resolve({ ok: true, statusCode: res.statusCode, body });
      });
    });
    req.on("timeout", () => {
      req.destroy(new Error("timeout"));
    });
    req.on("error", (error) => {
      resolve({ ok: false, error });
    });
  });
}

function isPortOpen(port) {
  return new Promise((resolve) => {
    const socket = net.createConnection({ host: "127.0.0.1", port });
    socket.setTimeout(1000);
    socket.on("connect", () => {
      socket.end();
      resolve(true);
    });
    socket.on("timeout", () => {
      socket.destroy();
      resolve(false);
    });
    socket.on("error", () => {
      resolve(false);
    });
  });
}

async function checkPortState(port) {
  const response = await httpGetText(appUrl(port));
  if (response.ok && response.statusCode === 200 && response.body.includes("CineSub Studio")) {
    return "cinesub";
  }
  if (await isPortOpen(port)) {
    return "occupied";
  }
  return "available";
}

function startBackend(port, python) {
  const args = [
    ...python.prefixArgs,
    "-B",
    path.join(repoRoot, "start_app.py"),
    "--no-browser",
    "--non-interactive",
    "--port",
    String(port)
  ];
  const child = spawn(python.command, args, {
    cwd: repoRoot,
    env: {
      ...process.env,
      PYTHONUTF8: "1",
      PYTHONIOENCODING: "utf-8"
    },
    windowsHide: true
  });

  child.stdout.on("data", (data) => {
    process.stdout.write(`[backend] ${data}`);
  });
  child.stderr.on("data", (data) => {
    process.stderr.write(`[backend] ${data}`);
  });
  child.on("exit", (code, signal) => {
    if (!isQuitting) {
      console.error(`Backend exited with code ${code} signal ${signal || ""}`.trim());
    }
  });

  backendProcess = child;
  return child;
}

async function waitForReady(port) {
  const deadline = Date.now() + READINESS_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (backendProcess && backendProcess.exitCode !== null) {
      throw new Error(`Backend exited before readiness with code ${backendProcess.exitCode}`);
    }
    const response = await httpGetText(appUrl(port));
    if (response.ok && response.statusCode === 200) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, READINESS_INTERVAL_MS));
  }
  throw new Error(`Timed out waiting for ${appUrl(port)}`);
}

function isLocalAppUrl(targetUrl, port) {
  try {
    const parsed = new URL(targetUrl);
    return parsed.protocol === "http:" && parsed.hostname === "127.0.0.1" && parsed.port === String(port);
  } catch {
    return false;
  }
}

function createWindow(port) {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1100,
    minHeight: 720,
    title: "CineSub Studio",
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
      preload: path.join(__dirname, "preload.js")
    }
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (!isLocalAppUrl(url, port)) {
      shell.openExternal(url);
      return { action: "deny" };
    }
    return { action: "allow" };
  });

  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (!isLocalAppUrl(url, port)) {
      event.preventDefault();
      shell.openExternal(url);
    }
  });

  mainWindow.loadURL(appUrl(port));
}

function stopBackend() {
  if (!backendProcess || backendProcess.killed || backendProcess.exitCode !== null) {
    backendProcess = null;
    return;
  }
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/pid", String(backendProcess.pid), "/T", "/F"], {
      windowsHide: true,
      stdio: "ignore"
    });
  } else {
    backendProcess.kill();
  }
  backendProcess = null;
}

function showStartupError(message) {
  dialog.showErrorBox(
    "CineSub Studio startup failed",
    `${message}\n\nPossible causes:\n- Python environment is unavailable\n- Port is already in use\n- Dependencies are not installed\n- Backend startup failed\n\nCheck console logs or run:\n.\\start_web.ps1 -Smoke -NoBrowser -NonInteractive`
  );
}

function registerDirectoryPicker() {
  ipcMain.handle("dialog:select-directory", async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
      properties: ["openDirectory"]
    });
    if (result.canceled || !result.filePaths || !result.filePaths.length) {
      return null;
    }
    return result.filePaths[0] || null;
  });
}

async function main() {
  const port = parsePort();
  const python = resolvePython();
  if (!python) {
    dialog.showErrorBox(
      "Python runtime not found",
      "CineSub Studio could not find Python. Create the project .venv first, or use a future bundled runtime build."
    );
    app.quit();
    return;
  }

  const portState = await checkPortState(port);
  if (portState === "cinesub") {
    dialog.showErrorBox(
      "CineSub Studio is already running",
      `http://127.0.0.1:${port}/ already returns the CineSub homepage. Close the existing service before starting the desktop shell.`
    );
    app.quit();
    return;
  }
  if (portState === "occupied") {
    dialog.showErrorBox(
      "Port is already in use",
      `Port ${port} is occupied by another process. Stop that process or set CINESUB_DESKTOP_PORT to a free port.`
    );
    app.quit();
    return;
  }

  console.log(`Starting backend with ${python.label}`);
  startBackend(port, python);
  try {
    await waitForReady(port);
    createWindow(port);
  } catch (error) {
    stopBackend();
    showStartupError(error.message);
    app.quit();
  }
}

app.whenReady().then(() => {
  registerDirectoryPicker();
  return main();
});

app.on("before-quit", () => {
  isQuitting = true;
  stopBackend();
});

app.on("window-all-closed", () => {
  isQuitting = true;
  stopBackend();
  app.quit();
});
