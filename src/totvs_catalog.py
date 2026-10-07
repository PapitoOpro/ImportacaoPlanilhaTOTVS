# -*- coding: utf-8 -*-
"""Catálogo de NCM/CEST cadastrados no TOTVS do cliente (exportado do banco do sistema).

O TOTVS só aceita na importação códigos já cadastrados nele; esta base permite barrar antes.

Uso:
    python src/totvs_catalog.py --arquivo "NCM E CEST TOTVS.xlsx"
"""
import argparse
import json
import logging
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import openpyxl
import pandas as pd

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
-- Códigos que o TOTVS recusou numa importação real (aprendidos do ErrosImportacao.txt).
CREATE TABLE IF NOT EXISTS totvs_recusados (
    tipo      TEXT NOT NULL CHECK (tipo IN ('NCM', 'CEST')),
    codigo    TEXT NOT NULL,
    motivo    TEXT NOT NULL,
    data      TEXT NOT NULL,
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
        dados = {"CEST": _read_sheet(wb, "CEST", 7)}  # NCM vem da tabela MDIC (import_ncm_mdic), não desta aba
    finally:
        wb.close()

    if not dados["CEST"]:
        raise CatalogoError("Exportação sem CEST")

    conn.executescript(SCHEMA)
    with conn:
        conn.execute("DELETE FROM totvs_catalogo WHERE tipo = 'CEST'")
        for tipo, itens in dados.items():
            conn.executemany(
                "INSERT INTO totvs_catalogo (tipo, codigo, descricao, ativo) VALUES (?, ?, ?, ?)",
                [(tipo, c, d, a) for c, (d, a) in itens.items()],
            )
        conn.execute(
            "INSERT INTO totvs_catalogo_importacao (arquivo, data_importacao, total_ncm, total_cest) "
            "VALUES (?, ?, ?, ?)",
            (path.name, datetime.now().isoformat(timespec="seconds"), 0, len(dados["CEST"])),
        )
    totais = {"cest": len(dados["CEST"])}
    logger.info(json.dumps({"event": "catalogo_totvs_importado", "arquivo": path.name, **totais}))
    return totais


def _detect_code_column(df: Any) -> str:
    """Coluna do código NCM: pelo nome (ncm/codigo) ou, na falta, a que mais tem valores de 7-8 dígitos."""
    for col in df.columns:
        nome = re.sub(r"[^a-z]", "", str(col).lower())
        if "ncm" in nome or nome in ("codigo", "cod"):
            return col

    def parece_ncm(v: Any) -> bool:
        return len(re.sub(r"\D", "", str(v))) in (7, 8)

    melhor = max(df.columns, key=lambda c: df[c].map(parece_ncm).mean())
    if df[melhor].map(parece_ncm).mean() < 0.5:
        raise CatalogoError("Não encontrei a coluna de código NCM no arquivo")
    return melhor


def import_ncm_mdic(conn: sqlite3.Connection, path: Path) -> int:
    """Carrega a tabela MDIC do sistema (TabelaMDICCodigoNCM exportada em xlsx/csv): a lista de NCMs
    que o TOTVS aceita. Substitui a anterior."""
    try:
        if path.suffix.lower() == ".csv":
            df = pd.read_csv(path, dtype=str, sep=None, engine="python", encoding="utf-8-sig")
        else:
            df = pd.read_excel(path, dtype=str)
    except (OSError, ValueError) as exc:
        raise CatalogoError(f"Arquivo ilegível: {exc}") from exc

    coluna = _detect_code_column(df)
    col_inativo = next((c for c in df.columns if str(c).strip().lower() == "inativo"), None)
    codigos: dict[str, int] = {}  # código -> ativo (o NCM repete por CEST vinculado)
    for _, linha in df.iterrows():
        digitos = re.sub(r"\D", "", str(linha[coluna]))
        if len(digitos) not in (7, 8):  # Excel/SQL numérico perde o zero à esquerda
            continue
        inativo = col_inativo is not None and str(linha[col_inativo]).strip() in ("1", "True", "true")
        codigo = digitos.zfill(8)
        codigos[codigo] = max(codigos.get(codigo, 0), 0 if inativo else 1)
    if not codigos:
        raise CatalogoError("Nenhum NCM de 7/8 dígitos encontrado")

    conn.executescript(SCHEMA)
    with conn:
        conn.execute("DELETE FROM totvs_catalogo WHERE tipo = 'NCM'")
        conn.executemany("INSERT INTO totvs_catalogo (tipo, codigo, descricao, ativo) VALUES ('NCM', ?, '', ?)",
                         sorted(codigos.items()))
    logger.info(json.dumps({"event": "ncm_mdic_importado", "arquivo": path.name, "coluna": str(coluna),
                            "total": len(codigos)}, ensure_ascii=False))
    return len(codigos)


_ERRO_LINHA = re.compile(r"Aba:\s*(?P<aba>.+?)\s*\|\s*C.lula:\s*(?P<ref>[A-Z]+\d+)\s*\|\s*(?P<tipo>NCM|CEST)\s+Inv", re.I)


def parse_erros_totvs(texto: str, planilha: Path) -> dict[tuple[str, str], str]:
    """Lê o ErrosImportacao.txt do TOTVS e devolve {(tipo, código): motivo}, buscando o valor
    na célula indicada da planilha que foi importada."""
    wb = openpyxl.load_workbook(planilha, read_only=False, data_only=True)
    try:
        recusados: dict[tuple[str, str], str] = {}
        for linha in texto.splitlines():
            m = _ERRO_LINHA.search(linha)
            if not m or m["aba"].strip() not in wb.sheetnames:
                continue
            tipo = m["tipo"].upper()
            valor = wb[m["aba"].strip()][m["ref"]].value
            digitos = "".join(c for c in str(valor if valor is not None else "") if c.isdigit())
            tamanho = _SHEETS[tipo][1]
            if digitos and len(digitos) <= tamanho:
                recusados[(tipo, digitos.zfill(tamanho))] = f"{tipo} Inválido (recusado na importação do TOTVS)"
        return recusados
    finally:
        wb.close()


def registrar_recusados(conn: sqlite3.Connection, texto_erros: str, planilha: Path) -> list[tuple[str, str]]:
    recusados = parse_erros_totvs(texto_erros, planilha)
    conn.executescript(SCHEMA)
    agora = datetime.now().isoformat(timespec="seconds")
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO totvs_recusados (tipo, codigo, motivo, data) VALUES (?, ?, ?, ?)",
            [(t, c, motivo, agora) for (t, c), motivo in recusados.items()],
        )
    logger.info(json.dumps({"event": "recusados_totvs_registrados", "total": len(recusados),
                            "codigos": sorted(f"{t}:{c}" for t, c in recusados)}))
    return sorted(recusados)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Importa o cadastro NCM/CEST exportado do TOTVS.")
    ap.add_argument("--arquivo", type=Path, help="Exportação NCM/CEST do TOTVS (xlsx)")
    ap.add_argument("--ncm-mdic", type=Path, help="Tabela MDIC do sistema (TabelaMDICCodigoNCM) em xlsx/csv")
    ap.add_argument("--erros", type=Path, help="ErrosImportacao.txt devolvido pelo TOTVS")
    ap.add_argument("--planilha", type=Path, help="Planilha que foi importada (para ler as células do erro)")
    ap.add_argument("--db", type=Path, help="Caminho do SQLite (padrão: data/fiscal.db)")
    args = ap.parse_args(argv)
    if not (args.arquivo or args.ncm_mdic or (args.erros and args.planilha)):
        ap.error("informe --arquivo, --ncm-mdic, ou --erros junto com --planilha")

    conn = connect(args.db)
    try:
        if args.arquivo:
            totais = import_catalog(conn, args.arquivo)
            print(f"Catálogo TOTVS importado: {totais['cest']} CESTs")
        if args.ncm_mdic:
            print(f"Tabela MDIC importada: {import_ncm_mdic(conn, args.ncm_mdic)} NCMs")
        if args.erros:
            texto = args.erros.read_bytes().decode("utf-8", errors="replace")
            for tipo, codigo in registrar_recusados(conn, texto, args.planilha):
                print(f"Recusado pelo TOTVS: {tipo} {codigo}")
    except (CatalogoError, OSError) as exc:
        logger.error(json.dumps({"event": "catalogo_totvs_falhou", "erro": str(exc)}, ensure_ascii=False))
        return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
