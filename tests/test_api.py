import io
import zipfile

from fastapi.testclient import TestClient

from app import app
from conftest import LOPSIDED

client = TestClient(app)


def test_chorales_list_is_clean_satb():
    d = client.get("/api/chorales").json()
    assert len(d["chorales"]) > 300
    ids = [c["id"] for c in d["chorales"]]
    assert "bwv66.6" in ids and len(ids) == len(set(ids))
    assert d["grades"] == ["0.5", "1", "2", "3"]


def test_roster_upload(sample_xlsx):
    r = client.post("/api/roster", files={"file": (sample_xlsx.name, sample_xlsx.read_bytes())})
    assert r.status_code == 200
    d = r.json()
    assert d["counts"] == {**LOPSIDED, "Percussion": 2}
    assert {u["name"] for u in d["unrecognized"]} == {"Piano"}


def test_roster_paste():
    r = client.post("/api/roster", data={"text": "Name\tInstrument\nA\tFlute\nB\tTbn\n"})
    assert r.json()["counts"] == {"Flute": 1, "Trombone": 1}


def test_roster_errors_are_friendly():
    r = client.post("/api/roster", data={"text": "Name\tAge\nA\t12\n"})
    assert r.status_code == 400 and "instrument column" in r.json()["detail"]
    r = client.post("/api/roster", data={})
    assert r.status_code == 400


def test_arrange_and_zip():
    body = {"roster": LOPSIDED, "chorale": "bwv66.6", "grade": "1"}
    d = client.post("/api/arrange", json=body).json()
    assert d["key"]["concert"] and d["runner_up"]
    assert len(d["parts"]) == len(LOPSIDED)
    assert all(p["musicxml"].lstrip().startswith("<?xml") for p in d["parts"])
    assert "OCTAVE SHIFTS" in d["fit_report_text"]
    z = client.post("/api/zip", json=body)
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert "fit-report.txt" in names and sum(n.endswith(".musicxml") for n in names) == len(LOPSIDED)


def test_arrange_bad_chorale():
    r = client.post("/api/arrange", json={"roster": LOPSIDED, "chorale": "bwv9999", "grade": "1"})
    assert r.status_code == 400


def test_arrange_split_sections_via_api():
    body = {"roster": {"Flute": 6, "Trumpet": 4, "Trombone": 2, "Tuba": 1}, "chorale": "bwv66.6", "grade": "2",
            "parts": "multiple"}
    d = client.post("/api/arrange", json=body).json()
    names = [p["part_name"] for p in d["parts"]]
    assert "Flute 1" in names and "Flute 2" in names and "Trumpet 1 in Bb" in names and "Trombone 2" in names
    z = client.post("/api/zip", json=body)
    assert len(zipfile.ZipFile(io.BytesIO(z.content)).namelist()) == len(d["parts"]) + 1
