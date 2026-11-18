"""Read a roster (.xlsx, .csv or pasted spreadsheet text) and count players per instrument.

Two layouts are understood:
  * one row per student, with an instrument column somewhere (header like "Instrument",
    "Inst" or "Section"; otherwise the column that looks most like instrument names);
  * instrument-and-count pairs ("Flute, 9").
Nothing is stored: everything happens in memory for the request.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from .instruments import ORDER, canonical

INSTRUMENT_HEADERS = ("instrument", "inst", "section", "instr")
COUNT_HEADERS = ("count", "qty", "quantity", "players", "how many", "number of players", "# of players",
                 "# players", "num players", "total")
COUNT_EXACT = ("#", "n", "num", "number", "no.")   # only when the whole header is just this


class RosterError(ValueError):
    """The roster could not be read at all (empty, unreadable, no instrument column)."""


@dataclass
class Roster:
    counts: dict[str, int] = field(default_factory=dict)          # standard name -> players
    unrecognized: dict[str, int] = field(default_factory=dict)    # raw name -> players
    layout: str = "per-student"                                   # or "counts"
    instrument_column: str = ""

    def ordered_counts(self) -> dict[str, int]:
        return {k: self.counts[k] for k in ORDER if self.counts.get(k)}

    def to_json(self) -> dict:
        return {
            "counts": self.ordered_counts(),
            "unrecognized": [{"name": k, "count": v} for k, v in self.unrecognized.items()],
            "layout": self.layout,
            "instrument_column": self.instrument_column,
            "total_players": sum(self.counts.values()),
        }


# ---------------------------------------------------------------------------
# Raw rows
# ---------------------------------------------------------------------------
def rows_from_xlsx(data: bytes) -> list[list[str]]:
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001 - openpyxl raises many types
        raise RosterError(f"Couldn't open that spreadsheet ({type(e).__name__}). Is it a real .xlsx file?") from e
    best: list[list[str]] = []
    for ws in wb.worksheets:  # use the first sheet that has anything recognisable on it
        rows = [["" if v is None else str(v).strip() for v in r] for r in ws.iter_rows(values_only=True)]
        rows = _clean(rows)
        if rows and not best:
            best = rows
        if rows and any(canonical(c) for r in rows for c in r):
            return rows
    return best


def rows_from_text(text: str) -> list[list[str]]:
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if not lines:
        return []
    sample = "\n".join(lines[:20])
    if "\t" in sample:
        delim = "\t"
    else:
        try:
            delim = csv.Sniffer().sniff(sample, delimiters=",;|").delimiter
        except csv.Error:
            delim = ","
    rows = list(csv.reader(lines, delimiter=delim))
    return _clean([[c.strip() for c in r] for r in rows])


def rows_from_upload(filename: str, data: bytes) -> list[list[str]]:
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")) or data[:2] == b"PK":
        return rows_from_xlsx(data)
    if name.endswith(".xls"):
        raise RosterError("Old .xls files aren't supported. Save as .xlsx or .csv, or paste the rows.")
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return rows_from_text(data.decode(enc))
        except UnicodeDecodeError:
            continue
    raise RosterError("Couldn't read that file as text.")


def _clean(rows: list[list[str]]) -> list[list[str]]:
    rows = [r for r in rows if any(c for c in r)]
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    keep = [j for j in range(width) if any(r[j] for r in rows)]   # drop empty columns
    return [[r[j] for j in keep] for r in rows]


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------
def _as_count(s: str) -> int | None:
    s = s.strip()
    if re.fullmatch(r"\d+(\.0+)?", s):
        return int(float(s))
    return None


def _header_match(cell: str, words: tuple[str, ...]) -> bool:
    c = cell.strip().lower()
    return any(c == w or re.search(rf"(^|\W){re.escape(w)}(\W|$)", c) for w in words)


def _looks_like_header(row: list[str]) -> bool:
    hints = INSTRUMENT_HEADERS + COUNT_HEADERS + ("name", "student", "first", "last", "grade", "id", "notes")
    return any(_header_match(c, hints) for c in row if c) and not any(canonical(c) for c in row if c)


def parse_rows(rows: list[list[str]]) -> Roster:
    if not rows:
        raise RosterError("The roster is empty. Upload a spreadsheet or paste rows with an instrument column.")

    header: list[str] | None = rows[0] if _looks_like_header(rows[0]) else None
    body = rows[1:] if header else rows
    if not body:
        raise RosterError("The roster only has a header row and no students.")
    ncol = len(rows[0])

    # Instrument column: a header that says so, else the column whose cells best match aliases.
    inst_col = None
    if header:
        for j, h in enumerate(header):
            if _header_match(h, INSTRUMENT_HEADERS):
                inst_col = j
                break
    if inst_col is None:
        scores = [sum(1 for r in body if canonical(r[j])) for j in range(ncol)]
        if max(scores, default=0) == 0:
            raise RosterError(
                "Couldn't find an instrument column. Add a header called \"Instrument\" "
                "or make sure one column lists instruments like Flute, Clarinet, Trumpet."
            )
        inst_col = scores.index(max(scores))

    # Count column: a header that says so, or (no header) a 2-column sheet whose other column is all numbers.
    count_col = None
    if header:
        for j, h in enumerate(header):
            if j != inst_col and (_header_match(h, COUNT_HEADERS) or h.strip().lower() in COUNT_EXACT):
                count_col = j
                break
    elif ncol == 2:
        other = 1 - inst_col
        if all(_as_count(r[other]) is not None for r in body if r[inst_col]):
            count_col = other

    roster = Roster(
        layout="counts" if count_col is not None else "per-student",
        instrument_column=(header[inst_col] if header else f"column {inst_col + 1}"),
    )
    for r in body:
        raw = r[inst_col].strip()
        if not raw:
            continue
        n = 1
        if count_col is not None:
            n = _as_count(r[count_col]) or 0
            if n <= 0:
                continue
        std = canonical(raw)
        if std:
            roster.counts[std] = roster.counts.get(std, 0) + n
        else:
            roster.unrecognized[raw] = roster.unrecognized.get(raw, 0) + n

    if not roster.counts and not roster.unrecognized:
        raise RosterError("No players found in the roster.")
    return roster


def parse_text(text: str) -> Roster:
    return parse_rows(rows_from_text(text))


def parse_upload(filename: str, data: bytes) -> Roster:
    if not data:
        raise RosterError("That file is empty.")
    return parse_rows(rows_from_upload(filename, data))
