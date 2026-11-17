# -*- coding: utf-8 -*-
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from obrigatorios import garantir_obrigatorios, ocultar_do_cardapio  # noqa: E402


def _df(rows: list[tuple[str | None, str]], grupos: dict[str, str] | None = None) -> pd.DataFrame:
    df = pd.DataFrame([{"CODIGO PRODUTO VENDA": c, "NOME PRODUTO": n} for c, n in rows])
    for col, valor in (grupos or {}).items():
        df[col] = valor
    return df


def _pares(df: pd.DataFrame) -> list[tuple[str | None, str]]:
    return list(zip(df["CODIGO PRODUTO VENDA"], df["NOME PRODUTO"]))


def test_inclui_quando_ausentes_com_padrao_de_taxas() -> None:
    df, correcoes, erros = garantir_obrigatorios(_df([(None, "AGUA")]))
    assert _pares(df)[1:] == [("999", "TDS"), ("997", "TDE")]
    tds = df.iloc[1]
    assert (tds["UNIDADE DE MEDIDA"], tds["PRECO DE VENDA"], tds["GRUPO"], tds["SUBGRUPO"]) == ("UN", "1,00", "Taxas", "Taxas")
    assert [c["para"] for c in correcoes] == ["999", "997"] and not erros
    assert all("incluído" in c["motivo"] for c in correcoes)


def test_usa_grafia_de_grupo_ja_existente_no_cliente() -> None:
    df, _, _ = garantir_obrigatorios(_df([("1", "AGUA"), ("2", "SERVICO")], grupos={"GRUPO": "TAXAS"}))
    assert df.iloc[2]["GRUPO"] == "TAXAS"


def test_ja_existentes_nao_mudam_nada() -> None:
    df, correcoes, erros = garantir_obrigatorios(_df([("999", "TDS"), ("997.0", "tde"), ("1", "AGUA")]))
    assert correcoes == [] and erros == [] and len(df) == 3


def test_codigo_usado_por_outro_produto_barra_a_linha() -> None:
    df, correcoes, erros = garantir_obrigatorios(_df([("999", "TAXA DE SERVIÇO 10%"), ("997", "TDE")]))
    assert _pares(df)[0] == ("999", "TAXA DE SERVIÇO 10%")  # não renomeia
    assert len(erros) == 1 and erros[0].row == 3 and erros[0].field == "Código Produto"
    assert "TDS" in erros[0].reason and "Troque o código" in erros[0].reason
    assert correcoes == []


def test_nome_com_outro_codigo_e_corrigido_e_informado() -> None:
    df, correcoes, erros = garantir_obrigatorios(_df([("50", "Taxa de Entrega"), ("999", "TDS")]))
    assert _pares(df)[0] == ("997", "TDE")
    assert [c["campo"] for c in correcoes] == ["Código Produto", "Nome Produto"]
    assert len(df) == 2 and not erros


def test_item_repetido_e_sinalizado_sem_apagar() -> None:
    df, correcoes, _ = garantir_obrigatorios(_df([("10", "TDS"), ("11", "TAXA DE SERVICO"), ("997", "TDE")]))
    assert len(df) == 3
    assert any("repetido" in c["motivo"] and c["linha"] == 4 for c in correcoes)


def test_taxas_nao_aparecem_no_cardapio() -> None:
    df = pd.DataFrame({"Código Produto": ["1", "999", "997"], "Não Exibir no Cardápio": ["0", "0", "0"]})
    assert list(ocultar_do_cardapio(df)["Não Exibir no Cardápio"]) == ["0", "1", "1"]
