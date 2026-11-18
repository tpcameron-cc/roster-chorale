"""Parts for players who don't take a chorale line: timpani and unpitched percussion.

Bach wrote no percussion, so these parts frame the phrases the way a band or orchestra
arranger would, and stay out of the way otherwise:

* Timpani: two drums tuned to the tonic and dominant of the concert key. They double the
  bass line only at cadences (the last two bass notes of each phrase, when they are the
  tonic or dominant), plus the opening note. From grade 2 they also take tonic/dominant
  downbeats, and fermatas are rolled.
* Percussion: bass drum on the first downbeat of each phrase, suspended cymbal on every
  fermata (a soft roll from grade 2; a single let-ring stroke below that).
"""
from __future__ import annotations

from dataclasses import dataclass

from music21 import bar as m21bar
from music21 import expressions, key, meter, metadata, note, pitch, stream

from . import chorale as chmod
from .chorale import EPS, Chorale, pitch_label
from .instruments import Inst


@dataclass
class Hit:
    offset: float
    dur: float
    sound: object | None      # music21 Pitch (timpani), "bd"/"cym" (percussion), or None for a rest
    fermata: bool = False
    roll: bool = False


def _grade_num(grade: str) -> float:
    return float(grade)


def _on_downbeat(b: chmod.Bar, e: chmod.Ev) -> bool:
    return abs(b.padding + e.offset) < EPS


# ---------------------------------------------------------------------------
# Timpani
# ---------------------------------------------------------------------------
def timpani_tuning(tonic: str, lo: int, hi: int) -> tuple[pitch.Pitch, pitch.Pitch]:
    """Tonic and dominant placed on two drums inside the timpani range, as close together as possible."""
    dom = pitch.Pitch(tonic).transpose("P5").name

    def options(name: str) -> list[pitch.Pitch]:
        return [p for p in (pitch.Pitch(f"{name}{o}") for o in range(1, 5)) if lo <= p.ps <= hi]

    pairs = [(t, d) for t in options(tonic) for d in options(dom)]
    if not pairs:                      # range too narrow: just take the nearest octaves
        t = min((pitch.Pitch(f"{tonic}{o}") for o in range(1, 5)), key=lambda p: abs(p.ps - (lo + hi) / 2))
        d = min((pitch.Pitch(f"{dom}{o}") for o in range(1, 5)), key=lambda p: abs(p.ps - t.ps))
        return t, d
    return min(pairs, key=lambda td: (abs(td[0].ps - td[1].ps), td[0].ps))


def timpani_hits(ch: Chorale, key_iv, tonic: str, grade: str, lo: int, hi: int):
    t_p, d_p = timpani_tuning(tonic, lo, hi)
    by_pc = {int(t_p.ps) % 12: t_p, int(d_p.ps) % 12: d_p}
    rolls = _grade_num(grade) >= 2
    bars = ch.voices["B"]
    flat = [(bi, ei, e) for bi, b in enumerate(bars) for ei, e in enumerate(b.events)]
    notes = [(bi, ei, e) for bi, ei, e in flat if not e.is_rest]
    play: set[tuple[int, int]] = set()
    if notes:
        play.add(notes[0][:2])                       # the opening note
    by_phrase: dict[int, list] = {}
    for item in notes:
        by_phrase.setdefault(item[2].phrase, []).append(item)
    for items in by_phrase.values():
        for item in items[-2:]:                      # the cadence
            play.add(item[:2])
    if _grade_num(grade) >= 2:
        for bi, ei, e in notes:
            if _on_downbeat(bars[bi], e):
                play.add((bi, ei))
    out: list[list[Hit]] = []
    n_hits = 0
    for bi, b in enumerate(bars):
        row: list[Hit] = []
        for ei, e in enumerate(b.events):
            pc = None if e.is_rest else int(e.pitch.transpose(key_iv).ps) % 12
            if (bi, ei) in play and pc in by_pc:
                n_hits += 1
                if e.fermata or e.dur <= 1 + EPS:
                    row.append(Hit(e.offset, e.dur, by_pc[pc], e.fermata, roll=rolls and e.fermata))
                else:                                # a long note: one stroke, then let it ring as a rest
                    row.append(Hit(e.offset, 1.0, by_pc[pc]))
                    row.append(Hit(e.offset + 1.0, e.dur - 1.0, None))
            else:
                row.append(Hit(e.offset, e.dur, None, e.fermata))
        out.append(row)
    text = (f"Tuned to {pitch_label(t_p)} and {pitch_label(d_p)} (tonic and dominant). Plays {n_hits} notes: "
            f"the opening, the cadence at the end of each phrase"
            + (", tonic/dominant downbeats, and rolls on fermatas." if rolls else ", one stroke per fermata (no rolls at this grade)."))
    return out, text, (t_p, d_p)


# ---------------------------------------------------------------------------
# Unpitched percussion
# ---------------------------------------------------------------------------
def percussion_hits(ch: Chorale, grade: str):
    rolls = _grade_num(grade) >= 2
    bars = ch.voices["S"]
    seen_phrase: set[int] = set()
    out: list[list[Hit]] = []
    n_bd = n_cym = 0
    for b in bars:
        row: list[Hit] = []
        for e in b.events:
            if e.fermata:
                n_cym += 1
                row.append(Hit(e.offset, e.dur, "cym", True, roll=rolls))
                seen_phrase.add(e.phrase)
            elif e.phrase not in seen_phrase and _on_downbeat(b, e):
                seen_phrase.add(e.phrase)
                n_bd += 1
                row.append(Hit(e.offset, min(e.dur, 1.0), "bd"))
                if e.dur > 1 + EPS:
                    row.append(Hit(e.offset + 1.0, e.dur - 1.0, None))
            else:
                row.append(Hit(e.offset, e.dur, None))
        out.append(row)
    text = (f"Bass drum on the first downbeat of each phrase ({n_bd} notes); suspended cymbal "
            + ("soft roll (yarn mallets)" if rolls else "single soft stroke, let ring")
            + f" on every fermata ({n_cym}).")
    return out, text


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------
def _fermata() -> expressions.Fermata:
    f = expressions.Fermata()
    f.type = "upright"
    return f


def _merge(row: list[Hit]) -> list[Hit]:
    out: list[Hit] = []
    for h in row:
        if h.sound is None and out and out[-1].sound is None and not out[-1].fermata:
            out[-1] = Hit(out[-1].offset, out[-1].dur + h.dur, None, h.fermata)
        else:
            out.append(h)
    return out


def write_part(ch: Chorale, template_voice: str, rows: list[list[Hit]], inst: Inst, kr_name: str,
               grade_label: str, concert_key: key.Key | None) -> stream.Score:
    part = stream.Part()
    part.insert(0, inst.m21())
    part.partName = inst.part_name
    bars = ch.voices[template_voice]
    for idx, (b, row) in enumerate(zip(bars, rows)):
        m = stream.Measure(number=b.number)
        if idx == 0:
            m.insert(0, inst.m21_clef())
            if concert_key is not None:
                m.insert(0, key.KeySignature(0))      # timpani parts traditionally carry no key signature
        if b.ts:
            m.insert(0, meter.TimeSignature(b.ts))
        row = _merge(row)
        if len(row) == 1 and row[0].sound is None and idx not in (0, len(bars) - 1):
            r = note.Rest(quarterLength=row[0].dur)
            r.fullMeasure = True
            if row[0].fermata:
                r.expressions.append(_fermata())
            m.append(r)
            part.append(m)
            continue
        for h in row:
            if h.sound is None:
                n = note.Rest(quarterLength=h.dur)
            elif isinstance(h.sound, pitch.Pitch):
                n = note.Note(h.sound, quarterLength=h.dur)
            else:
                n = note.Unpitched(quarterLength=h.dur)
                if h.sound == "bd":
                    n.displayStep, n.displayOctave = "F", 4
                else:
                    n.displayStep, n.displayOctave = "A", 5
                    n.notehead = "x"
            if h.roll and h.sound is not None:
                t = expressions.Tremolo()
                t.numberOfMarks = 3
                n.expressions.append(t)
            if h.fermata:
                n.expressions.append(_fermata())
            m.append(n)
        if idx == 0 and b.padding:
            m.paddingLeft = b.padding
        if idx == len(bars) - 1:
            m.rightBarline = m21bar.Barline("final")
        part.append(m)

    sc = stream.Score()
    sc.metadata = metadata.Metadata()
    sc.metadata.title = chmod.display_title(ch.title, ch.bwv)
    sc.metadata.composer = "J. S. Bach"
    sc.metadata.movementName = f"{inst.part_name} · {grade_label} · Concert {kr_name}"
    sc.insert(0, part)
    return sc
