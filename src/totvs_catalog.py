# -*- coding: utf-8 -*-
"""Catálogo de NCM/CEST cadastrados no TOTVS do cliente (exportado do banco do sistema).

O TOTVS só aceita na importação códigos já cadastrados nele; esta base permite barrar antes.

Uso:
    python src/totvs_catalog.py --arquivo "NCM E CEST TOTVS.xlsx"
"""
import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fiscal_db import connect  # noqa: E402

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS totvs_catalogo (
    tipo      TEXT NOT NULL CHECK (tipo IN ('NCM', 'CEST')),
    codigo    TEXT NOT NULL,
    descricao TEXT,
    ativo     INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1)),
    PRIMARY KEY (tipo, codigo)
);
CREATE TABLE IF NOT EXISTS totvs_catalogo_importacao (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    arquivo         TEXT NOT NULL,
    data_importacao TEXT NOT NULL,
    total_ncm       INTEGER NOT NULL,
    total_cest      INTEGER NOT NULL
);
"""

_SHEETS: dict[str, tuple[str, int]] = {"NCM": ("NCM", 8), "CEST": ("CEST", 7)}
_COL_CODIGO = {"NCM": "Codigo", "CEST": "Cod_CEST"}


class CatalogoError(Exception):
    """Planilha de exportação fora do formato esperado."""


def _read_sheet(wb: Any, tipo: str, tamanho: int) -> dict[str, tuple[str, int]]:
    if tipo not in wb.sheetnames:
        raise CatalogoError(f"Aba '{tipo}' não encontrada")
    rows = wb[tipo].iter_rows(values_only=True)
    header = [str(h).strip() if h is not None else "" for h in next(rows, ())]
    col = _COL_CODIGO[tipo]
    if col not in header:
        raise CatalogoError(f"Coluna '{col}' não encontrada na aba '{tipo}'")
    i_cod, i_desc = header.index(col), header.index("Descricao") if "Descricao" in header else None
    i_inativo = header.index("Inativo") if "Inativo" in header else None

    result: dict[str, tuple[str, int]] = {}
    for row in rows:
        digits = "".join(c for c in str(row[i_cod] if row[i_cod] is not None else "") if c.isdigit())
        if not digits or len(digits) > tamanho:
            continue  # linha inválida/descartada; o código vem numérico e perde zeros à esquerda
        ativo = 0 if i_inativo is not None and str(row[i_inativo]).strip() in ("1", "True") else 1
        desc = str(row[i_desc]).strip(' "') if i_desc is not None and row[i_desc] not in (None, "NULL") else ""
        codigo = digits.zfill(tamanho)
        anterior = result.get(codigo)
        result[codigo] = (anterior[0] if anterior and not desc else desc, max(ativo, anterior[1] if anterior else 0))
    return result


def import_catalog(conn: sqlite3.Connection, path: Path) -> dict[str, int]:
    """Substitui o catálogo atual pelo conteúdo da exportação. Atômico."""
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except (OSError, ValueError, KeyError) as exc:
        raise CatalogoError(f"Arquivo ilegível: {exc}") from exc
    try:
        dados = {tipo: _read_sheet(wb, tipo, tam) for tipo, (_, tam) in _SHEETS.items()}
    finally:
        wb.close()

    if not dados["NCM"] or not dados["CEST"]:
        raise CatalogoError("Exportação sem NCM ou sem CEST")

    conn.executescript(SCHEMA)
    with conn:
        conn.execute("DELETE FROM totvs_catalogo")
        for tipo, itens in dados.items():
            conn.executemany(
                "INSERT INTO totvs_catalogo (tipo, codigo, descricao, ativo) VALUES (?, ?, ?, ?)",
                [(tipo, c, d, a) for c, (d, a) in itens.items()],
            )
        conn.execute(
            "INSERT INTO totvs_catalogo_importacao (arquivo, data_importacao, total_ncm, total_cest) "
            "VALUES (?, ?, ?, ?)",
            (path.name, datetime.now().isoformat(timespec="seconds"), len(dados["NCM"]), len(dados["CEST"])),
        )
    totais = {"ncm": len(dados["NCM"]), "cest": len(dados["CEST"])}
    logger.info(json.dumps({"event": "catalogo_totvs_importado", "arquivo": path.name, **totais}))
    return totais


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Importa o cadastro NCM/CEST exportado do TOTVS.")
    ap.add_argument("--arquivo", type=Path, required=True)
    ap.add_argument("--db", type=Path, help="Caminho do SQLite (padrão: data/fiscal.db)")
    args = ap.parse_args(argv)

    conn = connect(args.db)
    try:
        totais = import_catalog(conn, args.arquivo)
    except CatalogoError as exc:
        logger.error(json.dumps({"event": "catalogo_totvs_falhou", "erro": str(exc)}, ensure_ascii=False))
        return 1
    finally:
        conn.close()
    print(f"Catálogo TOTVS importado: {totais['ncm']} NCMs, {totais['cest']} CESTs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
