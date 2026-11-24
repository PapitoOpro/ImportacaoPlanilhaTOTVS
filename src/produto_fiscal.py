# -*- coding: utf-8 -*-
"""Alinha o cadastro de produtos com as regras fiscais: o produto herda a regra final de `build_regras`."""
from __future__ import annotations

import pandas as pd

from fiscal import _FIRST_DATA_ROW, _SIMPLES, RegraFiscal


def _num(valor: float | None) -> str:
    return f"{valor:.2f}".replace(".", ",") if valor is not None else ""


def _campos(regra: RegraFiscal, regime: str) -> dict[str, str]:
    simples = regime in _SIMPLES
    campos = {
        "COD_NCM": regra.ncm,
        "Cod_CEST": regra.cest,
        "CFOP_Venda": regra.cfop,
        "Tributo": regra.tributo,
        "Imposto": _num(regra.imposto),
        "CSOSN": "1" if simples else "0",
        "CST_CSOSN_Venda": regra.cst_csosn,
    }
    if regra.beneficio:
        campos["CodigoBeneficioRBC"] = regra.beneficio
    if not simples:
        for nome, grupo in (("PIS", regra.pis), ("COFINS", regra.cofins)):
            if grupo:
                cst, aliquota, _ = grupo
                campos[f"CST_{nome}"] = cst
                if aliquota is not None:
                    campos[nome] = _num(aliquota)
    return campos


def aplicar_fiscal_produtos(df: pd.DataFrame, por_linha: dict[int, RegraFiscal], regime: str) -> pd.DataFrame:
    """Sobrescreve os campos fiscais do produto pela regra da mesma linha do cliente.
    Linhas sem regra (ex.: taxas 999/997 incluídas pelo sistema) mantêm o padrão do template."""
    df = df.copy()
    for idx in df.index:
        regra = por_linha.get(int(idx) + _FIRST_DATA_ROW)
        if regra is None:
            continue
        for coluna, valor in _campos(regra, regime).items():
            if coluna in df.columns:
                df.at[idx, coluna] = valor
    return df
