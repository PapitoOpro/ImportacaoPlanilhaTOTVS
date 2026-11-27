# -*- coding: utf-8 -*-
"""Planilha sem dados fiscais entra com o fiscal genérico do template, mesmo com regime selecionado."""
import base64
import io
import sys
from pathlib import Path

import openpyxl
import pandas as pd
import pytest
from fastapi.testclient import TestClient

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "src"))

from api import app  # noqa: E402
from fiscal import build_regras, tem_dados_fiscais  # noqa: E402


@pytest.mark.parametrize("linha, esperado", [
    ({"NOME PRODUTO": "ITEM", "NCM": None, "CFOP": "  "}, False),
    ({"NOME PRODUTO": "ITEM"}, False),
    ({"NOME PRODUTO": "ITEM", "NCM": "22021000"}, True),
    ({"NOME PRODUTO": "ITEM", "NCM": None, "CST OU CSOSN": "102"}, True),
])
def test_tem_dados_fiscais(linha: dict, esperado: bool) -> None:
    assert tem_dados_fiscais(pd.DataFrame([linha])) is esperado


def _enviar(ncm: str, **form: str) -> dict:
    csv = ("CODIGO PRODUTO VENDA;NOME PRODUTO;UNIDADE DE MEDIDA;GRUPO;SUBGRUPO;PRECO DE VENDA;NCM\n"
           f"1;AGUA;UN;BEBIDAS;AGUAS;4,00;{ncm}\n"
           "2;SUCO;UN;BEBIDAS;SUCOS;5,00;\n")
    resposta = TestClient(app).post("/processar", data=form, files={"file": ("c.csv", csv.encode(), "text/csv")})
    assert resposta.status_code == 200
    return resposta.json()


@pytest.mark.parametrize("regime", ["1", "3"])
def test_sem_dados_fiscais_gera_generico_sem_erros(regime: str) -> None:
    assert _enviar("", regime=regime, previa="true")["correcoes"] == []
    data = _enviar("", regime=regime)
    assert data["erros"] == [] and data["stats"]["rejeitados"] == 0
    assert data["dados_genericos"] is True


def test_com_dados_fiscais_mantem_validacao() -> None:
    data = _enviar("123", regime="1")
    assert data["dados_genericos"] is False
    assert any(e["campo"] == "NCM" for e in data["erros"])


@pytest.mark.parametrize("regime, regime_saida", [("1", "1"), ("2", "2"), ("3", "1")])
def test_regras_ncm_sem_dados_fiscais_gera_regra_generica(regime: str, regime_saida: str) -> None:
    df = pd.DataFrame([{"NOME PRODUTO": "AGUA"}, {"NOME PRODUTO": "SUCO"}])  # sem coluna NCM
    r = build_regras(df, regime)
    assert (r.dados_genericos, r.regime, r.errors, r.total_produtos, r.produtos_validos) == \
        (True, regime_saida, [], 2, 2)
    assert [(x.ncm, x.cest, x.tributo, x.imposto, x.cfop, x.cst_csosn) for x in r.regras] == \
        [("21069090", "", "I", 0.0, "5102", "102")]


def test_api_regras_ncm_sem_dados_fiscais() -> None:
    csv = "CODIGO PRODUTO VENDA;NOME PRODUTO;NCM\n1;AGUA;\n2;SUCO;\n"
    resposta = TestClient(app).post("/processar-regras-ncm", data={"regime": "3"},
                                    files={"file": ("c.csv", csv.encode(), "text/csv")})
    data = resposta.json()
    assert data["dados_genericos"] is True and data["erros"] == [] and data["stats"]["regras"] == 1
    aba = openpyxl.load_workbook(io.BytesIO(base64.b64decode(data["arquivo"])))["NCM X UF"]
    assert (aba["C2"].value, aba["E2"].value) == ("21069090", 1)
