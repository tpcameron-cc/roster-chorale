"""Key search, voice assignment, octave fitting and part writing.

How it decides, in order of importance (see COST weights):
  1. notes that are still out of range after all octave fitting (should be zero),
  2. notes that only fit thanks to a phrase-level octave shift, and how many shifts,
  3. each instrument playing a voice it is suited to (its preference order),
  4. no voice left thin (balance), then the smallest move from Bach's key.

Octave fitting is per part first (the whole line up or down), then per phrase
(fermata to fermata). It never moves single notes.
"""
from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from functools import lru_cache

from music21 import bar as m21bar
from music21 import expressions, interval, key, metadata, meter, note, pitch, stream, tie
from music21.musicxml.m21ToXml import GeneralObjectExporter

from . import chorale as chmod
from .chorale import DATA, Chorale, key_name, pitch_label
from .instruments import INSTRUMENTS, ORDER, VOICE_NAMES, VOICES, Inst

COST_RESIDUAL = 100     # per note still out of range
COST_TACET = 15         # a part rests for a phrase it can't play in any octave (another part covers it)
COST_PRE = 2            # per note that needed a phrase shift
COST_SHIFT = 4          # per phrase shift
COST_PREF = (0, 3, 6)   # voice preference rank; anything else costs COST_UNLISTED
COST_UNLISTED = 20
COST_BELOW_BASS = 500   # an upper voice octave-displaced so it sounds below the bass line (wrong harmony)
BELOW_BASS_MARGIN = 5   # semitones an upper voice's average must stay above the bass line's average
COST_NO_REAL_BASS = 80  # nobody plays the bass line at pitch or lower (so the harmony is inverted)
COST_TWO_OCT = 2        # whole part moved two octaves
COST_THIN = 4           # per player a voice is short of BALANCE_SHARE of the band
BALANCE_SHARE = 0.15
COST_KEY_MOVE = 0.25    # per semitone away from Bach's key (tie-break)
OCTAVE_TRIES = (0, -1, 1, -2, 2)


class ArrangeError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Grade table
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def grades() -> dict:
    return json.loads((DATA / "grades.json").read_text(encoding="utf-8"))["grades"]


def grade_cfg(grade: str | float) -> dict:
    g = str(grade)
    if g.endswith(".0"):
        g = g[:-2]
    try:
        return grades()[g]
    except KeyError:
        raise ArrangeError(f"Unknown grade {grade!r}; choose one of {', '.join(grades())}") from None


def ranges_midi(cfg: dict) -> dict[str, tuple[int, int]]:
    return {k: (int(pitch.Pitch(lo).ps), int(pitch.Pitch(hi).ps)) for k, (lo, hi) in cfg["ranges"].items()}


# ---------------------------------------------------------------------------
# Keys
# ---------------------------------------------------------------------------
def key_interval(orig_tonic: str, target_tonic: str) -> interval.Interval:
    """Spelled interval from orig to target tonic, moving at most a tritone (-6..+5 semitones)."""
    p0 = pitch.Pitch(orig_tonic + "4")
    for octv in (3, 4, 5):
        p1 = pitch.Pitch(target_tonic + str(octv))
        if -6 <= p1.ps - p0.ps <= 5:
            return interval.Interval(pitchStart=p0, pitchEnd=p1)
    raise AssertionError("unreachable")


# ---------------------------------------------------------------------------
# Fitting one instrument to one voice in one key
# ---------------------------------------------------------------------------
@dataclass
class NoteRef:
    measure: int
    beat: str
    midi: int          # concert midi in the chorale's ORIGINAL key
    phrase: int
    label: str         # concert pitch name in the original key


@dataclass
class Fit:
    octave: int                          # whole-part shift in octaves
    phrase_shifts: dict[int, int]        # phrase -> extra octaves on top of `octave`
    pre_out: int                         # out of range after the whole-part shift only
    pre_above: int = 0
    pre_below: int = 0
    # notes still out of range after phrase shifts, by phrase: (note, written midi, "above"/"below")
    bad: dict[int, list[tuple[NoteRef, int, str]]] = field(default_factory=dict)
    tacet: set[int] = field(default_factory=set)   # phrases this part rests (decided with the whole band)

    def shift_for(self, phrase: int) -> int:
        return self.octave + self.phrase_shifts.get(phrase, 0)

    @property
    def residual(self) -> int:
        """Notes printed out of range (phrases this part rests don't count)."""
        return sum(len(ns) for ph, ns in self.bad.items() if ph not in self.tacet)

    @property
    def problems(self) -> list[tuple[NoteRef, int, str]]:
        return [x for ph, ns in sorted(self.bad.items()) if ph not in self.tacet for x in ns]

    @property
    def cost(self) -> float:
        """Cost of the octave plan itself (out-of-range phrases are priced with the whole band)."""
        return (COST_PRE * self.pre_out + COST_SHIFT * len(self.phrase_shifts)
                + (COST_TWO_OCT if abs(self.octave) == 2 else 0))


def voice_notes(ch: Chorale, v: str) -> list[NoteRef]:
    refs = []
    for b in ch.voices[v]:
        for e in b.events:
            if not e.is_rest:
                refs.append(NoteRef(b.number, chmod._fmt_beat(b, e.offset), int(e.pitch.ps), e.phrase, pitch_label(e.pitch)))
    return refs


def fit(notes: list[NoteRef], base: int, lo: int, hi: int) -> Fit:
    """Best octave plan for one line. `base` = key transposition + instrument written offset (semitones).

    1. shift the whole part by the octave that leaves the fewest notes out of range;
    2. then, phrase by phrase, shift any phrase that still has notes out of range by
       the octave that fixes the most of them. Never single notes.
    """
    def out(n: NoteRef, sh: int) -> int:
        w = n.midi + base + 12 * sh
        return 0 if lo <= w <= hi else (1 if w > hi else -1)

    centre = (lo + hi) / 2
    best = None
    for o in OCTAVE_TRIES:
        cnt = sum(1 for n in notes if out(n, o))
        mean = sum(n.midi for n in notes) / max(len(notes), 1) + base + 12 * o
        score = (cnt, abs(o), abs(mean - centre))
        if best is None or score < best[0]:
            best = (score, o)
    o = best[1]
    pre = [n for n in notes if out(n, o)]
    pre_above = sum(1 for n in pre if out(n, o) > 0)

    phrase_shifts: dict[int, int] = {}
    for ph in sorted({n.phrase for n in pre}):
        ph_notes = [n for n in notes if n.phrase == ph]
        best_q, best_cnt = 0, sum(1 for n in ph_notes if out(n, o))
        for q in (-1, 1, -2, 2):
            c = sum(1 for n in ph_notes if out(n, o + q))
            if c < best_cnt:
                best_q, best_cnt = q, c
        if best_q:
            phrase_shifts[ph] = best_q

    bad: dict[int, list] = {}
    for n in notes:
        sh = o + phrase_shifts.get(n.phrase, 0)
        d = out(n, sh)
        if d:
            bad.setdefault(n.phrase, []).append((n, n.midi + base + 12 * sh, "above" if d > 0 else "below"))
    return Fit(o, phrase_shifts, len(pre), pre_above, len(pre) - pre_above, bad)


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------
def pref_cost(inst: Inst, v: str) -> int:
    return COST_PREF[inst.prefs.index(v)] if v in inst.prefs else COST_UNLISTED


def balance_cost(assign: dict[str, str], counts: dict[str, int]) -> float:
    total = sum(counts.values())
    per = {v: 0 for v in VOICES}
    for i, v in assign.items():
        per[v] += counts[i]
    target = BALANCE_SHARE * total
    return COST_THIN * sum(max(0.0, target - per[v]) for v in VOICES if per[v] > 0)


def required_voices(n_types: int) -> list[str]:
    return {1: ["S"], 2: ["S", "B"], 3: ["S", "B"]}.get(n_types, list(VOICES))


def phrase_plan(assign: dict[str, str], fits: dict[tuple[str, str], Fit],
                n_phrases: int) -> tuple[float, dict[str, set[int]], int]:
    """Decide which parts rest in phrases they can't play in any octave.

    A part rests for a phrase only when another part on the same voice plays that
    phrase in range, so the line is never lost, and it rests for at most half of its
    phrases (nobody gets a nearly empty part). Otherwise the notes stay and are
    reported as out of range. Returns (cost, tacet phrases per instrument, notes left out of range).
    """
    by_voice: dict[str, list[str]] = {}
    for i, v in assign.items():
        by_voice.setdefault(v, []).append(i)
    max_rests = n_phrases // 2
    cost = 0.0
    residual = 0
    tacet: dict[str, set[int]] = {i: set() for i in assign}
    for v, insts in by_voice.items():
        if not any(fits[i, v].bad for i in insts):
            continue
        for i in insts:
            bad = fits[i, v].bad
            if not bad:
                continue
            # phrases someone else on this voice plays cleanly, worst first
            coverable = sorted((ph for ph in bad if any(ph not in fits[j, v].bad for j in insts if j != i)),
                               key=lambda ph: -len(bad[ph]))
            rests = set(coverable[:max_rests])
            tacet[i] = rests
            cost += COST_TACET * len(rests)
            n = sum(len(ns) for ph, ns in bad.items() if ph not in rests)
            cost += COST_RESIDUAL * n
            residual += n
    return cost, tacet, residual


def assign_voices(cost: dict[tuple[str, str], float], fits: dict[tuple[str, str], Fit],
                  counts: dict[str, int], n_phrases: int,
                  real_bass: dict[str, bool] | None = None) -> tuple[dict[str, str], float]:
    """Exact search: choose distinct instruments to cover the required voices, everyone
    else takes their cheapest voice; then a hill-climb that also weighs balance."""
    insts = list(counts)
    n = len(insts)
    req = required_voices(n)
    real_bass = real_bass or {i: True for i in insts}
    # optimistic per-pair cost (assumes out-of-range phrases can rest) to seed the search
    seed = {(i, v): cost[i, v] + COST_TACET * len(fits[i, v].bad) for i in insts for v in VOICES}
    cheapest = {i: min(VOICES, key=lambda v: (seed[i, v], VOICES.index(v))) for i in insts}

    def bass_penalty(a: dict[str, str]) -> float:
        if "B" not in a.values():
            return 0.0
        return 0.0 if any(real_bass[i] for i, v in a.items() if v == "B") else COST_NO_REAL_BASS

    def total_of(a: dict[str, str], balance: bool = True) -> float:
        t = sum(cost[i, v] for i, v in a.items()) + bass_penalty(a) + phrase_plan(a, fits, n_phrases)[0]
        return t + (balance_cost(a, counts) if balance else 0.0)

    best_assign, best_total = None, float("inf")
    cover_sets = [req] if n != 3 else [["S", "B", "A"], ["S", "B", "T"]]
    for voices in cover_sets:
        for chosen in itertools.permutations(insts, len(voices)):
            assign = {i: cheapest[i] for i in insts}
            assign.update(dict(zip(chosen, voices)))
            if n <= 3 and len(set(assign.values())) < len(assign):
                continue    # with few instrument types, spread them over distinct voices
            quick = sum(seed[i, v] for i, v in assign.items())
            if quick >= best_total:   # a lower bound that can't win: skip the full evaluation
                continue
            total = total_of(assign, balance=False)
            if total < best_total:
                best_assign, best_total = assign, total
    assert best_assign is not None

    def covered_ok(a: dict[str, str]) -> bool:
        used = set(a.values())
        if not all(v in used for v in req):
            return False
        return n > 3 or len(used) == len(a)

    cur, cur_total = dict(best_assign), total_of(best_assign)
    improved = True
    while improved:
        improved = False
        for i in insts:
            for v in VOICES:
                if v == cur[i]:
                    continue
                trial = dict(cur)
                trial[i] = v
                if not covered_ok(trial):
                    continue
                t = total_of(trial)
                if t < cur_total - 1e-9:
                    cur, cur_total, improved = trial, t, True
    return cur, cur_total


# ---------------------------------------------------------------------------
# Key search
# ---------------------------------------------------------------------------
@dataclass
class KeyResult:
    tonic: str               # music21 spelling, e.g. "B-"
    mode: str
    semitones: int
    assign: dict[str, str]
    fits: dict[str, Fit]     # per instrument, for its assigned voice (tacet filled in)
    total: float

    @property
    def name(self) -> str:
        return key_name(self.tonic, self.mode)

    @property
    def residual(self) -> int:
        return sum(f.residual for f in self.fits.values())

    @property
    def pre_out(self) -> int:
        return sum(f.pre_out for f in self.fits.values())

    @property
    def n_shifts(self) -> int:
        return sum(len(f.phrase_shifts) for f in self.fits.values())

    @property
    def n_tacet(self) -> int:
        return sum(len(f.tacet) for f in self.fits.values())


def search_keys(ch: Chorale, counts: dict[str, int], cfg: dict) -> list[KeyResult]:
    mode = ch.key.mode if ch.key.mode in ("major", "minor") else "major"
    allowed = [pitch.Pitch(t).name for t in cfg["keys"][mode]]
    rng = ranges_midi(cfg)
    notes = {v: voice_notes(ch, v) for v in VOICES}
    n_phrases = max((n.phrase for v in VOICES for n in notes[v]), default=0) + 1
    orig = ch.key.tonic.name
    mean = {v: sum(n.midi for n in notes[v]) / max(len(notes[v]), 1) for v in VOICES}
    results = []
    for spelled in allowed:
        semis = key_interval(orig, spelled).semitones
        fits: dict[tuple[str, str], Fit] = {}
        cost: dict[tuple[str, str], float] = {}
        for name in counts:
            inst = INSTRUMENTS[name]
            lo, hi = rng[name]
            for v in VOICES:
                f = fit(notes[v], semis + inst.written_semitones, lo, hi)
                fits[name, v] = f
                cost[name, v] = f.cost + pref_cost(inst, v)
                if v != "B" and mean[v] + 12 * f.octave < mean["B"] + BELOW_BASS_MARGIN:
                    cost[name, v] += COST_BELOW_BASS
        real_bass = {name: fits[name, "B"].octave <= 0 for name in counts}
        assign, total = assign_voices(cost, fits, counts, n_phrases, real_bass)
        _, tacet, _ = phrase_plan(assign, fits, n_phrases)
        chosen = {}
        for i, v in assign.items():
            f = fits[i, v]
            chosen[i] = Fit(f.octave, f.phrase_shifts, f.pre_out, f.pre_above, f.pre_below, f.bad, tacet[i])
        total += COST_KEY_MOVE * abs(semis)
        results.append(KeyResult(spelled, mode, semis, assign, chosen, total))
    results.sort(key=lambda r: (r.residual, r.total))
    return results


# ---------------------------------------------------------------------------
# Plain-English explanation
# ---------------------------------------------------------------------------
def _worst(kr: KeyResult, cfg: dict) -> str:
    """e.g. '7 Trumpet notes above C5' for the instrument with the most notes out of range."""
    if kr.pre_out == 0:
        return ""
    name, f = max(kr.fits.items(), key=lambda kv: kv[1].pre_out)
    lo, hi = cfg["ranges"][name]
    if f.pre_above >= f.pre_below:
        return f"{f.pre_above} {name} note{'s' if f.pre_above != 1 else ''} above {pitch_label(hi)}"
    return f"{f.pre_below} {name} note{'s' if f.pre_below != 1 else ''} below {pitch_label(lo)}"


def _residual_text(kr: KeyResult, cfg: dict) -> str:
    """e.g. '1 Trombone note (written D4) stays above its C4 limit whatever octave it's in'."""
    parts = []
    for name, f in kr.fits.items():
        for d in ("above", "below"):
            probs = [x for x in f.problems if x[2] == d]
            if not probs:
                continue
            limit = cfg["ranges"][name][1] if d == "above" else cfg["ranges"][name][0]
            written = ", ".join(pitch_label(pitch.Pitch(w)) for w in sorted({w for _, w, _ in probs}))
            parts.append(f"{_plural(len(probs), name + ' note')} (written {written}) "
                         f"{'stay' if len(probs) != 1 else 'stays'} {d} the {pitch_label(limit)} limit in every octave")
    return "; ".join(parts) or f"{_plural(kr.residual, 'note')} stay out of range"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _fit_phrase(kr: KeyResult) -> str:
    if kr.residual:
        return f"{_plural(kr.residual, 'note')} still out of range"
    if kr.pre_out == 0:
        return "0 notes out of range"
    fixes = []
    if kr.n_shifts:
        fixes.append(_plural(kr.n_shifts, "phrase-level octave shift"))
    if kr.n_tacet:
        fixes.append(_plural(kr.n_tacet, "phrase rest") + " (another part covers the line)")
    return f"{_plural(kr.pre_out, 'note')} out of range, all fixed by {' and '.join(fixes)}"


def explain(results: list[KeyResult], ch: Chorale, cfg: dict) -> tuple[str, str]:
    best = results[0]
    short = f"Concert {best.name}: {_fit_phrase(best)}."
    long = (f"Bach wrote this in {ch.key_name}. Of the keys allowed at {cfg['label'].lower()}, "
            f"concert {best.name} fits your band best: {_fit_phrase(best)}.")
    if len(results) > 1:
        ru = results[1]
        worst = _worst(ru, cfg)
        if ru.residual > best.residual:
            why = _residual_text(ru, cfg)
        elif ru.pre_out > best.pre_out and worst:
            why = f"it puts {worst} (written) before any octave fixes"
        elif ru.n_shifts + ru.n_tacet > best.n_shifts + best.n_tacet:
            why = (f"it needs {ru.n_shifts + ru.n_tacet} phrase shifts and rests "
                   f"instead of {best.n_shifts + best.n_tacet}")
        elif abs(ru.semitones) > abs(best.semitones) and ru.total - best.total <= COST_KEY_MOVE * 12:
            why = f"it's a bigger move from Bach's {ch.key_name}"
        else:
            moved = [f"{i} on {VOICE_NAMES[v].lower()}" for i, v in ru.assign.items()
                     if best.assign.get(i) != v]
            why = ("its best voicing puts " + ", ".join(moved[:3]) + ", which suits them less") if moved                 else "its parts sit less comfortably in range"
        short += f" Runner-up {ru.name}: {worst}." if worst and ru.pre_out > best.pre_out             else f" Runner-up {ru.name} lost: {why}."
        long += f" The runner-up, concert {ru.name}, lost because {why}."
    return short, long


# ---------------------------------------------------------------------------
# Writing parts
# ---------------------------------------------------------------------------
def build_part(ch: Chorale, v: str, inst: Inst, kr: KeyResult, f: Fit, grade_label: str) -> stream.Score:
    key_iv = key_interval(ch.key.tonic.name, kr.tonic)
    w_iv = inst.written_interval
    concert_key = key.Key(kr.tonic, kr.mode)
    written_key = concert_key.transpose(w_iv) if w_iv.semitones else concert_key
    ks = key.KeySignature(written_key.sharps)

    part = stream.Part()
    m21inst = inst.m21()
    part.insert(0, m21inst)
    part.partName = inst.part_name
    part.partAbbreviation = inst.name
    bars = ch.voices[v]
    for idx, b in enumerate(bars):
        m = stream.Measure(number=b.number)
        if idx == 0:
            m.insert(0, inst.m21_clef())
            m.insert(0, ks)
        if b.ts:
            m.insert(0, meter.TimeSignature(b.ts))
        events = _merge_rests(b, f)
        if len(events) == 1 and (events[0].is_rest or events[0].phrase in f.tacet) and idx not in (0, len(bars) - 1):
            r = note.Rest(quarterLength=events[0].dur)
            r.fullMeasure = True
            if events[0].fermata:
                r.expressions.append(_fermata())
            m.append(r)
            part.append(m)
            continue
        for e in events:
            if e.is_rest or e.phrase in f.tacet:
                n = note.Rest(quarterLength=e.dur)
            else:
                p = e.pitch.transpose(key_iv)
                if w_iv.semitones:
                    p = p.transpose(w_iv)
                sh = f.shift_for(e.phrase)
                if sh:
                    p = p.transpose(interval.Interval(12 * sh))
                n = note.Note(p, quarterLength=e.dur)
                if e.tie and not (f.tacet & {e.phrase - 1, e.phrase + 1}):
                    n.tie = tie.Tie(e.tie)
            if e.fermata:
                n.expressions.append(_fermata())
            m.append(n)
        if idx == 0 and b.padding:
            m.paddingLeft = b.padding
        if idx == len(bars) - 1:
            m.rightBarline = m21bar.Barline("final")
        part.append(m)
    part.atSoundingPitch = False

    sc = stream.Score()
    sc.metadata = metadata.Metadata()
    sc.metadata.title = chmod.display_title(ch.title, ch.bwv)
    sc.metadata.composer = "J. S. Bach"
    sc.metadata.movementName = f"{inst.part_name} · {grade_label} · Concert {kr.name}"
    sc.insert(0, part)
    sc.atSoundingPitch = False
    return sc


def _fermata() -> expressions.Fermata:
    f = expressions.Fermata()
    f.type = "upright"   # above the staff, as players expect
    return f


def _merge_rests(b: chmod.Bar, f: Fit) -> list[chmod.Ev]:
    """Join consecutive rests in a bar (including phrases this part sits out) so a resting
    player reads one rest, not a string of quarter rests."""
    out: list[chmod.Ev] = []
    for e in b.events:
        silent = e.is_rest or e.phrase in f.tacet
        if silent and out and (out[-1].is_rest) and not out[-1].fermata:
            out[-1] = chmod.Ev(out[-1].offset, out[-1].dur + e.dur, None, e.fermata, None, e.phrase)
        elif silent:
            out.append(chmod.Ev(e.offset, e.dur, None, e.fermata, None, e.phrase))
        else:
            out.append(e)
    return out


def to_musicxml(sc: stream.Score) -> str:
    return GeneralObjectExporter(sc).parse().decode("utf-8")


def _cover(others: list[str], kr: KeyResult, ph: int) -> str:
    who = [i for i in others if ph not in kr.fits[i].tacet]
    return f"{', '.join(who)} cover{'s' if len(who) == 1 else ''}"


def safe_filename(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "-" for c in s).strip("-")


# ---------------------------------------------------------------------------
# The whole job
# ---------------------------------------------------------------------------
def arrange(chorale_id: str, counts: dict[str, int], grade: str | float, with_parts: bool = True) -> dict:
    cfg = grade_cfg(grade)
    unknown = [k for k in counts if k not in INSTRUMENTS]
    if unknown:
        raise ArrangeError(f"Unknown instruments: {', '.join(unknown)}")
    counts = {k: int(v) for k, v in counts.items() if int(v) > 0}
    if not counts:
        raise ArrangeError("The roster has no recognised instruments yet. Classify the unrecognised names first.")
    meta = chmod.find(chorale_id)
    if meta is None:
        raise ArrangeError(f"No chorale matches {chorale_id!r}.")
    counts = {k: counts[k] for k in ORDER if k in counts}

    original = chmod.load(meta["id"])
    ch, rhythm = chmod.simplify(original, cfg["rhythm"])
    results = search_keys(ch, counts, cfg)
    best = results[0]
    short, long = explain(results, ch, cfg)

    warnings: list[str] = []
    voices_used = set(best.assign.values())
    missing = [VOICE_NAMES[v] for v in VOICES if v not in voices_used]
    if len(counts) == 1:
        warnings.append(f"Only one instrument type ({next(iter(counts))}), so everyone plays the melody (soprano).")
    elif missing:
        warnings.append(f"Only {len(counts)} instrument types, so the {' and '.join(missing)} line"
                        f"{'s are' if len(missing) > 1 else ' is'} left out.")
    if "B" in voices_used and not any(INSTRUMENTS[i].low for i, v in best.assign.items() if v == "B"):
        bass_players = [i for i, v in best.assign.items() if v == "B"]
        warnings.append(f"No low instrument (trombone, euphonium, tuba, bari sax, bass clarinet or bassoon) on the roster: "
                        f"the bass line is played by {', '.join(bass_players)}, octave-displaced. It will sound thin.")
    if best.residual:
        notes_txt = []
        for name, f in best.fits.items():
            for n, w, d in f.problems:
                notes_txt.append(f"{name} m. {n.measure}: written {pitch_label(pitch.Pitch(w))} ({d} "
                                 f"{pitch_label(cfg['ranges'][name][1] if d == 'above' else cfg['ranges'][name][0])})")
        shown = "; ".join(notes_txt[:4]) + ("; …" if len(notes_txt) > 4 else "")
        warnings.append(f"No allowed key keeps every note inside the {cfg['label']} ranges. Concert {best.name} is the "
                        f"least-bad option: {_plural(best.residual, 'note')} still out of range ({shown}). "
                        f"Check these with the player, or widen that range in data/grades.json.")

    key_iv = key_interval(ch.key.tonic.name, best.tonic)
    for r in rhythm:   # name pitches in the chosen concert key
        ps = r.pop("pitches", {})
        r["change"] = r["change"].format(**{k: ("rest" if p is None else pitch_label(p.transpose(key_iv)))
                                            for k, p in ps.items()})
    phrase_spans = {v: ch.phrases(v) for v in VOICES}
    assignment = []
    parts = []
    for name, v in best.assign.items():
        inst = INSTRUMENTS[name]
        f = best.fits[name]
        spans = phrase_spans[v]
        shifts = [{
            "phrase": ph + 1,
            "measures": f"{spans[ph][0]}–{spans[ph][1]}" if spans[ph][0] != spans[ph][1] else f"{spans[ph][0]}",
            "octaves": q,
            "text": f"phrase {ph + 1} (mm. {spans[ph][0]}–{spans[ph][1]}): {'up' if q > 0 else 'down'} "
                    f"{'an octave' if abs(q) == 1 else 'two octaves'} from the rest of the part",
        } for ph, q in sorted(f.phrase_shifts.items())]
        problems = [{
            "measure": n.measure, "beat": n.beat,
            "written": pitch_label(pitch.Pitch(w)), "direction": d,
            "text": f"m. {n.measure} beat {n.beat}: written {pitch_label(pitch.Pitch(w))} is {d} the "
                    f"{pitch_label(cfg['ranges'][name][1] if d == 'above' else cfg['ranges'][name][0])} limit",
        } for n, w, d in f.problems]
        others = [i for i, vv in best.assign.items() if vv == v and i != name]
        rests = [{
            "phrase": ph + 1,
            "measures": f"{spans[ph][0]}–{spans[ph][1]}",
            "text": f"rests in phrase {ph + 1} (mm. {spans[ph][0]}–{spans[ph][1]}): no octave keeps it in range; "
                    f"{_cover(others, best, ph)} the line there",
        } for ph in sorted(f.tacet)]
        entry = {
            "instrument": name,
            "part_name": inst.part_name,
            "players": counts[name],
            "voice": v,
            "voice_name": VOICE_NAMES[v],
            "range": f"{pitch_label(cfg['ranges'][name][0])}–{pitch_label(cfg['ranges'][name][1])} (written)",
            "part_octave": f.octave,
            "part_octave_text": ("as written" if f.octave == 0 else
                                 f"whole part {'up' if f.octave > 0 else 'down'} "
                                 f"{'an octave' if abs(f.octave) == 1 else 'two octaves'}"),
            "phrase_shifts": shifts,
            "rests": rests,
            "out_before_phrase_shifts": f.pre_out,
            "out_of_range": f.residual,
            "problems": problems,
        }
        assignment.append(entry)
        if with_parts:
            sc = build_part(ch, v, inst, best, f, cfg["label"])
            parts.append({
                "instrument": name,
                "part_name": inst.part_name,
                "voice": v,
                "filename": safe_filename(f"{meta['id']}-{name}") + ".musicxml",
                "musicxml": to_musicxml(sc),
            })

    def key_row(r: KeyResult) -> dict:
        return {"key": r.name, "out_before_phrase_shifts": r.pre_out, "phrase_shifts": r.n_shifts,
                "out_of_range": r.residual, "rests": r.n_tacet, "score": round(r.total, 1), "worst": _worst(r, cfg)}

    by_voice = {VOICE_NAMES[v]: [a["instrument"] for a in assignment if a["voice"] == v] for v in VOICES}
    return {
        "chorale": {"id": meta["id"], "bwv": meta["bwv"], "title": meta["title"], "original_key": ch.key_name,
                    "measures": meta["measures"]},
        "grade": {"value": str(grade), "label": cfg["label"]},
        "key": {**key_row(best), "concert": best.name, "transpose_semitones": best.semitones},
        "runner_up": key_row(results[1]) if len(results) > 1 else None,
        "keys_considered": [key_row(r) for r in results],
        "summary": short,
        "explanation": long,
        "least_bad": best.residual > 0,
        "assignment": assignment,
        "voices": by_voice,
        "rhythm": rhythm,
        "warnings": warnings,
        "parts": parts,
    }


def fit_report_text(res: dict) -> str:
    c = res["chorale"]
    lines = [
        f"{chmod.display_title(c['title'], c['bwv'])}, {res['grade']['label']}",
        f"Bach's key: {c['original_key']}.  Concert key chosen: {res['key']['concert']}.",
        "",
        res["explanation"],
        "",
    ]
    if res["warnings"]:
        lines += ["WARNINGS"] + [f"  ! {w}" for w in res["warnings"]] + [""]
    lines.append("KEYS CONSIDERED (out of range before phrase shifts / phrase shifts / phrase rests / still out of range)")
    for k in res["keys_considered"]:
        lines.append(f"  {k['key']:<12} {k['out_before_phrase_shifts']:>3} / {k['phrase_shifts']:>2} / {k['rests']:>2} / {k['out_of_range']:>2}"
                     + (f"   worst: {k['worst']}" if k["worst"] else ""))
    lines += ["", "VOICES"]
    for vname, insts in res["voices"].items():
        lines.append(f"  {vname:<8} {', '.join(insts) if insts else '(not covered)'}")
    lines += ["", "OCTAVE SHIFTS AND PHRASE RESTS"]
    for a in res["assignment"]:
        lines.append(f"  {a['instrument']} ({a['players']} on {a['voice_name'].lower()}, range {a['range']}): {a['part_octave_text']}")
        for s in a["phrase_shifts"]:
            lines.append(f"      {s['text']}")
        for r in a["rests"]:
            lines.append(f"      {r['text']}")
        for p in a["problems"]:
            lines.append(f"      OUT OF RANGE {p['text']}")
    lines += ["", f"RHYTHM SIMPLIFICATIONS ({res['grade']['label']})"]
    if not res["rhythm"]:
        lines.append("  none (played as written)")
    for r in res["rhythm"]:
        lines.append(f"  {r['voice_name']:<8} m. {r['measure']} beat {r['beat']}: {r['change']}")
    return "\n".join(lines) + "\n"
