# -*- coding: utf-8 -*-
"""Testes da base fiscal versionada (NCM / CEST / NCM×CEST).

ATENÇÃO: todos os códigos abaixo são FICTÍCIOS (capítulo 99 / segmento 99), criados apenas para
exercitar a lógica. Não representam classificação fiscal real.
"""
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fiscal import build_regras  # noqa: E402
from fiscal_codes import format_cest, normalize_cest, normalize_ncm  # noqa: E402
from fiscal_db import connect_memory  # noqa: E402
from fiscal_import import ImportacaoError, import_cest, import_ncm, parse_cest_html  # noqa: E402
from fiscal_service import FiscalService, StatusFiscal  # noqa: E402


# ----------------------------------------------------------------------------- fixtures fictícias

def _ncm_json(itens: list[tuple[str, str, str]], ato: str = "TESTE 1/2026") -> bytes:
    nomenclaturas = [{"Codigo": "99.01", "Descricao": "Posição fictícia de teste",
                      "Data_Inicio": "01/01/2020", "Data_Fim": "31/12/9999"}]
    nomenclaturas += [
        {"Codigo": c, "Descricao": d, "Data_Inicio": ini, "Data_Fim": "31/12/9999"} for c, d, ini in itens
    ]
    return json.dumps({"Data_Ultima_Atualizacao_NCM": "teste", "Ato": ato,
                       "Nomenclaturas": nomenclaturas}).encode("utf-8")


NCM_V1 = _ncm_json([
    ("9901.10.00", "Item fictício A", "01/01/2020"),
    ("9901.20.00", "Item fictício B", "01/01/2020"),
    ("9901.30.00", "Item fictício C", "01/01/2020"),
    ("0999.10.00", "Item fictício com zero à esquerda", "01/01/2020"),
])


def _row(item: str, cest: str, ncm: str, desc: str, verde: bool = False) -> str:
    cls = "A9-2Tabelajustificadoverde" if verde else "A7-2Tabelajustificado"
    return (f'<tr><td><p class="{cls}">{item}</p></td><td><p class="{cls}">{cest}</p></td>'
            f'<td colspan="2"><p class="{cls}">{ncm}</p></td><td><p class="{cls}">{desc}</p></td></tr>')


def _rem(texto: str, anterior: bool = False) -> str:
    cls = "A8-2RemissaoAnt" if anterior else "A8-1Remissao"
    return f'<tr><td colspan="5"><p class="{cls}">{texto}</p></td></tr>'


CEST_V1 = ("<table>" + "".join([
    _row("1.0", "99.001.00", "9901.10.00", "Mercadoria fictícia 1"),
    _row("2.0", "99.002.00", "9901.20", "Mercadoria fictícia 2"),
    _row("3.0", "99.003.00", "9901.20.00", "Mercadoria fictícia 3"),
    _rem("Nova redação dada ao item 4 pelo Conv. TESTE, efeitos a partir de 01.06.24."),
    _row("4.0", "99.004.00", "9901.30.00", "Mercadoria fictícia 4 - nova redação"),
    _rem("Redação original, efeitos até 31.05.24.", anterior=True),
    _row("4.0", "99.004.00", "9901.30.00", "Mercadoria fictícia 4 - redação original", verde=True),
    _row("5.0", "99.005.00", "Capítulo 98", "Mercadoria fictícia 5 (outro capítulo)"),
]) + "</table>").encode("utf-8")


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = connect_memory()
    import_ncm(c, NCM_V1, data_importacao=date(2026, 1, 1))
    import_cest(c, CEST_V1, data_importacao=date(2026, 1, 1))
    return c


@pytest.fixture()
def svc(conn: sqlite3.Connection) -> FiscalService:
    return FiscalService(conn)


# ----------------------------------------------------------------------------- normalização

@pytest.mark.parametrize("raw,expected", [
    ("9901.10.00", "99011000"), ("99011000", "99011000"), (" 9901.10.00 ", "99011000"),
    ("9991000.0", "09991000"), ("9991000", "09991000"), ("123", ""), ("", ""),
])
def test_normalize_ncm(raw: str, expected: str) -> None:
    assert normalize_ncm(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("99.001.00", "9900100"), ("9900100", "9900100"), ("100100", "0100100"), ("01.001.00", "0100100"), ("12", ""),
])
def test_normalize_cest(raw: str, expected: str) -> None:
    assert normalize_cest(raw) == expected
    assert not expected or format_cest(expected).count(".") == 2


# ----------------------------------------------------------------------------- NCM

def test_ncm_valido_com_e_sem_pontuacao(svc: FiscalService) -> None:
    for entrada in ("9901.10.00", "99011000"):
        res = svc.validate_ncm(entrada)
        assert res["valid"] is True
        assert res["ncm"] == "99011000"
        assert "Item fictício A" in res["description"]
        assert "Posição fictícia" in res["description"]  # hierarquia


def test_ncm_invalido(svc: FiscalService) -> None:
    assert svc.validate_ncm("9901.99.99")["valid"] is False
    v = svc.validate_ncm_cest("9901.99.99")
    assert v.status is StatusFiscal.INVALID_NCM
    assert "não encontrado" in v.messages[0]
    assert svc.validate_ncm_cest("12.34").status is StatusFiscal.INVALID_NCM


# ----------------------------------------------------------------------------- CEST

def test_cest_valido_e_invalido(svc: FiscalService) -> None:
    assert svc.validate_cest("99.001.00")["valid"] is True
    assert svc.validate_cest("9900100")["valid"] is True
    assert svc.validate_cest("99.999.99")["valid"] is False


def test_ncm_cest_compativeis(svc: FiscalService) -> None:
    v = svc.validate_ncm_cest("9901.10.00", "99.001.00")
    assert v.status is StatusFiscal.VALID
    assert v.ncm_valid and v.cest_valid


def test_ncm_cest_incompativeis(svc: FiscalService) -> None:
    v = svc.validate_ncm_cest("9901.10.00", "99.003.00")
    assert v.status is StatusFiscal.NCM_CEST_MISMATCH
    assert v.cest_valid is True
    assert "não foi encontrada relação" in v.messages[-1]


def test_cest_inexistente_com_ncm_valido(svc: FiscalService) -> None:
    v = svc.validate_ncm_cest("9901.10.00", "99.999.99")
    assert v.status is StatusFiscal.INVALID_CEST


def test_ncm_com_multiplos_cests_nao_determina(svc: FiscalService) -> None:
    # 9901.20.00 casa com 99.002.00 (prefixo 9901.20) e 99.003.00 (NCM completo)
    cests = {c.code for c in svc.get_cests_by_ncm("99012000")}
    assert cests == {"9900200", "9900300"}
    v = svc.validate_ncm_cest("99012000", "")
    assert v.status is StatusFiscal.MULTIPLE_CEST
    assert v.cest == ""
    assert "Não determinado automaticamente" in v.messages[-1]


def test_ncm_com_um_cest_relacionado_exige_revisao(svc: FiscalService) -> None:
    v = svc.validate_ncm_cest("99011000", "")
    assert v.status is StatusFiscal.NEEDS_REVIEW
    assert v.cest == ""  # nunca aplica automaticamente


def test_ncm_sem_cest(svc: FiscalService) -> None:
    v = svc.validate_ncm_cest("09991000", None)
    assert v.status is StatusFiscal.NO_CEST
    assert "Não foram encontrados CESTs relacionados" in v.messages[-1]


# ----------------------------------------------------------------------------- vigência / versionamento

def test_consulta_por_data_de_vigencia(svc: FiscalService) -> None:
    antes = svc.get_cest("9900400", "2024-05-31")
    depois = svc.get_cest("9900400", "2024-06-01")
    assert antes is not None and "redação original" in antes.description
    assert depois is not None and "nova redação" in depois.description


def test_nova_versao_ncm_preserva_historico(conn: sqlite3.Connection, svc: FiscalService) -> None:
    v2 = _ncm_json([
        ("9901.10.00", "Item fictício A - descrição revisada", "01/01/2020"),  # alterado
        ("9901.20.00", "Item fictício B", "01/01/2020"),                       # inalterado
        ("0999.10.00", "Item fictício com zero à esquerda", "01/01/2020"),
        ("9901.40.00", "Item fictício D (novo)", "01/07/2026"),                # novo
    ], ato="TESTE 2/2026")                                                     # 9901.30.00 removido
    rel = import_ncm(conn, v2, data_importacao=date(2026, 7, 1))

    assert (rel.novos, rel.alterados, rel.inativados, rel.inalterados) == (1, 1, 1, 2)
    assert "revisada" in svc.get_ncm("99011000", "2026-07-02").description
    # removido: válido antes da nova versão, inválido depois
    assert svc.get_ncm("99013000", "2026-06-30") is not None
    assert svc.get_ncm("99013000", "2026-07-01") is None
    # histórico preservado (nada excluído)
    total = conn.execute("SELECT COUNT(*) FROM ncm WHERE codigo = '99011000'").fetchone()[0]
    assert total == 2
    assert conn.execute("SELECT COUNT(*) FROM ncm").fetchone()[0] == 6
    # novo só vale a partir do início de vigência oficial
    assert svc.get_ncm("99014000", "2026-06-30") is None
    assert svc.get_ncm("99014000", "2026-07-01") is not None


def test_codigo_removido_nao_ressurge_apos_nova_versao(conn: sqlite3.Connection, svc: FiscalService) -> None:
    v2 = _ncm_json([("9901.10.00", "Item fictício A - v2", "01/01/2020")], ato="TESTE 2/2026")
    v3 = _ncm_json([("9901.20.00", "Item fictício B", "01/01/2020")], ato="TESTE 3/2026")
    import_ncm(conn, v2, data_importacao=date(2026, 3, 1))
    import_ncm(conn, v3, data_importacao=date(2026, 4, 1))
    assert svc.get_ncm("99011000", "2026-03-15").description.endswith("Item fictício A - v2")
    assert svc.get_ncm("99011000", "2026-05-01") is None


def test_importacao_duplicada_e_ignorada(conn: sqlite3.Connection) -> None:
    antes = conn.execute("SELECT COUNT(*) FROM ncm").fetchone()[0]
    rel = import_ncm(conn, NCM_V1, data_importacao=date(2026, 2, 1))
    assert rel.duplicada is True
    assert conn.execute("SELECT COUNT(*) FROM ncm").fetchone()[0] == antes
    assert import_cest(conn, CEST_V1, data_importacao=date(2026, 2, 1)).duplicada is True


def test_nova_versao_cest_preserva_historico(conn: sqlite3.Connection, svc: FiscalService) -> None:
    v2 = ("<table>" + "".join([
        _row("1.0", "99.001.00", "9901.10.00 9901.30.00", "Mercadoria fictícia 1"),  # NCM adicionado
        _row("2.0", "99.002.00", "9901.20", "Mercadoria fictícia 2"),
        _rem("Nova redação dada ao item 4 pelo Conv. TESTE, efeitos a partir de 01.06.24."),
        _row("4.0", "99.004.00", "9901.30.00", "Mercadoria fictícia 4 - nova redação"),
        _rem("Redação original, efeitos até 31.05.24.", anterior=True),
        _row("4.0", "99.004.00", "9901.30.00", "Mercadoria fictícia 4 - redação original", verde=True),
        _row("5.0", "99.005.00", "Capítulo 98", "Mercadoria fictícia 5 (outro capítulo)"),
    ]) + "</table>").encode("utf-8")  # 99.003.00 removido
    rel = import_cest(conn, v2, data_importacao=date(2026, 8, 1))

    assert (rel.alterados, rel.inativados) == (1, 1)
    assert {c.code for c in svc.get_cests_by_ncm("99013000", "2026-08-02")} == {"9900100", "9900400"}
    assert svc.get_cest("9900300", "2026-07-31") is not None
    assert svc.get_cest("9900300", "2026-08-01") is None
    assert conn.execute("SELECT COUNT(*) FROM cest WHERE codigo = '9900300'").fetchone()[0] == 1


# ----------------------------------------------------------------------------- parser CONFAZ

def test_parser_cest_vigencia_prefixos_e_capitulos() -> None:
    records, errors, duplicados = parse_cest_html(CEST_V1)
    assert not errors and duplicados == 0
    por_chave = {(r.codigo, r.inicio, r.fim): r for r in records}
    assert por_chave[("9900400", "2024-06-01", None)].descricao.endswith("nova redação")
    assert por_chave[("9900400", None, "2024-05-31")].descricao.endswith("redação original")
    assert por_chave[("9900200", None, None)].ncm_prefixos == ("990120",)
    assert por_chave[("9900500", None, None)].ncm_prefixos == ("98",)


def test_parser_cest_ignora_sem_efeitos_e_revogado() -> None:
    html = ("<table>" + "".join([
        _rem("Acrescido o item 1.1 pelo Conv. TESTE, sem efeitos.", anterior=True),
        _row("1.1", "99.001.01", "9901.10.00", "Nunca vigente", verde=True),
        _rem("Revogado o item 2 pelo Conv. TESTE, efeitos a partir de 01.01.25."),
        _row("2.0", "99.002.00", "9901.20.00", "REVOGADO"),
        _rem("Redação original, efeitos até 31.12.24.", anterior=True),
        _row("2.0", "99.002.00", "9901.20.00", "Item revogado", verde=True),
    ]) + "</table>").encode("utf-8")
    records, _, _ = parse_cest_html(html)
    assert [(r.codigo, r.fim) for r in records] == [("9900200", "2024-12-31")]


def test_parser_cest_sem_tabela() -> None:
    with pytest.raises(ImportacaoError):
        parse_cest_html(b"<html>sem dados</html>")


# ----------------------------------------------------------------------------- integração com a conversão

def _planilha(rows: list[dict[str, str]]) -> pd.DataFrame:
    base = {"NOME PRODUTO": "Produto", "NCM": "", "CEST": "", "TRIBUTO": "T", "IMPOSTO (% ICMS)": "0",
            "CFOP": "5102", "CST OU CSOSN": "102"}
    return pd.DataFrame([{**base, **r} for r in rows])


def test_conversao_rejeita_ncm_invalido_e_sinaliza_multiplos(svc: FiscalService) -> None:
    df = _planilha([
        {"NOME PRODUTO": "Válido", "NCM": "9901.10.00", "CEST": "99.001.00"},
        {"NOME PRODUTO": "NCM inexistente", "NCM": "9901.99.99"},
        {"NOME PRODUTO": "Múltiplos", "NCM": "99012000"},
        {"NOME PRODUTO": "Divergente", "NCM": "99011000", "CEST": "9900300"},
    ])
    result = build_regras(df, "1", service=svc, usuario="teste", arquivo="teste.xlsx", reference_date="2026-01-02")

    status = {a["produto"]: a["status"] for a in result.analise}
    assert status == {"Válido": "VALID", "NCM inexistente": "INVALID_NCM",
                      "Múltiplos": "MULTIPLE_CEST", "Divergente": "NCM_CEST_MISMATCH"}
    assert result.produtos_validos == 3
    assert [e.field for e in result.errors] == ["NCM"]
    # CEST nunca é preenchido automaticamente
    assert {r.cest for r in result.regras if r.ncm == "99012000"} == {""}


# ----------------------------------------------------------------------------- catálogo TOTVS

def test_catalogo_totvs_barra_cest_ausente(tmp_path: Path) -> None:
    import openpyxl
    from totvs_catalog import import_catalog

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "NCM"
    ws.append(["Id", "Codigo", "Descricao", "Inativo"])
    ws.append([1, 9901100, '"Item"', 0])  # zero à esquerda perdido pelo Excel
    ws = wb.create_sheet("CEST")
    ws.append(["Id", "Cod_CEST", "Descricao", "Inativo"])
    ws.append([1, 9900100, "Cest", 0])
    ws.append([2, 9900200, "Cest inativo", 1])
    path = tmp_path / "totvs.xlsx"
    wb.save(path)

    conn = connect_memory()
    assert import_catalog(conn, path) == {"ncm": 1, "cest": 2}
    svc = FiscalService(conn)
    assert svc.has_totvs_catalog()
    assert svc.in_totvs_catalog("NCM", "00990110") is False  # zfill(8) de 9901100 = 09901100
    assert svc.in_totvs_catalog("NCM", "09901100")
    assert svc.in_totvs_catalog("CEST", "9900100")
    assert not svc.in_totvs_catalog("CEST", "9900200")  # inativo
    assert not svc.in_totvs_catalog("CEST", "9900300")  # ausente


class _FakeCest:
    def __init__(self, description: str) -> None:
        self.description = description


class _FakeService:
    """Catálogo fictício: código → (descrição, cadastrado no TOTVS)."""

    def __init__(self, cests: dict[str, tuple[str, bool]], ncm_ok: bool = True) -> None:
        self._c, self._ncm_ok = cests, ncm_ok

    def in_totvs_catalog(self, tipo: str, code: str) -> bool:
        return self._ncm_ok if tipo == "NCM" else self._c.get(code, ("", False))[1]

    def totvs_cest_candidates(self, ncm: str, ref: str | None) -> list[str]:
        return [c for c, (_, ok) in self._c.items() if ok]

    def get_cests_by_ncm(self, ncm: str, ref: str | None) -> list:
        return [type("C", (), {"code": c})() for c in self._c]

    def get_cest(self, code: str, ref: str | None = None) -> _FakeCest | None:
        return _FakeCest(self._c[code][0]) if code in self._c else None


def _regra(cest: str = "9900900", tributo: str = "T", cst: str = "102") -> "RegraFiscal":
    from fiscal import RegraFiscal
    return RegraFiscal("99011000", cest, tributo, 0.0, "5102", cst, "", None, None, None)


def test_cest_ausente_no_totvs_corrige_so_com_escolha_inequivoca() -> None:
    from fiscal import _aplicar_catalogo_totvs

    unico = _FakeService({"9900100": ("Cerveja", True)})
    nova, erros, corr = _aplicar_catalogo_totvs(unico, 3, "CERVEJA X", _regra(), None)
    assert (nova.cest, erros, corr["para"]) == ("9900100", [], "9900100")

    por_nome = _FakeService({"9900100": ("Cerveja de malte", True), "9900200": ("Refrigerante", True)})
    nova, erros, corr = _aplicar_catalogo_totvs(por_nome, 3, "CERVEJA LATA 350ML", _regra(), None)
    assert nova.cest == "9900100" and "aderente" in corr["motivo"]

    empate = _FakeService({"9900100": ("Bebida alfa", True), "9900200": ("Bebida beta", True)})
    nova, erros, corr = _aplicar_catalogo_totvs(empate, 3, "BEBIDA", _regra(), None)
    assert corr is None and nova.cest == "9900900" and "9900100" in erros[0].reason

    _, erros, _ = _aplicar_catalogo_totvs(_FakeService({}, ncm_ok=False), 3, "X", _regra(), None)
    assert erros[0].field == "NCM"


def test_embalagem_nao_conta_na_escolha_por_descricao() -> None:
    from fiscal import _aplicar_catalogo_totvs

    svc = _FakeService({"9900100": ("Cerveja de malte", True), "9900200": ("Energetico em lata", True)})
    nova, _, _ = _aplicar_catalogo_totvs(svc, 3, "CERVEJA LATA", _regra(), None)
    assert nova.cest == "9900100"  # "lata" casaria com o energético se contasse


def test_obrigatoriedade_cest_so_com_icms_st() -> None:
    from fiscal import _aplicar_obrigatoriedade

    svc = _FakeService({"9900100": ("Cerveja", True)})
    # sem ST (CSOSN 102): CEST fica em branco
    nova, erros, corr = _aplicar_obrigatoriedade(svc, 1, "CERVEJA", _regra("", "T", "102"), "1", None, True)
    assert (nova.cest, erros, corr) == ("", [], None)
    # ST (CSOSN 500): preenche pelo candidato único
    nova, erros, corr = _aplicar_obrigatoriedade(svc, 1, "CERVEJA", _regra("", "T", "500"), "1", None, True)
    assert nova.cest == "9900100" and corr["de"] == ""
    # Regime normal CST 060 (3 dígitos: origem+CST) também é ST
    nova, _, _ = _aplicar_obrigatoriedade(svc, 1, "CERVEJA", _regra("", "T", "060"), "3", None, True)
    assert nova.cest == "9900100"
    # NCM fora do Convênio: nada a preencher mesmo com ST
    vazio = _FakeService({})
    nova, erros, _ = _aplicar_obrigatoriedade(vazio, 1, "X", _regra("", "S", "500"), "1", None, True)
    assert (nova.cest, erros) == ("", [])
    # vários candidatos sem destaque: rejeita (Rejeição 806) listando opções
    amb = _FakeService({"9900100": ("Alfa", True), "9900200": ("Beta", True)})
    _, erros, _ = _aplicar_obrigatoriedade(amb, 1, "COISA", _regra("", "S", "500"), "1", None, True)
    assert "806" in erros[0].reason and "9900200" in erros[0].reason


def test_sugestao_recusada_rejeita_linha_em_vez_de_aplicar() -> None:
    svc = _FakeService({"9900100": ("Cerveja", True)})
    svc.has_totvs_catalog = lambda: True  # type: ignore[attr-defined]
    svc.validate_ncm_cest = lambda *a, **k: type(  # type: ignore[attr-defined]
        "V", (), {"status": StatusFiscal.VALID, "ncm": "", "cest": "", "messages": [],
                  "to_dict": lambda self: {}})()
    svc.audit = lambda *a, **k: None  # type: ignore[attr-defined]
    df = pd.DataFrame([{"NOME PRODUTO": "CERVEJA", "NCM": "99011000", "CEST": "9900900", "TRIBUTO": "S",
                        "IMPOSTO (% ICMS)": "0", "CFOP": "5405", "CST OU CSOSN": "500"}])

    aceita = build_regras(df, "1", service=svc, aplicar_sugestoes=True)
    assert aceita.produtos_validos == 1 and aceita.regras[0].cest == "9900100" and len(aceita.correcoes) == 1

    recusada = build_regras(df, "1", service=svc, aplicar_sugestoes=False)
    assert recusada.produtos_validos == 0 and not recusada.correcoes
    assert "Sugestão não aplicada: 9900100" in recusada.errors[0].reason
