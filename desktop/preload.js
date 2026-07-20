"use strict";

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("cineSubDesktop", {
  selectDirectory: () => ipcRenderer.invoke("dialog:select-directory")
});
