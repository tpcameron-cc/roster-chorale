"""Build data/chorales.json: the clean SATB chorales from the music21 corpus.

Run once (or after upgrading music21):  uv run python scripts/build_catalog.py
Keeps only chorales with exactly four parts named Soprano/Alto/Tenor/Bass.
Starts from music21's chorale iterator (the 371 Riemenschneider chorales, which carries
titles), then adds the other plain "bwvNN.N" chorale files in the Bach corpus (such as
bwv66.6) that the iterator leaves out.
"""
import os
import re
import json
import sys
from pathlib import Path

from music21 import corpus
from music21.corpus.chorales import ChoraleListRKBWV

OUT = Path(__file__).resolve().parent.parent / "data" / "chorales.json"


def main() -> None:
    titles = ChoraleListRKBWV().byRiemenschneider
    files = list(corpus.chorales.Iterator(returnType="filename"))
    extras = sorted({os.path.basename(str(p)).rsplit(".", 1)[0] for p in corpus.getComposer("bach")})
    extras = [f"bach/{n}" for n in extras if re.fullmatch(r"bwv\d+\.\d+", n)]
    numbered = list(enumerate(files, start=1)) + [(None, fn) for fn in extras]
    out, skipped, seen = [], [], set()
    for riem, fn in numbered:
        cid = fn.split("/")[-1]
        if cid in seen:  # a few chorales appear twice in the Riemenschneider order
            continue
        seen.add(cid)
        try:
            s = corpus.parse(fn)
        except Exception as e:  # noqa: BLE001
            skipped.append((cid, f"parse error {e}"))
            continue
        names = [(p.partName or "").strip() for p in s.parts]
        if names != ["Soprano", "Alto", "Tenor", "Bass"]:
            skipped.append((cid, f"parts {names}"))
            continue
        k = s.analyze("key")
        info = titles.get(riem, {}) if riem else {}
        title = info.get("title") or f"Chorale from BWV {cid.removeprefix('bwv')}"
        out.append({
            "id": cid,
            "bwv": cid.removeprefix("bwv"),
            "riemenschneider": riem,
            "title": title or cid,
            "key": f"{k.tonic.name.replace('-', 'b')} {k.mode}",
            "measures": len(s.parts[0].getElementsByClass("Measure")),
        })
        print(f"{riem or '-':>3} {cid:14s} {title}", file=sys.stderr)
    out.sort(key=lambda c: (c["riemenschneider"] is None, c["riemenschneider"] or 0))
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"kept {len(out)}, skipped {len(skipped)}")
    for s in skipped:
        print("  skip", *s)


if __name__ == "__main__":
    main()
