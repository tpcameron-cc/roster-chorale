"""Roster Chorale web API (FastAPI). Vercel picks up `app` from this file.

Everything is per request and in memory: no accounts, no database, no stored rosters.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from roster_chorale import chorale as chmod
from roster_chorale import roster as rmod
from roster_chorale.arrange import ArrangeError, arrange, fit_report_text
from roster_chorale.instruments import ORDER

ROOT = Path(__file__).resolve().parent
MAX_UPLOAD = 2 * 1024 * 1024

app = FastAPI(title="Roster Chorale", docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.exception_handler(rmod.RosterError)
@app.exception_handler(ArrangeError)
async def _user_error(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "chorales": len(chmod.catalog())}


@app.get("/api/chorales")
def chorales() -> dict:
    return {
        "instruments": ORDER,
        "grades": ["0.5", "1", "2", "3"],
        "chorales": [{"id": c["id"], "bwv": c["bwv"], "title": c["title"], "key": c["key"],
                      "measures": c["measures"], "riemenschneider": c["riemenschneider"]}
                     for c in chmod.catalog()],
    }


@app.post("/api/roster")
async def roster(file: UploadFile | None = File(None), text: str | None = Form(None)) -> dict:
    """Clean a roster from an uploaded .xlsx/.csv, or from pasted spreadsheet rows."""
    if file is not None and file.filename:
        data = await file.read(MAX_UPLOAD + 1)
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "That file is bigger than 2 MB. A roster should be much smaller.")
        r = rmod.parse_upload(file.filename, data)
    elif text and text.strip():
        r = rmod.parse_text(text)
    else:
        raise HTTPException(400, "Upload a roster file or paste some rows first.")
    return r.to_json()


class ArrangeBody(BaseModel):
    roster: dict[str, int] = Field(..., description="standard instrument name -> number of players")
    chorale: str = Field(..., description="chorale id such as bwv66.6")
    grade: str = "1"
    parts: str = Field("single", description='"single" = one part per instrument, "multiple" = split sections')


@app.post("/api/arrange")
def do_arrange(body: ArrangeBody) -> dict:
    res = arrange(body.chorale, body.roster, body.grade, parts_mode=body.parts)
    res["fit_report_text"] = fit_report_text(res)
    return res


@app.post("/api/zip")
def do_zip(body: ArrangeBody) -> Response:
    res = arrange(body.chorale, body.roster, body.grade, parts_mode=body.parts)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in res["parts"]:
            z.writestr(p["filename"], p["musicxml"])
        z.writestr("fit-report.txt", fit_report_text(res))
    name = f"{res['chorale']['id']}-grade{res['grade']['value']}-parts.zip"
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# Local development: serve the static page. On Vercel, public/ is served by the CDN
# (and isn't bundled into the function), so this mount only exists locally.
if (ROOT / "public").is_dir():
    app.mount("/", StaticFiles(directory=ROOT / "public", html=True), name="static")
