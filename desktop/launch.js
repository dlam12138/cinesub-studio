"use strict";

const { spawn } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const repoRoot = path.resolve(__dirname, "..");
const stagedAppDir = path.join(os.tmpdir(), "cinesub-studio-electron-app");
const electronPath = require("electron");

function copyDesktopShell() {
  fs.rmSync(stagedAppDir, { recursive: true, force: true });
  fs.mkdirSync(stagedAppDir, { recursive: true });
  for (const fileName of ["main.js", "preload.js"]) {
    fs.copyFileSync(path.join(__dirname, fileName), path.join(stagedAppDir, fileName));
  }
  fs.writeFileSync(
    path.join(stagedAppDir, "package.json"),
    JSON.stringify({ name: "cinesub-studio-desktop-runtime", private: true, main: "main.js" }, null, 2)
  );
}

copyDesktopShell();

const child = spawn(electronPath, [stagedAppDir], {
  cwd: repoRoot,
  env: {
    ...process.env,
    CINESUB_REPO_ROOT: repoRoot
  },
  stdio: "inherit",
  windowsHide: false
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code === null ? 1 : code);
});

child.on("error", (error) => {
  console.error(error.message);
  process.exit(1);
});
