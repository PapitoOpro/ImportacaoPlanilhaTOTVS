# -*- coding: utf-8 -*-
"""Converte PDF (com texto) em rascunho de artigo Markdown. Sem OCR: PDF digitalizado é recusado."""
import io
import re
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_PAGINAS = 300

_BULLET_RE = re.compile(r"^[•●▪◦‣∙·\-–—*]\s+")
_NUMERADO_RE = re.compile(r"^(\d{1,3})[.)]\s+")
_SECAO_RE = re.compile(r"^\d+(?:\.\d+)*\.?\s+[A-ZÀ-Ú]")
_PAGINA_RE = re.compile(r"^(?:p[áa]gina\s*)?\d{1,4}(?:\s*(?:de|/)\s*\d{1,4})?$", re.IGNORECASE)


class PdfConversaoError(Exception):
    """PDF ilegível, protegido, sem texto ou grande demais."""


@dataclass(frozen=True)
class RascunhoArtigo:
    titulo: str
    conteudo: str
    paginas: int


def _limpar(linha: str) -> str:
    return re.sub(r"\s+", " ", linha.replace("\x00", "")).strip()


def _eh_titulo(linha: str) -> bool:
    if len(linha) > 80 or linha.endswith((".", ",", ";", ":")):
        return False
    letras = [c for c in linha if c.isalpha()]
    caixa_alta = len(letras) >= 3 and all(c.isupper() for c in letras)
    return caixa_alta or bool(_SECAO_RE.match(linha))


def _escape(linha: str) -> str:
    """Evita que texto comum vire sintaxe Markdown (#, >, |)."""
    return re.sub(r"^([#>|])", r"\\\1", linha)


def _linhas_repetidas(paginas: list[list[str]]) -> set[str]:
    """Cabeçalho/rodapé: linhas curtas presentes em mais da metade das páginas."""
    if len(paginas) < 3:
        return set()
    contagem = Counter(l for p in paginas for l in set(p) if len(l) <= 80)
    return {l for l, n in contagem.items() if n > len(paginas) / 2}


def _pagina_para_blocos(linhas: list[str]) -> list[str]:
    tamanhos = [len(l) for l in linhas if len(l) > 20]
    linha_tipica = statistics.median(tamanhos) if tamanhos else 80
    blocos: list[str] = []
    paragrafo = ""

    def fechar() -> None:
        nonlocal paragrafo
        if paragrafo:
            blocos.append(_escape(paragrafo))
            paragrafo = ""

    for linha in linhas:
        if _eh_titulo(linha):
            fechar()
            blocos.append(f"## {linha}")
            continue
        if _BULLET_RE.match(linha):
            fechar()
            blocos.append("- " + _BULLET_RE.sub("", linha))
            continue
        if _NUMERADO_RE.match(linha):
            fechar()
            blocos.append(_NUMERADO_RE.sub(r"\1. ", linha))
            continue
        if blocos and blocos[-1].startswith(("- ", )) and not paragrafo and linha[:1].islower():
            blocos[-1] += " " + linha  # continuação do item de lista
            continue
        if paragrafo.endswith("-") and linha[:1].islower():
            paragrafo = paragrafo[:-1] + linha  # palavra hifenizada na quebra
        else:
            paragrafo = f"{paragrafo} {linha}".strip()
        if linha.endswith((".", "!", "?", ":")) and len(linha) < 0.8 * linha_tipica:
            fechar()
    fechar()
    return blocos


def pdf_para_artigo(raw: bytes, nome_arquivo: str = "") -> RascunhoArtigo:
    try:
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted and not reader.decrypt(""):
            raise PdfConversaoError("PDF protegido por senha.")
        total = len(reader.pages)
    except PdfReadError as exc:
        raise PdfConversaoError(f"Arquivo PDF inválido: {exc}") from exc

    if total > MAX_PAGINAS:
        raise PdfConversaoError(f"PDF com {total} páginas (máximo {MAX_PAGINAS}).")

    paginas: list[list[str]] = []
    for page in reader.pages:
        texto = page.extract_text() or ""
        paginas.append([l for l in (_limpar(x) for x in texto.splitlines()) if l])

    repetidas = _linhas_repetidas(paginas)
    blocos: list[str] = []
    for linhas in paginas:
        uteis = [l for l in linhas if l not in repetidas and not _PAGINA_RE.match(l)]
        blocos.extend(_pagina_para_blocos(uteis))

    if not blocos:
        raise PdfConversaoError("O PDF não contém texto extraível (provavelmente digitalizado/imagem).")

    titulo = _limpar(str((reader.metadata or {}).get("/Title") or ""))
    if not titulo or titulo.lower() in {"untitled", "sem título", "documento"}:
        primeiro = next((b for b in blocos if b.startswith("## ")), "")
        titulo = primeiro[3:] if primeiro else Path(nome_arquivo).stem.replace("_", " ").strip()
    titulo = (titulo or "Artigo importado")[:200]

    if blocos[0] == f"## {titulo}":
        blocos = blocos[1:]
    return RascunhoArtigo(titulo=titulo, conteudo=_juntar(blocos), paginas=total)


def _eh_item(bloco: str) -> bool:
    return bloco.startswith("- ") or bool(re.match(r"^\d{1,3}\. ", bloco))


def _juntar(blocos: list[str]) -> str:
    """Parágrafos separados por linha em branco; itens de lista consecutivos na mesma lista."""
    partes: list[str] = []
    for i, bloco in enumerate(blocos):
        if i and not (_eh_item(bloco) and _eh_item(blocos[i - 1])):
            partes.append("\n")
        partes.append(bloco + "\n")
    return "".join(partes).strip()
