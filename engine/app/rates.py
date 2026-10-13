"""Currency rates, and the only other part of this app that uses the network.

Everything in the value converters is pure arithmetic done on this machine —
except this. Exchange rates have to come from somewhere, and that somewhere is
someone else's server. So the boundary is kept explicit and in one file: the
screen says plainly that this is a network feature, the rate carries the date it
was published, and a rate that could not be refreshed is served from cache with
its true age rather than silently presented as current.

Two sources, because a free service with no API key is a free service that can
disappear. If the first cannot be reached the second is tried; if neither can, a
cached table is used and the interface says how old it is.

Nothing is fetched on a schedule. A rate is looked up when the screen asks for
one, and then cached for the rest of the day — the sources publish daily, so
asking more often would be noise.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from . import config
from .errors import ConversionError

# Rates are published once a day. Holding them for twelve hours keeps a long
# session from re-fetching while still picking up the new table the same day.
_MAX_AGE_SECONDS = 12 * 60 * 60

_SOURCES = (
    # name, url template, how to read the payload
    ("exchangerate-api.com", "https://open.er-api.com/v6/latest/{base}", "er"),
    ("frankfurter.dev (ECB)", "https://api.frankfurter.dev/v1/latest?base={base}", "frankfurter"),
)

USER_AGENT = f"Convertor/{config.VERSION} (+local value converter)"


@dataclass
class RateTable:
    base: str
    rates: dict[str, float]
    published: str = ""
    source: str = ""
    fetched: float = field(default_factory=time.time)

    @property
    def age_seconds(self) -> float:
        return time.time() - self.fetched

    def to_dict(self) -> dict:
        return {
            "base": self.base,
            "rates": self.rates,
            "published": self.published,
            "source": self.source,
            "ageSeconds": round(self.age_seconds),
            "stale": self.age_seconds > _MAX_AGE_SECONDS,
            "currencies": sorted(self.rates),
        }


_cache: dict[str, RateTable] = {}


def _cache_path():
    return config.WORKSPACE_ROOT / "rates.json"


def _load_from_disk() -> None:
    """Survive a restart without a network call.

    A converter that cannot convert until it reaches the internet is worse than
    one that says "these rates are from Tuesday".
    """
    path = _cache_path()
    if not path.is_file():
        return
    try:
        stored = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return
    for base, entry in stored.items():
        try:
            _cache[base] = RateTable(
                base=base,
                rates={k: float(v) for k, v in entry["rates"].items()},
                published=entry.get("published", ""),
                source=entry.get("source", ""),
                fetched=float(entry.get("fetched", 0)),
            )
        except (KeyError, TypeError, ValueError):
            continue


def _save_to_disk() -> None:
    try:
        _cache_path().write_text(json.dumps({
            base: {
                "rates": table.rates,
                "published": table.published,
                "source": table.source,
                "fetched": table.fetched,
            }
            for base, table in _cache.items()
        }), encoding="utf-8")
    except OSError:
        pass  # a cache that cannot be written is a slow start, not a failure


def _parse(kind: str, payload: dict[str, Any], base: str) -> RateTable | None:
    if kind == "er" and payload.get("result") == "success":
        return RateTable(
            base=base,
            rates={k: float(v) for k, v in (payload.get("rates") or {}).items()},
            published=payload.get("time_last_update_utc", ""),
        )
    if kind == "frankfurter" and payload.get("rates"):
        return RateTable(
            base=base,
            rates={k: float(v) for k, v in payload["rates"].items()},
            published=payload.get("date", ""),
        )
    return None


def _fetch(base: str) -> RateTable | None:
    for name, template, kind in _SOURCES:
        request = urllib.request.Request(
            template.format(base=base), headers={"User-Agent": USER_AGENT}
        )
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                payload = json.load(response)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            continue
        table = _parse(kind, payload, base)
        if table and table.rates:
            table.source = name
            # The base itself is not in the published table, and a converter that
            # cannot convert USD to USD looks broken.
            table.rates.setdefault(base, 1.0)
            return table
    return None


def table_for(base: str, *, refresh: bool = False) -> RateTable:
    """Rates for one base currency, from cache when it is fresh enough."""
    base = (base or "USD").upper()
    if not _cache:
        _load_from_disk()

    cached = _cache.get(base)
    if cached and not refresh and cached.age_seconds <= _MAX_AGE_SECONDS:
        return cached

    fetched = _fetch(base)
    if fetched is not None:
        _cache[base] = fetched
        _save_to_disk()
        return fetched

    if cached is not None:
        # Out of date is not the same as wrong, and the caller is told the age.
        return cached

    raise ConversionError(
        code="rates_unavailable",
        title="Exchange rates could not be fetched",
        detail=f"Neither rate source could be reached for {base}, and nothing "
               "has been cached yet.",
        suggestions=[
            "Check your internet connection and try again.",
            "Every other converter on this screen works offline.",
        ],
    )


def convert(amount: float, source: str, target: str) -> dict:
    source, target = (source or "").upper(), (target or "").upper()
    table = table_for(source)
    if target not in table.rates:
        raise ConversionError(
            code="unknown_currency",
            title=f"{target or 'That currency'} is not in the rate table",
            detail=f"{table.source} publishes {len(table.rates)} currencies and "
                   f"{target!r} is not one of them.",
            suggestions=["Pick a currency from the list."],
        )
    rate = table.rates[target]
    return {
        "amount": amount,
        "from": source,
        "to": target,
        "rate": rate,
        "result": amount * rate,
        **{k: v for k, v in table.to_dict().items() if k != "rates"},
    }
