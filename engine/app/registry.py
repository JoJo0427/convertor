"""The converter registry, route planner and recommendation engine.

This is the extensibility seam of the product. A new capability is a module that
builds a Converter and calls `register()`; detection, the output picker, the
advanced settings panel, presets and the format browser all pick it up without
any further wiring.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Sequence

from . import engines, formats, packs
from .errors import unsupported_route

# How a converter consumes its inputs.
ONE_TO_ONE = "one_to_one"      # each input produces one output
MANY_TO_ONE = "many_to_one"    # all inputs are combined into one output
ONE_TO_MANY = "one_to_many"    # one input explodes into several outputs


@dataclass(frozen=True)
class Choice:
    value: str
    label: str
    help: str = ""


@dataclass(frozen=True)
class Option:
    """One advanced setting, described well enough for the UI to render it."""

    id: str
    label: str
    type: str  # select | number | boolean | text
    default: Any
    help: str = ""
    choices: tuple[Choice, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    unit: str = ""
    advanced: bool = True
    # Only show this option when another option holds one of these values.
    depends_on: tuple[str, tuple[str, ...]] | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "type": self.type,
            "default": self.default,
            "help": self.help,
            "choices": [c.__dict__ for c in self.choices],
            "min": self.minimum,
            "max": self.maximum,
            "step": self.step,
            "unit": self.unit,
            "advanced": self.advanced,
            "dependsOn": (
                {"option": self.depends_on[0], "values": list(self.depends_on[1])}
                if self.depends_on else None
            ),
        }


@dataclass(frozen=True)
class Preset:
    id: str
    label: str
    description: str
    values: dict[str, Any]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "values": self.values,
        }


@dataclass
class InputFile:
    """One resolved input, as handed to a converter."""

    id: str
    name: str
    path: Path
    format_id: str
    metadata: dict = field(default_factory=dict)


@dataclass
class RunContext:
    inputs: list[InputFile]
    target: str
    options: dict[str, Any]
    workdir: Path
    outdir: Path
    # Called with (fraction 0..1, human message).
    progress: Callable[[float, str], None]
    # Converters append plain-language warnings here as they discover them.
    warnings: list[dict] = field(default_factory=list)

    def warn(self, title: str, detail: str) -> None:
        self.warnings.append({"level": "warning", "title": title, "detail": detail})

    def note(self, title: str, detail: str) -> None:
        self.warnings.append({"level": "info", "title": title, "detail": detail})

    def option(self, key: str, fallback: Any = None) -> Any:
        value = self.options.get(key, fallback)
        return fallback if value is None else value


@dataclass
class Converter:
    id: str
    label: str
    description: str
    inputs: frozenset[str]
    outputs: frozenset[str]
    run: Callable[[RunContext], Awaitable[list[Path]]]
    engines: tuple[str, ...] = ()
    # Importable modules this converter needs. Some capabilities ship as Python
    # packages rather than binaries, and must gate the route just the same.
    modules: tuple[str, ...] = ()
    # A runtime check for capabilities that neither a binary nor an import can
    # express. Returns "" when the converter can run, or a short reason why not.
    #
    # This exists because `modules` was not enough and the gap shipped: WeasyPrint
    # imports cleanly and then fails inside cffi when its GTK libraries are absent,
    # so the route advertised itself, ran, and died with a dynamic-linker message
    # quoted at the user. Anything whose real dependency lives outside Python's
    # import system belongs here.
    requires: Callable[[], str] | None = None
    cardinality: str = ONE_TO_ONE
    # Higher wins when several converters claim the same route.
    quality: int = 50
    options_for: Callable[[str, str], list[Option]] | None = None
    presets_for: Callable[[str, str], list[Preset]] | None = None
    warnings_for: Callable[[Sequence[InputFile], str, dict], list[dict]] | None = None
    # Routes this converter can technically serve but should never be chosen for.
    excluded_routes: frozenset[tuple[str, str]] = frozenset()
    # Describes, without running anything, what this converter would do —
    # including the literal engine command where there is one. Powers the expert
    # view, so what is shown is what actually executes.
    plan_for: Callable[[Sequence[InputFile], str, dict], list[dict]] | None = None
    # Inputs accepted only when this converter is the FIRST step of a route.
    # A reader that is a sensible last resort for a real file can still be
    # nonsense to chain into: "read the first <table> out of this HTML" is right
    # for an HTML file the user supplied, and useless for HTML that some earlier
    # step happened to generate. Anything not listed here is refused mid-chain.
    chain_inputs: frozenset[str] | None = None

    def handles(self, source: str, target: str) -> bool:
        if (source, target) in self.excluded_routes:
            return False
        return source in self.inputs and target in self.outputs

    @property
    def available(self) -> bool:
        import importlib.util

        if not all(engines.available(e) for e in self.engines):
            return False
        if not all(importlib.util.find_spec(m) is not None for m in self.modules):
            return False
        return not (self.requires and self.requires())

    @property
    def missing_engines(self) -> list[str]:
        import importlib.util

        names = [
            engines.ENGINES[e].label for e in self.engines if not engines.available(e)
        ]
        names += [m for m in self.modules if importlib.util.find_spec(m) is None]
        if self.requires:
            reason = self.requires()
            if reason:
                names.append(reason)
        return names


_REGISTRY: list[Converter] = []


def register(converter: Converter) -> Converter:
    _REGISTRY.append(converter)
    return converter


def reset() -> None:
    """Empty the registry so converters can be declared again.

    Needed because some converters decide their format sets at declaration time
    rather than on each query — `data.table` only lists Parquet if pyarrow is
    importable. Installing that pack therefore changes nothing until the
    converters are declared a second time, and a 36 MB download that appears to
    do nothing is worse than no download button at all.
    """
    _REGISTRY.clear()


def all_converters() -> list[Converter]:
    return list(_REGISTRY)


def get(converter_id: str) -> Converter | None:
    return next((c for c in _REGISTRY if c.id == converter_id), None)


# --------------------------------------------------------------------- routing


@dataclass
class Step:
    converter: Converter
    source: str
    target: str


@dataclass
class Route:
    steps: list[Step]

    @property
    def target(self) -> str:
        return self.steps[-1].target

    @property
    def source(self) -> str:
        return self.steps[0].source

    @property
    def direct(self) -> bool:
        return len(self.steps) == 1

    @property
    def available(self) -> bool:
        return all(s.converter.available for s in self.steps)

    @property
    def missing_engines(self) -> list[str]:
        seen: list[str] = []
        for step in self.steps:
            for name in step.converter.missing_engines:
                if name not in seen:
                    seen.append(name)
        return seen

    @property
    def cardinality(self) -> str:
        for step in self.steps:
            if step.converter.cardinality != ONE_TO_ONE:
                return step.converter.cardinality
        return ONE_TO_ONE

    def describe(self) -> str:
        names = [formats.label(self.steps[0].source)]
        names += [formats.label(s.target) for s in self.steps]
        return " → ".join(names)

    def to_dict(self) -> dict:
        return {
            "steps": [
                {
                    "converterId": s.converter.id,
                    "converter": s.converter.label,
                    "from": s.source,
                    "to": s.target,
                }
                for s in self.steps
            ],
            "direct": self.direct,
            "available": self.available,
            "missingEngines": self.missing_engines,
            "cardinality": self.cardinality,
            "description": self.describe(),
            "missingPacks": [
                {"id": p.id, "label": p.label, "megabytes": p.megabytes}
                for p in _missing_packs_for(self)
            ],
        }


def _missing_packs_for(route: "Route") -> list:
    from . import packs as _packs

    found = _packs.missing_for_engines(
        [e for step in route.steps for e in step.converter.engines]
    )
    for pack in _packs.missing_for_modules(
        [m for step in route.steps for m in step.converter.modules]
    ):
        if pack not in found:
            found.append(pack)
    return found


def _candidates(source: str, target: str, cardinality: str | None = None) -> list[Converter]:
    found = [c for c in _REGISTRY if c.handles(source, target)]
    if cardinality is not None:
        found = [c for c in found if c.cardinality == cardinality]
    # Engines that are actually present first; then, when no cardinality was
    # demanded, a plain per-file converter beats a combining one — asking to turn
    # one CSV into XLSX must not silently run the "combine datasets" path.
    found.sort(key=lambda c: (
        not c.available,
        0 if (cardinality is not None or c.cardinality == ONE_TO_ONE) else 1,
        -c.quality,
    ))
    return found


def find_route(
    source: str,
    target: str,
    *,
    cardinality: str | None = None,
    max_hops: int = 3,
) -> Route | None:
    """Find the best path from one format to another, via intermediates if needed."""
    if source == target and cardinality in (None, ONE_TO_ONE):
        passthrough = _candidates(source, target, cardinality)
        if passthrough:
            return Route([Step(passthrough[0], source, target)])

    direct = _candidates(source, target, cardinality)
    if direct:
        return Route([Step(direct[0], source, target)])

    # Multi-input and exploding converters are never chained; the intermediate
    # semantics ("which of the many outputs feeds the next step?") are not
    # meaningful, and a surprising chain is worse than an honest refusal.
    if cardinality not in (None, ONE_TO_ONE):
        return None

    queue: deque[tuple[str, list[Step]]] = deque([(source, [])])
    seen = {source}
    best: Route | None = None
    while queue:
        current, path = queue.popleft()
        if len(path) >= max_hops:
            continue
        for conv in _REGISTRY:
            if conv.cardinality != ONE_TO_ONE:
                continue
            accepted = (
                conv.inputs if not path
                else (conv.chain_inputs if conv.chain_inputs is not None else conv.inputs)
            )
            if current not in accepted:
                continue
            for nxt in conv.outputs:
                if nxt in seen or (current, nxt) in conv.excluded_routes:
                    continue
                step = Step(conv, current, nxt)
                if nxt == target:
                    route = Route([*path, step])
                    if best is None or len(route.steps) < len(best.steps):
                        best = route
                    continue
                seen.add(nxt)
                queue.append((nxt, [*path, step]))
        if best is not None:
            break
    return best


def require_route(source: str, target: str, **kwargs) -> Route:
    route = find_route(source, target, **kwargs)
    if route is None:
        raise unsupported_route(formats.label(source), formats.label(target))
    return route


def reachable_from(source: str, *, max_hops: int = 2) -> dict[str, Route]:
    """Every format reachable from `source`, mapped to its best route."""
    out: dict[str, Route] = {}
    for fmt in formats.all_formats():
        if fmt.id == source:
            continue
        route = find_route(source, fmt.id, max_hops=max_hops)
        if route is not None:
            out[fmt.id] = route
    return out


def sources_for(target: str, *, max_hops: int = 2) -> list[str]:
    return [
        fmt.id
        for fmt in formats.all_formats()
        if fmt.id != target and find_route(fmt.id, target, max_hops=max_hops)
    ]


def combine_targets(sources: Sequence[str]) -> dict[str, Route]:
    """Formats that all the given inputs can be combined into as one output."""
    out: dict[str, Route] = {}
    for conv in _REGISTRY:
        if conv.cardinality != MANY_TO_ONE:
            continue
        if not all(s in conv.inputs for s in sources):
            continue
        for target in conv.outputs:
            existing = out.get(target)
            if existing is None or conv.quality > existing.steps[0].converter.quality:
                out[target] = Route([Step(conv, sources[0], target)])
    return out


def split_targets(sources: Sequence[str]) -> dict[str, Route]:
    """Formats each input can be broken apart into as several outputs."""
    out: dict[str, Route] = {}
    for conv in _REGISTRY:
        if conv.cardinality != ONE_TO_MANY:
            continue
        if not all(s in conv.inputs for s in sources):
            continue
        for target in conv.outputs:
            existing = out.get(target)
            if existing is None or conv.quality > existing.steps[0].converter.quality:
                out[target] = Route([Step(conv, sources[0], target)])
    return out


def resolve_route(sources: Sequence[str], target: str, mode: str) -> Route | None:
    """The route for a whole input set, not just its first file.

    In combined mode the converter has to accept *every* input format — picking
    by the first file alone produces a route that then fails partway through on
    a file it was never able to read.
    """
    unique = list(dict.fromkeys(sources))
    if not unique:
        return None
    if mode == "combined":
        return combine_targets(unique).get(target)
    if mode == "split":
        return split_targets(unique).get(target)
    return find_route(unique[0], target)


def cardinality_for_mode(mode: str) -> str | None:
    """Map the workspace's three modes onto converter shapes."""
    if mode == "combined":
        return MANY_TO_ONE
    if mode == "split":
        return ONE_TO_MANY
    return None


# ------------------------------------------------------------- recommendations

# Curated ordering: what a person most often wants from each kind of input.
_PRIORITY: dict[str, tuple[str, ...]] = {
    formats.DOCUMENT: ("pdf", "docx", "md", "txt", "html", "png", "epub"),
    formats.TEXT: ("pdf", "docx", "html", "md", "txt"),
    formats.IMAGE: ("png", "jpeg", "webp", "pdf", "txt", "tiff", "avif"),
    formats.DATA: ("csv", "xlsx", "json", "md_table", "xml", "yaml"),
    formats.AUDIO: ("mp3", "wav", "flac", "aac", "opus"),
    formats.VIDEO: ("mp4", "webm", "mp3", "gif", "mkv"),
    formats.ARCHIVE: ("zip", "targz", "sevenzip"),
    formats.SUBTITLE: ("srt", "vtt", "txt"),
}


# What a person is trying to achieve, mapped to the formats that achieve it.
# Ordered best-first; anything unreachable from the current input is dropped.
INTENTS: tuple[dict, ...] = (
    {
        "id": "smaller",
        "label": "Make it smaller",
        "detail": "Trade some quality for a much smaller file",
        "targets": ("webp", "jpeg", "mp4", "mp3", "opus", "zip", "sevenzip",
                    "avif", "targz", "parquet"),
    },
    {
        "id": "editable",
        "label": "Make it editable",
        "detail": "Something you can open and change",
        "targets": ("docx", "xlsx", "md", "odt", "csv", "txt"),
    },
    {
        "id": "share",
        "label": "Send it to someone",
        "detail": "Opens anywhere, no special software",
        "targets": ("pdf", "jpeg", "mp4", "mp3", "zip", "csv", "html"),
    },
    {
        "id": "web",
        "label": "Put it on a website",
        "detail": "Small and fast to load in a browser",
        "targets": ("webp", "avif", "webm", "mp4", "html", "json", "svg"),
    },
    {
        "id": "quality",
        "label": "Keep maximum quality",
        "detail": "Largest files, nothing thrown away",
        "targets": ("png", "tiff", "flac", "wav", "pdf", "mkv"),
    },
    {
        "id": "text",
        "label": "Get the text out",
        "detail": "Just the words, for searching or reuse",
        "targets": ("txt", "md"),
    },
    {
        "id": "audio",
        "label": "Keep only the sound",
        "detail": "Discard the picture",
        "targets": ("mp3", "aac", "opus", "flac", "wav"),
    },
    {
        "id": "print",
        "label": "Print it",
        "detail": "Fixed layout at print resolution",
        "targets": ("pdf", "tiff", "png"),
    },
)


def intents_for(sources: Sequence[str], mode: str = "each") -> list[dict]:
    """The goals that are actually achievable from this input set."""
    if not sources:
        return []
    reachable = {r.format_id: r for r in recommend(sources, mode=mode)}
    out: list[dict] = []
    for intent in INTENTS:
        options = [
            {
                "formatId": target,
                "label": formats.label(target),
                "reason": reachable[target].reason,
                "direct": reachable[target].route.direct,
            }
            for target in intent["targets"] if target in reachable
        ]
        if options:
            out.append({
                "id": intent["id"],
                "label": intent["label"],
                "detail": intent["detail"],
                "best": options[0]["formatId"],
                "options": options[:5],
            })
    return out


@dataclass
class Recommendation:
    format_id: str
    route: Route
    score: float
    reason: str
    featured: bool

    def to_dict(self) -> dict:
        fmt = formats.get(self.format_id)
        return {
            "formatId": self.format_id,
            "label": fmt.label if fmt else self.format_id,
            "category": fmt.category if fmt else "",
            "summary": fmt.summary if fmt else "",
            "reason": self.reason,
            "featured": self.featured,
            "score": round(self.score, 3),
            "route": self.route.to_dict(),
        }


def recommend(
    source_formats: Sequence[str],
    *,
    mode: str = "each",
) -> list[Recommendation]:
    """Rank the output formats worth offering for this set of inputs.

    `mode` is "each" (convert every input separately) or "combined" (fold all the
    inputs into a single output).
    """
    if not source_formats:
        return []

    unique = list(dict.fromkeys(source_formats))

    if mode in ("combined", "split"):
        routes = (
            combine_targets(unique) if mode == "combined" else split_targets(unique)
        )
        results: list[Recommendation] = []
        for target, route in routes.items():
            fmt = formats.get(target)
            label = fmt.label if fmt else target
            results.append(Recommendation(
                format_id=target,
                route=route,
                score=(100.0 if fmt and fmt.multi else 90.0)
                if mode == "combined" else float(route.steps[0].converter.quality),
                reason=(
                    f"Combine {len(source_formats)} files into one {label}"
                    if mode == "combined"
                    else f"Break each file into separate {label} files"
                ),
                featured=True,
            ))
        results.sort(key=lambda r: -r.score)
        return results

    # Score every candidate target by how well it serves *all* the inputs.
    per_source: list[dict[str, Route]] = [reachable_from(s) for s in unique]
    shared: set[str] = set(per_source[0])
    for table in per_source[1:]:
        shared &= set(table)

    results = []
    for target in shared:
        fmt = formats.get(target)
        if fmt is None:
            continue
        routes = [table[target] for table in per_source]
        hops = max(len(r.steps) for r in routes)
        available = all(r.available for r in routes)

        score = 50.0
        reason = f"Convert to {fmt.label}"
        featured = False
        for source in unique:
            src_fmt = formats.get(source)
            priority = _PRIORITY.get(src_fmt.category if src_fmt else "", ())
            if target in priority:
                rank = priority.index(target)
                score += 40 - rank * 4
                featured = featured or rank < 5
        score -= (hops - 1) * 18
        if not available:
            score -= 30
        if fmt.category == (formats.get(unique[0]).category if formats.get(unique[0]) else None):
            score += 4
        if hops > 1:
            reason = routes[0].describe()

        results.append(Recommendation(
            format_id=target,
            route=routes[0],
            score=score,
            reason=reason,
            featured=featured and available,
        ))

    results.sort(key=lambda r: (-r.score, r.format_id))
    return results


# ---------------------------------------------------------- options & warnings


def options_for(route: Route, source: str) -> list[Option]:
    """Merged option list for a route, de-duplicated by option id."""
    merged: dict[str, Option] = {}
    current = source
    for step in route.steps:
        if step.converter.options_for:
            for opt in step.converter.options_for(current, step.target):
                merged.setdefault(opt.id, opt)
        current = step.target
    return list(merged.values())


def presets_for(route: Route, source: str) -> list[Preset]:
    """Presets come from the final step, which decides the output's character."""
    last = route.steps[-1]
    start = route.steps[-1].source if len(route.steps) > 1 else source
    if last.converter.presets_for:
        return last.converter.presets_for(start, last.target)
    return []


def plan_for(
    route: Route,
    inputs: Sequence[InputFile],
    options: dict,
    source: str,
) -> list[dict]:
    """A step-by-step account of what this route will do."""
    steps: list[dict] = []
    current = source
    for position, step in enumerate(route.steps, start=1):
        entry = {
            "step": position,
            "converter": step.converter.label,
            "from": formats.label(current),
            "to": formats.label(step.target),
            "engine": (
                engines.ENGINES[step.converter.engines[0]].label
                if step.converter.engines else "Built in"
            ),
            "detail": step.converter.description,
            "command": "",
        }
        if step.converter.plan_for:
            try:
                for extra in step.converter.plan_for(inputs, step.target, options):
                    entry.update(extra)
            except Exception:
                pass  # a preview must never break the page
        steps.append(entry)
        current = step.target
    return steps


def warnings_for(
    route: Route,
    inputs: Sequence[InputFile],
    options: dict,
) -> list[dict]:
    """Everything the user should know before committing to this conversion."""
    notices: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(level: str, title: str, detail: str) -> None:
        key = (title, detail)
        if key not in seen:
            seen.add(key)
            notices.append({"level": level, "title": title, "detail": detail})

    for step in route.steps:
        if step.converter.warnings_for:
            for item in step.converter.warnings_for(inputs, step.target, options):
                add(item.get("level", "warning"), item["title"], item["detail"])

    if not route.direct:
        add(
            "info",
            "This conversion runs in more than one step",
            f"There is no direct path, so the engine goes {route.describe()}. "
            "Each step can shift formatting slightly.",
        )

    if not route.available:
        needed = _missing_packs_for(route)
        if needed:
            names = " and ".join(f"{p.label} ({p.megabytes} MB)" for p in needed)
            add(
                "error",
                f"Needs the {needed[0].label} pack",
                f"This conversion is supported, but the {names} pack is not "
                "installed yet. Install it and the conversion becomes available.",
            )
        else:
            add(
                "error",
                "A required engine is not available",
                "This conversion needs "
                + ", ".join(route.missing_engines)
                + ", which this build does not include.",
            )

    target_fmt = formats.get(route.target)
    if target_fmt and target_fmt.notes:
        add("info", f"About {target_fmt.label}", " ".join(target_fmt.notes))

    return notices
