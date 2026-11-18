# Build: Roster Chorale

> Turn any of the 371 Bach chorales into a printable set of band parts scored for the instruments a band actually has, at the difficulty grade the director picks, with the concert key chosen to keep everyone in range. Built for a middle-school band director on Sunday night planning Monday's warm-up, with a lopsided roster and an Excel sheet of students.

Hosted on **Vercel**: a single web page plus one Python (FastAPI) function.

## 1. The demo (acceptance test)

This 60-second walkthrough is how we know it works. Do not call the build done
until every step below works on the deployed Vercel URL with real chorales.

1. Open the Vercel URL, then upload `samples/lopsided-band.xlsx`, a one-row-per-student roster with messy instrument names like "Bb Clar", "Alto Sax 1", "Bari", "Tbn" and "Flute 2". The page shows a cleaned roster: Flute 9, Clarinet 5, Bass Clarinet 1, Alto Sax 3, Tenor Sax 1, Bari Sax 1, Trumpet 4, Horn 1, Trombone 2, Tuba 1. It also lists any names it couldn't recognize, with a dropdown to classify each one.
2. Do it again by **pasting** rows copied straight from Excel or Google Sheets into the text box. You get the same cleaned roster.
3. Pick a chorale (search by BWV number or title, or press "Surprise me"), set **Target grade** to `1` and click **Arrange**. The page shows:
   - the chosen concert key, plus a runner-up key and why it lost (e.g. "Concert Bb: 0 notes out of range. Runner-up C: 7 trumpet notes above C5")
   - the voice each instrument plays (soprano, alto, tenor or bass)
   - a fit report: every octave shift (by phrase) and every rhythm simplification made for grade 1
4. Every part renders in the browser as real notation, transposed for that instrument (Bb clarinet and trumpet a step up, alto sax a sixth up, horn in F, and so on), with title, part name and measure numbers.
5. Click **Print all parts**. The browser's print dialog gives one part per page, ready to save as PDF. **Download MusicXML (.zip)** gives a file per part that opens in MuseScore.
6. Switch the target grade to `3` on the same chorale and click **Arrange** again. Now the original eighth-note passing tones are back, the key may change, and fewer octave shifts are needed. This is the "huh, neat" moment.

## 2. Scope

**In (day one):**
- Roster input as an `.xlsx` or `.csv` upload, or pasted tab-separated text copied from a spreadsheet. The roster can be one row per student (find the instrument column and count) or instrument-and-count pairs.
- An alias table that maps messy instrument names to a standard list, and lists anything unrecognized instead of silently dropping it.
- **Target grade selector: 0.5, 1, 2 or 3.** It drives three things:
  - the range limit for each instrument at that grade
  - the allowed concert keys
  - rhythm simplification
- Voice assignment plus a search over all 12 concert keys, scored by out-of-range notes across the whole roster.
- Octave displacement done per instrument part first, then per phrase (Bach chorales mark phrase ends with fermatas), never note by note.
- Transposed MusicXML for each part, rendered in the browser with Verovio, with print-to-PDF and a zip download.
- Deployment to Vercel (FastAPI function plus a static page).

**Out: do NOT build these:**
- User accounts, login, saved rosters or a database. Everything is per request; the browser may keep the last roster in localStorage.
- Server-side PDF generation (no MuseScore or LilyPond on the server). Browser print is the PDF path.
- Audio playback or MIDI.
- Any music beyond the music21 Bach chorale corpus (no uploading your own MusicXML).
- Divisi or multiple parts per instrument (one "Clarinet" part, not Clarinet 1/2/3).
- Percussion other than mallets. Leave out mallets too if they slow you down.
- Per-student grade levels. One grade applies to the whole arrangement.
- A drag-and-drop part editor or a settings page. The range table is a JSON file in the repo.
- Google Sheets API or OAuth. Copy-paste covers it.

## 3. Stack (and why)

| Piece | Choice | Why |
|-------|--------|-----|
| Language (logic) | Python 3.12 | The owner is fluent in Python, and music21 is Python. |
| Music library | `music21` (10.5.2 verified) | Ships the 371 Bach chorales locally and handles parsing, transposition by instrument, key analysis and MusicXML export. |
| Spreadsheet input | `openpyxl` + stdlib `csv` | Reads `.xlsx` without pandas, keeping the bundle small. `csv.Sniffer` handles pasted tab or comma text. |
| API | FastAPI, `app.py` exposing `app` | Vercel's Python runtime detects FastAPI with zero config. |
| Front end | One static `index.html` + vanilla JS | No framework needed for one form and a results view. |
| Notation rendering | Verovio JS toolkit (npm `verovio` 6.4.1, WASM build from jsDelivr) | Renders MusicXML to SVG in the browser with real engraving, so there's no server-side PDF. |
| PDF | Browser print with `@media print` page breaks | Free, works everywhere, and nothing runs on the server. |
| Hosting | Vercel (Hobby/free) with the `vercel` CLI | Required by the owner. The owner must run `vercel login` themselves; stop and ask them to when you get there. |
| Package mgmt | `uv` + `pyproject.toml` | Vercel builds with uv with zero config. |
| Tests | `pytest` | |

Keep it boring. No framework beyond FastAPI.

## 4. Verified data sources

### music21 Bach chorale corpus (local, bundled with the package)
- **Endpoint:** `from music21 import corpus; corpus.chorales.Iterator(returnType='filename')`, and `corpus.parse('bach/bwv66.6')`
- **Auth:** none. It's a local package.
- **Rate limit:** n/a.
- **Verified on 2026-11-18:** `music21 10.5.2` installed with `uv`. `corpus.chorales.Iterator` returned **371** chorales (`corpus.getComposer('bach')` returns 433 files, so use the chorale iterator, not the composer list). Parsing `bach/bwv66.6`:
  ```
  parts: ['Soprano', 'Alto', 'Tenor', 'Bass']   key: f# minor
  Soprano notes 37 range E4 E5
  Alto    notes 42 range F#3 A4
  Tenor   notes 45 range E#3 E4
  Bass    notes 41 range F#2 D4
  durations (tenor): [0.5, 1.0, 2.0]
  ```
  Import plus first parse took about 1.3 s locally. One transposed chorale written as MusicXML was 54 KB.
- **Package size:** about 91.5 MB installed, of which 60.6 MB is the corpus. Numpy and matplotlib come along as dependencies.
- **Gotchas:**
  - A few chorales have more or fewer than four parts, or instrumental lines. Skip anything that isn't exactly four parts named Soprano/Alto/Tenor/Bass, and filter the picker list at build time.
  - Eighth-note passing tones are what grade simplification removes.
  - Fermatas (`expressions.Fermata` on notes) mark phrase ends. Use them as phrase boundaries for octave shifts.

### Verovio JS toolkit (browser)
- **Endpoint:** `https://cdn.jsdelivr.net/npm/verovio@6.4.1/dist/verovio-toolkit-wasm.js`
- **Auth:** none.
- **Verified on 2026-11-18:** npm `verovio` latest = **6.4.1**. The jsDelivr URL (fetched as `@latest`) returned HTTP 200 and 7.4 MB. Pin `@6.4.1` in the page.
- **Gotchas:** The WASM build loads asynchronously; wait for the module's `onRuntimeInitialized` before calling `new verovio.toolkit()`. Use `tk.loadData(musicxmlString)` and `tk.renderToSVG(page)` with `tk.getPageCount()`. Set `pageHeight` and `pageWidth` for letter paper and a sensible `scale` so each part prints on one page where possible.

### Vercel Python runtime
- **Docs:** https://vercel.com/docs/functions/runtimes/python (page last updated 2026-08-12)
- **Verified on 2026-11-18 (from the docs):**
  - Python 3.12 is the default (3.13 and 3.14 are also available).
  - The FastAPI entrypoint is `app.py` with a top-level `app`.
  - Dependencies come from `pyproject.toml` or `requirements.txt`, with uv supported.
  - The standard Python bundle limit is **500 MB uncompressed**.
  - Files to skip go in `vercel.json` → `functions` → `"app.py": {"excludeFiles": "..."}`.
  - There is no tree-shaking.
- **Gotchas:**
  - music21 plus numpy and matplotlib should be well under 500 MB, but **check the real bundle in M0**.
  - Relative `open()` paths resolve from the project root.
  - Check in M0 how the static `index.html` is served alongside the FastAPI app. Try `public/` first; if Vercel routes everything to FastAPI, mount it with `StaticFiles` or a `FileResponse` at `/`.
  - Cold start includes importing music21, so expect a few seconds on the first request. Show a spinner.

### Grade table (hand-encoded, in repo)
- **File:** `data/grades.json`. These are starting values: conservative and approximate. Keep them editable and add a README note that directors should check them against their state's grading guide.
- Ranges are **written pitch** (what the player reads), inclusive.

| Instrument | Grade 0.5–1 | Grade 2 | Grade 3 |
|---|---|---|---|
| Flute | D4–C6 | C4–F6 | C4–G6 |
| Oboe | D4–B5 | C4–D6 | Bb3–E6 |
| Bb Clarinet | G3–C5 | E3–G5 | E3–C6 |
| Bb Bass Clarinet | G3–Bb4 | E3–F5 | E3–A5 |
| Bassoon (concert) | F2–C4 | Bb1–F4 | Bb1–A4 |
| Alto / Tenor / Bari Sax | D4–C6 | C4–D6 | Bb3–E6 |
| Bb Trumpet | Bb3–C5 | A3–E5 | F#3–G5 |
| F Horn | G3–C5 | F3–E5 | C4–F5 (and C3 low) |
| Trombone / Euphonium (concert) | Bb2–C4 | F2–D4 | E2–F4 |
| Tuba (concert) | Bb1–Bb2 | F1–D3 | E1–F3 |
| Mallets/Bells (written) | G4–C6 | G4–C6 | G4–C7 |

- **Allowed concert keys:** grade 0.5–1 allows Bb, Eb, F and Ab major plus G, C and F minor. Grade 2 adds C, Db and Bb minor. Grade 3 allows any key with up to 4 flats or 2 sharps.
- **Rhythm by grade:**
  - **Grade 0.5–1:** merge every eighth-note pair into a quarter that keeps the on-beat pitch, remove ties across barlines (keep the first note's length up to the barline and drop the rest), and keep fermatas.
  - **Grade 2:** keep eighths, simplify sixteenths to eighths.
  - **Grade 3:** as written.
- Use music21's instrument classes (`instrument.Clarinet()`, `AltoSaxophone()`, `Horn()`, …) for transposition, not hand-written intervals. The chart's written ranges are compared after transposing to written pitch.

## 5. Build order

Each milestone ends with something you can run. Run it before moving on.

- **M0, Spike (≈30 min):** Two checks.
  - Locally: load `bach/bwv66.6`, transpose the soprano to Bb trumpet written pitch, write MusicXML, and print the written range.
  - On Vercel: deploy a "hello" FastAPI `app.py` that imports music21 and returns the chorale count. Record the bundle size and cold-start time. If the owner isn't logged in to Vercel, stop and ask them to run `vercel login`.
  - If the bundle is a problem, exclude `music21/corpus/**` except the Bach files via `excludeFiles`.
- **M1, Core loop, ugly CLI (≈2.5 h):** `python -m roster_chorale samples/lopsided-band.xlsx --chorale bwv66.6 --grade 1 --out out/`
  1. Parse the roster and count instruments, using the alias table.
  2. Apply grade rhythm simplification.
  3. Search all keys in the grade's allowed set.
  4. Assign voices. Each instrument has a ranked list of preferred voices, e.g. flute S, clarinet S/A, alto sax A, horn A/T, tenor sax T, trombone T/B, bari/bass clarinet/tuba B. Minimize out-of-range notes, require every voice (especially bass) to be covered by at least one instrument, and break ties toward the instrument's preferred voice.
  5. Shift whole parts by an octave, then shift individual phrases between fermatas.
  6. Write one MusicXML file per instrument plus `fit-report.txt`.
- **M2, Real data path + web (≈2 h):**
  - The FastAPI endpoints are:
    - `POST /api/roster` (file or pasted text) returns the cleaned counts plus unrecognized names.
    - `GET /api/chorales` returns BWV numbers and titles, filtered to clean four-part chorales.
    - `POST /api/arrange` takes roster counts, chorale and grade, and returns the key decision, assignment, fit report and a MusicXML string per part.
    - `GET /api/zip` (or a POST with the same body) returns the parts as a zip.
  - `index.html` renders each part with Verovio and has print CSS with `page-break-after`.
  - Deploy to Vercel.
- **M3, Edge cases & errors (≈1 h):**
  - A roster with no bass instrument (fall back to trombone or bari, and if none of those exist, warn clearly).
  - A roster with only one instrument type.
  - An empty or unreadable spreadsheet.
  - A sheet whose instrument column isn't the first column (detect a header containing "instrument", "inst" or "section").
  - A chorale where no allowed key fits (choose the least-bad key, flag it in red, and list the offending notes by measure).
  - Pasted text with blank lines or a header row.
- **M4, Polish (≈1 h):** Clean part headers (title, BWV, concert key, part name, grade). A "Surprise me" button. Copy that explains the key choice in plain English. A README with a screenshot of a printed part and of the fit report, how the grade table works, and deploy steps.

## 6. Quality bar

- **Tests for core logic:**
  - alias mapping (at least 25 messy names → standard instruments)
  - roster counting from both per-student and count formats, for `.xlsx`, `.csv` and pasted TSV
  - range check in written pitch for each transposing instrument (Bb clarinet, alto sax, horn, trumpet, bari)
  - key search picks the key with the fewest out-of-range notes for a fixed roster
  - every voice is covered
  - grade-1 rhythm simplification never leaves an eighth note, and keeps total measure durations correct
  - phrase-level octave shifts happen only at fermata boundaries
- No placeholder or mock music in the demo: every part comes from a real corpus chorale. `samples/lopsided-band.xlsx` is a realistic input file (invented student first names are fine). Commit it.
- **Graceful failures:** unrecognized instruments are listed for the user to classify. If no key fits, the page says so and shows the least-bad option with the problem notes. If the function cold-starts slowly, the page shows a "Warming up…" state instead of hanging.
- **Musical correctness rule:** every printed part must play the correct concert pitches. Add a test that transposes each written part back to concert pitch and checks it matches the assigned voice (after intentional octave shifts) note for note.
- No personal or student data is stored on the server; requests are processed in memory only.

## 7. How to work

- Start by writing a short plan. Then build milestone by milestone.
- After each milestone, run it and show the output (for M1, print the fit report and open one MusicXML in a text check; for M2+, show the deployed URL).
- Only stop to ask me if something in "Out" starts to look required, or when you need me to log in to Vercel.
- Commit after each milestone if this is a git repo.

## 8. Prior art (stay different from these)

| Existing tool | What it does | How this build differs |
|---------------|--------------|------------------------|
| [Bach Chorales for Band (Hal Leonard)](https://www.halleonard.com/product/3770726/bach-chorales-for-band) | 28 printed chorales in fixed keys and fixed parts | Takes your roster and grade, can use any of the 371 chorales, and searches keys |
| [Flexible chorale books (e.g. J.W. Pepper listing)](https://www.jwpepper.com/Three-Bach-Chorales-For-Band/10607646.item) | Four-part books that any instrument mix can read | Static books, with no octave fitting for your players and no fit report |
| [MuseScore "Arrange Parts" plugin](https://musescore.org/project/arrange-parts) | Spreads chord voices across staves | States it does not bring notes into range, has no roster input and doesn't choose a key |
| [Sibelius Arrange](https://www.avid.com/sibelius/music-arranging) | Manual, paid explode/reduce to an ensemble | No roster-driven key search, no grade target, no web use |
| [salu133445/arranger](https://github.com/salu133445/arranger) | Research code for automatic instrumentation | Research tool, not tied to school band ranges or grades, and no classroom output |

The wedge, in one sentence: it starts from the band you actually have and a target grade, then picks voices, octaves and the concert key to fit them, which fixed-key chorale books and manual notation tools can't do.
