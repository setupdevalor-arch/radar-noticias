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
from dataclasses import dataclass
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
logger = logging.getLogger("FinanceNewsBot")

# ==============================================================================
# CONSTANTES E CONFIGURAÇÕES DE AMBIENTE (GITHUB SECRETS / ENV)
# ==============================================================================
HISTORICO_ARQUIVO = Path(os.environ.get("HISTORICO_ARQUIVO", "historico_enviados.json"))
GRUPO_URL = os.environ.get("TELEGRAM_GROUP_URL", "https://t.me/+dxKqjeavEWYzMzYx")

MAX_HISTORICO = 800
MAX_NOTICIAS_POR_VEZ = int(os.environ.get("MAX_NOTICIAS_POR_VEZ", "5"))
MAX_IDADE_MINUTOS = int(os.environ.get("MAX_IDADE_MINUTOS", "120"))
MAX_CONCURRENT_REQUESTS = 6

MAX_HTML_BYTES = 5 * 1024 * 1024          # 5 MB para páginas HTML e imagens
MAX_VIDEO_BYTES = 50 * 1024 * 1024        # 50 MB (Limite oficial da Telegram Bot API para upload)
TELEGRAM_CAPTION_LIMIT = 1024             # Limite de caracteres em legendas de mídia

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
    "https://www.cnnbrasil.com.br/politica/feed/",
    "https://g1.globo.com/rss/g1/mundo/",
    "https://www.cnnbrasil.com.br/internacional/feed/",
    "https://feeds.bbci.co.uk/portuguese/rss.xml",
    "https://portaldobitcoin.uol.com.br/feed/",
    "https://br.cointelegraph.com/rss",
]

BLOQUEADOS_PROPAGANDA = (
    "black friday", "black xp", "promoção", "promocao", "desconto", "descontos", "cupom",
    "turbinam benefícios", "turbina benefícios", "benefícios em cartões", "beneficios em cartoes",
    "cashback", "pontos e milhas", "acumule pontos", "compras parceladas", "condições exclusivas",
    "condicoes exclusivas", "oferta", "ofertas", "compre com", "ganhe até", "sorteio",
    "curso gratuito", "inscrições abertas", "inscricoes abertas", "participe do",
    "webinar", "mentoria", "baixe o e-book", "baixe o app", "abra sua conta",
    "cadastre-se", "treinamento", "workshop", "evento online", "evento gratuito",
    "cartão de crédito", "cartao de credito", "cartão black", "anuidade zero",
    "limite do cartão", "empréstimo pessoal", "financiamento de veículos",
    "fundo imobiliário", "fundos imobiliários", "fii ", "fiis", "fiagro", "ifix",
    "por cota", "cotistas", "rendimento por cota",
    "horóscopo", "signo", "loteria", "mega-sena", "bbb", "fofoca", "onde assistir",
    "escalação", "futebol", "novela",
)

STOPWORDS_DEDUP = {
    "aprova", "anuncia", "diz", "após", "apos", "sobre", "pode", "novo", "nova",
    "nesta", "neste", "segundo", "veja", "confira", "contra", "entre", "mais",
    "para", "como", "onde", "quando", "quem", "qual", "está", "esta", "estão",
    "foram", "será", "pagamento", "tem", "faz", "vai", "com", "por", "que", "dos", "das",
    "fecham", "fecha", "sobe", "cai", "alta", "queda", "dias", "pontos",
}

REGEX_INDICADORES_FINANCEIROS = re.compile(
    r"("
    r"[+-]?\d+(?:[.,]\d+)?\s*%"
    r"|(?:R\$|US\$|€|£)\s*\d+(?:[.,]\d+)?"
    r"|\b\d+(?:[.,]\d+)?\s*(?:milhões|bilhões|trilhões|mil|bi|tri)\b"
    r"|\b(?:alta|queda|subiu|caiu|recuou|avançou|disparou|desabou|"
    r"lucro|prejuízo|ebitda|receita|dividendos|selic|ipca|pib|juros|"
    r"inflação|dólar|ibovespa|pontos|cotação|fechou)\b"
    r")",
    flags=re.IGNORECASE,
)


@dataclass
class NoticiaPreparada:
    link: str
    titulo: str
    texto_html: str
    imagem: Optional[str] = None
    video_candidato_url: Optional[str] = None
    audio: Optional[str] = None


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
# UTILITÁRIOS GERAIS E LIMPEZA DE TEXTO
# ==============================================================================
def gerar_botao_grupo_aleatorio() -> Dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": random.choice(TEXTOS_BOTAO_GRUPO), "url": GRUPO_URL}]
        ]
    }


def contem_termo(texto: str, lista_termos: Tuple[str, ...]) -> bool:
    for termo in lista_termos:
        if re.search(r"(?<!\w)" + re.escape(termo) + r"(?!\w)", texto, flags=re.IGNORECASE):
            return True
    return False


def normalizar_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    url = html.unescape(url.strip())
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith(("http://", "https://")):
        return None
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
                dt = email.utils.parsedate_to_datetime(texto_data)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=TZ_BRASILIA)
                return dt.astimezone(timezone.utc)
            except Exception:
                pass
            try:
                dt = datetime.fromisoformat(texto_data.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=TZ_BRASILIA)
                return dt.astimezone(timezone.utc)
            except Exception:
                pass
    return None


def eh_propaganda_ou_lixo(titulo: str, descricao: str) -> bool:
    texto = f"{titulo} {descricao}".lower()
    return any(b in texto for b in BLOQUEADOS_PROPAGANDA)


def limpar_caudas_wordpress(texto: str) -> str:
    t = re.sub(r"The post .*? appeared first on .*$", "", texto, flags=re.IGNORECASE)
    t = re.sub(r"O post .*? apareceu primeiro em .*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"appeared first on .*$", "", t, flags=re.IGNORECASE)
    return t.strip()


def remover_carimbo_data_g1(txt: str) -> str:
    if not txt:
        return ""
    txt = re.sub(
        r"\b\d{1,2}/\d{1,2}/\d{2,4}\s*(?:às\s*|-?\s*)?\d{1,2}[h:]\d{0,2}\b"
        r"(?:\s*[•·|\-–—]?\s*Atualizado\s*(?:há\s*|em\s*)?(?:\d+|um|uma|poucos|alguns)?\s*"
        r"(?:minuto|hora|segundo|dia|mês|meses|ano)s?(?:\s+atrás)?)?",
        " ",
        txt,
        flags=re.IGNORECASE,
    )
    txt = re.sub(
        r"[•·|\-–—]?\s*\bAtualizado\s+(?:há\s+|em\s+)?(?:\d+|um|uma|poucos|alguns)\s*"
        r"(?:minuto|hora|segundo|dia|mês|meses|ano)s?(?:\s+atrás)?\b",
        " ",
        txt,
        flags=re.IGNORECASE,
    )
    return " ".join(txt.split()).strip()


def limpar_html(txt: str) -> str:
    if not txt:
        return ""
    txt = re.sub(
        r"<(script|style|noscript|svg|head|nav|footer|header|aside|form)\b[^>]*>.*?</\1>",
        " ",
        txt,
        flags=re.DOTALL | re.IGNORECASE,
    )
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = html.unescape(txt)
    txt = re.sub(r"https?://\S+|www\.\S+", "", txt, flags=re.IGNORECASE)
    txt = remover_carimbo_data_g1(txt)
    return " ".join(txt.split()).strip()


def paragrafo_eh_ruido_editorial(txt: str, titulo: str) -> bool:
    if not txt or len(txt) < 45:
        return True
    ruidos = (
        "{", "}", "@type", "@context", "schema.org", "dataLayer", "window.", "function(",
        "gostaria de receber", "assine a", "exclusivo para assinantes", "inscreva-se",
        "todos os direitos reservados", "the post", "apareceu primeiro", "leia também",
        "veja também", "confira também", "siga o", "canal no whatsapp", "receba notícias",
        "reprodução/", "foto:", "crédito:", "imagem:", "divulgação/",
    )
    t_low = txt.lower()
    if any(r in t_low for r in ruidos):
        return True
    if titulo and t_low.startswith(titulo[:30].lower()):
        return True
    if txt.count("[") >= 3 or txt.count("]") >= 3:
        return True
    return False


# ==============================================================================
# 1. EXTRAÇÃO COM TRAFILATURA E ESTRUTURAÇÃO ALGORÍTMICA (COMEÇO, MEIO, FIM)
# ==============================================================================
def extrair_paragrafos_trafilatura(raw_html: str, desc_bruta: str, titulo: str) -> List[str]:
    paragrafos_limpos: List[str] = []

    if raw_html:
        texto_extraido = trafilatura.extract(
            raw_html,
            include_comments=False,
            include_tables=False,
            no_fallback=False,
            favor_precision=True,
        )
        if texto_extraido:
            for linha in texto_extraido.splitlines():
                p = limpar_caudas_wordpress(limpar_html(linha))
                p = re.sub(
                    r"^(?:Negócios|Economia|INFRA|Tecnologia|Weekend|Podcasts|Journal|Mercados|\s)+[:\-–—]?\s*",
                    "",
                    p,
                    flags=re.IGNORECASE,
                ).strip()
                if not paragrafo_eh_ruido_editorial(p, titulo):
                    paragrafos_limpos.append(p)

    if not paragrafos_limpos and desc_bruta:
        t_desc = limpar_caudas_wordpress(limpar_html(desc_bruta))
        frases = [f.strip() for f in re.split(r"(?<=[.!?])\s+", t_desc) if len(f.strip()) >= 35]
        if len(frases) >= 3:
            paragrafos_limpos = [
                frases[0],
                " ".join(frases[1:-1]),
                frases[-1],
            ]
        elif t_desc and len(t_desc) >= 60:
            paragrafos_limpos = [t_desc]

    return paragrafos_limpos


def truncar_frase_limpa(texto: str, max_chars: int) -> str:
    if len(texto) <= max_chars:
        return texto if texto[-1] in ".!?" else texto + "."
    cortado = texto[:max_chars].rsplit(" ", 1)[0].rstrip(",;:-–— ")
    return cortado + "."


def estruturar_noticia_comeco_meio_fim(paragrafos: List[str], titulo: str) -> str:
    if not paragrafos:
        return ""

    if len(paragrafos) == 1:
        frases = [f.strip() for f in re.split(r"(?<=[.!?])\s+", paragrafos[0]) if len(f.strip()) > 25]
        if len(frases) >= 3:
            p_comeco = truncar_frase_limpa(frases[0], 230)
            p_meio = truncar_frase_limpa(frases[1], 230)
            p_fim = truncar_frase_limpa(frases[-1], 220)
            return f"{p_comeco}\n\n{p_meio}\n\n{p_fim}"
        return truncar_frase_limpa(paragrafos[0], 620)

    if len(paragrafos) == 2:
        p_comeco = truncar_frase_limpa(paragrafos[0], 300)
        p_fim = truncar_frase_limpa(paragrafos[1], 300)
        return f"{p_comeco}\n\n{p_fim}"

    p_comeco = truncar_frase_limpa(paragrafos[0], 240)
    candidatos_meio = paragrafos[1:-1]

    def calcular_score_financeiro(paragrafo: str) -> Tuple[int, int]:
        matches = REGEX_INDICADORES_FINANCEIROS.findall(paragrafo)
        bonus_tamanho = 1 if 80 <= len(paragrafo) <= 320 else 0
        return (len(matches) * 10 + bonus_tamanho, -abs(len(paragrafo) - 200))

    melhor_meio = max(candidatos_meio, key=calcular_score_financeiro)
    p_meio = truncar_frase_limpa(melhor_meio, 240)

    p_fim_bruto = paragrafos[-1]
    if p_fim_bruto == melhor_meio and len(paragrafos) > 3:
        p_fim_bruto = paragrafos[-2]
    p_fim = truncar_frase_limpa(p_fim_bruto, 220)

    blocos = [p_comeco]
    if p_meio.lower() != p_comeco.lower():
        blocos.append(p_meio)
    if p_fim.lower() not in (p_comeco.lower(), p_meio.lower()):
        blocos.append(p_fim)

    return "\n\n".join(blocos)


# ==============================================================================
# CATEGORIZAÇÃO E FORMATAÇÃO HTML SEGURA PARA O TELEGRAM
# ==============================================================================
def categorizar_materia(titulo: str, texto: str) -> str:
    for escopo in (titulo, f"{titulo} {texto}"):
        if contem_termo(escopo, ("urgente", "breaking", "plantão", "circuit breaker", "ataque terrorista", "guerra declarada")):
            return "🚨 <b>RADAR URGENTE</b>"
        if contem_termo(escopo, ("dividendo", "dividendos", "provento", "proventos", "jcp", "juros sobre capital", "data com", "bonificação")):
            return "💰 <b>DIVIDENDOS &amp; PROVENTOS</b>"
        if contem_termo(escopo, (
            "lula", "congresso", "stf", "senado", "senador", "câmara", "camara", "deputado", "deputados",
            "motta", "lira", "haddad", "planalto", "reforma tributária", "arcabouço", "orçamento",
            "eleição", "eleições", "candidatura", "partido", "pl", "pt", "governador", "prefeito", "pec",
        )):
            return "🏛️ <b>POLÍTICA &amp; PODER</b>"
        if contem_termo(escopo, (
            "eua", "trump", "biden", "china", "guerra", "rússia", "ucrânia", "oriente médio",
            "israel", "irã", "otan", "taiwan", "bce", "união europeia", "geopolítica", "casa branca", "tarifas",
        )):
            return "🌍 <b>GEOPOLÍTICA &amp; MUNDO</b>"
        if contem_termo(escopo, (
            "energia", "leilão de energia", "leilões de energia", "eletricidade", "setor elétrico",
            "petróleo", "brent", "wti", "opep", "minério", "dalian", "commodities", "commodity",
            "soja", "milho", "safra", "ouro", "gás natural", "combustível", "gasolina",
        )):
            return "🛢️ <b>COMMODITIES &amp; ENERGIA</b>"
        if contem_termo(escopo, (
            "fusão", "aquisição", "m&a", "incorporação", "ipo", "oferta pública",
            "reestruturação", "privatização", "joint venture", "concessão", "recuperação judicial",
        )):
            return "🤝 <b>NEGÓCIOS &amp; EMPRESAS</b>"
        if contem_termo(escopo, (
            "inteligência artificial", "chatgpt", "openai", "nvidia", "semicondutores",
            "chips", "apple", "microsoft", "google", "meta", "big tech", "tsmc",
        )):
            return "🤖 <b>TECNOLOGIA &amp; IA</b>"
        if contem_termo(escopo, (
            "bitcoin", "btc", "ethereum", "cripto", "criptomoeda", "criptomoedas",
            "criptoativos", "solana", "drex", "blockchain", "tokenização",
        )):
            return "🪙 <b>CRIPTOATIVOS</b>"
        if contem_termo(escopo, (
            "dólar", "selic", "copom", "ipca", "inflação", "banco central", "fed",
            "juros", "pib", "câmbio", "boletim focus", "tesouro direto",
        )):
            return "💵 <b>MACROECONOMIA &amp; CÂMBIO</b>"
        if contem_termo(escopo, (
            "lucro", "balanço", "prejuízo", "receita", "ebitda", "petrobras", "vale",
            "itaú", "bradesco", "b3", "bolsa", "ibovespa", "ações", "nyse", "nasdaq", "s&p 500",
        )):
            return "📊 <b>MERCADO &amp; AÇÕES</b>"

    return "⚡️ <b>RADAR DE MERCADO</b>"


def montar_post_html_seguro(categoria_html: str, titulo_limpo: str, resumo_texto: str) -> str:
    titulo_esc = html.escape(titulo_limpo)
    resumo_atual = resumo_texto

    while True:
        resumo_esc = html.escape(resumo_atual)
        post = f"{categoria_html}\n\n<b>{titulo_esc}</b>\n\n{resumo_esc}"
        if len(post) <= TELEGRAM_CAPTION_LIMIT or len(resumo_atual) <= 100:
            return post
        resumo_atual = resumo_atual[:-40].rsplit(" ", 1)[0].rstrip(",;:-") + "."


# ==============================================================================
# 2. ENGINE DE MÍDIA E VÍDEO LOCAL (YT-DLP <= 50MB + LIMPEZA TEMPORÁRIA)
# ==============================================================================
def extrair_og_image_da_pagina(raw_html: str) -> Optional[str]:
    padroes = (
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
    )
    for padrao in padroes:
        m = re.search(padrao, raw_html, re.IGNORECASE)
        if m:
            url = normalizar_url(m.group(1))
            if url:
                return url
    return None


def extrair_candidato_video_html(raw_html: str) -> Optional[str]:
    padroes = (
        r'<meta[^>]+property=["\']og:video(?::secure_url|:url)?["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:video(?::secure_url|:url)?["\']',
        r'<meta[^>]+name=["\']twitter:player:stream["\'][^>]+content=["\']([^"\']+)["\']',
        r'<source[^>]+src=["\']([^"\']+\.(?:mp4|m3u8)(?:\?[^"\']*)?)["\']',
        r'<video[^>]+src=["\']([^"\']+\.(?:mp4|m3u8)(?:\?[^"\']*)?)["\']',
        r'"contentUrl"\s*:\s*"([^"]+\.(?:mp4|m3u8)(?:\?[^"]*)?)"',
        r'<iframe[^>]+src=["\']((?:https?:)?//(?:www\.)?(?:youtube\.com/embed/|youtu\.be/|player\.vimeo\.com/video/|dailymotion\.com/embed/video/)[^"\'?]+)',
    )
    for padrao in padroes:
        for match in re.finditer(padrao, raw_html, re.IGNORECASE):
            raw_url = match.group(1).replace(r"\/", "/")
            url = normalizar_url(raw_url)
            if url:
                return url
    return None


def extrair_midias_do_rss(
    item: XmlElement, desc_bruta: str
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    foto_url: Optional[str] = None
    video_url: Optional[str] = None
    audio_url: Optional[str] = None

    enc = item.find("enclosure")
    if enc is not None and "url" in enc.attrib:
        url = normalizar_url(enc.attrib["url"])
        tipo = enc.attrib.get("type", "").lower()
        if url:
            if "video" in tipo or url.lower().endswith((".mp4", ".mov", ".m3u8")):
                video_url = url
            elif "audio" in tipo or url.lower().endswith((".mp3", ".m4a", ".ogg", ".wav")):
                audio_url = url
            elif "image" in tipo or url.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                foto_url = url

    for tag in ("{http://search.yahoo.com/mrss/}content", "{http://search.yahoo.com/mrss/}thumbnail"):
        for m in item.findall(tag):
            if "url" in m.attrib:
                url = normalizar_url(m.attrib["url"])
                tipo = m.attrib.get("type", "").lower()
                medium = m.attrib.get("medium", "").lower()
                if url:
                    if "video" in tipo or medium == "video" or url.lower().endswith((".mp4", ".m3u8")):
                        video_url = video_url or url
                    elif "audio" in tipo or medium == "audio" or url.lower().endswith(".mp3"):
                        audio_url = audio_url or url
                    elif not foto_url and ("image" in tipo or medium == "image" or url.startswith("http")):
                        foto_url = url

    if not video_url and desc_bruta:
        busca_vid = re.search(r'<(?:video|source)[^>]+src=["\']([^"\']+\.mp4[^"\']*)["\']', desc_bruta, re.IGNORECASE)
        if busca_vid:
            video_url = normalizar_url(busca_vid.group(1))

    if not foto_url and desc_bruta:
        busca_img = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc_bruta, re.IGNORECASE)
        if busca_img:
            foto_url = normalizar_url(busca_img.group(1))

    return foto_url, video_url, audio_url


def _baixar_video_ytdlp_sync(url_alvo: str, referer_url: str, pasta_temp: str) -> Optional[Path]:
    output_template = os.path.join(pasta_temp, "video_%(id)s.%(ext)s")
    ydl_opts: Dict[str, Any] = {
        "format": (
            "best[ext=mp4][filesize<=50M]/"
            "best[ext=mp4][filesize_approx<=50M]/"
            "bestvideo[ext=mp4][filesize<=40M]+bestaudio[ext=m4a][filesize<=10M]/"
            "best[ext=mp4]/best"
        ),
        "merge_output_format": "mp4",
        "outtmpl": output_template,
        "max_filesize": MAX_VIDEO_BYTES,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 15,
        "concurrent_fragment_downloads": 4,
        "http_headers": {**HEADERS, "Referer": referer_url},
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url_alvo, download=True)
            if not info:
                return None
            if "entries" in info:
                entries = [e for e in info["entries"] if e]
                if not entries:
                    return None
                info = entries[0]

            caminho_str = ydl.prepare_filename(info)
            caminho = Path(caminho_str)
            if not caminho.exists():
                caminho_mp4 = caminho.with_suffix(".mp4")
                if caminho_mp4.exists():
                    caminho = caminho_mp4

            if caminho.exists():
                tamanho = caminho.stat().st_size
                if 1024 < tamanho <= MAX_VIDEO_BYTES:
                    return caminho
                logger.warning("Vídeo descartado por tamanho inválido (%d bytes): %s", tamanho, url_alvo)
    except Exception as exc:
        logger.debug("yt-dlp sem vídeo válido para %s: %s", url_alvo, exc)

    return None


# ==============================================================================
# INGESTÃO E SCRAPING ASSÍNCRONO
# ==============================================================================
async def buscar_feed_rss(
    client: httpx.AsyncClient, sem: asyncio.Semaphore, url_feed: str
) -> List[Dict[str, Any]]:
    agora_utc = datetime.now(timezone.utc)
    itens_validos: List[Dict[str, Any]] = []

    async with sem:
        try:
            resp = await client.get(url_feed, timeout=10.0)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except Exception as exc:
            logger.warning("Falha ao processar feed %s: %s", url_feed, exc)
            return []

    for item in root.findall(".//item"):
        link_el = item.find("link")
        title_el = item.find("title")
        desc_el = item.find("description")
        content_el = item.find("{http://purl.org/rss/1.0/modules/content/}encoded")

        link = normalizar_url(link_el.text if link_el is not None else "")
        title = html.unescape(title_el.text.strip()) if title_el is not None and title_el.text else ""
        desc_bruta = (
            desc_el.text
            if desc_el is not None and desc_el.text
            else (content_el.text if content_el is not None and content_el.text else "")
        )

        if not link or not title or len(title) < 25:
            continue

        dt_pub = extrair_data_publicacao(item)
        if not dt_pub:
            continue

        idade_minutos = (agora_utc - dt_pub).total_seconds() / 60.0
        if idade_minutos > MAX_IDADE_MINUTOS or idade_minutos < -10.0:
            continue

        if eh_propaganda_ou_lixo(title, desc_bruta):
            continue

        itens_validos.append({
            "dt_pub": dt_pub,
            "item": item,
            "link": link,
            "title": title,
            "desc_bruta": desc_bruta,
        })

    return itens_validos


async def obter_conteudo_e_midias_artigo(
    client: httpx.AsyncClient, url: str, desc_bruta: str, titulo: str
) -> Tuple[List[str], Optional[str], Optional[str]]:
    foto_og: Optional[str] = None
    video_pag: Optional[str] = None
    raw_html = ""

    try:
        resp = await client.get(url, timeout=10.0)
        if resp.status_code == 200 and len(resp.content) <= MAX_HTML_BYTES:
            raw_html = resp.text
            foto_og = extrair_og_image_da_pagina(raw_html)
            video_pag = extrair_candidato_video_html(raw_html)
    except Exception as exc:
        logger.debug("Download do HTML falhou para %s: %s", url, exc)

    paragrafos = await asyncio.to_thread(
        extrair_paragrafos_trafilatura, raw_html, desc_bruta, titulo
    )
    return paragrafos, foto_og, video_pag


async def carregar_proximas_noticias(
    client: httpx.AsyncClient, limite: int = MAX_NOTICIAS_POR_VEZ
) -> Tuple[List[NoticiaPreparada], List[str], List[str]]:
    links_historico, titulos_historico = carregar_historico()
    set_links: Set[str] = set(links_historico)
    titulos_rodada: List[str] = list(titulos_historico)

    sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    resultados_feeds = await asyncio.gather(
        *(buscar_feed_rss(client, sem, url) for url in FEEDS)
    )

    candidatos = [
        item for sublista in resultados_feeds for item in sublista
        if item["link"] not in set_links and gerar_hash_url(item["link"]) not in set_links
    ]
    candidatos.sort(key=lambda x: x["dt_pub"], reverse=True)

    noticias: List[NoticiaPreparada] = []
    for cand in candidatos:
        title = cand["title"]
        link = cand["link"]
        link_hash = gerar_hash_url(link)
        desc_bruta = cand["desc_bruta"]
        item = cand["item"]

        if link in set_links or link_hash in set_links or eh_mesma_noticia(title, titulos_rodada):
            continue

        foto_rss, video_rss, audio_rss = extrair_midias_do_rss(item, desc_bruta)
        paragrafos, foto_og, video_pag = await obter_conteudo_e_midias_artigo(
            client, link, desc_bruta, title
        )

        resumo_estruturado = estruturar_noticia_comeco_meio_fim(paragrafos, title)
        if not resumo_estruturado or len(resumo_estruturado) < 100:
            continue

        categoria = categorizar_materia(title, resumo_estruturado)

        t_limpo = title.strip()
        if " - " in t_limpo:
            t_limpo, _ = t_limpo.rsplit(" - ", 1)
        if t_limpo and t_limpo[-1] not in ".!?":
            t_limpo += "."

        post_html = montar_post_html_seguro(categoria, t_limpo, resumo_estruturado)

        noticias.append(
            NoticiaPreparada(
                link=link,
                titulo=title,
                texto_html=post_html,
                imagem=foto_rss or foto_og,
                video_candidato_url=video_rss or video_pag,
                audio=audio_rss,
            )
        )
        titulos_rodada.append(title)
        set_links.add(link)
        set_links.add(link_hash)

        if len(noticias) >= limite:
            break

    noticias.reverse()
    return noticias, links_historico, titulos_historico


# ==============================================================================
# CLIENTE TELEGRAM (UPLOAD DE VÍDEO TEMPORÁRIO + FALLBACK LINKPREVIEWOPTIONS)
# ==============================================================================
async def telegram_request(
    client: httpx.AsyncClient,
    url: str,
    json_payload: Optional[Dict[str, Any]] = None,
    data_fields: Optional[Dict[str, Any]] = None,
    files: Optional[Dict[str, Any]] = None,
    max_retries: int = 3,
) -> bool:
    for tentativa in range(1, max_retries + 1):
        try:
            if files:
                resp = await client.post(url, data=data_fields, files=files, timeout=90.0)
            else:
                resp = await client.post(url, json=json_payload, timeout=30.0)

            if resp.status_code == 200:
                return True

            if resp.status_code == 429:
                body = resp.json()
                retry_after = int(body.get("parameters", {}).get("retry_after", 5))
                logger.warning("Rate limit (429) atingido no Telegram. Aguardando %ss...", retry_after)
                await asyncio.sleep(retry_after + 1)
                continue

            logger.warning(
                "Telegram retornou HTTP %s em %s: %s",
                resp.status_code,
                url.rsplit("/", 1)[-1],
                resp.text[:250],
            )
            return False

        except httpx.RequestError as exc:
            logger.warning("Erro de rede na tentativa %d/%d para Telegram: %s", tentativa, max_retries, exc)
            await asyncio.sleep(2 * tentativa)

    return False


async def tentar_enviar_video_ytdlp(
    client: httpx.AsyncClient,
    base_url: str,
    chat_id: str,
    item: NoticiaPreparada,
    botao_sorteado: Dict[str, Any],
) -> bool:
    urls_para_testar: List[str] = []
    if item.video_candidato_url:
        urls_para_testar.append(item.video_candidato_url)
    urls_para_testar.append(item.link)

    with tempfile.TemporaryDirectory(prefix="tg_vid_") as pasta_temp:
        caminho_video: Optional[Path] = None
        for url_alvo in dict.fromkeys(urls_para_testar):
            caminho_video = await asyncio.to_thread(
                _baixar_video_ytdlp_sync, url_alvo, item.link, pasta_temp
            )
            if caminho_video and caminho_video.exists():
                break

        if not caminho_video or not caminho_video.exists():
            return False

        logger.info(
            "Vídeo extraído via yt-dlp (%.2f MB). Enviando via sendVideo...",
            caminho_video.stat().st_size / (1024 * 1024),
        )
        data_fields = {
            "chat_id": chat_id,
            "caption": item.texto_html,
            "parse_mode": "HTML",
            "supports_streaming": "true",
            "reply_markup": json.dumps(botao_sorteado),
        }
        with open(caminho_video, "rb") as f_vid:
            files = {"video": (caminho_video.name, f_vid, "video/mp4")}
            return await telegram_request(
                client, f"{base_url}/sendVideo", data_fields=data_fields, files=files
            )


async def enviar_post(
    client: httpx.AsyncClient, token: str, chat_id: str, item: NoticiaPreparada
) -> bool:
    base_url = f"https://api.telegram.org/bot{token}"
    botao_sorteado = gerar_botao_grupo_aleatorio()

    sucesso_video = await tentar_enviar_video_ytdlp(
        client, base_url, chat_id, item, botao_sorteado
    )
    if sucesso_video:
        return True

    url_preview = item.imagem if item.imagem else item.link
    link_preview_options: Dict[str, Any] = {
        "is_disabled": False,
        "url": url_preview,
        "prefer_large_media": True,
        "show_above_text": True,
    }

    logger.info("Sem vídeo nativo para '%s'. Enviando via sendMessage com LinkPreviewOptions.", item.titulo)
    payload_msg = {
        "chat_id": chat_id,
        "text": item.texto_html,
        "parse_mode": "HTML",
        "link_preview_options": link_preview_options,
        "reply_markup": botao_sorteado,
    }
    return await telegram_request(client, f"{base_url}/sendMessage", json_payload=payload_msg)


# ==============================================================================
# ENTRYPOINT ASSÍNCRONO
# ==============================================================================
async def main() -> None:
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()

    if not token or not chat_id:
        logger.critical(
            "Variáveis de ambiente obrigatórias ausentes: configure TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID nos Secrets do GitHub."
        )
        sys.exit(1)

    limits = httpx.Limits(max_keepalive_connections=10, max_connections=20)
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, limits=limits) as client:
        noticias, links_historico, titulos_historico = await carregar_proximas_noticias(
            client, limite=MAX_NOTICIAS_POR_VEZ
        )

        if not noticias:
            logger.info("Nenhuma notícia nova qualificada neste ciclo.")
            return

        logger.info("Encontradas %d notícias qualificadas para envio.", len(noticias))
        for noticia in noticias:
            sucesso = await enviar_post(client, token, chat_id, noticia)
            if sucesso:
                links_historico.append(noticia.link)
                links_historico.append(gerar_hash_url(noticia.link))
                titulos_historico.append(noticia.titulo)
                salvar_historico(links_historico, titulos_historico)
                logger.info("Notícia publicada com sucesso: %s", noticia.titulo)
                await asyncio.sleep(3.0)
            else:
                logger.error("Falha definitiva ao publicar notícia: %s", noticia.titulo)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Execução interrompida manualmente.")
