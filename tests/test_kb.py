# -*- coding: utf-8 -*-
"""Testes da Base de Conhecimento (repositório, PDF → artigo, API com senha de admin)."""
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kb_api import build_router  # noqa: E402
from kb_pdf import PdfConversaoError, pdf_para_artigo  # noqa: E402
from kb_repository import ArtigoInput, KnowledgeBase, metadata  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402

SENHA = "senha-teste-123"


def _make_pdf(pages: list[list[str]], title: str | None = None) -> bytes:
    """Gera um PDF mínimo com texto (Helvetica/WinAnsi) — apenas para testes."""
    def esc(t: str) -> str:
        return t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    n = len(pages)
    font_id, first_page = 3, 4
    objs: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: ("<< /Type /Pages /Kids [" + " ".join(f"{first_page + 2 * i} 0 R" for i in range(n))
            + f"] /Count {n} >>").encode(),
        font_id: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    }
    for i, lines in enumerate(pages):
        page_id, content_id = first_page + 2 * i, first_page + 2 * i + 1
        body = "BT /F1 11 Tf 14 TL 50 800 Td " + " ".join(f"({esc(l)}) Tj T*" for l in lines) + " ET"
        stream = body.encode("cp1252")  # WinAnsiEncoding
        objs[page_id] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                         f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>").encode()
        objs[content_id] = f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
    info_id = None
    if title:
        info_id = max(objs) + 1
        objs[info_id] = f"<< /Title ({esc(title)}) >>".encode("cp1252")

    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for oid in sorted(objs):
        offsets[oid] = len(out)
        out += f"{oid} 0 obj\n".encode() + objs[oid] + b"\nendobj\n"
    xref = len(out)
    size = max(objs) + 1
    out += f"xref\n0 {size}\n0000000000 65535 f \n".encode()
    for oid in range(1, size):
        out += f"{offsets[oid]:010d} 00000 n \n".encode()
    trailer = f"<< /Size {size} /Root 1 0 R" + (f" /Info {info_id} 0 R" if info_id else "") + " >>"
    out += f"trailer\n{trailer}\nstartxref\n{xref}\n%%EOF".encode()
    return bytes(out)


@pytest.fixture()
def kb() -> KnowledgeBase:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    metadata.create_all(engine)
    return KnowledgeBase(engine)


@pytest.fixture()
def client(kb: KnowledgeBase, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("KB_ADMIN_PASSWORD", SENHA)
    app = FastAPI()
    app.include_router(build_router(lambda: kb))
    return TestClient(app)


ADMIN = {"X-Admin-Password": SENHA}


# ----------------------------------------------------------------------------- repositório

def test_crud_e_busca(kb: KnowledgeBase) -> None:
    a = kb.criar(ArtigoInput("Como importar produtos", "Conversões", "Use a planilha **do cliente**."))
    b = kb.criar(ArtigoInput("Regras fiscais", "Fiscal", "NCM e CEST oficiais."))
    assert [x["titulo"] for x in kb.listar(busca="cest")] == ["Regras fiscais"]
    assert [x["id"] for x in kb.listar(categoria="Conversões")] == [a]
    assert kb.categorias() == ["Conversões", "Fiscal"]

    assert kb.atualizar(b, ArtigoInput("Regras fiscais v2", "Fiscal", "Atualizado"))
    assert kb.obter(b)["titulo"] == "Regras fiscais v2"
    assert kb.excluir(a) and kb.obter(a) is None
    assert not kb.excluir(999)


# ----------------------------------------------------------------------------- PDF

def test_pdf_para_artigo_estrutura() -> None:
    pdf = _make_pdf([[
        "MANUAL DE IMPORTACAO",
        "1. Preparar a planilha",
        "O arquivo deve conter as colunas de produto e o",
        "preco de venda preenchido.",
        "- Codigo do produto",
        "- Nome do produto",
        "Pagina 1 de 1",
    ]])
    r = pdf_para_artigo(pdf, "manual_importacao.pdf")
    assert r.titulo == "MANUAL DE IMPORTACAO"
    assert r.paginas == 1
    assert "## 1. Preparar a planilha" in r.conteudo
    assert "O arquivo deve conter as colunas de produto e o preco de venda preenchido." in r.conteudo
    assert "- Codigo do produto\n- Nome do produto" in r.conteudo  # lista contínua
    assert "Pagina 1 de 1" not in r.conteudo


def test_pdf_usa_titulo_dos_metadados_e_remove_rodape_repetido() -> None:
    pages = [[f"Texto da pagina {i} com conteudo.", "Empresa X - Documento interno"] for i in range(1, 5)]
    r = pdf_para_artigo(_make_pdf(pages, title="Guia Interno"), "x.pdf")
    assert r.titulo == "Guia Interno"
    assert "Empresa X" not in r.conteudo
    assert "Texto da pagina 4 com conteudo." in r.conteudo


def test_pdf_sem_texto_e_invalido() -> None:
    with pytest.raises(PdfConversaoError, match="texto"):
        pdf_para_artigo(_make_pdf([[]]), "scan.pdf")
    with pytest.raises(PdfConversaoError):
        pdf_para_artigo(b"%PDF-1.4 lixo", "x.pdf")


# ----------------------------------------------------------------------------- API

def test_leitura_publica_escrita_exige_senha(client: TestClient) -> None:
    payload = {"titulo": "Artigo", "categoria": "Geral", "conteudo": "Texto"}
    assert client.post("/api/kb/artigos", json=payload).status_code == 401
    assert client.post("/api/kb/artigos", json=payload, headers={"X-Admin-Password": "errada"}).status_code == 401

    r = client.post("/api/kb/artigos", json=payload, headers=ADMIN)
    assert r.status_code == 201
    art_id = r.json()["id"]

    assert client.get("/api/kb/artigos").json()["artigos"][0]["titulo"] == "Artigo"
    assert client.get(f"/api/kb/artigos/{art_id}").json()["conteudo"] == "Texto"
    assert client.put(f"/api/kb/artigos/{art_id}", json={**payload, "titulo": "Novo"}).status_code == 401
    assert client.put(f"/api/kb/artigos/{art_id}", json={**payload, "titulo": "Novo"}, headers=ADMIN).status_code == 200
    assert client.delete(f"/api/kb/artigos/{art_id}").status_code == 401
    assert client.delete(f"/api/kb/artigos/{art_id}", headers=ADMIN).status_code == 200
    assert client.get(f"/api/kb/artigos/{art_id}").status_code == 404


def test_validacao_payload(client: TestClient) -> None:
    r = client.post("/api/kb/artigos", json={"titulo": "  ", "conteudo": "x"}, headers=ADMIN)
    assert r.status_code == 422
    r = client.post("/api/kb/artigos", json={"titulo": "x" * 201, "conteudo": "x"}, headers=ADMIN)
    assert r.status_code == 422


def test_edicao_desabilitada_sem_senha_configurada(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KB_ADMIN_PASSWORD")
    r = client.post("/api/kb/artigos", json={"titulo": "a", "conteudo": "b"}, headers=ADMIN)
    assert r.status_code == 503
    assert client.get("/api/kb/status").json()["edicao_habilitada"] is False


def test_upload_pdf_gera_rascunho_sem_salvar(client: TestClient) -> None:
    pdf = _make_pdf([["GUIA RAPIDO", "Primeiro passo do processo."]])
    files = {"file": ("guia.pdf", pdf, "application/pdf")}
    assert client.post("/api/kb/pdf", files=files).status_code == 401

    r = client.post("/api/kb/pdf", files=files, headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["titulo"] == "GUIA RAPIDO"
    assert "Primeiro passo do processo." in body["conteudo"]
    assert client.get("/api/kb/artigos").json()["artigos"] == []  # rascunho não é salvo

    r = client.post("/api/kb/pdf", files={"file": ("x.pdf", b"nao e pdf", "application/pdf")}, headers=ADMIN)
    assert r.status_code == 400
    r = client.post("/api/kb/pdf", files={"file": ("x.txt", pdf, "text/plain")}, headers=ADMIN)
    assert r.status_code == 400
