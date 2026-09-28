#!/usr/bin/env python3
"""Copy native engine binaries into the app bundle, with their libraries.

A Homebrew binary links against absolute paths under /opt/homebrew, which exist
on this machine and on no user's. Shipping one unchanged produces an app that
works perfectly for the person who built it and dies instantly for everyone
else — the worst possible failure mode, because it passes local testing.

So each binary is copied in along with every non-system library it depends on,
transitively, and every reference is rewritten to @executable_path. The result
is verified at the end: if anything still points outside the bundle, this exits
non-zero rather than producing a broken app.

Usage:
    python3 bundle_binaries.py ffmpeg ffprobe potrace
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BIN_DIR = HERE / "src-tauri" / "binaries"
LIB_DIR = BIN_DIR / "lib"

# Anything under these prefixes ships with macOS and must not be copied.
SYSTEM_PREFIXES = ("/usr/lib/", "/System/", "/Library/Frameworks/")


def run(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=False).stdout


def rpaths(binary: Path, origin: Path) -> list[Path]:
    """The LC_RPATH entries of a binary, with @loader_path already resolved."""
    found: list[Path] = []
    lines = run("otool", "-l", str(binary)).splitlines()
    for i, line in enumerate(lines):
        if "LC_RPATH" not in line:
            continue
        for follow in lines[i : i + 6]:
            token = follow.strip()
            if token.startswith("path "):
                raw = token[5:].split(" (offset")[0].strip()
                raw = raw.replace("@loader_path", str(origin.parent))
                raw = raw.replace("@executable_path", str(origin.parent))
                found.append(Path(raw))
                break
    return found


def dependencies(binary: Path, origin: Path | None = None) -> list[str]:
    """Absolute paths of the non-system libraries this binary needs.

    `binary` is where the file is now; `origin` is where it came from, which is
    what @loader_path and @rpath are relative to. They differ as soon as a file
    has been copied into the bundle, and getting that wrong is how a dependency
    goes missing.

    @-prefixed references are resolved rather than skipped. Skipping them is
    tempting — they *look* already-relative and therefore fine — but a Homebrew
    library reached through @rpath is no more present in the bundle than one
    reached by absolute path. libwebp refers to libsharpyuv this way, and
    skipping it produced an app where every Ghostscript conversion died on a
    missing dylib while the bundle verified clean.
    """
    origin = origin or binary
    search = rpaths(binary, origin)
    out: list[str] = []

    for line in run("otool", "-L", str(binary)).splitlines()[1:]:
        ref = line.strip().split(" (")[0]
        if not ref:
            continue

        if ref.startswith("@rpath/"):
            name = ref[len("@rpath/"):]
            for root in search:
                candidate = root / name
                if candidate.exists():
                    ref = str(candidate)
                    break
            else:
                # Not on any rpath: fall back to the origin's own directory,
                # which is where Homebrew keeps siblings.
                ref = str(origin.parent / name)
        elif ref.startswith("@loader_path/"):
            ref = str((origin.parent / ref[len("@loader_path/"):]).resolve())
        elif ref.startswith("@executable_path/"):
            ref = str((origin.parent / ref[len("@executable_path/"):]).resolve())
        elif ref.startswith("@"):
            continue

        if any(ref.startswith(prefix) for prefix in SYSTEM_PREFIXES):
            continue
        # A reference that already points inside the bundle is done.
        if str(BIN_DIR) in ref:
            continue
        out.append(ref)
    return out


def raw_references(binary: Path) -> list[str]:
    """Every install name a binary carries, verbatim — no resolution."""
    return [
        line.strip().split(" (")[0]
        for line in run("otool", "-L", str(binary)).splitlines()[1:]
        if line.strip()
    ]


def collect(binary: Path, origin: Path, seen: set[str], missing: list[str]) -> None:
    """Copy every transitive dependency into lib/, once each.

    Recursion walks the *original* file, not the copy, so @loader_path and
    @rpath keep resolving against the directory they were written for.
    """
    for dep in dependencies(binary, origin):
        name = os.path.basename(dep)
        if name in seen:
            continue
        source = Path(dep)
        if not source.exists():
            missing.append(f"{origin.name} needs {dep}, which does not exist")
            continue
        seen.add(name)
        target = LIB_DIR / name
        shutil.copy2(source, target)
        target.chmod(0o755)
        collect(target, source, seen, missing)


def relink(binary: Path, rel_lib: str) -> None:
    """Point every reference at the bundled copy.

    Rewriting works on the verbatim install name, because that is the string
    install_name_tool matches — a resolved absolute path would match nothing for
    an @rpath entry and leave it silently unchanged.
    """
    for ref in raw_references(binary):
        if any(ref.startswith(prefix) for prefix in SYSTEM_PREFIXES):
            continue
        if ref.startswith(rel_lib + "/"):
            continue
        name = os.path.basename(ref)
        if name == binary.name:
            continue
        run("install_name_tool", "-change", ref, f"{rel_lib}/{name}", str(binary))
    # A library must also announce its own new location.
    if binary.suffix == ".dylib":
        run("install_name_tool", "-id", f"{rel_lib}/{binary.name}", str(binary))


def verify() -> list[str]:
    """Every reference must resolve to a file that is actually in the bundle.

    The earlier version of this check accepted any @-prefixed reference as
    already fine, which is what let a missing @rpath dependency ship: the bundle
    verified clean and then died at runtime on a dylib that was never copied.
    So relative references are now *followed*, not trusted.
    """
    problems: list[str] = []
    for path in list(BIN_DIR.iterdir()) + list(LIB_DIR.iterdir()):
        if path.is_dir() or not os.access(path, os.X_OK):
            continue
        for ref in raw_references(path):
            if any(ref.startswith(prefix) for prefix in SYSTEM_PREFIXES):
                continue
            if ref.startswith("@executable_path/"):
                resolved = BIN_DIR / ref[len("@executable_path/"):]
            elif ref.startswith("@loader_path/"):
                resolved = path.parent / ref[len("@loader_path/"):]
            elif ref.startswith("@"):
                problems.append(f"{path.name} has an unresolvable reference {ref}")
                continue
            else:
                problems.append(f"{path.name} still references {ref}")
                continue
            if not resolved.exists():
                problems.append(f"{path.name} needs {ref}, which is not in the bundle")
    return problems


def main(names: list[str]) -> int:
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    LIB_DIR.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    bundled: list[str] = []
    unresolved: list[str] = []

    for name in names:
        source = shutil.which(name)
        if source is None:
            print(f"  {name}: not on PATH, skipping")
            continue
        target = BIN_DIR / name
        shutil.copy2(source, target)
        target.chmod(0o755)
        print(f"  {name}: copied")
        collect(target, Path(source), seen, unresolved)
        bundled.append(name)

    # Executables sit in binaries/, libraries in binaries/lib/.
    for name in bundled:
        relink(BIN_DIR / name, "@executable_path/lib")
    for lib in LIB_DIR.iterdir():
        if lib.is_file():
            relink(lib, "@loader_path")

    # Ad-hoc signing: macOS refuses to load a dylib whose signature no longer
    # matches after install_name_tool rewrote its header.
    for path in list(BIN_DIR.iterdir()) + list(LIB_DIR.iterdir()):
        if path.is_file():
            run("codesign", "--force", "--sign", "-", str(path))

    problems = verify() + unresolved
    total = sum(f.stat().st_size for f in LIB_DIR.iterdir() if f.is_file())
    total += sum(f.stat().st_size for f in BIN_DIR.iterdir() if f.is_file())

    print(f"\n  {len(bundled)} binaries, {len(seen)} libraries, {total / 1e6:.0f} MB")
    if problems:
        print("\n  BUNDLE IS NOT SELF-CONTAINED:")
        for p in problems[:12]:
            print(f"    {p}")
        return 1
    print("  every reference resolves inside the bundle")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or ["ffmpeg", "ffprobe"]))
