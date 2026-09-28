// Convertor desktop shell.
//
// The shell owns one job: run the conversion engine for exactly as long as the
// window is open, and tell the interface where to find it. Everything else —
// detection, routing, conversion, the whole API — is the same engine that runs
// under Docker, unchanged.
//
// The engine binds to a random free port on 127.0.0.1 rather than a fixed one,
// so two copies of the app, or an unrelated service on 8000, cannot collide.

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpListener;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent, WindowEvent};

/// The child engine process, so it can be killed on exit rather than orphaned.
struct Engine(Mutex<Option<Child>>);

/// Ask the OS for a free port by binding to 0 and immediately releasing it.
fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .unwrap_or(8756)
}

/// Where the app keeps the workspace: converted files, history, packs.
/// A real directory the user can find, not a container volume.
fn workspace_dir(app: &tauri::AppHandle) -> PathBuf {
    app.path()
        .app_data_dir()
        .unwrap_or_else(|_| PathBuf::from("."))
        .join("workspace")
}

/// The bundled Python runtime and the engine sources, inside the app bundle.
fn engine_paths(app: &tauri::AppHandle) -> (PathBuf, PathBuf) {
    let resources = app
        .path()
        .resource_dir()
        .unwrap_or_else(|_| PathBuf::from("."));

    // Shipped builds carry a relocatable interpreter under runtime/.
    // During development we fall back to whatever python3 is on PATH, so the
    // shell can be run without building the runtime first.
    let bundled = if cfg!(target_os = "windows") {
        resources.join("runtime/python.exe")
    } else {
        resources.join("runtime/bin/python3")
    };
    let python = if bundled.exists() {
        bundled
    } else {
        PathBuf::from("python3")
    };

    (python, resources)
}

fn spawn_engine(app: &tauri::AppHandle, port: u16) -> std::io::Result<Child> {
    let (python, resources) = engine_paths(app);
    let workspace = workspace_dir(app);
    std::fs::create_dir_all(&workspace).ok();

    // The engine package lives beside the interpreter in the bundle; in
    // development it is two levels up from the Tauri crate.
    let engine_root = if resources.join("app").exists() {
        resources.clone()
    } else {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../engine")
    };

    let mut command = Command::new(python);
    command
        .current_dir(&engine_root)
        .arg("-m")
        .arg("uvicorn")
        .arg("app.main:app")
        .arg("--host")
        .arg("127.0.0.1")
        .arg("--port")
        .arg(port.to_string())
        .env("CONVERTOR_WORKSPACE", &workspace)
        .env("CONVERTOR_BUILD", "desktop")
        // The engine watches this pid and exits when it disappears. Killing the
        // child on close (below) covers a graceful quit; this covers every other
        // ending — Force Quit, a crash, logging out — where no cleanup runs.
        .env("CONVERTOR_PARENT_PID", std::process::id().to_string())
        // The engine binaries ship beside the app, not on the user's PATH.
        .env("CONVERTOR_BIN_DIR", resources.join("binaries"))
        .env("PYTHONUNBUFFERED", "1")
        .stdout(Stdio::null())
        .stderr(Stdio::piped());

    // Without this a console window flashes up on every launch on Windows.
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }

    command.spawn()
}

/// Block until the engine answers, so the window never opens onto a dead API.
fn await_engine(port: u16, timeout: Duration) -> bool {
    let started = Instant::now();
    while started.elapsed() < timeout {
        if std::net::TcpStream::connect(("127.0.0.1", port)).is_ok() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(120));
    }
    false
}

/// Reveal a produced file in Finder or Explorer. On the desktop this replaces
/// "here is a path" with the thing a person actually wants.
#[tauri::command]
fn reveal(path: String) -> Result<(), String> {
    let target = PathBuf::from(&path);
    if !target.exists() {
        return Err(format!("{path} no longer exists"));
    }
    #[cfg(target_os = "macos")]
    let result = Command::new("open").arg("-R").arg(&target).spawn();
    #[cfg(target_os = "windows")]
    let result = Command::new("explorer")
        .arg(format!("/select,{}", target.display()))
        .spawn();
    #[cfg(all(not(target_os = "macos"), not(target_os = "windows")))]
    let result = Command::new("xdg-open")
        .arg(target.parent().unwrap_or(&target))
        .spawn();

    result.map(|_| ()).map_err(|e| e.to_string())
}

#[tauri::command]
fn workspace_path(app: tauri::AppHandle) -> String {
    workspace_dir(&app).display().to_string()
}

fn main() {
    let port = free_port();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(Engine(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![reveal, workspace_path])
        .setup(move |app| {
            let handle = app.handle().clone();

            match spawn_engine(&handle, port) {
                Ok(child) => {
                    app.state::<Engine>().0.lock().unwrap().replace(child);
                }
                Err(error) => {
                    eprintln!("could not start the conversion engine: {error}");
                }
            }

            if !await_engine(port, Duration::from_secs(30)) {
                eprintln!("the conversion engine did not start within 30s");
            }

            // The interface reads this before its first request. Injected rather
            // than compiled in, because the port is chosen at launch.
            let window = app.get_webview_window("main").unwrap();
            window.eval(&format!(
                "window.__CONVERTOR_ENGINE__ = 'http://127.0.0.1:{port}';"
            ))?;

            Ok(())
        })
        .on_window_event(|window, event| {
            // Closing the window ends the session; the engine must not outlive it.
            if let WindowEvent::CloseRequested { .. } = event {
                if let Some(engine) = window.app_handle().try_state::<Engine>() {
                    if let Some(mut child) = engine.0.lock().unwrap().take() {
                        let _ = child.kill();
                    }
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("failed to start Convertor")
        .run(|app, event| {
            if let RunEvent::ExitRequested { .. } = event {
                if let Some(engine) = app.try_state::<Engine>() {
                    if let Some(mut child) = engine.0.lock().unwrap().take() {
                        let _ = child.kill();
                    }
                }
            }
        });
}
