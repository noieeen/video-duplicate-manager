import { contextBridge, ipcRenderer, webUtils, type IpcRendererEvent } from "electron";
import type { Api, EngineEvent, Settings } from "../src/types";

const api: Api = {
  pickFolder: () => ipcRenderer.invoke("dialog:pickFolder"),
  pickFolders: () => ipcRenderer.invoke("dialog:pickFolders"),
  getSettings: () => ipcRenderer.invoke("settings:get"),
  saveSettings: (s: Settings) => ipcRenderer.invoke("settings:save", s),
  run: (args: string[]) => ipcRenderer.invoke("engine:run", args),
  cancel: () => ipcRenderer.invoke("engine:cancel"),
  onEvent: (cb: (ev: EngineEvent) => void) => {
    const listener = (_e: IpcRendererEvent, ev: EngineEvent): void => cb(ev);
    ipcRenderer.on("engine:event", listener);
    return () => {
      ipcRenderer.removeListener("engine:event", listener);
    };
  },
  openPath: (p: string) => ipcRenderer.invoke("shell:openPath", p),
  showInFolder: (p: string) => ipcRenderer.invoke("shell:showInFolder", p),
  // "thumb" is a standard scheme, so the URL needs a host; the path is the encoded file path.
  fileUrl: (p: string) => `thumb://local/${encodeURIComponent(p)}`,
  // Electron 32+ removed File.path; webUtils is the replacement for drag-drop paths.
  pathForFile: (file: File) => webUtils.getPathForFile(file),
};

contextBridge.exposeInMainWorld("api", api);
