import asyncio
import email.utils
import hashlib
import html
import json
import logging
import os
import re
import sys
import tempfile
import unicodedata
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
MAX_IDADE_MINUTOS = int(os.environ.get("MAX_IDADE_MINUTOS", "240"))  # 4 horas
SCORE_MINIMO_CORTE = int(os.environ.get("SCORE_MINIMO_CORTE", "18"))  # Foco estrito: Ações B3, FIIs e Renda Fixa
MAX_CONCURRENT_REQUESTS = 6

MAX_HTML_BYTES = 5 * 1024 * 1024          # 5 MB para páginas HTML e fotos
MIN_IMAGE_BYTES = 8 * 1024                # 8 KB mínimos (rejeita pixels 1x1, ícones e logos minúsculos)
MAX_VIDEO_BYTES = 50 * 1024 * 1024        # 50 MB (Limite da Telegram Bot API)
TELEGRAM_CAPTION_LIMIT = 1024             # Limite oficial de caption em sendPhoto/sendVideo
ALVO_MAX_CARACTERES_POST = 1520           # Teto para manter matérias grandes em ~1300-1400 caracteres

TZ_BRASILIA = timezone(timedelta(hours=-3))

# CTA único, curto, fixo e persuasivo (focado em "Chat" aberto, sem parecer grupo pago/venda)
TEXTO_BOTAO_CHAT = "💬 Participe do Chat de Investidores"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}

HEADERS_TELEGRAM_PREVIEW = {
    "User-Agent": "TelegramBot (like TwitterBot)",
    "Accept": "image/jpeg,image/png,*/*;q=0.5",
}

# Feeds EXCLUSIVOS de investimentos: Ações B3, FIIs e Renda Fixa (sem política geral, cripto ou variedades)
FEEDS = [
    "https://www.infomoney.com.br/mercados/feed/",
    "https://www.infomoney.com.br/onde-investir/feed/",
    "https://www.moneytimes.com.br/feed/",
    "https://www.seudinheiro.com/feed/",
    "https://www.suno.com.br/noticias/feed/",
    "https://einvestidor.estadao.com.br/feed/",
    "https://exame.com/invest/feed/",
    "https://braziljournal.com/feed",
    "https://valorinveste.globo.com/rss/valorinveste",
    "https://fiis.com.br/feed/",
]

CAMINHOS_URL_BLOQUEADOS = (
    "/esporte/", "/esportes/", "/futebol/", "/entretenimento/", "/pop/",
    "/estilo/", "/viagem/", "/gastronomia/", "/astrologia/", "/loterias/",
    "/curiosidades/", "/tv/", "/famosos/", "/cultura/", "/politica/",
    "/mundo/", "/policia/", "/apostas/", "/casual/", "/ciencia/", "/blog-",
    "/cursos/", "/marketing/", "/carros/", "/autoesporte/", "/turismo/",
    "/future-of-money/", "/cripto/", "/criptomoedas/", "/carreira/",
    "/tecnologia/", "/inteligencia-artificial/",
)

# 1. Termos de propaganda explícita e venda de cursos (verificados no título e início do texto)
BLOQUEADOS_PROPAGANDA_CORRETORA = (
    "renda fixa hoje:", "dentro da plataforma da xp", "plataforma da xp oferece",
    "lcas na xp", "lcis na xp", "cdbs na xp", "confira as taxas de cdbs, lcis e lcas",
    "conteúdo patrocinado", "branded content", "especial publicitário",
    "curso gratuito", "inscrições abertas", "inscricoes abertas",
    "webinar", "mentoria", "baixe o e-book", "treinamento gratuito",
    "evento online", "evento gratuito", "cupom de desconto",
    "turbinam benefícios", "turbina benefícios", "pontos e milhas", "acumule pontos",
    "cartão de criptomoedas", "encerra seu cartão", "cartão black", "anuidade grátis",
)

# 2. Assuntos fora do foco estrito (Ações B3, FIIs e Renda Fixa) — verificados APENAS no TÍTULO
BLOQUEADOS_APENAS_NO_TITULO = (
    # Startups estrangeiras / IA / Cripto / Big Techs fora da B3
    "dona do claude", "anthropic", "openai", "chatgpt", "spacex", "elon musk",
    "neuralink", "tiktok", "bytedance", "sam altman", "bitcoin", "ethereum",
    "criptomoeda", "criptomoedas", "solana", "dogecoin", "binance",
    # Política partidária / Assistencialismo / Concursos / Loterias
    "lula", "bolsonaro", "stf", "eleições", "eleição", "pesquisa eleitoral",
    "calendário do inss", "meu inss", "benefício do inss", "aposentadoria do inss",
    "pis/pasep", "abono salarial", "bolsa família", "pé-de-meia", "auxílio gás",
    "saque-aniversário", "fgts", "cnh social", "concurso público", "edital publicado",
    "quem pode fazer mba", "horóscopo", "mega-sena", "lotofácil", "loteria",
    # Esportes / Entretenimento / Jogos / Policial / Tempo real
    "fofoca", "onde assistir", "escalação", "futebol", "brasileirão",
    "campeonato brasileiro", "data fifa", "libertadores", "copa do brasil",
    "champions league", "eliminatórias", "seleção brasileira",
    "gta 6", "playstation", "xbox", "canetas emagrecedoras", "cabelo cair",
    "melhores produtos de", "quadro icônico", "obra de arte",
    "jogo do tigrinho", "cassino online", "operação cash out",
    "assalto", "sequestro", "homicídio", "tráfico de drogas", "facção criminosa",
    "lei da dosimetria", "ao vivo:", "tempo real:", "minuto a minuto",
)

# Apenas palavras gramaticais/genéricas (NUNCA incluir ativos/temas como ibovespa, fiis, petrobras, tesouro)
STOPWORDS_DEDUP = {
    "aprova", "anuncia", "diz", "apos", "sobre", "pode", "novo", "nova", "novos", "novas",
    "nesta", "neste", "segundo", "veja", "confira", "contra", "entre", "mais", "menos",
    "para", "como", "onde", "quando", "quem", "qual", "esta", "estao", "esse", "essa",
    "foram", "sera", "serao", "tem", "faz", "vai", "vao", "com", "por", "que", "dos", "das",
    "uns", "uma", "seu", "sua", "seus", "suas", "pelo", "pela", "pelos", "pelas", "nos", "nas",
    "fecham", "fecha", "fechou", "abre", "abriu", "sobe", "sobem", "cai", "caem", "recua",
    "avanca", "alta", "queda", "dias", "dia", "hoje", "semana", "mes", "ano", "anos",
    "pontos", "radar", "pagamento", "mercado", "brasil", "brasileiro", "brasileira",
    "quarta", "quinta", "sexta", "segunda", "terca", "feira", "manha", "tarde", "noite",
}

# ==============================================================================
# ÂNCORAS OBRIGATÓRIAS DOS 3 PILARES: APENAS AÇÕES B3, FIIs E RENDA FIXA
# (Matéria sem nenhuma âncora desta lista no Título ou 2 primeiros parágrafos é DESCARTADA!)
# ==============================================================================
ANCORAS_3_PILARES = (
    # PILAR 1: AÇÕES DA B3 & EMPRESAS LISTADAS
    "ibovespa", "b3", "bolsa brasileira", "bolsa de valores", "ações", "ação",
    "dividendos", "dividendo", "jcp", "juros sobre capital próprio", "proventos",
    "data-com", "data com", "data-ex", "dividend yield", "payout", "bonificação",
    "fato relevante", "recompra de ações", "programa de recompra", "follow-on", "opa",
    "carteira recomendada", "preço-alvo", "balanço", "lucro líquido", "ebitda", "lajida",
    "petrobras", "vale", "itaú", "itau", "bradesco", "banco do brasil",
    "santander", "btg", "itaúsa", "itausa", "sabesp", "eletrobras", "taesa", "cemig",
    "copel", "engie", "equatorial", "cpfl", "auren", "alupar", "neoenergia", "isa energia",
    "sanepar", "copasa", "bb seguridade", "caixa seguridade", "porto seguro", "irb",
    "vivo", "telefônica", "tim", "weg", "klabin", "suzano", "gerdau", "csn", "usiminas",
    "prio", "brava", "vibra", "ultrapar", "raízen", "ambev", "jbs", "marfrig", "brf",
    "minerva", "localiza", "movida", "azul", "gol", "embraer", "cyrela", "mrv", "eztec",
    "cury", "direcional", "multiplan", "iguatemi", "allos", "totvs", "magazine luiza",
    "casas bahia", "lojas renner", "c&a", "guararapes", "raia drogasil", "hapvida",
    "rede d'or", "fleury", "hypera", "cosan", "rumo", "ccr", "motiva", "ecorodovias",
    "santos brasil", "nubank", "inter", "stone", "pagbank", "mercado livre", "assai", "assaí",
    "carrefour", "smart fit", "marcopolo", "mahle", "metal leve", "slc agrícola", "dexco",
    "b3sa3", "petr4", "petr3", "vale3", "itub4", "bbas3", "bbdc4", "itsa4", "bpac11",
    "wege3", "sbsp3", "elet3", "taee11", "cmig4", "cple3", "egie3", "eqtl3", "sapr11",
    "bbse3", "cxse3", "vivt3", "tims3", "klbn11", "suzb3", "ggbr4", "prio3", "rent3",
    # PILAR 2: FUNDOS IMOBILIÁRIOS (FIIs) & FIAGROS
    "fundo imobiliário", "fundos imobiliários", "fii", "fiis", "ifix", "fiagro", "fiagros",
    "fii de tijolo", "fiis de tijolo", "fii de papel", "fiis de papel", "galpões logísticos",
    "galpão logístico", "lajes corporativas", "vacância", "p/vp", "rendimento por cota",
    "cotistas", "emissão de cotas", "subscrição de cotas", "cap rate", "valor patrimonial",
    "mxrf11", "hglg11", "xpml11", "knri11", "kncr11", "knip11", "visc11", "brco11",
    "btlg11", "hgru11", "trxf11", "cpts11", "irdm11", "vghf11", "knsc11", "hgbs11",
    "vilg11", "lvbi11", "pvbi11", "hgre11", "jsre11", "bcff11", "rztr11", "knca11",
    "xplg11", "alzr11", "mall11", "hsml11", "rbrr11", "rbrf11", "vgir11", "ggrc11",
    # PILAR 3: RENDA FIXA & TESOURO DIRETO (+ JUROS QUE PRECIFICAM RENDA FIXA)
    "renda fixa", "tesouro direto", "tesouro selic", "tesouro ipca", "tesouro ipca+",
    "tesouro prefixado", "tesouro renda+", "tesouro educa+", "ntn-b", "ltn", "lft",
    "cdb", "cdbs", "lci", "lca", "lcis", "lcas", "cri", "cra", "cris", "cras",
    "debênture", "debêntures", "debêntures incentivadas", "cdi", "% do cdi",
    "marcação a mercado", "títulos públicos", "crédito privado", "fidc", "poupança",
    "selic", "copom", "di futuro", "curva de juros", "juros futuros",
)

# ==============================================================================
# TAXONOMIA ESTRITA NOS 3 PILARES (DIVIDENDOS, FIIs, RENDA FIXA E AÇÕES B3)
# ==============================================================================
TAXONOMIA_PESOS: Dict[str, Dict[str, Any]] = {
    "PROVENTOS": {
        "label": "🟢 <b>DIVIDENDOS</b>",
        "termos": {
            "dividendos": 22,
            "dividendo": 22,
            "jcp": 24,
            "juros sobre capital próprio": 24,
            "juros sobre o capital próprio": 24,
            "rendimentos": 16,
            "rendimento por cota": 24,
            "por cota": 18,
            "data-com": 24,
            "data com": 22,
            "data-ex": 20,
            "proventos": 20,
            "dividend yield": 20,
            "payout": 18,
            "bonificação": 16,
        },
    },
    "FUNDOS_IMOBILIARIOS": {
        "label": "🏢 <b>FUNDOS IMOBILIÁRIOS</b>",
        "termos": {
            "fundo imobiliário": 24,
            "fundos imobiliários": 24,
            "fiis": 24,
            "fii": 22,
            "ifix": 24,
            "fiagro": 22,
            "fiagros": 22,
            "p/vp": 20,
            "vacância": 18,
            "cap rate": 18,
            "emissão de cotas": 20,
            "subscrição": 16,
            "galpão logístico": 18,
            "galpões logísticos": 18,
            "lajes corporativas": 18,
            "fii de tijolo": 24,
            "fiis de tijolo": 24,
            "fii de papel": 24,
            "fiis de papel": 24,
            "cotistas": 16,
            "valor patrimonial": 16,
        },
    },
    "RENDA_FIXA": {
        "label": "🏦 <b>RENDA FIXA</b>",
        "termos": {
            "renda fixa": 24,
            "tesouro direto": 26,
            "tesouro ipca+": 28,
            "tesouro ipca": 26,
            "tesouro selic": 26,
            "tesouro prefixado": 26,
            "tesouro renda+": 24,
            "tesouro educa+": 22,
            "ntn-b": 22,
            "ipca+": 24,
            "ipca +": 24,
            "% do cdi": 24,
            "do cdi": 18,
            "cdi": 18,
            "cdb": 22,
            "cdbs": 22,
            "lci": 22,
            "lca": 22,
            "lcis": 22,
            "lcas": 22,
            "cri": 18,
            "cra": 18,
            "debêntures": 22,
            "debênture": 20,
            "debêntures incentivadas": 24,
            "crédito privado": 20,
            "títulos públicos": 22,
            "marcação a mercado": 24,
            "isento de ir": 18,
            "isenção de imposto": 18,
            "selic": 18,
            "copom": 18,
            "curva de juros": 18,
            "juros futuros": 18,
            "di futuro": 18,
        },
    },
    "ACOES_B3": {
        "label": "📊 <b>AÇÕES B3</b>",
        "termos": {
            "ibovespa": 20,
            "b3": 18,
            "bolsa brasileira": 18,
            "ações": 16,
            "ação": 14,
            "balanço": 18,
            "lucro líquido": 22,
            "lucro": 15,
            "ebitda": 22,
            "lajida": 20,
            "prejuízo": 16,
            "receita líquida": 20,
            "margem líquida": 18,
            "margem ebitda": 18,
            "roe": 16,
            "roic": 16,
            "alavancagem": 15,
            "dívida líquida": 16,
            "geração de caixa": 16,
            "guidance": 18,
            "fato relevante": 24,
            "fusão": 16,
            "aquisição": 16,
            "recompra de ações": 22,
            "programa de recompra": 20,
            "opa": 20,
            "follow-on": 20,
            "recuperação judicial": 18,
            "privatização": 16,
            "preço-alvo": 18,
            "recomendação de compra": 18,
            "carteira recomendada": 20,
            "1t2": 14, "2t2": 14, "3t2": 14, "4t2": 14,
        },
    },
}

REGEX_TICKER_B3 = re.compile(r"\b([A-Z]{4}(?:3|4|5|6|11))\b")

REGEX_METRICAS_NUMERICAS = re.compile(
    r"("
    r"[+-]?\d+(?:[.,]\d+)?\s*%"
    r"|(?:R\$|US\$|€|£)\s*\d+(?:[.,]\d+)?"
    r"|\b\d+(?:[.,]\d+)?\s*(?:milhões|bilhões|trilhões|mil|bi|tri|pontos-base|bps|pontos)\b"
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

# Remove legendas de fotos e créditos de agências infiltrados no início do texto (ex: Getty Images, NurPhoto, Reuters)
REGEX_CREDITO_FOTO_PARENTESES = re.compile(
    r"[^.!?]*\([^)]*(?:Getty Images|NurPhoto|Reuters|Bloomberg|AFP|AP Photo|Shutterstock|"
    r"Freepik|Unsplash|Estadão Conteúdo|Agência Brasil|Agência Estado|Foto:|Imagem:|"
    r"Divulgação|Reprodução|Marcello Casal|Abril)[^)]*\)\.?\s*",
    flags=re.IGNORECASE,
)

MARCADORES_DESFECHO = (
    "com isso", "dessa forma", "diante disso", "nesse cenário", "neste cenário",
    "no acumulado", "por fim", "como resultado", "em reação", "após o anúncio",
    "para o mercado", "na avaliação", "a expectativa", "os papéis", "as ações",
    "as cotas", "o fundo", "os cotistas", "para o investidor",
    "fechou em", "encerrou em", "operam em", "cotadas a", "recomendação",
    "preço-alvo", "impacto", "perspectiva", "próximos passos", "a decisão",
    "no ano", "nos últimos 12 meses", "segundo analistas", "relatório",
)

TERMOS_URL_IMAGEM_RUIM = (
    "/logo", "logo-", "logo_", "avatar", "gravatar", "favicon", "pixel", "spacer",
    "placeholder", "default-image", "blank.", "1x1", ".svg", ".gif", "badge",
    "/author/", "/perfil/", "/ads/", "banner-app",
)


@dataclass
class AnaliseHeuristica:
    score_total: int
    categoria_chave: str
    categoria_label: str
    tickers: List[str] = field(default_factory=list)
    termos_detectados: List[str] = field(default_factory=list)


@dataclass
class ImagemVerificada:
    url: str
    pode_usar_preview_longo: bool
    conteudo_bytes: Optional[bytes] = None
    content_type: str = "image/jpeg"


@dataclass
class NoticiaPreparada:
    link: str
    titulo: str
    lead_limpo: str
    texto_html: str
    caption_curta_html: str
    score: int
    dt_pub: datetime
    imagem: Optional[str] = None
    imagem_verificada: Optional[ImagemVerificada] = None
    video_candidato_url: Optional[str] = None
    audio: Optional[str] = None


# ==============================================================================
# SELETOR DE TAG CURTA E PRECISA (APENAS AÇÕES B3, FIIs, DIVIDENDOS E RENDA FIXA)
# ==============================================================================
def _contem_termo_exato(texto_low: str, termos: Tuple[str, ...]) -> bool:
    return any(re.search(r"(?<!\w)" + re.escape(k) + r"(?!\w)", texto_low) for k in termos)


def definir_tag_curta_e_precisa(
    titulo: str, lead: str, categoria_dominante: str, tickers: List[str], tickers_fii: List[str]
) -> str:
    t_low = titulo.lower()

    if _contem_termo_exato(t_low, ("dividendo", "dividendos", "jcp", "juros sobre capital", "proventos", "data-com", "data com", "rendimentos", "rendimento por cota")):
        return "🟢 <b>DIVIDENDOS</b>"
    if tickers_fii or _contem_termo_exato(t_low, ("fii", "fiis", "fundo imobiliário", "fundos imobiliários", "ifix", "fiagro", "fiagros")):
        return "🏢 <b>FUNDOS IMOBILIÁRIOS</b>"
    if _contem_termo_exato(t_low, ("tesouro direto", "tesouro ipca", "tesouro ipca+", "tesouro selic", "tesouro prefixado", "tesouro renda", "renda fixa", "cdb", "cdbs", "lci", "lcis", "lca", "lcas", "debênture", "debêntures", "cri", "cris", "cra", "cras", "cdi", "selic", "copom", "curva de juros", "juros futuros", "títulos públicos", "marcação a mercado")):
        return "🏦 <b>RENDA FIXA</b>"
    if _contem_termo_exato(t_low, ("balanço", "lucro", "lucra", "ebitda", "prejuízo", "receita", "resultado", "1t25", "2t25", "3t25", "4t25", "1t26", "2t26", "3t26", "4t26", "1t27", "2t27", "3t27", "4t27")):
        return "🔵 <b>BALANÇOS B3</b>"
    if _contem_termo_exato(t_low, ("ibovespa", "bolsa", "b3", "pregão")):
        return "📊 <b>BOLSA &amp; AÇÕES</b>"

    return TAXONOMIA_PESOS.get(categoria_dominante, TAXONOMIA_PESOS["ACOES_B3"])["label"]


# ==============================================================================
# MOTOR HEURÍSTICO ESTRITO NOS 3 PILARES: AÇÕES B3, FIIs E RENDA FIXA
# ==============================================================================
def extrair_tickers_b3(texto: str) -> List[str]:
    encontrados = REGEX_TICKER_B3.findall(texto)
    falsos_positivos = {"HTTP3", "HTML4", "IPV4", "UTF8", "MP4", "FIFA3", "COPA3", "G11", "BR11"}
    return [t for t in dict.fromkeys(encontrados) if t not in falsos_positivos]


def formatar_tickers_no_texto(texto_html_escapado: str) -> str:
    return REGEX_TICKER_B3.sub(r"<b>#\1</b>", texto_html_escapado)


def avaliar_impacto_financeiro(titulo: str, paragrafos: List[str]) -> AnaliseHeuristica:
    """
    Filtro Estrito nos 3 Pilares: Ações (B3), Fundos Imobiliários (FIIs) e Renda Fixa.
    - Exige âncora direta dos 3 pilares no Título ou nos 2 primeiros parágrafos.
    - Exige pontuação na taxonomia exclusiva dos 3 pilares (Dividendos, FIIs, Renda Fixa ou Ações B3).
    - Sem penalidade negativa por ausência de números regex (evita bloquear boas matérias de análise).
    """
    if not paragrafos:
        return AnaliseHeuristica(score_total=0, categoria_chave="DESCARTADA", categoria_label="")

    lead = paragrafos[0]
    dois_primeiros = " ".join(paragrafos[:2])
    corpo_texto = " ".join(paragrafos)
    texto_completo = f"{titulo} {corpo_texto}"

    titulo_low = re.sub(r"\breceita federal\b", "órgão tributário", titulo.lower())
    lead_low = re.sub(r"\breceita federal\b", "órgão tributário", lead.lower())
    foco_inicial_low = re.sub(r"\breceita federal\b", "órgão tributário", f"{titulo} {dois_primeiros}".lower())
    corpo_low = re.sub(r"\breceita federal\b", "órgão tributário", corpo_texto.lower())

    tickers = extrair_tickers_b3(texto_completo)
    tickers_inicio = extrair_tickers_b3(f"{titulo} {dois_primeiros}")

    tem_ancora_3_pilares = (
        bool(tickers_inicio)
        or any(
            re.search(r"(?<!\w)" + re.escape(a) + r"(?!\w)", foco_inicial_low)
            for a in ANCORAS_3_PILARES
        )
    )

    if not tem_ancora_3_pilares:
        return AnaliseHeuristica(score_total=0, categoria_chave="DESCARTADA", categoria_label="")

    pontos_por_categoria: Dict[str, int] = {cat: 0 for cat in TAXONOMIA_PESOS}
    termos_encontrados: List[str] = []

    for cat_key, dados_cat in TAXONOMIA_PESOS.items():
        for termo, peso in dados_cat["termos"].items():
            padrao = r"(?<!\w)" + re.escape(termo) + r"(?!\w)"
            no_titulo = bool(re.search(padrao, titulo_low))
            no_lead = bool(re.search(padrao, lead_low))
            no_corpo = bool(re.search(padrao, corpo_low))

            if no_titulo:
                pontos_por_categoria[cat_key] += int(peso * 1.8)
                termos_encontrados.append(termo)
            elif no_lead:
                pontos_por_categoria[cat_key] += int(peso * 1.2)
                termos_encontrados.append(termo)
            elif no_corpo:
                pontos_por_categoria[cat_key] += int(peso * 0.6)
                termos_encontrados.append(termo)

    units_acoes = {"TAEE11", "SAPR11", "SANB11", "ALUP11", "KLBN11", "BPAC11", "ENGI11", "IGTI11"}
    tickers_fii = [t for t in tickers if t.endswith("11") and t not in units_acoes]
    if tickers_fii:
        pontos_por_categoria["FUNDOS_IMOBILIARIOS"] += len(tickers_fii) * 22

    soma_taxonomia = sum(pontos_por_categoria.values())
    if soma_taxonomia == 0 and not tickers:
        return AnaliseHeuristica(score_total=0, categoria_chave="DESCARTADA", categoria_label="")

    bonus_tickers = min(len(tickers) * 14, 42)
    bonus_ancora = 8

    metricas = REGEX_METRICAS_NUMERICAS.findall(texto_completo)
    bonus_metricas = min(len(metricas) * 3, 18)

    categoria_dominante = max(pontos_por_categoria, key=lambda k: pontos_por_categoria[k])
    score_final = max(0, soma_taxonomia + bonus_tickers + bonus_ancora + bonus_metricas)

    cat_label = definir_tag_curta_e_precisa(titulo, lead, categoria_dominante, tickers, tickers_fii)

    return AnaliseHeuristica(
        score_total=score_final,
        categoria_chave=categoria_dominante,
        categoria_label=cat_label,
        tickers=tickers,
        termos_detectados=list(dict.fromkeys(termos_encontrados)),
    )


# ==============================================================================
# CONTROLE ANTIDUPLICIDADE TRIPLO (URL CANÔNICA + TÍTULO + LEAD DE AGÊNCIAS)
# ==============================================================================
def normalizar_texto_dedup(texto: str) -> str:
    t = html.unescape(texto or "").strip()
    t = re.sub(
        r"\s+(?:-|–|—|\|)\s+(?:InfoMoney|Money Times|Seu Dinheiro|Suno|Exame|G1|Valor Investe|E-Investidor|Brazil Journal|FIIs).*$",
        "",
        t,
        flags=re.IGNORECASE,
    )
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return t.lower().strip()


def extrair_tokens_dedup(texto: str) -> Set[str]:
    norm = normalizar_texto_dedup(texto)
    return {
        p for p in re.findall(r"[a-z0-9]+", norm)
        if len(p) >= 3 and p not in STOPWORDS_DEDUP and not p.isdigit()
    }


def extrair_numeros_relevantes(texto: str) -> Set[str]:
    nums = set()
    for m in re.findall(r"\d+(?:[.,]\d+)?", texto or ""):
        limpo = m.replace(".", "").replace(",", ".")
        if limpo in {"2024", "2025", "2026", "2027", "2028", "2029", "2030"}:
            continue
        if len(m) >= 2 or "," in m or "." in m:
            nums.add(m)
    return nums


def gerar_hash_url(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    host = p.netloc.lower().removeprefix("www.")
    query_limpa = "&".join(
        f"{k}={v}"
        for k, v in urllib.parse.parse_qsl(p.query)
        if not k.lower().startswith(("utm_", "fbclid", "gclid", "ref"))
    )
    url_canonica = urllib.parse.urlunsplit(("https", host, p.path.rstrip("/"), query_limpa, ""))
    return hashlib.sha256(url_canonica.encode("utf-8")).hexdigest()[:16]


def carregar_historico() -> Tuple[List[str], List[str], List[str]]:
    if HISTORICO_ARQUIVO.exists():
        try:
            dados = json.loads(HISTORICO_ARQUIVO.read_text(encoding="utf-8"))
            if isinstance(dados, dict):
                return (
                    dados.get("links", []),
                    dados.get("titulos", []),
                    dados.get("leads", []),
                )
            if isinstance(dados, list):
                return dados, [], []
        except Exception as exc:
            logger.error("Erro ao ler arquivo de histórico: %s", exc)
    return [], [], []


def salvar_historico(links: List[str], titulos: List[str], leads: List[str]) -> None:
    try:
        dados = {
            "links": list(dict.fromkeys(links))[-MAX_HISTORICO:],
            "titulos": list(dict.fromkeys(titulos))[-MAX_HISTORICO:],
            "leads": list(dict.fromkeys(leads))[-250:],
        }
        temp_file = HISTORICO_ARQUIVO.with_suffix(".tmp")
        temp_file.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_file, HISTORICO_ARQUIVO)
    except Exception as exc:
        logger.error("Erro ao salvar histórico atomicamente: %s", exc)


def eh_mesma_noticia(novo_titulo: str, titulos_anteriores: List[str]) -> bool:
    norm_novo = normalizar_texto_dedup(novo_titulo)
    tokens_novo = extrair_tokens_dedup(novo_titulo)
    nums_novo = extrair_numeros_relevantes(novo_titulo)
    if not tokens_novo:
        return False

    for antigo in titulos_anteriores[-100:]:
        norm_antigo = normalizar_texto_dedup(antigo)
        if norm_novo == norm_antigo:
            return True

        tokens_antigo = extrair_tokens_dedup(antigo)
        if not tokens_antigo:
            continue

        inter = tokens_novo & tokens_antigo
        menor = min(len(tokens_novo), len(tokens_antigo))
        ratio = len(inter) / menor if menor > 0 else 0.0

        if len(inter) >= 3 and ratio >= 0.45:
            return True

        nums_antigo = extrair_numeros_relevantes(antigo)
        if nums_novo and (nums_novo & nums_antigo) and len(inter) >= 2:
            return True

    return False


def eh_mesmo_conteudo_lead(novo_lead: str, leads_anteriores: List[str]) -> bool:
    if not novo_lead or len(novo_lead) < 60:
        return False
    tokens_novo = extrair_tokens_dedup(novo_lead[:280])
    if len(tokens_novo) < 5:
        return False

    for antigo in leads_anteriores[-100:]:
        tokens_antigo = extrair_tokens_dedup(antigo[:280])
        if len(tokens_antigo) < 5:
            continue
        inter = tokens_novo & tokens_antigo
        menor = min(len(tokens_novo), len(tokens_antigo))
        ratio = len(inter) / menor if menor > 0 else 0.0
        if len(inter) >= 5 and ratio >= 0.48:
            return True
    return False


# ==============================================================================
# UTILITÁRIOS DE LIMPEZA E EXTRAÇÃO DE TEXTO (TRAFILATURA)
# ==============================================================================
def gerar_botao_grupo_aleatorio() -> Dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": TEXTO_BOTAO_CHAT, "url": GRUPO_URL}]
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


def url_imagem_eh_valida_sintaticamente(url: Optional[str]) -> bool:
    if not url:
        return False
    u_low = url.lower()
    if not u_low.startswith(("http://", "https://")):
        return False
    if any(ruim in u_low for ruim in TERMOS_URL_IMAGEM_RUIM):
        return False
    return True


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


def eh_propaganda_ou_lixo(titulo: str, inicio_texto: str, url: str = "") -> bool:
    url_low = url.lower()
    if any(caminho in url_low for caminho in CAMINHOS_URL_BLOQUEADOS):
        return True
    t_low = titulo.lower()
    if any(b in t_low for b in BLOQUEADOS_APENAS_NO_TITULO):
        return True
    amostra = f"{t_low} {(inicio_texto or '')[:450].lower()}"
    return any(b in amostra for b in BLOQUEADOS_PROPAGANDA_CORRETORA)


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
        r"<(script|style|noscript|svg|head|nav|footer|header|aside|form|figure|figcaption)\b[^>]*>.*?</\1>",
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
    txt = REGEX_CREDITO_FOTO_PARENTESES.sub(" ", txt)
    txt = remover_carimbo_data_g1(txt)
    txt = " ".join(txt.split()).strip()
    txt = re.sub(r"\s+([.,;:!?)])", r"\1", txt)
    txt = re.sub(r"([(])\s+", r"\1", txt)
    txt = re.sub(r"\b(R\$|US\$)(\d)", r"\1 \2", txt)
    return txt


def paragrafo_eh_ruido_editorial(txt: str, titulo: str) -> bool:
    if not txt or len(txt) < 40:
        return True
    if txt.strip().endswith("?"):
        return True
    ruidos = (
        "{", "}", "@type", "@context", "schema.org", "dataLayer", "window.", "function(",
        "gostaria de receber", "assine a", "exclusivo para assinantes", "inscreva-se",
        "todos os direitos reservados", "the post", "apareceu primeiro", "leia também",
        "veja também", "confira também", "siga o", "canal no whatsapp", "receba notícias",
        "reprodução/", "foto:", "crédito:", "imagem:", "divulgação/", "getty images",
        "nurphoto", "shutterstock", "unsplash", "freepik", "embeds_referring_euri",
        "source_ve_path", "time_continue=", "watch?v=", "publicidade", "patrocinado",
    )
    t_low = txt.lower()
    if any(r in t_low for r in ruidos):
        return True
    if titulo and t_low == titulo.lower().strip():
        return True
    if txt.count("[") >= 3 or txt.count("]") >= 3:
        return True
    return False


def extrair_paragrafos_trafilatura(raw_html: str, desc_bruta: str, titulo: str) -> List[str]:
    paragrafos_limpos: List[str] = []

    if raw_html:
        html_sem_legendas = re.sub(
            r"<(figure|figcaption)\b[^>]*>.*?</\1>",
            " ",
            raw_html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        texto_extraido = trafilatura.extract(
            html_sem_legendas,
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

    if len(paragrafos_limpos) < 2 and desc_bruta:
        blocos_p = re.findall(r"<p\b[^>]*>(.*?)</p>", desc_bruta, flags=re.DOTALL | re.IGNORECASE)
        if blocos_p:
            candidatos_rss: List[str] = []
            for b in blocos_p:
                p = limpar_caudas_wordpress(limpar_html(b))
                if not paragrafo_eh_ruido_editorial(p, titulo):
                    candidatos_rss.append(p)
            if len(candidatos_rss) > len(paragrafos_limpos):
                paragrafos_limpos = candidatos_rss

        if len(paragrafos_limpos) < 2:
            t_desc = limpar_caudas_wordpress(limpar_html(desc_bruta))
            frases = [
                f.strip()
                for f in re.split(r"(?<=[.!?])\s+", t_desc)
                if len(f.strip()) >= 35 and not f.strip().endswith("?")
            ]
            if len(frases) >= 3:
                paragrafos_limpos = [frases[0], " ".join(frases[1:-1]), frases[-1]]
            elif len(frases) == 2:
                paragrafos_limpos = [frases[0], frases[1]]
            elif not paragrafos_limpos and t_desc and len(t_desc) >= 60:
                paragrafos_limpos = [t_desc]

    return paragrafos_limpos


# ==============================================================================
# NARRATIVA APROFUNDADA (~1300 A 1400 CARACTERES COM COMEÇO, MEIO E FIM)
# ==============================================================================
def dividir_em_frases_completas(texto: str) -> List[str]:
    texto = REGEX_CREDITO_FOTO_PARENTESES.sub(" ", texto)
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
        if p.endswith("?"):
            continue
        p_low = p.lower()
        if any(cred in p_low for cred in ("getty images", "nurphoto", "shutterstock", "reprodução/", "divulgação/", "foto:")):
            continue
        if re.search(
            r"\b(?:de|da|do|das|dos|em|na|no|para|por|com|que|e|ou|cerca|visam|foram|está|são)\.?$",
            p,
            flags=re.IGNORECASE,
        ):
            continue
        if p[-1] not in ".!":
            p += "."
        frases.append(p)
    return frases


def frase_repete_informacao(nova_frase: str, frases_ja_escolhidas: List[str]) -> bool:
    palavras_nova = extrair_tokens_dedup(nova_frase)
    if not palavras_nova:
        return True

    metricas_nova = set(m.lower().strip() for m in REGEX_METRICAS_NUMERICAS.findall(nova_frase))

    for f_antiga in frases_ja_escolhidas:
        palavras_antiga = extrair_tokens_dedup(f_antiga)
        if not palavras_antiga:
            continue
        inter = palavras_nova & palavras_antiga
        proporcao = len(inter) / min(len(palavras_nova), len(palavras_antiga))
        if proporcao >= 0.55:
            return True
        metricas_antiga = set(m.lower().strip() for m in REGEX_METRICAS_NUMERICAS.findall(f_antiga))
        if metricas_nova and (metricas_nova & metricas_antiga) and proporcao >= 0.35:
            return True

    return False


def truncar_frase_limpa(texto: str, max_chars: int) -> str:
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
    if len(primeira) <= max_chars + 45:
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
    qtd_metricas = len(REGEX_METRICAS_NUMERICAS.findall(paragrafo))
    qtd_tickers = len(REGEX_TICKER_B3.findall(paragrafo))
    p_low = paragrafo.lower()
    qtd_taxonomia = sum(
        peso
        for cat in TAXONOMIA_PESOS.values()
        for termo, peso in cat["termos"].items()
        if termo in p_low
    )
    return (qtd_metricas * 12) + (qtd_tickers * 18) + qtd_taxonomia


def extrair_narrativa_aprofundada(paragrafos: List[str], max_por_bloco: int = 340) -> Optional[List[str]]:
    todas_frases: List[str] = []
    for p in paragrafos:
        for f in dividir_em_frases_completas(p):
            if len(f) >= 35 and not frase_repete_informacao(f, todas_frases):
                todas_frases.append(f)

    if len(todas_frases) < 2:
        return None

    blocos: List[str] = []
    bloco_atual = ""
    for f in todas_frases:
        cand = f"{bloco_atual} {f}".strip() if bloco_atual else f
        if len(cand) <= max_por_bloco:
            bloco_atual = cand
        else:
            if bloco_atual and len(bloco_atual) >= 55:
                blocos.append(bloco_atual)
            bloco_atual = truncar_frase_limpa(f, max_por_bloco)
    if bloco_atual and len(bloco_atual) >= 45:
        blocos.append(bloco_atual)

    if len(blocos) < 3:
        blocos_fallback = [truncar_frase_limpa(p, max_por_bloco) for p in paragrafos if len(p) >= 40]
        blocos_fallback = [b for b in dict.fromkeys(blocos_fallback) if b]
        if len(blocos_fallback) >= 3:
            blocos = blocos_fallback
        elif len(todas_frases) >= 3:
            b1 = truncar_frase_limpa(todas_frases[0], max_por_bloco)
            b2 = truncar_frase_limpa(todas_frases[1], max_por_bloco)
            b3 = truncar_frase_limpa(" ".join(todas_frases[2:4]), max_por_bloco)
            if b1 and b2 and b3:
                return [b1, b2, b3]
        elif len(todas_frases) == 2:
            b1 = truncar_frase_limpa(todas_frases[0], max_por_bloco)
            b2 = truncar_frase_limpa(todas_frases[1], max_por_bloco)
            if b1 and b2:
                return [b1, b2]
            return None
        else:
            return None

    p_comeco = blocos[0]

    if len(blocos) >= 4:
        idx_meio1 = 1
        if len(blocos) >= 5 and not REGEX_INICIO_ORFAO.search(blocos[2]):
            if pontuar_paragrafo_meio(blocos[2]) > pontuar_paragrafo_meio(blocos[1]) + 25:
                idx_meio1 = 2
        p_meio1 = blocos[idx_meio1]

        restantes_meio = list(range(idx_meio1 + 1, min(idx_meio1 + 4, len(blocos) - 1)))
        idx_meio2 = restantes_meio[0] if restantes_meio else (idx_meio1 + 1)
        melhor_score_m2 = -999
        for idx in restantes_meio:
            cand = blocos[idx]
            penalidade = -50 if REGEX_INICIO_ORFAO.search(cand) else 0
            score = pontuar_paragrafo_meio(cand) + (12 if idx == idx_meio1 + 1 else 0) + penalidade
            if score > melhor_score_m2:
                melhor_score_m2 = score
                idx_meio2 = idx
        p_meio2 = blocos[idx_meio2]

        janela_fim = blocos[idx_meio2 + 1 : min(idx_meio2 + 5, len(blocos))]
        if not janela_fim:
            janela_fim = [blocos[-1]]

        melhor_fim = janela_fim[0]
        melhor_score_fim = -999
        for offset, cand in enumerate(janela_fim):
            penalidade = -50 if (offset > 0 and REGEX_INICIO_ORFAO.search(cand)) else 0
            c_low = cand.lower()
            bonus_desfecho = 25 if any(m in c_low for m in MARCADORES_DESFECHO) else 0
            score = bonus_desfecho + (pontuar_paragrafo_meio(cand) // 2) + (12 - offset * 3) + penalidade
            if score > melhor_score_fim:
                melhor_score_fim = score
                melhor_fim = cand
        p_fim = melhor_fim

        escolhidos = [p_comeco, p_meio1, p_meio2, p_fim]
    elif len(blocos) == 3:
        escolhidos = [blocos[0], blocos[1], blocos[2]]
    else:
        escolhidos = blocos[:2]

    normalizados = {b.lower().strip() for b in escolhidos if b.strip()}
    if len(normalizados) < 2:
        return None

    return escolhidos


def tamanho_texto_visivel(html_str: str) -> int:
    texto_sem_tags = re.sub(r"<[^>]+>", "", html_str)
    return len(html.unescape(texto_sem_tags))


def montar_mensagem_investidor(
    titulo: str, paragrafos: List[str], analise: AnaliseHeuristica
) -> Optional[Tuple[str, str]]:
    t_limpo = limpar_html(titulo.strip())
    if " - " in t_limpo:
        t_limpo, _ = t_limpo.rsplit(" - ", 1)
    if t_limpo and t_limpo[-1] not in ".!?":
        t_limpo += "."

    blocos_narrativa = extrair_narrativa_aprofundada(paragrafos, max_por_bloco=345)
    if not blocos_narrativa:
        return None

    titulo_html = formatar_tickers_no_texto(html.escape(t_limpo))

    if analise.tickers:
        pills_tickers = " ".join(f"<code>#{t}</code>" for t in analise.tickers[:3])
        cabecalho_tema = f"<blockquote>{analise.categoria_label}  ·  {pills_tickers}</blockquote>"
    else:
        cabecalho_tema = f"<blockquote>{analise.categoria_label}</blockquote>"

    paragrafos_html = [formatar_tickers_no_texto(html.escape(p)) for p in blocos_narrativa]
    corpo_unido = "\n\n".join(paragrafos_html)
    post_completo = f"{cabecalho_tema}\n\n<b>{titulo_html}</b>\n\n{corpo_unido}"

    if tamanho_texto_visivel(post_completo) > ALVO_MAX_CARACTERES_POST:
        blocos_ajustados = extrair_narrativa_aprofundada(paragrafos, max_por_bloco=295)
        if blocos_ajustados:
            paragrafos_html = [formatar_tickers_no_texto(html.escape(p)) for p in blocos_ajustados]
            post_completo = f"{cabecalho_tema}\n\n<b>{titulo_html}</b>\n\n" + "\n\n".join(paragrafos_html)

    if tamanho_texto_visivel(post_completo) < 260:
        return None

    # Caption nativa <= 1024 caracteres (usada no sendPhoto/sendVideo quando o site bloqueia preview remoto)
    blocos_curtos = [
        truncar_frase_limpa(blocos_narrativa[0], 240) or blocos_narrativa[0][:240],
        truncar_frase_limpa(blocos_narrativa[min(1, len(blocos_narrativa) - 1)], 250)
        or blocos_narrativa[min(1, len(blocos_narrativa) - 1)][:250],
    ]
    if len(blocos_narrativa) >= 3:
        blocos_curtos.append(truncar_frase_limpa(blocos_narrativa[-1], 240) or blocos_narrativa[-1][:240])

    curtos_html = [formatar_tickers_no_texto(html.escape(p)) for p in blocos_curtos if p]
    caption_curta = f"{cabecalho_tema}\n\n<b>{titulo_html}</b>\n\n" + "\n\n".join(curtos_html)
    if tamanho_texto_visivel(caption_curta) > TELEGRAM_CAPTION_LIMIT - 10:
        blocos_curtos = [
            truncar_frase_limpa(blocos_narrativa[0], 200) or blocos_narrativa[0][:200],
            truncar_frase_limpa(blocos_narrativa[-1], 200) or blocos_narrativa[-1][:200],
        ]
        curtos_html = [formatar_tickers_no_texto(html.escape(p)) for p in blocos_curtos if p]
        caption_curta = f"{cabecalho_tema}\n\n<b>{titulo_html}</b>\n\n" + "\n\n".join(curtos_html)

    return post_completo, caption_curta


# ==============================================================================
# ENGINE DE MÍDIA E GARANTIA 100% DE IMAGEM VÁLIDA (NUNCA ENVIA TEXTO SECO)
# ==============================================================================
def extrair_og_image_da_pagina(raw_html: str) -> Optional[str]:
    padroes = (
        r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::secure_url)?["\']',
        r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image(?::src)?["\']',
        r'"image"\s*:\s*\{\s*"@type"\s*:\s*"ImageObject"[^}]*"url"\s*:\s*"([^"]+)"',
        r'"image"\s*:\s*\[\s*"(https?://[^"]+)"',
        r'"image"\s*:\s*"(https?://[^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"',
    )
    for padrao in padroes:
        m = re.search(padrao, raw_html, re.IGNORECASE)
        if m:
            url = normalizar_url(m.group(1).replace(r"\/", "/"))
            if url_imagem_eh_valida_sintaticamente(url):
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
            elif ("image" in tipo or url.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))) and url_imagem_eh_valida_sintaticamente(url):
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
                    elif not foto_url and ("image" in tipo or medium == "image" or url.startswith("http")) and url_imagem_eh_valida_sintaticamente(url):
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
        for attr in ("src", "data-src", "data-lazy-src"):
            busca_img = re.search(rf'<img[^>]+{attr}=["\']([^"\']+)["\']', desc_bruta, re.IGNORECASE)
            if busca_img:
                cand_img = normalizar_url(busca_img.group(1))
                if url_imagem_eh_valida_sintaticamente(cand_img):
                    foto_url = cand_img
                    break

    return foto_url, video_url, audio_url


async def verificar_e_preparar_imagem(
    client: httpx.AsyncClient, sem: asyncio.Semaphore, url_imagem: Optional[str], referer_url: str
) -> Optional[ImagemVerificada]:
    """
    Garante que a imagem realmente existe, é grande (>= 8 KB) e decide o modo 100% confiável:
    - Se o crawler do Telegram ('TelegramBot' sem Referer) consegue baixar JPEG/PNG direto ->
      permite post longo (~1.300-1.400 chars) com preview grande no topo.
    - Se o servidor bloqueia o crawler do Telegram ou devolve WebP -> baixa os bytes com
      User-Agent + Referer para upload nativo via sendPhoto (100% garantido com imagem!).
    - Se não conseguir imagem válida de jeito nenhum -> retorna None (matéria descartada).
    """
    if not url_imagem_eh_valida_sintaticamente(url_imagem):
        return None

    assert url_imagem is not None

    async with sem:
        # 1) Simula o crawler do Telegram (sem Referer)
        try:
            resp_tg = await client.get(url_imagem, headers=HEADERS_TELEGRAM_PREVIEW, timeout=10.0)
            ct_tg = (resp_tg.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if (
                resp_tg.status_code == 200
                and MIN_IMAGE_BYTES <= len(resp_tg.content) <= MAX_HTML_BYTES
                and ct_tg in ("image/jpeg", "image/jpg", "image/png")
            ):
                return ImagemVerificada(
                    url=str(resp_tg.url),
                    pode_usar_preview_longo=True,
                    conteudo_bytes=resp_tg.content,
                    content_type=ct_tg,
                )
            if (
                resp_tg.status_code == 200
                and MIN_IMAGE_BYTES <= len(resp_tg.content) <= MAX_HTML_BYTES
                and ct_tg == "image/webp"
            ):
                return ImagemVerificada(
                    url=str(resp_tg.url),
                    pode_usar_preview_longo=False,
                    conteudo_bytes=resp_tg.content,
                    content_type="image/webp",
                )
        except Exception as exc:
            logger.debug("Teste TelegramBot na imagem falhou (%s): %s", url_imagem, exc)

        # 2) Fallback com navegador + Referer -> força upload nativo via sendPhoto (nunca envia sem imagem!)
        try:
            headers_ref = {
                **HEADERS,
                "Referer": referer_url,
                "Accept": "image/jpeg,image/png,image/webp,*/*;q=0.5",
            }
            resp_ref = await client.get(url_imagem, headers=headers_ref, timeout=12.0)
            ct_ref = (resp_ref.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if (
                resp_ref.status_code == 200
                and MIN_IMAGE_BYTES <= len(resp_ref.content) <= MAX_HTML_BYTES
                and ct_ref in ("image/jpeg", "image/jpg", "image/png", "image/webp")
            ):
                return ImagemVerificada(
                    url=str(resp_ref.url),
                    pode_usar_preview_longo=False,
                    conteudo_bytes=resp_ref.content,
                    content_type=ct_ref,
                )
        except Exception as exc:
            logger.debug("Download com Referer na imagem falhou (%s): %s", url_imagem, exc)

    return None


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
        texto_desc = desc_el.text if desc_el is not None and desc_el.text else ""
        texto_content = content_el.text if content_el is not None and content_el.text else ""
        desc_bruta = texto_content if len(texto_content) > len(texto_desc) else texto_desc

        if not link or not title or len(title) < 22:
            continue

        dt_pub = extrair_data_publicacao(item)
        if not dt_pub:
            continue

        idade_minutos = (agora_utc - dt_pub).total_seconds() / 60.0
        if idade_minutos > MAX_IDADE_MINUTOS or idade_minutos < -200.0:
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
) -> Tuple[List[NoticiaPreparada], List[str], List[str], List[str]]:
    links_historico, titulos_historico, leads_historico = carregar_historico()
    set_links: Set[str] = set(links_historico)
    titulos_rodada: List[str] = list(titulos_historico)
    leads_rodada: List[str] = list(leads_historico)

    sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    resultados_feeds = await asyncio.gather(
        *(buscar_feed_rss(client, sem, url) for url in FEEDS)
    )

    candidatos_unicos: List[Dict[str, Any]] = []
    hashes_enfileirados: Set[str] = set()
    for sublista in resultados_feeds:
        for item in sublista:
            link_h = gerar_hash_url(item["link"])
            if item["link"] in set_links or link_h in set_links or link_h in hashes_enfileirados:
                continue
            hashes_enfileirados.add(link_h)
            candidatos_unicos.append(item)

    candidatos_unicos.sort(key=lambda x: x["dt_pub"], reverse=True)

    noticias_aprovadas: List[NoticiaPreparada] = []
    qtd_dup_titulo = 0
    qtd_dup_lead = 0
    qtd_sem_imagem = 0
    qtd_descartadas = 0

    for cand in candidatos_unicos[:60]:
        if len(noticias_aprovadas) >= limite * 2:
            break

        title = cand["title"]
        link = cand["link"]
        link_hash = gerar_hash_url(link)
        desc_bruta = cand["desc_bruta"]
        item = cand["item"]

        if eh_mesma_noticia(title, titulos_rodada):
            qtd_dup_titulo += 1
            continue

        foto_rss, video_rss, audio_rss = extrair_midias_do_rss(item, desc_bruta)
        paragrafos, foto_og, video_pag = await obter_conteudo_e_midias_artigo(
            client, sem, link, desc_bruta, title
        )

        if not paragrafos:
            qtd_descartadas += 1
            logger.info("Descartada (texto da página não pôde ser extraído): %s", title)
            continue

        lead_artigo = paragrafos[0]
        if eh_mesmo_conteudo_lead(lead_artigo, leads_rodada):
            qtd_dup_lead += 1
            logger.info("Descartada por duplicidade de conteúdo (mesma matéria em outro portal): %s", title)
            continue

        if eh_propaganda_ou_lixo(title, " ".join(paragrafos[:2]), link):
            qtd_descartadas += 1
            logger.info("Descartada por filtro de propaganda/tema: %s", title)
            continue

        analise = avaliar_impacto_financeiro(title, paragrafos)
        if analise.score_total < SCORE_MINIMO_CORTE:
            qtd_descartadas += 1
            logger.info(
                "Descartada fora dos 3 pilares Ações/FIIs/Renda Fixa (Score %d < %d): %s",
                analise.score_total,
                SCORE_MINIMO_CORTE,
                title,
            )
            continue

        montagem = montar_mensagem_investidor(title, paragrafos, analise)
        if not montagem:
            qtd_descartadas += 1
            logger.info("Descartada por não atingir estrutura completa (Começo, Meio e Fim): %s", title)
            continue

        # Prioriza og:image (alta resolução) e tenta foto do RSS como backup
        img_verificada: Optional[ImagemVerificada] = None
        for cand_img in dict.fromkeys(u for u in (foto_og, foto_rss) if u):
            img_verificada = await verificar_e_preparar_imagem(client, sem, cand_img, link)
            if img_verificada:
                break

        video_cand = video_rss or video_pag
        if not img_verificada and not video_cand:
            qtd_sem_imagem += 1
            logger.info("Descartada (sem imagem válida para exibir no Telegram): %s", title)
            continue

        post_html, caption_curta_html = montagem
        modo_img = (
            "Preview Longo"
            if (img_verificada and img_verificada.pode_usar_preview_longo)
            else ("Foto Nativa" if img_verificada else "Vídeo")
        )

        logger.info(
            "Aprovada [Score %d | %s | %s | %d chars | Tickers: %s]: %s",
            analise.score_total,
            analise.categoria_chave,
            modo_img,
            tamanho_texto_visivel(post_html),
            ",".join(analise.tickers) or "N/A",
            title,
        )

        noticias_aprovadas.append(
            NoticiaPreparada(
                link=link,
                titulo=title,
                lead_limpo=lead_artigo[:280],
                texto_html=post_html,
                caption_curta_html=caption_curta_html,
                score=analise.score_total,
                dt_pub=cand["dt_pub"],
                imagem=img_verificada.url if img_verificada else None,
                imagem_verificada=img_verificada,
                video_candidato_url=video_cand,
                audio=audio_rss,
            )
        )
        titulos_rodada.append(title)
        leads_rodada.append(lead_artigo[:280])
        set_links.add(link)
        set_links.add(link_hash)

    logger.info(
        "Resumo do ciclo: %d links novos | %d dup título | %d dup conteúdo | %d sem imagem | %d fora dos 3 pilares | %d aprovados",
        len(candidatos_unicos),
        qtd_dup_titulo,
        qtd_dup_lead,
        qtd_sem_imagem,
        qtd_descartadas,
        len(noticias_aprovadas),
    )

    noticias_aprovadas.sort(key=lambda n: (n.score, n.dt_pub), reverse=True)
    selecionadas = noticias_aprovadas[:limite]
    selecionadas.sort(key=lambda n: n.dt_pub)

    return selecionadas, links_historico, titulos_historico, leads_historico


# ==============================================================================
# CLIENTE TELEGRAM (SEMPRE COM IMAGEM OU VÍDEO — ZERO POSTS SÓ DE TEXTO)
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
    if not item.video_candidato_url:
        return False

    urls_para_testar = [item.video_candidato_url, item.link]

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
            legenda_video = (
                item.texto_html
                if tamanho_texto_visivel(item.texto_html) <= TELEGRAM_CAPTION_LIMIT
                else item.caption_curta_html
            )
            data_fields = {
                "chat_id": chat_id,
                "caption": legenda_video,
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


async def tentar_enviar_foto_garantida(
    client: httpx.AsyncClient,
    base_url: str,
    chat_id: str,
    item: NoticiaPreparada,
    botao_sorteado: Dict[str, Any],
) -> bool:
    img = item.imagem_verificada
    if not img:
        return False

    texto_eh_longo = (
        len(item.texto_html) > TELEGRAM_CAPTION_LIMIT
        or tamanho_texto_visivel(item.texto_html) > TELEGRAM_CAPTION_LIMIT
    )

    # Caso 1: Texto longo (~1300-1400 chars) + imagem JPEG/PNG acessível pelo crawler do Telegram
    if texto_eh_longo and img.pode_usar_preview_longo:
        img_escapada = html.escape(img.url, quote=True)
        texto_com_midia_topo = f'<a href="{img_escapada}">&#8203;</a>{item.texto_html}'
        payload_longo = {
            "chat_id": chat_id,
            "text": texto_com_midia_topo,
            "parse_mode": "HTML",
            "link_preview_options": {
                "is_disabled": False,
                "url": img.url,
                "prefer_large_media": True,
                "show_above_text": True,
            },
            "reply_markup": botao_sorteado,
        }
        if await telegram_request(client, f"{base_url}/sendMessage", json_payload=payload_longo):
            return True

    # Caso 2: Upload binário nativo via sendPhoto (100% garantido que a imagem aparece!)
    url_photo = f"{base_url}/sendPhoto"
    legenda_foto = item.texto_html if not texto_eh_longo else item.caption_curta_html
    if img.conteudo_bytes:
        ext = "png" if "png" in img.content_type else ("webp" if "webp" in img.content_type else "jpg")
        data_fields = {
            "chat_id": chat_id,
            "caption": legenda_foto,
            "parse_mode": "HTML",
            "reply_markup": json.dumps(botao_sorteado),
        }
        files = {"photo": (f"cover.{ext}", img.conteudo_bytes, img.content_type)}
        if await telegram_request(client, url_photo, data_fields=data_fields, files=files):
            return True

    payload_img = {
        "chat_id": chat_id,
        "photo": img.url,
        "caption": legenda_foto,
        "parse_mode": "HTML",
        "reply_markup": botao_sorteado,
    }
    return await telegram_request(client, url_photo, json_payload=payload_img)


async def enviar_post(
    client: httpx.AsyncClient, token: str, chat_id: str, item: NoticiaPreparada
) -> bool:
    base_url = f"https://api.telegram.org/bot{token}"
    botao_sorteado = gerar_botao_grupo_aleatorio()

    if await tentar_enviar_video_ytdlp(client, base_url, chat_id, item, botao_sorteado):
        return True

    if await tentar_enviar_foto_garantida(client, base_url, chat_id, item, botao_sorteado):
        return True

    # Nunca publica texto seco sem imagem/vídeo
    logger.warning("Publicação cancelada para evitar texto sem imagem: %s", item.titulo)
    return False


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
        noticias, links_historico, titulos_historico, leads_historico = await carregar_proximas_noticias(
            client, limite=MAX_NOTICIAS_POR_VEZ
        )

        if not noticias:
            logger.info("Nenhuma notícia de Ações, FIIs ou Renda Fixa com imagem válida neste ciclo.")
            return

        logger.info("Encontradas %d notícias (Ações, FIIs e Renda Fixa) para publicação.", len(noticias))
        for noticia in noticias:
            sucesso = await enviar_post(client, token, chat_id, noticia)
            if sucesso:
                links_historico.append(noticia.link)
                links_historico.append(gerar_hash_url(noticia.link))
                titulos_historico.append(noticia.titulo)
                if noticia.lead_limpo:
                    leads_historico.append(noticia.lead_limpo)
                salvar_historico(links_historico, titulos_historico, leads_historico)
                logger.info("Notícia publicada [Score %d]: %s", noticia.score, noticia.titulo)
                await asyncio.sleep(3.0)
            else:
                logger.error("Falha ao publicar notícia com mídia: %s", noticia.titulo)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Execução interrompida manualmente.")
