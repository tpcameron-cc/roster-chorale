"""Build data/chorale_notes.json: each chorale's four lines as (concert MIDI, phrase) pairs,
once per rhythm level (as written / sixteenths to eighths / eighths to quarters).

The "pick a key" feature ranks every chorale against a roster in a second or so; parsing
the MusicXML for ~175 chorales on each click would take far longer, so the notes are
precomputed here. Rebuild after changing the catalog or the rhythm rules:

    uv run python scripts/build_notes.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from roster_chorale import chorale as chmod  # noqa: E402
from roster_chorale.arrange import grade_cfg, rhythm_id  # noqa: E402
from roster_chorale.instruments import VOICES  # noqa: E402

OUT = ROOT / "data" / "chorale_notes.json"
RULES = {rhythm_id(grade_cfg(g)["rhythm"]): grade_cfg(g)["rhythm"] for g in ("1", "2", "3")}


def main() -> None:
    out: dict[str, dict] = {rid: {} for rid in RULES}
    catalog = chmod.catalog()
    for k, meta in enumerate(catalog, start=1):
        original = chmod.load(meta["id"])
        for rid, rule in RULES.items():
            ch, _ = chmod.simplify(original, rule)
            voices = {}
            for v in VOICES:
                flat: list[int] = []
                for b in ch.voices[v]:
                    for e in b.events:
                        if not e.is_rest:
                            flat += [int(e.pitch.ps), e.phrase]
                voices[v] = flat
            out[rid][meta["id"]] = {"tonic": ch.key.tonic.name,
                                    "mode": ch.key.mode if ch.key.mode in ("major", "minor") else "major",
                                    "v": voices}
        if k % 25 == 0:
            print(f"{k}/{len(catalog)}", file=sys.stderr)
    OUT.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB) for {len(catalog)} chorales x {len(RULES)} rhythm levels")


if __name__ == "__main__":
    main()
