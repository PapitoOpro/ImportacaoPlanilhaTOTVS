# -*- coding: utf-8 -*-
"""Rota da consulta CNPJ / Inscrição Estadual."""
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from cnpj_service import (CnpjInvalidoError, CnpjNaoEncontradoError, CnpjService, FontesIndisponiveisError,
                          LimiteConsultasError)

_STATUS_HTTP: dict[type[Exception], int] = {
    CnpjInvalidoError: 400,
    CnpjNaoEncontradoError: 404,
    LimiteConsultasError: 429,
    FontesIndisponiveisError: 503,
}


def build_router(service: CnpjService) -> APIRouter:
    router = APIRouter()

    # Query string porque o CNPJ digitado tem "/"; rota síncrona roda no threadpool (chamada HTTP bloqueante).
    @router.get("/api/cnpj")
    def consultar_cnpj(cnpj: str = Query(max_length=30)) -> dict[str, Any]:
        try:
            return service.consultar(cnpj)
        except tuple(_STATUS_HTTP) as exc:
            raise HTTPException(status_code=_STATUS_HTTP[type(exc)], detail=str(exc)) from exc

    return router
