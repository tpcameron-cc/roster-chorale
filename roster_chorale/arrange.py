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
from . import percussion as perc
from .chorale import DATA, Chorale, key_name, pitch_label
from .instruments import INSTRUMENTS, ORDER, VOICE_NAMES, VOICES, Inst

COST_RESIDUAL = 100     # per note still out of range
COST_TACET = 15         # a part rests for a phrase it can't play in any octave (another part covers it)
COST_PRE = 2            # per note that needed a phrase shift
COST_SHIFT = 4          # per phrase shift
COST_PREF = (0, 3, 6, 9)  # voice preference rank; anything else costs COST_UNLISTED
COST_UNLISTED = 20
COST_LOW_ON_MELODY = 30  # a bass instrument (tuba, bari, cello…) given the soprano line
COST_BELOW_BASS = 500   # an upper voice octave-displaced so it sounds below the bass line (wrong harmony)
BELOW_BASS_MARGIN = 5   # semitones an upper voice's average must stay above the bass line's average
COST_NO_REAL_BASS = 80  # nobody plays the bass line at pitch or lower (so the harmony is inverted)
COST_LOW_OFF_BASS = 300 # the roster has a low instrument but none of them is on the bass line
COST_OVER_MELODY = 60   # an alto/tenor part sounding above the highest melody part (hides the tune)
COST_TWO_OCT = 2        # whole part moved two octaves
COST_THIN = 4           # per player a voice is short of BALANCE_SHARE of the band
BALANCE_SHARE = 0.15
COST_KEY_MOVE = 0.25    # per semitone away from Bach's key (tie-break)
COST_KEY_FAMILY = 3     # per player whose family (band / strings) doesn't list the key at this grade
OCTAVE_TRIES = (0, -1, 1, -2, 2)
INVERSION_WEIGHT = 5    # in key ranking, a part sounding below the bass counts as this many out-of-range notes
COVER_CANDIDATES = 6    # per voice, how many instruments to try as that voice's guaranteed player


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
    phrases: int = 1                     # how many phrases the line has

    def shift_for(self, phrase: int) -> int:
        return self.octave + self.phrase_shifts.get(phrase, 0)

    @property
    def level(self) -> float:
        """Where the part actually sounds, in octaves: the whole-part shift plus the phrase
        shifts averaged over the line (a part 'as written' with every phrase down an octave
        sounds an octave down)."""
        return self.octave + sum(self.phrase_shifts.values()) / max(self.phrases, 1)

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


def fit(notes: list[NoteRef], base: int, lo: int, hi: int, octave: int | None = None) -> Fit:
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
    for o in (OCTAVE_TRIES if octave is None else (octave,)):
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
    return Fit(o, phrase_shifts, len(pre), pre_above, len(pre) - pre_above, bad,
               phrases=len({n.phrase for n in notes}) or 1)


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------
def pref_cost(inst: Inst, v: str) -> int:
    unlisted = COST_UNLISTED * (3 if inst.family == "mallets" else 1)   # bells on the bass line: no
    c = COST_PREF[inst.prefs.index(v)] if v in inst.prefs else unlisted
    return c + (COST_LOW_ON_MELODY if inst.low and v == "S" else 0)


def balance_cost(assign: dict[str, str], counts: dict[str, int], inst_of: dict[str, str] | None = None) -> float:
    """Penalty for thin voices. Mallets only double, so they don't count toward balance."""
    inst_of = inst_of or {i: i for i in counts}
    counts = {i: n for i, n in counts.items() if INSTRUMENTS[inst_of[i]].family != "mallets"} or counts
    total = sum(counts.values())
    per = {v: 0 for v in VOICES}
    for i, v in assign.items():
        if i in counts:
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


def inversions(a: dict[str, str], fits: dict[tuple[str, str], Fit], means: dict[str, float]) -> set[str]:
    """Parts on an upper line that sound below the lowest-sounding bass part (which turns the chords upside down)."""
    bass = [i for i, v in a.items() if v == "B"]
    if not bass or not means:
        return set()
    low = min(means["B"] + 12 * fits[i, "B"].level for i in bass)
    return {i for i, v in a.items() if v != "B" and means[v] + 12 * fits[i, v].level < low + BELOW_BASS_MARGIN}


def over_melody(a: dict[str, str], fits: dict[tuple[str, str], Fit], means: dict[str, float]) -> set[str]:
    """Alto/tenor parts that sound above the highest-sounding soprano part."""
    sop = [i for i, v in a.items() if v == "S"]
    if not sop or not means:
        return set()
    top = max(means["S"] + 12 * fits[i, "S"].level for i in sop)
    return {i for i, v in a.items() if v in ("A", "T") and means[v] + 12 * fits[i, v].level > top}


def assign_voices(cost: dict[tuple[str, str], float], fits: dict[tuple[str, str], Fit],
                  counts: dict[str, int], n_phrases: int,
                  real_bass: dict[str, bool] | None = None,
                  inst_of: dict[str, str] | None = None,
                  means: dict[str, float] | None = None,
                  below: set[tuple[str, str]] | None = None,
                  natural_lines: bool = False) -> tuple[dict[str, str], float]:
    """Exact search: choose distinct parts to cover the required voices, everyone else
    takes their cheapest voice; then a hill-climb that also weighs balance.

    Keys are parts: normally one per instrument, or "Flute 1", "Flute 2" and so on when
    an instrument is split (inst_of maps each part to its instrument)."""
    insts = list(counts)
    inst_of = inst_of or {i: i for i in insts}
    # Mallets double lines; they never carry a voice alone unless the group is only mallets.
    core = [i for i in insts if INSTRUMENTS[inst_of[i]].family != "mallets"] or insts
    n = len(core)
    req = required_voices(n)
    real_bass = real_bass or {i: True for i in insts}
    # optimistic per-pair cost (assumes out-of-range phrases can rest) to seed the search
    seed = {(i, v): cost[i, v] + COST_TACET * len(fits[i, v].bad) for i in insts for v in VOICES}
    cheapest = {i: min(VOICES, key=lambda v: (seed[i, v], VOICES.index(v))) for i in insts}
    # parts split from one instrument (Flute 1, Flute 2) must play different lines
    siblings: dict[str, list[str]] = {}
    for i in insts:
        siblings.setdefault(inst_of[i], []).append(i)
    siblings = {k: ps for k, ps in siblings.items() if len(ps) > 1}
    # In "split sections" mode a section stays on its natural lines (Flute 1/2 on S/A,
    # Trombone 1/2 on T/B). Emergency splits that cover missing lines may use any line.
    allowed: dict[str, tuple[str, ...]] = {}
    for k, ps in siblings.items():
        prefs = INSTRUMENTS[k].prefs
        for pt in ps:
            allowed[pt] = prefs if natural_lines and len(ps) <= len(prefs) else VOICES

    def ranked_for(pt: str) -> list[str]:
        return sorted(allowed.get(pt, VOICES), key=lambda v: (seed[pt, v], VOICES.index(v)))

    for ps in siblings.values():
        for pt, v in zip(ps, ranked_for(ps[0])):
            cheapest[pt] = v

    def spread_siblings(a: dict[str, str], fixed: set[str]) -> dict[str, str] | None:
        """Move unfixed sibling parts off lines a sibling already plays."""
        for ps in siblings.values():
            used = {a[pt] for pt in ps if pt in fixed}
            if len(used) < sum(1 for pt in ps if pt in fixed):
                return None
            if any(a[pt] not in allowed.get(pt, VOICES) for pt in ps if pt in fixed):
                return None
            for pt in ps:
                if pt in fixed:
                    continue
                if a[pt] in used or a[pt] not in allowed.get(pt, VOICES):
                    free = [v for v in ranked_for(pt) if v not in used]
                    if not free:
                        return None
                    a[pt] = free[0]
                used.add(a[pt])
        return a

    def siblings_ok(a: dict[str, str]) -> bool:
        return all(len({a[pt] for pt in ps}) == len(ps) and all(a[pt] in allowed[pt] for pt in ps)
                   for ps in siblings.values())

    has_low = any(INSTRUMENTS[inst_of[i]].low for i in insts)

    def bass_penalty(a: dict[str, str]) -> float:
        if "B" not in a.values():
            return 0.0
        pen = 0.0 if any(real_bass[i] for i, v in a.items() if v == "B") else COST_NO_REAL_BASS
        if has_low and not any(INSTRUMENTS[inst_of[i]].low for i, v in a.items() if v == "B"):
            pen += COST_LOW_OFF_BASS
        return pen

    below = below or set()

    def inversion_penalty(a: dict[str, str]) -> float:
        # inversions the per-part check (against Bach's bass pitch) didn't already price in
        extra = [i for i in inversions(a, fits, means or {}) if (i, a[i]) not in below]
        return COST_BELOW_BASS * len(extra) + COST_OVER_MELODY * len(over_melody(a, fits, means or {}))

    def total_of(a: dict[str, str], balance: bool = True) -> float:
        t = (sum(cost[i, v] for i, v in a.items()) + bass_penalty(a) + inversion_penalty(a)
             + phrase_plan(a, fits, n_phrases)[0])
        return t + (balance_cost(a, counts, inst_of) if balance else 0.0)

    # For each voice, the instruments that lose least by being moved onto it (all of them for
    # small rosters). Trying these as each voice's guaranteed player keeps big orchestras fast.
    regret = {(i, v): seed[i, v] - seed[i, cheapest[i]] for i in insts for v in VOICES}
    cands = {v: sorted(core, key=lambda i: (regret[i, v], insts.index(i)))[:COVER_CANDIDATES] for v in VOICES}

    best_assign, best_total = None, float("inf")
    cover_sets = [req] if n != 3 else [["S", "B", "A"], ["S", "B", "T"]]
    for voices in cover_sets:
        for chosen in itertools.product(*(cands[v] for v in voices)):
            if len(set(chosen)) < len(chosen):
                continue
            assign = {i: cheapest[i] for i in insts}
            assign.update(dict(zip(chosen, voices)))
            if siblings and spread_siblings(assign, set(chosen)) is None:
                continue
            if n <= 3 and len({assign[i] for i in core}) < n:
                continue    # with few instrument types, spread them over distinct voices
            quick = sum(seed[i, v] for i, v in assign.items())
            if quick >= best_total:   # a lower bound that can't win: skip the full evaluation
                continue
            total = total_of(assign, balance=False)
            if total < best_total:
                best_assign, best_total = assign, total
    assert best_assign is not None

    def covered_ok(a: dict[str, str]) -> bool:
        used = {a[i] for i in core}
        if not all(v in used for v in req):
            return False
        if any(a[i] not in used for i in insts if i not in core):
            return False             # a mallet part must double a line someone else plays
        if not siblings_ok(a):
            return False
        return n > 3 or len(used) == n

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
    off_list: dict[str, int] = field(default_factory=dict)  # family -> players for whom the key isn't on their grade list
    inverted: int = 0        # parts pushed below the bass line (wrong harmony); ranks like 5 notes out of range
    part_inst: dict[str, str] = field(default_factory=dict)   # part ("Flute 2") -> instrument ("Flute")
    players: dict[str, int] = field(default_factory=dict)     # part -> players
    crossed: int = 0         # small groups: phrases where a line sounds above the one written over it

    def inst(self, part: str) -> str:
        return self.part_inst.get(part, part)

    @property
    def rank(self) -> tuple[float, float]:
        return (self.residual + INVERSION_WEIGHT * self.inverted, self.total)

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


def key_lists(cfg: dict, mode: str) -> dict[str, list[str]]:
    return {"band": [pitch.Pitch(t).name for t in cfg["keys"][mode]],
            "strings": [pitch.Pitch(t).name for t in cfg.get("string_keys", cfg["keys"])[mode]]}


def candidate_keys(counts: dict[str, int], cfg: dict, mode: str) -> list[tuple[str, dict[str, int]]]:
    """Keys to try, each with the players (by family) for whom it's off their grade's key list.

    Winds use the band list, strings the string list; mallets and percussion don't mind.
    A mixed band-and-strings roster tries both lists and the majority usually wins."""
    lists = key_lists(cfg, mode)
    players = {fam: sum(n for i, n in counts.items() if INSTRUMENTS[i].family == fam) for fam in lists}
    present = [fam for fam in lists if players[fam]] or ["band"]
    out: list[tuple[str, dict[str, int]]] = []
    seen: set[int] = set()
    for fam in present:
        for t in lists[fam]:
            pc = int(pitch.Pitch(t).ps) % 12
            if pc in seen:
                continue
            seen.add(pc)
            off = {f: players[f] for f in present
                   if players[f] and pc not in {int(pitch.Pitch(x).ps) % 12 for x in lists[f]}}
            out.append((t, off))
    return out


PARTS_MODES = ("single", "multiple")
MAX_SECTION_PARTS = 3


COST_OFF_SHARE = 1000   # a small group's section playing a line outside its share (see line_shares)


def line_shares(counts: dict[str, int], cfg: dict) -> dict[str, tuple[str, ...]] | None:
    """For a small group with fewer instrument types than lines (a tuba quartet, euphoniums and
    tubas, horns and trombones): share the four lines out by register and by players.

    Sections are ordered high to low by their natural lines; each gets at least one line,
    the rest go in proportion to players (never more lines than players), and lines are dealt
    out top-down in order: 4 euphoniums + 4 tubas -> euphoniums S/A, tubas T/B; 6 euphoniums +
    2 tubas -> euphoniums S/A/T, tubas B. Returns None for larger groups, which voice normally."""
    core = {i: n for i, n in counts.items() if INSTRUMENTS[i].family != "mallets" and n > 0}
    target = min(len(VOICES), sum(core.values()))
    if not core or len(core) >= target:
        return None
    # high to low by the lines the instrument naturally plays (horn above trombone, euphonium above
    # tuba), then by the middle of its grade-1 range; neither changes with the grade chosen
    rng = ranges_midi(grade_cfg("1"))
    natural = {i: sum(VOICES.index(v) for v in INSTRUMENTS[i].prefs) / len(INSTRUMENTS[i].prefs) for i in core}
    mid = {i: (rng[i][0] + rng[i][1]) / 2 - INSTRUMENTS[i].written_semitones for i in core}
    order = sorted(core, key=lambda i: (natural[i], -mid[i]))
    total = sum(core.values())
    quota = {i: target * core[i] / total for i in order}
    k = {i: min(core[i], max(1, int(quota[i]))) for i in order}
    while sum(k.values()) > target:            # minimum-one rule overshot: take from the biggest share
        i = max((i for i in order if k[i] > 1), key=lambda i: (k[i] - quota[i], k[i]))
        k[i] -= 1
    while sum(k.values()) < target:            # largest remainder first; ties go to the higher section
        i = max((i for i in order if k[i] < core[i]), key=lambda i: (quota[i] - k[i], -order.index(i)))
        k[i] += 1
    out, at = {}, 0
    for i in order:
        out[i] = tuple(VOICES[at:at + k[i]])
        at += k[i]
    return out


def shared_split(counts: dict[str, int], shares: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, int]]:
    """Parts for line_shares: one part per line a section was given (Euphonium 1, Euphonium 2...)."""
    parts: dict[str, tuple[str, int]] = {}
    for i, n in counts.items():
        k = len(shares.get(i, ())) or 1
        if k == 1:
            parts[i] = (i, n)
            continue
        base, extra = divmod(n, k)
        for j in range(k):
            parts[f"{i} {j + 1}"] = (i, base + (1 if j < extra else 0))
    return parts


def section_split(counts: dict[str, int]) -> dict[str, tuple[str, int]]:
    """'Split sections' mode: every section with players to spare divides across the lines
    it suits (up to 3 parts, never more parts than players): Flute 1/2, Clarinet 1/2/3,
    Violin 1/2, Trombone 1/2. Mallets, timpani and percussion stay as one part."""
    parts: dict[str, tuple[str, int]] = {}
    for i, n in counts.items():
        inst = INSTRUMENTS[i]
        k = 1 if inst.family == "mallets" else min(n, len(inst.prefs), MAX_SECTION_PARTS)
        if k <= 1:
            parts[i] = (i, n)
            continue
        base, extra = divmod(n, k)
        for j in range(k):
            parts[f"{i} {j + 1}"] = (i, base + (1 if j < extra else 0))
    return parts


def split_options(counts: dict[str, int], mode: str = "single") -> list[dict[str, tuple[str, int]]]:
    """Ways to turn the roster into parts: {part: (instrument, players)}.

    Normally one part per instrument. When there are fewer instrument types than
    chorale lines, instruments with players to spare are split into numbered parts
    ("Flute 1", "Flute 2") so that every line is covered: as many lines as there are
    players, up to four. Mallets only double, so they are never split."""
    identity = {i: (i, n) for i, n in counts.items()}
    if mode == "multiple":
        sections = section_split(counts)
        core_parts = [pt for pt, (i, _) in sections.items() if INSTRUMENTS[i].family != "mallets"]
        if len(core_parts) >= min(len(VOICES), sum(n for i, n in counts.items() if INSTRUMENTS[i].family != "mallets")):
            return [sections] + ([identity] if sections != identity else [])
        # still short of lines (tiny group): fall back to the minimal split below
    core = {i: n for i, n in counts.items() if INSTRUMENTS[i].family != "mallets"}
    if not core:
        return [identity]
    target = min(len(VOICES), sum(core.values()))
    if len(core) >= target:
        return [identity]
    names = list(core)
    options = []
    for ks in itertools.product(*(range(1, min(core[i], len(VOICES)) + 1) for i in names)):
        if sum(ks) != target:
            continue
        parts: dict[str, tuple[str, int]] = {}
        for i, k in zip(names, ks):
            if k == 1:
                parts[i] = (i, core[i])
                continue
            base, extra = divmod(core[i], k)
            for j in range(k):
                parts[f"{i} {j + 1}"] = (i, base + (1 if j < extra else 0))
        for i, n in counts.items():
            if i not in core:
                parts[i] = (i, n)
        options.append(parts)
    return options or [identity]


def _number_parts(assign: dict[str, str], inst_of: dict[str, str], players: dict[str, int]):
    """Renumber split parts top-down (Flute 1 = highest line); higher lines get the larger groups."""
    groups: dict[str, list[str]] = {}
    for pt, i in inst_of.items():
        if pt != i:
            groups.setdefault(i, []).append(pt)
    if not groups:
        return assign, inst_of, players
    new_assign, new_inst, new_players = {}, {}, {}
    for pt, v in assign.items():
        if inst_of[pt] == pt:
            new_assign[pt], new_inst[pt], new_players[pt] = v, pt, players[pt]
    for i, pts in groups.items():
        pts = sorted(pts, key=lambda x: VOICES.index(assign[x]))
        sizes = sorted((players[x] for x in pts), reverse=True)
        for j, (pt, n) in enumerate(zip(pts, sizes), start=1):
            key_ = f"{i} {j}"
            new_assign[key_], new_inst[key_], new_players[key_] = assign[pt], i, n
    order = {i: k for k, i in enumerate(ORDER)}
    keys = sorted(new_assign, key=lambda x: (order[new_inst[x]], x))
    return ({k: new_assign[k] for k in keys}, {k: new_inst[k] for k in keys}, {k: new_players[k] for k in keys})


def _group_octaves(assign, fits_p, inst_of, notes, means, semis, rng) -> int:
    """Small groups (a tuba quartet, euphoniums and tubas): choose the octave of all four lines
    together, so the group moves down as one and the lines stay in order. Notes out of range
    cost most, then phrases where a line crosses the one written above it, then phrase shifts
    and spreading the lines apart."""
    import itertools as _it
    lines = [v for v in VOICES if v in assign.values()]
    by_line = {v: [pt for pt, x in assign.items() if x == v] for v in lines}

    cache: dict[tuple[str, str, int], Fit] = {}

    def forced(pt: str, v: str, octave: int) -> Fit:
        if (pt, v, octave) not in cache:
            inst = INSTRUMENTS[inst_of[pt]]
            lo, hi = rng[inst.name]
            cache[pt, v, octave] = fit(notes[v], semis + inst.written_semitones, lo, hi, octave)
        return cache[pt, v, octave]

    phrase_mean = {v: {} for v in lines}
    for v in lines:
        acc: dict[int, list[int]] = {}
        for n in notes[v]:
            acc.setdefault(n.phrase, []).append(n.midi)
        phrase_mean[v] = {ph: sum(m) / len(m) for ph, m in acc.items()}

    def crossings(trial) -> int:
        """Phrases where a line sounds above the melody or below the bass (inner lines may cross
        each other: that doesn't change the harmony)."""
        out = 0
        top, bottom = lines[0], lines[-1]
        for ph in phrase_mean[top]:
            sound = {v: [phrase_mean[v][ph] + 12 * trial[pt, v].shift_for(ph) for pt in by_line[v]]
                     for v in lines if ph in phrase_mean[v]}
            if top == "S":
                out += any(max(ms) > min(sound["S"]) for v, ms in sound.items() if v != "S")
            if bottom == "B" and "B" in sound:
                out += any(min(ms) < max(sound["B"]) for v, ms in sound.items() if v != "B")
        return out

    best = None
    for combo in _it.product((1, 0, -1, -2), repeat=len(lines)):
        trial = {}
        score = 0.0
        for v, o in zip(lines, combo):
            for pt in by_line[v]:
                f = forced(pt, v, o)
                trial[pt, v] = f
                score += (COST_RESIDUAL * sum(len(x) for x in f.bad.values()) + COST_PRE * f.pre_out
                          + COST_SHIFT * len(f.phrase_shifts))
        score += 3 * (combo[0] - combo[-1]) + 0.5 * sum(abs(o) for o in combo)   # compact, close to written
        score += COST_OVER_MELODY * crossings(trial)
        if best is None or score < best[0]:
            best = (score, trial)
    if not best:
        return 0
    trial = dict(best[1])

    def fits_phrase(pt: str, v: str, ph: int, shift: int) -> bool:  # noqa: E306
        inst = INSTRUMENTS[inst_of[pt]]
        lo, hi = rng[inst.name]
        base = semis + inst.written_semitones + 12 * shift
        return all(lo <= n.midi + base <= hi for n in notes[v] if n.phrase == ph)

    def move(line: str, ph: int, d: int) -> bool:
        parts = [(pt, line) for pt in by_line[line]]
        if not all(fits_phrase(pt, v, ph, trial[pt, v].shift_for(ph) + d) for pt, v in parts):
            return False
        for pt, v in parts:
            f = trial[pt, v]
            shifts = dict(f.phrase_shifts)
            shifts[ph] = shifts.get(ph, 0) + d
            shifts = {x: y for x, y in shifts.items() if y}
            trial[pt, v] = Fit(f.octave, shifts, f.pre_out, f.pre_above, f.pre_below, f.bad, f.tacet, f.phrases)
        return True

    # A melody phrase that had to drop an octave takes the lines that now sound above it down too,
    # and a bass phrase that had to rise takes the lines now under it up, where they have room.
    for _ in range(len(lines)):
        changed = False
        for ph in sorted({n.phrase for v in lines for n in notes[v]}):
            for v in lines[1:]:
                if "S" in lines and ph in phrase_mean["S"] and ph in phrase_mean[v]:
                    mel = min(phrase_mean["S"][ph] + 12 * trial[pt, "S"].shift_for(ph) for pt in by_line["S"])
                    if max(phrase_mean[v][ph] + 12 * trial[pt, v].shift_for(ph) for pt in by_line[v]) > mel:
                        changed |= move(v, ph, -1) or move("S", ph, +1)
            for v in lines[:-1]:
                if "B" in lines and ph in phrase_mean["B"] and ph in phrase_mean[v]:
                    bas = max(phrase_mean["B"][ph] + 12 * trial[pt, "B"].shift_for(ph) for pt in by_line["B"])
                    if min(phrase_mean[v][ph] + 12 * trial[pt, v].shift_for(ph) for pt in by_line[v]) < bas:
                        changed |= move(v, ph, +1) or move("B", ph, -1)
        if not changed:
            break
    for (pt, v), f in trial.items():
        fits_p[pt, v] = _fold(f, lambda o, pt=pt, v=v: forced(pt, v, o))
    return crossings(trial)


def _fold(f: Fit, refit) -> Fit:
    """Report 'whole part down an octave' rather than 'two octaves down, every phrase back up one':
    when one phrase shift covers more than half the phrases, make it the whole-part octave.
    The notes played are unchanged."""
    from collections import Counter
    eff = Counter(f.phrase_shifts.get(ph, 0) for ph in range(f.phrases))
    q, n = eff.most_common(1)[0]
    if q == 0 or 2 * n <= f.phrases:
        return f
    g = refit(f.octave + q)
    shifts = {ph: f.phrase_shifts.get(ph, 0) - q for ph in range(f.phrases) if f.phrase_shifts.get(ph, 0) != q}
    return Fit(g.octave, shifts, g.pre_out, g.pre_above, g.pre_below, f.bad, f.tacet, f.phrases)


def _keep_melody_on_top(assign, fits_p, inst_of, notes, means, semis, rng) -> None:
    """Keep the lines in order after voicing: nothing above the melody, nothing below the bass.

    When the bass line had to go up an octave (say, clarinets only), inner parts that now
    sit under it move up too; when an inner part sits above the melody, the melody moves up
    (or the inner part down). A move is only made if no new note leaves the range."""
    def bad_count(f: Fit) -> int:
        return sum(len(ns) for ns in f.bad.values())

    def refit(pt: str, v: str, octave: int) -> Fit:
        inst = INSTRUMENTS[inst_of[pt]]
        lo, hi = rng[inst.name]
        return fit(notes[v], semis + inst.written_semitones, lo, hi, octave)

    def try_move(moves: dict[str, int]) -> bool:
        """moves: part -> octave delta. Apply all-or-nothing if it helps and keeps notes in range."""
        trial = dict(fits_p)
        for pt, d in moves.items():
            v = assign[pt]
            new = refit(pt, v, fits_p[pt, v].octave + d)
            if abs(new.octave) > 2 or bad_count(new) > bad_count(fits_p[pt, v]):
                return False
            trial[pt, v] = new
        before = len(inversions(assign, fits_p, means)) + len(over_melody(assign, fits_p, means))
        after = len(inversions(assign, trial, means)) + len(over_melody(assign, trial, means))
        if after < before:
            fits_p.update(trial)
            return True
        return False

    sop = [pt for pt, v in assign.items() if v == "S"]
    for _ in range(6):
        inv = inversions(assign, fits_p, means)
        over = over_melody(assign, fits_p, means)
        if not inv and not over:
            return
        if inv and (try_move({pt: 1 for pt in inv})
                    or try_move({**{pt: 1 for pt in inv}, **{pt: 1 for pt in sop if pt not in inv}})):
            continue
        if over and (try_move({pt: 1 for pt in sop}) or try_move({pt: -1 for pt in over})):
            continue
        return


def part_label(inst: Inst, part: str) -> str:
    """Printed name: 'Flute 2', 'Clarinet 2 in Bb', 'Horn 3 in F'."""
    if part == inst.name:
        return inst.part_name
    num = part.rsplit(" ", 1)[1]
    full = inst.part_name
    if " in " in full:
        a, b = full.split(" in ", 1)
        return f"{a} {num} in {b}"
    return f"{full} {num}"


def evaluate_key(notes: dict[str, list[NoteRef]], mode: str, orig_tonic: str, counts: dict[str, int], cfg: dict,
                 spelled: str, off: dict[str, int], parts_mode: str = "single") -> KeyResult:
    """Voice and fit the roster for one concert key (tonic in music21 spelling, e.g. 'B-').

    Small groups share the lines by register (line_shares) unless the ordinary voicing keeps
    more notes in range, e.g. horns and trombones at grade 1, where trombones taking the alto
    line an octave down is playable and horns there are not."""
    kr = _evaluate_key(notes, mode, orig_tonic, counts, cfg, spelled, off, parts_mode, True)
    if line_shares(counts, cfg) and kr.residual:
        plain = _evaluate_key(notes, mode, orig_tonic, counts, cfg, spelled, off, parts_mode, False)
        if plain.residual < kr.residual:
            return plain
    return kr


def _evaluate_key(notes, mode, orig_tonic, counts, cfg, spelled, off, parts_mode, use_shares) -> KeyResult:
    rng = ranges_midi(cfg)
    n_phrases = max((n.phrase for v in VOICES for n in notes[v]), default=0) + 1
    mean = {v: sum(n.midi for n in notes[v]) / max(len(notes[v]), 1) for v in VOICES}
    semis = key_interval(orig_tonic, spelled).semitones
    fits: dict[tuple[str, str], Fit] = {}
    cost: dict[tuple[str, str], float] = {}
    below: set[tuple[str, str]] = set()
    # a lone player only takes the melody, so there is no bass line to sound below
    has_bass = sum(n for i, n in counts.items() if INSTRUMENTS[i].family != "mallets") >= 2
    for name in counts:
        inst = INSTRUMENTS[name]
        lo, hi = rng[name]
        for v in VOICES:
            f = fit(notes[v], semis + inst.written_semitones, lo, hi)
            fits[name, v] = f
            cost[name, v] = f.cost + pref_cost(inst, v)
            if has_bass and v != "B" and mean[v] + 12 * f.level < mean["B"] + BELOW_BASS_MARGIN:
                cost[name, v] += COST_BELOW_BASS
                below.add((name, v))
    best_opt = None
    shares = line_shares(counts, cfg) if use_shares else None
    options = [shared_split(counts, shares)] if shares else split_options(counts, parts_mode)
    for parts in options:
        inst_of = {pt: i for pt, (i, _) in parts.items()}
        pc = {pt: n for pt, (_, n) in parts.items()}
        cost_p = {(pt, v): cost[i, v] + (COST_OFF_SHARE if shares and i in shares and v not in shares[i] else 0)
                  for pt, i in inst_of.items() for v in VOICES}
        fits_p = {(pt, v): fits[i, v] for pt, i in inst_of.items() for v in VOICES}
        real_bass = {pt: fits[i, "B"].octave <= 0 for pt, i in inst_of.items()}
        below_p = {(pt, v) for pt, i in inst_of.items() for v in VOICES if (i, v) in below}
        natural = parts_mode == "multiple" and not shares and any(pt != i for pt, i in inst_of.items())
        try:
            assign, total = assign_voices(cost_p, fits_p, pc, n_phrases, real_bass, inst_of, mean, below_p,
                                          natural_lines=natural)
        except AssertionError:      # no voicing keeps every section on its natural lines
            assign, total = assign_voices(cost_p, fits_p, pc, n_phrases, real_bass, inst_of, mean, below_p)
        if parts_mode == "multiple" and all(pt == i for pt, i in inst_of.items()) and best_opt is not None:
            continue                # the unsplit fallback only counts if splitting found nothing
        if best_opt is None or total < best_opt[1]:
            best_opt = (assign, total, inst_of, pc)
    assign, total, inst_of, pc = best_opt
    assign, inst_of, pc = _number_parts(assign, inst_of, pc)
    fits_p = {(pt, v): fits[inst_of[pt], v] for pt in assign for v in VOICES}
    crossed = 0
    if shares:
        crossed = _group_octaves(assign, fits_p, inst_of, notes, mean, semis, rng)
    else:
        _keep_melody_on_top(assign, fits_p, inst_of, notes, mean, semis, rng)
    _, tacet, _ = phrase_plan(assign, fits_p, n_phrases)
    chosen = {}
    for pt, v in assign.items():
        f = fits_p[pt, v]
        chosen[pt] = Fit(f.octave, f.phrase_shifts, f.pre_out, f.pre_above, f.pre_below, f.bad, tacet[pt],
                         f.phrases)
    if parts_mode == "multiple":
        assign, chosen, inst_of, pc = _octave_divisi(assign, chosen, inst_of, pc, notes, semis, rng,
                                                     allow_up=not shares)
        fits_p = {(pt, assign[pt]): chosen[pt] for pt in assign}
    total += COST_KEY_MOVE * abs(semis) + COST_KEY_FAMILY * sum(off.values()) + COST_OVER_MELODY * crossed
    inverted = {pt for pt, v in assign.items() if (inst_of[pt], v) in below} | inversions(assign, fits_p, mean)
    return KeyResult(spelled, mode, semis, assign, chosen, total, off, len(inverted), inst_of, pc, crossed)


def _octave_divisi(assign, chosen, inst_of, pc, notes, semis, rng, allow_up: bool = True):
    """'Split sections' for a low section that only suits one line (tuba, bari sax, string bass):
    with two or more players it divides into two parts on that line, in octaves the way band
    tubas do. Part 2 goes an octave below part 1 if that leaves no extra notes out of range
    (phrases that can't go down rejoin part 1, at most half of them); otherwise part 1 goes an
    octave above part 2; failing both, the two parts play in unison."""
    out_a, out_c, out_i, out_p = {}, {}, {}, {}
    for pt, v in assign.items():
        i = inst_of[pt]
        inst = INSTRUMENTS[i]
        f = chosen[pt]
        if pt != i or len(inst.prefs) != 1 or not inst.low or pc[pt] < 2:
            out_a[pt], out_c[pt], out_i[pt], out_p[pt] = v, f, i, pc[pt]
            continue
        lo, hi = rng[i]
        base = semis + inst.written_semitones
        phrases = sorted({n.phrase for n in notes[v]})
        resid = lambda g: sum(len(x) for x in g.bad.values())
        apart = lambda hi, lo: sum(1 for ph in phrases if hi.octave + hi.phrase_shifts.get(ph, 0)
                                   > lo.octave + lo.phrase_shifts.get(ph, 0))
        # no extra notes out of range, and an octave apart in at least half the phrases
        ok_low = lambda g: resid(g) <= resid(f) and 2 * apart(f, g) >= len(phrases)
        ok_high = lambda g: resid(g) <= resid(f) and 2 * apart(g, f) >= len(phrases)
        top, bottom = f, f
        lower = fit(notes[v], base, lo, hi, octave=f.octave - 1)
        upper = fit(notes[v], base, lo, hi, octave=f.octave + 1)
        if ok_low(lower):
            bottom = lower
        elif allow_up and ok_high(upper):   # (not in small groups: it would climb over the line above)
            top = upper
        n1 = (pc[pt] + 1) // 2
        for name, g, n in ((f"{i} 1", top, n1), (f"{i} 2", bottom, pc[pt] - n1)):
            out_a[name] = v
            out_c[name] = Fit(g.octave, g.phrase_shifts, g.pre_out, g.pre_above, g.pre_below, g.bad, f.tacet,
                              g.phrases)
            out_i[name], out_p[name] = i, n
    return out_a, out_c, out_i, out_p


def chorale_mode(ch: Chorale) -> str:
    return ch.key.mode if ch.key.mode in ("major", "minor") else "major"


def search_keys(ch: Chorale, counts: dict[str, int], cfg: dict, parts_mode: str = "single") -> list[KeyResult]:
    """Try every concert key the grade allows for this roster; best first."""
    mode = chorale_mode(ch)
    notes = {v: voice_notes(ch, v) for v in VOICES}
    keys = candidate_keys(counts, cfg, mode)
    results = [_evaluate_key(notes, mode, ch.key.tonic.name, counts, cfg, spelled, off, parts_mode, True)
               for spelled, off in keys]
    results.sort(key=lambda r: r.rank)
    if line_shares(counts, cfg) and results[0].residual:
        # no key keeps a small group playable with lines shared by register: if the ordinary
        # voicing (which may swap inner lines between sections) leaves fewer notes out, use it
        plain = sorted((_evaluate_key(notes, mode, ch.key.tonic.name, counts, cfg, spelled, off, parts_mode, False)
                        for spelled, off in keys), key=lambda r: r.rank)
        if plain[0].residual < results[0].residual:
            results = plain
    return results


def off_list_for(tonic: str, mode: str, counts: dict[str, int], cfg: dict) -> dict[str, int]:
    """Players (by family) for whom this key isn't on their grade's key list."""
    lists = key_lists(cfg, mode)
    pc = int(pitch.Pitch(tonic).ps) % 12
    out = {}
    for fam, keys in lists.items():
        n = sum(c for i, c in counts.items() if INSTRUMENTS[i].family == fam)
        if n and pc not in {int(pitch.Pitch(t).ps) % 12 for t in keys}:
            out[fam] = n
    return out


# ---------------------------------------------------------------------------
# Picking a key first: parse it, and rank chorales that suit it
# ---------------------------------------------------------------------------
KEY_SPELLINGS = {
    "major": ["C", "D-", "D", "E-", "E", "F", "F#", "G", "A-", "A", "B-", "B"],
    "minor": ["C", "C#", "D", "E-", "E", "F", "F#", "G", "G#", "A", "B-", "B"],
}


def parse_key(text: str) -> tuple[str, str]:
    """'Bb major', 'B- major', 'f# minor', 'G' -> (music21 tonic, mode)."""
    parts = (text or "").strip().split()
    if not parts:
        raise ArrangeError("Pick a concert key, like Bb major or G minor.")
    tonic = parts[0]
    mode = parts[1].lower() if len(parts) > 1 else ("minor" if tonic[0].islower() else "major")
    if mode not in ("major", "minor"):
        raise ArrangeError(f"Unknown key {text!r}: say major or minor, like 'Bb major'.")
    t = tonic[0].upper() + tonic[1:].replace("b", "-").replace("\u266d", "-").replace("\u266f", "#")
    try:
        p = pitch.Pitch(t)
    except Exception:  # noqa: BLE001
        raise ArrangeError(f"Unknown key {text!r}.") from None
    # prefer the conventional spelling for that pitch (A# major -> Bb major)
    pc = int(p.ps) % 12
    spelled = next(x for x in KEY_SPELLINGS[mode] if int(pitch.Pitch(x).ps) % 12 == pc)
    return (t if t in KEY_SPELLINGS[mode] else spelled), mode


@lru_cache(maxsize=1)
def _notes_data() -> dict:
    return json.loads((DATA / "chorale_notes.json").read_text(encoding="utf-8"))


def rhythm_id(rule: dict | None) -> str:
    if not rule:
        return "as_written"
    return "quarters" if float(rule["grid"]) == 1.0 else "eighths"


def _stored_notes(entry: dict) -> dict[str, list[NoteRef]]:
    out = {}
    for v in VOICES:
        flat = entry["v"][v]
        out[v] = [NoteRef(0, "", flat[k], flat[k + 1], "") for k in range(0, len(flat), 2)]
    return out


RANK_PREFILTER = 30


def rank_chorales(counts: dict[str, int], grade: str | float, key_text: str, parts_mode: str = "single",
                  limit: int = 5) -> dict:
    """Chorales (in the key's mode) that fit this roster best once transposed to the chosen key."""
    cfg = grade_cfg(grade)
    tonic, mode = parse_key(key_text)
    counts = {k: int(v) for k, v in counts.items() if k in INSTRUMENTS and int(v) > 0}
    voice_counts = {k: n for k, n in counts.items() if INSTRUMENTS[k].pitched_voice}
    if not voice_counts:
        raise ArrangeError("Add at least one instrument that plays the chorale (winds, brass, strings or mallets).")
    data = _notes_data()[rhythm_id(cfg["rhythm"])]
    rng = ranges_midi(cfg)
    off = off_list_for(tonic, mode, voice_counts, cfg)

    # 1) quick score for every chorale: notes out of range if each instrument took its best line
    quick = []
    for cid, entry in data.items():
        if entry["mode"] != mode:
            continue
        notes = _stored_notes(entry)
        semis = key_interval(entry["tonic"], tonic).semitones
        score = 0.0
        for name in voice_counts:
            inst = INSTRUMENTS[name]
            lo, hi = rng[name]
            best_line = float("inf")
            for v in VOICES:
                f = fit(notes[v], semis + inst.written_semitones, lo, hi)
                best_line = min(best_line, f.cost + COST_RESIDUAL * sum(len(b) for b in f.bad.values())
                                + pref_cost(inst, v))
            score += best_line
        quick.append((score, cid, entry, notes))
    quick.sort(key=lambda x: (x[0], x[1]))

    # 2) full voicing for the most promising ones
    meta = {c["id"]: c for c in chmod.catalog()}
    scored = []
    for _, cid, entry, notes in quick[:RANK_PREFILTER]:
        kr = evaluate_key(notes, mode, entry["tonic"], voice_counts, cfg, tonic, off, parts_mode)
        scored.append((kr.rank, cid, kr))
    # cleanest first: fewest notes still out of range (and no upside-down chords), then fewest fixes
    scored.sort(key=lambda x: (x[0][0], x[2].n_shifts + x[2].n_tacet, x[2].pre_out, x[0][1], x[1]))
    out = []
    for _, cid, kr in scored[:limit]:
        m = meta[cid]
        out.append({
            "id": cid, "bwv": m["bwv"], "title": m["title"], "original_key": m["key"], "measures": m["measures"],
            "out_of_range": kr.residual, "phrase_shifts": kr.n_shifts, "rests": kr.n_tacet,
            "out_before_phrase_shifts": kr.pre_out,
            "fit": _fit_phrase(kr),
        })
    return {"key": key_name(tonic, mode), "tonic": tonic, "mode": mode, "grade": str(grade),
            "candidates": sum(1 for e in data.values() if e["mode"] == mode), "shortlist": out,
            "off_list": off}


# ---------------------------------------------------------------------------
# Plain-English explanation
# ---------------------------------------------------------------------------
def _worst(kr: KeyResult, cfg: dict) -> str:
    """e.g. '7 Trumpet notes above C5' for the instrument with the most notes out of range."""
    if kr.pre_out == 0:
        return ""
    name, f = max(kr.fits.items(), key=lambda kv: kv[1].pre_out)
    lo, hi = cfg["ranges"][kr.inst(name)]
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
            limit = cfg["ranges"][kr.inst(name)][1] if d == "above" else cfg["ranges"][kr.inst(name)][0]
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
def build_part(ch: Chorale, v: str, inst: Inst, kr: KeyResult, f: Fit, grade_label: str,
               label: str | None = None) -> stream.Score:
    label = label or inst.part_name
    key_iv = key_interval(ch.key.tonic.name, kr.tonic)
    w_iv = inst.written_interval
    concert_key = key.Key(kr.tonic, kr.mode)
    written_key = concert_key.transpose(w_iv) if w_iv.semitones else concert_key
    ks = key.KeySignature(written_key.sharps)

    part = stream.Part()
    m21inst = inst.m21()
    m21inst.partName = label
    part.insert(0, m21inst)
    part.partName = label
    part.partAbbreviation = inst.name
    bars = ch.voices[v]
    for idx, b in enumerate(bars):
        m = stream.Measure(number=b.number)
        if idx == 0:
            m.insert(0, inst.m21_clef(v))
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
    sc.metadata.movementName = f"{label} · {grade_label} · Concert {kr.name}"
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
def arrange(chorale_id: str, counts: dict[str, int], grade: str | float, with_parts: bool = True,
            parts_mode: str = "single", key_choice: str | None = None) -> dict:
    """parts_mode: "single" = one part per instrument (sections split only when needed to cover
    all four lines); "multiple" = sections split into numbered parts (Flute 1, Flute 2...).

    key_choice: a concert key to use as-is ("Bb major"); None lets the app pick the best-fitting key."""
    cfg = grade_cfg(grade)
    if parts_mode not in PARTS_MODES:
        raise ArrangeError(f"Unknown parts option {parts_mode!r}; use 'single' or 'multiple'.")
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
    extras = {k: n for k, n in counts.items() if not INSTRUMENTS[k].pitched_voice}
    all_counts, counts = counts, {k: n for k, n in counts.items() if INSTRUMENTS[k].pitched_voice}
    if not counts:
        raise ArrangeError("Timpani and percussion need at least one instrument playing the chorale itself. "
                           "Add winds, brass, strings or mallets.")

    original = chmod.load(meta["id"])
    ch, rhythm = chmod.simplify(original, cfg["rhythm"])
    results = search_keys(ch, counts, cfg, parts_mode)
    chosen_key = bool(key_choice and key_choice.strip())
    if chosen_key:
        tonic, kmode = parse_key(key_choice)
        if kmode != chorale_mode(ch):
            raise ArrangeError(f"BWV {meta['bwv']} is in a {chorale_mode(ch)} key ({ch.key_name}); pick a "
                               f"{chorale_mode(ch)} key for it, or let the app choose the key.")
        notes_ = {v: voice_notes(ch, v) for v in VOICES}
        forced = evaluate_key(notes_, kmode, ch.key.tonic.name, counts, cfg, tonic,
                              off_list_for(tonic, kmode, counts, cfg), parts_mode)
        auto_best = results[0]
        results = [forced] + [r for r in results if r.name != forced.name]
        best = forced
        short = f"Concert {forced.name} (your choice): {_fit_phrase(forced)}."
        if forced.name == ch.key_name:
            long = f"You chose concert {forced.name}, Bach's own key: {_fit_phrase(forced)}."
        else:
            long = f"Bach wrote this in {ch.key_name}. You chose concert {forced.name}: {_fit_phrase(forced)}."
        if auto_best.name != forced.name and auto_best.rank < forced.rank:
            long += (f" For this band at {cfg['label'].lower()}, the best-fitting key would be concert "
                     f"{auto_best.name} ({_fit_phrase(auto_best)}).")
    else:
        best = results[0]
        short, long = explain(results, ch, cfg)

    warnings: list[str] = []
    notes: list[str] = []
    voices_used = set(best.assign.values())
    missing = [VOICE_NAMES[v] for v in VOICES if v not in voices_used]
    line_players = sum(n for i, n in counts.items() if INSTRUMENTS[i].family != "mallets") or sum(counts.values())
    if missing:
        who = "player" if line_players == 1 else "players"
        warnings.append(f"Only {line_players} {who} for the chorale's four lines, so the {' and '.join(missing)} line"
                        f"{'s are' if len(missing) > 1 else ' is'} left out. Add a player (or classify an unrecognised "
                        f"one) to fill {'them' if len(missing) > 1 else 'it'} in.")
    split = {}
    for pt, i in best.part_inst.items():
        if pt != i:
            split.setdefault(i, []).append(pt)
    def _desc(pts: list[str]) -> str:
        out = []
        for pt in pts:
            same = [q for q in pts if q != pt and best.assign[q] == best.assign[pt]]
            how = ""
            if same and best.fits[pt].octave < best.fits[same[0]].octave:
                how = ", an octave lower"
            elif same and best.fits[pt].octave == best.fits[same[0]].octave and pt.endswith(" 2"):
                how = ", in unison (an octave apart would leave notes out of range)"
            out.append(f"{pt} on {VOICE_NAMES[best.assign[pt]].lower()} ({_plural(best.players[pt], 'player')}{how})")
        return ", ".join(out)
    if parts_mode == "multiple" and split:
        notes.append("Sections are split into numbered parts: "
                     + "; ".join(_desc(pts) for pts in split.values()) + ".")
    else:
        for i, pts in split.items():
            notes.append(f"Not enough instrument types to cover all four lines, so the {i.lower()} section is split "
                         f"into {len(pts)} parts: {_desc(pts)}.")
    if best.crossed:
        warnings.append(f"The ranges don't leave room for all four lines in "
                        f"{_plural(best.crossed, 'phrase')}, so there one line sounds above the line written over it. "
                        f"Check the fit report for the phrase shifts, or try another grade.")
    if best.inverted:
        means = {v: sum(n.midi for n in voice_notes(ch, v)) / max(len(voice_notes(ch, v)), 1) for v in VOICES}
        fp = {(pt, v): best.fits[pt] for pt, v in best.assign.items()}
        under = sorted(inversions(best.assign, fp, means))
        if under:
            warnings.append(f"The bass line has to be played an octave up to stay in range, so {', '.join(under)} "
                            f"sometimes sound{'s' if len(under) == 1 else ''} below it and some chords come out "
                            f"upside down. A low instrument on the roster would fix this.")
    if "B" in voices_used and not any(INSTRUMENTS[best.inst(i)].low for i, v in best.assign.items() if v == "B"):
        bass_players = [i for i, v in best.assign.items() if v == "B"]
        warnings.append(f"No low instrument (cello, double bass, trombone, euphonium, tuba, bari sax, bass clarinet "
                        f"or bassoon) on the roster: "
                        f"the bass line is played by {', '.join(bass_players)}, octave-displaced. It will sound thin.")
    if best.off_list:
        fam_word = {"band": "wind and brass", "strings": "string"}
        lists = key_lists(cfg, best.mode)
        for fam, n in best.off_list.items():
            other = [f for f in lists if f != fam and f not in best.off_list]
            if chosen_key:
                warnings.append(f"Concert {best.name} isn't on the {cfg['label']} {fam_word[fam]} key list, so your "
                                f"{_plural(n, fam_word[fam] + ' player')} will be reading a less familiar key.")
                continue
            warnings.append(f"Concert {best.name} isn't on the {cfg['label']} {fam_word[fam]} key list, so your "
                            f"{_plural(n, fam_word[fam] + ' player')} read a less familiar key. It was chosen because it suits "
                            f"the {' and '.join(fam_word[f] for f in other) or 'rest of the'} players and keeps everyone in range."
                            .replace("players players", "players"))
    if best.residual:
        notes_txt = []
        for name, f in best.fits.items():
            for n, w, d in f.problems:
                rg = cfg["ranges"][best.inst(name)]
                notes_txt.append(f"{name} m. {n.measure}: written {pitch_label(pitch.Pitch(w))} ({d} "
                                 f"{pitch_label(rg[1] if d == 'above' else rg[0])})")
        shown = "; ".join(notes_txt[:4]) + ("; …" if len(notes_txt) > 4 else "")
        if chosen_key:
            warnings.append(f"In concert {best.name}, {_plural(best.residual, 'note')} stay out of the {cfg['label']} "
                            f"ranges ({shown}). Try another chorale from the shortlist, another key, or let the app "
                            f"choose the key.")
        else:
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
        inst = INSTRUMENTS[best.inst(name)]
        rg = cfg["ranges"][inst.name]
        label = part_label(inst, name)
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
                    f"{pitch_label(rg[1] if d == 'above' else rg[0])} limit",
        } for n, w, d in f.problems]
        others = [i for i, vv in best.assign.items() if vv == v and i != name]
        rests = [{
            "phrase": ph + 1,
            "measures": f"{spans[ph][0]}–{spans[ph][1]}",
            "text": f"rests in phrase {ph + 1} (mm. {spans[ph][0]}–{spans[ph][1]}): no octave keeps it in range; "
                    f"{_cover(others, best, ph)} the line there",
        } for ph in sorted(f.tacet)]
        entry = {
            "part": name,
            "instrument": inst.name,
            "part_name": label,
            "players": best.players.get(name, counts.get(name, 0)),
            "voice": v,
            "voice_name": VOICE_NAMES[v],
            "range": f"{pitch_label(rg[0])}–{pitch_label(rg[1])} (written)",
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
            sc = build_part(ch, v, inst, best, f, cfg["label"], label)
            parts.append({
                "part": name,
                "instrument": inst.name,
                "part_name": label,
                "voice": v,
                "filename": safe_filename(f"{meta['id']}-{name}") + ".musicxml",
                "musicxml": to_musicxml(sc),
            })

    if extras:
        concert_key = key.Key(best.tonic, best.mode)
        for name, n in extras.items():
            inst = INSTRUMENTS[name]
            lo, hi = ranges_midi(cfg).get(name, (41, 53))
            if inst.role == "timpani":
                rows, text, tuning = perc.timpani_hits(ch, key_iv, best.tonic, str(grade), lo, hi)
                template, ks_key = "B", concert_key
                rng_text = f"{pitch_label(cfg['ranges'][name][0])}–{pitch_label(cfg['ranges'][name][1])} (tuning)"
            else:
                rows, text = perc.percussion_hits(ch, str(grade))
                template, ks_key, rng_text = "S", None, "unpitched"
            assignment.append({
                "part": name, "instrument": name, "part_name": inst.part_name, "players": n, "voice": "P",
                "voice_name": "Percussion", "range": rng_text, "part_octave": 0, "part_octave_text": text,
                "phrase_shifts": [], "rests": [], "out_before_phrase_shifts": 0, "out_of_range": 0, "problems": [],
            })
            if with_parts:
                sc = perc.write_part(ch, template, rows, inst, best.name, cfg["label"], ks_key)
                parts.append({
                    "part": name, "instrument": name, "part_name": inst.part_name, "voice": "P",
                    "filename": safe_filename(f"{meta['id']}-{name}") + ".musicxml",
                    "musicxml": to_musicxml(sc),
                })

    def key_row(r: KeyResult) -> dict:
        return {"key": r.name, "out_before_phrase_shifts": r.pre_out, "phrase_shifts": r.n_shifts,
                "out_of_range": r.residual, "rests": r.n_tacet, "score": round(r.total, 1), "worst": _worst(r, cfg)}

    by_voice = {VOICE_NAMES[v]: [a["part"] for a in assignment if a["voice"] == v] for v in VOICES}
    if extras:
        by_voice["Percussion"] = list(extras)
    return {
        "chorale": {"id": meta["id"], "bwv": meta["bwv"], "title": meta["title"], "original_key": ch.key_name,
                    "measures": meta["measures"]},
        "grade": {"value": str(grade), "label": cfg["label"]},
        "parts_mode": parts_mode,
        "key": {**key_row(best), "concert": best.name, "transpose_semitones": best.semitones},
        "runner_up": key_row(results[1]) if len(results) > 1 else None,
        "key_choice": "chosen" if chosen_key else "best_fit",
        "keys_considered": [key_row(r) for r in results],
        "summary": short,
        "explanation": long,
        "least_bad": best.residual > 0,
        "assignment": assignment,
        "voices": by_voice,
        "rhythm": rhythm,
        "warnings": warnings,
        "notes": notes,
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
    if res.get("notes"):
        lines += ["NOTES"] + [f"  - {n}" for n in res["notes"]] + [""]
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
        lines.append(f"  {a['part']} ({a['players']} on {a['voice_name'].lower()}, range {a['range']}): {a['part_octave_text']}")
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
