"""Load a Bach chorale into a simple event model, simplify its rhythm, and mark phrases.

The model is deliberately small: per voice, a list of bars, each with events
(offset, duration, concert pitch or None for a rest, fermata, tie). Everything
downstream (key search, octave fitting, part writing) works from this.
"""
from __future__ import annotations

import copy
import json
import random
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from music21 import chord, corpus, expressions, key, pitch

from .instruments import VOICE_NAMES, VOICES

DATA = Path(__file__).resolve().parent.parent / "data"
EPS = 1e-6


@dataclass
class Ev:
    offset: float                 # quarter lengths from start of bar
    dur: float
    pitch: pitch.Pitch | None     # concert pitch; None = rest
    fermata: bool = False
    tie: str | None = None        # music21 tie type: start / continue / stop
    phrase: int = 0

    @property
    def is_rest(self) -> bool:
        return self.pitch is None


@dataclass
class Bar:
    number: int
    padding: float                # pickup bars: quarter lengths missing at the start
    ts: str | None                # time signature if it is set/changed here
    events: list[Ev] = field(default_factory=list)

    @property
    def length(self) -> float:
        return sum(e.dur for e in self.events)


@dataclass
class Chorale:
    id: str
    bwv: str
    title: str
    key: key.Key
    voices: dict[str, list[Bar]]  # "S"/"A"/"T"/"B" -> bars

    @property
    def key_name(self) -> str:
        return key_name(self.key.tonic.name, self.key.mode)

    def phrases(self, v: str) -> list[tuple[int, int]]:
        """(first bar number, last bar number) for each phrase of voice v."""
        spans: dict[int, list[int]] = {}
        for b in self.voices[v]:
            for e in b.events:
                spans.setdefault(e.phrase, []).append(b.number)
        return [(min(ms), max(ms)) for _, ms in sorted(spans.items())]


def key_name(tonic: str, mode: str) -> str:
    return f"{tonic.replace('-', 'b')} {mode}"


def display_title(title: str, bwv: str) -> str:
    return title if f"BWV {bwv}" in title else f"{title} (BWV {bwv})"


def pitch_label(p: pitch.Pitch | str) -> str:
    """Bb4-style label for a pitch (music21 writes B-4)."""
    s = p.nameWithOctave if isinstance(p, pitch.Pitch) else p
    return s.replace("-", "b")


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def catalog() -> list[dict]:
    return json.loads((DATA / "chorales.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _catalog_index() -> dict[str, dict]:
    return {c["id"]: c for c in catalog()}


def find(query: str) -> dict | None:
    """Look a chorale up by id ('bwv66.6'), BWV number ('66.6') or exact title."""
    q = (query or "").strip().lower().removeprefix("bwv").strip()
    idx = _catalog_index()
    if ("bwv" + q) in idx:
        return idx["bwv" + q]
    for c in catalog():
        if c["title"].lower() == q or str(c["riemenschneider"]) == q:
            return c
    return None


def surprise(rng: random.Random | None = None) -> dict:
    return (rng or random).choice(catalog())


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
@lru_cache(maxsize=32)
def _parse(cid: str):
    return corpus.parse(f"bach/{cid}")


def load(cid: str) -> Chorale:
    meta = _catalog_index().get(cid)
    if meta is None:
        raise KeyError(f"Unknown chorale {cid!r}")
    score = _parse(cid)
    parts = list(score.parts)
    voices: dict[str, list[Bar]] = {}
    for v, part in zip(VOICES, parts):
        bars: list[Bar] = []
        last_ts = None
        for m in part.getElementsByClass("Measure"):
            ts = m.timeSignature.ratioString if m.timeSignature else None
            if ts == last_ts:
                ts = None
            elif ts:
                last_ts = ts
            bar = Bar(number=m.number, padding=float(m.paddingLeft or 0.0), ts=ts)
            end = 0.0
            for n in m.flatten().notesAndRests:
                ql = float(n.quarterLength)
                if ql <= 0 or n.duration.isGrace:
                    continue
                off = float(n.offset)
                if off < end - EPS:          # overlapping inner voice: ignore
                    continue
                if off > end + EPS:           # gap: fill with a rest
                    bar.events.append(Ev(end, off - end, None))
                if n.isRest:
                    p = None
                elif isinstance(n, chord.Chord):
                    ps = sorted(n.pitches, key=lambda x: x.ps)
                    p = copy.deepcopy(ps[-1] if v in "SA" else ps[0])
                else:
                    p = copy.deepcopy(n.pitch)
                fermata = any(isinstance(x, expressions.Fermata) for x in n.expressions)
                bar.events.append(Ev(off, ql, p, fermata, n.tie.type if n.tie else None))
                end = off + ql
            bars.append(bar)
        voices[v] = bars
    k = score.analyze("key")
    ch = Chorale(cid, meta["bwv"], meta["title"], k, voices)
    mark_phrases(ch)
    return ch


def _timeline(bars: list[Bar]):
    """Yield (absolute start, bar, event) for every event of one voice."""
    t = 0.0
    for b in bars:
        for e in b.events:
            yield t + e.offset, b, e
        t += b.length


def phrase_boundaries(ch: Chorale) -> list[float]:
    """Absolute times where a new phrase starts: just after each fermata.

    Bach marks phrase ends with fermatas. Some corpus files only carry them in the
    soprano, so the soprano's fermatas define the phrases for all four voices; other
    voices are used only if the soprano has none.
    """
    for vs in (["S"], ["A", "T", "B"]):
        ends = sorted({round(t + e.dur, 6) for v in vs for t, _, e in _timeline(ch.voices[v]) if e.fermata})
        total = max(sum(b.length for b in bars) for bars in ch.voices.values())
        ends = [x for x in ends if x < total - EPS]
        if ends:
            return ends
    return []


def mark_phrases(ch: Chorale) -> None:
    """Number the phrases of every voice, splitting at the fermata boundaries."""
    bounds = phrase_boundaries(ch)
    for bars in ch.voices.values():
        for t, _, e in _timeline(bars):
            e.phrase = sum(1 for x in bounds if t >= x - EPS)


# ---------------------------------------------------------------------------
# Rhythm simplification
# ---------------------------------------------------------------------------
def _fmt_beat(bar: Bar, off: float) -> str:
    pos = bar.padding + off
    beat = int(pos + EPS) + 1
    frac = pos - int(pos + EPS)
    if frac < EPS:
        return str(beat)
    if abs(frac - 0.5) < EPS:
        return f"{beat}&"
    return f"{beat}+{frac:g}"


def simplify(ch: Chorale, rule: dict | None) -> tuple[Chorale, list[dict]]:
    """Return a rhythm-simplified copy of the chorale plus a report of every change.

    rule = {"grid": g, "drop_bar_ties": bool}. Every note that starts off the grid is
    absorbed into the note before it (so the on-beat pitch is kept), and ties across
    barlines can be removed (the tied-over continuation becomes a rest). rule=None
    leaves the music as written.
    """
    out = copy.deepcopy(ch)
    report: list[dict] = []
    if not rule:
        return out, report
    grid = float(rule["grid"])
    drop_ties = bool(rule.get("drop_bar_ties"))
    unit = "eighth" if grid == 1.0 else "sixteenth"
    longer = "quarter" if grid == 1.0 else "eighth"

    for v, bars in out.voices.items():
        prev_last: Ev | None = None
        for b in bars:
            new: list[Ev] = []
            for i, e in enumerate(b.events):
                e = copy.copy(e)
                tied_in = e.tie in ("stop", "continue")
                # 1) tie that arrives from the previous bar
                if tied_in and i == 0 and drop_ties and not e.is_rest:
                    report.append({"voice": v, "measure": b.number, "beat": _fmt_beat(b, e.offset),
                                   "change": "tie across the barline removed ({p} held into this bar becomes a rest)",
                                   "pitches": {"p": e.pitch}})
                    e = Ev(e.offset, e.dur, None, e.fermata, None, e.phrase)
                # 2) tie inside the bar: just lengthen the previous note
                elif tied_in and new and not new[-1].is_rest and new[-1].pitch.ps == e.pitch.ps:
                    new[-1].dur += e.dur
                    new[-1].fermata |= e.fermata
                    new[-1].tie = None if drop_ties else _tie(new[-1], e)
                    continue
                on_grid = abs(((b.padding + e.offset) / grid) - round((b.padding + e.offset) / grid)) < EPS
                if on_grid or not new:
                    if drop_ties:
                        e.tie = None
                    new.append(e)
                    continue
                # 3) off-grid note: absorb into the previous (on-beat) note
                host = new[-1]
                if not e.is_rest or not host.is_rest:
                    report.append({"voice": v, "measure": b.number, "beat": _fmt_beat(b, e.offset),
                                   "change": f"{unit} {{x}} merged into {{h}} (keeps the on-beat {{h}})",
                                   "pitches": {"x": e.pitch, "h": host.pitch}, "kind": "merge"})
                host.dur += e.dur
                host.fermata |= e.fermata
                if not drop_ties and not host.is_rest:
                    host.tie = _tie(host, e)
            b.events = new
    _sanitize_ties(out)
    # collapse the per-merge lines into one line per beat
    report = _collapse(report, unit, longer)
    mark_phrases(out)
    return out, report


def _tie(host: Ev, absorbed: Ev) -> str | None:
    """Tie type for `host` after it swallowed `absorbed` (which followed it)."""
    incoming = host.tie in ("stop", "continue")
    outgoing = absorbed.tie in ("start", "continue")
    return {(False, False): None, (True, False): "stop", (False, True): "start", (True, True): "continue"}[(incoming, outgoing)]


def _sanitize_ties(ch: Chorale) -> None:
    """Keep only ties that join two consecutive notes of the same pitch."""
    for bars in ch.voices.values():
        evs = [e for b in bars for e in b.events]
        for i, e in enumerate(evs):
            if e.is_rest:
                e.tie = None
                continue
            nxt = evs[i + 1] if i + 1 < len(evs) else None
            prv = evs[i - 1] if i > 0 else None
            out_ok = e.tie in ("start", "continue") and nxt is not None and not nxt.is_rest \
                and nxt.pitch.ps == e.pitch.ps and nxt.tie in ("stop", "continue")
            in_ok = e.tie in ("stop", "continue") and prv is not None and not prv.is_rest \
                and prv.pitch.ps == e.pitch.ps and prv.tie in ("start", "continue")
            e.tie = {(False, False): None, (True, False): "stop", (False, True): "start", (True, True): "continue"}[(in_ok, out_ok)]


def _collapse(report: list[dict], unit: str, longer: str) -> list[dict]:
    merged: dict[tuple, list[dict]] = {}
    other: list[dict] = []
    for r in report:
        if r.get("kind") == "merge":
            beat = r["beat"].split("&")[0].split("+")[0]
            merged.setdefault((r["voice"], r["measure"], beat), []).append(r)
        else:
            other.append(r)
    for (v, m, beat), recs in merged.items():
        if len(recs) == 1:
            other.append({"voice": v, "measure": m, "beat": beat, "change": recs[0]["change"], "pitches": recs[0]["pitches"]})
        else:
            other.append({"voice": v, "measure": m, "beat": beat, "pitches": {"h": recs[0]["pitches"]["h"]},
                          "change": f"{len(recs)} off-beat {unit}s merged into the on-beat {{h}}"})
    order = {v: i for i, v in enumerate(VOICES)}
    other.sort(key=lambda r: (order[r["voice"]], r["measure"], str(r["beat"])))
    for r in other:
        r["voice_name"] = VOICE_NAMES[r["voice"]]
    return other
