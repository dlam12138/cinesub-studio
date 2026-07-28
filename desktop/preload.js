"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("cineSubDesktop", {
  selectDirectory: () => ipcRenderer.invoke("dialog:select-directory"),
  openOutputDirectory: () => ipcRenderer.invoke("shell:open-output-directory")
});
