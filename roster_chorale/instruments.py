"""The standard instrument list, music21 classes, voice preferences and name aliases."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from music21 import clef, instrument, interval

VOICES = ("S", "A", "T", "B")
VOICE_NAMES = {"S": "Soprano", "A": "Alto", "T": "Tenor", "B": "Bass"}


@dataclass(frozen=True)
class Inst:
    name: str                       # standard name used everywhere
    factory: Callable[[], instrument.Instrument]
    prefs: tuple[str, ...]          # voices in order of preference
    clef: str                       # "treble" or "bass"
    low: bool = False               # can carry a real bass line
    full_name: str = ""             # name printed on the part

    @property
    def part_name(self) -> str:
        return self.full_name or self.name

    def m21(self) -> instrument.Instrument:
        i = self.factory()
        i.partName = self.part_name
        i.partAbbreviation = self.name
        return i

    @property
    def written_interval(self) -> interval.Interval:
        """Interval from concert pitch to written pitch (e.g. M2 up for Bb clarinet)."""
        t = self.factory().transposition
        return t.reverse() if t is not None else interval.Interval("P1")

    @property
    def written_semitones(self) -> int:
        return self.written_interval.semitones

    def m21_clef(self) -> clef.Clef:
        return clef.TrebleClef() if self.clef == "treble" else clef.BassClef()


def _euph() -> instrument.Instrument:
    i = instrument.Baritone()
    i.instrumentName = "Euphonium"
    return i


INSTRUMENTS: dict[str, Inst] = {i.name: i for i in [
    Inst("Flute", instrument.Flute, ("S", "A"), "treble"),
    Inst("Oboe", instrument.Oboe, ("S", "A"), "treble"),
    Inst("Clarinet", instrument.Clarinet, ("S", "A", "T"), "treble", full_name="Clarinet in Bb"),
    Inst("Bass Clarinet", instrument.BassClarinet, ("B", "T"), "treble", low=True, full_name="Bass Clarinet in Bb"),
    Inst("Bassoon", instrument.Bassoon, ("B", "T"), "bass", low=True),
    Inst("Alto Sax", instrument.AltoSaxophone, ("A", "S", "T"), "treble", full_name="Alto Saxophone in Eb"),
    Inst("Tenor Sax", instrument.TenorSaxophone, ("T", "B", "A"), "treble", full_name="Tenor Saxophone in Bb"),
    Inst("Bari Sax", instrument.BaritoneSaxophone, ("B",), "treble", low=True, full_name="Baritone Saxophone in Eb"),
    Inst("Trumpet", instrument.Trumpet, ("S", "A"), "treble", full_name="Trumpet in Bb"),
    Inst("Horn", instrument.Horn, ("A", "T"), "treble", full_name="Horn in F"),
    Inst("Trombone", instrument.Trombone, ("T", "B"), "bass", low=True),
    Inst("Euphonium", _euph, ("T", "B"), "bass", low=True),
    Inst("Tuba", instrument.Tuba, ("B",), "bass", low=True),
]}

# Score order for display.
ORDER = list(INSTRUMENTS)

# ---------------------------------------------------------------------------
# Aliases: messy roster names -> standard names.
# Exact matches are checked first (after normalising), then keyword rules.
# ---------------------------------------------------------------------------
_EXACT = {
    "fl": "Flute", "flt": "Flute", "flute": "Flute", "piccolo": "Flute", "picc": "Flute",
    "ob": "Oboe", "oboe": "Oboe",
    "cl": "Clarinet", "clar": "Clarinet", "clt": "Clarinet", "clarinet": "Clarinet",
    "soprano clarinet": "Clarinet",
    "bcl": "Bass Clarinet", "bass cl": "Bass Clarinet", "bass clar": "Bass Clarinet", "bass clarinet": "Bass Clarinet",
    "b cl": "Bass Clarinet", "bs cl": "Bass Clarinet", "bs clar": "Bass Clarinet",
    "bsn": "Bassoon", "bassoon": "Bassoon",
    "as": "Alto Sax", "asx": "Alto Sax", "alto": "Alto Sax", "alto sax": "Alto Sax", "alto saxophone": "Alto Sax",
    "a sax": "Alto Sax", "altosax": "Alto Sax",
    "ts": "Tenor Sax", "tsx": "Tenor Sax", "tenor": "Tenor Sax", "tenor sax": "Tenor Sax", "tenor saxophone": "Tenor Sax",
    "t sax": "Tenor Sax", "tenorsax": "Tenor Sax",
    "bari": "Bari Sax", "bari sax": "Bari Sax", "baritone sax": "Bari Sax", "bs": "Bari Sax", "b sax": "Bari Sax",
    "baritone saxophone": "Bari Sax", "barisax": "Bari Sax", "bari saxophone": "Bari Sax",
    "tpt": "Trumpet", "trp": "Trumpet", "tp": "Trumpet", "trumpet": "Trumpet", "cornet": "Trumpet",
    "hn": "Horn", "hrn": "Horn", "horn": "Horn", "french horn": "Horn", "f horn": "Horn",
    "mellophone": "Horn", "mello": "Horn",
    "tbn": "Trombone", "trb": "Trombone", "tb": "Trombone", "trombone": "Trombone",
    "bone": "Trombone", "bass trombone": "Trombone",
    "euph": "Euphonium", "euphonium": "Euphonium", "baritone": "Euphonium",
    "baritone horn": "Euphonium", "bar": "Euphonium", "bh": "Euphonium", "bc baritone": "Euphonium",
    "tba": "Tuba", "tu": "Tuba", "tuba": "Tuba", "sousaphone": "Tuba", "sousa": "Tuba",
}

# (required substrings, standard name) checked in order; first match wins.
_RULES: list[tuple[tuple[str, ...], str]] = [
    (("bass", "clar"), "Bass Clarinet"),
    (("bari", "sax"), "Bari Sax"),
    (("baritone", "sax"), "Bari Sax"),
    (("alto", "sax"), "Alto Sax"),
    (("tenor", "sax"), "Tenor Sax"),
    (("clar",), "Clarinet"),
    (("flut",), "Flute"),
    (("oboe",), "Oboe"),
    (("bassoon",), "Bassoon"),
    (("trump",), "Trumpet"),
    (("cornet",), "Trumpet"),
    (("french",), "Horn"),
    (("trom",), "Trombone"),
    (("euph",), "Euphonium"),
    (("tuba",), "Tuba"),
    (("sousa",), "Tuba"),
    (("horn",), "Horn"),
]

_STRIP = [
    r"\bin (bb|b flat|bflat|eb|e flat|eflat|f|c)\b",
    r"^(bb|b flat|bflat|eb|e flat|eflat)\b",
    r"\b(1st|2nd|3rd|4th|first|second|third|i{1,3}|iv|[1-4])\b",
    r"\b(section|part|player|chair)\b",
]


def normalise(name: str) -> str:
    s = name.lower().replace("♭", "b").replace("♯", "#")
    s = re.sub(r"[._/\\\-,:;()\[\]]+", " ", s)
    s = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", s)  # "Flute2" -> "flute 2"
    s = re.sub(r"\s+", " ", s).strip()
    for pat in _STRIP:
        s = re.sub(pat, " ", s)
        s = re.sub(r"\s+", " ", s).strip()
    return s


def canonical(name: str) -> str | None:
    """Map a messy roster name to a standard instrument name, or None if unknown."""
    if not name or not str(name).strip():
        return None
    name = str(name).strip()
    if name in INSTRUMENTS:
        return name
    s = normalise(name)
    if not s:
        return None
    if s in _EXACT:
        return _EXACT[s]
    if s.endswith("s") and s[:-1] in _EXACT:   # plurals: "Flutes", "Trumpets"
        return _EXACT[s[:-1]]
    if s.endswith("es") and s[:-2] in _EXACT:  # "Saxes"
        return _EXACT[s[:-2]]
    for needles, std in _RULES:
        if all(n in s for n in needles):
            return std
    return None
