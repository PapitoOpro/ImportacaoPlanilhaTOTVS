# -*- coding: utf-8 -*-
"""Planilha sem dados fiscais entra com o fiscal genérico do template, mesmo com regime selecionado."""
import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "src"))

from api import app  # noqa: E402
from fiscal import tem_dados_fiscais  # noqa: E402


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
