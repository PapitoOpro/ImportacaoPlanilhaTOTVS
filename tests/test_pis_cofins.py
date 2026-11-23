# -*- coding: utf-8 -*-
"""PIS/COFINS por NCM (tabelas 4.3.x da EFD-Contribuições). Códigos FICTÍCIOS (capítulo 99)."""
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fiscal import build_regras  # noqa: E402
from fiscal_db import connect_memory  # noqa: E402
from fiscal_service import FiscalService  # noqa: E402
from pis_cofins_import import ImportacaoPisCofinsError, importar, parse_tabela  # noqa: E402

CAB_10 = "versão=37 CODIGO, DESC_PROD, DT_INI, DT_FIM, NCM, NCM_EX, EX_IPI, ALIQ_PIS, ALIQ_COFIN"
CAB_13 = "versão=53 CODIGO, DESC_PROD, DT_INI, DT_FIM, NCM, NCM_EX, EX_IPI"


def _arq(cabecalho: str, *linhas: str) -> bytes:
    return "\r\n".join([cabecalho, *linhas]).encode("latin-1")


# 4.3.10 (monofásico): 9901 inteiro, menos 99011100; 99012000 só para um EX da TIPI; 9903 encerrado; operação sem NCM
T10 = _arq(
    CAB_10,
    "101|Bebida fictícia A|01012018||9901;|99011100||1,00|2,00",
    "102|Peça fictícia EX|01012018||99012000||01|1,00|2,00",
    "103|Item encerrado|01012009|31122015|9903|||1,00|2,00",
    "104|Regra por operação|01012009||||||1,00|2,00",
    "105|Data inválida|xx|||9904|||1,00|2,00",
)
# 4.3.13 (alíquota zero): 9902 e também 9901 (para provocar conflito de CST com a 4.3.10)
T13 = _arq(CAB_13, "201|Hortaliça fictícia|01012009||9902;9901|||", "202|Ovo fictício|01012009||99025000|||")


@pytest.fixture
def service() -> FiscalService:
    conn = connect_memory()
    importar(conn, {"4.3.10": T10, "4.3.13": T13})
    return FiscalService(conn)


def test_parse_ignora_linhas_sem_ncm_data_invalida_e_le_excecoes() -> None:
    versao, regras = parse_tabela(T10)
    assert versao == "37"
    assert {(r.codigo, r.prefixo) for r in regras} == {("101", "9901"), ("102", "99012000"), ("103", "9903")}
    r101 = next(r for r in regras if r.codigo == "101")
    assert r101.excecoes == "99011100" and not r101.restrito and r101.inicio == "2018-01-01" and r101.fim is None
    assert next(r for r in regras if r.codigo == "102").restrito  # EX da TIPI: não decide pelo NCM
    assert next(r for r in regras if r.codigo == "103").fim == "2015-12-31"


def test_cabecalho_invalido_ou_tabela_vazia_aborta_sem_tocar_base() -> None:
    with pytest.raises(ImportacaoPisCofinsError):
        parse_tabela(b"lixo")
    conn = connect_memory()
    with pytest.raises(ImportacaoPisCofinsError):
        importar(conn, {"4.3.10": T10, "4.3.13": _arq(CAB_13, "201|Só operação|01012009||||")})
    assert conn.execute("SELECT COUNT(*) FROM pis_cofins_ncm").fetchone()[0] == 0  # nada importado (tudo ou nada)
    with pytest.raises(ImportacaoPisCofinsError):
        importar(conn, {"9.9.9": T10})


def test_importar_e_idempotente(service: FiscalService) -> None:
    conn = service._conn
    antes = conn.execute("SELECT COUNT(*) FROM pis_cofins_ncm").fetchone()[0]
    importar(conn, {"4.3.10": T10, "4.3.13": T13})
    assert conn.execute("SELECT COUNT(*) FROM pis_cofins_ncm").fetchone()[0] == antes
    assert conn.execute("SELECT versao FROM pis_cofins_versao WHERE tabela = '4.3.13'").fetchone()[0] == "53"


def test_sugestao_unica_usa_cst_da_tabela(service: FiscalService) -> None:
    r = service.sugerir_pis_cofins("9902.40.00", "2026-11-23")
    assert (r["status"], r["cst"], r["fontes"][0]["tabela"]) == ("SUGESTAO", "06", "4.3.13")
    assert r["versoes"]["4.3.13"] == "53"


def test_ncm_excluido_da_regra_nao_recebe_sugestao_dessa_regra(service: FiscalService) -> None:
    # 99011100 está excluído da 101 (4.3.10); a 201 (4.3.13, prefixo 9901) segue valendo → só CST 06
    r = service.sugerir_pis_cofins("99011100", "2026-11-23")
    assert (r["status"], r["cst"]) == ("SUGESTAO", "06")


def test_csts_conflitantes_pedem_revisao(service: FiscalService) -> None:
    r = service.sugerir_pis_cofins("99011300", "2026-11-23")  # 101 (04) e 201 (06) cobrem o NCM
    assert r["status"] == "REVISAR" and r["cst"] is None
    assert {f["cst"] for f in r["fontes"]} == {"04", "06"}


def test_regra_restrita_a_ex_da_tipi_nunca_vira_sugestao(service: FiscalService) -> None:
    solo = connect_memory()
    importar(solo, {"4.3.10": _arq(CAB_10, "102|Peça fictícia EX|01012018||99012000||01|1,00|2,00")})
    assert FiscalService(solo).sugerir_pis_cofins("99012000", "2026-11-23")["status"] == "REVISAR"


def test_linha_restrita_com_mesmo_cst_da_geral_nao_impede_sugestao() -> None:
    conn = connect_memory()
    importar(conn, {"4.3.10": _arq(
        CAB_10,
        "101|Geral|01012018||9905|||1,00|2,00",
        "102|Só EX|01012018||9905||01|1,00|2,00")})
    assert FiscalService(conn).sugerir_pis_cofins("99051000", "2026-11-23")["cst"] == "04"
    conn2 = connect_memory()
    importar(conn2, {"4.3.10": _arq(CAB_10, "101|Geral|01012018||9905|||1,00|2,00"),
                     "4.3.13": _arq(CAB_13, "201|Só EX|01012018||9905||01")})
    assert FiscalService(conn2).sugerir_pis_cofins("99051000", "2026-11-23")["status"] == "REVISAR"  # CSTs divergem


def test_vigencia_e_ncm_fora_das_tabelas(service: FiscalService) -> None:
    assert service.sugerir_pis_cofins("99030000", "2026-11-23")["status"] == "SEM_REGRA"  # regra encerrada em 2015
    assert service.sugerir_pis_cofins("99030000", "2014-06-01")["status"] == "SUGESTAO"
    assert service.sugerir_pis_cofins("99999999", "2026-11-23")["status"] == "SEM_REGRA"


def test_sem_base_nao_quebra() -> None:
    assert FiscalService(connect_memory()).sugerir_pis_cofins("99011000")["status"] == "SEM_BASE"
    legado = sqlite3.connect(":memory:")  # fiscal.db antigo, sem as tabelas
    legado.row_factory = sqlite3.Row
    assert FiscalService(legado).sugerir_pis_cofins("99011000")["status"] == "SEM_BASE"


# ----------------------------------------------------------------------------- integração com build_regras

def _df(ncm: str, **extra: str) -> pd.DataFrame:
    base = {"NOME PRODUTO": "ITEM", "NCM": ncm, "CEST": "", "TRIBUTO": "T", "IMPOSTO (% ICMS)": "18",
            "CFOP": "5102", "CST OU CSOSN": "00"}
    return pd.DataFrame([{**base, **extra}])


def test_regime_normal_sem_cst_pis_recebe_sugestao_registrada(service: FiscalService) -> None:
    r = build_regras(_df("99024000"), "3", service=service, reference_date="2026-11-23")
    assert r.regras[0].pis == ("06", None, None) and r.regras[0].cofins == ("06", None, None)
    c = next(c for c in r.correcoes if c["campo"] == "CST PIS/COFINS")
    assert c["para"] == "06" and "4.3.13" in c["motivo"] and "versão 53" in c["motivo"]


def test_nao_sobrescreve_cst_informado_pelo_cliente(service: FiscalService) -> None:
    r = build_regras(_df("99024000", **{"CST PIS": "01", "ALIQUOTA PIS": "1,65", "CST COFINS": "01",
                                        "ALIQUOTA COFINS": "7,6"}),
                     "3", service=service, reference_date="2026-11-23")
    assert r.regras[0].pis[0] == "01"
    assert not any(c["campo"] == "CST PIS/COFINS" for c in r.correcoes)


def test_simples_e_sugestoes_recusadas_nao_alteram_pis(service: FiscalService) -> None:
    simples = build_regras(_df("99024000", **{"CST OU CSOSN": "102"}), "1", service=service,
                           reference_date="2026-11-23")
    assert simples.regras[0].pis is None
    recusado = build_regras(_df("99024000"), "3", service=service, reference_date="2026-11-23",
                            aplicar_sugestoes=False)
    assert recusado.regras[0].pis is None and not recusado.errors
