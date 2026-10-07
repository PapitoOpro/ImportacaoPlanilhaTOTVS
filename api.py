# -*- coding: utf-8 -*-
import base64
import json
import logging
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).parent / "src"))

from config import (COLUMN_MAP, FIELD_FILL_DEFAULTS, FIELD_RULES,
                    REQUIRED_FIELDS, TEMPLATE_COLUMNS, TEMPLATE_DEFAULTS)
from fiscal import REGIMES, build_regras, validate_uf, write_analise_report, write_regras
from fiscal_db import connect as connect_fiscal_db
from fiscal_service import FiscalService
from kb_api import build_router as build_kb_router
from kb_repository import KnowledgeBase, create_kb_engine
from reader import read_client_file
from transformer import transform
from validator import validate
from writer import write_error_report, write_output

_BASE = Path(__file__).parent
_TEMPLATE = _BASE / "PlanilhaImportaçãoLojaComValidação.xlsm"
_TEMPLATE_REGRAS = _BASE / "ImportacaoRegraNCMDadosFiscais.xlsx"
_EXTENSOES = (".xls", ".xlsx", ".xlsm", ".csv")

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("api")


def _load_fiscal_service() -> FiscalService | None:
    try:
        service = FiscalService(connect_fiscal_db(readonly=True))
    except (FileNotFoundError, OSError) as exc:
        logger.warning(json.dumps({"event": "base_fiscal_indisponivel", "erro": str(exc)}, ensure_ascii=False))
        return None
    if not service.has_data():
        logger.warning(json.dumps({"event": "base_fiscal_vazia"}, ensure_ascii=False))
        return None
    return service


_FISCAL = _load_fiscal_service()

_KB: KnowledgeBase | None = None


def get_kb() -> KnowledgeBase:
    """Conecta na primeira requisição (Supabase fora do ar não derruba as conversões)."""
    global _KB
    if _KB is None:
        try:
            _KB = KnowledgeBase(create_kb_engine())
        except Exception as exc:  # noqa: BLE001 - qualquer falha de conexão vira 503
            logger.error(json.dumps({"event": "kb_indisponivel", "erro": str(exc)}, ensure_ascii=False))
            raise HTTPException(status_code=503, detail="Base de Conhecimento indisponível.") from exc
    return _KB


app = FastAPI(title="TOTVS Food — Importação de Produtos")
app.include_router(build_kb_router(get_kb))
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
    usuario: str = Form(""),
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
            result = build_regras(
                df, regime, service=_FISCAL,
                usuario=usuario.strip()[:100] or "anonimo",
                arquivo=Path(file.filename or "").name[:200],
            )
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

        arquivo_analise_b64 = None
        if result.analise:
            analise_path = work_dir / "analise_fiscal.xlsx"
            write_analise_report(result.analise, analise_path)
            arquivo_analise_b64 = base64.b64encode(analise_path.read_bytes()).decode()

        resumo_fiscal: dict[str, int] = {}
        for item in result.analise:
            resumo_fiscal[item["status"]] = resumo_fiscal.get(item["status"], 0) + 1

        return JSONResponse({
            "base_fiscal":      result.base_fiscal_disponivel,
            "resumo_fiscal":    resumo_fiscal,
            "analise":          result.analise,
            "correcoes":        result.correcoes,
            "arquivo_analise":  arquivo_analise_b64,
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


def _require_fiscal() -> FiscalService:
    if _FISCAL is None:
        raise HTTPException(status_code=503, detail="Base fiscal não carregada. Execute src/fiscal_import.py.")
    return _FISCAL


def _parse_data(data: str | None) -> str | None:
    if not data:
        return None
    try:
        return date.fromisoformat(data).isoformat()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Data inválida (use AAAA-MM-DD)") from exc


@app.get("/api/fiscal/resumo")
async def resumo_fiscal():
    if _FISCAL is None:
        return {"disponivel": False}
    return {"disponivel": True, **_FISCAL.summary()}


@app.get("/api/fiscal/ncm/{ncm}")
async def consultar_ncm(ncm: str, data: str | None = None):
    return _require_fiscal().validate_ncm(ncm[:20], _parse_data(data))


@app.get("/api/fiscal/cest/{cest}")
async def consultar_cest(cest: str, data: str | None = None):
    return _require_fiscal().validate_cest(cest[:20], _parse_data(data))


@app.get("/api/fiscal/validar")
async def validar_ncm_cest(ncm: str, cest: str = "", data: str | None = None):
    return _require_fiscal().validate_ncm_cest(ncm[:20], cest[:20], _parse_data(data)).to_dict()
