"""Engine packs — the capabilities that are not in the base install.

The desktop build ships a small base and fetches the heavy engines when they are
first needed. This module is what makes that legible: it maps each optional
capability to a named pack, reports whether it is present, and lets the
interface say *"needs the Office pack, 350 MB"* rather than *"LibreOffice is not
installed"*, which means nothing to someone who has never heard of LibreOffice.

The Docker build ships every pack, so everything here simply reports installed.
The same code serves both, and nothing elsewhere in the engine needs to know
which build it is running in.

Packs are fetched from the **upstream projects themselves** — The Document
Foundation for LibreOffice, the pandoc GitHub release, 7-zip.org, PyPI — rather
than from a mirror of our own. That costs a little flexibility and buys the thing
that matters: there is no build of ours in the trust path, and a user can check
any URL here against the project's own download page.

Every artefact is pinned by version and, where upstream publishes one, by
SHA-256. Upstream checksums are quoted verbatim; the rest were computed once from
the pinned file and are recorded here so a changed or truncated download is
caught. `desktop/packs/pin.py` recomputes them when a version is bumped.
"""

from __future__ import annotations

import importlib
import importlib.util
import platform
from dataclasses import dataclass, field

from . import engines


# Platform keys. "any" means the artefact is platform-independent (a pip install).
def platform_key() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        machine = "x86_64"
    elif machine in ("arm64", "aarch64"):
        machine = "arm64"
    if system == "darwin":
        return f"macos-{machine}"
    if system == "windows":
        return f"windows-{machine}"
    return f"{system}-{machine}"


@dataclass(frozen=True)
class Source:
    """One downloadable artefact, for one platform.

    `kind` decides how it is unpacked, and each kind is a deliberate choice
    rather than a generic "extract it and hope":

    - `zip` / `tar`   plain archive; `members` maps an archive path to the name
                      the engine looks for, because upstream naming is not ours
                      to predict (7-Zip ships `7zz`, the engine wants `7z`).
    - `dmg-app`       a macOS disk image holding an .app. The app is copied whole
                      — LibreOffice is not a single relocatable binary — and a
                      symlink into `bin/` points at the executable inside it.
    - `msi` / `exe`   a Windows installer, run silently into the pack directory.
    - `pip`           installed from a requirements file into the pack's own
                      site directory, never into the bundled runtime, so an app
                      update cannot half-remove it.
    """

    platform: str
    url: str = ""
    sha256: str = ""
    kind: str = "zip"
    # archive member -> name to expose in the pack's bin/ directory
    members: tuple[tuple[str, str], ...] = ()
    # For dmg-app: the .app to copy, and the executable inside it to expose.
    app: str = ""
    executable: str = ""
    # Measured size of the download, so the interface can be honest before it
    # starts rather than quoting a guess.
    megabytes: int = 0
    # Measured size once installed, which for a pip pack is three times the
    # download and for an .app is more than twice it. Someone deciding whether
    # to spend the space needs the number that will actually be spent.
    installed_megabytes: int = 0


@dataclass(frozen=True)
class Pack:
    id: str
    label: str
    summary: str
    # What the user gains, in their words, not ours.
    unlocks: tuple[str, ...]
    # Rough download size in megabytes, for the button that offers it.
    megabytes: int
    # Engine ids from engines.ENGINES this pack provides.
    provides_engines: tuple[str, ...] = ()
    # Importable module names this pack provides.
    provides_modules: tuple[str, ...] = ()
    # Requirements file, for the Python-only packs.
    requirements: str = ""
    # Where the payload comes from, per platform. A pack with no source for this
    # platform is reported as not installable, with the reason, instead of
    # offering a button that cannot work.
    sources: tuple[Source, ...] = ()
    # Shown when there is no source: the manual route, in one line.
    manual_hint: str = ""

    @property
    def root(self):
        """Where this pack's files live once installed."""
        from . import config

        return config.PACKS_DIR / self.id

    @property
    def bin_dir(self):
        return self.root / "bin"

    @property
    def site_dir(self):
        return self.root / "site"

    def source(self) -> "Source | None":
        key = platform_key()
        for source in self.sources:
            if source.platform in (key, "any"):
                return source
        return None

    @property
    def installed(self) -> bool:
        """Present if everything it claims to provide is actually resolvable."""
        for engine_id in self.provides_engines:
            if not engines.available(engine_id):
                return False
        for module in self.provides_modules:
            if importlib.util.find_spec(module) is None:
                return False
        return bool(self.provides_engines or self.provides_modules)

    @property
    def download_megabytes(self) -> int:
        source = self.source()
        return (source.megabytes if source and source.megabytes else self.megabytes)

    @property
    def installed_megabytes(self) -> int:
        """What it costs on disk. Falls back to the download size when a pack is
        unpacked rather than expanded."""
        source = self.source()
        if source and source.installed_megabytes:
            return source.installed_megabytes
        return self.download_megabytes

    @property
    def disk_megabytes(self) -> int:
        """Actual measured size of what is installed, or 0 when it is not."""
        root = self.root
        if not root.is_dir():
            return 0
        total = 0
        for path in root.rglob("*"):
            try:
                if path.is_file() and not path.is_symlink():
                    total += path.stat().st_size
            except OSError:
                continue
        return round(total / 1_000_000)

    def to_dict(self) -> dict:
        from . import installer

        source = self.source()
        job = installer.state_for(self.id)
        return {
            "id": self.id,
            "label": self.label,
            "summary": self.summary,
            "unlocks": list(self.unlocks),
            "megabytes": self.download_megabytes,
            "installedMegabytes": self.installed_megabytes,
            "diskMegabytes": self.disk_megabytes,
            "installed": self.installed,
            "engines": list(self.provides_engines),
            # Whether this build can fetch it at all, and if not, why not.
            "installable": bool(source) and _is_desktop_build(),
            "unavailableReason": _unavailable_reason(self, source),
            "manualHint": self.manual_hint,
            # Live install state, absent when nothing is running.
            "job": job,
        }


# Upstream download roots, pinned to a version. Bumping a version means bumping
# the checksums with it — desktop/packs/pin.py does both.
_LO = "https://download.documentfoundation.org/libreoffice/stable/26.8.0/"
_PANDOC = "https://github.com/jgm/pandoc/releases/download/3.11/"


PACKS: tuple[Pack, ...] = (
    Pack(
        id="office",
        label="Office documents",
        summary="Reads and writes Word, Excel and PowerPoint files, and their "
                "OpenDocument equivalents.",
        unlocks=("DOCX", "XLSX", "PPTX", "ODT", "ODS", "ODP", "DOC", "XLS", "PPT"),
        megabytes=298,
        provides_engines=("libreoffice",),
        sources=(
            # Checksums as published by The Document Foundation alongside each file.
            Source(
                platform="macos-arm64",
                url=_LO + "mac/aarch64/LibreOffice_26.8.0_MacOS_aarch64.dmg",
                sha256="8858d8058da4f862f47559486814e65efc27294da67c5e4bb56b006b1ee59f89",
                kind="dmg-app",
                app="LibreOffice.app",
                executable="Contents/MacOS/soffice",
                megabytes=298,
                installed_megabytes=802,
            ),
            Source(
                platform="macos-x86_64",
                url=_LO + "mac/x86_64/LibreOffice_26.8.0_MacOS_x86-64.dmg",
                sha256="2dcbce4894e01bc1ecd594658e2cbda70ff7bfcd0b310f35d38887797172d09e",
                kind="dmg-app",
                app="LibreOffice.app",
                executable="Contents/MacOS/soffice",
                megabytes=309,
                installed_megabytes=830,
            ),
            Source(
                platform="windows-x86_64",
                url=_LO + "win/x86_64/LibreOffice_26.8.0_Win_x86-64.msi",
                sha256="4aa6c6e1895f4055104effcb556bd3362d20c6ad707c149543304f395ef9db95",
                kind="msi",
                executable="program/soffice.exe",
                megabytes=350,
            ),
        ),
        manual_hint="Installing LibreOffice yourself also works — the app will "
                    "find it on your PATH.",
    ),
    Pack(
        id="markup",
        label="E-books and markup",
        summary="Converts between Markdown, HTML, EPUB, reStructuredText, LaTeX "
                "and Word.",
        unlocks=("EPUB", "RST", "LaTeX", "Markdown ↔ Word"),
        megabytes=41,
        provides_engines=("pandoc",),
        sources=(
            # pandoc publishes no checksum file; these were computed from the
            # pinned releases. See the note at the top of this module.
            Source(
                platform="macos-arm64",
                url=_PANDOC + "pandoc-3.11-arm64-macOS.zip",
                sha256="15806bedf9517bfead72e88fe6a6696635c3691efbb6e152173440e9c5bb50b4",
                kind="zip",
                members=(("pandoc-3.11-arm64/bin/pandoc", "pandoc"),),
                megabytes=41,
            ),
            Source(
                platform="macos-x86_64",
                url=_PANDOC + "pandoc-3.11-x86_64-macOS.zip",
                sha256="3b1c1b57f160112c821d02f23d946ede8b7f57a6ccf4632a25a512d334a9291f",
                kind="zip",
                members=(("pandoc-3.11-x86_64/bin/pandoc", "pandoc"),),
                megabytes=26,
            ),
            Source(
                platform="windows-x86_64",
                url=_PANDOC + "pandoc-3.11-windows-x86_64.zip",
                sha256="2ab72baf2399450e148ddf7a2a8689806c42e1bba71862b57e220fd9b8456d3d",
                kind="zip",
                members=(("pandoc-3.11/pandoc.exe", "pandoc.exe"),),
                megabytes=41,
            ),
        ),
    ),
    Pack(
        id="pdfword",
        label="PDF to Word with layout",
        summary="Rebuilds a PDF as an editable document, keeping its tables, "
                "images and page structure instead of only the text.",
        unlocks=("PDF → Word", "PDF → Markdown with tables"),
        megabytes=128,
        provides_modules=("pdf2docx", "pymupdf4llm"),
        requirements="requirements-pack-pdfword.txt",
        sources=(Source(platform="any", kind="pip", megabytes=128,
                        installed_megabytes=421),),
    ),
    Pack(
        id="ocr",
        label="Reading scans",
        summary="Recognises text in scanned documents and pictures, in 17 "
                "languages.",
        unlocks=("Scanned PDF → text", "Image → text", "Searchable PDF"),
        megabytes=50,
        provides_engines=("tesseract",),
        # Tesseract publishes no portable build for macOS, and the common Windows
        # installer is third-party. Rather than ship a button that downloads
        # something we cannot point at an official release, this pack states the
        # one-line manual route. An installed Tesseract is picked up from PATH
        # like any other engine.
        sources=(),
        manual_hint="macOS: brew install tesseract tesseract-lang · "
                    "Windows: install Tesseract from the UB Mannheim project, "
                    "then restart Convertor.",
    ),
    Pack(
        id="archives",
        label="7z and RAR",
        summary="Opens 7z and RAR archives, and creates 7z.",
        unlocks=("7z", "RAR"),
        megabytes=2,
        provides_engines=("sevenzip",),
        sources=(
            Source(
                platform="macos-arm64",
                url="https://www.7-zip.org/a/7z2603-mac.tar.xz",
                sha256="5ca87677072c59f5602e5c49baa27d4694bacd2259b4e507f0094249d4281480",
                kind="tar",
                # Upstream names it 7zz; the engine resolves "7z".
                members=(("7zz", "7z"),),
                megabytes=2,
            ),
            Source(
                platform="macos-x86_64",
                url="https://www.7-zip.org/a/7z2603-mac.tar.xz",
                sha256="5ca87677072c59f5602e5c49baa27d4694bacd2259b4e507f0094249d4281480",
                kind="tar",
                members=(("7zz", "7z"),),
                megabytes=2,
            ),
            Source(
                platform="windows-x86_64",
                url="https://www.7-zip.org/a/7z2603-x64.exe",
                sha256="0859c524b8a63551848f0c246abddcb1d0b7b656b0fbfe879f8d85e61a9e6edd",
                kind="exe-nsis",
                executable="7z.exe",
                megabytes=2,
            ),
        ),
    ),
    Pack(
        id="parquet",
        label="Parquet",
        summary="Reads and writes the columnar format used for analytics data.",
        unlocks=("Parquet",),
        megabytes=36,
        provides_modules=("pyarrow",),
        requirements="requirements-pack-parquet.txt",
        sources=(Source(platform="any", kind="pip", megabytes=36,
                        installed_megabytes=115),),
    ),
)

BY_ID: dict[str, Pack] = {p.id: p for p in PACKS}

# Reverse lookup: which pack would supply this engine.
_ENGINE_TO_PACK: dict[str, str] = {
    engine_id: pack.id for pack in PACKS for engine_id in pack.provides_engines
}


def _unavailable_reason(pack: Pack, source: "Source | None") -> str:
    """Why this pack cannot be installed here, or "" when it can."""
    if pack.installed:
        return ""
    if not _is_desktop_build():
        # The image is the unit of installation under Docker; downloading into a
        # container that will be replaced on the next `docker compose up` would
        # quietly lose the pack.
        return "Packs are part of the image in the Docker build."
    if source is None:
        return f"No automatic download is available for {platform_key()}."
    return ""


def bin_dirs() -> list:
    """Directories the engine should search for pack-provided binaries.

    Returned even when empty of a given binary: engines.py checks each in turn,
    so a pack that is half-installed cannot shadow a working system copy.
    """
    return [p.bin_dir for p in PACKS if p.bin_dir.is_dir()]


def site_dirs() -> list:
    """Directories holding pack-provided Python packages."""
    return [p.site_dir for p in PACKS if p.site_dir.is_dir()]


def activate_site_dirs() -> list[str]:
    """Put pack site directories on sys.path.

    Called once at startup, before converters are registered, because a route's
    availability is decided by whether its modules import — and they cannot
    import from a directory Python has never been told about.
    """
    import site
    import sys

    added: list[str] = []
    for directory in site_dirs():
        path = str(directory)
        if path not in sys.path:
            site.addsitedir(path)
            added.append(path)
    # addsitedir appends; a pack should win over nothing, but never over the
    # base install, so leaving the order alone is correct.
    importlib.invalidate_caches()
    return added


def pack_for_engine(engine_id: str) -> Pack | None:
    pack_id = _ENGINE_TO_PACK.get(engine_id)
    return BY_ID.get(pack_id) if pack_id else None


def missing_for_engines(engine_ids: tuple[str, ...] | list[str]) -> list[Pack]:
    """The packs a route would need, de-duplicated and in declaration order."""
    wanted: list[Pack] = []
    for engine_id in engine_ids:
        if engines.available(engine_id):
            continue
        pack = pack_for_engine(engine_id)
        if pack and pack not in wanted:
            wanted.append(pack)
    return wanted


def missing_for_modules(modules: tuple[str, ...] | list[str]) -> list[Pack]:
    wanted: list[Pack] = []
    for module in modules:
        if importlib.util.find_spec(module) is not None:
            continue
        for pack in PACKS:
            if module in pack.provides_modules and pack not in wanted:
                wanted.append(pack)
    return wanted


def status() -> list[dict]:
    return [p.to_dict() for p in PACKS]


def summary() -> dict:
    installed = [p for p in PACKS if p.installed]
    return {
        "packs": status(),
        "installedCount": len(installed),
        "totalCount": len(PACKS),
        # A build that ships everything has nothing to offer, and the interface
        # can hide the whole concept rather than showing six "installed" rows.
        "complete": len(installed) == len(PACKS),
        "platform": platform_key(),
        # Downloading is only meaningful where the app manages its own engines.
        # Under Docker the image is the unit of installation.
        "manageable": _is_desktop_build(),
    }


def _is_desktop_build() -> bool:
    """True when the engine runs as a bundled sidecar rather than in a container."""
    from . import config

    return config.BUILD_KIND == "desktop"
