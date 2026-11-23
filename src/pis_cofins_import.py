# -*- coding: utf-8 -*-
"""Importa as tabelas de PIS/COFINS por NCM da EFD-Contribuições (Receita/SPED): 4.3.10 a 4.3.13.

Uso:
    python src/pis_cofins_import.py --baixar              # baixa do Portal SPED (consulta pública)
    python src/pis_cofins_import.py --pasta tabelas/      # arquivos 4_3_10.txt, 4_3_11.txt, ... já baixados

Cada tabela é substituída de forma atômica. Só entra o que dá para decidir pelo NCM na revenda
(monofásico CST 04, ST CST 05, alíquota zero CST 06); isenção/suspensão dependem da operação e ficam de fora.
"""
import argparse
import hashlib
import html
import http.cookiejar
import json
import logging
import re
import sqlite3
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fiscal_db import connect  # noqa: E402

logger = logging.getLogger(__name__)

FONTE = "Receita Federal - Portal SPED, EFD-Contribuições (tabelas externas)"
_URL = "http://www.sped.fazenda.gov.br/spedtabelas/AppConsulta/publico/aspx/ConsultaTabelasExternas.aspx?CodSistema=SpedPisCofins"
_CAMPO = "ctl00$ContentPlaceHolder1$"
# tabela -> (pacote, id da tabela no portal, CST sugerido para quem revende)
TABELAS: dict[str, tuple[str, str, str]] = {
    "4.3.10": ("72", "162", "04"),  # monofásica, alíquotas diferenciadas (CST 02 fabricante / 04 revenda)
    "4.3.11": ("72", "164", "04"),  # monofásica, alíquota por unidade (CST 03 fabricante / 04 revenda)
    "4.3.12": ("72", "163", "05"),  # substituição tributária
    "4.3.13": ("72", "166", "06"),  # alíquota zero
}


class ImportacaoPisCofinsError(Exception):
    """Tabela ilegível, vazia ou fora do formato esperado."""


@dataclass(frozen=True)
class RegraPisCofins:
    codigo: str
    descricao: str
    prefixo: str
    excecoes: str
    restrito: bool
    inicio: str
    fim: str | None


def _iso(ddmmaaaa: str) -> str | None:
    try:
        return datetime.strptime(ddmmaaaa.strip(), "%d%m%Y").date().isoformat()
    except ValueError:
        return None


def _prefixos(texto: str) -> list[str]:
    return [t for t in (x.strip() for x in texto.split(";")) if re.fullmatch(r"\d{2,8}", t)]


def parse_tabela(raw: bytes) -> tuple[str, list[RegraPisCofins]]:
    """Lê o arquivo da tabela (Latin-1, campos separados por '|'). Linhas sem NCM (regras por operação) são ignoradas."""
    linhas = raw.decode("latin-1").splitlines()
    m = re.match(r"vers.o=(\S+)\s+(.+)", linhas[0]) if linhas else None
    if not m:
        raise ImportacaoPisCofinsError("Cabeçalho da tabela não reconhecido (esperado 'versão=NN COLUNAS...').")
    colunas = [c.strip() for c in m.group(2).split(",")]
    for obrigatoria in ("CODIGO", "DESC_PROD", "DT_INI", "DT_FIM", "NCM"):
        if obrigatoria not in colunas:
            raise ImportacaoPisCofinsError(f"Coluna {obrigatoria} ausente na tabela.")
    idx = {c: i for i, c in enumerate(colunas)}

    def campo(partes: list[str], nome: str) -> str:
        i = idx.get(nome)
        return partes[i].strip() if i is not None and i < len(partes) else ""

    vistos: dict[RegraPisCofins, None] = {}
    for linha in linhas[1:]:
        if not linha.strip():
            continue
        partes = linha.split("|")
        inicio = _iso(campo(partes, "DT_INI"))
        fim_raw = campo(partes, "DT_FIM")
        fim = _iso(fim_raw) if fim_raw else None
        if inicio is None or (fim_raw and fim is None):
            continue
        ncm_ex = _prefixos(campo(partes, "NCM_EX"))
        # NCM_EX mistura NCMs excluídos (>= 4 dígitos) e códigos EX da TIPI (2 dígitos): o EX restringe a regra
        # a uma parte do NCM, o que o NCM sozinho não decide.
        restrito = bool(campo(partes, "EX_IPI")) or any(len(e) < 4 for e in ncm_ex)
        excecoes = ";".join(e for e in ncm_ex if len(e) >= 4)
        for prefixo in _prefixos(campo(partes, "NCM")):
            vistos.setdefault(RegraPisCofins(
                campo(partes, "CODIGO"), campo(partes, "DESC_PROD"), prefixo, excecoes, restrito, inicio, fim), None)
    return m.group(1), list(vistos)


def importar(conn: sqlite3.Connection, arquivos: dict[str, bytes]) -> dict[str, dict[str, str | int]]:
    """`arquivos`: tabela ('4.3.10'...) -> conteúdo bruto. Tudo ou nada."""
    lidos: dict[str, tuple[str, list[RegraPisCofins], str]] = {}
    for tabela, raw in arquivos.items():
        if tabela not in TABELAS:
            raise ImportacaoPisCofinsError(f"Tabela não suportada: {tabela}")
        versao, regras = parse_tabela(raw)
        if not regras:
            raise ImportacaoPisCofinsError(f"Tabela {tabela} sem regras por NCM; importação abortada.")
        lidos[tabela] = (versao, regras, hashlib.sha256(raw).hexdigest())

    agora = datetime.now().isoformat(timespec="seconds")
    resumo: dict[str, dict[str, str | int]] = {}
    with conn:
        for tabela, (versao, regras, digest) in lidos.items():
            cst = TABELAS[tabela][2]
            conn.execute("DELETE FROM pis_cofins_ncm WHERE tabela = ?", (tabela,))
            conn.executemany(
                "INSERT INTO pis_cofins_ncm (tabela, codigo, descricao, ncm_prefixo, excecoes, restrito, inicio, fim, cst) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(tabela, r.codigo, r.descricao, r.prefixo, r.excecoes, int(r.restrito), r.inicio, r.fim, cst)
                 for r in regras])
            conn.execute(
                "INSERT INTO pis_cofins_versao (tabela, versao, fonte, conteudo_hash, data_importacao) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(tabela) DO UPDATE SET versao = excluded.versao, "
                "fonte = excluded.fonte, conteudo_hash = excluded.conteudo_hash, "
                "data_importacao = excluded.data_importacao",
                (tabela, versao, FONTE, digest, agora))
            resumo[tabela] = {"versao": versao, "regras": len(regras)}
    logger.info(json.dumps({"event": "pis_cofins_importado", "tabelas": resumo}, ensure_ascii=False))
    return resumo


# ------------------------------------------------------------------ download (consulta pública do Portal SPED)

def _hidden(pagina: str) -> dict[str, str]:
    return {m.group(1): html.unescape(m.group(2))
            for m in re.finditer(r'<input[^>]*type="hidden"[^>]*name="([^"]+)"[^>]*value="([^"]*)"', pagina)}


def baixar(timeout: int = 60) -> dict[str, bytes]:
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def enviar(pagina: str, extra: dict[str, str]):  # noqa: ANN202
        dados = {**_hidden(pagina), **extra}
        req = urllib.request.Request(_URL, urllib.parse.urlencode(dados).encode(),
                                     {"Content-Type": "application/x-www-form-urlencoded"})
        return opener.open(req, timeout=timeout)

    arquivos: dict[str, bytes] = {}
    try:
        for tabela, (pacote, id_tabela, _cst) in TABELAS.items():
            inicial = opener.open(_URL, timeout=timeout).read().decode("utf-8", "replace")
            com_pacote = enviar(inicial, {"__EVENTTARGET": _CAMPO + "ddlPacotes", "__EVENTARGUMENT": "",
                                          _CAMPO + "ddlPacotes": pacote}).read().decode("utf-8", "replace")
            com_tabela = enviar(com_pacote, {"__EVENTTARGET": _CAMPO + "ddlTabelas", "__EVENTARGUMENT": "",
                                             _CAMPO + "ddlPacotes": pacote,
                                             _CAMPO + "ddlTabelas": id_tabela}).read().decode("utf-8", "replace")
            resposta = enviar(com_tabela, {_CAMPO + "ddlPacotes": pacote, _CAMPO + "ddlTabelas": id_tabela,
                                           _CAMPO + "btnBaixarTabela": "Baixar tabela"})
            conteudo = resposta.read()
            if "attachment" not in (resposta.headers.get("Content-Disposition") or ""):
                raise ImportacaoPisCofinsError(f"O portal não devolveu o arquivo da tabela {tabela}.")
            arquivos[tabela] = conteudo
    except OSError as exc:
        raise ImportacaoPisCofinsError(f"Falha de rede ao acessar o Portal SPED: {exc}") from exc
    return arquivos


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    origem = parser.add_mutually_exclusive_group(required=True)
    origem.add_argument("--baixar", action="store_true", help="baixa do Portal SPED")
    origem.add_argument("--pasta", type=Path, help="pasta com 4_3_10.txt ... 4_3_13.txt")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        if args.baixar:
            arquivos = baixar()
        else:
            arquivos = {t: (args.pasta / f"{t.replace('.', '_')}.txt").read_bytes() for t in TABELAS}
        resumo = importar(connect(), arquivos)
    except (ImportacaoPisCofinsError, OSError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    for tabela, r in resumo.items():
        print(f"Tabela {tabela} (versão {r['versao']}): {r['regras']} regras por NCM.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
