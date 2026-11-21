"""Start from a key: parse it, rank chorales for it, and arrange in exactly that key."""
import pytest
from music21 import pitch

from roster_chorale import chorale as chmod
from roster_chorale.arrange import (ArrangeError, _notes_data, arrange, grade_cfg, parse_key, rank_chorales,
                                    rhythm_id)
from roster_chorale.instruments import VOICES

from conftest import LOPSIDED, SAMPLE_CHORALES

STRINGS = {"Violin": 12, "Viola": 4, "Cello": 5, "Double Bass": 2}


@pytest.mark.parametrize("text,expected", [
    ("Bb major", ("B-", "major")), ("B- major", ("B-", "major")), ("bb major", ("B-", "major")),
    ("F# minor", ("F#", "minor")), ("g minor", ("G", "minor")), ("A# major", ("B-", "major")),
    ("Eb", ("E-", "major")), ("C# minor", ("C#", "minor")),
])
def test_parse_key(text, expected):
    assert parse_key(text) == expected


@pytest.mark.parametrize("text", ["", "H major", "Bb dorian"])
def test_parse_key_rejects_nonsense(text):
    with pytest.raises(ArrangeError):
        parse_key(text)


@pytest.mark.parametrize("roster,key,grade", [
    (LOPSIDED, "Bb major", "1"), (LOPSIDED, "G minor", "1"), (LOPSIDED, "Eb major", "3"),
    (STRINGS, "D major", "1"), ({"Clarinet": 7}, "F major", "2"),
])
def test_shortlist_is_in_the_right_mode_and_cleanest_first(roster, key, grade):
    res = rank_chorales(roster, grade, key, limit=5)
    _, mode = parse_key(key)
    catalog = {c["id"]: c for c in chmod.catalog()}
    assert 1 <= len(res["shortlist"]) <= 5
    assert res["candidates"] > 100
    for c in res["shortlist"]:
        assert catalog[c["id"]]["key"].endswith(mode)
    residuals = [c["out_of_range"] for c in res["shortlist"]]
    assert residuals == sorted(residuals)


@pytest.mark.parametrize("key,grade", [("Bb major", "1"), ("G minor", "2"), ("D major", "3")])
def test_shortlist_matches_the_real_arrangement(key, grade):
    """The fit shown in the shortlist is what you get when you arrange that chorale."""
    top = rank_chorales(LOPSIDED, grade, key, limit=1)["shortlist"][0]
    res = arrange(top["id"], LOPSIDED, grade, with_parts=False, key_choice=key)
    assert res["key"]["concert"] == parse_key_name(key)
    assert res["key"]["out_of_range"] == top["out_of_range"]
    assert res["key"]["phrase_shifts"] == top["phrase_shifts"]


def parse_key_name(text):
    t, m = parse_key(text)
    return f"{t.replace('-', 'b')} {m}"


@pytest.mark.parametrize("key", ["D minor", "B minor", "E- minor"])
def test_arrange_uses_the_chosen_key_exactly(key):
    res = arrange("bwv66.6", LOPSIDED, "1", with_parts=False, key_choice=key)
    assert res["key"]["concert"] == parse_key_name(key)
    assert res["key_choice"] == "chosen"
    assert "your choice" in res["summary"]


def test_chosen_key_off_the_grade_list_warns():
    res = arrange("bwv66.6", LOPSIDED, "1", with_parts=False, key_choice="B minor")
    assert any("isn't on the Grade 1 wind and brass key list" in w for w in res["warnings"])


def test_chosen_key_must_match_the_chorale_mode():
    with pytest.raises(ArrangeError, match="minor"):
        arrange("bwv66.6", LOPSIDED, "1", key_choice="Bb major")


def test_best_fit_mode_is_unchanged():
    res = arrange("bwv66.6", LOPSIDED, "1", with_parts=False)
    assert res["key_choice"] == "best_fit"


def test_chosen_key_parts_are_written_in_that_key():
    res = arrange("bwv66.6", {"Flute": 2, "Trumpet": 2, "Trombone": 2}, "2", key_choice="C minor")
    flute = next(p["musicxml"] for p in res["parts"] if p["instrument"] == "Flute")
    trumpet = next(p["musicxml"] for p in res["parts"] if p["instrument"] == "Trumpet")
    assert "<fifths>-3</fifths>" in flute        # C minor: 3 flats
    assert "<fifths>-1</fifths>" in trumpet      # Bb trumpet reads D minor: 1 flat


@pytest.mark.parametrize("cid", SAMPLE_CHORALES[:5])
@pytest.mark.parametrize("grade", ["1", "2", "3"])
def test_precomputed_notes_match_the_corpus(cid, grade):
    """data/chorale_notes.json must match what loading the chorale gives (rebuild it if not)."""
    rule = grade_cfg(grade)["rhythm"]
    stored = _notes_data()[rhythm_id(rule)][cid]
    ch, _ = chmod.simplify(chmod.load(cid), rule)
    assert stored["tonic"] == ch.key.tonic.name
    for v in VOICES:
        live = [x for b in ch.voices[v] for e in b.events if not e.is_rest for x in (int(e.pitch.ps), e.phrase)]
        assert stored["v"][v] == live, (cid, grade, v)


def test_every_catalog_chorale_has_precomputed_notes():
    ids = {c["id"] for c in chmod.catalog()}
    for rid, entries in _notes_data().items():
        assert set(entries) == ids, rid


def test_shortlist_rejects_percussion_only():
    with pytest.raises(ArrangeError):
        rank_chorales({"Percussion": 3}, "1", "Bb major")
