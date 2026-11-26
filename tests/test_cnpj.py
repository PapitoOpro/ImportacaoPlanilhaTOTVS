# -*- coding: utf-8 -*-
"""Testes da consulta CNPJ / IE (validação, normalização das fontes, reserva, cache e API)."""
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cnpj_api import build_router  # noqa: E402
from cnpj_service import (CnpjInvalidoError, CnpjNaoEncontradoError, CnpjService,  # noqa: E402
                          FonteError, LimiteConsultasError, FontesIndisponiveisError, normalizar_cnpj)

CNPJ = "00000000000191"


def _cnpjws(situacao: str = "Ativa", ies: list[dict] | None = None, simples: dict | None = None) -> dict[str, Any]:
    if ies is None:
        ies = [{"inscricao_estadual": "0809427800174", "ativo": True, "estado": {"sigla": "DF"}}]
    return {
        "razao_social": "EMPRESA TESTE LTDA",
        "capital_social": "1000.00",
        "atualizado_em": "2026-11-14T03:00:00.000Z",
        "porte": {"descricao": "Demais"},
        "natureza_juridica": {"descricao": "Sociedade Limitada"},
        "simples": simples,
        "estabelecimento": {
            "cnpj": CNPJ, "nome_fantasia": "TESTE", "tipo": "Matriz",
            "situacao_cadastral": situacao, "data_situacao_cadastral": "2005-11-03",
            "data_inicio_atividade": "1966-08-01",
            "tipo_logradouro": "RUA", "logradouro": "A", "numero": "10", "complemento": None,
            "bairro": "CENTRO", "cep": "70040912", "cidade": {"nome": "Brasília"}, "estado": {"sigla": "DF"},
            "ddd1": "61", "telefone1": "34939002", "email": "a@b.com",
            "atividade_principal": {"subclasse": "6422-1/00", "descricao": "Bancos"},
            "atividades_secundarias": [{"subclasse": "6499-9/99", "descricao": "Outras"}],
            "inscricoes_estaduais": ies,
        },
    }


def _cnpja() -> dict[str, Any]:
    return {
        "updated": "2026-11-25T21:22:27.821Z", "taxId": CNPJ, "alias": "Teste", "founded": "1966-08-01",
        "head": True, "statusDate": "2005-11-03", "status": {"text": "Ativa"},
        "company": {"name": "EMPRESA TESTE LTDA", "equity": 1000, "nature": {"text": "Sociedade Limitada"},
                    "size": {"text": "Demais"}, "simples": {"optant": True}, "simei": {"optant": False}},
        "address": {"street": "Rua A", "number": "10", "district": "Centro", "city": "Brasília",
                    "state": "DF", "zip": "70040912"},
        "phones": [{"area": "61", "number": "34939002"}], "emails": [{"address": "a@b.com"}],
        "mainActivity": {"id": 6422100, "text": "Bancos"}, "sideActivities": [],
    }


class Fake:
    def __init__(self, resposta: Any) -> None:
        self.resposta, self.chamadas = resposta, 0

    def __call__(self, cnpj: str) -> dict[str, Any]:
        self.chamadas += 1
        if isinstance(self.resposta, Exception):
            raise self.resposta
        return self.resposta


def _service(primaria: Any, reserva: Any = None, **kw: Any) -> tuple[CnpjService, Fake, Fake]:
    p, r = Fake(primaria), Fake(reserva if reserva is not None else FonteError(503))
    return CnpjService(p, r, **kw), p, r


@pytest.mark.parametrize("entrada", ["00.000.000/0001-91", CNPJ, " 00000000000191 "])
def test_normaliza_cnpj_valido(entrada: str) -> None:
    assert normalizar_cnpj(entrada) == CNPJ


def test_aceita_cnpj_alfanumerico() -> None:
    assert normalizar_cnpj("12.abc.345/01de-35") == "12ABC34501DE35"


@pytest.mark.parametrize("entrada", ["", "123", "00000000000192", "11111111111111", "../etc/passwd", "0000000000019A"])
def test_rejeita_cnpj_invalido(entrada: str) -> None:
    with pytest.raises(CnpjInvalidoError):
        normalizar_cnpj(entrada)


def test_normaliza_resposta_da_fonte_principal() -> None:
    service, _, _ = _service(_cnpjws(simples={"simples": "Sim", "mei": "Não"}))
    r = service.consultar(CNPJ)
    assert r["razao_social"] == "EMPRESA TESTE LTDA"
    assert r["inscricoes_estaduais"] == [{"numero": "0809427800174", "uf": "DF", "ativa": True}]
    assert r["regime_tributario"] == "Simples Nacional"
    assert r["cnae_principal"] == {"codigo": "6422-1/00", "descricao": "Bancos"}
    assert r["endereco"]["logradouro"] == "RUA A"
    assert r["endereco"]["municipio"] == "Brasília"
    assert r["telefones"] == ["(61) 34939002"]
    assert r["capital_social"] == 1000.0
    assert r["fonte"] == "CNPJ.ws"


@pytest.mark.parametrize("simples,esperado", [
    (None, "Não informado"),
    ({"simples": "Não", "mei": "Não"}, "Não optante pelo Simples"),
    ({"simples": "Sim", "mei": "Sim"}, "MEI (Simples Nacional)"),
])
def test_regime_tributario(simples: dict | None, esperado: str) -> None:
    service, _, _ = _service(_cnpjws(simples=simples))
    assert service.consultar(CNPJ)["regime_tributario"] == esperado


@pytest.mark.parametrize("situacao,ies,cor", [
    ("Ativa", None, "verde"),
    ("Ativa", [], "azul"),
    ("Ativa", [{"inscricao_estadual": "1", "ativo": False, "estado": {"sigla": "DF"}}], "vermelho"),
    ("Baixada", None, "cinza"),
])
def test_status_por_cor(situacao: str, ies: list[dict] | None, cor: str) -> None:
    service, _, _ = _service(_cnpjws(situacao, ies))
    assert service.consultar(CNPJ)["status"]["cor"] == cor


def test_ie_da_uf_da_empresa_vem_primeiro_e_define_o_status() -> None:
    ies = [{"inscricao_estadual": "1", "ativo": True, "estado": {"sigla": "SP"}},
           {"inscricao_estadual": "2", "ativo": False, "estado": {"sigla": "DF"}}]
    service, _, _ = _service(_cnpjws(ies=ies))
    r = service.consultar(CNPJ)
    assert [i["uf"] for i in r["inscricoes_estaduais"]] == ["DF", "SP"]
    assert r["status"]["cor"] == "vermelho"


@pytest.mark.parametrize("erro", [FonteError(429), FonteError(503), FonteError(404)])
def test_usa_reserva_quando_principal_falha(erro: FonteError) -> None:
    service, _, _ = _service(erro, _cnpja())
    r = service.consultar(CNPJ)
    assert r["fonte"] == "CNPJá"
    assert r["inscricoes_estaduais"] is None
    assert r["status"]["cor"] == "laranja"
    assert r["regime_tributario"] == "Simples Nacional"


def test_resposta_inesperada_da_principal_cai_na_reserva() -> None:
    service, _, _ = _service({"detalhe": "formato novo"}, _cnpja())
    assert service.consultar(CNPJ)["fonte"] == "CNPJá"


@pytest.mark.parametrize("primaria,reserva,excecao", [
    (404, 404, CnpjNaoEncontradoError),
    (404, 503, CnpjNaoEncontradoError),
    (429, 503, LimiteConsultasError),
    (503, 429, LimiteConsultasError),
    (503, 503, FontesIndisponiveisError),
])
def test_erros_quando_as_duas_fontes_falham(primaria: int, reserva: int, excecao: type[Exception]) -> None:
    service, _, _ = _service(FonteError(primaria), FonteError(reserva))
    with pytest.raises(excecao):
        service.consultar(CNPJ)


def test_cache_expira_apos_o_prazo() -> None:
    agora = [0.0]
    service, p, _ = _service(_cnpjws(), ttl=100, relogio=lambda: agora[0])
    service.consultar(CNPJ)
    service.consultar("00.000.000/0001-91")
    assert p.chamadas == 1
    agora[0] = 101
    service.consultar(CNPJ)
    assert p.chamadas == 2


def test_resultado_da_reserva_nao_fica_em_cache() -> None:
    service, p, _ = _service(FonteError(429), _cnpja())
    service.consultar(CNPJ)
    service.consultar(CNPJ)
    assert p.chamadas == 2


def test_cache_tem_tamanho_limitado() -> None:
    service, p, _ = _service(_cnpjws(), max_cache=1)
    for cnpj in (CNPJ, "12ABC34501DE35", CNPJ):
        service.consultar(cnpj)
    assert p.chamadas == 3


def _client(service: CnpjService) -> TestClient:
    app = FastAPI()
    app.include_router(build_router(service))
    return TestClient(app)


def test_api_retorna_consulta() -> None:
    service, _, _ = _service(_cnpjws())
    res = _client(service).get("/api/cnpj", params={"cnpj": "00.000.000/0001-91"})
    assert res.status_code == 200
    assert res.json()["cnpj"] == CNPJ


@pytest.mark.parametrize("cnpj,erro,codigo", [
    ("123", 503, 400), (CNPJ, 404, 404), (CNPJ, 429, 429), (CNPJ, 503, 503),
])
def test_api_mapeia_erros(cnpj: str, erro: int, codigo: int) -> None:
    service, _, _ = _service(FonteError(erro), FonteError(erro))
    res = _client(service).get("/api/cnpj", params={"cnpj": cnpj})
    assert res.status_code == codigo
    assert res.json()["detail"]
