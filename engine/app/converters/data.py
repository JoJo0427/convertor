"""Structured data conversion: spreadsheets, CSV, JSON, XML, YAML and friends."""

from __future__ import annotations

import asyncio
import json as jsonlib
from pathlib import Path
from typing import Any, Sequence

from .. import formats
from ..errors import ConversionError, corrupt_file
from ..registry import (
    MANY_TO_ONE,
    Choice,
    Converter,
    InputFile,
    Option,
    Preset,
    RunContext,
    register,
)
from .base import NAMING_OPTION, OVERWRITE_OPTION, safe_stem, target_path

TABULAR = ("csv", "tsv", "xlsx", "xls", "ods", "json", "jsonl", "xml", "yaml",
           "parquet", "md_table")
WRITABLE = ("csv", "tsv", "xlsx", "ods", "json", "jsonl", "xml", "yaml",
            "parquet", "md_table", "html")

_DELIMITERS = {"comma": ",", "semicolon": ";", "tab": "\t", "pipe": "|"}


def _flatten(record: Any, prefix: str = "", separator: str = ".") -> dict:
    """Flatten nested JSON so it can live in a table without losing information."""
    out: dict[str, Any] = {}
    if isinstance(record, dict):
        for key, value in record.items():
            path = f"{prefix}{separator}{key}" if prefix else str(key)
            out.update(_flatten(value, path, separator))
    elif isinstance(record, list):
        if all(not isinstance(v, (dict, list)) for v in record):
            out[prefix] = ", ".join("" if v is None else str(v) for v in record)
        else:
            for index, value in enumerate(record):
                out.update(_flatten(value, f"{prefix}[{index}]", separator))
    else:
        out[prefix or "value"] = record
    return out


def read_table(path: Path, format_id: str, *, nrows: int | None = None, **opts):
    """Read any supported tabular format into a DataFrame."""
    import pandas as pd

    if format_id == "csv":
        return pd.read_csv(path, nrows=nrows, sep=opts.get("sep"),
                           engine="python" if opts.get("sep") is None else "c",
                           encoding=opts.get("encoding", "utf-8"))
    if format_id == "tsv":
        return pd.read_csv(path, sep="\t", nrows=nrows,
                           encoding=opts.get("encoding", "utf-8"))
    if format_id in ("xlsx", "xls", "ods"):
        engine = {"xlsx": "openpyxl", "xls": "xlrd", "ods": "odf"}[format_id]
        sheet = opts.get("sheet") or 0
        return pd.read_excel(path, sheet_name=sheet, nrows=nrows, engine=engine)
    if format_id == "parquet":
        frame = pd.read_parquet(path)
        return frame.head(nrows) if nrows else frame
    if format_id == "jsonl":
        records = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                records.append(_flatten(jsonlib.loads(line)))
                if nrows and len(records) >= nrows:
                    break
        return pd.DataFrame(records)
    if format_id == "json":
        payload = jsonlib.loads(path.read_text("utf-8"))
        if isinstance(payload, dict):
            # A single object wrapping the real list is the common API shape.
            arrays = [v for v in payload.values() if isinstance(v, list)]
            payload = arrays[0] if len(arrays) == 1 else [payload]
        if not isinstance(payload, list):
            payload = [payload]
        rows = [_flatten(item) if isinstance(item, (dict, list)) else {"value": item}
                for item in payload]
        frame = pd.DataFrame(rows)
        return frame.head(nrows) if nrows else frame
    if format_id == "yaml":
        import yaml

        payload = yaml.safe_load(path.read_text("utf-8"))
        if isinstance(payload, dict):
            arrays = [v for v in payload.values() if isinstance(v, list)]
            payload = arrays[0] if len(arrays) == 1 else [payload]
        if not isinstance(payload, list):
            payload = [payload]
        frame = pd.DataFrame([_flatten(i) if isinstance(i, (dict, list)) else {"value": i}
                              for i in payload])
        return frame.head(nrows) if nrows else frame
    if format_id == "xml":
        try:
            frame = pd.read_xml(path)
        except Exception:
            import xml.etree.ElementTree as ET

            root = ET.parse(path).getroot()
            rows = []
            for child in root:
                row = dict(child.attrib)
                for leaf in child:
                    row[leaf.tag] = leaf.text
                if child.text and child.text.strip():
                    row.setdefault("text", child.text.strip())
                rows.append(row)
            frame = pd.DataFrame(rows)
        return frame.head(nrows) if nrows else frame
    if format_id in ("md_table", "md"):
        rows = []
        header: list[str] = []
        for line in path.read_text("utf-8").splitlines():
            if not line.strip().startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(set(c) <= set("-: ") and c for c in cells):
                continue
            if not header:
                header = cells
            else:
                rows.append(cells[: len(header)] + [""] * (len(header) - len(cells)))
        return pd.DataFrame(rows, columns=header or None)
    if format_id == "html":
        tables = pd.read_html(path)
        if not tables:
            raise ConversionError(
                code="no_table",
                title="No table was found in this page",
                detail="The HTML file does not contain a <table> element to extract.",
                suggestions=["Convert it to text or Markdown instead."],
            )
        return tables[0]
    raise ConversionError(
        code="unsupported_input",
        title=f"{formats.label(format_id)} cannot be read as a table",
        detail="This format is not one of the structured data formats.",
        suggestions=["Pick a different output, or convert to CSV first."],
    )


def _write_table(frame, path: Path, target: str, ctx: RunContext) -> None:
    import pandas as pd

    if target == "csv":
        sep = _DELIMITERS.get(str(ctx.option("delimiter", "comma")), ",")
        frame.to_csv(path, index=False, sep=sep,
                     encoding=str(ctx.option("encoding", "utf-8")))
    elif target == "tsv":
        frame.to_csv(path, index=False, sep="\t",
                     encoding=str(ctx.option("encoding", "utf-8")))
    elif target == "xlsx":
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            sheet = str(ctx.option("sheet_name", "Sheet1")) or "Sheet1"
            frame.to_excel(writer, index=False, sheet_name=sheet[:31])
            if ctx.option("autofit", True):
                worksheet = writer.sheets[sheet[:31]]
                for column_cells in worksheet.columns:
                    width = max(
                        (len(str(c.value)) for c in column_cells if c.value is not None),
                        default=8,
                    )
                    worksheet.column_dimensions[column_cells[0].column_letter].width = min(
                        max(10, width + 2), 60
                    )
    elif target == "ods":
        frame.to_excel(path, index=False, engine="odf")
    elif target == "parquet":
        frame.to_parquet(path, index=False)
    elif target == "json":
        orient = str(ctx.option("json_shape", "records"))
        indent = int(ctx.option("indent", 2))
        frame.to_json(path, orient=orient, indent=indent, date_format="iso",
                      force_ascii=False)
    elif target == "jsonl":
        with path.open("w", encoding="utf-8") as fh:
            for record in frame.to_dict(orient="records"):
                fh.write(jsonlib.dumps(record, ensure_ascii=False, default=str) + "\n")
    elif target == "yaml":
        import yaml

        payload = frame.to_dict(orient="records")
        path.write_text(
            yaml.safe_dump(jsonlib.loads(jsonlib.dumps(payload, default=str)),
                           sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    elif target == "xml":
        root = str(ctx.option("xml_root", "rows")) or "rows"
        row = str(ctx.option("xml_row", "row")) or "row"
        safe = frame.copy()
        safe.columns = [
            "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in str(c)).strip("_")
            or f"column_{i}"
            for i, c in enumerate(safe.columns)
        ]
        safe.to_xml(path, index=False, root_name=root, row_name=row)
    elif target == "md_table":
        path.write_text(frame.to_markdown(index=False) + "\n", encoding="utf-8")
    elif target == "html":
        table = frame.to_html(index=False, border=0, escape=True)
        path.write_text(
            "<!doctype html><meta charset=\"utf-8\">"
            "<style>body{font:14px system-ui;margin:2rem}"
            "table{border-collapse:collapse}"
            "th,td{border:1px solid #d4d4d8;padding:6px 10px;text-align:left}"
            "th{background:#f4f4f5}</style>" + table,
            encoding="utf-8",
        )
    else:  # pragma: no cover - registry prevents this
        raise ConversionError(
            code="unsupported_output",
            title=f"{formats.label(target)} cannot be written from table data",
            detail="This output format is not supported for structured data.",
            suggestions=["Try CSV, XLSX or JSON."],
        )


def _data_options(source: str, target: str) -> list[Option]:
    opts: list[Option] = []
    if source in ("csv", "tsv"):
        opts.append(Option(
            id="encoding", label="Text encoding", type="select", default="utf-8",
            help="Choose the encoding the source file was saved with. UTF-8 covers most cases.",
            choices=(Choice("utf-8", "UTF-8"), Choice("latin-1", "Latin-1 (ISO-8859-1)"),
                     Choice("cp1252", "Windows-1252"), Choice("utf-16", "UTF-16")),
        ))
    if source in ("xlsx", "xls", "ods"):
        opts.append(Option(
            id="sheet", label="Sheet", type="text", default="",
            help="Leave blank for the first sheet, or give the sheet name.",
            advanced=False,
        ))
    if target == "csv":
        opts.append(Option(
            id="delimiter", label="Separator", type="select", default="comma",
            help="The character between columns.",
            choices=(Choice("comma", "Comma  ,"), Choice("semicolon", "Semicolon  ;"),
                     Choice("tab", "Tab"), Choice("pipe", "Pipe  |")),
            advanced=False,
        ))
    if target == "xlsx":
        opts += [
            Option(id="sheet_name", label="Sheet name", type="text", default="Sheet1",
                   help="", advanced=False),
            Option(id="autofit", label="Fit column widths", type="boolean", default=True,
                   help="Size each column to its content so the sheet is readable immediately."),
        ]
    if target == "json":
        opts += [
            Option(id="json_shape", label="JSON shape", type="select", default="records",
                   help="How rows are arranged in the output.",
                   choices=(
                       Choice("records", "List of objects",
                              '[{"id": 1, "name": "A"}, ...] — the usual choice'),
                       Choice("columns", "Object of columns",
                              '{"id": {"0": 1}, "name": {"0": "A"}}'),
                       Choice("index", "Keyed by row index"),
                       Choice("values", "Arrays only", "[[1, \"A\"], ...]"),
                   ), advanced=False),
            Option(id="indent", label="Indentation", type="number", default=2,
                   help="Set to 0 for a single compact line.", minimum=0, maximum=8, step=1),
        ]
    if target == "xml":
        opts += [
            Option(id="xml_root", label="Root element name", type="text", default="rows", help=""),
            Option(id="xml_row", label="Row element name", type="text", default="row", help=""),
        ]
    opts += [NAMING_OPTION, OVERWRITE_OPTION]
    return opts


def _data_warnings(inputs: Sequence[InputFile], target: str, options: dict) -> list[dict]:
    notices: list[dict] = []
    sources = {i.format_id for i in inputs}

    if target in ("csv", "tsv") and sources & {"xlsx", "xls", "ods"}:
        notices.append({
            "level": "warning",
            "title": "Only one sheet is kept",
            "detail": f"{formats.label(target)} holds a single table. Formulas, "
                      "cell formatting and extra sheets are not carried over.",
        })
    if target in ("csv", "tsv", "xlsx", "ods", "parquet") and sources & {"json", "jsonl", "yaml", "xml"}:
        notices.append({
            "level": "warning",
            "title": "Nested values are flattened",
            "detail": "Nested objects become dotted column names such as "
                      "user.address.city, and simple lists are joined with commas.",
        })
    if target == "md_table":
        notices.append({
            "level": "info",
            "title": "Markdown tables suit small datasets",
            "detail": "Wide or long tables become hard to read in Markdown.",
        })
    if target == "csv":
        notices.append({
            "level": "info",
            "title": "CSV stores text only",
            "detail": "Dates and numbers are written as text; the receiving tool decides "
                      "how to interpret them.",
        })
    return notices


async def _convert_data(ctx: RunContext) -> list[Path]:
    outputs: list[Path] = []
    total = len(ctx.inputs)
    for index, item in enumerate(ctx.inputs, start=1):
        ctx.progress((index - 1) / total, f"Reading {item.name}")

        def work() -> Path:
            try:
                frame = read_table(
                    item.path, item.format_id,
                    sep=_DELIMITERS.get(str(ctx.option("source_delimiter", "")), None),
                    encoding=str(ctx.option("encoding", "utf-8")),
                    sheet=str(ctx.option("sheet", "")) or None,
                )
            except ConversionError:
                raise
            except UnicodeDecodeError:
                raise ConversionError(
                    code="encoding_mismatch",
                    title="The text encoding does not match",
                    detail=f"{item.name} is not valid "
                           f"{ctx.option('encoding', 'utf-8')} text.",
                    suggestions=[
                        "Set a different encoding in advanced settings — Windows "
                        "exports are often Windows-1252.",
                    ],
                )
            except Exception as exc:
                raise corrupt_file(item.name, str(exc)[:200])

            if frame.empty:
                ctx.warn(
                    "This file produced no rows",
                    f"{item.name} was read successfully but contained no data rows.",
                )
            out = target_path(ctx, item.name, ctx.target, index=index)
            _write_table(frame, out, ctx.target, ctx)
            ctx.note(
                "Converted table",
                f"{len(frame):,} rows and {len(frame.columns)} columns were written.",
            )
            return out

        outputs.append(await asyncio.to_thread(work))
        ctx.progress(index / total, f"Converted {item.name}")
    return outputs


async def _combine_data(ctx: RunContext) -> list[Path]:
    import pandas as pd

    frames = []
    total = len(ctx.inputs)
    strategy = str(ctx.option("combine_strategy", "stack"))

    def load() -> Any:
        for index, item in enumerate(ctx.inputs, start=1):
            ctx.progress((index - 1) / (total + 1), f"Reading {item.name}")
            frame = read_table(item.path, item.format_id)
            if ctx.option("add_source_column", True):
                frame.insert(0, "_source", item.name)
            frames.append(frame)
        if strategy == "stack":
            return pd.concat(frames, ignore_index=True, sort=False)
        key = str(ctx.option("join_key", "")).strip()
        if not key:
            raise ConversionError(
                code="missing_join_key",
                title="A column to join on is required",
                detail="Side-by-side merging needs a shared column name that appears "
                       "in every file.",
                suggestions=["Set the join column in advanced settings, or switch to "
                             "stacking rows."],
            )
        merged = frames[0]
        for frame in frames[1:]:
            if key not in frame.columns:
                raise ConversionError(
                    code="missing_join_key",
                    title=f'One file has no "{key}" column',
                    detail="Every file must contain the join column for a side-by-side merge.",
                    suggestions=[f'Rename the column to "{key}", or stack rows instead.'],
                )
            merged = merged.merge(frame, on=key, how="outer")
        return merged

    combined = await asyncio.to_thread(load)
    ctx.progress(0.9, "Writing combined dataset")
    stem = f"{safe_stem(ctx.inputs[0].name)}-combined"
    out = target_path(ctx, stem, ctx.target)
    await asyncio.to_thread(_write_table, combined, out, ctx.target, ctx)
    ctx.note(
        "Files combined",
        f"{total} files became {len(combined):,} rows across "
        f"{len(combined.columns)} columns.",
    )
    return [out]


def _combine_options(source: str, target: str) -> list[Option]:
    return [
        Option(id="combine_strategy", label="How to combine", type="select",
               default="stack",
               help="Stacking appends rows; merging matches rows on a shared column.",
               choices=(
                   Choice("stack", "Stack rows", "All rows from every file, one after another"),
                   Choice("merge", "Merge side by side", "Match rows using a shared column"),
               ), advanced=False),
        Option(id="join_key", label="Column to join on", type="text", default="",
               help="The column name shared by every file.",
               depends_on=("combine_strategy", ("merge",)), advanced=False),
        Option(id="add_source_column", label="Record which file each row came from",
               type="boolean", default=True,
               help="Adds a _source column holding the original file name."),
        *_data_options(source, target),
    ]


def _writable_targets() -> tuple[str, ...]:
    """Parquet needs pyarrow, which is a pack. Drop it when absent rather than
    offering a route that fails on import."""
    import importlib.util

    if importlib.util.find_spec("pyarrow") is None:
        return tuple(t for t in WRITABLE if t != "parquet")
    return WRITABLE


def _readable_sources() -> tuple[str, ...]:
    import importlib.util

    if importlib.util.find_spec("pyarrow") is None:
        return tuple(t for t in TABULAR if t != "parquet")
    return TABULAR


def register_all() -> None:
    register(Converter(
        id="data.table",
        label="Structured data converter",
        description="Convert between spreadsheets, CSV, JSON, XML, YAML and Markdown tables.",
        inputs=frozenset(_readable_sources() + ("html",)),
        outputs=frozenset(_writable_targets()),
        run=_convert_data,
        cardinality="one_to_one",
        quality=85,
        options_for=_data_options,
        warnings_for=_data_warnings,
        # HTML is read as a table only when the user supplied the HTML itself.
        chain_inputs=frozenset(TABULAR),
        presets_for=lambda s, t: [
            Preset("plain", "Plain data",
                   "Straight conversion with sensible defaults.", {}),
            Preset("readable", "Readable output",
                   "Formatted for a person to read: indented JSON, fitted spreadsheet columns.",
                   {"indent": 2, "autofit": True}),
            Preset("compact", "Compact",
                   "Smallest output, no pretty-printing.", {"indent": 0, "autofit": False}),
        ],
        # HTML is a document first; only claim it when a table is actually wanted.
        excluded_routes=frozenset({("html", "pdf"), ("html", "docx"), ("html", "txt")}),
    ))

    register(Converter(
        id="data.combine",
        label="Combine datasets",
        description="Fold several data files into one table by stacking or merging.",
        inputs=frozenset(_readable_sources()),
        outputs=frozenset(_writable_targets()),
        run=_combine_data,
        cardinality=MANY_TO_ONE,
        quality=88,
        options_for=_combine_options,
        warnings_for=lambda inputs, target, options: [
            *_data_warnings(inputs, target, options),
            {
                "level": "info" if options.get("combine_strategy", "stack") == "stack" else "warning",
                "title": "Columns are matched by name",
                "detail": "Files with different column names produce blank cells where a "
                          "column is missing.",
            },
        ],
    ))
