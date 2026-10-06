# -*- coding: utf-8 -*-
"""Importação versionada das tabelas oficiais de NCM (Receita/Siscomex) e CEST (CONFAZ Conv. ICMS 142/18).

Uso:
    python src/fiscal_import.py ncm  --baixar
    python src/fiscal_import.py ncm  --arquivo nomenclatura.json
    python src/fiscal_import.py cest --baixar
    python src/fiscal_import.py cest --arquivo CV142_18.html
"""
import argparse
import hashlib
import html
import json
import logging
import re
import sqlite3
import sys
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fiscal_codes import normalize_cest, normalize_ncm  # noqa: E402
from fiscal_db import connect  # noqa: E402

logger = logging.getLogger(__name__)

NCM_URL = "https://portalunico.siscomex.gov.br/classif/api/publico/nomenclatura/download/json?perfil=PUBLICO"
NCM_FONTE = "Receita Federal - Sistema Classif (Siscomex)"
CEST_URL = "https://www.confaz.fazenda.gov.br/legislacao/convenios/2018/CV142_18"
CEST_FONTE = "CONFAZ - Convênio ICMS 142/18"
_FIM_INDETERMINADO = "9999-12-31"


class ImportacaoError(Exception):
    """Arquivo de origem ilegível ou fora do formato esperado."""


@dataclass(frozen=True)
class NcmRecord:
    codigo: str
    descricao: str
    inicio: str | None
    fim: str | None


@dataclass(frozen=True)
class CestRecord:
    codigo: str
    descricao: str
    segmento: str
    inicio: str | None
    fim: str | None
    ncm_prefixos: tuple[str, ...]


@dataclass
class RelatorioImportacao:
    tipo: str
    versao: str
    fonte: str
    processados: int = 0
    novos: int = 0
    alterados: int = 0
    inativados: int = 0
    inalterados: int = 0
    duplicados: int = 0
    duplicada: bool = False
    erros: list[str] = field(default_factory=list)

    def resumo(self) -> str:
        if self.duplicada:
            return f"Importação ignorada: {self.tipo} versão '{self.versao}' já importada (conteúdo idêntico)."
        return (
            f"Importação concluída ({self.tipo} - {self.versao})\n\n"
            f"{self.tipo}s processados: {self.processados}\n"
            f"{self.tipo}s novos: {self.novos}\n"
            f"{self.tipo}s alterados: {self.alterados}\n"
            f"{self.tipo}s inativados: {self.inativados}\n"
            f"Duplicados no arquivo: {self.duplicados}\n"
            f"Erros: {len(self.erros)}"
        )


def _log(level: int, event: str, **kwargs: Any) -> None:
    logger.log(level, json.dumps({"event": event, **kwargs}, ensure_ascii=False, default=str))


def _clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", "", html.unescape(value or ""))
    return re.sub(r"\s+", " ", value).strip()


def _br_date(value: str | None) -> str | None:
    """'01/04/2022' → '2022-04-01'; 31/12/9999 (indeterminado) → None."""
    if not value:
        return None
    parsed = datetime.strptime(value.strip(), "%d/%m/%Y").date().isoformat()
    return None if parsed == _FIM_INDETERMINADO else parsed


def _content_hash(records: Iterable[Any]) -> str:
    payload = json.dumps([asdict(r) for r in records], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- NCM (Siscomex JSON)

def parse_ncm_json(raw: bytes) -> tuple[str, list[NcmRecord], list[str]]:
    """Retorna (versão, registros de NCM com 8 dígitos, erros). Descrição inclui a hierarquia (posição > ... > item)."""
    try:
        data = json.loads(raw.decode("utf-8-sig"))
        nomenclaturas = data["Nomenclaturas"]
    except (ValueError, KeyError, UnicodeDecodeError) as exc:
        raise ImportacaoError(f"JSON de NCM inválido: {exc}") from exc

    versao = _clean_text(f"{data.get('Ato', '')} ({data.get('Data_Ultima_Atualizacao_NCM', '')})")
    descricoes: dict[str, str] = {}
    for item in nomenclaturas:
        descricoes[re.sub(r"\D", "", str(item.get("Codigo", "")))] = _clean_text(item.get("Descricao", "")).lstrip("- ").strip()

    records: list[NcmRecord] = []
    errors: list[str] = []
    for item in nomenclaturas:
        raw_code = str(item.get("Codigo", ""))
        digits = re.sub(r"\D", "", raw_code)
        if len(digits) != 8:
            continue  # capítulos/posições/subposições: só compõem a descrição
        codigo = normalize_ncm(digits)
        try:
            inicio, fim = _br_date(item.get("Data_Inicio")), _br_date(item.get("Data_Fim"))
        except ValueError:
            errors.append(f"NCM {raw_code}: data de vigência inválida")
            continue
        niveis = [descricoes.get(digits[:n]) for n in (4, 5, 6, 7)]
        hierarquia = [d for d in niveis if d] + [descricoes[digits]]
        records.append(NcmRecord(codigo, " > ".join(dict.fromkeys(hierarquia)), inicio, fim))
    return versao, records, errors


# --------------------------------------------------------------------------- CEST (CONFAZ HTML)

class _TableParser(HTMLParser):
    """Extrai <tr> → [células] → [(classe do <p>, texto)]."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[list[tuple[str, str]]]] = []
        self._row: list[list[tuple[str, str]]] | None = None
        self._cell: list[tuple[str, str]] | None = None
        self._p: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag == "td" and self._row is not None:
            self._cell = []
            self._row.append(self._cell)
        elif tag == "p" and self._cell is not None:
            self._p = [dict(attrs).get("class") or "", ""]

    def handle_endtag(self, tag: str) -> None:
        if tag == "p" and self._p is not None and self._cell is not None:
            self._cell.append((self._p[0], self._p[1]))
            self._p = None
        elif tag == "td":
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._p is not None:
            self._p[1] += data


_CEST_RE = re.compile(r"^\d{2}\.\d{3}\.\d{2}$")
_DATE_RE = re.compile(r"(\d{1,2})[º°]?\.(\d{2})\.(\d{4}|\d{2})(?!\d)")
_ITEMS_RE = re.compile(r"ite(?:m|ns)\s*([\d.,\se aoà]+)", re.IGNORECASE)


@dataclass
class _Remissao:
    inicio: str | None
    fim: str | None
    sem_efeitos: bool
    revogacao: bool
    anterior: bool  # nota de redação anterior (A8-2RemissaoAnt) → só vale para linhas históricas
    itens: list[tuple[float, float]]

    def cobre(self, item: str) -> bool:
        try:
            n = float(item)
        except ValueError:
            return False
        return any(a <= n <= b for a, b in self.itens)


def _parse_date_br_short(d: str, m: str, y: str) -> str | None:
    year = int(y) + 2000 if len(y) == 2 else int(y)
    try:
        return date(year, int(m), int(d)).isoformat()
    except ValueError:
        return None


def _parse_itens(text: str) -> list[tuple[float, float]]:
    match = _ITEMS_RE.search(text)
    if not match:
        return []
    chunk = re.split(r"\b(?:do|ao|em|pelo)\s+(?:Anexo|Conv|“|\")", match.group(1))[0]
    itens: list[tuple[float, float]] = []
    for a, b in re.findall(r"(\d+(?:\.\d+)?)(?:\s*(?:a|ao)\s*(\d+(?:\.\d+)?))?", chunk):
        lo = float(a)
        itens.append((lo, float(b) if b else lo))
    return itens


def _parse_remissao(text: str, anterior: bool) -> _Remissao:
    lowered = text.lower()
    datas = [d for d in (_parse_date_br_short(*m) for m in _DATE_RE.findall(text)) if d]
    inicio = fim = None
    if len(datas) >= 2:
        inicio, fim = datas[0], datas[1]
    elif len(datas) == 1:
        if "até" in lowered:
            fim = datas[0]
        else:
            inicio = datas[0]
    return _Remissao(
        inicio=inicio,
        fim=fim,
        sem_efeitos=bool(re.search(r"sem\s+e?feitos", lowered)),
        revogacao=lowered.startswith("revogad"),
        anterior=anterior,
        itens=_parse_itens(text),
    )


def _parse_ncm_cell(paragraphs: list[str]) -> tuple[str, ...]:
    prefixos: list[str] = []
    for text in paragraphs:
        if "cap" in text.lower():
            for a, b in re.findall(r"(\d{1,2})(?:\s*a\s*(\d{1,2}))?", text):
                for chap in range(int(a), int(b or a) + 1):
                    prefixos.append(f"{chap:02d}")
            continue
        for token in re.findall(r"\d[\d.]*\d|\d", text):
            if re.match(r"^\d{3}\.", token):  # "401.10" → zero à esquerda omitido
                token = "0" + token
            digits = token.replace(".", "")
            if 2 <= len(digits) <= 8:
                prefixos.append(digits)
    return tuple(dict.fromkeys(prefixos))


def parse_cest_html(raw: bytes) -> tuple[list[CestRecord], list[str], int]:
    """Retorna (registros, erros, linhas duplicadas consolidadas)."""
    parser = _TableParser()
    parser.feed(raw.decode("utf-8", errors="ignore"))

    merged: dict[tuple[str, str | None, str | None], CestRecord] = {}
    revogacoes: dict[str, str] = {}
    errors: list[str] = []
    duplicados = 0
    rem: _Remissao | None = None

    for row in parser.rows:
        cells = [[(cls, _clean_text(txt)) for cls, txt in cell] for cell in row]
        texts = [" ".join(t for _, t in cell if t).strip() for cell in cells]
        classes = " ".join(cls for cell in cells for cls, _ in cell)

        if "Remissao" in classes and not any(_CEST_RE.match(t) for t in texts):
            rem = _parse_remissao(next((t for t in texts if t), ""), anterior="RemissaoAnt" in classes)
            continue
        if len(texts) < 4 or not _CEST_RE.match(texts[1]):
            continue

        item, cest_fmt = texts[0], texts[1]
        historica = "verde" in classes
        descricao = next((t for t in texts[3:] if t), "")
        if rem is None or rem.anterior != historica:
            vig = None
        elif historica:
            vig = rem
        else:
            vig = rem if (not rem.itens or rem.cobre(item)) else None
            if not rem.itens or not rem.cobre(item):
                rem = None  # nota sem lista de itens vale só para a linha seguinte

        codigo = normalize_cest(cest_fmt)
        if not codigo:
            errors.append(f"CEST {cest_fmt}: formato inválido")
            continue
        if vig and vig.sem_efeitos:
            continue
        if descricao.upper().startswith("REVOGAD"):
            if vig and vig.inicio:
                revogacoes[codigo] = vig.inicio
            continue
        if not descricao:
            errors.append(f"CEST {cest_fmt}: sem descrição")
            continue

        record = CestRecord(
            codigo=codigo,
            descricao=descricao,
            segmento=codigo[:2],
            inicio=vig.inicio if vig else None,
            fim=vig.fim if vig else None,
            ncm_prefixos=_parse_ncm_cell([t for _, t in cells[2] if t]),
        )
        key = (record.codigo, record.inicio, record.fim)
        if key in merged:
            duplicados += 1
            atual = merged[key]
            prefixos = tuple(dict.fromkeys(atual.ncm_prefixos + record.ncm_prefixos))
            merged[key] = CestRecord(atual.codigo, atual.descricao, atual.segmento, atual.inicio, atual.fim, prefixos)
        else:
            merged[key] = record

    records: list[CestRecord] = []
    for rec in merged.values():
        revogado_em = revogacoes.get(rec.codigo)
        if revogado_em and rec.fim is None and (rec.inicio is None or rec.inicio < revogado_em):
            fim = (date.fromisoformat(revogado_em) - timedelta(days=1)).isoformat()
            rec = CestRecord(rec.codigo, rec.descricao, rec.segmento, rec.inicio, fim, rec.ncm_prefixos)
        records.append(rec)

    if not records:
        raise ImportacaoError("Nenhum CEST encontrado no HTML (layout do CONFAZ mudou?)")
    return records, errors, duplicados


# --------------------------------------------------------------------------- Persistência versionada

def _registrar_importacao(
    conn: sqlite3.Connection, tipo: str, versao: str, fonte: str, url: str | None,
    oficial: bool, conteudo_hash: str, data_importacao: str,
) -> int | None:
    if conn.execute("SELECT 1 FROM importacao WHERE tipo = ? AND conteudo_hash = ?", (tipo, conteudo_hash)).fetchone():
        return None
    cur = conn.execute(
        "INSERT INTO importacao (tipo, versao, fonte, url_fonte, fonte_oficial, conteudo_hash, data_importacao, relatorio) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, '{}')",
        (tipo, versao, fonte, url, int(oficial), conteudo_hash, data_importacao),
    )
    return int(cur.lastrowid)


def _encerrar(conn: sqlite3.Connection, table: str, where: str, params: tuple[Any, ...], corte: str) -> None:
    """Inativa a chave e encerra vigência em aberto (preserva histórico, nunca exclui)."""
    conn.execute(
        f"UPDATE {table} SET ativo = 0, updated_at = datetime('now'), "
        f"fim_vigencia = CASE WHEN fim_vigencia IS NULL OR fim_vigencia > ? THEN ? ELSE fim_vigencia END "
        f"WHERE {where}",
        (corte, corte, *params),
    )


def import_ncm(
    conn: sqlite3.Connection, raw: bytes, fonte: str = NCM_FONTE, url: str | None = NCM_URL,
    oficial: bool = True, data_importacao: date | None = None,
) -> RelatorioImportacao:
    versao, records, errors = parse_ncm_json(raw)
    hoje = data_importacao or date.today()
    report = RelatorioImportacao("NCM", versao, fonte, erros=errors)

    unique: dict[tuple[str, str | None], NcmRecord] = {}
    for rec in records:
        key = (rec.codigo, rec.inicio)
        if key in unique:
            report.duplicados += 1
            if unique[key] != rec:
                report.erros.append(f"NCM {rec.codigo}: registros conflitantes no arquivo (mantido o primeiro)")
            continue
        unique[key] = rec
    report.processados = len(unique)

    with conn:
        lote = _registrar_importacao(conn, "NCM", versao, fonte, url, oficial, _content_hash(unique.values()), hoje.isoformat())
        if lote is None:
            report.duplicada = True
            return report

        atuais = {
            (r["codigo"], r["inicio_vigencia"]): r
            for r in conn.execute("SELECT * FROM ncm WHERE ativo = 1")
        }
        corte = (hoje - timedelta(days=1)).isoformat()
        for key, rec in unique.items():
            atual = atuais.pop(key, None)
            if atual is not None and (atual["descricao"], atual["fim_vigencia"]) == (rec.descricao, rec.fim):
                report.inalterados += 1
                continue
            if atual is not None:
                conn.execute("UPDATE ncm SET ativo = 0, updated_at = datetime('now') WHERE id = ?", (atual["id"],))
                report.alterados += 1
            else:
                report.novos += 1
            conn.execute(
                "INSERT INTO ncm (codigo, descricao, inicio_vigencia, fim_vigencia, fonte, versao, importacao_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (rec.codigo, rec.descricao, rec.inicio, rec.fim, fonte, versao, lote),
            )
        for (codigo, inicio) in atuais:
            _encerrar(conn, "ncm", "codigo = ? AND COALESCE(inicio_vigencia, '') = COALESCE(?, '')", (codigo, inicio), corte)
            report.inativados += 1
        conn.execute("UPDATE importacao SET relatorio = ? WHERE id = ?", (json.dumps(asdict(report), ensure_ascii=False), lote))

    _log(logging.INFO, "importacao_fiscal", **{k: v for k, v in asdict(report).items() if k != "erros"}, erros=len(report.erros))
    return report


def import_cest(
    conn: sqlite3.Connection, raw: bytes, fonte: str = CEST_FONTE, url: str | None = CEST_URL,
    oficial: bool = True, data_importacao: date | None = None, versao: str | None = None,
) -> RelatorioImportacao:
    records, errors, duplicados = parse_cest_html(raw)
    hoje = data_importacao or date.today()
    conteudo_hash = _content_hash(records)
    versao = versao or f"Conv. ICMS 142/18 - {hoje.isoformat()} #{conteudo_hash[:8]}"
    report = RelatorioImportacao("CEST", versao, fonte, processados=len(records), duplicados=duplicados, erros=errors)

    def chave(codigo: str, inicio: str | None, fim: str | None) -> tuple[str, str, str]:
        return codigo, inicio or "", fim or ""

    with conn:
        lote = _registrar_importacao(conn, "CEST", versao, fonte, url, oficial, conteudo_hash, hoje.isoformat())
        if lote is None:
            report.duplicada = True
            return report

        atuais: dict[tuple[str, str, str], tuple[sqlite3.Row, tuple[str, ...]]] = {}
        for r in conn.execute("SELECT * FROM cest WHERE ativo = 1"):
            prefixos = tuple(p["ncm_prefixo"] for p in conn.execute(
                "SELECT ncm_prefixo FROM ncm_cest WHERE cest_id = ? ORDER BY id", (r["id"],)))
            atuais[chave(r["codigo"], r["inicio_vigencia"], r["fim_vigencia"])] = (r, prefixos)

        corte = (hoje - timedelta(days=1)).isoformat()
        for rec in records:
            key = chave(rec.codigo, rec.inicio, rec.fim)
            atual = atuais.pop(key, None)
            if atual is not None and (atual[0]["descricao"], set(atual[1])) == (rec.descricao, set(rec.ncm_prefixos)):
                report.inalterados += 1
                continue
            if atual is not None:
                conn.execute("UPDATE cest SET ativo = 0, updated_at = datetime('now') WHERE id = ?", (atual[0]["id"],))
                report.alterados += 1
            else:
                report.novos += 1
            cur = conn.execute(
                "INSERT INTO cest (codigo, descricao, segmento, inicio_vigencia, fim_vigencia, fonte, versao, importacao_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (rec.codigo, rec.descricao, rec.segmento, rec.inicio, rec.fim, fonte, versao, lote),
            )
            conn.executemany(
                "INSERT INTO ncm_cest (cest_id, ncm_prefixo) VALUES (?, ?)",
                [(cur.lastrowid, p) for p in rec.ncm_prefixos],
            )
        for (codigo, inicio, fim) in atuais:
            _encerrar(
                conn, "cest",
                "codigo = ? AND COALESCE(inicio_vigencia, '') = ? AND COALESCE(fim_vigencia, '') = ?",
                (codigo, inicio, fim), corte,
            )
            report.inativados += 1
        conn.execute("UPDATE importacao SET relatorio = ? WHERE id = ?", (json.dumps(asdict(report), ensure_ascii=False), lote))

    _log(logging.INFO, "importacao_fiscal", **{k: v for k, v in asdict(report).items() if k != "erros"}, erros=len(report.erros))
    return report


def _baixar(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (importador-fiscal)"})
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 - URL oficial fixa
        return resp.read()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Importa tabelas fiscais oficiais (versionadas).")
    ap.add_argument("tipo", choices=("ncm", "cest"))
    origem = ap.add_mutually_exclusive_group(required=True)
    origem.add_argument("--arquivo", type=Path, help="Arquivo oficial já baixado (JSON NCM / HTML CEST)")
    origem.add_argument("--baixar", action="store_true", help="Baixa da fonte oficial")
    ap.add_argument("--url", help="URL de origem (registrada na importação)")
    ap.add_argument("--fonte", help="Nome da fonte (padrão: fonte oficial)")
    ap.add_argument("--nao-oficial", action="store_true", help="Marca a importação como fonte secundária")
    ap.add_argument("--db", type=Path, help="Caminho do SQLite (padrão: data/fiscal.db)")
    args = ap.parse_args(argv)

    url_padrao, fonte_padrao = (NCM_URL, NCM_FONTE) if args.tipo == "ncm" else (CEST_URL, CEST_FONTE)
    url = args.url or (url_padrao if args.baixar else None)
    try:
        raw = _baixar(url_padrao) if args.baixar else args.arquivo.read_bytes()
    except (OSError, ValueError) as exc:
        _log(logging.ERROR, "importacao_fiscal_falhou", tipo=args.tipo, erro=str(exc))
        return 1

    conn = connect(args.db)
    try:
        importer = import_ncm if args.tipo == "ncm" else import_cest
        report = importer(conn, raw, fonte=args.fonte or fonte_padrao, url=url, oficial=not args.nao_oficial)
    except ImportacaoError as exc:
        _log(logging.ERROR, "importacao_fiscal_falhou", tipo=args.tipo, erro=str(exc))
        return 1
        if not report.duplicada:
            conn.execute("VACUUM")
    finally:
        conn.close()

    print(report.resumo())
    for erro in report.erros[:50]:
        print(f"  - {erro}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
