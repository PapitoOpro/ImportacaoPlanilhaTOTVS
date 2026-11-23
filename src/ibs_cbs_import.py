# -*- coding: utf-8 -*-
"""Importa o vínculo NCM × classificação IBS/CBS do banco da calculadora oficial da Receita (MIT).

Uso:
    python src/ibs_cbs_import.py --arquivo caminho/calculadora-pro.db

O arquivo é aberto somente para leitura; as tabelas ibs_cbs_* do fiscal.db são substituídas
de forma atômica. Sem rede e sem Java.
"""
import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fiscal_db import connect  # noqa: E402

logger = logging.getLogger(__name__)

FONTE = "Receita Federal - calculadora-rtc (MIT)"
_TABELAS_ORIGEM = ("NCM_APLICAVEL", "EXCECAO_NCM_APLICAVEL", "CLASSIFICACAO_TRIBUTARIA", "SITUACAO_TRIBUTARIA", "VERSAO_BASE_DADO")


class ImportacaoIbsCbsError(Exception):
    """Banco de origem ausente ou fora do formato esperado."""


def _abrir_origem(origem: Path) -> sqlite3.Connection:
    if not origem.is_file():
        raise ImportacaoIbsCbsError(f"Arquivo não encontrado: {origem}")
    try:
        conn = sqlite3.connect(f"file:{origem.as_posix()}?mode=ro", uri=True)
        existentes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    except sqlite3.DatabaseError as exc:
        raise ImportacaoIbsCbsError(f"Arquivo não é um banco SQLite válido: {origem}") from exc
    faltando = [t for t in _TABELAS_ORIGEM if t not in existentes]
    if faltando:
        conn.close()
        raise ImportacaoIbsCbsError(f"Banco da Receita sem as tabelas esperadas: {', '.join(faltando)}")
    return conn


def importar(origem: Path, destino: sqlite3.Connection) -> dict[str, str | int]:
    src = _abrir_origem(origem)
    try:
        classes = src.execute(
            "SELECT c.CLTR_ID, c.CLTR_CD, c.CLTR_DESCRICAO, s.SITR_CD "
            "FROM CLASSIFICACAO_TRIBUTARIA c JOIN SITUACAO_TRIBUTARIA s ON s.SITR_ID = c.CLTR_SITR_ID"
        ).fetchall()
        vinculos = src.execute(
            "SELECT NCMA_ID, NCMA_NCM_CD, NCMA_CLTR_ID, NCMA_INICIO_VIGENCIA, NCMA_FIM_VIGENCIA FROM NCM_APLICAVEL"
        ).fetchall()
        excecoes = src.execute(
            "SELECT ENCM_ID, ENCM_NCMA_ID, ENCM_NCM_CD, ENCM_INICIO_VIGENCIA, ENCM_FIM_VIGENCIA "
            "FROM EXCECAO_NCM_APLICAVEL"
        ).fetchall()
        ultima = src.execute(
            "SELECT VRBD_VERSAO_BASE_DADO, substr(VRBD_DATA, 1, 10) FROM VERSAO_BASE_DADO "
            "ORDER BY VRBD_ID DESC LIMIT 1").fetchone()
        versao = f"{ultima[0]} ({ultima[1]})" if ultima else "desconhecida"
    finally:
        src.close()
    if not vinculos:
        raise ImportacaoIbsCbsError("Banco da Receita sem vínculos NCM × classificação; importação abortada.")

    with destino:  # transação única: ou troca tudo, ou nada
        for tabela in ("ncm_ibs_cbs_excecao", "ncm_ibs_cbs", "ibs_cbs_classificacao"):
            destino.execute(f"DELETE FROM {tabela}")
        destino.executemany(
            "INSERT INTO ibs_cbs_classificacao (id, cclasstrib, descricao, cst) VALUES (?, ?, ?, ?)", classes)
        destino.executemany(
            "INSERT INTO ncm_ibs_cbs (id, ncm_prefixo, classificacao_id, inicio_vigencia, fim_vigencia) "
            "VALUES (?, ?, ?, ?, ?)", [(i, n, c, ini, fim) for i, n, c, ini, fim in vinculos])
        destino.executemany(
            "INSERT INTO ncm_ibs_cbs_excecao (id, vinculo_id, ncm_prefixo, inicio_vigencia, fim_vigencia) "
            "VALUES (?, ?, ?, ?, ?)", excecoes)
        destino.execute(
            "INSERT INTO ibs_cbs_versao (id, versao, fonte, data_importacao) VALUES (1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET versao = excluded.versao, fonte = excluded.fonte, "
            "data_importacao = excluded.data_importacao",
            (versao, FONTE, datetime.now().isoformat(timespec="seconds")))
    resumo = {"versao": versao, "classificacoes": len(classes), "vinculos": len(vinculos), "excecoes": len(excecoes)}
    logger.info(json.dumps({"event": "ibs_cbs_importado", **resumo}, ensure_ascii=False))
    return resumo


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--arquivo", required=True, type=Path, help="calculadora-pro.db da Receita")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        resumo = importar(args.arquivo, connect())
    except ImportacaoIbsCbsError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    print(f"Importado (base {resumo['versao']}): {resumo['classificacoes']} classificações, "
          f"{resumo['vinculos']} vínculos NCM, {resumo['excecoes']} exceções.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
