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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("BotInvestidor")

# ==============================================================================
# CONFIGURAÇÕES GERAIS (GITHUB SECRETS / ENV)
# ==============================================================================
HISTORICO_ARQUIVO = Path(os.environ.get("HISTORICO_ARQUIVO", "historico_enviados.json"))
GRUPO_URL = os.environ.get("TELEGRAM_GROUP_URL", "https://t.me/+dxKqjeavEWYzMzYx")

MAX_HISTORICO = 800
MAX_NOTICIAS_POR_VEZ = int(os.environ.get("MAX_NOTICIAS_POR_VEZ", "5"))
MAX_IDADE_MINUTOS = int(os.environ.get("MAX_IDADE_MINUTOS", "90"))
SCORE_MINIMO_CORTE = int(os.environ.get("SCORE_MINIMO_CORTE", "16"))
MAX_CONCURRENT_REQUESTS = 12

MAX_HTML_BYTES = 5 * 1024 * 1024
MIN_IMAGE_BYTES = 8 * 1024
MAX_VIDEO_BYTES = 50 * 1024 * 1024
TELEGRAM_CAPTION_LIMIT = 1024
MIN_CARACTERES_POST = 450       # Rejeita textos rasos/cortados por paywall (2 frases)
ALVO_MAX_CARACTERES_POST = 1280 # Ponto ideal no celular: 3 parágrafos completos (~900 a 1.250 chars)

TZ_BRASILIA = timezone(timedelta(hours=-3))
TEXTO_BOTAO_CHAT = "💬 Participe do Chat de Investidores"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}
HEADERS_TELEGRAM_PREVIEW = {
    "User-Agent": "TelegramBot (like TwitterBot)",
    "Accept": "image/jpeg,image/png,*/*;q=0.5",
}

# ==============================================================================
# 32 FEEDS 100% VERIFICADOS AO VIVO (ZERO 404, ZERO XML QUEBRADO, ZERO FEED VAZIO, ZERO FIIs)
# ==============================================================================
FEEDS = [
    # 1. Grandes portais de mercado financeiro e ações B3
    "https://www.infomoney.com.br/feed/",
    "https://www.moneytimes.com.br/feed/",
    "https://valorinveste.globo.com/rss/valorinveste",
    "https://braziljournal.com/feed",
    "https://neofeed.com.br/feed/",
    "https://www.bloomberglinea.com.br/arc/outboundfeeds/rss.xml",
    "https://br.investing.com/rss/news_25.rss",
    "https://br.investing.com/rss/news.rss",
    "https://exame.com/feed/",
    "https://exame.com/invest/feed/",
    "https://www.seudinheiro.com/feed/",
    "https://www.suno.com.br/noticias/feed/",
    "https://www.spacemoney.com.br/feed/",
    "https://bmcnews.com.br/feed/",
    "https://investnews.com.br/feed/",
    "https://borainvestir.b3.com.br/feed/",
    "https://forbes.com.br/forbes-money/feed/",
    # 2. Portais especialistas nos 5 setores (Bancos, Energia Elétrica, Saneamento, Seguros e Telecom)
    "https://finsidersbrasil.com.br/feed/",
    "https://megawhat.energy/feed/",
    "https://eixos.com.br/feed/",
    "https://agenciainfra.com/blog/feed/",
    "https://www.sonhoseguro.com.br/feed/",
    "https://www.cqcs.com.br/feed/",
    "https://telesintese.com.br/feed/",
    "https://teletime.com.br/feed/",
    "https://mobiletime.com.br/feed/",
    # 3. Grandes jornais e agências nacionais
    "https://www.cnnbrasil.com.br/feed/",
    "https://www.poder360.com.br/feed/",
    "https://www.estadao.com.br/arc/outboundfeeds/feeds/rss/sections/economia/",
    "https://feeds.folha.uol.com.br/mercado/rss091.xml",
    "https://g1.globo.com/rss/g1/economia/",
    "https://agenciabrasil.ebc.com.br/rss/economia/feed.xml",
]

DOMINIOS_BLOQUEADOS = (
    "valor.globo.com",
    "fiis.com.br",
)

CAMINHOS_URL_BLOQUEADOS = (
    "/esporte/", "/esportes/", "/futebol/", "/entretenimento/", "/pop/", "/estilo/",
    "/viagem/", "/gastronomia/", "/astrologia/", "/loterias/", "/curiosidades/", "/tv/",
    "/famosos/", "/cultura/", "/politica/", "/mundo/", "/policia/", "/apostas/", "/casual/",
    "/ciencia/", "/blog-", "/cursos/", "/marketing/", "/carros/", "/autoesporte/", "/turismo/",
    "/future-of-money/", "/cripto/", "/criptomoedas/", "/carreira/", "/tecnologia/", "/inteligencia-artificial/",
)

BLOQUEADOS_PROPAGANDA_CORRETORA = (
    "renda fixa hoje:", "dentro da plataforma da xp", "plataforma da xp oferece",
    "lcas na xp", "lcis na xp", "cdbs na xp", "confira as taxas de cdbs, lcis e lcas",
    "conteúdo patrocinado", "branded content", "especial publicitário", "curso gratuito",
    "inscrições abertas", "inscricoes abertas", "webinar", "mentoria", "baixe o e-book",
    "treinamento gratuito", "evento online", "evento gratuito", "cupom de desconto",
    "turbinam benefícios", "turbina benefícios", "pontos e milhas", "acumule pontos",
    "cartão de criptomoedas", "encerra seu cartão", "cartão black", "anuidade grátis",
)

BLOQUEADOS_APENAS_NO_TITULO = (
    # FIIs / FIAGROs (para não vazar FII gerido por BTG, Itaú, Santander ou BB)
    "fundo imobiliário", "fundos imobiliários", "fundo imobiliario", "fundos imobiliarios",
    "fiis", "ifix", "fiagro", "fiagros", "rendimento por cota", "por cota", "cotistas",
    "dona do claude", "anthropic", "openai", "chatgpt", "spacex", "elon musk", "neuralink",
    "tiktok", "bytedance", "sam altman", "bitcoin", "ethereum", "criptomoeda", "criptomoedas",
    "solana", "dogecoin", "binance", "calendário do inss", "meu inss", "benefício do inss",
    "aposentadoria do inss", "pis/pasep", "abono salarial", "bolsa família", "pé-de-meia",
    "auxílio gás", "saque-aniversário", "cnh social", "concurso público", "edital publicado",
    "quem pode fazer mba", "horóscopo", "mega-sena", "lotofácil", "loteria", "fofoca",
    "onde assistir", "escalação", "futebol", "brasileirão", "campeonato brasileiro", "data fifa",
    "libertadores", "copa do brasil", "champions league", "eliminatórias", "seleção brasileira",
    "gta 6", "playstation", "xbox", "canetas emagrecedoras", "cabelo cair", "melhores produtos de",
    "quadro icônico", "obra de arte", "jogo do tigrinho", "cassino online", "operação cash out",
    "assalto", "sequestro", "homicídio", "tráfico de drogas", "facção criminosa", "lei da dosimetria",
    "ao vivo:", "tempo real:", "minuto a minuto", "morning call", "giro de mercado",
    "giro dos mercados", "radar do mercado", "radar corporativo", "resumo do dia",
    "resumo do mercado", "destaques do dia", "destaques corporativos", "o que movimenta",
    "para ficar de olho", "tudo o que você precisa saber", "acompanhe os destaques",
    "agenda do dia", "melhores ações para", "carteira recomendada", "recomendações da semana",
)

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
# TODAS AS EMPRESAS DA B3 NOS 5 SETORES PERENES
# (Bancos, Energia Elétrica, Saneamento, Seguros e Telecomunicações)
# ==============================================================================
SETOR_LABELS: Dict[str, str] = {
    "BANCOS": "🏦 <b>BANCOS</b>",
    "ENERGIA": "⚡ <b>ENERGIA ELÉTRICA</b>",
    "SANEAMENTO": "💧 <b>SANEAMENTO</b>",
    "SEGUROS": "🛡️ <b>SEGUROS</b>",
    "TELECOM": "📡 <b>TELECOM</b>",
}

EMPRESAS_ALVO_B3: Tuple[Tuple[str, str, Tuple[str, ...], Tuple[str, ...]], ...] = (
    # 1. BANCOS
    ("itau", "BANCOS", ("itaú unibanco", "itau unibanco", "itaú", "itau"), ("ITUB4", "ITUB3")),
    ("itausa", "BANCOS", ("itaúsa", "itausa"), ("ITSA4", "ITSA3")),
    ("banco_do_brasil", "BANCOS", ("banco do brasil",), ("BBAS3",)),
    ("bradesco", "BANCOS", ("bradesco",), ("BBDC4", "BBDC3")),
    ("santander", "BANCOS", ("santander brasil", "santander"), ("SANB11", "SANB3", "SANB4")),
    ("btg_pactual", "BANCOS", ("btg pactual", "btg"), ("BPAC11", "BPAC3", "BPAC5")),
    ("abc_brasil", "BANCOS", ("banco abc brasil", "banco abc", "abc brasil"), ("ABCB4",)),
    ("banrisul", "BANCOS", ("banrisul",), ("BRSR6", "BRSR3", "BRSR5")),
    ("banco_bmg", "BANCOS", ("banco bmg",), ("BMGB4",)),
    ("banco_pan", "BANCOS", ("banco pan",), ("BPAN4",)),
    ("banco_pine", "BANCOS", ("banco pine",), ("PINE4", "PINE3")),
    ("banco_inter", "BANCOS", ("banco inter", "inter & co", "inter&co"), ("INBR32",)),
    ("nubank", "BANCOS", ("nubank", "nu holdings"), ("ROXO34",)),
    ("br_partners", "BANCOS", ("br partners",), ("BRBI11",)),
    ("banestes", "BANCOS", ("banestes",), ("BEES3", "BEES4")),
    ("banco_nordeste", "BANCOS", ("banco do nordeste",), ("BNBR3",)),
    ("banco_amazonia", "BANCOS", ("banco da amazônia", "banco da amazonia"), ("BAZA3",)),
    ("banese", "BANCOS", ("banese",), ("BGIP4", "BGIP3")),
    ("banco_mercantil", "BANCOS", ("banco mercantil", "mercantil do brasil"), ("BMEB4", "BMEB3", "BMIN4", "BMIN3")),
    ("brb", "BANCOS", ("banco de brasília", "banco de brasilia", "banco brb"), ("BSLI3", "BSLI4")),
    ("banco_alfa", "BANCOS", ("banco alfa", "financeira alfa"), ("BRIV4", "BRIV3")),
    # 2. ENERGIA ELÉTRICA
    ("eletrobras", "ENERGIA", ("eletrobras", "furnas", "chesf", "eletronorte", "axia energia"), ("ELET3", "ELET6", "ELET5")),
    ("taesa", "ENERGIA", ("taesa",), ("TAEE11", "TAEE3", "TAEE4")),
    ("isa_energia", "ENERGIA", ("isa energia", "isa cteep", "cteep", "transmissão paulista", "transmissao paulista"), ("ISAE4", "ISAE3", "TRPL4", "TRPL3")),
    ("engie", "ENERGIA", ("engie brasil", "engie"), ("EGIE3",)),
    ("cpfl", "ENERGIA", ("cpfl energia", "cpfl"), ("CPFE3",)),
    ("cemig", "ENERGIA", ("cemig",), ("CMIG4", "CMIG3")),
    ("copel", "ENERGIA", ("copel",), ("CPLE3", "CPLE6", "CPLE11")),
    ("equatorial", "ENERGIA", ("equatorial energia", "equatorial"), ("EQTL3", "EQPA3")),
    ("energisa", "ENERGIA", ("energisa",), ("ENGI11", "ENGI3", "ENGI4", "ENMT3", "ENMT4")),
    ("alupar", "ENERGIA", ("alupar",), ("ALUP11", "ALUP3", "ALUP4")),
    ("auren", "ENERGIA", ("auren energia", "auren", "aes brasil"), ("AURE3",)),
    ("neoenergia", "ENERGIA", ("neoenergia",), ("NEOE3",)),
    ("eneva", "ENERGIA", ("eneva",), ("ENEV3",)),
    ("serena", "ENERGIA", ("serena energia", "omega energia"), ("SRNA3",)),
    ("light", "ENERGIA", ("light energia", "light s.a", "distribuidora light", "concessionária light", "grupo light"), ("LIGT3",)),
    ("celesc", "ENERGIA", ("celesc",), ("CLSC4", "CLSC3")),
    ("coelba", "ENERGIA", ("coelba",), ("CEEB3", "CEEB5")),
    ("cosern", "ENERGIA", ("cosern",), ("CSRN3", "CSRN5", "CSRN6")),
    ("coelce", "ENERGIA", ("coelce", "enel ceará", "enel ceara"), ("COCE5", "COCE3")),
    ("ceb", "ENERGIA", ("ceb geração", "ceb distribuicao", "companhia energética de brasília"), ("CEBR3", "CEBR5", "CEBR6")),
    ("ceee", "ENERGIA", ("ceee-g", "ceee-t", "ceee geração"), ("EEEL3", "EEEL4")),
    ("elektro", "ENERGIA", ("elektro",), ("EKTR4", "EKTR3")),
    ("emae", "ENERGIA", ("emae",), ("EMAE4", "EMAE3")),
    ("renova", "ENERGIA", ("renova energia",), ("RNEW3", "RNEW4", "RNEW11")),
    ("afluente_t", "ENERGIA", ("afluente t",), ("AFLT3",)),
    ("rede_energia", "ENERGIA", ("rede energia",), ("REDE3",)),
    ("ampla_energia", "ENERGIA", ("ampla energia",), ("CBEE3",)),
    ("eletropar", "ENERGIA", ("eletropar",), ("LIPR3",)),
    ("paranapanema_energia", "ENERGIA", ("rio paranapanema energia", "ctg brasil"), ("GEPA4", "GEPA3")),
    # 3. SANEAMENTO
    ("sabesp", "SANEAMENTO", ("sabesp",), ("SBSP3",)),
    ("sanepar", "SANEAMENTO", ("sanepar",), ("SAPR11", "SAPR4", "SAPR3")),
    ("copasa", "SANEAMENTO", ("copasa",), ("CSMG3",)),
    ("casan", "SANEAMENTO", ("casan",), ("CASN3", "CASN4")),
    ("orizon", "SANEAMENTO", ("orizon",), ("ORVR3",)),
    # 4. SEGUROS
    ("bb_seguridade", "SEGUROS", ("bb seguridade",), ("BBSE3",)),
    ("caixa_seguridade", "SEGUROS", ("caixa seguridade",), ("CXSE3",)),
    ("porto_seguro", "SEGUROS", ("porto seguro", "porto s.a", "porto bank", "porto saúde", "porto saude"), ("PSSA3",)),
    ("irb", "SEGUROS", ("irb brasil", "irb(re)", "irb re", "irb"), ("IRBR3",)),
    ("wiz", "SEGUROS", ("wiz co", "wiz soluções", "wiz solucoes"), ("WIZC3", "WIZS3")),
    ("odontoprev", "SEGUROS", ("odontoprev",), ("ODPV3",)),
    ("qualicorp", "SEGUROS", ("qualicorp",), ("QUAL3",)),
    # 5. TELECOMUNICAÇÕES
    ("vivo", "TELECOM", ("telefônica brasil", "telefonica brasil", "telefônica", "telefonica", "operadora vivo", "vivo fibra", "vivo empresas"), ("VIVT3",)),
    ("tim", "TELECOM", ("tim brasil", "tim s.a", "operadora tim"), ("TIMS3",)),
    ("oi", "TELECOM", ("oi s.a", "operadora oi", "grupo oi", "nova oi"), ("OIBR3", "OIBR4")),
    ("desktop", "TELECOM", ("desktop s.a", "operadora desktop", "desktop internet"), ("DESK3",)),
    ("unifique", "TELECOM", ("unifique",), ("FIQE3",)),
    ("brisanet", "TELECOM", ("brisanet",), ("BRIT3",)),
    ("telebras", "TELECOM", ("telebras", "telebrás"), ("TELB4", "TELB3")),
)

TICKER_PARA_EMPRESA: Dict[str, Tuple[str, str]] = {
    tk: (ent_id, setor) for ent_id, setor, _, tickers in EMPRESAS_ALVO_B3 for tk in tickers
}

REGEX_BB_BANCO = re.compile(r"\bBB\b(?!\s*(?:Seguridade|Investimentos|BI|-BI|Asset|DTVM|Consórcios|Previdência))")
REGEX_VIVO_OPERADORA = re.compile(r"(?<!\bao\s)(?<!\bAo\s)(?<!\bem\s)(?<!\bser\s)(?<!\bestar\s)(?<!\bestá\s)(?<!\bsegue\s)(?<!\bmais\s)\bVivo\b")
REGEX_TIM_OPERADORA = re.compile(r"\bTIM\b(?!\s+(?:Cook|Berners|Burton|Maia|Hortons|Robbins|Walz|Kaine))")
REGEX_LIGHT_ENERGIA = re.compile(r"\bLight\b(?=.*(?:energia|distribuidor|concess|aneel|tarifa|dívida|recuperação|ações|lucro|balanço|rio))", flags=re.IGNORECASE)

# Evita publicar matérias de outras empresas/macro só porque um banco assinou o relatório
REGEX_RELATORIO_DE_BANCO = re.compile(
    r"\b(?:itaú\s+bba|itau\s+bba|bradesco\s+bbi|bb\s+investimentos|bb-bi|btg\s+pactual\s+digital)\b"
    r"|\b(?:btg(?:\s+pactual)?|itaú|itau|bradesco|santander|banco\s+do\s+brasil)\s+"
    r"(?:recomenda|eleva|corta|rebaixa|reduz\s+preço|aumenta\s+preço|vê\s+|projeta\s+|revisa\s+|prevê\s+|"
    r"aposta\s+em|escolhe\s+|indica\s+|lista\s+|aponta\s+|inclui\s+|retira\s+|remove\s+|tira\s+|"
    r"mantém\s+(?:compra|venda|neutro|recomendação)|inicia\s+cobertura|atualiza\s+carteira|"
    r"diz\s+que\s+(?:a\s+selic|o\s+dólar|o\s+ibovespa|o\s+pib|a\s+inflação))\b"
    r"|\b(?:segundo|para|na\s+visão\s+do|em\s+relatório\s+do|carteira\s+(?:do|da))\s+(?:o\s+)?(?:btg(?:\s+pactual)?|itaú|itau|bradesco|santander|bb)\b",
    flags=re.IGNORECASE,
)

TERMOS_BANCO_PROTAGONISTA = (
    "dividendo", "dividendos", "jcp", "proventos", "lucro", "lucra", "balanço", "ebitda",
    "prejuízo", "roe", "carteira de crédito", "inadimplência", "receita", "resultado",
    "fato relevante", "recompra", "aquisição", "compra banco", "vende", "fusão", "debêntures",
    "emissão", "rating", "agências", "clientes", "ceo", "presidente", "conselho",
    "ações do", "ações da", "papéis do", "papéis da", "capta", "captou",
)

PESOS_EVENTOS_FINANCEIROS: Dict[str, int] = {
    "dividendos": 22, "dividendo": 22, "jcp": 24, "juros sobre capital próprio": 24,
    "juros sobre o capital próprio": 24, "proventos": 20, "data-com": 24, "data com": 22,
    "data-ex": 20, "dividend yield": 20, "payout": 18, "bonificação": 16, "balanço": 20,
    "lucro líquido": 22, "lucro": 16, "lucra": 16, "ebitda": 22, "lajida": 20, "prejuízo": 18,
    "receita líquida": 18, "margem": 14, "roe": 18, "inadimplência": 16, "carteira de crédito": 16,
    "geração de caixa": 16, "guidance": 18, "fato relevante": 24, "fusão": 18, "aquisição": 18,
    "recompra": 20, "opa": 20, "follow-on": 20, "privatização": 18, "concessão": 18, "leilão": 18,
    "aneel": 16, "tarifa": 14, "reajuste tarifário": 18, "anatel": 16, "susep": 16, "debêntures": 16,
    "emissão": 14, "capex": 16, "investimento": 12, "investimentos": 12, "contrato": 12, "dívida": 14,
    "alavancagem": 14, "recuperação judicial": 18, "rating": 14, "ações": 12, "ação": 10,
    "papéis": 10, "acionistas": 14, "b3": 12, "1t2": 14, "2t2": 14, "3t2": 14, "4t2": 14,
}

COOLDOWN_EVENTO_MINUTOS: Dict[str, int] = {
    "PROVENTOS": 1080, "BALANCO": 1440, "EVENTO_CORP": 1080, "GERAL_ATIVO": 480,
}

REGEX_TICKER_B3 = re.compile(r"\b([A-Z]{4}(?:3|4|5|6|11|32|34))\b")
REGEX_METRICAS_NUMERICAS = re.compile(
    r"([+-]?\d+(?:[.,]\d+)?\s*%|\b(?:R\$|US\$|€|£)\s*\d+(?:[.,]\d+)?|\b\d+(?:[.,]\d+)?\s*(?:milhões|bilhões|trilhões|mil|bi|tri|pontos-base|bps|pontos)\b)",
    flags=re.IGNORECASE,
)
REGEX_YOUTUBE_ID = re.compile(r"(?:youtube\.com/(?:watch\?v=|embed/|shorts/|live/)|youtu\.be/)([A-Za-z0-9_-]{11})", flags=re.IGNORECASE)
REGEX_INICIO_ORFAO = re.compile(
    r"^(?:A companhia\b|A empresa\b|O banco\b|A elétrica\b|A operadora\b|A seguradora\b|A instituição\b|"
    r"Segundo ele|Segundo ela|Segundo a empresa|Segundo a companhia|Segundo o banco|Segundo o executivo|"
    r"Para ele|Para ela|De acordo com ele|De acordo com ela|De acordo com a empresa|De acordo com a companhia|"
    r"De acordo com o banco|Ainda segundo ele|Ainda segundo a empresa|Na visão dele|Na avaliação dele|"
    r"Ele acrescenta|Ela acrescenta|Ele afirma|Ela afirma|Além disso|No mesmo intervalo|Na mesma linha|Outro ponto citado)\b",
    flags=re.IGNORECASE,
)
REGEX_DATA_TEXTO_BROADCAST = re.compile(
    r"(?:publicad[ao]\s+(?:na|no|em)\s+Broadcast\+?|originalmente\s+publicad[ao])\s*"
    r"(?:no\s+dia\s+|em\s+)?(\d{1,2})/(\d{1,2})/(20\d{2})\s*,?\s*(?:às\s*)?(\d{1,2})[h:](\d{2})",
    flags=re.IGNORECASE,
)
REGEX_FRASE_BROADCAST_RODAPE = re.compile(r"Esta\s+(?:notícia|matéria|reportagem)\s+foi\s+publicada\s+(?:na|no)\s+Broadcast\+?\b[^.!?]*[.!?]?\s*", flags=re.IGNORECASE)
REGEX_CREDITO_FOTO_PARENTESES = re.compile(
    r"[^.!?]*\([^)]*(?:Getty Images|NurPhoto|Reuters|Bloomberg|AFP|AP Photo|Shutterstock|"
    r"Freepik|Unsplash|Estadão Conteúdo|Agência Brasil|Agência Estado|Foto:|Imagem:|Divulgação|Reprodução|Marcello Casal|Abril)[^)]*\)\.?\s*",
    flags=re.IGNORECASE,
)

MARCADORES_DESFECHO = (
    "com isso", "dessa forma", "diante disso", "nesse cenário", "neste cenário", "no acumulado",
    "por fim", "como resultado", "em reação", "após o anúncio", "para o mercado", "na avaliação",
    "a expectativa", "os papéis", "as ações", "para o investidor", "fechou em", "encerrou em",
    "operam em", "cotadas a", "impacto", "perspectiva", "próximos passos", "a decisão",
    "no ano", "nos últimos 12 meses", "segundo analistas", "pagamento", "acionistas",
)
TERMOS_URL_IMAGEM_RUIM = (
    "/logo", "logo-", "logo_", "-logo.", "_logo.", "avatar", "gravatar", "favicon", "pixel", "spacer",
    "placeholder", "default-image", "default_image", "default-og", "og-default", "og_default",
    "og-image", "og_image", "opengraph", "social-share", "social_share", "share-default",
    "-share.", "_share.", "capa-padrao", "capa_padrao", "sem-imagem", "sem_imagem", "fallback",
    "marca-valor", "valor-economico", "valoreconomico", "valor_economico", "valor-share",
    "blank.", "1x1", ".svg", ".gif", "badge", "/author/", "/perfil/", "/ads/", "banner-app",
)


@dataclass
class AnaliseHeuristica:
    score_total: int
    setor_chave: str
    categoria_label: str
    entidades: List[str] = field(default_factory=list)
    tickers: List[str] = field(default_factory=list)
    termos_detectados: List[str] = field(default_factory=list)


@dataclass
class ImagemVerificada:
    url: str
    pode_usar_preview_longo: bool
    conteudo_bytes: Optional[bytes] = None
    content_type: str = "image/jpeg"
    sha256: str = ""


@dataclass
class NoticiaPreparada:
    link: str
    titulo: str
    lead_limpo: str
    chaves_evento: List[str]
    texto_html: str
    caption_curta_html: str
    score: int
    dt_pub: datetime
    imagem: Optional[str] = None
    imagem_verificada: Optional[ImagemVerificada] = None
    video_candidato_url: Optional[str] = None
    audio: Optional[str] = None


# ==============================================================================
# DETECÇÃO DAS EMPRESAS DOS 5 SETORES + TAG LIMPA POR SETOR/EVENTO
# ==============================================================================
def _contem_termo_exato(texto_low: str, termos: Tuple[str, ...]) -> bool:
    return any(re.search(r"(?<!\w)" + re.escape(k) + r"(?!\w)", texto_low) for k in termos)


def extrair_tickers_alvo(texto: str) -> List[str]:
    return [t for t in dict.fromkeys(REGEX_TICKER_B3.findall(texto)) if t in TICKER_PARA_EMPRESA]


def formatar_tickers_no_texto(texto_html_escapado: str) -> str:
    return REGEX_TICKER_B3.sub(
        lambda m: f"<b>#{m.group(1)}</b>" if m.group(1) in TICKER_PARA_EMPRESA else m.group(0),
        texto_html_escapado,
    )


def detectar_empresas_alvo_no_texto(texto_original: str) -> List[Tuple[str, str, str]]:
    if not texto_original:
        return []
    t_low = texto_original.lower()
    encontradas: List[Tuple[str, str, str]] = []
    vistos: Set[str] = set()

    for tk in extrair_tickers_alvo(texto_original):
        ent_id, setor = TICKER_PARA_EMPRESA[tk]
        if ent_id not in vistos:
            vistos.add(ent_id)
            encontradas.append((ent_id, setor, tk))

    for ent_id, setor, termos, tickers in EMPRESAS_ALVO_B3:
        if ent_id not in vistos and _contem_termo_exato(t_low, termos):
            vistos.add(ent_id)
            encontradas.append((ent_id, setor, tickers[0]))

    for ent_id, setor, tk, rx in (
        ("banco_do_brasil", "BANCOS", "BBAS3", REGEX_BB_BANCO),
        ("vivo", "TELECOM", "VIVT3", REGEX_VIVO_OPERADORA),
        ("tim", "TELECOM", "TIMS3", REGEX_TIM_OPERADORA),
        ("light", "ENERGIA", "LIGT3", REGEX_LIGHT_ENERGIA),
    ):
        if ent_id not in vistos and rx.search(texto_original):
            vistos.add(ent_id)
            encontradas.append((ent_id, setor, tk))

    return encontradas


def extrair_protagonistas_titulo(titulo: str) -> List[Tuple[str, str, str]]:
    entidades = detectar_empresas_alvo_no_texto(titulo)
    if not entidades:
        return []
    if REGEX_RELATORIO_DE_BANCO.search(titulo):
        t_low = titulo.lower()
        eh_casa = bool(re.search(r"\b(?:itaú\s+bba|itau\s+bba|bradesco\s+bbi|bb\s+investimentos|bb-bi)\b", t_low))
        if eh_casa or not _contem_termo_exato(t_low, TERMOS_BANCO_PROTAGONISTA):
            return detectar_empresas_alvo_no_texto(REGEX_RELATORIO_DE_BANCO.sub(" ", titulo))
    return entidades


def definir_tag_curta_por_setor(titulo: str, setor_principal: str) -> str:
    t_low = titulo.lower()
    if _contem_termo_exato(t_low, ("dividendo", "dividendos", "jcp", "juros sobre capital", "proventos", "data-com", "data com", "bonificação")):
        return "🟢 <b>DIVIDENDOS</b>"
    if _contem_termo_exato(t_low, ("balanço", "lucro", "lucra", "ebitda", "prejuízo", "receita líquida", "resultado trimestral", "1t25", "2t25", "3t25", "4t25", "1t26", "2t26", "3t26", "4t26", "1t27", "2t27", "3t27", "4t27")):
        return "🔵 <b>BALANÇOS</b>"
    return SETOR_LABELS.get(setor_principal, "📊 <b>AÇÕES B3</b>")


def avaliar_impacto_financeiro(titulo: str, paragrafos: List[str]) -> AnaliseHeuristica:
    if not paragrafos:
        return AnaliseHeuristica(0, "DESCARTADA", "")
    entidades_titulo = extrair_protagonistas_titulo(titulo)
    if not entidades_titulo:
        return AnaliseHeuristica(0, "DESCARTADA", "")

    lead = paragrafos[0]
    corpo_texto = " ".join(paragrafos)
    texto_completo = f"{titulo} {corpo_texto}"
    titulo_low = re.sub(r"\breceita federal\b", "órgão tributário", titulo.lower())
    lead_low = re.sub(r"\breceita federal\b", "órgão tributário", lead.lower())
    corpo_low = re.sub(r"\breceita federal\b", "órgão tributário", corpo_texto.lower())

    ids_protag = [e[0] for e in entidades_titulo]
    tickers_exp = [tk for tk in extrair_tickers_alvo(texto_completo) if TICKER_PARA_EMPRESA[tk][0] in ids_protag]
    tickers_finais = list(dict.fromkeys(tickers_exp + [tk for _, _, tk in entidades_titulo]))

    pontos_eventos = 0
    termos_encontrados: List[str] = []
    for termo, peso in PESOS_EVENTOS_FINANCEIROS.items():
        padrao = r"(?<!\w)" + re.escape(termo) + r"(?!\w)"
        if re.search(padrao, titulo_low):
            pontos_eventos += int(peso * 1.6)
            termos_encontrados.append(termo)
        elif re.search(padrao, lead_low):
            pontos_eventos += int(peso * 1.1)
            termos_encontrados.append(termo)
        elif re.search(padrao, corpo_low):
            pontos_eventos += int(peso * 0.5)
            termos_encontrados.append(termo)

    metricas = REGEX_METRICAS_NUMERICAS.findall(texto_completo)
    if pontos_eventos == 0 and not metricas:
        return AnaliseHeuristica(0, "DESCARTADA", "")

    setor_principal = entidades_titulo[0][1]
    score_final = 14 + pontos_eventos + min(len(metricas) * 3, 18)
    return AnaliseHeuristica(
        score_total=score_final,
        setor_chave=setor_principal,
        categoria_label=definir_tag_curta_por_setor(titulo, setor_principal),
        entidades=ids_protag[:3],
        tickers=tickers_finais[:3],
        termos_detectados=list(dict.fromkeys(termos_encontrados)),
    )


# ==============================================================================
# ANTI-NOTÍCIA VELHA (HTML + BROADCAST+ + URL) E ANTIDUPLICIDADE EM 4 CAMADAS
# ==============================================================================
def _parse_iso_ou_rfc_data(texto_data: str) -> Optional[datetime]:
    if not texto_data:
        return None
    s = texto_data.strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return (dt.replace(tzinfo=TZ_BRASILIA) if dt.tzinfo is None else dt).astimezone(timezone.utc)
    except Exception:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(s)
        return (dt.replace(tzinfo=TZ_BRASILIA) if dt.tzinfo is None else dt).astimezone(timezone.utc)
    except Exception:
        return None


def extrair_data_original_html(raw_html: str) -> Optional[datetime]:
    if not raw_html:
        return None
    padroes = (
        r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']article:published_time["\']',
        r'<meta[^>]+name=["\'](?:pubdate|publish-date|publishdate|datePublished)["\'][^>]+content=["\']([^"\']+)["\']',
        r'"datePublished"\s*:\s*"([^"]+)"',
    )
    datas: List[datetime] = []
    for p in padroes:
        m = re.search(p, raw_html, re.IGNORECASE)
        if m:
            dt = _parse_iso_ou_rfc_data(m.group(1))
            if dt:
                datas.append(dt)
    m_bc = REGEX_DATA_TEXTO_BROADCAST.search(raw_html)
    if m_bc:
        try:
            d, mo, y, h, mi = (int(x) for x in m_bc.groups())
            datas.append(datetime(y, mo, d, h, mi, tzinfo=TZ_BRASILIA).astimezone(timezone.utc))
        except ValueError:
            pass
    return min(datas) if datas else None


def url_tem_data_antiga(url: str, agora_utc: datetime) -> bool:
    m = re.search(r"/(20\d{2})/(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])/", url)
    if not m:
        return False
    try:
        data_url = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=TZ_BRASILIA).date()
        hoje_brt = agora_utc.astimezone(TZ_BRASILIA)
        return (hoje_brt.date() - data_url).days > (1 if hoje_brt.hour < 2 else 0)
    except ValueError:
        return False


def normalizar_texto_dedup(texto: str) -> str:
    t = re.sub(
        r"\s+(?:-|–|—|\|)\s+(?:InfoMoney|Money Times|Seu Dinheiro|Suno|Exame|G1|Valor Investe|"
        r"Valor Econômico|Valor|E-Investidor|Estadão|Folha|CNN Brasil|UOL|Forbes|IstoÉ Dinheiro|"
        r"Bora Investir|Agência Brasil|Brazil Journal|FIIs|NeoFeed|Bloomberg|Investing).*$",
        "", html.unescape(texto or "").strip(), flags=re.IGNORECASE,
    )
    return "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c)).lower().strip()


def extrair_tokens_dedup(texto: str) -> Set[str]:
    return {p for p in re.findall(r"[a-z0-9]+", normalizar_texto_dedup(texto)) if len(p) >= 3 and p not in STOPWORDS_DEDUP and not p.isdigit()}


def extrair_numeros_relevantes(texto: str) -> Set[str]:
    return {
        m for m in re.findall(r"\d+(?:[.,]\d+)?", texto or "")
        if m.replace(".", "").replace(",", ".") not in {"2024", "2025", "2026", "2027", "2028", "2029", "2030"}
        and (len(m) >= 2 or "," in m or "." in m)
    }


def extrair_chaves_evento_ativo(titulo: str, lead: str, entidades: List[str]) -> List[str]:
    foco = normalizar_texto_dedup(f"{titulo} {lead[:220]}")
    if _contem_termo_exato(foco, ("dividendo", "dividendos", "jcp", "juros sobre capital", "proventos", "data com", "data-com", "data ex", "data-ex", "bonificacao")):
        ev = "PROVENTOS"
    elif _contem_termo_exato(foco, ("balanco", "lucro liquido", "lucra", "lucro", "ebitda", "lajida", "prejuizo", "receita liquida", "resultado trimestral", "1t25", "2t25", "3t25", "4t25", "1t26", "2t26", "3t26", "4t26")):
        ev = "BALANCO"
    elif _contem_termo_exato(foco, ("fato relevante", "fusao", "aquisicao", "recompra", "opa", "follow-on", "privatizacao", "leilao", "concessao", "recuperacao judicial", "debenture", "debentures")):
        ev = "EVENTO_CORP"
    else:
        ev = "GERAL_ATIVO"
    return [f"{ent}::{ev}" for ent in dict.fromkeys(entidades[:3])]


def evento_ja_publicado_recentemente(chaves_novas: List[str], eventos_recentes: List[Dict[str, Any]], agora_ts: float) -> Optional[str]:
    if not chaves_novas or not eventos_recentes:
        return None
    mapa: Dict[str, float] = {}
    for ev in eventos_recentes:
        c, ts = ev.get("chave"), float(ev.get("ts", 0))
        if c and ts > mapa.get(c, 0):
            mapa[c] = ts
    for ch in chaves_novas:
        if ch in mapa:
            cooldown = COOLDOWN_EVENTO_MINUTOS.get(ch.split("::", 1)[-1], 480)
            if 0 <= (agora_ts - mapa[ch]) / 60.0 <= cooldown:
                return ch
    return None


def gerar_hash_url(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    q = "&".join(f"{k}={v}" for k, v in urllib.parse.parse_qsl(p.query) if not k.lower().startswith(("utm_", "fbclid", "gclid", "ref")))
    canon = urllib.parse.urlunsplit(("https", p.netloc.lower().removeprefix("www."), p.path.rstrip("/"), q, ""))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def carregar_historico() -> Tuple[List[str], List[str], List[str], List[Dict[str, Any]], List[str]]:
    if HISTORICO_ARQUIVO.exists():
        try:
            d = json.loads(HISTORICO_ARQUIVO.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return (
                    d.get("links", []),
                    d.get("titulos", []),
                    d.get("leads", []),
                    d.get("eventos_recentes", []),
                    d.get("hashes_imagens", []),
                )
            if isinstance(d, list):
                return d, [], [], [], []
        except Exception as exc:
            logger.error("Erro ao ler histórico: %s", exc)
    return [], [], [], [], []


def salvar_historico(
    links: List[str],
    titulos: List[str],
    leads: List[str],
    eventos_recentes: List[Dict[str, Any]],
    hashes_imagens: List[str],
) -> None:
    try:
        agora_ts = datetime.now(timezone.utc).timestamp()
        dados = {
            "links": list(dict.fromkeys(links))[-MAX_HISTORICO:],
            "titulos": list(dict.fromkeys(titulos))[-MAX_HISTORICO:],
            "leads": list(dict.fromkeys(leads))[-250:],
            "eventos_recentes": [e for e in eventos_recentes if isinstance(e, dict) and (agora_ts - float(e.get("ts", 0))) <= 48 * 3600][-300:],
            "hashes_imagens": list(dict.fromkeys(hashes_imagens))[-300:],
        }
        tmp = HISTORICO_ARQUIVO.with_suffix(".tmp")
        tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, HISTORICO_ARQUIVO)
    except Exception as exc:
        logger.error("Erro ao salvar histórico: %s", exc)


def eh_mesma_noticia(novo_titulo: str, titulos_anteriores: List[str]) -> bool:
    norm_n, tok_n, num_n = normalizar_texto_dedup(novo_titulo), extrair_tokens_dedup(novo_titulo), extrair_numeros_relevantes(novo_titulo)
    if not tok_n:
        return False
    for antigo in titulos_anteriores[-120:]:
        if norm_n == normalizar_texto_dedup(antigo):
            return True
        tok_a = extrair_tokens_dedup(antigo)
        if not tok_a:
            continue
        inter = tok_n & tok_a
        menor = min(len(tok_n), len(tok_a))
        if len(inter) >= 3 and (len(inter) / menor) >= 0.42:
            return True
        if num_n and (num_n & extrair_numeros_relevantes(antigo)) and len(inter) >= 2:
            return True
    return False


def eh_mesmo_conteudo_lead(novo_lead: str, leads_anteriores: List[str]) -> bool:
    if not novo_lead or len(novo_lead) < 60:
        return False
    tok_n = extrair_tokens_dedup(novo_lead[:350])
    if len(tok_n) < 5:
        return False
    for antigo in leads_anteriores[-120:]:
        tok_a = extrair_tokens_dedup(antigo[:350])
        if len(tok_a) >= 5:
            inter = tok_n & tok_a
            if len(inter) >= 5 and (len(inter) / min(len(tok_n), len(tok_a))) >= 0.42:
                return True
    return False


# ==============================================================================
# LIMPEZA EDITORIAL, TRAFILATURA E NARRATIVA IDEAL (450 A 1.280 CARACTERES)
# ==============================================================================
def gerar_botao_chat() -> Dict[str, Any]:
    return {"inline_keyboard": [[{"text": TEXTO_BOTAO_CHAT, "url": GRUPO_URL}]]}


def normalizar_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    u = html.unescape(url.strip())
    if u.startswith("//"):
        u = "https:" + u
    if not u.startswith(("http://", "https://")):
        return None
    m_yt = REGEX_YOUTUBE_ID.search(u)
    if m_yt:
        return f"https://www.youtube.com/watch?v={m_yt.group(1)}"
    try:
        p = urllib.parse.urlsplit(u)
        return urllib.parse.urlunsplit((p.scheme, p.netloc, urllib.parse.quote(urllib.parse.unquote(p.path), safe="/:@&+$,;=-_.~"), p.query, p.fragment))
    except Exception:
        return None


def url_imagem_eh_valida_sintaticamente(url: Optional[str]) -> bool:
    if not url:
        return False
    u = url.lower()
    return u.startswith(("http://", "https://")) and not any(r in u for r in TERMOS_URL_IMAGEM_RUIM)


def extrair_data_publicacao(item: XmlElement) -> Optional[datetime]:
    for tag in ("pubDate", "{http://purl.org/dc/elements/1.1/}date", "published", "{http://www.w3.org/2005/Atom}published", "updated", "{http://www.w3.org/2005/Atom}updated"):
        el = item.find(tag)
        if el is not None and el.text:
            dt = _parse_iso_ou_rfc_data(el.text)
            if dt:
                return dt
    return None


def eh_propaganda_ou_lixo(titulo: str, inicio_texto: str, url: str = "") -> bool:
    u_low = url.lower()
    if any(dom in u_low for dom in DOMINIOS_BLOQUEADOS) or any(c in u_low for c in CAMINHOS_URL_BLOQUEADOS):
        return True
    t_low = titulo.lower()
    if re.search(r"\bfii\b", t_low) or any(b in t_low for b in BLOQUEADOS_APENAS_NO_TITULO):
        return True
    return any(b in f"{t_low} {(inicio_texto or '')[:450].lower()}" for b in BLOQUEADOS_PROPAGANDA_CORRETORA)


def limpar_caudas_wordpress(texto: str) -> str:
    return re.sub(r"(?:The post .*? appeared first on|O post .*? apareceu primeiro em|appeared first on) .*$", "", texto, flags=re.IGNORECASE).strip()


def limpar_html(txt: str) -> str:
    if not txt:
        return ""
    txt = re.sub(r"<(script|style|noscript|svg|head|nav|footer|header|aside|form|figure|figcaption)\b[^>]*>.*?</\1>", " ", txt, flags=re.DOTALL | re.IGNORECASE)
    txt = html.unescape(re.sub(r"<[^>]+>", " ", txt))
    txt = re.sub(r"https?://\S+|www\.\S+", "", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:com|br|net|org|io|gov|edu|co|be|me|tv|info)\b/\S*", "", txt, flags=re.IGNORECASE)
    txt = REGEX_FRASE_BROADCAST_RODAPE.sub(" ", REGEX_CREDITO_FOTO_PARENTESES.sub(" ", txt))
    txt = re.sub(
        r"\b\d{1,2}/\d{1,2}/\d{2,4}\s*(?:às\s*|-?\s*)?\d{1,2}[h:]\d{0,2}\b"
        r"(?:\s*[•·|\-–—]?\s*Atualizado\s*(?:há\s*|em\s*)?(?:\d+|um|uma|poucos|alguns)?\s*(?:minuto|hora|segundo|dia|mês|meses|ano)s?(?:\s+atrás)?)?",
        " ", txt, flags=re.IGNORECASE,
    )
    txt = re.sub(r"[•·|\-–—]?\s*\bAtualizado\s+(?:há\s+|em\s+)?(?:\d+|um|uma|poucos|alguns)\s*(?:minuto|hora|segundo|dia|mês|meses|ano)s?(?:\s+atrás)?\b", " ", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\s+([.,;:!?)])", r"\1", " ".join(txt.split()).strip())
    return re.sub(r"\b(R\$|US\$)(\d)", r"\1 \2", re.sub(r"([(])\s+", r"\1", txt))


def paragrafo_eh_ruido_editorial(txt: str, titulo: str) -> bool:
    if not txt or len(txt) < 40 or txt.strip().endswith("?"):
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
    return any(r in t_low for r in ruidos) or (bool(titulo) and t_low == titulo.lower().strip()) or txt.count("[") >= 3 or txt.count("]") >= 3


def extrair_paragrafos_trafilatura(raw_html: str, desc_bruta: str, titulo: str) -> List[str]:
    paragrafos: List[str] = []
    if raw_html:
        sem_leg = re.sub(r"<(figure|figcaption)\b[^>]*>.*?</\1>", " ", raw_html, flags=re.DOTALL | re.IGNORECASE)
        texto = trafilatura.extract(sem_leg, include_comments=False, include_tables=False, include_links=False, no_fallback=False, favor_precision=True)
        if texto:
            for linha in texto.splitlines():
                p = re.sub(r"^(?:Negócios|Economia|INFRA|Tecnologia|Weekend|Podcasts|Journal|Mercados|\s)+[:\-–—]?\s*", "", limpar_caudas_wordpress(limpar_html(linha)), flags=re.IGNORECASE).strip()
                if not paragrafo_eh_ruido_editorial(p, titulo):
                    paragrafos.append(p)

    if len(paragrafos) < 2 and desc_bruta:
        blocos = [limpar_caudas_wordpress(limpar_html(b)) for b in re.findall(r"<p\b[^>]*>(.*?)</p>", desc_bruta, flags=re.DOTALL | re.IGNORECASE)]
        cands = [p for p in blocos if not paragrafo_eh_ruido_editorial(p, titulo)]
        if len(cands) > len(paragrafos):
            paragrafos = cands
        if len(paragrafos) < 2:
            t_desc = limpar_caudas_wordpress(limpar_html(desc_bruta))
            frases = [f.strip() for f in re.split(r"(?<=[.!?])\s+", t_desc) if len(f.strip()) >= 35 and not f.strip().endswith("?")]
            if len(frases) >= 3:
                paragrafos = [frases[0], " ".join(frases[1:-1]), frases[-1]]
            elif len(frases) == 2:
                paragrafos = frases
            elif not paragrafos and len(t_desc) >= 60:
                paragrafos = [t_desc]
    return paragrafos


def dividir_em_frases_completas(texto: str) -> List[str]:
    texto = " ".join(REGEX_CREDITO_FOTO_PARENTESES.sub(" ", texto).split()).strip()
    if not texto:
        return []
    partes = re.split(r"(?<!\bS\.A)(?<!\bCia)(?<!\bDr)(?<!\bSr)(?<!\bSra)(?<!\bInc)(?<!\bLtd)(?<=[.!?])\s+(?=[A-ZÁÉÍÓÚÂÊÔÃÕÇ0-9\"'“‘(])", texto)
    frases: List[str] = []
    for p in partes:
        p = p.strip()
        if not p or "[…]" in p or "[...]" in p or p.endswith("?"):
            continue
        if any(c in p.lower() for c in ("getty images", "nurphoto", "shutterstock", "reprodução/", "divulgação/", "foto:")):
            continue
        if re.search(r"\b(?:de|da|do|das|dos|em|na|no|para|por|com|que|e|ou|cerca|visam|foram|está|são)\.?$", p, flags=re.IGNORECASE):
            continue
        if p[-1] not in ".!":
            p += "."
        if "”" in p and "“" not in p:
            p = "“" + p
        elif p.count('"') % 2 == 1 and not p.startswith('"'):
            p = '"' + p
        frases.append(p)
    return frases


def frase_repete_informacao(nova: str, anteriores: List[str]) -> bool:
    p_n = extrair_tokens_dedup(nova)
    if not p_n:
        return True
    m_n = {m.lower().strip() for m in REGEX_METRICAS_NUMERICAS.findall(nova)}
    for ant in anteriores:
        p_a = extrair_tokens_dedup(ant)
        if not p_a:
            continue
        prop = len(p_n & p_a) / min(len(p_n), len(p_a))
        if prop >= 0.55 or (m_n and (m_n & {m.lower().strip() for m in REGEX_METRICAS_NUMERICAS.findall(ant)}) and prop >= 0.35):
            return True
    return False


def truncar_frase_limpa(texto: str, max_chars: int) -> str:
    frases = dividir_em_frases_completas(texto)
    if not frases:
        return ""
    acumulado = ""
    for f in frases:
        cand = f"{acumulado} {f}".strip() if acumulado else f
        if len(cand) <= max_chars:
            acumulado = cand
        else:
            break
    if acumulado:
        return acumulado
    if len(frases[0]) <= max_chars + 25:
        return frases[0]
    trecho = frases[0][:max_chars]
    for sep in ("; ", ", mas ", ", enquanto ", ", quando ", ", o que ", ", segundo ", ", "):
        pos = trecho.rfind(sep)
        if pos >= int(max_chars * 0.60):
            corte = trecho[:pos].rstrip(",;:-–— ")
            if not re.search(r"\b(?:de|da|do|das|dos|em|na|no|para|por|com|que|e|ou|cerca|visam|foram|está|são)\b$", corte, flags=re.IGNORECASE):
                return corte + "."
    return ""


def pontuar_paragrafo_meio(p: str) -> int:
    return len(REGEX_METRICAS_NUMERICAS.findall(p)) * 12 + len(extrair_tickers_alvo(p)) * 18 + sum(w for t, w in PESOS_EVENTOS_FINANCEIROS.items() if t in p.lower())


def extrair_narrativa_enxuta(paragrafos: List[str], max_blocos: int = 3, max_por_bloco: int = 245) -> Optional[List[str]]:
    todas: List[str] = []
    for p in paragrafos:
        for f in dividir_em_frases_completas(p):
            if len(f) >= 35 and not frase_repete_informacao(f, todas):
                todas.append(f)
    if len(todas) < 2:
        return None
    if REGEX_INICIO_ORFAO.search(todas[0]) and not REGEX_INICIO_ORFAO.search(todas[1]):
        todas[0], todas[1] = todas[1], todas[0]

    blocos: List[str] = []
    atual = ""
    for f in todas:
        cand = f"{atual} {f}".strip() if atual else f
        if len(cand) <= max_por_bloco:
            atual = cand
        else:
            if atual and len(atual) >= 45:
                blocos.append(atual)
            atual = truncar_frase_limpa(f, max_por_bloco)
    if atual and len(atual) >= 40:
        blocos.append(atual)

    if len(blocos) < 2:
        blocos = [b for b in dict.fromkeys(truncar_frase_limpa(f, max_por_bloco) for f in todas[:4]) if b]
    if len(blocos) < 2:
        return None

    if REGEX_INICIO_ORFAO.search(blocos[0]) and not REGEX_INICIO_ORFAO.search(blocos[1]):
        blocos[0], blocos[1] = blocos[1], blocos[0]

    if max_blocos == 2 or len(blocos) == 2:
        escolhidos = [blocos[0], blocos[1]]
    else:
        cands_meio = list(range(1, min(4, len(blocos) - 1))) or [1]
        idx_meio = max(cands_meio, key=lambda i: pontuar_paragrafo_meio(blocos[i]) + (10 if i == 1 else 0) + (-50 if REGEX_INICIO_ORFAO.search(blocos[i]) else 0))
        janela_fim = blocos[idx_meio + 1 : min(idx_meio + 5, len(blocos))] or [blocos[-1]]
        p_fim = max(
            enumerate(janela_fim),
            key=lambda it: (25 if any(m in it[1].lower() for m in MARCADORES_DESFECHO) else 0) + (pontuar_paragrafo_meio(it[1]) // 2) + (10 - it[0] * 3) + (-50 if (it[0] > 0 and REGEX_INICIO_ORFAO.search(it[1])) else 0),
        )[1]
        escolhidos = [blocos[0], blocos[idx_meio], p_fim]

    unicos = [b for b in dict.fromkeys(escolhidos) if b.strip()]
    return unicos if len(unicos) >= 2 else None


def tamanho_texto_visivel(html_str: str) -> int:
    return len(html.unescape(re.sub(r"<[^>]+>", "", html_str)))


def montar_mensagem_investidor(titulo: str, paragrafos: List[str], analise: AnaliseHeuristica) -> Optional[Tuple[str, str]]:
    t_limpo = limpar_html(titulo.strip())
    if " - " in t_limpo:
        t_limpo, _ = t_limpo.rsplit(" - ", 1)
    if t_limpo and t_limpo[-1] not in ".!?":
        t_limpo += "."

    titulo_html = formatar_tickers_no_texto(html.escape(t_limpo))
    if analise.tickers:
        pills = " ".join(f"<code>#{t}</code>" for t in analise.tickers[:3])
        cabecalho = f"<blockquote>{analise.categoria_label}  ·  {pills}</blockquote>"
    else:
        cabecalho = f"<blockquote>{analise.categoria_label}</blockquote>"

    # Ponto ideal de leitura no celular: 3 parágrafos completos entre 450 e 1.280 caracteres visíveis
    post_principal: Optional[str] = None
    blocos_escolhidos: Optional[List[str]] = None
    for max_blocos, max_bloco in ((3, 340), (3, 300), (3, 260), (2, 360), (2, 290)):
        blocos = extrair_narrativa_enxuta(paragrafos, max_blocos=max_blocos, max_por_bloco=max_bloco)
        if not blocos:
            continue
        corpo = "\n\n".join(formatar_tickers_no_texto(html.escape(p)) for p in blocos)
        candidato = f"{cabecalho}\n\n<b>{titulo_html}</b>\n\n{corpo}"
        vis = tamanho_texto_visivel(candidato)
        if MIN_CARACTERES_POST <= vis <= ALVO_MAX_CARACTERES_POST:
            post_principal = candidato
            blocos_escolhidos = blocos
            break

    if not post_principal or not blocos_escolhidos:
        return None

    # Versão <= 1.004 chars (para fallback sendPhoto / sendVideo quando preview longo não for suportado)
    if tamanho_texto_visivel(post_principal) <= TELEGRAM_CAPTION_LIMIT - 20:
        caption_curta = post_principal
    else:
        caption_curta = post_principal
        for max_blocos, max_bloco in ((3, 240), (3, 200), (2, 260), (2, 210), (2, 175)):
            bc = extrair_narrativa_enxuta(paragrafos, max_blocos=max_blocos, max_por_bloco=max_bloco)
            if not bc:
                continue
            cand_c = f"{cabecalho}\n\n<b>{titulo_html}</b>\n\n" + "\n\n".join(formatar_tickers_no_texto(html.escape(p)) for p in bc)
            if tamanho_texto_visivel(cand_c) <= TELEGRAM_CAPTION_LIMIT - 20:
                caption_curta = cand_c
                break

    return post_principal, caption_curta


# ==============================================================================
# MÍDIA GARANTIDA (IMAGEM REAL OU VÍDEO YT-DLP — BLOQUEIA CAPAS/LOGOS PADRÃO)
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
    for p in padroes:
        m = re.search(p, raw_html, re.IGNORECASE)
        if m:
            u = normalizar_url(m.group(1).replace(r"\/", "/"))
            if url_imagem_eh_valida_sintaticamente(u):
                return u
    return None


def extrair_candidato_video_html(raw_html: str) -> Optional[str]:
    for p in (
        r'<meta[^>]+property=["\']og:video(?::secure_url|:url)?["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:video(?::secure_url|:url)?["\']',
        r'<meta[^>]+name=["\']twitter:player:stream["\'][^>]+content=["\']([^"\']+)["\']',
        r'<(?:source|video)[^>]+src=["\']([^"\']+\.(?:mp4|m3u8)(?:\?[^"\']*)?)["\']',
        r'"contentUrl"\s*:\s*"([^"]+\.(?:mp4|m3u8)(?:\?[^"]*)?)"',
    ):
        for m in re.finditer(p, raw_html, re.IGNORECASE):
            u = normalizar_url(m.group(1).replace(r"\/", "/"))
            if u:
                return u
    m_yt = REGEX_YOUTUBE_ID.search(raw_html)
    return f"https://www.youtube.com/watch?v={m_yt.group(1)}" if m_yt else None


def extrair_midias_do_rss(item: XmlElement, desc_bruta: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    foto, video, audio = None, None, None
    enc = item.find("enclosure")
    if enc is not None and "url" in enc.attrib:
        u, tp = normalizar_url(enc.attrib["url"]), enc.attrib.get("type", "").lower()
        if u:
            if "video" in tp or u.lower().endswith((".mp4", ".mov", ".m3u8")):
                video = u
            elif "audio" in tp or u.lower().endswith((".mp3", ".m4a", ".ogg", ".wav")):
                audio = u
            elif ("image" in tp or u.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))) and url_imagem_eh_valida_sintaticamente(u):
                foto = u

    for tag in ("{http://search.yahoo.com/mrss/}content", "{http://search.yahoo.com/mrss/}thumbnail"):
        for m in item.findall(tag):
            u = normalizar_url(m.attrib.get("url"))
            if not u:
                continue
            tp, med = m.attrib.get("type", "").lower(), m.attrib.get("medium", "").lower()
            if "video" in tp or med == "video" or u.lower().endswith((".mp4", ".m3u8")):
                video = video or u
            elif "audio" in tp or med == "audio" or u.lower().endswith(".mp3"):
                audio = audio or u
            elif not foto and url_imagem_eh_valida_sintaticamente(u):
                foto = u

    if not video and desc_bruta:
        mv = re.search(r'<(?:video|source)[^>]+src=["\']([^"\']+\.mp4[^"\']*)["\']', desc_bruta, re.IGNORECASE)
        if mv:
            video = normalizar_url(mv.group(1))
        elif (myt := REGEX_YOUTUBE_ID.search(desc_bruta)):
            video = f"https://www.youtube.com/watch?v={myt.group(1)}"

    if not foto and desc_bruta:
        for attr in ("src", "data-src", "data-lazy-src"):
            mi = re.search(rf'<img[^>]+{attr}=["\']([^"\']+)["\']', desc_bruta, re.IGNORECASE)
            if mi and url_imagem_eh_valida_sintaticamente(c := normalizar_url(mi.group(1))):
                foto = c
                break
    return foto, video, audio


async def verificar_e_preparar_imagem(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    url_imagem: Optional[str],
    referer_url: str,
    hashes_imagens_usadas: Optional[Set[str]] = None,
) -> Optional[ImagemVerificada]:
    if not url_imagem_eh_valida_sintaticamente(url_imagem):
        return None
    assert url_imagem is not None
    async with sem:
        for hdrs in (HEADERS_TELEGRAM_PREVIEW, {**HEADERS, "Referer": referer_url, "Accept": "image/jpeg,image/png,image/webp,*/*;q=0.5"}):
            try:
                resp = await client.get(url_imagem, headers=hdrs, timeout=12.0)
                ct = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if resp.status_code == 200 and MIN_IMAGE_BYTES <= len(resp.content) <= MAX_HTML_BYTES and ct in ("image/jpeg", "image/jpg", "image/png", "image/webp"):
                    img_hash = hashlib.sha256(resp.content).hexdigest()[:16]
                    if hashes_imagens_usadas is not None and img_hash in hashes_imagens_usadas:
                        logger.info("Imagem rejeitada por ser capa/logo padrão repetido de portal (%s)", url_imagem)
                        return None
                    pode_preview = (hdrs is HEADERS_TELEGRAM_PREVIEW) and ct in ("image/jpeg", "image/jpg", "image/png")
                    return ImagemVerificada(str(resp.url), pode_preview, resp.content, ct, img_hash)
            except Exception:
                pass
    return None


def _baixar_video_ytdlp_sync(url_alvo: str, referer_url: str, pasta_temp: str) -> Optional[Path]:
    ydl_opts: Dict[str, Any] = {
        "format": "best[ext=mp4][height<=720][filesize<=48M]/best[ext=mp4][height<=720][filesize_approx<=48M]/bestvideo[ext=mp4][height<=720][filesize<=40M]+bestaudio[ext=m4a][filesize<=8M]/best[ext=mp4][filesize<=48M]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "outtmpl": os.path.join(pasta_temp, "video_%(id)s.%(ext)s"),
        "max_filesize": MAX_VIDEO_BYTES,
        "noplaylist": True, "quiet": True, "no_warnings": True, "socket_timeout": 15,
        "concurrent_fragment_downloads": 4,
        "http_headers": {**HEADERS, "Referer": referer_url},
        "extractor_args": {"youtube": {"player_client": ["android", "ios", "web"]}},
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(normalizar_url(url_alvo) or url_alvo, download=True)
            if not info:
                return None
            if "entries" in info:
                entries = [e for e in info["entries"] if e]
                if not entries:
                    return None
                info = entries[0]
            if info.get("is_live"):
                return None
            caminho = Path(ydl.prepare_filename(info))
            if not caminho.exists() and caminho.with_suffix(".mp4").exists():
                caminho = caminho.with_suffix(".mp4")
            if caminho.exists():
                if 1024 < caminho.stat().st_size <= MAX_VIDEO_BYTES:
                    return caminho
                os.remove(caminho)
    except Exception:
        pass
    return None


# ==============================================================================
# INGESTÃO ASSÍNCRONA, FILTRAGEM E ENVIO AO TELEGRAM
# ==============================================================================
async def buscar_feed_rss(client: httpx.AsyncClient, sem: asyncio.Semaphore, url_feed: str) -> List[Dict[str, Any]]:
    agora_utc = datetime.now(timezone.utc)
    itens_validos: List[Dict[str, Any]] = []
    async with sem:
        try:
            resp = await client.get(url_feed, timeout=10.0)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except Exception as exc:
            logger.warning("Feed indisponível (%s): %s", url_feed, str(exc)[:100])
            return []

    itens_xml = root.findall(".//item")
    eh_atom = False
    if not itens_xml:
        itens_xml = root.findall(".//{http://www.w3.org/2005/Atom}entry") or root.findall(".//entry")
        eh_atom = bool(itens_xml)

    for item in itens_xml:
        if eh_atom:
            le = item.find("{http://www.w3.org/2005/Atom}link") or item.find("link")
            raw_link = le.attrib.get("href", "") if le is not None else ""
            te = item.find("{http://www.w3.org/2005/Atom}title") or item.find("title")
            de = item.find("{http://www.w3.org/2005/Atom}summary") or item.find("summary")
            ce = item.find("{http://www.w3.org/2005/Atom}content") or item.find("content")
        else:
            le = item.find("link")
            raw_link = le.text if le is not None and le.text else ""
            te, de = item.find("title"), item.find("description")
            ce = item.find("{http://purl.org/rss/1.0/modules/content/}encoded")

        link = normalizar_url(raw_link)
        title = html.unescape(te.text.strip()) if te is not None and te.text else ""
        td, tc = (de.text or "") if de is not None else "", (ce.text or "") if ce is not None else ""
        desc_bruta = tc if len(tc) > len(td) else td

        if not link or len(title) < 22 or url_tem_data_antiga(link, agora_utc):
            continue
        dt_pub = extrair_data_publicacao(item)
        if not dt_pub:
            continue
        idade_min = (agora_utc - dt_pub).total_seconds() / 60.0
        if idade_min > MAX_IDADE_MINUTOS or idade_min < -15.0 or eh_propaganda_ou_lixo(title, desc_bruta, link):
            continue
        if not extrair_protagonistas_titulo(title):
            continue

        itens_validos.append({"dt_pub": dt_pub, "item": item, "link": link, "title": title, "desc_bruta": desc_bruta})
    return itens_validos


async def obter_conteudo_e_midias_artigo(client: httpx.AsyncClient, sem: asyncio.Semaphore, url: str, desc_bruta: str, titulo: str) -> Tuple[List[str], Optional[str], Optional[str], Optional[datetime]]:
    foto_og, video_pag, dt_html, raw_html = None, None, None, ""
    async with sem:
        try:
            resp = await client.get(url, timeout=10.0)
            if resp.status_code == 200 and len(resp.content) <= MAX_HTML_BYTES:
                raw_html = resp.text
                foto_og = extrair_og_image_da_pagina(raw_html)
                video_pag = extrair_candidato_video_html(raw_html)
                dt_html = extrair_data_original_html(raw_html)
        except Exception:
            pass
    paragrafos = await asyncio.to_thread(extrair_paragrafos_trafilatura, raw_html, desc_bruta, titulo)
    return paragrafos, foto_og, video_pag, dt_html


async def carregar_proximas_noticias(client: httpx.AsyncClient, limite: int = MAX_NOTICIAS_POR_VEZ) -> Tuple[List[NoticiaPreparada], List[str], List[str], List[str], List[Dict[str, Any]], List[str]]:
    agora_utc = datetime.now(timezone.utc)
    agora_ts = agora_utc.timestamp()

    links_hist, titulos_hist, leads_hist, eventos_hist, hashes_img_hist = carregar_historico()
    set_links: Set[str] = set(links_hist)
    set_hashes_img: Set[str] = set(hashes_img_hist)
    titulos_rodada, leads_rodada, eventos_rodada = list(titulos_hist), list(leads_hist), list(eventos_hist)

    sem = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    resultados = await asyncio.gather(*(buscar_feed_rss(client, sem, u) for u in FEEDS))

    candidatos: List[Dict[str, Any]] = []
    hashes: Set[str] = set()
    for sub in resultados:
        for it in sub:
            lh = gerar_hash_url(it["link"])
            if it["link"] not in set_links and lh not in set_links and lh not in hashes:
                hashes.add(lh)
                candidatos.append(it)
    candidatos.sort(key=lambda x: x["dt_pub"], reverse=True)

    aprovadas: List[NoticiaPreparada] = []
    stats = {"antiga": 0, "dup_t": 0, "dup_c": 0, "sem_img": 0, "desc": 0}

    for cand in candidatos[:75]:
        if len(aprovadas) >= limite * 2:
            break
        title, link, desc_bruta, item, dt_rss = cand["title"], cand["link"], cand["desc_bruta"], cand["item"], cand["dt_pub"]

        if eh_mesma_noticia(title, titulos_rodada):
            stats["dup_t"] += 1
            continue

        foto_rss, video_rss, audio_rss = extrair_midias_do_rss(item, desc_bruta)
        paragrafos, foto_og, video_pag, dt_html = await obter_conteudo_e_midias_artigo(client, sem, link, desc_bruta, title)

        dt_real = min(dt_rss, dt_html) if dt_html else dt_rss
        idade_min = (agora_utc - dt_real).total_seconds() / 60.0
        if idade_min > MAX_IDADE_MINUTOS:
            stats["antiga"] += 1
            logger.info("Descartada (matéria antiga republicada — %.0f min): %s", idade_min, title)
            continue

        if not paragrafos:
            stats["desc"] += 1
            continue

        lead = paragrafos[0]
        if eh_mesmo_conteudo_lead(lead, leads_rodada):
            stats["dup_c"] += 1
            continue
        if eh_propaganda_ou_lixo(title, " ".join(paragrafos[:2]), link):
            stats["desc"] += 1
            continue

        analise = avaliar_impacto_financeiro(title, paragrafos)
        if analise.score_total < SCORE_MINIMO_CORTE:
            stats["desc"] += 1
            continue

        chaves_ev = extrair_chaves_evento_ativo(title, lead, analise.entidades)
        if (col := evento_ja_publicado_recentemente(chaves_ev, eventos_rodada, agora_ts)):
            stats["dup_c"] += 1
            logger.info("Descartada por duplicidade de assunto (%s): %s", col, title)
            continue

        montagem = montar_mensagem_investidor(title, paragrafos, analise)
        if not montagem:
            stats["desc"] += 1
            continue
        post_html, caption_curta_html = montagem

        img_ver: Optional[ImagemVerificada] = None
        for u_img in dict.fromkeys(u for u in (foto_og, foto_rss) if u):
            img_ver = await verificar_e_preparar_imagem(client, sem, u_img, link, set_hashes_img)
            if img_ver:
                break

        video_cand = video_rss or video_pag
        if not img_ver and not video_cand:
            stats["sem_img"] += 1
            continue

        logger.info("Aprovada [Score %d | %s | %.0f min | %d chars]: %s", analise.score_total, analise.setor_chave, idade_min, tamanho_texto_visivel(post_html), title)
        aprovadas.append(NoticiaPreparada(link, title, lead[:350], chaves_ev, post_html, caption_curta_html, analise.score_total, dt_real, img_ver.url if img_ver else None, img_ver, video_cand, audio_rss))
        titulos_rodada.append(title)
        leads_rodada.append(lead[:350])
        for ch in chaves_ev:
            eventos_rodada.append({"chave": ch, "ts": agora_ts})
        if img_ver and img_ver.sha256:
            set_hashes_img.add(img_ver.sha256)
        set_links.update((link, gerar_hash_url(link)))

    logger.info("Ciclo: %d candidatos | %d antigas | %d dup título | %d dup assunto | %d sem img | %d descartadas | %d aprovadas", len(candidatos), stats["antiga"], stats["dup_t"], stats["dup_c"], stats["sem_img"], stats["desc"], len(aprovadas))
    aprovadas.sort(key=lambda n: (n.score, n.dt_pub), reverse=True)
    selecionadas = sorted(aprovadas[:limite], key=lambda n: n.dt_pub)
    return selecionadas, links_hist, titulos_hist, leads_hist, eventos_hist, hashes_img_hist


async def telegram_request(client: httpx.AsyncClient, url: str, json_payload: Optional[Dict[str, Any]] = None, data_fields: Optional[Dict[str, Any]] = None, files: Optional[Dict[str, Any]] = None, max_retries: int = 3) -> bool:
    for tentativa in range(1, max_retries + 1):
        try:
            resp = await (client.post(url, data=data_fields, files=files, timeout=90.0) if files else client.post(url, json=json_payload, timeout=30.0))
            if resp.status_code == 200:
                return True
            if resp.status_code == 429:
                await asyncio.sleep(int(resp.json().get("parameters", {}).get("retry_after", 5)) + 1)
                continue
            logger.warning("Telegram HTTP %s em %s: %s", resp.status_code, url.rsplit("/", 1)[-1], resp.text[:200])
            return False
        except httpx.RequestError as exc:
            logger.warning("Erro de rede (%d/%d): %s", tentativa, max_retries, exc)
            await asyncio.sleep(2 * tentativa)
    return False


async def tentar_enviar_video_ytdlp(client: httpx.AsyncClient, base_url: str, chat_id: str, item: NoticiaPreparada, botao: Dict[str, Any]) -> bool:
    if not item.video_candidato_url:
        return False
    with tempfile.TemporaryDirectory(prefix="tg_vid_") as pasta_temp:
        caminho: Optional[Path] = None
        try:
            for url_alvo in dict.fromkeys((item.video_candidato_url, item.link)):
                caminho = await asyncio.to_thread(_baixar_video_ytdlp_sync, url_alvo, item.link, pasta_temp)
                if caminho and caminho.exists():
                    break
            if not caminho or not caminho.exists():
                return False
            legenda_vid = item.texto_html if tamanho_texto_visivel(item.texto_html) <= TELEGRAM_CAPTION_LIMIT - 10 else item.caption_curta_html
            data = {"chat_id": chat_id, "caption": legenda_vid, "parse_mode": "HTML", "supports_streaming": "true", "reply_markup": json.dumps(botao)}
            with open(caminho, "rb") as f_vid:
                return await telegram_request(client, f"{base_url}/sendVideo", data_fields=data, files={"video": (caminho.name, f_vid, "video/mp4")})
        finally:
            if caminho and caminho.exists():
                try:
                    os.remove(caminho)
                except OSError:
                    pass


async def tentar_enviar_foto_garantida(client: httpx.AsyncClient, base_url: str, chat_id: str, item: NoticiaPreparada, botao: Dict[str, Any]) -> bool:
    img = item.imagem_verificada
    if not img:
        return False

    texto_eh_longo = tamanho_texto_visivel(item.texto_html) > TELEGRAM_CAPTION_LIMIT - 10

    # Para textos entre ~1.000 e 1.280 chars com imagem aceita pelo TelegramBot: foto grande em cima + texto completo
    if texto_eh_longo and img.pode_usar_preview_longo:
        img_esc = html.escape(img.url, quote=True)
        payload_longo = {
            "chat_id": chat_id,
            "text": f'<a href="{img_esc}">&#8203;</a>{item.texto_html}',
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": False, "url": img.url, "prefer_large_media": True, "show_above_text": True},
            "reply_markup": botao,
        }
        if await telegram_request(client, f"{base_url}/sendMessage", json_payload=payload_longo):
            return True

    url_photo = f"{base_url}/sendPhoto"
    legenda = item.caption_curta_html if texto_eh_longo else item.texto_html
    if img.conteudo_bytes:
        ext = "png" if "png" in img.content_type else ("webp" if "webp" in img.content_type else "jpg")
        data = {"chat_id": chat_id, "caption": legenda, "parse_mode": "HTML", "reply_markup": json.dumps(botao)}
        if await telegram_request(client, url_photo, data_fields=data, files={"photo": (f"cover.{ext}", img.conteudo_bytes, img.content_type)}):
            return True
    return await telegram_request(client, url_photo, json_payload={"chat_id": chat_id, "photo": img.url, "caption": legenda, "parse_mode": "HTML", "reply_markup": botao})


async def enviar_post(client: httpx.AsyncClient, token: str, chat_id: str, item: NoticiaPreparada) -> bool:
    base_url, botao = f"https://api.telegram.org/bot{token}", gerar_botao_chat()
    if await tentar_enviar_video_ytdlp(client, base_url, chat_id, item, botao):
        return True
    if await tentar_enviar_foto_garantida(client, base_url, chat_id, item, botao):
        return True
    logger.warning("Publicação cancelada (sem mídia): %s", item.titulo)
    return False


async def main() -> None:
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not token or not chat_id:
        logger.critical("Configure TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID nos Secrets do GitHub.")
        sys.exit(1)

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, limits=httpx.Limits(max_keepalive_connections=15, max_connections=30)) as client:
        noticias, links_hist, titulos_hist, leads_hist, eventos_hist, hashes_img_hist = await carregar_proximas_noticias(client, limite=MAX_NOTICIAS_POR_VEZ)
        if not noticias:
            logger.info("Nenhuma notícia inédita e recente (<= %d min) neste ciclo.", MAX_IDADE_MINUTOS)
            return

        agora_ts = datetime.now(timezone.utc).timestamp()
        for noticia in noticias:
            if await enviar_post(client, token, chat_id, noticia):
                links_hist.extend((noticia.link, gerar_hash_url(noticia.link)))
                titulos_hist.append(noticia.titulo)
                if noticia.lead_limpo:
                    leads_hist.append(noticia.lead_limpo)
                for ch in noticia.chaves_evento:
                    eventos_hist.append({"chave": ch, "ts": agora_ts})
                if noticia.imagem_verificada and noticia.imagem_verificada.sha256:
                    hashes_img_hist.append(noticia.imagem_verificada.sha256)
                salvar_historico(links_hist, titulos_hist, leads_hist, eventos_hist, hashes_img_hist)
                logger.info("Publicado [Score %d | %d chars]: %s", noticia.score, tamanho_texto_visivel(noticia.texto_html), noticia.titulo)
                await asyncio.sleep(3.0)
            else:
                logger.error("Falha ao publicar com mídia: %s", noticia.titulo)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Execução interrompida manualmente.")
