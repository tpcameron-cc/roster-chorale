"""CLI:  python -m roster_chorale samples/lopsided-band.xlsx --chorale bwv66.6 --grade 1 --out out/"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import roster as rmod
from .arrange import arrange, fit_report_text, rank_chorales


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="roster_chorale", description=__doc__)
    ap.add_argument("roster", help=".xlsx or .csv roster (one row per student, or instrument + count)")
    ap.add_argument("--chorale", default="bwv66.6", help="chorale id or BWV number, e.g. bwv66.6 or 66.6")
    ap.add_argument("--grade", default="1", choices=["0.5", "1", "2", "3"])
    ap.add_argument("--parts", default="single", choices=["single", "multiple"],
                    help="single: one part per instrument; multiple: split sections (Flute 1, Flute 2...)")
    ap.add_argument("--key", default=None,
                    help='concert key to use, e.g. "Bb major" (default: pick the best-fitting key). '
                         'With --chorale best, picks the best-fitting chorale for that key.')
    ap.add_argument("--out", default="out", help="output folder")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    path = Path(a.roster)
    r = rmod.parse_upload(path.name, path.read_bytes())
    print("Roster:", ", ".join(f"{k} {v}" for k, v in r.ordered_counts().items()))
    if r.unrecognized:
        print("Unrecognised (left out):", ", ".join(f"{k} x{v}" for k, v in r.unrecognized.items()))

    chorale_id = a.chorale
    if a.key and a.chorale == "best":
        sl = rank_chorales(r.ordered_counts(), a.grade, a.key, a.parts)
        print(f"Best chorales in concert {sl['key']}:")
        for c in sl["shortlist"]:
            print(f"  BWV {c['bwv']:<10} {c['title'][:40]:<40} {c['fit']}")
        chorale_id = sl["shortlist"][0]["id"]
    res = arrange(chorale_id, r.ordered_counts(), a.grade, parts_mode=a.parts, key_choice=a.key)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for p in res["parts"]:
        (out / p["filename"]).write_text(p["musicxml"], encoding="utf-8")
    report = fit_report_text(res)
    (out / "fit-report.txt").write_text(report, encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print()
    print(report)
    print(f"Wrote {len(res['parts'])} parts + fit-report.txt to {out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
