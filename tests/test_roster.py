import io

import pytest
from openpyxl import Workbook

from roster_chorale.instruments import canonical
from roster_chorale.roster import RosterError, parse_rows, parse_text, parse_upload, rows_from_text

from conftest import LOPSIDED

# ---------------------------------------------------------------------------
# Alias mapping: 25+ messy names
# ---------------------------------------------------------------------------
ALIASES = {
    "Bb Clar": "Clarinet", "Clarinet 1": "Clarinet", "B♭ Clarinet": "Clarinet", "clar 2": "Clarinet",
    "Clarinet II": "Clarinet", "Cl.": "Clarinet", "Clarinets": "Clarinet",
    "Bass Clar": "Bass Clarinet", "bass clarinet": "Bass Clarinet", "B. Cl": "Bass Clarinet",
    "Alto Sax 1": "Alto Sax", "Alto Saxophone": "Alto Sax", "A. Sax 2": "Alto Sax", "Eb Alto Sax": "Alto Sax",
    "Tenor Sax": "Tenor Sax", "Tenor Saxophone": "Tenor Sax",
    "Bari": "Bari Sax", "Bari Sax": "Bari Sax", "Baritone Saxophone": "Bari Sax",
    "Tbn": "Trombone", "Trombone 1": "Trombone", "Bones": "Trombone",
    "Flute 2": "Flute", "Fl.": "Flute", "FLUTE": "Flute", "Flute2": "Flute", "Piccolo": "Flute",
    "Tpt": "Trumpet", "Trumpet in Bb": "Trumpet", "Bb Trumpet": "Trumpet", "trumpet 2": "Trumpet", "Cornet": "Trumpet",
    "French Horn": "Horn", "Horn in F": "Horn", "Hn": "Horn", "Mellophone": "Horn",
    "Euph": "Euphonium", "Baritone": "Euphonium", "Baritone Horn": "Euphonium",
    "Tuba": "Tuba", "Sousaphone": "Tuba", "Oboe": "Oboe", "Bsn": "Bassoon",
}


@pytest.mark.parametrize("raw,std", ALIASES.items())
def test_alias(raw, std):
    assert canonical(raw) == std


def test_alias_count_is_at_least_25():
    assert len(ALIASES) >= 25


@pytest.mark.parametrize("raw", ["Percussion", "Piano", "Snare", "Violin", "", "   "])
def test_unrecognised_names_return_none(raw):
    assert canonical(raw) is None


# ---------------------------------------------------------------------------
# Roster counting
# ---------------------------------------------------------------------------
def test_sample_xlsx_per_student(sample_xlsx):
    r = parse_upload(sample_xlsx.name, sample_xlsx.read_bytes())
    assert r.ordered_counts() == LOPSIDED
    assert r.unrecognized == {"Percussion": 2, "Piano": 1}
    assert r.layout == "per-student"
    assert r.instrument_column == "Instrument"     # not the first column


def _xlsx(rows) -> bytes:
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_xlsx_counts_format():
    data = _xlsx([["Instrument", "Count"], ["Flute", 9], ["Bb Clar", 5], ["Tbn", 2], ["Tuba", 1]])
    r = parse_upload("band.xlsx", data)
    assert r.layout == "counts"
    assert r.ordered_counts() == {"Flute": 9, "Clarinet": 5, "Trombone": 2, "Tuba": 1}


def test_csv_per_student():
    csv_text = "Student,Grade,Section\nAva,6,Flute\nLiam,7,Bb Clar\nSam,8,Bari\nMia,6,Flute 2\n"
    r = parse_upload("band.csv", csv_text.encode())
    assert r.ordered_counts() == {"Flute": 2, "Clarinet": 1, "Bari Sax": 1}


def test_csv_counts_format_without_header():
    r = parse_upload("band.csv", b"Flute,9\nTrumpet,4\nTuba,1\n")
    assert r.layout == "counts"
    assert r.ordered_counts() == {"Flute": 9, "Trumpet": 4, "Tuba": 1}


def test_pasted_tsv_per_student_with_blank_lines_and_header():
    text = "First\tLast\tInstrument\n\nAva\tM\tFlute\r\nLiam\tB\tBb Clar\n\n\nSam\tY\tBari\n"
    r = parse_text(text)
    assert r.ordered_counts() == {"Flute": 1, "Clarinet": 1, "Bari Sax": 1}


def test_pasted_tsv_counts():
    r = parse_text("Instrument\t#\nFlute\t9\nAlto Sax 1\t3\n")
    assert r.ordered_counts() == {"Flute": 9, "Alto Sax": 3}


def test_pasted_rows_without_header_find_the_instrument_column():
    r = parse_text("Ava\t7\tFlute\nLiam\t6\tTpt\nZoe\t8\tTpt\n")
    assert r.ordered_counts() == {"Flute": 1, "Trumpet": 2}


def test_locker_number_column_is_not_a_count(sample_xlsx):
    # the sample has a "Locker #" column full of numbers; it must not be read as counts
    r = parse_upload(sample_xlsx.name, sample_xlsx.read_bytes())
    assert sum(r.counts.values()) == 28


def test_same_roster_from_xlsx_and_paste(sample_xlsx):
    from openpyxl import load_workbook
    ws = load_workbook(sample_xlsx).active
    tsv = "\n".join("\t".join("" if v is None else str(v) for v in row) for row in ws.iter_rows(values_only=True))
    assert parse_text(tsv).ordered_counts() == parse_upload(sample_xlsx.name, sample_xlsx.read_bytes()).ordered_counts()


@pytest.mark.parametrize("text", ["", "   \n\n  ", "Name\tInstrument\n"])
def test_empty_roster_errors(text):
    with pytest.raises(RosterError):
        parse_text(text)


def test_no_instrument_column_errors():
    with pytest.raises(RosterError, match="instrument column"):
        parse_text("Name\tAge\nAva\t12\nLiam\t13\n")


def test_unreadable_xlsx_errors():
    with pytest.raises(RosterError):
        parse_upload("broken.xlsx", b"PK\x03\x04 this is not really a spreadsheet")


def test_empty_file_errors():
    with pytest.raises(RosterError):
        parse_upload("x.csv", b"")


def test_sniffs_semicolons():
    assert rows_from_text("Instrument;Count\nFlute;3\n")[1] == ["Flute", "3"]


def test_parse_rows_lists_unrecognised():
    r = parse_rows([["Instrument"], ["Flute"], ["Kazoo"], ["Kazoo"]])
    assert r.unrecognized == {"Kazoo": 2}
