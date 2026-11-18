"""The standard instrument list, music21 classes, voice preferences and name aliases."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from music21 import clef, instrument, interval

VOICES = ("S", "A", "T", "B")
VOICE_NAMES = {"S": "Soprano", "A": "Alto", "T": "Tenor", "B": "Bass"}


FAMILIES = ("band", "strings", "mallets", "percussion")
_NAMED_INTERVALS = {24: "P15", 12: "P8", 0: "P1", -12: "P-8", -24: "P-15"}


@dataclass(frozen=True)
class Inst:
    name: str                       # standard name used everywhere
    factory: Callable[[], instrument.Instrument]
    prefs: tuple[str, ...]          # voices in order of preference (empty for timpani/percussion)
    clef: str                       # "treble", "alto", "bass", "auto" (treble for S/A, bass for T/B) or "percussion"
    low: bool = False               # can carry a real bass line
    full_name: str = ""             # name printed on the part
    family: str = "band"            # band / strings / mallets / percussion (drives key preferences)
    role: str = "voice"             # "voice" (plays a chorale line), "timpani" or "percussion"
    written_octaves: int | None = None  # octave transposition music21 doesn't know (bells read 2 octaves down)

    @property
    def part_name(self) -> str:
        return self.full_name or self.name

    @property
    def pitched_voice(self) -> bool:
        return self.role == "voice"

    def m21(self) -> instrument.Instrument:
        i = self.factory()
        i.partName = self.part_name
        i.partAbbreviation = self.name
        if self.written_octaves is not None:
            i.transposition = self.written_interval.reverse()   # written -> sounding, for MuseScore
        return i

    @property
    def written_interval(self) -> interval.Interval:
        """Interval from concert pitch to written pitch (e.g. M2 up for Bb clarinet)."""
        if self.written_octaves is not None:
            return interval.Interval(_NAMED_INTERVALS[12 * self.written_octaves])
        t = self.factory().transposition
        return t.reverse() if t is not None else interval.Interval("P1")

    @property
    def written_semitones(self) -> int:
        return self.written_interval.semitones

    def m21_clef(self, voice: str = "S") -> clef.Clef:
        c = self.clef
        if c == "auto":
            c = "treble" if voice in ("S", "A") else "bass"
        return {"treble": clef.TrebleClef, "alto": clef.AltoClef, "bass": clef.BassClef,
                "percussion": clef.PercussionClef}[c]()


def _euph() -> instrument.Instrument:
    i = instrument.Baritone()
    i.instrumentName = "Euphonium"
    return i


INSTRUMENTS: dict[str, Inst] = {i.name: i for i in [
    # woodwinds and brass
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
    # percussion
    Inst("Timpani", instrument.Timpani, (), "bass", family="percussion", role="timpani"),
    Inst("Bells", instrument.Glockenspiel, ("S",), "treble", family="mallets", written_octaves=-2,
         full_name="Bells (Glockenspiel)"),
    Inst("Xylophone", instrument.Xylophone, ("S", "A"), "treble", family="mallets", written_octaves=-1),
    Inst("Marimba", instrument.Marimba, ("A", "T", "S", "B"), "auto", family="mallets"),
    Inst("Vibraphone", instrument.Vibraphone, ("S", "A"), "treble", family="mallets"),
    Inst("Percussion", instrument.Percussion, (), "percussion", family="percussion", role="percussion",
         full_name="Percussion (B.D., Sus. Cym.)"),
    # strings
    Inst("Violin", instrument.Violin, ("S", "A"), "treble", family="strings"),
    Inst("Viola", instrument.Viola, ("A", "T"), "alto", family="strings"),
    Inst("Cello", instrument.Violoncello, ("B", "T"), "bass", low=True, family="strings"),
    Inst("Double Bass", instrument.Contrabass, ("B",), "bass", low=True, family="strings", written_octaves=1),
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
    # strings
    "vn": "Violin", "vln": "Violin", "vl": "Violin", "vi": "Violin", "violin": "Violin", "fiddle": "Violin",
    "va": "Viola", "vla": "Viola", "viola": "Viola",
    "vc": "Cello", "vlc": "Cello", "cello": "Cello", "violoncello": "Cello", "vcl": "Cello",
    "db": "Double Bass", "cb": "Double Bass", "sb": "Double Bass", "bass": "Double Bass", "double bass": "Double Bass",
    "string bass": "Double Bass", "upright bass": "Double Bass", "contrabass": "Double Bass", "kb": "Double Bass",
    "electric bass": "Double Bass", "bass guitar": "Double Bass", "upright": "Double Bass",
    # percussion
    "timp": "Timpani", "timps": "Timpani", "timpani": "Timpani", "tympani": "Timpani", "kettle drums": "Timpani",
    "bells": "Bells", "bell": "Bells", "glock": "Bells", "glockenspiel": "Bells", "orchestra bells": "Bells",
    "mallets": "Bells", "mallet": "Bells", "mallet percussion": "Bells", "keyboard percussion": "Bells",
    "chimes": "Bells", "xylo": "Xylophone", "xylophone": "Xylophone", "xyl": "Xylophone",
    "marimba": "Marimba", "mar": "Marimba", "mrb": "Marimba",
    "vibes": "Vibraphone", "vibraphone": "Vibraphone", "vib": "Vibraphone",
    "perc": "Percussion", "percussion": "Percussion", "drums": "Percussion", "drum": "Percussion",
    "snare": "Percussion", "snare drum": "Percussion", "sd": "Percussion", "bass drum": "Percussion",
    "bd": "Percussion", "cymbals": "Percussion", "cymbal": "Percussion", "crash cymbals": "Percussion",
    "sus cym": "Percussion", "suspended cymbal": "Percussion", "triangle": "Percussion", "tri": "Percussion",
    "drum set": "Percussion", "drumset": "Percussion", "drum kit": "Percussion", "kit": "Percussion",
    "aux": "Percussion", "aux perc": "Percussion", "auxiliary percussion": "Percussion",
    "tambourine": "Percussion", "battery": "Percussion", "battery percussion": "Percussion",
}

# (required substrings, standard name) checked in order; first match wins.
_RULES: list[tuple[tuple[str, ...], str]] = [
    (("bass", "clar"), "Bass Clarinet"),
    (("bass", "drum"), "Percussion"),
    (("timp",), "Timpani"),
    (("tymp",), "Timpani"),
    (("glock",), "Bells"),
    (("xylo",), "Xylophone"),
    (("marimb",), "Marimba"),
    (("vibra",), "Vibraphone"),
    (("mallet",), "Bells"),
    (("snare",), "Percussion"),
    (("cymb",), "Percussion"),
    (("perc",), "Percussion"),
    (("drum",), "Percussion"),
    (("triang",), "Percussion"),
    (("viola",), "Viola"),
    (("violin",), "Violin"),
    (("cello",), "Cello"),
    (("double", "bass"), "Double Bass"),
    (("string", "bass"), "Double Bass"),
    (("contrab",), "Double Bass"),
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
