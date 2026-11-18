"""Core arranging logic: ranges, key search, voices, rhythm, phrases, and concert-pitch correctness."""
import pytest
from music21 import converter, pitch

from roster_chorale import chorale as chmod
from roster_chorale.arrange import (ArrangeError, arrange, build_part, fit, grade_cfg, key_interval,
                                    ranges_midi, search_keys, to_musicxml, voice_notes)
from roster_chorale.instruments import INSTRUMENTS, VOICES

from conftest import LOPSIDED, SAMPLE_CHORALES


def midi(name: str) -> int:
    return int(pitch.Pitch(name).ps)


# ---------------------------------------------------------------------------
# Written-pitch range checks for transposing instruments
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("inst,concert,written", [
    ("Clarinet", "B-3", "C4"),        # Bb clarinet reads a major 2nd up
    ("Trumpet", "B-3", "C4"),
    ("Alto Sax", "E-4", "C5"),        # alto sax reads a major 6th up
    ("Horn", "F3", "C4"),             # horn in F reads a perfect 5th up
    ("Bari Sax", "E-2", "C4"),        # bari reads an octave + major 6th up
    ("Tenor Sax", "B-2", "C4"),
    ("Bass Clarinet", "B-2", "C4"),
    ("Flute", "C5", "C5"),
    ("Tuba", "B-1", "B-1"),
])
def test_written_offsets(inst, concert, written):
    assert midi(concert) + INSTRUMENTS[inst].written_semitones == midi(written)


@pytest.mark.parametrize("inst,concert_in,concert_out", [
    # grade-1 written ranges: Clarinet G3-C5, Trumpet Bb3-C5, Alto Sax D4-C6, Horn G3-C5, Bari D4-C6
    ("Clarinet", "B-4", "C5"),     # concert Bb4 = written C5 (top of range); concert C5 = written D5 (out)
    ("Trumpet", "B-4", "C5"),
    ("Alto Sax", "E-5", "F5"),     # concert Eb5 = written C6 (top); concert F5 = written D6 (out)
    ("Horn", "F4", "G4"),          # concert F4 = written C5 (top); concert G4 = written D5 (out)
    ("Bari Sax", "E-4", "F4"),     # concert Eb4 = written C6 (top); concert F4 = written D6 (out)
])
def test_range_check_is_in_written_pitch(inst, concert_in, concert_out):
    lo, hi = ranges_midi(grade_cfg("1"))[inst]
    base = INSTRUMENTS[inst].written_semitones
    ok = [chmod_note(concert_in)]
    bad = [chmod_note(concert_out)]
    assert fit(ok, base, lo, hi).pre_out == 0
    f = fit(bad, base, lo, hi)
    assert f.octave == -1 and f.pre_out == 0   # fixed by moving the whole (one-note) part down


def chmod_note(name: str, phrase: int = 0):
    from roster_chorale.arrange import NoteRef
    return NoteRef(1, "1", midi(name), phrase, name)


# ---------------------------------------------------------------------------
# Key search
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("cid", ["bwv66.6", "bwv269", "bwv86.6"])
@pytest.mark.parametrize("roster", [{"Trumpet": 4}, {"Alto Sax": 3}, {"Flute": 2, "Tuba": 1}, LOPSIDED])
def test_key_search_minimises_out_of_range_notes(cid, roster):
    cfg = grade_cfg("1")
    ch, _ = chmod.simplify(chmod.load(cid), cfg["rhythm"])
    results = search_keys(ch, roster, cfg)
    best = results[0]
    assert best.residual == min(r.residual for r in results)
    # every allowed key was considered
    mode = ch.key.mode
    assert len(results) == len(cfg["keys"][mode])


def test_key_search_picks_the_key_with_fewest_out_of_range_notes_single_instrument():
    # One trumpet section on the melody: the chosen key leaves the fewest notes outside Bb3-C5.
    cfg = grade_cfg("1")
    ch, _ = chmod.simplify(chmod.load("bwv269"), cfg["rhythm"])
    results = search_keys(ch, {"Trumpet": 4}, cfg)
    sop = voice_notes(ch, "S")
    lo, hi = ranges_midi(cfg)["Trumpet"]
    raw = {}
    for r in results:
        base = r.semitones + INSTRUMENTS["Trumpet"].written_semitones
        raw[r.name] = min(sum(1 for n in sop if not lo <= n.midi + base + 12 * o <= hi) for o in (-1, 0, 1))
    assert raw[results[0].name] == min(raw.values())


@pytest.mark.parametrize("grade", ["0.5", "1", "2", "3"])
def test_chosen_key_is_allowed_for_grade(grade):
    res = arrange("bwv66.6", LOPSIDED, grade, with_parts=False)
    cfg = grade_cfg(grade)
    tonic, mode = res["key"]["concert"].split()
    tonic = tonic[0] + tonic[1:].replace("b", "-")
    assert pitch.Pitch(tonic).name in [pitch.Pitch(t).name for t in cfg["keys"][mode]]


def test_key_interval_moves_at_most_a_tritone():
    for t in ["C", "D-", "D", "E-", "E", "F", "F#", "G", "A-", "A", "B-", "B"]:
        assert -6 <= key_interval("F#", t).semitones <= 5


# ---------------------------------------------------------------------------
# Voices
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("cid", SAMPLE_CHORALES[:6])
@pytest.mark.parametrize("roster", [
    LOPSIDED,
    {"Flute": 3, "Clarinet": 3, "Alto Sax": 2, "Trumpet": 2},          # no bass instrument at all
    {"Clarinet": 6, "Trombone": 2, "Horn": 1, "Bari Sax": 1},
    {"Flute": 1, "Oboe": 1, "Bassoon": 1, "Euphonium": 1, "Tuba": 1},
])
def test_every_voice_covered(cid, roster):
    res = arrange(cid, roster, "1", with_parts=False)
    assert all(res["voices"][v] for v in ("Soprano", "Alto", "Tenor", "Bass"))


def test_no_bass_instrument_warns():
    res = arrange("bwv66.6", {"Flute": 3, "Clarinet": 3, "Alto Sax": 2, "Trumpet": 2}, "1", with_parts=False)
    assert any("No low instrument" in w for w in res["warnings"])


def test_bass_line_goes_to_low_instruments_when_present():
    res = arrange("bwv66.6", LOPSIDED, "1", with_parts=False)
    bass = res["voices"]["Bass"]
    assert "Tuba" in bass and all(INSTRUMENTS[i].low for i in bass)


def test_one_instrument_type_plays_melody_with_warning():
    res = arrange("bwv66.6", {"Clarinet": 7}, "1", with_parts=False)
    assert res["voices"]["Soprano"] == ["Clarinet"]
    assert any("Only one instrument type" in w for w in res["warnings"])


def test_two_instrument_types_cover_soprano_and_bass():
    res = arrange("bwv66.6", {"Flute": 4, "Trombone": 2}, "1", with_parts=False)
    assert res["voices"]["Soprano"] == ["Flute"] and res["voices"]["Bass"] == ["Trombone"]
    assert any("left out" in w for w in res["warnings"])


def test_upper_voices_never_sound_below_the_bass():
    for cid in SAMPLE_CHORALES[:6]:
        res = arrange(cid, LOPSIDED, "1", with_parts=False)
        for a in res["assignment"]:
            if a["voice"] in "SAT":
                assert not (a["voice"] == "A" and a["part_octave"] <= -2), (cid, a["instrument"])
                assert not (a["voice"] == "T" and a["part_octave"] <= -1), (cid, a["instrument"])


def test_bad_inputs():
    with pytest.raises(ArrangeError):
        arrange("bwv66.6", {"Kazoo": 3}, "1")
    with pytest.raises(ArrangeError):
        arrange("bwv66.6", {}, "1")
    with pytest.raises(ArrangeError):
        arrange("nope", LOPSIDED, "1")
    with pytest.raises(ArrangeError):
        arrange("bwv66.6", LOPSIDED, "7")


# ---------------------------------------------------------------------------
# Rhythm simplification
# ---------------------------------------------------------------------------
def bar_lengths(ch):
    return {v: [round(b.length, 6) for b in bars] for v, bars in ch.voices.items()}


@pytest.mark.parametrize("cid", SAMPLE_CHORALES)
def test_grade1_leaves_no_eighths_and_keeps_bar_lengths(cid):
    original = chmod.load(cid)
    simple, report = chmod.simplify(original, grade_cfg("1")["rhythm"])
    assert bar_lengths(simple) == bar_lengths(original)
    for v, bars in simple.voices.items():
        for b in bars:
            for e in b.events:
                start = b.padding + e.offset
                if abs(start - round(start)) < 1e-6:   # every event starts on a beat...
                    assert e.dur >= 1 - 1e-6 or b.length < 1, (cid, v, b.number, e.dur)   # ...and is a quarter or longer
                else:
                    pytest.fail(f"{cid} {v} m.{b.number}: off-beat event survived at {start}")
                assert e.tie is None, "grade 1 removes ties"


@pytest.mark.parametrize("cid", SAMPLE_CHORALES)
def test_grade2_leaves_no_sixteenths_and_keeps_bar_lengths(cid):
    original = chmod.load(cid)
    simple, _ = chmod.simplify(original, grade_cfg("2")["rhythm"])
    assert bar_lengths(simple) == bar_lengths(original)
    for bars in simple.voices.values():
        for b in bars:
            for e in b.events:
                assert e.dur >= 0.5 - 1e-6


def test_grade3_is_as_written():
    original = chmod.load("bwv66.6")
    simple, report = chmod.simplify(original, grade_cfg("3")["rhythm"])
    assert report == []
    assert [(e.dur, e.pitch) for b in simple.voices["T"] for e in b.events] == \
           [(e.dur, e.pitch) for b in original.voices["T"] for e in b.events]


def test_grade3_brings_back_the_eighths_grade1_removed():
    g1 = arrange("bwv66.6", LOPSIDED, "1")
    g3 = arrange("bwv66.6", LOPSIDED, "3")
    assert len(g1["rhythm"]) > 0 and g3["rhythm"] == []
    assert sum(len(a["phrase_shifts"]) for a in g3["assignment"]) <= sum(len(a["phrase_shifts"]) for a in g1["assignment"])
    eighths = lambda res: sum(p["musicxml"].count("<type>eighth</type>") for p in res["parts"])
    assert eighths(g1) == 0 and eighths(g3) > 0


def test_built_grade1_parts_have_no_eighth_notes():
    res = arrange("bwv66.6", LOPSIDED, "1")
    for p in res["parts"]:
        part = converter.parseData(p["musicxml"], format="musicxml").parts[0]
        for n in part.recurse().notes:
            assert n.quarterLength >= 1, (p["instrument"], n.measureNumber, n.quarterLength)


# ---------------------------------------------------------------------------
# Phrases and octave shifts
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("cid", SAMPLE_CHORALES[:8])
def test_phrases_split_only_after_fermatas(cid):
    ch = chmod.load(cid)
    for v, bars in ch.voices.items():
        evs = [e for b in bars for e in b.events]
        for prev, cur in zip(evs, evs[1:]):
            if cur.phrase != prev.phrase:
                assert prev.fermata, f"{cid} {v}: phrase changes after a non-fermata event"
                assert cur.phrase == prev.phrase + 1


@pytest.mark.parametrize("grade", ["0.5", "1", "2"])
@pytest.mark.parametrize("cid", SAMPLE_CHORALES[:8])
def test_octave_shifts_change_only_at_fermata_boundaries(cid, grade):
    cfg = grade_cfg(grade)
    ch, _ = chmod.simplify(chmod.load(cid), cfg["rhythm"])
    best = search_keys(ch, LOPSIDED, cfg)[0]
    for inst, v in best.assign.items():
        f = best.fits[inst]
        evs = [e for b in ch.voices[v] for e in b.events]
        for prev, cur in zip(evs, evs[1:]):
            if f.shift_for(cur.phrase) != f.shift_for(prev.phrase):
                assert prev.fermata and cur.phrase != prev.phrase, (cid, inst)
        assert len(f.tacet) <= 0.5 * (max(e.phrase for e in evs) + 1) + 1e-9 or f.tacet == set()


# ---------------------------------------------------------------------------
# Musical correctness: every written part plays the right concert pitches
# ---------------------------------------------------------------------------
def expected_concert(ch, v, f, key_iv):
    out, t = {}, 0.0
    for b in ch.voices[v]:
        for e in b.events:
            if not e.is_rest and e.phrase not in f.tacet and e.tie not in ("stop", "continue"):
                out[round(t + e.offset, 4)] = int(e.pitch.transpose(key_iv).ps) + 12 * f.shift_for(e.phrase)
        t += b.length
    return out


@pytest.mark.parametrize("grade", ["1", "3"])
@pytest.mark.parametrize("cid", SAMPLE_CHORALES[:6])
def test_written_parts_transpose_back_to_the_assigned_voice(cid, grade):
    cfg = grade_cfg(grade)
    ch, _ = chmod.simplify(chmod.load(cid), cfg["rhythm"])
    best = search_keys(ch, LOPSIDED, cfg)[0]
    key_iv = key_interval(ch.key.tonic.name, best.tonic)
    for name, v in best.assign.items():
        inst = INSTRUMENTS[name]
        xml = to_musicxml(build_part(ch, v, inst, best, best.fits[name], cfg["label"]))
        part = converter.parseData(xml, format="musicxml").parts[0]
        sounding = inst.factory().transposition   # written -> sounding
        actual = {}
        for n in part.flatten().notes:
            if n.tie is not None and n.tie.type in ("stop", "continue"):
                continue
            p = n.pitch.transpose(sounding) if sounding is not None else n.pitch
            actual[round(float(n.offset), 4)] = int(p.ps)
        assert actual == expected_concert(ch, v, best.fits[name], key_iv), (cid, grade, name, v)


@pytest.mark.parametrize("grade", ["0.5", "1", "2", "3"])
def test_no_printed_note_out_of_range_for_sample_band(grade):
    res = arrange("bwv66.6", LOPSIDED, grade)
    rng = ranges_midi(grade_cfg(grade))
    for p in res["parts"]:
        lo, hi = rng[p["instrument"]]
        part = converter.parseData(p["musicxml"], format="musicxml").parts[0]
        for n in part.recurse().notes:
            assert lo <= n.pitch.ps <= hi, (grade, p["instrument"], n.nameWithOctave)
