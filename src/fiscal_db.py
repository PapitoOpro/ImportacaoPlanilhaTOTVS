# -*- coding: utf-8 -*-
"""Persistência da base fiscal (SQLite). Schema + conexão."""
import os
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "fiscal.db"

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS importacao (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo            TEXT NOT NULL CHECK (tipo IN ('NCM', 'CEST')),
    versao          TEXT NOT NULL,
    fonte           TEXT NOT NULL,
    url_fonte       TEXT,
    fonte_oficial   INTEGER NOT NULL DEFAULT 1 CHECK (fonte_oficial IN (0, 1)),
    conteudo_hash   TEXT NOT NULL,
    data_importacao TEXT NOT NULL,
    relatorio       TEXT NOT NULL,
    UNIQUE (tipo, conteudo_hash)
);

CREATE TABLE IF NOT EXISTS ncm (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo          TEXT NOT NULL CHECK (length(codigo) = 8 AND codigo NOT GLOB '*[^0-9]*'),
    descricao       TEXT NOT NULL,
    inicio_vigencia TEXT,
    fim_vigencia    TEXT,
    ativo           INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1)),
    fonte           TEXT NOT NULL,
    versao          TEXT NOT NULL,
    importacao_id   INTEGER NOT NULL REFERENCES importacao (id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ncm_versao
    ON ncm (codigo, versao, COALESCE(inicio_vigencia, ''));
CREATE INDEX IF NOT EXISTS ix_ncm_codigo ON ncm (codigo);

CREATE TABLE IF NOT EXISTS cest (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo          TEXT NOT NULL CHECK (length(codigo) = 7 AND codigo NOT GLOB '*[^0-9]*'),
    descricao       TEXT NOT NULL,
    segmento        TEXT NOT NULL,
    inicio_vigencia TEXT,
    fim_vigencia    TEXT,
    ativo           INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1)),
    fonte           TEXT NOT NULL,
    versao          TEXT NOT NULL,
    importacao_id   INTEGER NOT NULL REFERENCES importacao (id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_cest_versao
    ON cest (codigo, versao, COALESCE(inicio_vigencia, ''), COALESCE(fim_vigencia, ''));
CREATE INDEX IF NOT EXISTS ix_cest_codigo ON cest (codigo);

-- N:N. O Convênio 142/18 relaciona CEST a NCM completo OU a prefixo (capítulo/posição/subposição),
-- por isso a relação guarda o prefixo e não um FK para ncm. Vigência/fonte/versão vêm do registro cest.
CREATE TABLE IF NOT EXISTS ncm_cest (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    cest_id     INTEGER NOT NULL REFERENCES cest (id) ON DELETE CASCADE,
    ncm_prefixo TEXT NOT NULL CHECK (length(ncm_prefixo) BETWEEN 2 AND 8 AND ncm_prefixo NOT GLOB '*[^0-9]*'),
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (cest_id, ncm_prefixo)
);
CREATE INDEX IF NOT EXISTS ix_ncm_cest_prefixo ON ncm_cest (ncm_prefixo);

-- Estrutura para regras tributárias futuras (sem lógica automática por enquanto).
CREATE TABLE IF NOT EXISTS regras_fiscais (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ncm_id            INTEGER REFERENCES ncm (id),
    cest_id           INTEGER REFERENCES cest (id),
    uf                TEXT CHECK (uf IS NULL OR length(uf) = 2),
    origem            TEXT,
    regime_tributario TEXT CHECK (regime_tributario IS NULL OR regime_tributario IN ('1', '2', '3')),
    tipo_operacao     TEXT,
    cst               TEXT,
    csosn             TEXT,
    cfop              TEXT CHECK (cfop IS NULL OR length(cfop) = 4),
    aliquota_icms     REAL,
    reducao_bc        REAL,
    mva               REAL,
    pis_cst           TEXT,
    cofins_cst        TEXT,
    ipi_cst           TEXT,
    descricao         TEXT,
    inicio_vigencia   TEXT,
    fim_vigencia      TEXT,
    fonte             TEXT,
    versao            TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def db_path() -> Path:
    return Path(os.environ.get("FISCAL_DB_PATH") or DEFAULT_DB_PATH)


def connect(path: Path | str | None = None, readonly: bool = False) -> sqlite3.Connection:
    target = Path(path) if path else db_path()
    if readonly:
        if not target.exists():
            raise FileNotFoundError(f"Base fiscal não encontrada: {target}")
        conn = sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True, check_same_thread=False)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(target)
        conn.executescript(SCHEMA)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def connect_memory() -> sqlite3.Connection:
    """Base em memória (testes)."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    conn.row_factory = sqlite3.Row
    return conn
