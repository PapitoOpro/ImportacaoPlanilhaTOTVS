# -*- coding: utf-8 -*-
import base64
import shutil
import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).parent / "src"))

from config import (COLUMN_MAP, FIELD_FILL_DEFAULTS, FIELD_RULES,
                    REQUIRED_FIELDS, TEMPLATE_COLUMNS, TEMPLATE_DEFAULTS)
from reader import read_client_file
from transformer import transform
from validator import validate
from writer import write_error_report, write_output

_BASE = Path(__file__).parent
_TEMPLATE = _BASE / "PlanilhaImportaçãoLojaComValidação.xlsm"

app = FastAPI(title="TOTVS Food — Importação de Produtos")
app.mount("/static", StaticFiles(directory=str(_BASE / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse((_BASE / "templates" / "index.html").read_text(encoding="utf-8"))


@app.post("/processar")
async def processar(file: UploadFile = File(...)):
    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".xls", ".xlsx", ".xlsm", ".csv"):
        raise HTTPException(status_code=400, detail="Formato não suportado. Use .xls, .xlsx ou .csv")

    content = await file.read()
    work_dir = Path(tempfile.mkdtemp())

    try:
        input_path = work_dir / f"input{suffix}"
        input_path.write_bytes(content)

        df = read_client_file(input_path)
        df = transform(df, COLUMN_MAP, TEMPLATE_DEFAULTS, TEMPLATE_COLUMNS, FIELD_FILL_DEFAULTS)
        errors = validate(df, REQUIRED_FIELDS, FIELD_RULES)

        invalid_idx = {e.row - 3 for e in errors}
        valid_df = df[~df.index.isin(invalid_idx)].reset_index(drop=True)

        output_path = work_dir / "output.xlsm"
        write_output(valid_df, output_path, template_path=_TEMPLATE)
        arquivo_b64 = base64.b64encode(output_path.read_bytes()).decode()

        arquivo_erros_b64 = None
        if errors:
            error_path = work_dir / "erros.xlsx"
            write_error_report(errors, error_path)
            arquivo_erros_b64 = base64.b64encode(error_path.read_bytes()).decode()

        return JSONResponse({
            "stats": {
                "total":      len(df),
                "exportados": len(valid_df),
                "rejeitados": len(df) - len(valid_df),
            },
            "erros": [
                {"linha": e.row, "campo": e.field, "valor": e.value, "motivo": e.reason}
                for e in errors
            ],
            "arquivo":        arquivo_b64,
            "arquivo_erros":  arquivo_erros_b64,
        })
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
