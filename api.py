# -*- coding: utf-8 -*-
import base64
import shutil
import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).parent / "src"))

from config import (COLUMN_MAP, FIELD_FILL_DEFAULTS, FIELD_RULES,
                    REQUIRED_FIELDS, TEMPLATE_COLUMNS, TEMPLATE_DEFAULTS)
from fiscal import REGIMES, build_regras, validate_uf, write_regras
from reader import read_client_file
from transformer import transform
from validator import validate
from writer import write_error_report, write_output

_BASE = Path(__file__).parent
_TEMPLATE = _BASE / "PlanilhaImportaçãoLojaComValidação.xlsm"
_TEMPLATE_REGRAS = _BASE / "ImportacaoRegraNCMDadosFiscais.xlsx"
_EXTENSOES = (".xls", ".xlsx", ".xlsm", ".csv")

app = FastAPI(title="TOTVS Food — Importação de Produtos")
app.mount("/static", StaticFiles(directory=str(_BASE / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse((_BASE / "templates" / "index.html").read_text(encoding="utf-8"))


def _check_extensao(file: UploadFile) -> str:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _EXTENSOES:
        raise HTTPException(status_code=400, detail="Formato não suportado. Use .xls, .xlsx ou .csv")
    return suffix


@app.post("/processar")
async def processar(file: UploadFile = File(...), numero_loja: str = Form("")):
    suffix = _check_extensao(file)

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
        write_output(valid_df, output_path, template_path=_TEMPLATE, numero_loja=numero_loja.strip())
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


@app.post("/processar-regras-ncm")
async def processar_regras_ncm(
    file: UploadFile = File(...),
    regime: str = Form(...),
    uf: str = Form("SP"),
    numero_loja: str = Form(""),
):
    suffix = _check_extensao(file)
    if regime not in REGIMES:
        raise HTTPException(status_code=400, detail="Regime tributário inválido")
    try:
        uf = validate_uf(uf)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    content = await file.read()
    work_dir = Path(tempfile.mkdtemp())

    try:
        input_path = work_dir / f"input{suffix}"
        input_path.write_bytes(content)

        df = read_client_file(input_path)
        try:
            result = build_regras(df, regime)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        output_path = work_dir / "regras.xlsx"
        write_regras(result.regras, output_path, _TEMPLATE_REGRAS, regime, uf, numero_loja.strip())
        arquivo_b64 = base64.b64encode(output_path.read_bytes()).decode()

        arquivo_erros_b64 = None
        if result.errors:
            error_path = work_dir / "erros.xlsx"
            write_error_report(result.errors, error_path)
            arquivo_erros_b64 = base64.b64encode(error_path.read_bytes()).decode()

        return JSONResponse({
            "stats": {
                "total":      result.total_produtos,
                "exportados": result.produtos_validos,
                "rejeitados": result.total_produtos - result.produtos_validos,
                "regras":     len(result.regras),
            },
            "erros": [
                {"linha": e.row, "campo": e.field, "valor": e.value, "motivo": e.reason}
                for e in result.errors
            ],
            "arquivo":       arquivo_b64,
            "arquivo_erros": arquivo_erros_b64,
        })
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
