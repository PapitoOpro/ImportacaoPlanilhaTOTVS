# -*- coding: utf-8 -*-
import json
import logging
import re
import unicodedata
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_ABA_PRODUTOS = "1. Produtos de Venda"


def _normalize_col(name: str) -> str:
    """Remove acentos, uppercase, colapsa espaços e quebras de linha."""
    name = unicodedata.normalize("NFD", str(name))
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    name = re.sub(r"[\s\n\r]+", " ", name).strip().upper()
    return name


def _hidden_rows_xlsx(path: Path, sheet: str | None) -> set[int]:
    """Linhas (1-based, como no Excel) ocultas ou filtradas na aba; `sheet` None = primeira aba."""
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=False, data_only=True)
    try:
        ws = wb[sheet] if sheet else wb.worksheets[0]
        return {i for i, dim in ws.row_dimensions.items() if dim.hidden}
    finally:
        wb.close()


def _hidden_rows_xls(path: Path, sheet: str) -> set[int]:
    import xlrd

    book = xlrd.open_workbook(path, formatting_info=True)
    info = book.sheet_by_name(sheet).rowinfo_map
    return {r + 1 for r, row in info.items() if row.hidden}


def _drop_hidden(df: pd.DataFrame, hidden: set[int], header_row: int, arquivo: str) -> pd.DataFrame:
    """Linha oculta/filtrada no Excel não faz parte do cadastro (muitas vezes é sobra de outro cliente).

    O índice original é preservado: os números de linha dos relatórios continuam batendo com o Excel."""
    excel_rows = df.index + header_row + 1
    mask = excel_rows.isin(hidden)
    if mask.any():
        logger.info(json.dumps({"event": "linhas_ocultas_ignoradas", "arquivo": arquivo,
                                "total": int(mask.sum()), "linhas": [int(r) for r in excel_rows[mask][:50]]}))
    return df[~mask]


def read_client_file(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {path}")

    ext = path.suffix.lower()
    if ext in (".xlsx", ".xlsm"):
        # Tenta header=0; se as colunas não baterem com nenhum campo esperado,
        # assume estrutura TOTVS (linha 0 = títulos de seção, linha 1 = cabeçalho real)
        df = pd.read_excel(path, dtype=str, header=0, engine="openpyxl")
        header_row, sheet = 1, None
        first_cols = [_normalize_col(c) for c in df.columns]
        try:
            from config import COLUMN_MAP as _cm
        except ImportError:
            from .config import COLUMN_MAP as _cm
        expected = set(_cm.keys())
        if not any(c in expected for c in first_cols):
            df = pd.read_excel(path, dtype=str, header=1, engine="openpyxl", sheet_name=_ABA_PRODUTOS)
            header_row, sheet = 2, _ABA_PRODUTOS
        df = _drop_hidden(df, _hidden_rows_xlsx(path, sheet), header_row, path.name)
    elif ext == ".xls":
        # Planilha TOTVS Food 5.0: linha 0 = títulos de seção, linha 1 = cabeçalho real
        df = pd.read_excel(path, dtype=str, header=1, engine="xlrd", sheet_name=_ABA_PRODUTOS)
        df = _drop_hidden(df, _hidden_rows_xls(path, _ABA_PRODUTOS), 2, path.name)
    elif ext == ".csv":
        df = _read_csv(path)
    else:
        raise ValueError(f"Formato não suportado: {ext}")

    df = df.dropna(how="all")
    df.columns = [_normalize_col(c) for c in df.columns]
    return df


def _read_csv(path: Path) -> pd.DataFrame:
    for enc in ("utf-8", "latin-1", "cp1252"):
        try:
            return pd.read_csv(path, dtype=str, encoding=enc,
                               sep=None, engine="python")
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Não foi possível decodificar o CSV: {path.name}")
