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

categorias = Table(
    "kb_categorias",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("nome", String(60), nullable=False, unique=True),
    Column("criado_em", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

ORIGENS = {"manual", "pdf"}
CATEGORIA_PADRAO = "Geral"


class CategoriaDuplicadaError(ValueError):
    """Já existe categoria com o mesmo nome (ignorando maiúsculas/minúsculas)."""


class CategoriaEmUsoError(ValueError):
    """Categoria com artigos só pode ser excluída informando uma categoria de destino."""


class CategoriaDestinoError(ValueError):
    """Categoria de destino inexistente ou igual à de origem."""


def chave_categoria(nome: str) -> str:
    """Chave de comparação: "GERAL", "Geral" e " geral " são a mesma categoria."""
    return " ".join(nome.split()).casefold()


def _limpar_nome(nome: str) -> str:
    return " ".join(nome.split())[:60]


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
            conn.execute(text("ALTER TABLE kb_categorias ENABLE ROW LEVEL SECURITY"))
    sincronizar_categorias(engine)
    _log("kb_engine_pronto", dialeto=engine.dialect.name, persistente=engine.dialect.name != "sqlite")
    return engine


def sincronizar_categorias(engine: Engine) -> None:
    """Cadastra as categorias já usadas nos artigos e unifica grafias ("GERAL" → "Geral"). Idempotente."""
    with engine.begin() as conn:
        cadastradas = {chave_categoria(n): n for (n,) in conn.execute(select(categorias.c.nome))}
        usadas = conn.execute(
            select(artigos.c.categoria, func.count()).group_by(artigos.c.categoria)
        ).all()
        # grafia oficial de cada chave: a já cadastrada; senão, a mais usada nos artigos
        for nome, _ in sorted(usadas, key=lambda r: (-r[1], r[0])):
            chave = chave_categoria(nome)
            if chave and chave not in cadastradas:
                cadastradas[chave] = _limpar_nome(nome)
                conn.execute(insert(categorias).values(nome=cadastradas[chave]))
                _log("kb_categoria_cadastrada", nome=cadastradas[chave], origem="sincronizacao")
        for nome, total in usadas:
            oficial = cadastradas.get(chave_categoria(nome))
            if oficial and oficial != nome:
                conn.execute(update(artigos).where(artigos.c.categoria == nome).values(categoria=oficial))
                _log("kb_categoria_unificada", de=nome, para=oficial, artigos=total)


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
        return [c["nome"] for c in self.listar_categorias()]

    # ------------------------------------------------------------------ categorias

    def listar_categorias(self) -> list[dict[str, Any]]:
        """Categorias cadastradas com a quantidade de artigos, em ordem alfabética."""
        with self._engine.connect() as conn:
            totais = dict(conn.execute(select(artigos.c.categoria, func.count()).group_by(artigos.c.categoria)).all())
            linhas = conn.execute(select(categorias.c.id, categorias.c.nome)).all()
        itens = [{"id": i, "nome": n, "total": int(totais.get(n, 0))} for i, n in linhas]
        return sorted(itens, key=lambda c: chave_categoria(c["nome"]))

    @staticmethod
    def _por_chave(conn: Any, nome: str) -> tuple[int, str] | None:
        chave = chave_categoria(nome)
        for cat_id, existente in conn.execute(select(categorias.c.id, categorias.c.nome)):
            if chave_categoria(existente) == chave:
                return int(cat_id), existente
        return None

    def _resolver_categoria(self, conn: Any, nome: str) -> str:
        """Grafia oficial da categoria; cadastra se ainda não existir."""
        nome = _limpar_nome(nome) or CATEGORIA_PADRAO
        existente = self._por_chave(conn, nome)
        if existente:
            return existente[1]
        conn.execute(insert(categorias).values(nome=nome))
        _log("kb_categoria_cadastrada", nome=nome, origem="artigo")
        return nome

    def criar_categoria(self, nome: str) -> int:
        nome = _limpar_nome(nome)
        if not nome:
            raise ValueError("Nome da categoria em branco.")
        with self._engine.begin() as conn:
            existente = self._por_chave(conn, nome)
            if existente:
                raise CategoriaDuplicadaError(f'Já existe a categoria "{existente[1]}".')
            cat_id = int(conn.execute(insert(categorias).values(nome=nome).returning(categorias.c.id)).scalar_one())
        _log("kb_categoria_cadastrada", id=cat_id, nome=nome, origem="cadastro")
        return cat_id

    def renomear_categoria(self, categoria_id: int, novo_nome: str) -> bool:
        """Renomeia a categoria e leva junto os artigos dela. False se não existir."""
        novo_nome = _limpar_nome(novo_nome)
        if not novo_nome:
            raise ValueError("Nome da categoria em branco.")
        with self._engine.begin() as conn:
            atual = conn.execute(select(categorias.c.nome).where(categorias.c.id == categoria_id)).scalar()
            if atual is None:
                return False
            outra = self._por_chave(conn, novo_nome)
            if outra and outra[0] != categoria_id:
                raise CategoriaDuplicadaError(f'Já existe a categoria "{outra[1]}".')
            conn.execute(update(categorias).where(categorias.c.id == categoria_id).values(nome=novo_nome))
            movidos = conn.execute(
                update(artigos).where(artigos.c.categoria == atual).values(categoria=novo_nome)
            ).rowcount
        _log("kb_categoria_renomeada", id=categoria_id, de=atual, para=novo_nome, artigos=movidos)
        return True

    def excluir_categoria(self, categoria_id: int, destino_id: int | None = None) -> bool:
        """Exclui a categoria; se tiver artigos, eles vão para `destino_id`. False se não existir."""
        with self._engine.begin() as conn:
            nome = conn.execute(select(categorias.c.nome).where(categorias.c.id == categoria_id)).scalar()
            if nome is None:
                return False
            total = conn.execute(
                select(func.count()).select_from(artigos).where(artigos.c.categoria == nome)
            ).scalar_one()
            movidos = 0
            if total:
                if destino_id is None:
                    raise CategoriaEmUsoError(
                        f'A categoria "{nome}" tem {total} artigo(s). Informe para onde movê-los.')
                destino = conn.execute(select(categorias.c.nome).where(categorias.c.id == destino_id)).scalar()
                if destino is None or destino_id == categoria_id:
                    raise CategoriaDestinoError("Categoria de destino inválida.")
                movidos = conn.execute(
                    update(artigos).where(artigos.c.categoria == nome).values(categoria=destino)
                ).rowcount
            conn.execute(delete(categorias).where(categorias.c.id == categoria_id))
        _log("kb_categoria_excluida", id=categoria_id, nome=nome, artigos_movidos=movidos)
        return True

    def mover_artigo(self, artigo_id: int, categoria_id: int) -> bool:
        """Troca a categoria do artigo sem mexer no conteúdo nem em `atualizado_em`. False se o artigo não existir."""
        with self._engine.begin() as conn:
            destino = conn.execute(select(categorias.c.nome).where(categorias.c.id == categoria_id)).scalar()
            if destino is None:
                raise CategoriaDestinoError("Categoria de destino inválida.")
            result = conn.execute(update(artigos).where(artigos.c.id == artigo_id).values(categoria=destino))
        if result.rowcount:
            _log("kb_artigo_movido", id=artigo_id, categoria=destino)
        return bool(result.rowcount)

    # ------------------------------------------------------------------ artigos

    def obter(self, artigo_id: int) -> dict[str, Any] | None:
        with self._engine.connect() as conn:
            row = conn.execute(select(artigos).where(artigos.c.id == artigo_id)).first()
        return _serialize(row) if row else None

    def criar(self, dados: ArtigoInput) -> int:
        with self._engine.begin() as conn:
            valores = {**dados.__dict__, "categoria": self._resolver_categoria(conn, dados.categoria)}
            result = conn.execute(insert(artigos).values(**valores).returning(artigos.c.id))
            artigo_id = int(result.scalar_one())
        _log("kb_artigo_criado", id=artigo_id, titulo=dados.titulo, origem=dados.origem, autor=dados.autor)
        return artigo_id

    def atualizar(self, artigo_id: int, dados: ArtigoInput) -> bool:
        valores = {k: v for k, v in dados.__dict__.items() if k not in ("origem", "arquivo_origem")}
        with self._engine.begin() as conn:
            valores["categoria"] = self._resolver_categoria(conn, dados.categoria)
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
