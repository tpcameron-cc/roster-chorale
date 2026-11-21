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
    # fewest notes out of range (a part pushed below the bass counts as 5 such notes)
    assert best.rank[0] == min(r.rank[0] for r in results)
    assert best.inverted == 0 or all(r.inverted for r in results)
    # every allowed key was considered
    mode = ch.key.mode
    assert len(results) == len(cfg["keys"][mode])


def test_key_search_picks_the_key_with_fewest_out_of_range_notes_single_instrument():
    # One trumpet on the melody: the chosen key leaves the fewest notes outside Bb3-C5 once
    # octave and phrase shifts are applied ("fewest notes still out of range wins").
    cfg = grade_cfg("1")
    ch, _ = chmod.simplify(chmod.load("bwv269"), cfg["rhythm"])
    results = search_keys(ch, {"Trumpet": 1}, cfg)
    assert results[0].residual == min(r.residual for r in results)
    lo, hi = ranges_midi(cfg)["Trumpet"]
    best = results[0]
    f = best.fits["Trumpet"]
    base = best.semitones + INSTRUMENTS["Trumpet"].written_semitones
    printed_out = sum(1 for n in voice_notes(ch, "S") if not lo <= n.midi + base + 12 * f.shift_for(n.phrase) <= hi)
    assert printed_out == best.residual


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


FOUR_LINES = ("Soprano", "Alto", "Tenor", "Bass")


@pytest.mark.parametrize("grade", ["1", "3"])
def test_one_section_splits_to_cover_all_four_lines(grade):
    res = arrange("bwv66.6", {"Clarinet": 7}, grade)
    assert [res["voices"][v] for v in FOUR_LINES] == [["Clarinet 1"], ["Clarinet 2"], ["Clarinet 3"], ["Clarinet 4"]]
    assert sum(a["players"] for a in res["assignment"]) == 7
    assert [p["part_name"] for p in res["parts"]] == [f"Clarinet {n} in Bb" for n in range(1, 5)]
    assert len({p["filename"] for p in res["parts"]}) == 4
    assert any("split into 4 parts" in n for n in res["notes"])
    assert not any("left out" in w for w in res["warnings"])


@pytest.mark.parametrize("roster,expect_bass", [
    ({"Flute": 4, "Trombone": 2}, "Trombone"),
    ({"Flute": 3, "Tuba": 1}, "Tuba"),
    ({"Violin": 5, "Cello": 2}, "Cello"),
    ({"Clarinet": 4, "Bari Sax": 2}, "Bari Sax"),
    ({"Trumpet": 3, "Horn": 1}, "Horn"),
])
@pytest.mark.parametrize("grade", ["1", "3"])
def test_small_groups_cover_every_line_and_keep_the_bass_low(roster, expect_bass, grade):
    res = arrange("bwv66.6", roster, grade, with_parts=False)
    assert all(res["voices"][v] for v in FOUR_LINES), res["voices"]
    assert all(p.startswith(expect_bass) for p in res["voices"]["Bass"]), res["voices"]
    by_inst: dict[str, int] = {}
    for a in res["assignment"]:
        by_inst[a["instrument"]] = by_inst.get(a["instrument"], 0) + a["players"]
    assert by_inst == roster


@pytest.mark.parametrize("roster,lines", [({"Flute": 1}, 1), ({"Flute": 2}, 2), ({"Flute": 2, "Tuba": 1}, 3)])
def test_fewer_than_four_players_cover_as_many_lines_as_possible(roster, lines):
    res = arrange("bwv66.6", roster, "1", with_parts=False)
    covered = [v for v in FOUR_LINES if res["voices"][v]]
    assert len(covered) == lines and "Soprano" in covered
    if lines >= 2:
        assert "Bass" in covered
    assert any("left out" in w for w in res["warnings"])


def test_big_ensembles_are_not_split():
    res = arrange("bwv66.6", LOPSIDED, "1", with_parts=False)
    assert all(a["part"] == a["instrument"] for a in res["assignment"])
    assert res["notes"] == []


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


ORCHESTRA = {"Flute": 2, "Oboe": 2, "Clarinet": 2, "Bassoon": 2, "Horn": 4, "Trumpet": 2, "Trombone": 3, "Tuba": 1,
             "Bells": 1, "Xylophone": 1, "Marimba": 1, "Vibraphone": 1,
             "Violin": 16, "Viola": 6, "Cello": 5, "Double Bass": 3}


@pytest.mark.parametrize("roster", [LOPSIDED, ORCHESTRA, {"Clarinet": 7}, {"Flute": 4, "Trombone": 2}],
                         ids=["band", "orchestra", "clarinets-divisi", "flutes-trombones-divisi"])
@pytest.mark.parametrize("grade", ["1", "3"])
@pytest.mark.parametrize("cid", SAMPLE_CHORALES[:6])
def test_written_parts_transpose_back_to_the_assigned_voice(cid, grade, roster):
    cfg = grade_cfg(grade)
    ch, _ = chmod.simplify(chmod.load(cid), cfg["rhythm"])
    best = search_keys(ch, roster, cfg)[0]
    key_iv = key_interval(ch.key.tonic.name, best.tonic)
    for name, v in best.assign.items():
        inst = INSTRUMENTS[best.inst(name)]
        xml = to_musicxml(build_part(ch, v, inst, best, best.fits[name], cfg["label"]))
        part = converter.parseData(xml, format="musicxml").parts[0]
        sounding = inst.written_interval.reverse() if inst.written_semitones else None   # written -> sounding
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


@pytest.mark.parametrize("roster", [{"Clarinet": 7}, {"Flute": 4, "Trombone": 2}, {"Violin": 5, "Cello": 2},
                                    {"Trumpet": 4}, LOPSIDED])
@pytest.mark.parametrize("grade", ["1", "3"])
@pytest.mark.parametrize("cid", SAMPLE_CHORALES[:6])
def test_lines_stay_in_order(cid, grade, roster):
    """Nothing sounds above the melody line or below the bass line. Small groups (one or two kinds
    of instrument) can't always manage that inside narrow grade ranges; then the page must say so."""
    from roster_chorale.arrange import inversions, line_shares, over_melody
    from roster_chorale.instruments import VOICES
    cfg = grade_cfg(grade)
    ch, _ = chmod.simplify(chmod.load(cid), cfg["rhythm"])
    best = search_keys(ch, roster, cfg)[0]
    if line_shares(roster, cfg) is None:
        means = {v: sum(n.midi for n in voice_notes(ch, v)) / len(voice_notes(ch, v)) for v in VOICES}
        fp = {(p, v): best.fits[p] for p, v in best.assign.items()}
        assert not over_melody(best.assign, fp, means)
        assert not inversions(best.assign, fp, means)
        return
    if _melody_bass_violations(ch, best):
        res = arrange(cid, roster, grade, with_parts=False)
        assert res["warnings"], (cid, grade, roster)


def _melody_bass_violations(ch, best) -> int:
    """Phrases where some line sounds above the melody or below the bass."""
    pm = {}
    for v in "SATB":
        acc = {}
        for n in voice_notes(ch, v):
            acc.setdefault(n.phrase, []).append(n.midi)
        pm[v] = {ph: sum(m) / len(m) for ph, m in acc.items()}
    lvl = {}
    for p, v in best.assign.items():
        lvl.setdefault(v, []).append(best.fits[p])
    bad = 0
    for ph in pm["S"]:
        snd = {v: [pm[v][ph] + 12 * f.shift_for(ph) for f in fs] for v, fs in lvl.items() if ph in pm[v]}
        bad += "S" in snd and any(max(x) > min(snd["S"]) for v, x in snd.items() if v != "S")
        bad += "B" in snd and any(min(x) < max(snd["B"]) for v, x in snd.items() if v != "B")
    return bad


# ---------------------------------------------------------------------------
# "Split sections" mode
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("roster", [LOPSIDED, ORCHESTRA], ids=["band", "orchestra"])
@pytest.mark.parametrize("grade", ["1", "3"])
@pytest.mark.parametrize("cid", SAMPLE_CHORALES[:3])
def test_split_sections_mode(cid, grade, roster):
    res = arrange(cid, roster, grade, with_parts=False, parts_mode="multiple")
    by_inst: dict[str, list[dict]] = {}
    for a in res["assignment"]:
        by_inst.setdefault(a["instrument"], []).append(a)
    for inst, entries in by_inst.items():
        assert sum(e["players"] for e in entries) == roster[inst]
        info = INSTRUMENTS[inst]
        octaves = len(info.prefs) == 1 and info.low and roster[inst] >= 2   # tubas, basses: two parts in octaves
        expected = 1 if info.family == "mallets" else (2 if octaves else min(roster[inst], len(info.prefs), 3))
        assert len(entries) == expected, (inst, [e["part"] for e in entries])
        if octaves:
            assert {e["voice"] for e in entries} == {"B"}
        elif len(entries) > 1:
            lines = [e["voice"] for e in entries]
            assert len(set(lines)) == len(lines), (inst, lines)            # different lines
            assert all(v in info.prefs for v in lines), (inst, lines)      # lines that suit the instrument
            assert [e["part"] for e in entries] == [f"{inst} {n}" for n in range(1, len(entries) + 1)]
    assert all(res["voices"][v] for v in FOUR_LINES)
    assert any("split into numbered parts" in n for n in res["notes"])


def test_single_mode_keeps_one_part_per_instrument():
    res = arrange("bwv66.6", ORCHESTRA, "1", with_parts=False, parts_mode="single")
    assert all(a["part"] == a["instrument"] for a in res["assignment"])


@pytest.mark.parametrize("cid", SAMPLE_CHORALES[:3])
def test_split_parts_transpose_back_correctly(cid):
    cfg = grade_cfg("3")
    ch, _ = chmod.simplify(chmod.load(cid), cfg["rhythm"])
    best = search_keys(ch, LOPSIDED, cfg, "multiple")[0]
    key_iv = key_interval(ch.key.tonic.name, best.tonic)
    for name, v in best.assign.items():
        inst = INSTRUMENTS[best.inst(name)]
        xml = to_musicxml(build_part(ch, v, inst, best, best.fits[name], cfg["label"]))
        part = converter.parseData(xml, format="musicxml").parts[0]
        sounding = inst.written_interval.reverse() if inst.written_semitones else None
        actual = {}
        for n in part.flatten().notes:
            if n.tie is not None and n.tie.type in ("stop", "continue"):
                continue
            p = n.pitch.transpose(sounding) if sounding is not None else n.pitch
            actual[round(float(n.offset), 4)] = int(p.ps)
        assert actual == expected_concert(ch, v, best.fits[name], key_iv), (cid, name, v)


def test_unknown_parts_mode_errors():
    with pytest.raises(ArrangeError):
        arrange("bwv66.6", LOPSIDED, "1", parts_mode="lots")

# ---------------------------------------------------------------------------
# Split sections: low one-line sections divide in octaves
# ---------------------------------------------------------------------------
LOW_BRASS = {"Flute": 6, "Clarinet": 5, "Trumpet": 4, "Horn": 2, "Trombone": 3, "Euphonium": 3, "Tuba": 3}


@pytest.mark.parametrize("cid,grade", [("bwv66.6", "3"), ("bwv269", "3")])
def test_split_sections_divides_tubas_in_octaves(cid, grade):
    res = arrange(cid, LOW_BRASS, grade, parts_mode="multiple")
    tubas = {a["part"]: a for a in res["assignment"] if a["instrument"] == "Tuba"}
    assert set(tubas) == {"Tuba 1", "Tuba 2"}
    assert tubas["Tuba 1"]["players"] == 2 and tubas["Tuba 2"]["players"] == 1
    assert tubas["Tuba 1"]["voice"] == tubas["Tuba 2"]["voice"] == "B"
    assert tubas["Tuba 2"]["part_octave"] == tubas["Tuba 1"]["part_octave"] - 1
    assert all(not a["problems"] for a in tubas.values())
    assert any("Tuba 2 on bass (1 player, an octave lower)" in n for n in res["notes"])
    names = [p["part_name"] for p in res["parts"]]
    assert "Tuba 1" in names and "Tuba 2" in names
    # the two printed parts really are an octave apart
    parts = {p["part"]: converter.parseData(p["musicxml"], format="musicxml").parts[0] for p in res["parts"]}
    first = [n.pitch.ps for n in parts["Tuba 1"].recurse().notes]
    second = [n.pitch.ps for n in parts["Tuba 2"].recurse().notes]
    assert len(first) == len(second) and sum(1 for a, b in zip(first, second) if a - b == 12) > len(first) / 2


def test_one_tuba_or_single_mode_stays_one_part():
    one = arrange("bwv66.6", {**LOW_BRASS, "Tuba": 1}, "3", parts_mode="multiple", with_parts=False)
    single = arrange("bwv66.6", LOW_BRASS, "3", parts_mode="single", with_parts=False)
    for res in (one, single):
        assert [a["part"] for a in res["assignment"] if a["instrument"] == "Tuba"] == ["Tuba"]


# ---------------------------------------------------------------------------
# Small groups (brass choirs, tuba quartets): lines shared by register and players
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("roster,expected", [
    ({"Tuba": 4}, {"S": "Tuba", "A": "Tuba", "T": "Tuba", "B": "Tuba"}),
    ({"Euphonium": 4, "Tuba": 4}, {"S": "Euphonium", "A": "Euphonium", "T": "Tuba", "B": "Tuba"}),
    ({"Euphonium": 6, "Tuba": 2}, {"S": "Euphonium", "A": "Euphonium", "T": "Euphonium", "B": "Tuba"}),
    ({"Horn": 2, "Trombone": 2}, {"S": "Horn", "A": "Horn", "T": "Trombone", "B": "Trombone"}),
    ({"Trumpet": 4, "Trombone": 4, "Tuba": 2}, {"S": "Trumpet", "A": "Trumpet", "T": "Trombone", "B": "Tuba"}),
])
@pytest.mark.parametrize("mode", ["single", "multiple"])
def test_small_groups_share_the_lines_by_register(roster, expected, mode):
    res = arrange("bwv66.6", roster, "3", parts_mode=mode)
    got = {a["voice"]: a["instrument"] for a in res["assignment"]}
    assert got == expected
    assert sum(a["players"] for a in res["assignment"]) == sum(roster.values())
    # Bach's lines span wider than a brass choir's grade ranges: any compromise is reported
    assert all("don't leave room" in w or "upside down" in w for w in res["warnings"]), res["warnings"]
    assert len(res["parts"]) == len(res["assignment"])


def test_tuba_quartet_parts_are_numbered_top_down_and_in_range():
    res = arrange("bwv269", {"Tuba": 8}, "3", parts_mode="multiple")
    assert [(a["part"], a["voice"], a["players"]) for a in res["assignment"]] == [
        ("Tuba 1", "S", 2), ("Tuba 2", "A", 2), ("Tuba 3", "T", 2), ("Tuba 4", "B", 2)]
    assert all(not a["problems"] for a in res["assignment"])


def test_band_voicing_is_unchanged_by_small_group_sharing():
    from roster_chorale.arrange import line_shares
    assert line_shares(LOPSIDED, grade_cfg("1")) is None
    assert line_shares({"Trumpet": 2, "Horn": 1, "Trombone": 1, "Tuba": 1}, grade_cfg("1")) is None


@pytest.mark.parametrize("roster", [{"Euphonium": 4, "Tuba": 4}, {"Euphonium": 6, "Tuba": 2},
                                    {"Horn": 2, "Trombone": 2}, {"Trumpet": 4, "Trombone": 4, "Tuba": 2}])
def test_small_group_lines_stay_in_order_phrase_by_phrase(roster):
    cfg = grade_cfg("3")
    ch, _ = chmod.simplify(chmod.load("bwv66.6"), cfg["rhythm"])
    res = arrange("bwv66.6", roster, "3", parts_mode="multiple", with_parts=False)
    assert all(not a["problems"] for a in res["assignment"])
    level = {}
    for a in res["assignment"]:
        shifts = {s["phrase"] - 1: s["octaves"] for s in a["phrase_shifts"]}
        level.setdefault(a["voice"], []).append((a["part_octave"], shifts))
    means = {}
    for v in level:
        acc = {}
        for n in voice_notes(ch, v):
            acc.setdefault(n.phrase, []).append(n.midi)
        means[v] = {ph: sum(m) / len(m) for ph, m in acc.items()}
    crossed = 0      # phrases where a line sounds above the melody or below the bass
    for ph in means["S"]:
        snd = {v: [means[v][ph] + 12 * (o + s.get(ph, 0)) for o, s in level[v]] for v in level if ph in means[v]}
        crossed += any(max(x) > min(snd["S"]) for v, x in snd.items() if v != "S")
        crossed += any(min(x) < max(snd["B"]) for v, x in snd.items() if v != "B")
    # where the ranges really don't leave room, the page says so
    assert (crossed == 0) == (not any("don't leave room" in w for w in res["warnings"]))
