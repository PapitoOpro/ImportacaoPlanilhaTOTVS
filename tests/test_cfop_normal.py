# -*- coding: utf-8 -*-
"""Regime Normal: CFOP em branco é sugerido a partir do CST informado; CST nunca é inventado."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fiscal import build_regras  # noqa: E402


def _df(**campos: str) -> pd.DataFrame:
    base = {"NOME PRODUTO": "ITEM", "NCM": "99011000", "CEST": "", "TRIBUTO": "T", "IMPOSTO (% ICMS)": "18",
            "CFOP": "", "CST OU CSOSN": "00"}
    return pd.DataFrame([{**base, **campos}])


def test_cst_sem_st_sugere_5102() -> None:
    r = build_regras(_df(), "3")
    assert r.regras[0].cfop == "5102" and not r.errors
    c = next(c for c in r.correcoes if c["campo"] == "CFOP")
    assert c["para"] == "5102" and "sem ST" in c["motivo"]


def test_cst_de_st_sugere_5405() -> None:
    r = build_regras(_df(**{"CST OU CSOSN": "060", "TRIBUTO": "S", "IMPOSTO (% ICMS)": "0", "CEST": "9900900"}), "3")
    assert r.regras[0].cfop == "5405" and not r.errors
    assert next(c for c in r.correcoes if c["campo"] == "CFOP")["para"] == "5405"


def test_tributo_s_com_cst_de_st_de_dois_digitos() -> None:
    assert build_regras(_df(**{"CST OU CSOSN": "60", "CEST": "9900900"}), "3").regras[0].cfop == "5405"


def test_cst_em_branco_continua_rejeitando_e_explica_por_que_nao_sugere() -> None:
    r = build_regras(_df(**{"CST OU CSOSN": ""}), "3")
    assert not r.regras
    campos = {e.field: e.reason for e in r.errors}
    assert "não é sugerido" in campos["CST ou CSOSN"]
    assert "CFOP" in campos  # sem CST não dá para decidir se há ST


def test_cfop_informado_e_sugestoes_recusadas_nao_mudam() -> None:
    assert build_regras(_df(CFOP="5102"), "3").regras[0].cfop == "5102"
    informado = build_regras(_df(CFOP="5949"), "3")
    assert informado.regras[0].cfop == "5949" and not any(c["campo"] == "CFOP" for c in informado.correcoes)
    recusado = build_regras(_df(), "3", aplicar_sugestoes=False)
    assert not recusado.regras and any(e.field == "CFOP" for e in recusado.errors)


def test_simples_segue_com_o_padrao_proprio() -> None:
    r = build_regras(_df(**{"CST OU CSOSN": "102"}), "1")
    assert r.regras[0].cfop == "5102"
    assert "Simples" in next(c for c in r.correcoes if c["campo"] == "CFOP")["motivo"]
