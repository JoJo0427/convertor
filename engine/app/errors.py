"""Structured, explainable failures.

The product rule is that "something went wrong" is never an acceptable message,
so every failure carries a title, a plain-language detail and at least one thing
the user can actually do next.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ConversionError(Exception):
    code: str
    title: str
    detail: str
    suggestions: list[str] = field(default_factory=list)

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        return f"{self.code}: {self.title} - {self.detail}"

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "title": self.title,
            "detail": self.detail,
            "suggestions": self.suggestions,
        }


def engine_missing(engine: str, install_hint: str) -> ConversionError:
    return ConversionError(
        code="engine_missing",
        title=f"The {engine} engine is not installed",
        detail=(
            f"This conversion is handled by {engine}, which is not available in "
            "this container. Every other conversion still works."
        ),
        suggestions=[
            f"Rebuild the engine image, which installs {engine} automatically.",
            f"Or install it manually: {install_hint}",
        ],
    )


def password_protected(name: str) -> ConversionError:
    return ConversionError(
        code="password_protected",
        title="This file is password protected",
        detail=(
            f"{name} is encrypted, so its contents cannot be read without the "
            "password."
        ),
        suggestions=[
            "Open the file in its original application and save an unprotected copy.",
            "Then add the unprotected copy here.",
        ],
    )


def corrupt_file(name: str, detail: str = "") -> ConversionError:
    return ConversionError(
        code="corrupt_file",
        title="This file appears corrupted or incomplete",
        detail=(
            f"{name} could not be read to the end. "
            + (detail or "It may have been truncated during a download or copy.")
        ),
        suggestions=[
            "Try downloading or exporting the file again.",
            "Open it in its original application to confirm it still works.",
        ],
    )


def unsupported_route(source_label: str, target_label: str) -> ConversionError:
    return ConversionError(
        code="unsupported_route",
        title=f"{source_label} cannot be converted to {target_label}",
        detail=(
            "No conversion path exists between these two formats, directly or "
            "through an intermediate format."
        ),
        suggestions=[
            f"Browse what {source_label} can convert to in Format discovery.",
            "Or pick a different output format from the recommendations.",
        ],
    )


# Lines a tool emits on its way to doing the job correctly. LibreOffice prints
# the javaldx warning on every single invocation, and it is the *last* line of
# stderr — so quoting the tail of stderr reports a benign warning as the cause of
# every LibreOffice failure, and the real error, printed earlier, is thrown away.
# That masked a genuine large-file defect for an entire session.
_BENIGN = (
    "failed to launch javaldx",
    "javaldx",
    "fontconfig warning",
    "glib-gobject",
    "gtk-warning",
    "libpng warning",
    "deprecated pixel format",
    "warning: ",
)

# Lines that name an actual failure. Preferred over anything else, and searched
# from the end so the innermost error wins over the wrapper that re-raises it.
_DIAGNOSTIC = (
    "error",
    "exception",
    "cannot",
    "could not",
    "unable to",
    "no such file",
    "permission denied",
    "invalid",
    "unsupported",
    "not supported",
    "out of memory",
    "killed",
    "traceback",
    "failed to",
    "corrupt",
    "malformed",
)


def most_diagnostic_line(stderr: str) -> str:
    """The line from stderr most likely to explain the failure.

    Never just the last line: tools print warnings after errors, and the most
    common warning of all is the least informative one.
    """
    lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
    if not lines:
        return ""

    def benign(line: str) -> bool:
        low = line.lower()
        # "Warning: " is benign only when the line says nothing else useful;
        # "Warning: could not open X" is the actual cause.
        if any(marker in low for marker in _DIAGNOSTIC):
            return any(b in low for b in _BENIGN[:-1])
        return any(b in low for b in _BENIGN)

    useful = [ln for ln in lines if not benign(ln)]

    for line in reversed(useful):
        if any(marker in line.lower() for marker in _DIAGNOSTIC):
            return line
    if useful:
        return useful[-1]
    # Everything was a warning. Say so rather than quoting one as the cause.
    return ""


def tool_failed(engine: str, name: str, stderr: str) -> ConversionError:
    tail = most_diagnostic_line(stderr)[:300]

    if tail:
        detail = (
            f"{name} was recognised, but {engine} stopped while converting it."
            f' It reported: "{tail}"'
        )
        suggestions = [
            "Try a different output format, or a lower quality preset.",
            "If the file opens correctly elsewhere, re-export it and try again.",
        ]
    else:
        # A tool that dies without saying anything was almost always killed from
        # outside — memory limit, or the timeout. Saying "no reason given" is
        # more honest than quoting a warning, and points at the real cause.
        detail = (
            f"{name} was recognised, but {engine} stopped without reporting a "
            "reason. That usually means it ran out of memory or exceeded the "
            "time limit rather than that the file is wrong."
        )
        suggestions = [
            "Convert the file in smaller pieces — a page range, or one sheet.",
            "Try a different output format; some routes need far less memory.",
            "If the file is unusually large, the engine may need more memory "
            "than it currently has.",
        ]

    return ConversionError(
        code="engine_failed",
        title=f"{engine} could not process this file",
        detail=detail,
        suggestions=suggestions,
    )
