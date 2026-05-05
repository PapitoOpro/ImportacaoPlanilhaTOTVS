# -*- coding: utf-8 -*-
import sys
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).parent / "src"))

from config import (COLUMN_MAP, FIELD_FILL_DEFAULTS, FIELD_RULES,
                    REQUIRED_FIELDS, TEMPLATE_COLUMNS, TEMPLATE_DEFAULTS)
from reader import read_client_file
from transformer import transform
from validator import validate
from writer import write_error_report, write_output

_BASE = Path(__file__).parent

app = FastAPI(title="TOTVS Food — Importação de Produtos")
app.mount("/static", StaticFiles(directory=str(_BASE / "static")), name="static")

_sessions: dict[str, dict] = {}


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse((_BASE / "templates" / "index.html").read_text(encoding="utf-8"))


@app.post("/processar")
async def processar(file: UploadFile = File(...)):
    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".xls", ".xlsx", ".xlsm", ".csv"):
        raise HTTPException(status_code=400, detail="Formato não suportado. Use .xls, .xlsx ou .csv")

    content = await file.read()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        df = read_client_file(tmp_path)
        df = transform(df, COLUMN_MAP, TEMPLATE_DEFAULTS, TEMPLATE_COLUMNS, FIELD_FILL_DEFAULTS)
        errors = validate(df, REQUIRED_FIELDS, FIELD_RULES)

        invalid_idx = {e.row - 3 for e in errors}
        valid_df = df[~df.index.isin(invalid_idx)].reset_index(drop=True)

        session_id = str(uuid.uuid4())
        session_dir = Path(tempfile.gettempdir()) / session_id
        session_dir.mkdir()

        output_path = session_dir / "PlanilhaImportaçãoLoja.xlsm"
        error_path = session_dir / "erros.xlsx"

        write_output(valid_df, output_path)
        if errors:
            write_error_report(errors, error_path)

        _sessions[session_id] = {
            "output": output_path,
            "error_file": error_path if errors else None,
        }

        return JSONResponse({
            "session_id": session_id,
            "stats": {
                "total":       len(df),
                "exportados":  len(valid_df),
                "rejeitados":  len(df) - len(valid_df),
            },
            "erros": [
                {"linha": e.row, "campo": e.field, "valor": e.value, "motivo": e.reason}
                for e in errors
            ],
        })
    finally:
        tmp_path.unlink(missing_ok=True)


@app.get("/download/{session_id}/resultado")
async def download_resultado(session_id: str):
    session = _sessions.get(session_id)
    if not session or not session["output"].exists():
        raise HTTPException(status_code=404, detail="Sessão expirada ou arquivo não encontrado")
    return FileResponse(
        path=session["output"],
        filename="PlanilhaImportaçãoLoja.xlsm",
        media_type="application/vnd.ms-excel.sheet.macroEnabled.12",
    )


@app.get("/download/{session_id}/erros")
async def download_erros(session_id: str):
    session = _sessions.get(session_id)
    if not session or not session["error_file"] or not session["error_file"].exists():
        raise HTTPException(status_code=404, detail="Nenhum relatório de erros disponível")
    return FileResponse(
        path=session["error_file"],
        filename="erros.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
