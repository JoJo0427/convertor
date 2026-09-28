/** Capabilities that exist only when running inside the Tauri shell.
 *
 *  The same interface ships in both builds, so every call here degrades: in a
 *  browser `isDesktop` is false and the components fall back to what a browser
 *  can do (a download link instead of revealing a file in Finder). Nothing
 *  imports Tauri statically, so the Docker bundle does not carry it.
 */

type Invoke = (cmd: string, args?: Record<string, unknown>) => Promise<unknown>;

function invoker(): Invoke | null {
  if (typeof window === "undefined") return null;
  const tauri = (window as unknown as {
    __TAURI__?: { core?: { invoke?: Invoke } };
  }).__TAURI__;
  return tauri?.core?.invoke ?? null;
}

/** True when the interface is running inside the desktop app. */
export const isDesktop = (): boolean => invoker() !== null;

/** Show a produced file in Finder or Explorer, selected.
 *  Returns false if that is not possible, so the caller can fall back. */
export async function reveal(path: string): Promise<boolean> {
  const invoke = invoker();
  if (!invoke) return false;
  try {
    await invoke("reveal", { path });
    return true;
  } catch {
    return false;
  }
}

/** The folder the app is writing into, for display. */
export async function workspacePath(): Promise<string | null> {
  const invoke = invoker();
  if (!invoke) return null;
  try {
    return (await invoke("workspace_path")) as string;
  } catch {
    return null;
  }
}
