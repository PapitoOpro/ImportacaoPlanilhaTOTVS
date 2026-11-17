# -*- coding: utf-8 -*-
"""Produtos obrigatórios do TOTVS Food: 999 TDS (taxa de serviço) e 997 TDE (taxa de entrega).

O nome deve ser exatamente a sigla (sem a descrição). Opera na planilha do cliente, antes da transformação.
"""
import re
import unicodedata
from typing import Any

import pandas as pd

COL_CODIGO = "CODIGO PRODUTO VENDA"
COL_NOME = "NOME PRODUTO"
_FIRST_DATA_ROW = 3  # linha 1 = seções, linha 2 = cabeçalho na planilha do cliente

# (código, sigla, prefixos que identificam o mesmo item com outro nome)
OBRIGATORIOS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("999", "TDS", ("TDS", "TAXA DE SERVICO", "TAXA SERVICO")),
    ("997", "TDE", ("TDE", "TAXA DE ENTREGA", "TAXA ENTREGA")),
)


def _norm(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    text = unicodedata.normalize("NFD", str(value).upper())
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", text).strip()


def _codigo(value: Any) -> str:
    text = _norm(value)
    if re.fullmatch(r"\d+\.0+", text):  # "999.0" (Excel numérico)
        text = text.split(".")[0]
    return re.sub(r"\D", "", text)


def _correcao(linha: int | str, campo: str, de: str, para: str, motivo: str) -> dict[str, Any]:
    return {"linha": linha, "campo": campo, "de": de, "para": para, "motivo": motivo}


def garantir_obrigatorios(df: pd.DataFrame) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    """Confere 999 TDS e 997 TDE: inclui se faltar; se existir com outro nome/código, corrige e informa."""
    df = df.copy()
    correcoes: list[dict[str, Any]] = []
    if COL_CODIGO not in df.columns:
        df[COL_CODIGO] = ""
    if COL_NOME not in df.columns:
        df[COL_NOME] = ""

    for codigo, sigla, aliases in OBRIGATORIOS:
        codigos = df[COL_CODIGO].map(_codigo)
        nomes = df[COL_NOME].map(_norm)

        por_codigo = list(df.index[codigos == codigo])
        if por_codigo:
            for idx in por_codigo:
                if nomes[idx] != sigla:
                    correcoes.append(_correcao(
                        int(idx) + _FIRST_DATA_ROW, "Nome Produto", str(df.at[idx, COL_NOME]), sigla,
                        f"O código {codigo} é obrigatório do sistema e o nome deve ser apenas {sigla}. "
                        "Corrija na planilha do cliente."))
                    df.at[idx, COL_NOME] = sigla
            continue

        por_nome = [i for i in df.index if any(nomes[i] == a or nomes[i].startswith(a + " ") for a in aliases)]
        if por_nome:
            idx = por_nome[0]
            correcoes.append(_correcao(
                int(idx) + _FIRST_DATA_ROW, "Código Produto", str(df.at[idx, COL_CODIGO] or ""), codigo,
                f"O item de {sigla} deve ter o código {codigo} (obrigatório do sistema). Corrija na planilha do cliente."))
            df.at[idx, COL_CODIGO] = codigo
            if nomes[idx] != sigla:
                correcoes.append(_correcao(
                    int(idx) + _FIRST_DATA_ROW, "Nome Produto", str(df.at[idx, COL_NOME]), sigla,
                    f"O nome do item {codigo} deve ser apenas {sigla}. Corrija na planilha do cliente."))
                df.at[idx, COL_NOME] = sigla
            for extra in por_nome[1:]:
                correcoes.append(_correcao(
                    int(extra) + _FIRST_DATA_ROW, "Nome Produto", str(df.at[extra, COL_NOME]), "",
                    f"Item de {sigla} repetido: o sistema só tem o código {codigo}. Remova na planilha do cliente."))
            continue

        novo = (int(df.index.max()) + 1) if len(df.index) else 0
        df.loc[novo, COL_CODIGO] = codigo
        df.loc[novo, COL_NOME] = sigla
        correcoes.append(_correcao(
            "—", "Código Produto", "", codigo,
            f"Item obrigatório do sistema ausente na planilha; incluído {codigo} {sigla}."))
    return df, correcoes
