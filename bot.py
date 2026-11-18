import asyncio
import email.utils
import hashlib
import html
import json
import logging
import os
import random
import re
import sys
import tempfile
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from xml.etree.ElementTree import Element as XmlElement

import httpx
import trafilatura
import yt_dlp

try:
    import defusedxml.ElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET

# ==============================================================================
# CONFIGURAÇÃO DE LOGGING ESTRUTURADO
# ==============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("BotInvestidor")

# ==============================================================================
# CONSTANTES E CONFIGURAÇÕES DE AMBIENTE (GITHUB SECRETS / ENV)
# ==============================================================================
HISTORICO_ARQUIVO = Path(os.environ.get("HISTORICO_ARQUIVO", "historico_enviados.json"))
GRUPO_URL = os.environ.get("TELEGRAM_GROUP_URL", "https://t.me/+dxKqjeavEWYzMzYx")

MAX_HISTORICO = 800
MAX_NOTICIAS_POR_VEZ = int(os.environ.get("MAX_NOTICIAS_POR_VEZ", "5"))
MAX_IDADE_MINUTOS = int(os.environ.get("MAX_IDADE_MINUTOS", "120"))
SCORE_MINIMO_CORTE = int(os.environ.get("SCORE_MINIMO_CORTE", "16"))
MAX_CONCURRENT_REQUESTS = 6

MAX_HTML_BYTES = 5 * 1024 * 1024          # 5 MB para páginas HTML e fotos
MAX_VIDEO_BYTES = 50 * 1024 * 1024        # 50 MB (Limite da Telegram Bot API)
TELEGRAM_CAPTION_LIMIT = 1024             # Limite oficial de caption no Telegram

TZ_BRASILIA = timezone(timedelta(hours=-3))

TEXTOS_BOTAO_GRUPO = [
    "💬 E agora? Veja a opinião do grupo",
    "📊 Como isso afeta o mercado? Debata aqui",
    "🗣️ Trocar ideia com outros investidores",
    "📈 Oportunidade ou risco? Opine no grupo",
    "💡 Veja a visão da nossa comunidade",
    "🔥 Assunto rendendo! Participe no grupo",
    "🤝 Entrar na comunidade de investidores",
    "💬 Comente essa notícia no nosso Grupo",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}

FEEDS = [
    "https://www.infomoney.com.br/feed/",
    "https://www.infomoney.com.br/mercados/feed/",
    "https://www.moneytimes.com.br/feed/",
    "https://www.seudinheiro.com/feed/",
    "https://www.suno.com.br/noticias/feed/",
    "https://einvestidor.estadao.com.br/feed/",
    "https://g1.globo.com/rss/g1/economia/",
    "https://braziljournal.com/feed",
    "https://exame.com/feed/",
    "https://www.infomoney.com.br/politica/feed/",
    "https://g1.globo.com/rss/g1/politica/",
    "https://g1.globo.com/rss/g1/mundo/",
    "https://www.cnnbrasil.com.br/economia/feed/",
    "https://feeds.bbci.co.uk/portuguese/rss.xml",
    "https://portaldobitcoin.uol.com.br/feed/",
    "https://livecoins.com.br/feed/",
]

# Caminhos de URL que nunca devem entrar em um canal de mercado financeiro
CAMINHOS_URL_BLOQUEADOS = (
    "/esporte/", "/esportes/", "/futebol/", "/entretenimento/", "/pop/",
    "/estilo/", "/viagem/", "/gastronomia/", "/astrologia/", "/loterias/",
    "/carreira/", "/curiosidades/", "/tv/", "/famosos/", "/cultura/",
    "/policia/", "/apostas/",
)

BLOQUEADOS_PROPAGANDA = (
    "black friday", "black xp", "promoção", "promocao", "desconto", "descontos", "cupom",
    "turbinam benefícios", "turbina benefícios", "benefícios em cartões", "beneficios em cartoes",
    "cashback", "pontos e milhas", "acumule pontos", "compras parceladas", "condições exclusivas",
    "condicoes exclusivas", "oferta", "ofertas", "compre com", "ganhe até", "sorteio",
    "curso gratuito", "inscrições abertas", "inscricoes abertas", "participe do",
    "webinar", "mentoria", "baixe o e-book", "baixe o app", "abra sua conta",
    "cadastre-se", "treinamento", "workshop", "evento online", "evento gratuito",
    "ensinará a operar", "aprenda a operar", "de forma automatizada",
    "cartão de crédito", "cartao de credito", "cartão black", "anuidade zero",
    "limite do cartão", "empréstimo pessoal", "financiamento de veículos",
    "horóscopo", "signo", "loteria", "mega-sena", "bbb", "fofoca", "onde assistir",
    "escalação", "futebol", "novela", "brasileirão", "campeonato brasileiro",
    "rebaixamento", "data fifa", "libertadores", "copa do brasil", "champions league",
    "artilheiro", "goleiro", "estádio", "torcida", "série a", "série b", "35ª rodada",
    "tabela do campeonato", "eliminatórias", "seleção brasileira",
    # Operações policiais, crimes e esquemas de apostas (bets)
    "esquema de bets", "apostas online", "apostas de quota fixa", "cassino online",
    "jogo do tigrinho", "mandados de busca", "mandado de busca", "busca e apreensão",
    "mandado de prisão", "operação cash out", "operação da pf", "equipes policiais",
    "lavagem de dinheiro", "quadrilha", "assalto", "sequestro", "homicídio",
    "tráfico de drogas", "facção criminosa", "delegacia",
)

STOPWORDS_DEDUP = {
    "aprova", "anuncia", "diz", "após", "apos", "sobre", "pode", "novo", "nova",
    "nesta", "neste", "segundo", "veja", "confira", "contra", "entre", "mais",
    "para", "como", "onde", "quando", "quem", "qual", "está", "esta", "estão",
    "foram", "será", "pagamento", "tem", "faz", "vai", "com", "por", "que", "dos", "das",
    "fecham", "fecha", "sobe", "cai", "alta", "queda", "dias", "pontos",
}

# ==============================================================================
# TAXONOMIA FINANCEIRA OBRIGATÓRIA (PESOS E EXPRESSÕES REGULARES)
# ==============================================================================
TAXONOMIA_PESOS: Dict[str, Dict[str, Any]] = {
    "PROVENTOS": {
        "label": "💰 <b>PROVENTOS &amp; DIVIDENDOS</b>",
        "termos": {
            "dividendos": 18,
            "dividendo": 18,
            "jcp": 20,
            "juros sobre capital próprio": 20,
            "juros sobre o capital próprio": 20,
            "rendimentos": 12,
            "data-com": 22,
            "data com": 20,
            "data-ex": 18,
            "proventos": 16,
            "dividend yield": 16,
            "bonificação": 14,
        },
    },
    "RESULTADOS_TRIMESTRAIS": {
        "label": "📊 <b>RESULTADOS &amp; BALANÇOS</b>",
        "termos": {
            "balanço": 15,
            "lucro líquido": 20,
            "ebitda": 20,
            "lajida": 18,
            "prejuízo": 16,
            "receita líquida": 18,
            "receita bruta": 16,
            "receita operacional": 16,
            "margem líquida": 16,
            "margem ebitda": 16,
            "roe": 14,
            "alavancagem": 14,
            "guidance": 16,
            "1t": 10, "2t": 10, "3t": 10, "4t": 10,
        },
    },
    "EVENTOS_CORPORATIVOS": {
        "label": "🤝 <b>EVENTOS CORPORATIVOS &amp; M&amp;A</b>",
        "termos": {
            "fato relevante": 24,
            "fusão": 18,
            "aquisição": 18,
            "m&a": 18,
            "recompra de ações": 22,
            "programa de recompra": 20,
            "opa": 22,
            "oferta pública": 18,
            "follow-on": 18,
            "ipo": 16,
            "cisão": 18,
            "incorporação": 16,
            "grupamento": 16,
            "desdobramento": 16,
            "recuperação judicial": 20,
            "privatização": 16,
            "concessão": 14,
            "leilão": 14,
        },
    },
    "MACROECONOMIA": {
        "label": "🏛️ <b>MACROECONOMIA &amp; MERCADOS</b>",
        "termos": {
            "copom": 20,
            "selic": 20,
            "taxa de juros": 16,
            "banco central": 16,
            "fed": 18,
            "fomc": 18,
            "ipca": 20,
            "inflação": 14,
            "pib": 15,
            "treasury": 18,
            "treasuries": 18,
            "boletim focus": 18,
            "payroll": 18,
            "cpi": 16,
            "arcabouço fiscal": 16,
            "meta fiscal": 15,
            "tesouro direto": 15,
            "câmbio": 12,
            "dólar": 12,
            "ibovespa": 14,
            "b3": 14,
            "wall street": 14,
            "s&p 500": 14,
            "nasdaq": 14,
            "petróleo": 12,
            "minério de ferro": 14,
            "bitcoin": 14,
            "tarifas": 12,
        },
    },
}

REGEX_TICKER_B3 = re.compile(r"\b([A-Z]{4}(?:3|4|11))\b")

REGEX_METRICAS_NUMERCIAS = re.compile(
    r"("
    r"[+-]?\d+(?:[.,]\d+)?\s*%"
    r"|(?:R\$|US\$|€|£)\s*\d+(?:[.,]\d+)?"
    r"|\b\d+(?:[.,]\d+)?\s*(?:milhões|bilhões|trilhões|mil|bi|tri|pontos-base|bps)\b"
    r")",
    flags=re.IGNORECASE,
)

REGEX_YOUTUBE_ID = re.compile(
    r"(?:youtube\.com/(?:watch\?v=|embed/|shorts/|live/)|youtu\.be/)([A-Za-z0-9_-]{11})",
    flags=re.IGNORECASE,
)


@dataclass
class AnaliseHeuristica:
    score_total: int
    categoria_chave: str
    categoria_label: str
    tickers: List[str] = field(default_factory=list)
    termos_detectados: List[str] = field(default_factory=list)


@dataclass
class NoticiaPreparada:
    link: str
    titulo: str
    texto_html: str
    score: int
    dt_pub: datetime
    imagem: Optional[str] = None
    video_candidato_url: Optional[str] = None
    audio: Optional[str] = None


# ==============================================================================
# MOTOR HEURÍSTICO DE SCORING E TAXONOMIA FINANCEIRA
# ==============================================================================
def extrair_tickers_b3(texto: str) -> List[str]:
    encontrados = REGEX_TICKER_B3.findall(texto)
    falsos_positivos = {"HTTP3", "HTML4", "IPV4", "UTF8", "MP4", "FIFA3", "COPA3"}
    return [t for t in dict.fromkeys(encontrados) if t not in falsos_positivos]


def formatar_tickers_no_texto(texto_html_escapado: str) -> str:
    return REGEX_TICKER_B3.sub(r"<b>#\1</b>", texto_html_escapado)


def avaliar_impacto_financeiro(titulo: str, paragrafos: List[str]) -> AnaliseHeuristica:
    corpo_texto = " ".join(paragrafos)
    texto_completo = f"{titulo} {corpo_texto}"
    # Ignora "Receita Federal" na contagem de receita corporativa de balanço
    titulo_low = re.sub(r"\breceita federal\b", "órgão fiscal", titulo.lower())
    corpo_low = re.sub(r"\breceita federal\b", "órgão fiscal", corpo_texto.lower())

    pontos_por_categoria: Dict[str, int] = {cat: 0 for cat in TAXONOMIA_PESOS}
    termos_encontrados: List[str] = []

    for cat_key, dados_cat in TAXONOMIA_PESOS.items():
        for termo, peso in dados_cat["termos"].items():
            padrao = r"(?<!\w)" + re.escape(termo) + r"(?!\w)"
            no_titulo = bool(re.search(padrao, titulo_low))
            no_corpo = bool(re.search(padrao, corpo_low))

            if no_titulo:
                pontos_por_categoria[cat_key] += int(peso * 2.0)
                termos_encontrados.append(termo)
            elif no_corpo:
                pontos_por_categoria[cat_key] += peso
                termos_encontrados.append(termo)

    tickers = extrair_tickers_b3(texto_completo)
    bonus_tickers = min(len(tickers) * 15, 45)
    soma_taxonomia = sum(pontos_por_categoria.values())

    # TRAVA OBRIGATÓRIA: Se a matéria não tiver NENHUM termo da taxonomia financeira
    # nem ticker da B3, o score é ZERO (evita que notícias de esportes com '%' passem).
    if soma_taxonomia == 0 and bonus_tickers == 0:
        return AnaliseHeuristica(
            score_total=0,
            categoria_chave="DESCARTADA",
            categoria_label="",
        )

    metricas = REGEX_METRICAS_NUMERCIAS.findall(texto_completo)
    bonus_metricas = min(len(metricas) * 3, 18)

    categoria_dominante = max(pontos_por_categoria, key=lambda k: pontos_por_categoria[k])
    score_final = soma_taxonomia + bonus_tickers + bonus_metricas

    if pontos_por_categoria[categoria_dominante] > 0:
        cat_label = TAXONOMIA_PESOS[categoria_dominante]["label"]
    else:
        categoria_dominante = "TICKERS_B3"
        cat_label = "📈 <b>RADAR B3 &amp; AÇÕES</b>"

    return AnaliseHeuristica(
        score_total=score_final,
        categoria_chave=categoria_dominante,
        categoria_label=cat_label,
        tickers=tickers,
        termos_detectados=list(dict.fromkeys(termos_encontrados)),
    )


# ==============================================================================
# CONTROLE DE DUPLICIDADE E PERSISTÊNCIA ATÔMICA
# ==============================================================================
def gerar_hash_url(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    query_limpa = "&".join(
        f"{k}={v}"
        for k, v in urllib.parse.parse_qsl(p.query)
        if not k.lower().startswith(("utm_", "fbclid", "gclid", "ref"))
    )
    url_canonica = urllib.parse.urlunsplit((p.scheme, p.netloc, p.path.rstrip("/"), query_limpa, ""))
    return hashlib.sha256(url_canonica.encode("utf-8")).hexdigest()[:16]


def carregar_historico() -> Tuple[List[str], List[str]]:
    if HISTORICO_ARQUIVO.exists():
        try:
            dados = json.loads(HISTORICO_ARQUIVO.read_text(encoding="utf-8"))
            if isinstance(dados, dict):
                return dados.get("links", []), dados.get("titulos", [])
            if isinstance(dados, list):
                return dados, []
        except Exception as exc:
            logger.error("Erro ao ler arquivo de histórico: %s", exc)
    return [], []


def salvar_historico(links: List[str], titulos: List[str]) -> None:
    try:
        dados = {
            "links": list(dict.fromkeys(links))[-MAX_HISTORICO:],
            "titulos": list(dict.fromkeys(titulos))[-MAX_HISTORICO:],
        }
        temp_file = HISTORICO_ARQUIVO.with_suffix(".tmp")
        temp_file.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_file, HISTORICO_ARQUIVO)
    except Exception as exc:
        logger.error("Erro ao salvar histórico atomicamente: %s", exc)


def eh_mesma_noticia(novo_titulo: str, titulos_anteriores: List[str]) -> bool:
    tokens_novo = {
        p for p in re.findall(r"[a-z0-9áàéíóúâêîôûãõç]+", novo_titulo.lower())
        if len(p) > 3 and p not in STOPWORDS_DEDUP
    }
    if not tokens_novo:
        return False

    for antigo in titulos_anteriores:
        tokens_antigo = {
            p for p in re.findall(r"[a-z0-9áàéíóúâêîôûãõç]+", antigo.lower())
            if len(p) > 3 and p not in STOPWORDS_DEDUP
        }
        if not tokens_antigo:
            continue
        inter = tokens_novo & tokens_antigo
        ratio = len(inter) / min(len(tokens_novo), len(tokens_antigo))
        if len(inter) >= 2 and ratio >= 0.35:
            return True
    return False


# ==============================================================================
# UTILITÁRIOS DE LIMPEZA E EXTRAÇÃO DE TEXTO (TRAFILATURA)
# ==============================================================================
def gerar_botao_grupo_aleatorio() -> Dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": random.choice(TEXTOS_BOTAO_GRUPO), "url": GRUPO_URL}]
        ]
    }


def normalizar_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    url = html.unescape(url.strip())
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith(("http://", "https://")):
        return None
    m_yt = REGEX_YOUTUBE_ID.search(url)
    if m_yt:
        return f"https://www.youtube.com/watch?v={m_yt.group(1)}"
    try:
        p = urllib.parse.urlsplit(url)
        caminho = urllib.parse.quote(urllib.parse.unquote(p.path), safe="/:@&+$,;=-_.~")
        return urllib.parse.urlunsplit((p.scheme, p.netloc, caminho, p.query, p.fragment))
    except Exception:
        return None


def extrair_data_publicacao(item: XmlElement) -> Optional[datetime]:
    for tag in ("pubDate", "{http://purl.org/dc/elements/1.1/}date", "published", "updated"):
        el = item.find(tag)
        if el is not None and el.text:
            texto_data = el.text.strip()
            try:
                dt =
