"""Strings, mallets, timpani, percussion, and typed instrumentation."""
import re

import pytest
from music21 import converter, pitch

from roster_chorale.arrange import ArrangeError, arrange, grade_cfg, key_lists, ranges_midi
from roster_chorale.instruments import INSTRUMENTS, canonical
from roster_chorale.roster import parse_text

from conftest import LOPSIDED

STRINGS = {"Violin": 12, "Viola": 4, "Cello": 5, "Double Bass": 2}
FULL = {"Flute": 2, "Oboe": 2, "Clarinet": 2, "Bassoon": 2, "Horn": 4, "Trumpet": 2, "Trombone": 3, "Tuba": 1,
        "Timpani": 1, "Percussion": 3, "Bells": 1, "Violin": 16, "Viola": 6, "Cello": 5, "Double Bass": 3}


# ---------------------------------------------------------------------------
# Typed instrumentation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("Flute 9, Clarinet 5, Tuba 1", {"Flute": 9, "Clarinet": 5, "Tuba": 1}),
    ("9 flutes, 5 clarinets, 1 tuba", {"Flute": 9, "Clarinet": 5, "Tuba": 1}),
    ("Flute - 9\nTrumpet - 4\nTbn - 2", {"Flute": 9, "Trumpet": 4, "Trombone": 2}),
    ("Flute: 9; Alto Sax: 3", {"Flute": 9, "Alto Sax": 3}),
    ("flute x9, trumpet x 4, Xylophone 1", {"Flute": 9, "Trumpet": 4, "Xylophone": 1}),
    ("Flute 9 - Clarinet 5 - Tuba 1", {"Flute": 9, "Clarinet": 5, "Tuba": 1}),
    ("- Violin 12\n- Viola 4\n- Cello 5\n- Bass 2", {"Violin": 12, "Viola": 4, "Cello": 5, "Double Bass": 2}),
    ("Violins (12), violas (4) and cellos (5)", {"Violin": 12, "Viola": 4, "Cello": 5}),
    ("Flute=9 Trumpet=4", {"Flute": 9, "Trumpet": 4}),
    ("flute 9 clarinet 5 trumpet 4", {"Flute": 9, "Clarinet": 5, "Trumpet": 4}),
    ("Alto Sax 1 - 3, Alto Sax 2 - 2", {"Alto Sax": 5}),
    ("3 snare drums, 1 timpani, 2 bells", {"Timpani": 1, "Bells": 2, "Percussion": 3}),
    ("Flute, Flute, Clarinet, Tuba", {"Flute": 2, "Clarinet": 1, "Tuba": 1}),
    ("Flute - Clarinet - Trumpet", {"Flute": 1, "Clarinet": 1, "Trumpet": 1}),
    ("Flute\nFlute\nOboe", {"Flute": 2, "Oboe": 1}),
    # spreadsheet-ish text still reads as rows
    ("Ava,7,Flute\nLiam,6,Tpt\nZoe,8,Bari", {"Flute": 1, "Trumpet": 1, "Bari Sax": 1}),
    ("Flute 2\nClarinet\nFlute 1", {"Flute": 2, "Clarinet": 1}),
    ("Instrument,Count\nFlute,9\nTuba,1", {"Flute": 9, "Tuba": 1}),
])
def test_typed_instrumentation(text, expected):
    assert parse_text(text).ordered_counts() == expected


def test_typed_unrecognised_names_are_listed():
    r = parse_text("12 violin players, 2 kazoos")
    assert r.ordered_counts() == {"Violin": 12} and r.unrecognized == {"kazoos": 2}


# ---------------------------------------------------------------------------
# Aliases
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("raw,std", {
    "Violin 1": "Violin", "Vln. 2": "Violin", "Violin II": "Violin", "Viola": "Viola", "Vla": "Viola",
    "Cello": "Cello", "Violoncello": "Cello", "String Bass": "Double Bass", "Bass": "Double Bass",
    "Upright Bass": "Double Bass", "Snare Drum": "Percussion", "Bass Drum": "Percussion", "Perc 1": "Percussion",
    "Crash Cymbals": "Percussion", "Drum Set": "Percussion", "Timpani": "Timpani", "Timp.": "Timpani",
    "Bells": "Bells", "Glockenspiel": "Bells", "Mallet Percussion": "Bells", "Xylophone": "Xylophone",
    "Marimba": "Marimba", "Vibes": "Vibraphone", "Bass Clarinet": "Bass Clarinet",
}.items())
def test_orchestra_aliases(raw, std):
    assert canonical(raw) == std


# ---------------------------------------------------------------------------
# Keys by family
# ---------------------------------------------------------------------------
def _key_pc(res):
    tonic, mode = res["key"]["concert"].split()
    return int(pitch.Pitch(tonic[0] + tonic[1:].replace("b", "-")).ps) % 12, mode


@pytest.mark.parametrize("cid", ["bwv66.6", "bwv269", "bwv86.6", "bwv40.8"])
def test_string_orchestra_uses_string_keys(cid):
    res = arrange(cid, STRINGS, "1", with_parts=False)
    pc, mode = _key_pc(res)
    assert pc in {int(pitch.Pitch(t).ps) % 12 for t in key_lists(grade_cfg("1"), mode)["strings"]}


@pytest.mark.parametrize("cid", ["bwv66.6", "bwv269", "bwv86.6"])
def test_band_still_uses_band_keys(cid):
    res = arrange(cid, LOPSIDED, "1", with_parts=False)
    pc, mode = _key_pc(res)
    assert pc in {int(pitch.Pitch(t).ps) % 12 for t in key_lists(grade_cfg("1"), mode)["band"]}


def test_mixed_roster_warns_about_off_list_family():
    res = arrange("bwv66.6", FULL, "1", with_parts=False)
    pc, mode = _key_pc(res)
    lists = key_lists(grade_cfg("1"), mode)
    on_band = pc in {int(pitch.Pitch(t).ps) % 12 for t in lists["band"]}
    on_str = pc in {int(pitch.Pitch(t).ps) % 12 for t in lists["strings"]}
    if not (on_band and on_str):
        assert any("key list" in w for w in res["warnings"])


# ---------------------------------------------------------------------------
# Strings and mallets
# ---------------------------------------------------------------------------
def test_string_quartet_voicing():
    res = arrange("bwv66.6", STRINGS, "3", with_parts=False)
    assert res["voices"]["Soprano"] == ["Violin"] and res["voices"]["Alto"] == ["Viola"]
    assert res["voices"]["Tenor"] == ["Cello"] and res["voices"]["Bass"] == ["Double Bass"]


def test_viola_reads_alto_clef_and_bass_sounds_an_octave_down():
    res = arrange("bwv66.6", STRINGS, "3")
    xml = {p["instrument"]: p["musicxml"] for p in res["parts"]}
    assert "<sign>C</sign>" in xml["Viola"] and "<line>3</line>" in xml["Viola"]
    assert "<octave-change>-1</octave-change>" in xml["Double Bass"]


@pytest.mark.parametrize("inst,offset", [("Bells", -24), ("Xylophone", -12), ("Double Bass", 12), ("Marimba", 0)])
def test_octave_transposing_instruments(inst, offset):
    assert INSTRUMENTS[inst].written_semitones == offset


@pytest.mark.parametrize("grade", ["1", "3"])
def test_orchestra_parts_stay_in_range(grade):
    res = arrange("bwv66.6", FULL, grade)
    rng = ranges_midi(grade_cfg(grade))
    flagged = {(a["instrument"], p["measure"]) for a in res["assignment"] for p in a["problems"]}
    for p in res["parts"]:
        if INSTRUMENTS[p["instrument"]].role != "voice":
            continue
        lo, hi = rng[p["instrument"]]
        part = converter.parseData(p["musicxml"], format="musicxml").parts[0]
        for n in part.recurse().notes:
            assert lo <= n.pitch.ps <= hi or (p["instrument"], n.measureNumber) in flagged


# ---------------------------------------------------------------------------
# Timpani and percussion
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("grade", ["1", "3"])
@pytest.mark.parametrize("cid", ["bwv66.6", "bwv269", "bwv86.6"])
def test_timpani_plays_only_tonic_and_dominant_in_range(cid, grade):
    res = arrange(cid, FULL, grade)
    tonic, mode = res["key"]["concert"].split()
    t = pitch.Pitch(tonic[0] + tonic[1:].replace("b", "-"))
    allowed = {int(t.ps) % 12, int(t.transpose("P5").ps) % 12}
    lo, hi = ranges_midi(grade_cfg(grade))["Timpani"]
    xml = next(p["musicxml"] for p in res["parts"] if p["instrument"] == "Timpani")
    notes = list(converter.parseData(xml, format="musicxml").parts[0].recurse().notes)
    assert notes, "timpani should play at least the cadences"
    assert {int(n.pitch.ps) % 12 for n in notes} <= allowed
    assert all(lo <= n.pitch.ps <= hi for n in notes)


def test_percussion_part_marks_every_fermata():
    res = arrange("bwv66.6", FULL, "3")
    xml = next(p["musicxml"] for p in res["parts"] if p["instrument"] == "Percussion")
    assert "<unpitched>" in xml and re.search(r"<notehead[^>]*>x</notehead>", xml)
    part = converter.parseData(xml, format="musicxml").parts[0]
    cym = [n for n in part.recurse().notes if n.notehead == "x"]
    fermatas = [n for n in part.recurse().notesAndRests
                if any(type(e).__name__ == "Fermata" for e in n.expressions)]
    assert len(cym) == sum(1 for n in fermatas if not n.isRest) and len(cym) >= 4
    assert "<tremolo" in xml          # rolls at grade 3


def test_percussion_is_listed_but_does_not_take_a_voice():
    res = arrange("bwv66.6", {**LOPSIDED, "Percussion": 2, "Timpani": 1}, "1", with_parts=False)
    assert res["voices"]["Percussion"] == ["Timpani", "Percussion"]
    sung = [i for v in ("Soprano", "Alto", "Tenor", "Bass") for i in res["voices"][v]]
    assert "Percussion" not in sung and "Timpani" not in sung


def test_percussion_alone_errors():
    with pytest.raises(ArrangeError, match="at least one instrument"):
        arrange("bwv66.6", {"Percussion": 4, "Timpani": 1}, "1")


@pytest.mark.parametrize("cid", ["bwv66.6", "bwv329", "bwv269"])
def test_mallets_double_a_line_someone_else_plays(cid):
    roster = {"Bells": 1, "Xylophone": 1, "Violin": 14, "Viola": 5, "Cello": 6, "Double Bass": 2}
    res = arrange(cid, roster, "1", with_parts=False)
    for v in ("Soprano", "Alto", "Tenor", "Bass"):
        players = res["voices"][v]
        assert players, v
        assert any(INSTRUMENTS[i].family != "mallets" for i in players), (v, players)
    assert "Violin" in res["voices"]["Soprano"]
