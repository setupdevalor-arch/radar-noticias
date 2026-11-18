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
SCORE_MINIMO_CORTE = int(os.environ.get("SCORE_MINIMO_CORTE", "15"))
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
    "cartão de crédito", "cartao de credito", "cartão black", "anuidade zero",
    "limite do cartão", "empréstimo pessoal", "financiamento de veículos",
    "horóscopo", "signo", "loteria", "mega-sena", "bbb", "fofoca", "onde assistir",
    "escalação", "futebol", "novela", "brasileirão", "campeonato brasileiro",
    "rebaixamento", "data fifa", "libertadores", "copa do brasil", "champions league",
    "artilheiro", "goleiro", "estádio", "torcida", "série a", "série b", "35ª rodada",
    "tabela do campeonato", "eliminatórias", "seleção brasileira",
    "esquema de bets", "apostas online", "apostas de quota fixa", "cassino online",
    "jogo do tigrinho", "mandados de busca", "mandado de busca", "busca e apreensão",
    "mandado de prisão", "operação cash out", "operação da pf", "equipes policiais",
    "lavagem de dinheiro", "quadrilha", "assalto", "sequestro", "homicídio",
    "tráfico de drogas", "facção criminosa",
)

STOPWORDS_DEDUP = {
    "aprova", "anuncia", "diz", "após", "apos", "sobre", "pode", "novo", "nova",
    "nesta", "neste", "segundo", "veja", "confira", "contra", "entre", "mais",
    "para", "como", "onde", "quando", "quem", "qual", "está", "esta", "estão",
    "foram", "será", "pagamento", "tem", "faz", "vai", "com", "por", "que", "dos", "das",
    "fecham", "fecha", "sobe", "cai", "alta", "queda", "dias", "pontos",
}

# ==============================================================================
# TAXONOMIA FINANCEIRA OBRIGATÓRIA (PESOS E CORES POR TEMA)
# ==============================================================================
TAXONOMIA_PESOS: Dict[str, Dict[str, Any]] = {
    "PROVENTOS": {
        "label": "🟢 <b>PROVENTOS &amp; DIVIDENDOS</b>",
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
        "label": "🔵 <b>RESULTADOS &amp; BALANÇOS</b>",
        "termos": {
            "balanço": 15,
            "lucro líquido": 20,
            "ebitda": 20,
            "lajida": 18,
            "prejuízo": 16,
            "receita líquida": 18,
            "receita": 10,
            "margem": 12,
            "margem líquida": 16,
            "margem ebitda": 16,
            "roe": 14,
            "alavancagem": 14,
            "guidance": 16,
            "1t": 10, "2t": 10, "3t": 10, "4t": 10,
        },
    },
    "EVENTOS_CORPORATIVOS": {
        "label": "🟣 <b>EVENTOS CORPORATIVOS &amp; M&amp;A</b>",
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
        "label": "🟠 <b>MACROECONOMIA &amp; MERCADOS</b>",
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

REGEX_INICIO_ORFAO = re.compile(
    r"^(?:Segundo ele|Segundo ela|Para ele|Para ela|De acordo com ele|De acordo com ela|"
    r"Ainda segundo ele|Na visão dele|Na avaliação dele|Ele acrescenta|Ela acrescenta|"
    r"Ele afirma|Ela afirma|No mesmo intervalo|Na mesma linha|Outro ponto citado)\b",
    flags=re.IGNORECASE,
)

MARCADORES_DESFECHO = (
    "com isso", "dessa forma", "diante disso", "nesse cenário", "neste cenário",
    "no acumulado", "por fim", "como resultado", "em reação", "após o anúncio",
    "para o mercado", "na avaliação", "a expectativa", "os papéis", "as ações",
    "fechou em", "encerrou em", "operam em", "cotadas a", "recomendação",
    "preço-alvo", "impacto", "perspectiva", "próximos passos", "a decisão",
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
        cat_label = "🔵 <b>RADAR B3 &amp; AÇÕES</b>"

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


def eh_propaganda_ou_lixo(titulo: str, descricao: str, url: str = "") -> bool:
    url_low = url.lower()
    if any(caminho in url_low for caminho in CAMINHOS_URL_BLOQUEADOS):
        return True
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
    txt = re.sub(
        r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
        r"(?:com|br|net|org|io|gov|edu|co|be|me|tv|info)\b/\S*",
        "",
        txt,
        flags=re.IGNORECASE,
    )
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
        "reprodução/", "foto:", "crédito:", "imagem:", "divulgação/", "embeds_referring_euri",
        "source_ve_path", "time_continue=", "watch?v=",
    )
    t_low = txt.lower()
    if any(r in t_low for r in ruidos):
        return True
    if titulo and t_low.startswith(titulo[:30].lower()):
        return True
    if txt.count("[") >= 3 or txt.count("]") >= 3:
        return True
    return False


def extrair_paragrafos_trafilatura(raw_html: str, desc_bruta: str, titulo: str) -> List[str]:
    paragrafos_limpos: List[str] = []

    if raw_html:
        texto_extraido = trafilatura.extract(
            raw_html,
            include_comments=False,
            include_tables=False,
            include_links=False,
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
            paragrafos_limpos = [frases[0], " ".join(frases[1:-1]), frases[-1]]
        elif t_desc and len(t_desc) >= 60:
            paragrafos_limpos = [t_desc]

    return paragrafos_limpos


# ==============================================================================
# NARRATIVA DE 3 ATOS: COMEÇO (LEAD), MEIO (CONTEXTO & DADOS) E FIM (CONCLUSÃO)
# ==============================================================================
def dividir_em_frases_completas(texto: str) -> List[str]:
    texto = " ".join(texto.split()).strip()
    if not texto:
        return []
    partes = re.split(
        r"(?<!\bS\.A)(?<!\bCia)(?<!\bDr)(?<!\bSr)(?<!\bSra)(?<!\bInc)(?<!\bLtd)(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ0-9\"'“‘(])",
        texto,
    )
    frases: List[str] = []
    for p in partes:
        p = p.strip()
        if not p or "[…]" in p or "[...]" in p:
            continue
        if re.search(
            r"\b(?:de|da|do|das|dos|em|na|no|para|por|com|que|e|ou|cerca|visam|foram|está|são)\.?$",
            p,
            flags=re.IGNORECASE,
        ):
            continue
        if p[-1] not in ".!?":
            p += "."
        frases.append(p)
    return frases


def truncar_frase_limpa(texto: str, max_chars: int) -> str:
    """
    Constrói um parágrafo somente com frases 100% completas e com sentido fechado.
    Jamais corta uma frase no meio.
    """
    frases = dividir_em_frases_completas(texto)
    if not frases:
        return ""

    acumulado = ""
    for f in frases:
        candidato = f"{acumulado} {f}".strip() if acumulado else f
        if len(candidato) <= max_chars:
            acumulado = candidato
        else:
            break

    if acumulado:
        return acumulado

    primeira = frases[0]
    if len(primeira) <= max_chars + 35:
        return primeira

    trecho = primeira[:max_chars]
    for sep in ("; ", ", mas ", ", enquanto ", ", quando ", ", o que ", ", segundo ", ", "):
        pos = trecho.rfind(sep)
        if pos >= int(max_chars * 0.65):
            corte = trecho[:pos].rstrip(",;:-–— ")
            if not re.search(
                r"\b(?:de|da|do|das|dos|em|na|no|para|por|com|que|e|ou|cerca|visam|foram|está|são)\b$",
                corte,
                flags=re.IGNORECASE,
            ):
                return corte + "."

    return ""


def pontuar_paragrafo_meio(paragrafo: str) -> int:
    qtd_metricas = len(REGEX_METRICAS_NUMERCIAS.findall(paragrafo))
    qtd_tickers = len(REGEX_TICKER_B3.findall(paragrafo))
    p_low = paragrafo.lower()
    qtd_taxonomia = sum(
        peso
        for cat in TAXONOMIA_PESOS.values()
        for termo, peso in cat["termos"].items()
        if termo in p_low
    )
    return (qtd_metricas * 12) + (qtd_tickers * 18) + qtd_taxonomia


def extrair_comeco_meio_fim_coerentes(paragrafos: List[str]) -> Optional[Tuple[str, str, str]]:
    """
    Garante que toda notícia tenha estrutura lógica completa (Começo, Meio e Fim):
    1. COMEÇO: O fato principal (Lead).
    2. MEIO: O desenvolvimento direto logo após o lead (com os números/motivos, sem saltos desconexos).
    3. FIM: O desfecho real / impacto de mercado (sem apagar a conclusão e sem pegar rodapé aleatório).
    """
    blocos: List[str] = []
    for p in paragrafos:
        bloco = truncar_frase_limpa(p, 250)
        if bloco and len(bloco) >= 50 and bloco not in blocos:
            blocos.append(bloco)

    if len(blocos) < 3:
        todas_frases: List[str] = []
        for p in paragrafos:
            for f in dividir_em_frases_completas(p):
                if len(f) >= 45 and f not in todas_frases:
                    todas_frases.append(f)
        if len(todas_frases) < 3:
            return None
        p_comeco = truncar_frase_limpa(todas_frases[0], 235)
        p_meio = truncar_frase_limpa(todas_frases[1], 245)
        p_fim = truncar_frase_limpa(" ".join(todas_frases[2:4]), 235)
        if p_comeco and p_meio and p_fim:
            return p_comeco, p_meio, p_fim
        return None

    p_comeco = truncar_frase_limpa(blocos[0], 235)

    limite_meio = min(4, len(blocos) - 1)
    melhor_idx_meio = 1
    melhor_score_meio = -999

    for idx in range(1, limite_meio):
        cand = blocos[idx]
        penalidade_orfao = -60 if (idx > 1 and REGEX_INICIO_ORFAO.search(cand)) else 0
        bonus_sequencia = 16 if idx == 1 else (8 if idx == 2 else 0)
        score = bonus_sequencia + pontuar_paragrafo_meio(cand) + penalidade_orfao
        if score > melhor_score_meio:
            melhor_score_meio = score
            melhor_idx_meio = idx

    p_meio = truncar_frase_limpa(blocos[melhor_idx_meio], 245)

    janela_fim = blocos[melhor_idx_meio + 1 : min(melhor_idx_meio + 5, len(blocos))]
    if not janela_fim:
        janela_fim = [blocos[-1]]

    melhor_fim = janela_fim[0]
    melhor_score_fim = -999
    for offset, cand in enumerate(janela_fim):
        penalidade_orfao = -50 if (offset > 0 and REGEX_INICIO_ORFAO.search(cand)) else 0
        c_low = cand.lower()
        bonus_desfecho = 22 if any(m in c_low for m in MARCADORES_DESFECHO) else 0
        bonus_dados = pontuar_paragrafo_meio(cand) // 2
        bonus_proximidade = 12 - (offset * 3)
        score = bonus_desfecho + bonus_dados + bonus_proximidade + penalidade_orfao
        if score > melhor_score_fim:
            melhor_score_fim = score
            melhor_fim = cand

    p_fim = truncar_frase_limpa(melhor_fim, 235)

    if not (p_comeco and p_meio and p_fim):
        return None
    if len({p_comeco.lower(), p_meio.lower(), p_fim.lower()}) < 3:
        return None

    return p_comeco, p_meio, p_fim


def montar_mensagem_investidor(
    titulo: str, paragrafos: List[str], analise: AnaliseHeuristica
) -> Optional[str]:
    t_limpo = limpar_html(titulo.strip())
    if " - " in t_limpo:
        t_limpo, _ = t_limpo.rsplit(" - ", 1)
    if t_limpo and t_limpo[-1] not in ".!?":
        t_limpo += "."

    narrativa = extrair_comeco_meio_fim_coerentes(paragrafos)
    if not narrativa:
        return None

    p_comeco, p_meio, p_fim = narrativa

    titulo_html = formatar_tickers_no_texto(html.escape(t_limpo))
    comeco_html = formatar_tickers_no_texto(html.escape(p_comeco))
    meio_html = formatar_tickers_no_texto(html.escape(p_meio))
    fim_html = formatar_tickers_no_texto(html.escape(p_fim))

    if analise.tickers:
        pills_tickers = " ".join(f"<code>#{t}</code>" for t in analise.tickers[:3])
        cabecalho_tema = f"<blockquote>{analise.categoria_label}  ·  {pills_tickers}</blockquote>"
    else:
        cabecalho_tema = f"<blockquote>{analise.categoria_label}</blockquote>"

    post = f"{cabecalho_tema}\n\n<b>{titulo_html}</b>\n\n{comeco_html}\n\n{meio_html}\n\n{fim_html}"

    if len(post) > TELEGRAM_CAPTION_LIMIT:
        p_comeco_curto = truncar_frase_limpa(p_comeco, 195) or p_comeco
        p_meio_curto = truncar_frase_limpa(p_meio, 205) or p_meio
        p_fim_curto = truncar_frase_limpa(p_fim, 195) or p_fim
        comeco_html = formatar_tickers_no_texto(html.escape(p_comeco_curto))
        meio_html = formatar_tickers_no_texto(html.escape(p_meio_curto))
        fim_html = formatar_tickers_no_texto(html.escape(p_fim_curto))
        post = f"{cabecalho_tema}\n\n<b>{titulo_html}</b>\n\n{comeco_html}\n\n{meio_html}\n\n{fim_html}"

    if len(post) > TELEGRAM_CAPTION_LIMIT:
        return None

    return post


# ==============================================================================
# ENGINE DE MÍDIA E VÍDEO LOCAL (YT-DLP <= 50MB + OS.REMOVE)
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
    padroes_diretos = (
        r'<meta[^>]+property=["\']og:video(?::secure_url|:url)?["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:video(?::secure_url|:url)?["\']',
        r'<meta[^>]+name=["\']twitter:player:stream["\'][^>]+content=["\']([^"\']+)["\']',
        r'<source[^>]+src=["\']([^"\']+\.(?:mp4|m3u8)(?:\?[^"\']*)?)["\']',
        r'<video[^>]+src=["\']([^"\']+\.(?:mp4|m3u8)(?:\?[^"\']*)?)["\']',
        r'"contentUrl"\s*:\s*"([^"]+\.(?:mp4|m3u8)(?:\?[^"]*)?)"',
    )
    for padrao in padroes_diretos:
        for match in re.finditer(padrao, raw_html, re.IGNORECASE):
            raw_url = match.group(1).replace(r"\/", "/")
            url = normalizar_url(raw_url)
            if url:
                return url

    m_yt = REGEX_YOUTUBE_ID.search(raw_html)
    if m_yt:
        return f"https://www.youtube.com/watch?v={m_yt.group(1)}"

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
        else:
            m_yt = REGEX_YOUTUBE_ID.search(desc_bruta)
            if m_yt:
                video_url = f"https://www.youtube.com/watch?v={m_yt.group(1)}"

    if not foto_url and desc_bruta:
        busca_img = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc_bruta, re.IGNORECASE)
        if busca_img:
            foto_url = normalizar_url(busca_img.group(1))

    return foto_url, video_url, audio_url


def _baixar_video_ytdlp_sync(url_alvo: str, referer_url: str, pasta_temp: str) -> Optional[Path]:
    url_limpa = normalizar_url(url_alvo) or url_alvo
    output_template = os.path.join(pasta_temp, "video_%(id)s.%(ext)s")

    ydl_opts: Dict[str, Any] = {
        "format": (
            "best[ext=mp4][height<=720][filesize<=48M]/"
            "best[ext=mp4][height<=720][filesize_approx<=48M]/"
            "bestvideo[ext=mp4][height<=720][filesize<=40M]+bestaudio[ext=m4a][filesize<=8M]/"
            "best[ext=mp4][filesize<=48M]/"
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
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "ios", "web"],
            }
        },
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url_limpa, download=True)
            if not info:
                return None
            if "entries" in info:
                entries = [e for e in info["entries"] if e]
                if not entries:
                    return None
                info = entries[0]

            if info.get("is_live"):
                logger.info("Vídeo ignorado por ser transmissão ao vivo (live): %s", url_limpa)
                return None

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
                os.remove(caminho)
    except Exception as exc:
        logger.debug("yt-dlp não baixou vídeo de %s: %s", url_limpa, exc)

    return None


# ==============================================================================
# INGESTÃO, FILTRAGEM POR SCORE E SCRAPING ASSÍNCRONO
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

        if eh_propaganda_ou_lixo(title, desc_bruta, link):
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
    client: httpx.AsyncClient, sem: asyncio.Semaphore, url: str, desc_bruta: str, titulo: str
) -> Tuple[List[str], Optional[str], Optional[str]]:
    foto_og: Optional[str] = None
    video_pag: Optional[str] = None
    raw_html = ""

    async with sem:
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

    candidatos_brutos = [
        item for sublista in resultados_feeds for item in sublista
        if item["link"] not in set_links and gerar_hash_url(item["link"]) not in set_links
    ]

    candidatos_brutos.sort(key=lambda x: x["dt_pub"], reverse=True)

    noticias_aprovadas: List[NoticiaPreparada] = []
    for cand in candidatos_brutos[:25]:
        title = cand["title"]
        link = cand["link"]
        link_hash = gerar_hash_url(link)
        desc_bruta = cand["desc_bruta"]
        item = cand["item"]

        if link in set_links or link_hash in set_links or eh_mesma_noticia(title, titulos_rodada):
            continue

        foto_rss, video_rss, audio_rss = extrair_midias_do_rss(item, desc_bruta)
        paragrafos, foto_og, video_pag = await obter_conteudo_e_midias_artigo(
            client, sem, link, desc_bruta, title
        )

        if not paragrafos:
            continue

        if eh_propaganda_ou_lixo(title, " ".join(paragrafos), link):
            continue

        analise = avaliar_impacto_financeiro(title, paragrafos)
        if analise.score_total < SCORE_MINIMO_CORTE:
            logger.info(
                "Descartada por baixo impacto financeiro (Score %d < %d): %s",
                analise.score_total,
                SCORE_MINIMO_CORTE,
                title,
            )
            continue

        post_html = montar_mensagem_investidor(title, paragrafos, analise)
        if not post_html:
            logger.info("Descartada por não possuir narrativa completa (Começo, Meio e Fim): %s", title)
            continue

        logger.info(
            "Aprovada [Score %d | %s | Tickers: %s]: %s",
            analise.score_total,
            analise.categoria_chave,
            ",".join(analise.tickers) or "N/A",
            title,
        )

        noticias_aprovadas.append(
            NoticiaPreparada(
                link=link,
                titulo=title,
                texto_html=post_html,
                score=analise.score_total,
                dt_pub=cand["dt_pub"],
                imagem=foto_rss or foto_og,
                video_candidato_url=video_rss or video_pag,
                audio=audio_rss,
            )
        )
        titulos_rodada.append(title)
        set_links.add(link)
        set_links.add(link_hash)

    noticias_aprovadas.sort(key=lambda n: (n.score, n.dt_pub), reverse=True)
    selecionadas = noticias_aprovadas[:limite]
    selecionadas.sort(key=lambda n: n.dt_pub)

    return selecionadas, links_historico, titulos_historico


# ==============================================================================
# CLIENTE TELEGRAM (VÍDEO NATIVO -> FOTO NATIVA -> TEXTO SEM LINK PREVIEW)
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
        try:
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
        finally:
            if caminho_video and caminho_video.exists():
                try:
                    os.remove(caminho_video)
                except OSError:
                    pass


async def tentar_enviar_foto_nativa(
    client: httpx.AsyncClient,
    base_url: str,
    chat_id: str,
    item: NoticiaPreparada,
    botao_sorteado: Dict[str, Any],
) -> bool:
    if not item.imagem:
        return False

    url_photo = f"{base_url}/sendPhoto"
    payload_img = {
        "chat_id": chat_id,
        "photo": item.imagem,
        "caption": item.texto_html,
        "parse_mode": "HTML",
        "reply_markup": botao_sorteado,
    }
    if await telegram_request(client, url_photo, json_payload=payload_img):
        return True

    try:
        headers_img = {**HEADERS, "Referer": item.link}
        resp = await client.get(item.imagem, headers=headers_img, timeout=12.0)
        if resp.status_code == 200 and 500 < len(resp.content) <= MAX_HTML_BYTES:
            content_type = resp.headers.get("Content-Type", "image/jpeg")
            if "html" not in content_type.lower():
                data_fields = {
                    "chat_id": chat_id,
                    "caption": item.texto_html,
                    "parse_mode": "HTML",
                    "reply_markup": json.dumps(botao_sorteado),
                }
                files = {"photo": ("cover.jpg", resp.content, content_type)}
                return await telegram_request(client, url_photo, data_fields=data_fields, files=files)
    except Exception as exc:
        logger.debug("Falha no upload binário da imagem %s: %s", item.imagem, exc)

    return False


async def enviar_post(
    client: httpx.AsyncClient, token: str, chat_id: str, item: NoticiaPreparada
) -> bool:
    base_url = f"https://api.telegram.org/bot{token}"
    botao_sorteado = gerar_botao_grupo_aleatorio()

    if await tentar_enviar_video_ytdlp(client, base_url, chat_id, item, botao_sorteado):
        return True

    if await tentar_enviar_foto_nativa(client, base_url, chat_id, item, botao_sorteado):
        return True

    logger.info("Enviando via sendMessage (texto limpo) para: %s", item.titulo)
    payload_msg = {
        "chat_id": chat_id,
        "text": item.texto_html,
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
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
            logger.info("Nenhuma notícia atingiu o score mínimo de impacto financeiro neste ciclo.")
            return

        logger.info("Encontradas %d notícias de alto impacto para publicação.", len(noticias))
        for noticia in noticias:
            sucesso = await enviar_post(client, token, chat_id, noticia)
            if sucesso:
                links_historico.append(noticia.link)
                links_historico.append(gerar_hash_url(noticia.link))
                titulos_historico.append(noticia.titulo)
                salvar_historico(links_historico, titulos_historico)
                logger.info("Notícia publicada [Score %d]: %s", noticia.score, noticia.titulo)
                await asyncio.sleep(3.0)
            else:
                logger.error("Falha definitiva ao publicar notícia: %s", noticia.titulo)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Execução interrompida manualmente.")
