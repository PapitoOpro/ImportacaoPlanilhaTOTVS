# -*- coding: utf-8 -*-
"""Persistência da Base de Conhecimento (Postgres/Supabase em produção, SQLite local/testes)."""
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (Column, DateTime, Integer, MetaData, String, Table, Text, create_engine, delete,
                        func, insert, or_, select, text, update)
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

_LOCAL_DB = Path(__file__).resolve().parent.parent / "data" / "kb.db"

metadata = MetaData()

artigos = Table(
    "kb_artigos",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("titulo", String(200), nullable=False),
    Column("categoria", String(60), nullable=False, server_default="Geral", index=True),
    Column("conteudo", Text, nullable=False),
    Column("origem", String(10), nullable=False, server_default="manual"),
    Column("arquivo_origem", String(255)),
    Column("autor", String(100)),
    Column("criado_em", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("atualizado_em", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

ORIGENS = {"manual", "pdf"}


@dataclass(frozen=True)
class ArtigoInput:
    titulo: str
    categoria: str
    conteudo: str
    origem: str = "manual"
    arquivo_origem: str | None = None
    autor: str | None = None


def _log(event: str, **kwargs: Any) -> None:
    logger.info(json.dumps({"event": event, **kwargs}, ensure_ascii=False, default=str))


def database_url() -> str:
    """KB_DATABASE_URL / DATABASE_URL (Supabase) ou SQLite local."""
    url = os.environ.get("KB_DATABASE_URL") or os.environ.get("DATABASE_URL") or ""
    if not url:
        _LOCAL_DB.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{_LOCAL_DB.as_posix()}"
    # Supabase fornece postgres:// ou postgresql:// → driver psycopg 3
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def create_kb_engine(url: str | None = None) -> Engine:
    url = url or database_url()
    engine = create_engine(url, pool_pre_ping=True, future=True)
    metadata.create_all(engine)
    if engine.dialect.name == "postgresql":
        # Sem policies: bloqueia acesso via API REST pública do Supabase (anon key).
        # A conexão direta do site (dono da tabela) continua com acesso.
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE kb_artigos ENABLE ROW LEVEL SECURITY"))
    _log("kb_engine_pronto", dialeto=engine.dialect.name, persistente=engine.dialect.name != "sqlite")
    return engine


def _serialize(row: Any) -> dict[str, Any]:
    data = dict(row._mapping)
    for key in ("criado_em", "atualizado_em"):
        if isinstance(data.get(key), datetime):
            data[key] = data[key].isoformat()
    return data


class KnowledgeBase:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @property
    def banco(self) -> str:
        """'postgresql' (Supabase) ou 'sqlite' (local — não persiste em deploy com disco efêmero)."""
        return self._engine.dialect.name

    def listar(self, busca: str = "", categoria: str = "") -> list[dict[str, Any]]:
        stmt = select(
            artigos.c.id, artigos.c.titulo, artigos.c.categoria, artigos.c.origem,
            artigos.c.atualizado_em, func.substr(artigos.c.conteudo, 1, 240).label("trecho"),
        ).order_by(artigos.c.categoria, artigos.c.titulo)
        if busca:
            termo = f"%{busca.strip()}%"
            stmt = stmt.where(or_(artigos.c.titulo.ilike(termo), artigos.c.conteudo.ilike(termo)))
        if categoria:
            stmt = stmt.where(artigos.c.categoria == categoria)
        with self._engine.connect() as conn:
            return [_serialize(r) for r in conn.execute(stmt)]

    def categorias(self) -> list[str]:
        with self._engine.connect() as conn:
            return [r[0] for r in conn.execute(select(artigos.c.categoria).distinct().order_by(artigos.c.categoria))]

    def obter(self, artigo_id: int) -> dict[str, Any] | None:
        with self._engine.connect() as conn:
            row = conn.execute(select(artigos).where(artigos.c.id == artigo_id)).first()
        return _serialize(row) if row else None

    def criar(self, dados: ArtigoInput) -> int:
        with self._engine.begin() as conn:
            result = conn.execute(insert(artigos).values(**dados.__dict__).returning(artigos.c.id))
            artigo_id = int(result.scalar_one())
        _log("kb_artigo_criado", id=artigo_id, titulo=dados.titulo, origem=dados.origem, autor=dados.autor)
        return artigo_id

    def atualizar(self, artigo_id: int, dados: ArtigoInput) -> bool:
        valores = {k: v for k, v in dados.__dict__.items() if k not in ("origem", "arquivo_origem")}
        with self._engine.begin() as conn:
            result = conn.execute(
                update(artigos).where(artigos.c.id == artigo_id).values(**valores, atualizado_em=func.now())
            )
        if result.rowcount:
            _log("kb_artigo_atualizado", id=artigo_id, autor=dados.autor)
        return bool(result.rowcount)

    def excluir(self, artigo_id: int, autor: str | None = None) -> bool:
        with self._engine.begin() as conn:
            result = conn.execute(delete(artigos).where(artigos.c.id == artigo_id))
        if result.rowcount:
            _log("kb_artigo_excluido", id=artigo_id, autor=autor)
        return bool(result.rowcount)
