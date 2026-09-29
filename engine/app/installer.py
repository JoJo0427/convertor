"""Downloading and installing engine packs.

Five rules shape this module, and each of them is here because the alternative
misleads the person waiting:

1. **Nothing is downloaded unless a click asked for it.** There is no background
   fetch, no prefetch, no "while you're here". An app sold on working offline
   does not get to reach for the network on its own.
2. **A failed install leaves nothing behind.** Everything lands in a staging
   directory and is moved into place as the last step. A cancel, a dropped
   connection or a bad checksum removes the staging directory and the pack is
   exactly as absent as it was before.
3. **The checksum is verified before anything is unpacked**, not after. An
   archive that fails its checksum is never handed to a tar or zip reader.
4. **Progress is real.** Bytes come from Content-Length where the server gives
   one; where it does not, the phase is reported without a fake percentage
   rather than an invented one.
5. **Failures say which step failed.** "Install failed" is not a message; "the
   download finished but the checksum did not match" is.

Installs are run in a thread rather than on the event loop: they are almost
entirely blocking I/O in the standard library, and a 300 MB download must not
stall the API that is reporting its progress.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import config
from .errors import ConversionError

# How much to read at a time. Large enough that the checksum is not the
# bottleneck, small enough that cancelling is felt immediately.
CHUNK = 1024 * 256

USER_AGENT = f"Convertor/{config.VERSION} (+local pack installer)"


@dataclass
class Install:
    """Live state of one pack install, as the interface sees it."""

    pack_id: str
    status: str = "running"          # running | completed | failed | cancelled
    phase: str = "starting"          # what is happening, in words
    # None when the server sent no Content-Length: better than a fake number.
    progress: float | None = None
    downloaded: int = 0
    total: int = 0
    error: dict | None = None
    started: float = field(default_factory=time.time)
    finished: float = 0.0

    def to_dict(self) -> dict:
        data = asdict(self)
        data["elapsed"] = round((self.finished or time.time()) - self.started, 1)
        return data


# Install state lives in memory: it is meaningful only while the app is open, and
# a half-finished install does not survive a restart by design (rule 2).
_installs: dict[str, Install] = {}
_cancelled: set[str] = set()
_lock = threading.Lock()


def state_for(pack_id: str) -> dict | None:
    with _lock:
        install = _installs.get(pack_id)
        return install.to_dict() if install else None


def all_states() -> dict[str, dict]:
    with _lock:
        return {k: v.to_dict() for k, v in _installs.items()}


def _set(pack_id: str, **fields) -> None:
    with _lock:
        install = _installs.get(pack_id)
        if install is None:
            return
        for key, value in fields.items():
            setattr(install, key, value)


def _check_cancelled(pack_id: str) -> None:
    with _lock:
        if pack_id in _cancelled:
            raise _Cancelled()


class _Cancelled(Exception):
    """Raised inside a worker when the user cancelled."""


# --------------------------------------------------------------------- download


def _download(pack_id: str, url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        response = urllib.request.urlopen(request, timeout=60)
    except urllib.error.HTTPError as error:
        raise ConversionError(
            code="pack_download_failed",
            title="The download could not be started",
            detail=f"{url} returned HTTP {error.code}.",
            suggestions=[
                "Check your internet connection and try again.",
                "The pinned version may have been withdrawn upstream; a Convertor "
                "update would carry a new one.",
            ],
        ) from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise ConversionError(
            code="pack_unreachable",
            title="The download server could not be reached",
            detail=f"Connecting to {url} failed: {error}",
            suggestions=[
                "Check your internet connection and try again.",
                "If you are behind a proxy, Convertor uses the system proxy "
                "settings.",
            ],
        ) from error

    with response:
        total = int(response.headers.get("Content-Length") or 0)
        _set(pack_id, phase="downloading", total=total,
             progress=0.0 if total else None)
        done = 0
        with open(target, "wb") as handle:
            while True:
                _check_cancelled(pack_id)
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                handle.write(chunk)
                done += len(chunk)
                _set(pack_id, downloaded=done,
                     progress=(done / total) if total else None)

    if total and done != total:
        raise ConversionError(
            code="pack_truncated",
            title="The download ended early",
            detail=f"Expected {total} bytes but received {done}.",
            suggestions=["Try again — this is usually a dropped connection."],
        )


def _verify(pack_id: str, path: Path, expected: str) -> None:
    """Checksum before unpacking, never after."""
    if not expected:
        # No pinned checksum: say so rather than implying the file was verified.
        _set(pack_id, phase="no checksum published for this file")
        return
    _set(pack_id, phase="checking the download", progress=None)
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            _check_cancelled(pack_id)
            digest.update(block)
    actual = digest.hexdigest()
    if actual != expected:
        raise ConversionError(
            code="pack_checksum_mismatch",
            title="The download did not match its checksum",
            detail=(
                "The file finished downloading but its contents are not what "
                f"this version of Convertor expects.\nexpected {expected}\n"
                f"received {actual}"
            ),
            suggestions=[
                "Try again — a corrupted download is the usual cause.",
                "If it fails again, do not install it; report the mismatch.",
            ],
        )


# ---------------------------------------------------------------------- unpack


def _expose(binary: Path, bin_dir: Path, name: str) -> None:
    """Put an executable where the engine will look for it.

    The link is **relative**, and that is the whole point of this function.
    Everything is assembled in a staging directory and renamed into place at the
    end, so an absolute symlink written during staging points at a path that
    stops existing the instant the install succeeds — producing a pack that
    reports itself installed and provides nothing. Relative survives the move.

    A symlink rather than a copy because LibreOffice's `soffice` must stay inside
    its own .app to find its libraries; lifting the binary out breaks it.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    destination = bin_dir / name
    if destination.exists() or destination.is_symlink():
        destination.unlink()
    if binary.parent == bin_dir:
        os.chmod(binary, 0o755)
        return
    try:
        destination.symlink_to(os.path.relpath(binary, bin_dir))
    except OSError:
        shutil.copy2(binary, destination)
        os.chmod(destination, 0o755)


def _unpack_archive(pack_id: str, source, archive: Path, staging: Path) -> None:
    _set(pack_id, phase="unpacking", progress=None)
    extracted = staging / "extracted"
    extracted.mkdir(parents=True, exist_ok=True)

    if source.kind == "zip":
        with zipfile.ZipFile(archive) as z:
            z.extractall(extracted)
    else:
        # filter="data" refuses absolute paths and traversal outside the target.
        # Upstream archives are trusted, but a checked extraction costs nothing
        # and this code would otherwise be a way to write anywhere on disk.
        with tarfile.open(archive) as t:
            try:
                t.extractall(extracted, filter="data")
            except TypeError:  # Python < 3.12
                t.extractall(extracted)

    bin_dir = staging / "bin"
    for member, exposed in source.members:
        found = extracted / member
        if not found.is_file():
            # Upstream renamed a path inside the archive: say which one, because
            # "install failed" would send someone reading the wrong code.
            matches = list(extracted.rglob(Path(member).name))
            if not matches:
                raise ConversionError(
                    code="pack_layout_changed",
                    title="The download did not contain what was expected",
                    detail=f"{member} is not in the archive.",
                    suggestions=["This needs a Convertor update to fix."],
                )
            found = matches[0]
        bin_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(found, bin_dir / exposed)
        os.chmod(bin_dir / exposed, 0o755)

    # Only the named members are wanted. Keeping the rest would charge the user
    # disk space for a manual and a changelog they will never open — the 7-Zip
    # archive is 1 MB of binary and 1 MB of HTML documentation.
    shutil.rmtree(extracted, ignore_errors=True)


def _unpack_dmg(pack_id: str, source, image: Path, staging: Path) -> None:
    """Copy an .app out of a macOS disk image.

    Mounted read-only and with -nobrowse so nothing appears in Finder, and
    detached in a finally block: a leaked mount survives the app and confuses
    the next install.
    """
    _set(pack_id, phase="mounting the disk image", progress=None)
    mount = staging / "mnt"
    mount.mkdir(parents=True, exist_ok=True)
    attach = subprocess.run(
        ["hdiutil", "attach", str(image), "-mountpoint", str(mount),
         "-nobrowse", "-readonly", "-quiet"],
        capture_output=True, text=True,
    )
    if attach.returncode != 0:
        raise ConversionError(
            code="pack_mount_failed",
            title="The downloaded disk image could not be opened",
            detail=(attach.stderr or attach.stdout).strip()[:300],
            suggestions=["Try the download again."],
        )
    try:
        app = mount / source.app
        if not app.is_dir():
            candidates = [p for p in mount.iterdir() if p.suffix == ".app"]
            if not candidates:
                raise ConversionError(
                    code="pack_layout_changed",
                    title="The disk image did not contain an application",
                    detail=f"{source.app} is not on the mounted image.",
                    suggestions=["This needs a Convertor update to fix."],
                )
            app = candidates[0]
        _set(pack_id, phase=f"copying {app.name}")
        shutil.copytree(app, staging / app.name, symlinks=True)
    finally:
        subprocess.run(["hdiutil", "detach", str(mount), "-quiet", "-force"],
                       capture_output=True)
        # Scaffolding, not payload: an empty mount point left in the pack
        # directory is confusing and would be backed up with it.
        try:
            mount.rmdir()
        except OSError:
            pass

    binary = staging / app.name / source.executable
    if not binary.is_file():
        raise ConversionError(
            code="pack_layout_changed",
            title="The application is missing its program file",
            detail=f"{source.executable} is not inside {app.name}.",
            suggestions=["This needs a Convertor update to fix."],
        )
    _expose(binary, staging / "bin", Path(source.executable).name)


def _unpack_windows_installer(pack_id: str, source, package: Path, staging: Path) -> None:
    """Run a Windows installer into the pack directory rather than system-wide.

    An MSI is administratively extracted, which unpacks its payload without
    touching the registry or Program Files — so removing the pack is deleting a
    directory, and the machine is untouched by an install the user may cancel.
    NSIS installers (7-Zip) take /S /D= for the same effect.
    """
    _set(pack_id, phase="unpacking the installer", progress=None)
    target = staging / "payload"
    target.mkdir(parents=True, exist_ok=True)

    if source.kind == "msi":
        result = subprocess.run(
            ["msiexec", "/a", str(package), "/qn", f"TARGETDIR={target}"],
            capture_output=True, text=True,
        )
    else:
        result = subprocess.run(
            [str(package), "/S", f"/D={target}"], capture_output=True, text=True,
        )
    if result.returncode != 0:
        raise ConversionError(
            code="pack_install_failed",
            title="The installer did not complete",
            detail=(result.stderr or result.stdout).strip()[:300]
                   or f"The installer exited with code {result.returncode}.",
            suggestions=["Try again, or install the program yourself — Convertor "
                         "will find it on your PATH."],
        )

    wanted = Path(source.executable).name
    matches = list(target.rglob(wanted))
    if not matches:
        raise ConversionError(
            code="pack_layout_changed",
            title="The installer did not produce the expected program",
            detail=f"{wanted} was not found under the unpacked installer.",
            suggestions=["This needs a Convertor update to fix."],
        )
    _expose(matches[0], staging / "bin", wanted)


def _install_pip(pack_id: str, pack, staging: Path) -> None:
    """Install a Python-only pack into its own directory.

    `--target` rather than the bundled runtime's site-packages: an app update
    replaces the runtime, and a pack installed inside it would vanish silently,
    taking a route the user had paid 132 MB for with it.
    """
    requirements = Path(__file__).resolve().parent.parent / pack.requirements
    if not requirements.is_file():
        raise ConversionError(
            code="pack_requirements_missing",
            title="This pack's package list is missing from the install",
            detail=f"{pack.requirements} is not in the engine directory.",
            suggestions=["Reinstalling Convertor should restore it."],
        )

    site = staging / "site"
    site.mkdir(parents=True, exist_ok=True)
    _set(pack_id, phase="installing Python packages", progress=None)

    process = subprocess.Popen(
        [sys.executable, "-m", "pip", "install", "--no-input", "--disable-pip-version-check",
         "--target", str(site), "-r", str(requirements)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    tail: list[str] = []
    assert process.stdout is not None
    for line in process.stdout:
        line = line.strip()
        if not line:
            continue
        tail.append(line)
        del tail[:-40]
        # pip's own progress is the only honest progress available here.
        if line.startswith(("Collecting", "Downloading", "Installing", "Building")):
            _set(pack_id, phase=line[:120])
        try:
            _check_cancelled(pack_id)
        except _Cancelled:
            process.kill()
            raise
    if process.wait() != 0:
        from .errors import tool_failed

        raise tool_failed("pip", pack.label, "\n".join(tail))


# ------------------------------------------------------------------------ driver


def _what_is_missing(pack) -> list[str]:
    """Everything the pack claimed to provide and does not.

    Binaries are checked by resolving *and running* them: a broken symlink, a
    quarantined binary and a wrong-architecture build all pass an existence test
    and all fail the moment a conversion needs them.
    """
    import importlib
    import importlib.util

    from . import engines

    problems: list[str] = []

    for engine_id in pack.provides_engines:
        resolved = engines._resolve(engines.ENGINES[engine_id].binary)
        if resolved is None:
            problems.append(f"{engine_id} could not be found in the pack")
            continue
        if not Path(resolved).exists():
            problems.append(f"{engine_id} resolves to a path that does not exist")
            continue
        if not os.access(resolved, os.X_OK):
            problems.append(f"{engine_id} is not executable")

    importlib.invalidate_caches()
    for module in pack.provides_modules:
        if importlib.util.find_spec(module) is None:
            problems.append(f"{module} still cannot be imported")

    return problems


def _run(pack) -> None:
    """Install one pack. Runs in a worker thread."""
    source = pack.source()
    staging = Path(tempfile.mkdtemp(prefix=f"pack-{pack.id}-", dir=config.TMP_DIR))
    try:
        if source.kind == "pip":
            _install_pip(pack.id, pack, staging)
        else:
            archive = staging / Path(source.url).name
            _download(pack.id, source.url, archive)
            _verify(pack.id, archive, source.sha256)
            _check_cancelled(pack.id)
            if source.kind in ("zip", "tar"):
                _unpack_archive(pack.id, source, archive, staging)
            elif source.kind == "dmg-app":
                _unpack_dmg(pack.id, source, archive, staging)
            elif source.kind in ("msi", "exe-nsis"):
                _unpack_windows_installer(pack.id, source, archive, staging)
            else:
                raise ConversionError(
                    code="pack_unknown_kind",
                    title="This pack does not know how to install itself",
                    detail=f"Unrecognised source kind {source.kind!r}.",
                    suggestions=["This is a bug; please report it."],
                )
            archive.unlink(missing_ok=True)

        # Everything above happened in staging. This is the only step that
        # changes what the app can do, and it is a rename.
        _set(pack.id, phase="finishing", progress=None)
        _check_cancelled(pack.id)
        root = pack.root
        if root.exists():
            retired = root.with_name(f"{root.name}.replacing")
            shutil.rmtree(retired, ignore_errors=True)
            root.rename(retired)
        else:
            retired = None
        root.parent.mkdir(parents=True, exist_ok=True)
        try:
            staging.rename(root)
        except Exception:
            if retired is not None:
                try:
                    retired.rename(root)
                except Exception:
                    # The failure that displaced the working pack is still the
                    # useful error to report, even if recovery also fails.
                    pass
            raise
        staging = None  # type: ignore[assignment]
        if retired is not None:
            shutil.rmtree(retired, ignore_errors=True)

        # Binary packs change what resolves on disk; Python packs change what
        # imports. Both need the caches that answered "not installed" cleared.
        from . import engines, packs as packs_module

        engines.refresh()
        packs_module.activate_site_dirs()
        # The measured size is cached, and this is the moment it stopped being
        # true. Without this the packs screen would report the old size, or none.
        packs_module.invalidate_size_cache(pack.id)

        # Only now is it honest to say "installed" — and only if it is true.
        #
        # This check exists because the alternative already happened: a pack that
        # downloaded, verified, unpacked and renamed into place without a single
        # error, reported success, and provided nothing, because one symlink
        # pointed at the staging directory it had just been moved out of. A step
        # that completes without error is not a step that worked.
        _set(pack.id, phase="checking the install")
        broken = _what_is_missing(pack)
        if broken:
            shutil.rmtree(pack.root, ignore_errors=True)
            engines.refresh()
            raise ConversionError(
                code="pack_install_incomplete",
                title=f"{pack.label} installed but does not work",
                detail="The download and unpacking succeeded, but afterwards "
                       + "; ".join(broken)
                       + ". The pack has been removed rather than left in a "
                         "state where conversions would fail one at a time.",
                suggestions=[
                    "Try again — a partial unpack is usually transient.",
                    "If it fails again this is a bug in Convertor, not in your "
                    "machine.",
                ],
            )

        # A converter that decided its format set from what was importable has
        # to be asked again — otherwise the parquet pack installs and Parquet
        # still does not appear.
        from . import converters

        converters.load(force=True)

        _set(pack.id, status="completed", phase="installed", progress=1.0,
             finished=time.time())
    except _Cancelled:
        _set(pack.id, status="cancelled", phase="cancelled", finished=time.time())
    except ConversionError as error:
        _set(pack.id, status="failed", phase="failed", finished=time.time(),
             error=error.to_dict())
    except Exception as error:  # noqa: BLE001 - last resort, must still report
        _set(pack.id, status="failed", phase="failed", finished=time.time(),
             error={
                 "code": "pack_install_error",
                 "title": f"{pack.label} could not be installed",
                 "detail": f"{type(error).__name__}: {error}",
                 "suggestions": ["Try again. If it keeps failing, install the "
                                 "program yourself — Convertor will find it on "
                                 "your PATH."],
             })
    finally:
        with _lock:
            _cancelled.discard(pack.id)
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


def start(pack) -> dict:
    """Begin installing a pack. Returns its initial state."""
    from . import packs as packs_module

    install = Install(pack_id=pack.id)
    with _lock:
        existing = _installs.get(pack.id)
        if existing and existing.status == "running":
            return existing.to_dict()
        _installs[pack.id] = install
        _cancelled.discard(pack.id)

    try:
        if pack.installed:
            raise ConversionError(
                code="pack_already_installed",
                title=f"{pack.label} is already installed",
                detail="Nothing to download.",
                suggestions=[],
            )
        source = pack.source()
        if source is None:
            raise ConversionError(
                code="pack_unavailable",
                title=f"{pack.label} cannot be installed automatically",
                detail=packs_module._unavailable_reason(pack, None),
                suggestions=[pack.manual_hint] if pack.manual_hint else [],
            )
        if config.BUILD_KIND != "desktop":
            raise ConversionError(
                code="pack_not_manageable",
                title="Packs are part of the image in this build",
                detail="Under Docker every pack ships in the image, and a download "
                       "into the container would be lost when it is recreated.",
                suggestions=["Use the desktop app to manage packs individually."],
            )

        config.PACKS_DIR.mkdir(parents=True, exist_ok=True)
        thread = threading.Thread(target=_run, args=(pack,), daemon=True,
                                  name=f"pack-install-{pack.id}")
        thread.start()
    except Exception:
        with _lock:
            if _installs.get(pack.id) is install:
                _installs.pop(pack.id)
            _cancelled.discard(pack.id)
        raise
    return state_for(pack.id) or {}


def cancel(pack_id: str) -> dict | None:
    """Ask a running install to stop. It tears its own staging directory down."""
    with _lock:
        install = _installs.get(pack_id)
        if install is None or install.status != "running":
            return install.to_dict() if install else None
        _cancelled.add(pack_id)
        return install.to_dict()


def _forget_modules_under(root: Path) -> int:
    """Drop already-imported modules whose files lived under `root`.

    Without this, removing a pack does not look removed. `find_spec` answers from
    `sys.modules` first, so a module that was imported once keeps reporting a
    valid spec long after its files are deleted — and the pack panel would show
    a pack the user just removed as still installed.

    A loaded extension module stays mapped into this process either way; that
    cannot be undone. What can be undone is the *claim* that it is available, and
    the next import attempt then fails honestly on the missing file.
    """
    prefix = str(root)
    stale = []
    for name, module in list(sys.modules.items()):
        origin = getattr(module, "__file__", None) or ""
        if origin.startswith(prefix):
            stale.append(name)
    for name in stale:
        sys.modules.pop(name, None)
    return len(stale)


def remove(pack) -> None:
    """Delete an installed pack, and stop claiming what it provided."""
    root = pack.root
    if root.exists():
        # Move aside first, then delete: a large tree takes a moment to unlink,
        # and until it is gone the pack would still resolve.
        retired = root.with_name(f"{root.name}.removing")
        shutil.rmtree(retired, ignore_errors=True)
        try:
            root.rename(retired)
        except OSError:
            retired = root
        _forget_modules_under(root)
        shutil.rmtree(retired, ignore_errors=True)

    from . import engines, packs as packs_module

    engines.refresh()
    importlib.invalidate_caches()
    packs_module.invalidate_size_cache(pack.id)
    with _lock:
        _installs.pop(pack.id, None)


async def wait_for_change(pack_id: str, previous: dict | None,
                          timeout: float = 1.0) -> dict | None:
    """Poll one install's state, for the SSE endpoint."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = state_for(pack_id)
        if current != previous:
            return current
        await asyncio.sleep(0.15)
    return previous
