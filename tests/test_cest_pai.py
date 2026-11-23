# -*- coding: utf-8 -*-
"""CEST subdividido ausente no TOTVS: usa o item pai (SS.III.00) se for oficial do NCM e aceito pelo TOTVS."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fiscal import _cest_pai, build_regras  # noqa: E402
from fiscal_db import connect  # noqa: E402
from fiscal_service import FiscalService  # noqa: E402

DB = Path(__file__).resolve().parent.parent / "data" / "fiscal.db"


def test_cest_pai() -> None:
    assert _cest_pai("0301001") == "0301000" and _cest_pai("0301000") == "0301000"


def _df(ncm: str, cest: str) -> pd.DataFrame:
    return pd.DataFrame([{"NOME PRODUTO": "ITEM", "NCM": ncm, "CEST": cest, "TRIBUTO": "S", "IMPOSTO (% ICMS)": "0",
                          "CFOP": "5405", "CST OU CSOSN": "500"}])


def test_refrigerante_pet_vai_para_item_pai_e_cerveja_segue_erro() -> None:
    service = FiscalService(connect(DB))
    r = build_regras(_df("22021000", "0301001"), "1", service=service, reference_date="2026-11-23")
    assert r.regras[0].cest == "0301000" and not r.errors
    assert "item pai" in next(c for c in r.correcoes if c["campo"] == "CEST")["motivo"]
    cerveja = build_regras(_df("22030000", "0302101"), "1", service=service, reference_date="2026-11-23")
    assert not cerveja.regras and any(e.field == "CEST" for e in cerveja.errors)
