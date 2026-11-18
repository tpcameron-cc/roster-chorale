"""Write samples/lopsided-band.xlsx: a realistic one-row-per-student middle-school roster.

Invented first names. The instrument column is deliberately NOT the first column and the
names are messy, the way they come out of a real gradebook.
Expected clean roster: Flute 9, Clarinet 5, Bass Clarinet 1, Alto Sax 3, Tenor Sax 1,
Bari Sax 1, Trumpet 4, Horn 1, Trombone 2, Tuba 1 (plus 3 unrecognised: Percussion x2, Piano).
"""
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

ROWS = [
    ("Ava", "M.", 7, "Flute"), ("Mia", "R.", 6, "Flute 2"), ("Lily", "T.", 8, "flute 1"),
    ("Zoe", "K.", 6, "Fl."), ("Nora", "P.", 7, "Flute 2"), ("Ella", "S.", 6, "Flute"),
    ("Ruby", "D.", 8, "FLUTE"), ("Isla", "W.", 7, "Flute 1"), ("Maya", "H.", 6, "flute"),
    ("Liam", "B.", 7, "Bb Clar"), ("Noah", "C.", 6, "Clarinet 1"), ("Aria", "J.", 8, "Bb Clarinet"),
    ("Owen", "L.", 6, "clar 2"), ("Leah", "N.", 7, "Clarinet II"),
    ("Eli", "V.", 8, "Bass Clar"),
    ("Jack", "F.", 7, "Alto Sax 1"), ("Theo", "G.", 6, "Alto Saxophone"), ("Ivy", "Q.", 8, "A. Sax 2"),
    ("Max", "E.", 7, "Tenor Sax"),
    ("Sam", "Y.", 8, "Bari"),
    ("Leo", "A.", 7, "Trumpet 1"), ("Finn", "O.", 6, "Tpt"), ("Cora", "U.", 8, "Bb Trumpet"), ("Jude", "I.", 6, "trumpet 2"),
    ("Hazel", "Z.", 7, "French Horn"),
    ("Milo", "X.", 8, "Tbn"), ("Ezra", "R.", 6, "Trombone 1"),
    ("Gus", "M.", 8, "Tuba"),
    ("Remy", "K.", 7, "Percussion"), ("Tess", "L.", 6, "Percussion"), ("June", "P.", 8, "Piano"),
]


def main() -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Band 2026-27"
    ws.append(["First Name", "Last Initial", "Grade", "Instrument", "Locker #"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for i, (first, last, grade, inst) in enumerate(ROWS, start=1):
        ws.append([first, last, grade, inst, 100 + i])
    for col, w in zip("ABCDE", (14, 12, 8, 18, 10)):
        ws.column_dimensions[col].width = w
    out = Path(__file__).resolve().parent.parent / "samples" / "lopsided-band.xlsx"
    out.parent.mkdir(exist_ok=True)
    wb.save(out)
    print("wrote", out)


if __name__ == "__main__":
    main()
