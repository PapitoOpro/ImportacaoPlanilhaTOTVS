# -*- coding: utf-8 -*-
"""Conferência IBS/CBS por NCM. Códigos FICTÍCIOS (capítulo 99); não representam classificação real."""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fiscal_db import connect_memory  # noqa: E402
from fiscal_service import FiscalService  # noqa: E402
from ibs_cbs_import import ImportacaoIbsCbsError, importar  # noqa: E402


def _receita_fake(path: Path) -> Path:
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE SITUACAO_TRIBUTARIA (SITR_ID INTEGER, SITR_CD TEXT);
        CREATE TABLE CLASSIFICACAO_TRIBUTARIA (CLTR_ID INTEGER, CLTR_CD TEXT, CLTR_DESCRICAO TEXT, CLTR_SITR_ID INTEGER);
        CREATE TABLE NCM_APLICAVEL (NCMA_ID INTEGER, NCMA_NCM_CD TEXT, NCMA_CLTR_ID INTEGER,
                                    NCMA_INICIO_VIGENCIA TEXT, NCMA_FIM_VIGENCIA TEXT);
        CREATE TABLE EXCECAO_NCM_APLICAVEL (ENCM_ID INTEGER, ENCM_NCM_CD TEXT, ENCM_NCMA_ID INTEGER,
                                            ENCM_INICIO_VIGENCIA TEXT, ENCM_FIM_VIGENCIA TEXT);
        CREATE TABLE VERSAO_BASE_DADO (VRBD_ID INTEGER, VRBD_DATA TEXT, VRBD_VERSAO_BASE_DADO TEXT);
        INSERT INTO SITUACAO_TRIBUTARIA VALUES (1, '200');
        INSERT INTO CLASSIFICACAO_TRIBUTARIA VALUES
            (1, '200901', 'Classe fictícia A', 1), (2, '200902', 'Classe fictícia B', 1),
            (3, '200901', 'Classe fictícia A (2027)', 1);
        -- A: prefixo de posição '9901' (2026), vínculo duplicado por exceção (modelagem do anexo da Receita)
        -- B: NCM completo 99012000 só a partir de 2027
        INSERT INTO NCM_APLICAVEL VALUES (1, '9901', 1, '2026-01-01', NULL), (2, '9901', 1, '2026-01-01', NULL),
                                         (3, '99012000', 2, '2027-01-01', NULL);
        INSERT INTO EXCECAO_NCM_APLICAVEL VALUES (1, '99011100', 1, '2026-01-01', NULL),
                                                 (2, '99011100', 2, '2026-01-01', NULL);
        INSERT INTO VERSAO_BASE_DADO VALUES (2, '2026-11-09 00:00:00', 'V0002'), (1, '2025-02-04 00:00:00', 'V0001'), (3, '2026-11-23 11:38:25', 'V0003');
    """)
    c.commit()
    c.close()
    return path


@pytest.fixture
def service(tmp_path: Path) -> FiscalService:
    conn = connect_memory()
    importar(_receita_fake(tmp_path / "receita.db"), conn)
    return FiscalService(conn)


def test_importar_copia_tabelas_e_registra_versao(tmp_path: Path) -> None:
    conn = connect_memory()
    resumo = importar(_receita_fake(tmp_path / "r.db"), conn)
    assert resumo == {"versao": "V0003 (2026-11-23)", "classificacoes": 3, "vinculos": 3, "excecoes": 2}
    assert importar(tmp_path / "r.db", conn) == resumo  # idempotente: substitui, não duplica
    assert conn.execute("SELECT COUNT(*) FROM ncm_ibs_cbs").fetchone()[0] == 3


def test_prefixo_cobre_ncm_completo_e_vira_revisao(service: FiscalService) -> None:
    r = service.classificar_ibs_cbs("9901.13.00", "2026-11-23")
    assert r["status"] == "REVISAR"
    assert r["versao"] == "V0003 (2026-11-23)"
    assert [(c["cclasstrib"], c["vigencia"]) for c in r["candidatas"]] == [("200901", "vigente")]


def test_ncm_excepcionado_nao_e_candidato(service: FiscalService) -> None:
    # 99011100 está na exceção de todos os vínculos genéricos vigentes → sem classificação específica
    assert service.classificar_ibs_cbs("99011100", "2026-11-23")["status"] == "PADRAO"


def test_vinculo_futuro_aparece_com_data_de_inicio(service: FiscalService) -> None:
    antes = service.classificar_ibs_cbs("99012000", "2026-11-23")
    assert antes["status"] == "REVISAR"
    assert {c["cclasstrib"]: c["vigencia"] for c in antes["candidatas"]}["200902"] == "a partir de 2027-01-01"
    depois = service.classificar_ibs_cbs("99012000", "2027-06-01")
    assert {c["cclasstrib"]: c["vigencia"] for c in depois["candidatas"]}["200902"] == "vigente"


def test_ncm_sem_vinculo_e_padrao(service: FiscalService) -> None:
    assert service.classificar_ibs_cbs("99029000", "2026-11-23")["status"] == "PADRAO"


def test_sem_base_importada_nao_quebra() -> None:
    assert FiscalService(connect_memory()).classificar_ibs_cbs("99011000")["status"] == "SEM_BASE"
    legado = sqlite3.connect(":memory:")  # fiscal.db antigo, sem as tabelas novas
    legado.row_factory = sqlite3.Row
    assert FiscalService(legado).classificar_ibs_cbs("99011000")["status"] == "SEM_BASE"


def test_origem_invalida_aborta_sem_tocar_destino(tmp_path: Path) -> None:
    conn = connect_memory()
    with pytest.raises(ImportacaoIbsCbsError):
        importar(tmp_path / "nao_existe.db", conn)
    sqlite3.connect(tmp_path / "vazio.db").close()
    with pytest.raises(ImportacaoIbsCbsError):
        importar(tmp_path / "vazio.db", conn)
    assert conn.execute("SELECT COUNT(*) FROM ibs_cbs_versao").fetchone()[0] == 0
