# -*- coding: utf-8 -*-
"""O cadastro do produto herda a regra fiscal final da mesma linha (alinhado com a planilha de Regra NCM)."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fiscal import RegraFiscal, build_regras  # noqa: E402
from produto_fiscal import aplicar_fiscal_produtos  # noqa: E402


def _produtos() -> pd.DataFrame:
    cols = ["COD_NCM", "Cod_CEST", "CFOP_Venda", "Tributo", "Imposto", "CSOSN", "CST_CSOSN_Venda", "CST_PIS",
            "CST_COFINS", "PIS", "COFINS", "CodigoBeneficioRBC"]
    base = {"COD_NCM": "21069090", "CFOP_Venda": "5102", "Tributo": "I", "Imposto": "0", "CSOSN": "1",
            "CST_CSOSN_Venda": "102"}
    return pd.DataFrame([{c: base.get(c, "") for c in cols} for _ in range(3)], index=[0, 1, 5])


def test_simples_herda_regra_da_mesma_linha_e_preserva_as_demais() -> None:
    regra = RegraFiscal("22030000", "0302100", "S", 0.0, "5405", "500", "", None, None, None)
    df = aplicar_fiscal_produtos(_produtos(), {3: regra}, "1")  # idx 0 ↔ linha 3 da planilha do cliente
    assert df.loc[0, ["COD_NCM", "Cod_CEST", "CFOP_Venda", "Tributo", "CST_CSOSN_Venda", "CSOSN"]].tolist() == \
        ["22030000", "0302100", "5405", "S", "500", "1"]
    assert df.loc[1, "COD_NCM"] == "21069090" and df.loc[5, "CST_CSOSN_Venda"] == "102"  # sem regra: padrão


def test_regime_normal_marca_csosn_zero_e_leva_pis_cofins() -> None:
    regra = RegraFiscal("22030000", "0302100", "S", 18.0, "5405", "060", "", None, ("04", None, None), ("04", 7.6, None))
    df = aplicar_fiscal_produtos(_produtos(), {3: regra}, "3")
    linha = df.loc[0]
    assert (linha["CSOSN"], linha["Imposto"], linha["CST_PIS"], linha["CST_COFINS"], linha["COFINS"]) == \
        ("0", "18,00", "04", "04", "7,60")


def test_build_regras_expoe_regra_final_por_linha() -> None:
    df = pd.DataFrame([{"NOME PRODUTO": "ITEM", "NCM": "99011000", "CEST": "", "TRIBUTO": "T",
                        "IMPOSTO (% ICMS)": "18", "CFOP": "", "CST OU CSOSN": "00"}])
    r = build_regras(df, "3")
    assert r.por_linha[3].cfop == "5102" and len(r.por_linha) == 1
