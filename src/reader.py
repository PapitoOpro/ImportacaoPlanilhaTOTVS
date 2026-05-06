# -*- coding: utf-8 -*-
import unicodedata
import re
from pathlib import Path

import pandas as pd


def _normalize_col(name: str) -> str:
    """Remove acentos, uppercase, colapsa espaços e quebras de linha."""
    name = unicodedata.normalize("NFD", str(name))
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    name = re.sub(r"[\s\n\r]+", " ", name).strip().upper()
    return name


def read_client_file(path: str | Path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {path}")

    ext = path.suffix.lower()
    if ext in (".xlsx", ".xlsm"):
        # Tenta header=0; se as colunas não baterem com nenhum campo esperado,
        # assume estrutura TOTVS (linha 0 = títulos de seção, linha 1 = cabeçalho real)
        df = pd.read_excel(path, dtype=str, header=0, engine="openpyxl")
        first_cols = [_normalize_col(c) for c in df.columns]
        try:
            from config import COLUMN_MAP as _cm
        except ImportError:
            from .config import COLUMN_MAP as _cm
        expected = set(_cm.keys())
        if not any(c in expected for c in first_cols):
            df = pd.read_excel(path, dtype=str, header=1, engine="openpyxl",
                               sheet_name="1. Produtos de Venda")
    elif ext == ".xls":
        # Planilha TOTVS Food 5.0: linha 0 = títulos de seção, linha 1 = cabeçalho real
        df = pd.read_excel(path, dtype=str, header=1, engine="xlrd",
                           sheet_name="1. Produtos de Venda")
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
