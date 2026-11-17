# -*- coding: utf-8 -*-
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from obrigatorios import garantir_obrigatorios  # noqa: E402


def _df(rows: list[tuple[str | None, str]]) -> pd.DataFrame:
    return pd.DataFrame([{"CODIGO PRODUTO VENDA": c, "NOME PRODUTO": n} for c, n in rows])


def _pares(df: pd.DataFrame) -> list[tuple[str | None, str]]:
    return list(zip(df["CODIGO PRODUTO VENDA"], df["NOME PRODUTO"]))


def test_inclui_quando_ausentes() -> None:
    df, correcoes = garantir_obrigatorios(_df([(None, "AGUA")]))
    assert _pares(df)[1:] == [("999", "TDS"), ("997", "TDE")]
    assert [c["para"] for c in correcoes] == ["999", "997"]
    assert all("incluído" in c["motivo"] for c in correcoes)


def test_ja_existentes_nao_mudam_nada() -> None:
    entrada = _df([("999", "TDS"), ("997.0", "tde"), ("1", "AGUA")])
    df, correcoes = garantir_obrigatorios(entrada)
    assert correcoes == [] and len(df) == 3


def test_codigo_com_outro_nome_e_corrigido_e_informado() -> None:
    df, correcoes = garantir_obrigatorios(_df([("999", "TAXA DE SERVIÇO 10%"), ("997", "TDE")]))
    assert _pares(df)[0] == ("999", "TDS")
    assert len(correcoes) == 1 and correcoes[0]["de"] == "TAXA DE SERVIÇO 10%" and correcoes[0]["linha"] == 3


def test_nome_com_outro_codigo_e_corrigido_e_informado() -> None:
    df, correcoes = garantir_obrigatorios(_df([("50", "Taxa de Entrega"), ("999", "TDS")]))
    assert _pares(df)[0] == ("997", "TDE")
    assert [c["campo"] for c in correcoes] == ["Código Produto", "Nome Produto"]
    assert len(df) == 2  # nada incluído: o item existia


def test_item_repetido_e_sinalizado_sem_apagar() -> None:
    df, correcoes = garantir_obrigatorios(_df([("10", "TDS"), ("11", "TAXA DE SERVICO"), ("997", "TDE")]))
    assert len(df) == 3
    assert any("repetido" in c["motivo"] and c["linha"] == 4 for c in correcoes)
