# -*- coding: utf-8 -*-
"""Consulta CNPJ / Inscrição Estadual: CNPJ.ws pública (principal) e CNPJá aberta (reserva, sem IE)."""
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

URL_CNPJWS = "https://publica.cnpj.ws/cnpj/{cnpj}"
URL_CNPJA = "https://open.cnpja.com/office/{cnpj}"
TTL_CACHE = 48 * 60 * 60
MAX_CACHE = 500
_TIMEOUT = 15

Resposta = dict[str, Any]
Fonte = Callable[[str], Resposta]


class FonteError(Exception):
    """Falha ao consultar uma fonte externa; `status` é o código HTTP equivalente."""

    def __init__(self, status: int) -> None:
        super().__init__(f"fonte respondeu {status}")
        self.status = status


class CnpjInvalidoError(ValueError):
    pass


class CnpjNaoEncontradoError(Exception):
    pass


class LimiteConsultasError(Exception):
    pass


class FontesIndisponiveisError(Exception):
    pass


def _digito(base: str) -> str:
    pesos = ([2, 3, 4, 5, 6, 7, 8, 9] * 2)[:len(base)]
    resto = sum((ord(c) - 48) * p for c, p in zip(reversed(base), pesos)) % 11
    return "0" if resto < 2 else str(11 - resto)


def normalizar_cnpj(valor: str) -> str:
    """Remove a máscara e valida os dígitos verificadores (numérico ou alfanumérico)."""
    cnpj = re.sub(r"[.\-/\s]", "", valor or "").upper()
    if not re.fullmatch(r"[0-9A-Z]{12}\d{2}", cnpj) or len(set(cnpj)) == 1:
        raise CnpjInvalidoError("CNPJ inválido")
    base = cnpj[:12]
    base += _digito(base)
    if base + _digito(base) != cnpj:
        raise CnpjInvalidoError("CNPJ inválido: dígito verificador não confere")
    return cnpj


def _http_json(url: str) -> Resposta:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "consulta-cnpj/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as res:  # noqa: S310 - URLs fixas https
            return json.load(res)
    except urllib.error.HTTPError as exc:
        raise FonteError(exc.code) from exc
    except (OSError, ValueError) as exc:  # rede, timeout ou JSON inválido
        raise FonteError(503) from exc


def fetch_cnpjws(cnpj: str) -> Resposta:
    return _http_json(URL_CNPJWS.format(cnpj=cnpj))


def fetch_cnpja(cnpj: str) -> Resposta:
    return _http_json(URL_CNPJA.format(cnpj=cnpj))


def _regime(simples: bool | None, mei: bool | None) -> str:
    if mei:
        return "MEI (Simples Nacional)"
    if simples:
        return "Simples Nacional"
    return "Não informado" if simples is None else "Não optante pelo Simples"


def _status(situacao: str, uf: str, ies: list[Resposta] | None) -> Resposta:
    if situacao.lower() != "ativa":
        return {"cor": "cinza", "texto": f"CNPJ {situacao or 'não ativo'} na Receita Federal"}
    if ies is None:
        return {"cor": "laranja", "texto": "CNPJ ativo — Inscrição Estadual não verificada no momento"}
    if not ies:
        return {"cor": "azul", "texto": "CNPJ ativo — sem Inscrição Estadual"}
    da_uf = [i for i in ies if i["uf"] == uf] or ies
    if any(i["ativa"] for i in da_uf):
        return {"cor": "verde", "texto": "CNPJ e Inscrição Estadual ativos"}
    return {"cor": "vermelho", "texto": "CNPJ ativo — Inscrição Estadual não ativa"}


def _telefones(pares: list[tuple[str | None, str | None]]) -> list[str]:
    return [f"({ddd}) {numero}" for ddd, numero in pares if ddd and numero]


def _de_cnpjws(d: Resposta) -> Resposta:
    e = d["estabelecimento"]
    uf = (e.get("estado") or {}).get("sigla") or ""
    ies = sorted(
        ({"numero": i["inscricao_estadual"], "uf": (i.get("estado") or {}).get("sigla") or "",
          "ativa": bool(i.get("ativo"))} for i in e.get("inscricoes_estaduais") or []),
        key=lambda i: (i["uf"] != uf, not i["ativa"], i["uf"]),
    )
    simples = d.get("simples")
    cnae = lambda a: {"codigo": a.get("subclasse") or "", "descricao": a.get("descricao") or ""}  # noqa: E731
    return {
        "cnpj": e["cnpj"],
        "razao_social": d["razao_social"],
        "nome_fantasia": e.get("nome_fantasia"),
        "tipo": e.get("tipo"),
        "situacao_cadastral": e.get("situacao_cadastral") or "",
        "data_situacao": e.get("data_situacao_cadastral"),
        "data_abertura": e.get("data_inicio_atividade"),
        "natureza_juridica": (d.get("natureza_juridica") or {}).get("descricao"),
        "porte": (d.get("porte") or {}).get("descricao"),
        "capital_social": float(d["capital_social"]) if d.get("capital_social") else None,
        "regime_tributario": _regime(
            None if simples is None else simples.get("simples") == "Sim",
            None if simples is None else simples.get("mei") == "Sim",
        ),
        "inscricoes_estaduais": ies,
        "cnae_principal": cnae(e.get("atividade_principal") or {}),
        "cnaes_secundarios": [cnae(a) for a in e.get("atividades_secundarias") or []],
        "endereco": {
            "logradouro": " ".join(filter(None, (e.get("tipo_logradouro"), e.get("logradouro")))),
            "numero": e.get("numero"), "complemento": e.get("complemento"), "bairro": e.get("bairro"),
            "municipio": (e.get("cidade") or {}).get("nome"), "uf": uf, "cep": e.get("cep"),
        },
        "telefones": _telefones([(e.get("ddd1"), e.get("telefone1")), (e.get("ddd2"), e.get("telefone2"))]),
        "email": e.get("email"),
        "atualizado_em": d.get("atualizado_em"),
        "fonte": "CNPJ.ws",
    }


def _de_cnpja(d: Resposta) -> Resposta:
    company, address = d["company"], d.get("address") or {}
    simples, simei = company.get("simples"), company.get("simei")

    def cnae(a: Resposta) -> Resposta:
        codigo = str(a.get("id") or "").zfill(7)
        return {"codigo": f"{codigo[:4]}-{codigo[4]}/{codigo[5:]}", "descricao": a.get("text") or ""}

    return {
        "cnpj": d["taxId"],
        "razao_social": company["name"],
        "nome_fantasia": d.get("alias"),
        "tipo": "Matriz" if d.get("head") else "Filial",
        "situacao_cadastral": (d.get("status") or {}).get("text") or "",
        "data_situacao": d.get("statusDate"),
        "data_abertura": d.get("founded"),
        "natureza_juridica": (company.get("nature") or {}).get("text"),
        "porte": (company.get("size") or {}).get("text"),
        "capital_social": company.get("equity"),
        "regime_tributario": _regime(
            None if simples is None else bool(simples.get("optant")),
            None if simei is None else bool(simei.get("optant")),
        ),
        "inscricoes_estaduais": None,
        "cnae_principal": cnae(d.get("mainActivity") or {}),
        "cnaes_secundarios": [cnae(a) for a in d.get("sideActivities") or []],
        "endereco": {
            "logradouro": address.get("street"), "numero": address.get("number"),
            "complemento": address.get("details"), "bairro": address.get("district"),
            "municipio": address.get("city"), "uf": address.get("state") or "", "cep": address.get("zip"),
        },
        "telefones": _telefones([(p.get("area"), p.get("number")) for p in d.get("phones") or []]),
        "email": next((m.get("address") for m in d.get("emails") or []), None),
        "atualizado_em": d.get("updated"),
        "fonte": "CNPJá",
    }


class CnpjService:
    def __init__(self, primaria: Fonte = fetch_cnpjws, reserva: Fonte = fetch_cnpja, *,
                 ttl: float = TTL_CACHE, max_cache: int = MAX_CACHE,
                 relogio: Callable[[], float] = time.monotonic) -> None:
        self._fontes = ((primaria, _de_cnpjws), (reserva, _de_cnpja))
        self._ttl, self._max, self._relogio = ttl, max_cache, relogio
        self._cache: dict[str, tuple[float, Resposta]] = {}
        self._lock = threading.Lock()

    def consultar(self, valor: str) -> Resposta:
        cnpj = normalizar_cnpj(valor)
        with self._lock:
            item = self._cache.get(cnpj)
        if item and item[0] > self._relogio():
            return item[1]

        resultado = self._buscar(cnpj)
        # Resultado da reserva vem sem IE: não cacheia, para tentar a principal de novo.
        if resultado["inscricoes_estaduais"] is not None:
            with self._lock:
                self._cache.pop(cnpj, None)
                while len(self._cache) >= self._max:
                    del self._cache[next(iter(self._cache))]
                self._cache[cnpj] = (self._relogio() + self._ttl, resultado)
        return resultado

    def _buscar(self, cnpj: str) -> Resposta:
        falhas: list[int] = []
        for fetch, normalizar in self._fontes:
            try:
                resultado = normalizar(fetch(cnpj))
                resultado["status"] = _status(resultado["situacao_cadastral"], resultado["endereco"]["uf"],
                                              resultado["inscricoes_estaduais"])
                return resultado
            except FonteError as exc:
                falhas.append(exc.status)
            except (KeyError, TypeError, ValueError, AttributeError):  # formato inesperado
                falhas.append(502)
            logger.warning(json.dumps({"event": "cnpj_fonte_falhou", "fonte": normalizar.__name__[4:],
                                       "status": falhas[-1]}))
        if falhas[0] == 404:
            raise CnpjNaoEncontradoError("CNPJ não encontrado na base consultada")
        if 429 in falhas:
            raise LimiteConsultasError("Limite de consultas atingido. Aguarde um minuto e tente novamente.")
        raise FontesIndisponiveisError("Fontes de consulta indisponíveis no momento")
