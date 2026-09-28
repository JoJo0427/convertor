"""Converter registration.

Adding a capability is one module plus one line here. Nothing else in the engine
or the interface needs to know it exists.
"""

from __future__ import annotations

_LOADED = False


def load(force: bool = False) -> None:
    """Declare every converter.

    `force` re-declares them from scratch, which is what installing or removing
    a pack needs: a converter whose format set was decided by what was importable
    at declaration time has to be asked again.
    """
    global _LOADED
    if _LOADED and not force:
        return
    if force:
        from .. import registry

        # Only the converter registry is cleared. `formats.register` is keyed by
        # id and `setdefault`s extensions, so re-registering formats is already
        # idempotent — and FFmpeg's discovered set does not change because a pack
        # arrived.
        registry.reset()

    # HEIC support is a Pillow plugin; register it before anything reads images.
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:  # pragma: no cover - optional dependency
        pass

    from . import archives, data, documents, images, intelligent, media, ocr, text

    # FFmpeg's discovered containers join the catalog before anything registers,
    # so converters can claim them as inputs and outputs.
    media.register_discovered_formats()

    for module in (images, documents, data, media, archives, ocr, text, intelligent):
        module.register_all()

    _LOADED = True
