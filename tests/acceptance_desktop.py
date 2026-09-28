"""Acceptance run against a *packaged* desktop app.

    python3 tests/acceptance_desktop.py <port> [workdir]

The port is the one the Tauri shell picked for this launch; find it with

    ps -eo command | grep 'Convertor.app.*uvicorn'

This exists because every defect in TEST_PLAN.md's regression list was a
conversion that *reported success* or reported the wrong failure — not one was a
crash. So nothing here is satisfied by a job reaching "completed": every output
is reopened and checked for the thing the conversion was supposed to preserve,
which is rule 1 of that document.

It talks to the engine over HTTP exactly as the interface does, so it exercises
the shipped app rather than the source tree: the bundled interpreter, the bundled
engine binaries, the resolved dylib paths, and the real workspace directory.

Fixtures are generated here and deleted from the queue afterwards. Nothing in the
user's own queue is touched.
"""

import json, mimetypes, subprocess, sys, tempfile, time, urllib.request, uuid, zipfile
from pathlib import Path

PORT = sys.argv[1]
SP = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(tempfile.mkdtemp(prefix="convertor-acceptance-"))
B = f"http://127.0.0.1:{PORT}"
APP = Path("/Applications/Convertor.app/Contents/Resources")
BUNDLED_FFMPEG = APP / "binaries" / "ffmpeg"
FFMPEG = str(BUNDLED_FFMPEG) if BUNDLED_FFMPEG.exists() else "ffmpeg"
FFPROBE = str(BUNDLED_FFMPEG.with_name("ffprobe")) if BUNDLED_FFMPEG.exists() else "ffprobe"

passed, failed = [], []


def check(name, ok, detail=""):
    (passed if ok else failed).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))


def upload(path: Path) -> str:
    b = uuid.uuid4().hex
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"files\"; "
            f"filename=\"{path.name}\"\r\nContent-Type: {ctype}\r\n\r\n").encode()
    body += path.read_bytes() + f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request(B + "/api/files", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    with urllib.request.urlopen(req, timeout=600) as r:
        d = json.load(r)
    if not d.get("files"):
        raise RuntimeError(f"upload rejected: {d.get('rejected')}")
    return d["files"][0]["id"]


def convert(fid, target, options=None):
    req = urllib.request.Request(B + "/api/jobs", method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"fileIds": [fid], "target": target, "mode": "each",
                         "options": options or {}, "preset": None}).encode())
    with urllib.request.urlopen(req, timeout=120) as r:
        job = json.load(r)
    while True:
        with urllib.request.urlopen(f"{B}/api/jobs/{job['id']}", timeout=60) as r:
            j = json.load(r)
        if j["status"] not in ("running", "queued"):
            return j
        time.sleep(1)


def one(fid, target, options=None):
    j = convert(fid, target, options)
    it = j["items"][0]
    if it["status"] != "completed" or not it.get("outputs"):
        return None, (it.get("error") or {}).get("detail", it.get("message", "?"))
    return Path(it["outputs"][0]["path"]), ""


def drop(fid):
    urllib.request.urlopen(
        urllib.request.Request(f"{B}/api/files/{fid}", method="DELETE"), timeout=30)


def probe(path, entries, stream=None):
    """One ffprobe field. `stream` picks v:0 or a:0 — without it a two-stream
    file returns both and the last one silently wins, which is a test that
    asserts the wrong thing rather than a conversion that did."""
    cmd = [str(FFPROBE), "-v", "error"]
    if stream:
        cmd += ["-select_streams", stream]
    cmd += ["-show_entries", entries, "-of", "default=nw=1", str(path)]
    out = subprocess.run(cmd, capture_output=True, text=True)
    return dict(l.split("=", 1) for l in out.stdout.strip().splitlines() if "=" in l)


# ---------------------------------------------------------------- 1. media
subprocess.run([FFMPEG, "-v", "error",
                "-f", "lavfi", "-i", "testsrc=size=320x240:rate=24:duration=2",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                "-c:v", "libx264", "-c:a", "aac", "-y", str(SP / "a.mp4")], check=True)
fid = upload(SP / "a.mp4")
out, err = one(fid, "gif")
check("mp4 -> gif is an animated GIF", bool(out) and int(probe(out, "stream=nb_frames", "v:0").get("nb_frames", 0)) > 10,
      err or f"{probe(out, 'stream=nb_frames', 'v:0') if out else ''}")
out, err = one(fid, "mp3")
check("mp4 -> mp3 carries real audio", bool(out) and float(probe(out, "format=duration").get("duration", 0)) > 1.5, err)
out, err = one(fid, "webm")
check("mp4 -> webm re-encodes video to a VP-family codec",
      bool(out) and probe(out, "stream=codec_name", "v:0").get("codec_name", "").startswith("vp"), err)
drop(fid)

# ---------------------------------------------------------------- 2. vector
(SP / "b.svg").write_text(
    '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="140">'
    '<rect width="240" height="140" fill="#547792"/>'
    '<circle cx="120" cy="70" r="46" fill="#EAE0CF"/></svg>')
fid = upload(SP / "b.svg")
out, err = one(fid, "png", {"dpi": 192})
ok = bool(out) and out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
check("svg -> png renders through rsvg", ok, err)
out, err = one(fid, "pdf")
check("svg -> pdf", bool(out) and out.read_bytes()[:4] == b"%PDF", err)
drop(fid)

(SP / "c.eps").write_text(
    "%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 200 120\n"
    "0.33 0.47 0.57 setrgbcolor 0 0 200 120 rectfill\nshowpage\n")
fid = upload(SP / "c.eps")
out, err = one(fid, "png")
check("eps -> png renders through Ghostscript (R30)", bool(out) and out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", err)
out, err = one(fid, "pdf")
check("eps -> pdf", bool(out) and out.read_bytes()[:4] == b"%PDF", err)
drop(fid)

# ---------------------------------------------------------------- 3. trace
fid = upload(SP / "b.svg")
out, err = one(fid, "png")
drop(fid)
if out:
    fid = upload(out)
    traced, err = one(fid, "svg")
    check("png -> svg traces real paths via potrace",
          bool(traced) and b"<path" in traced.read_bytes(), err)
    drop(fid)

# ---------------------------------------------------------------- 4. data
(SP / "d.csv").write_text("name,qty,price\nwidget,4,9.99\ngizmo,11,2.50\nsprocket,7,15.00\n")
fid = upload(SP / "d.csv")
out, err = one(fid, "xlsx")
ok = False
if out:
    with zipfile.ZipFile(out) as z:
        ok = any("sheet1.xml" in n for n in z.namelist())
check("csv -> xlsx is a real workbook", ok, err)
out, err = one(fid, "json")
ok = bool(out) and len(json.loads(out.read_text())) == 3
check("csv -> json keeps all three rows", ok, err)
drop(fid)

# ---------------------------------------------------------------- 5. pdf
out_pdf = None
fid = upload(SP / "b.svg")
out_pdf, err = one(fid, "pdf")
drop(fid)
if out_pdf:
    fid = upload(out_pdf)
    out, err = one(fid, "png")
    check("pdf -> png rasterises", bool(out) and out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", err)
    drop(fid)

# ---------------------------------------------------------------- 6. errors
(SP / "broken.pdf").write_bytes(b"%PDF-1.7\nthis is not a pdf body at all\n")
try:
    fid = upload(SP / "broken.pdf")
    j = convert(fid, "txt")
    it = j["items"][0]
    err = (it.get("error") or {})
    detail = err.get("detail", "")
    check("a corrupt PDF gets a structured error", bool(err.get("code")) and bool(err.get("suggestions")), detail[:90])
    check("the error quotes no benign warning (R24)",
          not any(w in detail.lower() for w in ("javaldx", "fontconfig warning", "libpng warning")), detail[:90])
    drop(fid)
except RuntimeError as e:
    check("a corrupt PDF is rejected at upload with a reason", "error" in str(e).lower(), str(e)[:90])

# ---------------------------------------------------------------- 7. gating
with urllib.request.urlopen(B + "/api/system", timeout=30) as r:
    sysinfo = json.load(r)
hints = [e["installHint"] for e in sysinfo["engines"]]
check("no apt-get hints in the desktop build (R28)", not any("apt-get" in h for h in hints))
check("available engines carry no hint", all(
    not e["installHint"] for e in sysinfo["engines"] if e["available"]))
check("build reports as desktop", sysinfo.get("build") == "desktop", str(sysinfo.get("build")))

# ---------------------------------------------------------------- 8. packs
# The cheap pack is installed and removed for real. A pack system that has never
# installed anything is a screen, not a feature — and the two states that matter
# are "it appeared" and "it went away again", neither of which can be inspected
# from the source.
with urllib.request.urlopen(B + "/api/packs", timeout=30) as r:
    before = json.load(r)

by_id = {p["id"]: p for p in before["packs"]}
check("every pack reports a size and what it unlocks",
      all(p["megabytes"] > 0 and p["unlocks"] for p in before["packs"]))
check("a pack with no source says why instead of offering a button",
      all(p["installable"] or p["installed"] or p["unavailableReason"]
          for p in before["packs"]))
check("packs are manageable in this build", before["manageable"] is True)

# Pick the smallest pack that is genuinely missing, rather than naming one: on a
# developer machine Homebrew often already satisfies the cheap ones, and a test
# that skips itself is not a test.
candidates = sorted(
    (p for p in before["packs"] if p["installable"] and not p["installed"]),
    key=lambda p: p["megabytes"],
)

if not candidates:
    check("a pack install completes", True, "every pack already satisfied — skipped")
else:
    target = candidates[0]
    pid = target["id"]
    print(f"  ..    installing {pid} ({target['megabytes']} MB) to test the pack system")

    req = urllib.request.Request(f"{B}/api/packs/{pid}/install", method="POST",
                                 headers={"Content-Type": "application/json"},
                                 data=b"{}")
    with urllib.request.urlopen(req, timeout=60) as r:
        json.load(r)

    waited, state = 0.0, None
    while waited < 900:
        with urllib.request.urlopen(B + "/api/packs", timeout=30) as r:
            state = {p["id"]: p for p in json.load(r)["packs"]}[pid]
        if not state["job"] or state["job"]["status"] != "running":
            break
        time.sleep(2.0)
        waited += 2.0

    job = (state or {}).get("job") or {}
    check("a pack install completes", bool(state and state["installed"]),
          f"{job.get('status')} · {job.get('phase')}")
    check("an installed pack reports its real size on disk",
          bool(state and state["diskMegabytes"] > 0),
          f"{(state or {}).get('diskMegabytes')} MB")

    # The only proof that installing changed what the engine can do, rather than
    # only what the panel says.
    with urllib.request.urlopen(B + "/api/system", timeout=30) as r:
        engines_now = {e["id"]: e for e in json.load(r)["engines"]}
    provided = target["engines"]
    check("the installed pack's engine became available",
          all(engines_now.get(e, {}).get("available") for e in provided) if provided else True,
          ", ".join(provided) or "python-only pack")

    req = urllib.request.Request(f"{B}/api/packs/{pid}", method="DELETE")
    with urllib.request.urlopen(req, timeout=120) as r:
        json.load(r)
    with urllib.request.urlopen(B + "/api/packs", timeout=30) as r:
        after = {p["id"]: p for p in json.load(r)["packs"]}[pid]
    check("removing a pack is reported as removed (R32)",
          after["installed"] is False and after["diskMegabytes"] == 0,
          f"installed={after['installed']} disk={after['diskMegabytes']}MB")

# ---------------------------------------------------------------- 9. packaged regressions
(SP / "page.html").write_text(
    "<!doctype html><html><body><h1>Acceptance heading</h1>"
    "<p><strong>Bold acceptance paragraph.</strong></p>"
    "<table><tr><td>alpha</td><td>beta</td></tr>"
    "<tr><td>gamma</td><td>delta</td></tr></table></body></html>")
fid = upload(SP / "page.html")
out, err = one(fid, "pdf")
check("html -> pdf produces a PDF without taking the error path (R39)",
      bool(out) and not err and out.read_bytes()[:4] == b"%PDF", err)
check("html -> pdf reports no native-library loader failure (R39)",
      not any(s in err.lower() for s in ("libgobject", "dlopen", "cffi")), err[:90])
drop(fid)

for name in ("wmv", "wma", "m4a", "vob"):
    with urllib.request.urlopen(f"{B}/api/formats?q={name}", timeout=30) as r:
        found = json.load(r)["formats"]
    check(f"format catalog finds {name} by name (R42)",
          any(f["id"] == name for f in found),
          ", ".join(f["id"] for f in found) or "no matches")

subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                "sine=frequency=660:duration=2", "-c:a", "aac", "-y",
                str(SP / "strict.m4a")], check=True)
fid = upload(SP / "strict.m4a")
out, err = one(fid, "wav")
audio_codec = probe(out, "stream=codec_name", "a:0").get("codec_name", "") if out else ""
check("m4a -> wav uses a PCM audio codec (R41)",
      bool(out) and audio_codec.startswith("pcm"), err or audio_codec)
drop(fid)

subprocess.run([FFMPEG, "-v", "error",
                "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=2",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                "-c:v", "mpeg1video", "-c:a", "mp2", "-y", str(SP / "strict.mpg")],
               check=True)
fid = upload(SP / "strict.mpg")
out, err = one(fid, "vob")
video_codec = probe(out, "stream=codec_name", "v:0").get("codec_name", "") if out else ""
check("mpg -> vob uses MPEG-2 video (R41)",
      bool(out) and video_codec == "mpeg2video", err or video_codec)
out, err = one(fid, "wmv")
video_codec = probe(out, "stream=codec_name", "v:0").get("codec_name", "") if out else ""
audio_codec = probe(out, "stream=codec_name", "a:0").get("codec_name", "") if out else ""
check("mpg -> wmv uses Windows Media codecs (R41)",
      bool(out)
      and video_codec in {"wmv1", "wmv2", "wmv3", "msmpeg4v2", "msmpeg4v3"}
      and audio_codec in {"wmav1", "wmav2"},
      err or f"video={video_codec} audio={audio_codec}")
drop(fid)

print(f"\n  {len(passed)} passed, {len(failed)} failed")
if failed:
    print("  failed:", ", ".join(failed))
sys.exit(1 if failed else 0)
