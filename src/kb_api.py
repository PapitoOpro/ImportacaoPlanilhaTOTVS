# -*- coding: utf-8 -*-
"""Rotas da Base de Conhecimento. Leitura pública; escrita exige KB_ADMIN_PASSWORD (header X-Admin-Password)."""
import hmac
import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator

from kb_pdf import PdfConversaoError, pdf_para_artigo
from kb_repository import ArtigoInput, KnowledgeBase

logger = logging.getLogger(__name__)

MAX_PDF_BYTES = 15 * 1024 * 1024


class ArtigoPayload(BaseModel):
    titulo: str = Field(min_length=1, max_length=200)
    categoria: str = Field(default="Geral", max_length=60)
    conteudo: str = Field(min_length=1, max_length=500_000)
    autor: str | None = Field(default=None, max_length=100)
    origem: str = Field(default="manual", pattern="^(manual|pdf)$")
    arquivo_origem: str | None = Field(default=None, max_length=255)

    @field_validator("titulo", "categoria", "conteudo")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("titulo", "conteudo")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v:
            raise ValueError("não pode ficar em branco")
        return v

    def to_input(self) -> ArtigoInput:
        return ArtigoInput(
            titulo=self.titulo,
            categoria=self.categoria or "Geral",
            conteudo=self.conteudo,
            origem=self.origem,
            arquivo_origem=self.arquivo_origem,
            autor=(self.autor or "").strip() or None,
        )


def _log(level: int, event: str, **kwargs: Any) -> None:
    logger.log(level, json.dumps({"event": event, **kwargs}, ensure_ascii=False, default=str))


def require_admin(x_admin_password: str = Header(default="")) -> None:
    esperado = os.environ.get("KB_ADMIN_PASSWORD", "")
    if not esperado:
        raise HTTPException(status_code=503, detail="Edição desabilitada: defina KB_ADMIN_PASSWORD no servidor.")
    if not hmac.compare_digest(x_admin_password.encode("utf-8"), esperado.encode("utf-8")):
        _log(logging.WARNING, "kb_acesso_negado")
        raise HTTPException(status_code=401, detail="Senha de administrador inválida.")


def build_router(get_kb: Any) -> APIRouter:
    """`get_kb` é uma dependência FastAPI que retorna KnowledgeBase (permite trocar o banco nos testes)."""
    router = APIRouter(prefix="/api/kb", tags=["Base de Conhecimento"])

    @router.get("/status")
    def status(kb: KnowledgeBase = Depends(get_kb)) -> dict[str, Any]:
        return {"banco": kb.banco, "edicao_habilitada": bool(os.environ.get("KB_ADMIN_PASSWORD"))}

    @router.post("/auth", dependencies=[Depends(require_admin)])
    def auth() -> dict[str, bool]:
        return {"ok": True}

    @router.get("/artigos")
    def listar(q: str = "", categoria: str = "", kb: KnowledgeBase = Depends(get_kb)) -> dict[str, Any]:
        return {"artigos": kb.listar(q[:100], categoria[:60]), "categorias": kb.categorias()}

    @router.get("/artigos/{artigo_id}")
    def obter(artigo_id: int, kb: KnowledgeBase = Depends(get_kb)) -> dict[str, Any]:
        artigo = kb.obter(artigo_id)
        if not artigo:
            raise HTTPException(status_code=404, detail="Artigo não encontrado.")
        return artigo

    @router.post("/artigos", status_code=201, dependencies=[Depends(require_admin)])
    def criar(payload: ArtigoPayload, kb: KnowledgeBase = Depends(get_kb)) -> dict[str, Any]:
        return {"id": kb.criar(payload.to_input())}

    @router.put("/artigos/{artigo_id}", dependencies=[Depends(require_admin)])
    def atualizar(artigo_id: int, payload: ArtigoPayload, kb: KnowledgeBase = Depends(get_kb)) -> dict[str, Any]:
        if not kb.atualizar(artigo_id, payload.to_input()):
            raise HTTPException(status_code=404, detail="Artigo não encontrado.")
        return {"id": artigo_id}

    @router.delete("/artigos/{artigo_id}", dependencies=[Depends(require_admin)])
    def excluir(artigo_id: int, autor: str = "", kb: KnowledgeBase = Depends(get_kb)) -> dict[str, bool]:
        if not kb.excluir(artigo_id, autor[:100] or None):
            raise HTTPException(status_code=404, detail="Artigo não encontrado.")
        return {"ok": True}

    @router.post("/pdf", dependencies=[Depends(require_admin)])
    async def importar_pdf(file: UploadFile = File(...)) -> dict[str, Any]:
        """Gera um RASCUNHO (não salva). O usuário revisa no editor e então publica."""
        nome = Path(file.filename or "").name
        if not nome.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Envie um arquivo .pdf")
        raw = await file.read(MAX_PDF_BYTES + 1)
        if len(raw) > MAX_PDF_BYTES:
            raise HTTPException(status_code=413, detail="PDF maior que 15 MB.")
        if not raw.startswith(b"%PDF"):
            raise HTTPException(status_code=400, detail="O arquivo não é um PDF válido.")
        try:
            rascunho = pdf_para_artigo(raw, nome)
        except PdfConversaoError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        _log(logging.INFO, "kb_pdf_convertido", arquivo=nome, paginas=rascunho.paginas, caracteres=len(rascunho.conteudo))
        return {"titulo": rascunho.titulo, "conteudo": rascunho.conteudo, "paginas": rascunho.paginas,
                "arquivo_origem": nome[:255]}

    return router
