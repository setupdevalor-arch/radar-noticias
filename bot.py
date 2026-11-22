import asyncio
import email.utils
import hashlib
import html
import json
import logging
import os
import re
import socket
import sys
import tempfile
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from xml.etree.ElementTree import Element as XmlElement

# ==============================================================================
# BLINDAGEM DE REDE IPv4-FIRST (EVITA [Errno 101] Network is unreachable NO GITHUB ACTIONS)
# ==============================================================================
_ORIG_GETADDRINFO = socket.getaddrinfo


def _getaddrinfo_ipv4_first(host: Any, port: Any, family: int = 0, type: int = 0, proto: int = 0, flags: int = 0) -> Any:
    res = _ORIG_GETADDRINFO(host, port, family, type, proto, flags)
    ipv4 = [r for r in res if r[0] == socket.AF_INET]
    return ipv4 if ipv4 else res


socket.getaddrinfo = _getaddrinfo_ipv4_first  # type: ignore[assignment]

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
GEMINI_API_KEY = (os.environ.get("GEMINI_API_KEY") or "").strip().strip('"').strip("'")
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_MODELS: Tuple[str, ...] = tuple(
    dict.fromkeys(
        m.strip()
        for m in (
            os.environ.get("GEMINI_MODEL", ""),
            "gemini-3.5-flash-lite",
            "gemini-3.5-flash-lite-preview",
            "gemini-3.1-flash-lite",
            "gemini-3.1-flash-lite-preview",
            "gemini-2.5-flash-lite",
            "gemini-flash-lite-latest",
            "gemini-3.6-flash",
            "gemini-3-flash",
            "gemini-2.5-flash",
            "gemini-flash-latest",
        )
        if m and m.strip()
    )
)
_CACHE_MODELOS_GEMINI: Dict[str, Tuple[str, ...]] = {}
_MODELO_VENCEDOR_GEMINI: Dict[str, str] = {}
_MODELOS_BLOQUEADOS_SESSAO: Dict[str, Set[str]] = {}
_MODELOS_DEGRADADOS_SESSAO: Dict[str, Set[str]] = {}
_MODELOS_GEMINI_EXCLUSAO = (
    "embedding", "imagen", "veo", "tts", "audio", "live", "robotics",
    "computer-use", "deep-research", "aqa", "gemma", "learnlm", "vision",
    "image", "customtools", "thinking-exp", "omni", "transcribe", "translate",
    "nano-banana",
)
REGEX_SIGLA_BLOQUEADA = re.compile(r"\b" + "".join(("b", "e", "s", "s", "t")) + r"\b", flags=re.IGNORECASE)

MAX_HISTORICO = 800
MAX_NOTICIAS_POR_VEZ = int(os.environ.get("MAX_NOTICIAS_POR_VEZ", "5"))
MAX_IDADE_MINUTOS = int(os.environ.get("MAX_IDADE_MINUTOS", "90"))
SCORE_MINIMO_CORTE = int(os.environ.get("SCORE_MINIMO_CORTE", "24"))
MAX_CONCURRENT_REQUESTS = 12

MAX_HTML_BYTES = 5 * 1024 * 1024
MIN_IMAGE_BYTES = 8 * 1024
MAX_VIDEO_BYTES = 50 * 1024 * 1024
TELEGRAM_CAPTION_LIMIT = 1024
MIN_TEXTO_BRUTO_PARA_IA = 280   # Evita chamar IA em trechos rasos de paywall
MIN_CARACTERES_POST = 450       # Rejeita textos rasos/cortados por paywall
ALVO_MAX_CARACTERES_POST = 1280 # Ponto ideal no celular: 3 parágrafos completos (~800 a 1.250 chars)

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
HEADERS_API_JSON = {
    "User-Agent": "RadarNoticiasBot/3.0",
    "Accept": "application/json",
    "Connection": "close",
}

# ==============================================================================
# 33 FEEDS CONFIÁVEIS (RENDA VARIÁVEL B3 + FIIs/IFIX + RENDA FIXA & MACRO)
# ==============================================================================
FEEDS = [
    # 1. Grandes portais de mercado financeiro, ações B3, FIIs e renda fixa
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
    "https://fiis.com.br/feed/",
    "https://www.spacemoney.com.br/feed/",
    "https://bmcnews.com.br/feed/",
    "https://investnews.com.br/feed/",
    "https://borainvestir.b3.com.br/feed/",
    "https://forbes.com.br/forbes-money/feed/",
    # 2. Portais especialistas de setores relevantes da B3
    "https://finsidersbrasil.com.br/feed/",
    "https://megawhat.energy/feed/",
    "https://eixos.com.br/feed/",
    "https://agenciainfra.com/blog/feed/",
    "https://www.sonhoseguro.com.br/feed/",
    "https://www.cqcs.com.br/feed/",
    "https://telesintese.com.br/feed/",
    "https://teletime.com.br/feed/",
    "https://mobiletime.com.br/feed/",
    # 3. Grandes jornais e agências econômicas nacionais
    "https://www.cnnbrasil.com.br/feed/",
    "https://www.poder360.com.br/feed/",
    "https://www.estadao.com.br/arc/outboundfeeds/feeds/rss/sections/economia/",
    "https://feeds.folha.uol.com.br/mercado/rss091.xml",
    "https://g1.globo.com/rss/g1/economia/",
    "https://agenciabrasil.ebc.com.br/rss/economia/feed.xml",
]

DOMINIOS_BLOQUEADOS = (
    "valor.globo.com",
)

CAMINHOS_URL_BLOQUEADOS = (
    "/esporte/", "/esportes/", "/sports/", "/poder-sports/", "/futebol/", "/entretenimento/",
    "/pop/", "/estilo/", "/viagem/", "/gastronomia/", "/astrologia/", "/loterias/", "/curiosidades/",
    "/tv/", "/famosos/", "/cultura/", "/poder-midia/", "/midia/", "/comportamento/",
    "/policia/", "/apostas/", "/casual/", "/ciencia/", "/blog-", "/cursos/",
    "/marketing/", "/carros/", "/autoesporte/", "/turismo/", "/saude/", "/educacao/",
    "/future-of-money/", "/cripto/", "/criptomoedas/", "/carreira/", "/inteligencia-artificial/",
)

BLOQUEADOS_PROPAGANDA_CORRETORA = (
    "renda fixa hoje:", "dentro da plataforma da xp", "plataforma da xp oferece",
    "lcas na xp", "lcis na xp", "cdbs na xp", "confira as taxas de cdbs, lcis e lcas",
    "conteúdo patrocinado", "branded content", "especial publicitário", "curso gratuito",
    "inscrições abertas", "inscricoes abertas", "webinar", "mentoria", "baixe o e-book",
    "treinamento gratuito", "evento online", "evento gratuito", "cupom de desconto",
    "turbinam benefícios", "turbina benefícios", "pontos e milhas", "acumule pontos",
    "cartão de criptomoedas", "encerra seu cartão", "cartão black", "anuidade grátis",
    # Patrocínios esportivos, estádios, clubes, shows e marketing (sem relevância p/ investidor)
    "naming rights", "patrocínio", "patrocinador", "patrocina", "estádio", "estadio",
    "morumbi", "maracanã", "maracana", "allianz parque", "neo química", "neo quimica",
    "camisa do", "uniforme do", "uniforme da", "torcida", "torcedores", "clube de futebol", "clube e banco",
    "segundo o ge", "globoesporte", "flamengo", "corinthians", "palmeiras", "vasco",
    "botafogo", "fluminense", "grêmio", "cruzeiro", "atlético-mg", "atletico-mg",
    "santos fc", "são paulo fc", "cbf", "fifa", "conmebol", "neymar", "rock in rio",
    "lollapalooza", "carnaval", "camarote", "masterchef", "campanha publicitária",
    "comercial de tv", "festival de música", "pré-venda de ingressos", "venda de ingressos",
)

BLOQUEADOS_APENAS_NO_TITULO = (
    "dona do claude", "anthropic", "openai", "chatgpt", "spacex", "elon musk", "neuralink",
    "tiktok", "bytedance", "sam altman", "bitcoin", "ethereum", "criptomoeda", "criptomoedas",
    "solana", "dogecoin", "binance", "calendário do inss", "meu inss", "benefício do inss",
    "aposentadoria do inss", "pis/pasep", "abono salarial", "bolsa família", "pé-de-meia",
    "auxílio gás", "saque-aniversário", "cnh social", "bpc/loas", "cadúnico", "cadunico",
    "auxílio-doença", "seguro-desemprego", "lote residual", "serasa limpa nome", "feirão limpa nome",
    "desenrola brasil", "leilão da receita", "apreende", "contrabando", "medalha de ouro",
    "olimpíadas", "olimpiadas", "concurso público", "edital publicado",
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
    "vale a pena comprar", "vale a pena investir", "quanto rende", "é hora de comprar",
    "é hora de investir", "como investir em", "qual ação escolher", "qual banco paga mais",
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
# COBERTURA COMPLETA: RENDA VARIÁVEL (AÇÕES B3 + BDRs + FIIs) + RENDA FIXA + ECONOMIA
# ==============================================================================
PILAR_LABELS: Dict[str, str] = {
    "ACOES": "📊 <b>AÇÕES B3</b>",
    "BDR": "🌎 <b>BDR &amp; EXTERIOR</b>",
    "FIIS": "🏢 <b>FIIs</b>",
    "RENDA_FIXA": "🏛️ <b>RENDA FIXA</b>",
    "MACRO": "🌐 <b>ECONOMIA &amp; MACRO</b>",
}

MAPA_INVESTIMENTOS_AFETADOS: Dict[str, str] = {
    "acoes": "Ações",
    "acao": "Ações",
    "acoes b3": "Ações",
    "bdr": "BDRs",
    "bdrs": "BDRs",
    "bdrs e exterior": "BDRs",
    "etf": "Renda Variável",
    "etfs": "Renda Variável",
    "fii": "FIIs",
    "fiis": "FIIs",
    "fiagro": "FIIs",
    "fundos imobiliarios": "FIIs",
    "renda fixa": "Renda Fixa",
    "tesouro direto": "Renda Fixa",
    "renda variavel": "Renda Variável",
    "economia": "Economia",
    "economia geral": "Economia",
    "macro": "Economia",
    "macroeconomia": "Economia",
}

EMPRESAS_B3_MAPA: Tuple[Tuple[str, str, Tuple[str, ...], Tuple[str, ...]], ...] = (
    # Bancos & Financeiro
    ("itau", "ACOES", ("itaú unibanco", "itau unibanco", "itaú", "itau"), ("ITUB4", "ITUB3")),
    ("itausa", "ACOES", ("itaúsa", "itausa"), ("ITSA4", "ITSA3")),
    ("banco_do_brasil", "ACOES", ("banco do brasil",), ("BBAS3",)),
    ("bradesco", "ACOES", ("bradesco",), ("BBDC4", "BBDC3")),
    ("santander", "ACOES", ("santander brasil", "santander"), ("SANB11", "SANB3", "SANB4")),
    ("btg_pactual", "ACOES", ("btg pactual", "btg"), ("BPAC11", "BPAC3", "BPAC5")),
    ("b3", "ACOES", ("b3 s.a", "bolsa brasileira b3"), ("B3SA3",)),
    ("banrisul", "ACOES", ("banrisul",), ("BRSR6", "BRSR3", "BRSR5")),
    ("abc_brasil", "ACOES", ("banco abc brasil", "banco abc", "abc brasil"), ("ABCB4",)),
    ("banco_bmg", "ACOES", ("banco bmg",), ("BMGB4",)),
    ("banco_pine", "ACOES", ("banco pine",), ("PINE4", "PINE3")),
    ("banco_mercantil", "ACOES", ("banco mercantil", "mercantil do brasil"), ("BMEB4", "BMEB3")),
    ("banco_nordeste", "ACOES", ("banco do nordeste",), ("BNBR3",)),
    ("banco_amazonia", "ACOES", ("banco da amazônia", "banco da amazonia"), ("BAZA3",)),
    ("banestes", "ACOES", ("banestes",), ("BEES3", "BEES4")),
    ("brb", "ACOES", ("banco de brasília", "banco de brasilia", "banco brb"), ("BSLI3", "BSLI4")),
    ("banese", "ACOES", ("banese",), ("BGIP4", "BGIP3")),
    ("br_partners", "ACOES", ("br partners",), ("BRBI11",)),
    ("nubank", "ACOES", ("nubank", "nu holdings"), ("ROXO34",)),
    ("banco_inter", "ACOES", ("banco inter", "inter&co"), ("INBR32",)),
    ("xp_inc", "ACOES", ("xp inc",), ("XPBR31",)),
    # Petróleo, Mineração, Siderurgia & Papel
    ("petrobras", "ACOES", ("petrobras",), ("PETR4", "PETR3")),
    ("vale", "ACOES", ("vale s.a", "mineradora vale"), ("VALE3",)),
    ("prio", "ACOES", ("prio", "petrorio"), ("PRIO3",)),
    ("brava", "ACOES", ("brava energia", "3r petroleum"), ("BRAV3",)),
    ("petroreconcavo", "ACOES", ("petroreconcavo", "petrorecôncavo"), ("RECV3",)),
    ("vibra", "ACOES", ("vibra energia",), ("VBBR3",)),
    ("ultrapar", "ACOES", ("ultrapar",), ("UGPA3",)),
    ("cosan", "ACOES", ("cosan",), ("CSAN3",)),
    ("raizen", "ACOES", ("raízen", "raizen"), ("RAIZ4",)),
    ("suzano", "ACOES", ("suzano",), ("SUZB3",)),
    ("klabin", "ACOES", ("klabin",), ("KLBN11", "KLBN4", "KLBN3")),
    ("gerdau", "ACOES", ("gerdau",), ("GGBR4", "GOAU4")),
    ("csn", "ACOES", ("csn mineração", "csn mineracao", "companhia siderúrgica nacional"), ("CSNA3", "CMIN3")),
    ("usiminas", "ACOES", ("usiminas",), ("USIM5",)),
    ("braskem", "ACOES", ("braskem",), ("BRKM5",)),
    # Energia Elétrica, Saneamento & Ambiental
    ("eletrobras", "ACOES", ("axia energia", "eletrobras", "furnas", "chesf", "eletronorte"), ("AXIA3", "ELET3", "ELET6")),
    ("engie", "ACOES", ("engie brasil", "engie"), ("EGIE3",)),
    ("taesa", "ACOES", ("taesa",), ("TAEE11", "TAEE3", "TAEE4")),
    ("isa_energia", "ACOES", ("isa energia", "isa cteep", "cteep", "transmissão paulista", "transmissao paulista"), ("ISAE4", "ISAE3")),
    ("alupar", "ACOES", ("alupar",), ("ALUP11", "ALUP3", "ALUP4")),
    ("cemig", "ACOES", ("cemig",), ("CMIG4", "CMIG3")),
    ("copel", "ACOES", ("copel",), ("CPLE3", "CPLE6")),
    ("cpfl", "ACOES", ("cpfl energia", "cpfl"), ("CPFE3",)),
    ("equatorial", "ACOES", ("equatorial energia",), ("EQTL3",)),
    ("energisa", "ACOES", ("energisa",), ("ENGI11", "ENGI3", "ENGI4")),
    ("coelba", "ACOES", ("coelba", "neoenergia coelba"), ("CEEB3", "CEEB5")),
    ("celpe", "ACOES", ("celpe", "neoenergia pernambuco", "companhia energética de pernambuco"), ("CEPE5", "CEPE3", "CEPE6")),
    ("cosern", "ACOES", ("cosern", "neoenergia cosern"), ("CSRN3", "CSRN5", "CSRN6")),
    ("elektro", "ACOES", ("elektro", "neoenergia elektro"), ("EKTR4", "EKTR3")),
    ("neoenergia", "ACOES", ("neoenergia",), ("NEOE3",)),
    ("serena", "ACOES", ("serena energia", "omega energia"), ("SRNA3",)),
    ("auren", "ACOES", ("auren energia", "auren", "aes brasil"), ("AURE3",)),
    ("eneva", "ACOES", ("eneva",), ("ENEV3",)),
    ("light", "ACOES", ("light energia", "light s.a", "distribuidora light", "grupo light"), ("LIGT3",)),
    ("celesc", "ACOES", ("celesc",), ("CLSC4", "CLSC3")),
    ("emae", "ACOES", ("emae",), ("EMAE4", "EMAE3")),
    ("ceb", "ACOES", ("ceb geração", "ceb distribuição", "companhia energética de brasília"), ("CEBR3", "CEBR5", "CEBR6")),
    ("rede_energia", "ACOES", ("rede energia",), ("REDE3",)),
    ("renova", "ACOES", ("renova energia",), ("RNEW3", "RNEW4", "RNEW11")),
    ("sabesp", "ACOES", ("sabesp",), ("SBSP3",)),
    ("sanepar", "ACOES", ("sanepar",), ("SAPR11", "SAPR4", "SAPR3")),
    ("copasa", "ACOES", ("copasa",), ("CSMG3",)),
    ("ambipar", "ACOES", ("ambipar",), ("AMBP3",)),
    # Seguros, Telecom & Tecnologia
    ("bb_seguridade", "ACOES", ("bb seguridade",), ("BBSE3",)),
    ("caixa_seguridade", "ACOES", ("caixa seguridade",), ("CXSE3",)),
    ("porto_seguro", "ACOES", ("porto s.a", "porto bank", "porto saúde", "porto saude", "seguradora porto"), ("PSSA3",)),
    ("irb", "ACOES", ("irb brasil", "irb(re)", "irb re"), ("IRBR3",)),
    ("vivo", "ACOES", ("telefônica brasil", "telefonica brasil", "operadora vivo", "vivo fibra"), ("VIVT3",)),
    ("tim", "ACOES", ("tim brasil", "tim s.a", "operadora tim"), ("TIMS3",)),
    ("brisanet", "ACOES", ("brisanet",), ("BRST3",)),
    ("unifique", "ACOES", ("unifique",), ("FIQE3",)),
    ("desktop", "ACOES", ("desktop s.a", "operadora desktop", "desktop internet"), ("DESK3",)),
    ("telebras", "ACOES", ("telebras", "telebrás"), ("TELB4", "TELB3")),
    ("allied", "ACOES", ("allied tecnologia", "allied s.a", "allied brasil"), ("ALLD3",)),
    ("intelbras", "ACOES", ("intelbras",), ("INTB3",)),
    ("totvs", "ACOES", ("totvs",), ("TOTS3",)),
    ("lwsa", "ACOES", ("locaweb", "lwsa"), ("LWSA3",)),
    # Indústria, Logística & Transporte
    ("weg", "ACOES", ("weg",), ("WEGE3",)),
    ("embraer", "ACOES", ("embraer",), ("EMBR3",)),
    ("marcopolo", "ACOES", ("marcopolo",), ("POMO4",)),
    ("randon", "ACOES", ("randoncorp", "randon"), ("RAPT4",)),
    ("localiza", "ACOES", ("localiza",), ("RENT3",)),
    ("rumo", "ACOES", ("rumo logística", "rumo s.a", "rumo logistica"), ("RAIL3",)),
    ("motiva", "ACOES", ("motiva infra", "ccr s.a", "grupo ccr"), ("MOTV3", "CCRO3")),
    ("santos_brasil", "ACOES", ("santos brasil",), ("STBP3",)),
    ("azul", "ACOES", ("azul linhas aéreas", "azul s.a"), ("AZUL4",)),
    ("gol", "ACOES", ("gol linhas aéreas",), ("GOLL4",)),
    # Consumo, Alimentos, Varejo, Saúde & Construção
    ("ambev", "ACOES", ("ambev",), ("ABEV3",)),
    ("jbs", "ACOES", ("jbs",), ("JBSS3",)),
    ("marfrig", "ACOES", ("marfrig",), ("MRFG3",)),
    ("brf", "ACOES", ("brf",), ("BRFS3",)),
    ("minerva", "ACOES", ("minerva foods",), ("BEEF3",)),
    ("slc_agricola", "ACOES", ("slc agrícola", "slc agricola"), ("SLCE3",)),
    ("magazine_luiza", "ACOES", ("magazine luiza", "magalu"), ("MGLU3",)),
    ("lojas_renner", "ACOES", ("lojas renner",), ("LREN3",)),
    ("assai", "ACOES", ("assaí atacadista", "assai atacadista", "assaí"), ("ASAI3",)),
    ("carrefour_br", "ACOES", ("carrefour brasil", "atacadão"), ("CRFB3",)),
    ("mercado_livre", "ACOES", ("mercado livre",), ("MELI34",)),
    ("natura", "ACOES", ("natura&co", "natura &co"), ("NTCO3",)),
    ("raia_drogasil", "ACOES", ("raia drogasil", "rd saúde", "rd saude"), ("RADL3",)),
    ("rede_dor", "ACOES", ("rede d'or", "rede dor"), ("RDOR3",)),
    ("hapvida", "ACOES", ("hapvida",), ("HAPV3",)),
    ("fleury", "ACOES", ("grupo fleury", "fleury"), ("FLRY3",)),
    ("hypera", "ACOES", ("hypera pharma", "hypera"), ("HYPE3",)),
    ("cyrela", "ACOES", ("cyrela",), ("CYRE3",)),
    ("mrv", "ACOES", ("mrv&co", "mrv engenharia"), ("MRVE3",)),
    ("eztec", "ACOES", ("eztec",), ("EZTC3",)),
    ("direcional", "ACOES", ("direcional engenharia", "direcional"), ("DIRR3",)),
    ("cury", "ACOES", ("cury construtora",), ("CURY3",)),
    ("multiplan", "ACOES", ("multiplan",), ("MULT3",)),
    ("allos", "ACOES", ("allos",), ("ALOS3",)),
    ("iguatemi", "ACOES", ("iguatemi",), ("IGTI11",)),
    # Principais FIIs e FIAGROs (IFIX)
    ("mxrf11", "FIIS", ("maxi renda",), ("MXRF11",)),
    ("hglg11", "FIIS", ("pátria logística", "cshg logística"), ("HGLG11",)),
    ("xpml11", "FIIS", ("xp malls",), ("XPML11",)),
    ("knri11", "FIIS", ("kinea renda imobiliária",), ("KNRI11",)),
    ("kncr11", "FIIS", ("kinea rendimentos imobiliários",), ("KNCR11",)),
    ("knip11", "FIIS", ("kinea índices de preços",), ("KNIP11",)),
    ("knsc11", "FIIS", ("kinea securities",), ("KNSC11",)),
    ("visc11", "FIIS", ("vinci shopping centers",), ("VISC11",)),
    ("btlg11", "FIIS", ("btg pactual logística",), ("BTLG11",)),
    ("hgru11", "FIIS", ("pátria renda urbana", "cshg renda urbana"), ("HGRU11",)),
    ("trxf11", "FIIS", ("trx real estate",), ("TRXF11",)),
    ("cpts11", "FIIS", ("capitânia securities",), ("CPTS11",)),
    ("irdm11", "FIIS", ("iridium recebíveis",), ("IRDM11",)),
    ("vghf11", "FIIS", ("valora hedge fund",), ("VGHF11",)),
    ("hgbs11", "FIIS", ("hedge brasil shopping",), ("HGBS11",)),
    ("vilg11", "FIIS", ("vinci logística",), ("VILG11",)),
    ("lvbi11", "FIIS", ("vbi logístico",), ("LVBI11",)),
    ("pvbi11", "FIIS", ("vbi prime properties",), ("PVBI11",)),
    ("hgre11", "FIIS", ("pátria escritórios", "cshg real estate"), ("HGRE11",)),
    ("xplg11", "FIIS", ("xp log",), ("XPLG11",)),
    ("alzr11", "FIIS", ("alianza trust renda imobiliária",), ("ALZR11",)),
    ("brco11", "FIIS", ("bresco logística",), ("BRCO11",)),
    ("gare11", "FIIS", ("guardian real estate",), ("GARE11",)),
    ("rztr11", "FIIS", ("riza terrax",), ("RZTR11",)),
    ("snag11", "FIIS", ("suno agro",), ("SNAG11",)),
    ("knca11", "FIIS", ("kinea crédito agro",), ("KNCA11",)),
    ("vgia11", "FIIS", ("valora cra",), ("VGIA11",)),
    # Principais BDRs e ETFs negociados na B3
    ("apple", "BDR", ("apple",), ("AAPL34",)),
    ("microsoft", "BDR", ("microsoft",), ("MSFT34",)),
    ("nvidia", "BDR", ("nvidia",), ("NVDC34",)),
    ("alphabet", "BDR", ("alphabet",), ("GOGL34",)),
    ("amazon", "BDR", ("amazon",), ("AMZO34",)),
    ("meta_platforms", "BDR", ("meta platforms",), ("M1TA34",)),
    ("tesla", "BDR", ("tesla",), ("TSLA34",)),
    ("berkshire", "BDR", ("berkshire hathaway", "warren buffett"), ("BERK34",)),
    ("amd", "BDR", ("advanced micro devices",), ("A1MD34",)),
    ("intel", "BDR", ("intel corp", "intel corporation"), ("ITLC34",)),
    ("broadcom", "BDR", ("broadcom",), ("AVGO34",)),
    ("oracle", "BDR", ("oracle",), ("ORCL34",)),
    ("tsmc", "BDR", ("tsmc", "taiwan semiconductor"), ("TSMC34",)),
    ("netflix", "BDR", ("netflix",), ("NFLX34",)),
    ("disney", "BDR", ("walt disney", "disney"), ("DISB34",)),
    ("coca_cola", "BDR", ("coca-cola", "the coca-cola"), ("COCA34",)),
    ("jpmorgan", "BDR", ("jpmorgan", "jp morgan"), ("JPMC34",)),
    ("bank_of_america", "BDR", ("bank of america",), ("BOAC34",)),
    ("goldman_sachs", "BDR", ("goldman sachs",), ("GSGI34",)),
    ("visa", "BDR", ("visa inc",), ("VISA34",)),
    ("mastercard", "BDR", ("mastercard",), ("MSCD34",)),
    ("walmart", "BDR", ("walmart",), ("WALM34",)),
    ("mcdonalds", "BDR", ("mcdonald's", "mcdonalds"), ("MCDC34",)),
    ("exxon", "BDR", ("exxonmobil", "exxon mobil"), ("EXXO34",)),
    ("chevron", "BDR", ("chevron",), ("CHVX34",)),
    ("alibaba", "BDR", ("alibaba",), ("BABA34",)),
    ("eli_lilly", "BDR", ("eli lilly",), ("LILY34",)),
    ("novo_nordisk", "BDR", ("novo nordisk",), ("N1VO34",)),
    ("ivvb11", "BDR", ("ivvb11",), ("IVVB11",)),
    ("nasd11", "BDR", ("nasd11",), ("NASD11",)),
    ("bova11", "ACOES", ("bova11",), ("BOVA11",)),
    ("smal11", "ACOES", ("smal11",), ("SMAL11",)),
    ("divo11", "ACOES", ("divo11",), ("DIVO11",)),
)

TICKER_PARA_EMPRESA: Dict[str, Tuple[str, str]] = {
    tk: (ent_id, pilar) for ent_id, pilar, _, tickers in EMPRESAS_B3_MAPA for tk in tickers
}

FALSOS_TICKERS: Set[str] = {
    "HTML5", "HTTP3", "HTTP4", "WIFI6", "IPV6", "FASE3", "FASE4", "TIPO3", "TIPO4",
    "LOTE3", "LOTE4", "AREA3", "AREA4", "ZONA3", "ZONA4", "ITEM3", "ITEM4", "PART3",
    "CASE3", "LIVE3", "DATA4", "INFO4", "TEST3", "REAL3", "META4", "SALA4", "ETAP4",
    "IFRS11", "NOTA11", "TOP11",
}

TERMOS_BDR = (
    "bdr", "bdrs", "s&p 500", "nasdaq", "dow jones", "wall street", "nyse",
    "bolsas de nova york", "bolsas dos eua", "bolsas americanas", "etf", "etfs",
    "ações americanas", "big techs", "dividendos em dólar",
)
TERMOS_FIIS = (
    "fundo imobiliário", "fundos imobiliários", "fundo imobiliario", "fundos imobiliarios",
    "fii", "fiis", "ifix", "fiagro", "fiagros", "rendimento por cota", "por cota", "cotistas",
    "galpões logísticos", "lajes corporativas", "vacância", "p/vp", "emissão de cotas",
)
TERMOS_RENDA_FIXA = (
    "tesouro direto", "tesouro ipca", "tesouro selic", "tesouro prefixado", "tesouro renda+",
    "tesouro educa+", "ntn-b", "renda fixa", "cdb", "cdbs", "lci", "lcis", "lca", "lcas",
    "cri", "cris", "cra", "cras", "debênture", "debêntures", "debênture incentivada",
    "debêntures incentivadas", "cdi", "di futuro", "curva de juros", "juros futuros",
    "títulos públicos", "titulo publico", "título público", "fgc", "fundo garantidor",
    "isenção de ir", "tributação de lci", "tributação de lca", "juro real", "taxa real",
)
TERMOS_MACRO_JUROS = (
    "selic", "copom", "ata do copom", "ipca", "ipca-15", "igp-m", "inflação", "boletim focus",
    "relatório focus", "banco central", "galípolo", "galipolo", "taxa básica de juros",
    "taxa de juros", "arcabouço fiscal", "meta fiscal", "dívida pública", "déficit fiscal",
    "superávit primário", "déficit primário", "pacote fiscal", "corte de gastos",
    "arrecadação federal", "contas públicas", "gastos públicos", "imposto de renda", "iof",
    "ministério da fazenda", "haddad", "orçamento público", "orçamento federal",
    "reforma tributária", "ibovespa", "federal reserve", "fomc", "juros dos eua", "treasuries",
    "treasury", "payroll", "cpi", "pce", "inflação nos eua", "economia dos eua",
    "economia americana", "economia chinesa", "economia da china", "banco central europeu",
    "guerra comercial", "tarifas comerciais", "tarifas de importação", "tarifaço", "recessão",
    "pib", "pib do brasil", "crescimento do pib", "ibc-br", "atividade econômica",
    "produção industrial", "vendas no varejo", "setor de serviços", "caged", "empregos formais",
    "taxa de desemprego", "pnad", "pmi", "balança comercial", "exportações", "importações",
    "conta corrente", "fluxo estrangeiro", "investimento estrangeiro", "petróleo", "brent",
    "opep", "minério de ferro", "commodities", "dólar", "câmbio", "poupança",
)

REGEX_BB_BANCO = re.compile(r"\bBB\b(?!\s*(?:Seguridade|Investimentos|BI|-BI|Asset|DTVM|Consórcios|Previdência))")
REGEX_VALE_MINERADORA = re.compile(
    r"(?<!\bdo\s)(?<!\bno\s)(?<!\bao\s)\bVale\b(?!\s+(?:do\s+|da\s+|a\s+pena|lembrar|destacar|ressaltar|dizer|notar|alimenta|refei|transporte|presente|cultura|gás|gas|combust|reforçar|salientar))"
)
REGEX_PORTO_SEGURO = re.compile(r"(?<!\bcomo\s)(?<!\bum\s)(?<!\bo\s)(?<!\bde\s)(?<!\bem\s)(?<!\bComo\s)(?<!\bUm\s)\bPorto Seguro\b")
REGEX_EQUATORIAL = re.compile(r"(?<!Guiné\s)(?<!Guinea\s)(?<!clima\s)(?<!região\s)(?<!floresta\s)(?<!zona\s)(?<!linha\s)\bEquatorial\b")
REGEX_IRB = re.compile(r"\bIRB\b")
REGEX_VIVO_OPERADORA = re.compile(
    r"(?<!\bao\s)(?<!\bAo\s)(?<!\bem\s)(?<!\bser\s)(?<!\bestar\s)(?<!\bestá\s)(?<!\bsegue\s)(?<!\bmais\s)\bVivo\b(?!\s+ou\s+morto)"
    r"|(?<!conversa\s)(?<!ligação\s)(?<!ligacao\s)(?<!chamada\s)(?<!linha\s)(?<!rede\s)(?<!central\s)(?<!entrevista\s)\bTelef[ôo]nica\b"
)
REGEX_TIM_OPERADORA = re.compile(r"\bTIM\b(?!\s+(?:Cook|Berners|Burton|Maia|Hortons|Robbins|Walz|Kaine))")
REGEX_LIGHT_ENERGIA = re.compile(r"\bLight\b(?=.*(?:energia|distribuidor|concess|aneel|tarifa|dívida|recuperação|ações|lucro|balanço|rio))", flags=re.IGNORECASE)
REGEX_ALLIED_TECH = re.compile(r"\bAllied\b(?=.*(?:lucro|lucra|balanço|ebitda|receita|dividendo|jcp|proventos|ações|b3|cvm|varejo|eletrônicos|smartphones|distribuição|dívida|trimestre|1t2|2t2|3t2|4t2))", flags=re.IGNORECASE)
REGEX_GOOGLE_FIN = re.compile(r"\bGoogle\b(?=.*(?:ações|ação|bdr|gogl34|lucro|lucra|balanço|receita|ebitda|dividendo|recompra|wall street|nasdaq|antitruste|trimestre|bilhões|mercado))", flags=re.IGNORECASE)
REGEX_AMD_FIN = re.compile(r"\bAMD\b(?=.*(?:ações|ação|bdr|a1md34|lucro|lucra|balanço|receita|ebitda|wall street|nasdaq|chips|semicondutores|trimestre|bilhões|guidance))", flags=re.IGNORECASE)

# Evita rotular uma matéria de outra empresa/macro com o ticker do banco que assinou o relatório
REGEX_RELATORIO_DE_BANCO = re.compile(
    r"\b(?:itaú\s+bba|itau\s+bba|bradesco\s+bbi|bb\s+investimentos|bb-bi|btg\s+pactual\s+digital|xp\s+investimentos|genial\s+investimentos)\b"
    r"|\b(?:btg(?:\s+pactual)?|itaú|itau|bradesco|santander|banco\s+do\s+brasil|jpmorgan|jp\s+morgan|bank\s+of\s+america|goldman\s+sachs)\s+"
    r"(?:recomenda|rebaixa|eleva\s+(?:recomendação|preço-alvo|preço|target|nota|rating|projeção|estimativa|peso|ações|papéis|a\s+ação|as\s+ações)|"
    r"corta\s+(?:recomendação|preço-alvo|preço|target|nota|rating|projeção|estimativa|peso|ações|papéis)|"
    r"reduz\s+preço|aumenta\s+preço|revisa\s+(?:recomendação|preço|carteira|projeção|estimativa)|"
    r"(?:vê|projeta|prevê)\s+(?:potencial|upside|oportunidade\s+em|selic|dólar|ibovespa|pib|inflação|ipca|s&p\s*500|"
    r"(?:alta|queda)\s+(?:de\s+[\d.,]+%?\s+)?(?:para|em|das\s+ações|nos\s+papéis|do\s+dólar|da\s+selic|do\s+ibovespa))|"
    r"aposta\s+em|escolhe\s+|indica\s+|lista\s+|inclui\s+|retira\s+|remove\s+|tira\s+|"
    r"mantém\s+(?:compra|venda|neutro|recomendação)|inicia\s+cobertura|atualiza\s+carteira|"
    r"diz\s+que\s+(?:a\s+selic|o\s+dólar|o\s+ibovespa|o\s+pib|a\s+inflação))\b"
    r"|\b(?:segundo|para|na\s+visão\s+do|em\s+relatório\s+do|carteira\s+(?:do|da))\s+(?:o\s+)?(?:btg(?:\s+pactual)?|itaú|itau|bradesco|santander|bb|jpmorgan|jp\s+morgan|bank\s+of\s+america|goldman\s+sachs)\b",
    flags=re.IGNORECASE,
)

PESOS_EVENTOS_FINANCEIROS: Dict[str, int] = {
    # Proventos & Ações
    "dividendos": 22, "dividendo": 22, "jcp": 24, "juros sobre capital próprio": 24,
    "juros sobre o capital próprio": 24, "proventos": 20, "data-com": 24, "data com": 22,
    "data-ex": 20, "dividend yield": 20, "payout": 18, "bonificação": 16, "balanço": 20,
    "lucro líquido": 22, "lucro": 18, "lucra": 18, "ebitda": 22, "lajida": 20, "prejuízo": 18,
    "receita líquida": 18, "receita bruta": 14, "margem": 14, "roe": 18,
    "inadimplência": 16, "carteira de crédito": 18, "geração de caixa": 16, "guidance": 18,
    "fato relevante": 24, "fusão": 18, "aquisição": 18, "recompra": 20, "opa": 20, "follow-on": 20,
    "privatização": 18, "concessão": 18, "leilão": 18, "aneel": 16, "tarifa": 14,
    "reajuste tarifário": 18, "revisão tarifária": 18, "anatel": 16, "susep": 16, "cvm": 16,
    "capex": 18, "investimento": 14, "investimentos": 14, "expansão": 16,
    "dívida": 14, "alavancagem": 16, "recuperação judicial": 18, "rating": 16,
    "preço-alvo": 18, "ações": 14, "ação": 12, "papéis": 12, "acionistas": 16, "b3": 14,
    "1t2": 16, "2t2": 16, "3t2": 16, "4t2": 16,
    # BDRs, ETFs & Renda Variável Global
    "bdr": 22, "bdrs": 22, "etf": 20, "etfs": 20, "s&p 500": 20, "nasdaq": 20, "dow jones": 18,
    "wall street": 18, "big techs": 18, "bolsas americanas": 18, "bolsas de nova york": 18,
    "small caps": 18, "renda variável": 18, "desdobramento": 20, "split": 18,
    # FIIs & FIAGROs
    "fundo imobiliário": 22, "fundos imobiliários": 22, "fii": 20, "fiis": 20, "ifix": 22,
    "fiagro": 22, "rendimento por cota": 24, "por cota": 18, "cotistas": 16, "vacância": 20,
    "emissão de cotas": 22, "p/vp": 16, "galpões logísticos": 16, "lajes corporativas": 16,
    # Renda Fixa, Juros & Economia Geral
    "tesouro direto": 24, "tesouro ipca": 24, "tesouro selic": 22, "tesouro prefixado": 22,
    "ntn-b": 22, "renda fixa": 20, "cdb": 18, "lci": 20, "lca": 20, "cri": 18, "cra": 18,
    "debêntures": 22, "debênture": 20, "cdi": 18, "di futuro": 22, "curva de juros": 22,
    "juros futuros": 22, "títulos públicos": 22, "fgc": 22, "isenção de ir": 22, "juro real": 20,
    "poupança": 16, "selic": 24, "copom": 26, "ata do copom": 24, "ipca": 24, "ipca-15": 22,
    "igp-m": 18, "inflação": 18, "boletim focus": 22, "banco central": 18, "taxa básica de juros": 22,
    "taxa de juros": 20, "arcabouço fiscal": 20, "meta fiscal": 20, "dívida pública": 18,
    "arrecadação federal": 18, "contas públicas": 18, "gastos públicos": 18, "imposto de renda": 18,
    "iof": 18, "corte de gastos": 20, "reforma tributária": 18, "federal reserve": 20, "fomc": 20,
    "treasuries": 18, "payroll": 18, "cpi": 20, "pce": 20, "inflação nos eua": 20,
    "economia dos eua": 18, "economia americana": 18, "economia chinesa": 18, "economia da china": 18,
    "banco central europeu": 18, "guerra comercial": 18, "tarifas comerciais": 18,
    "tarifas de importação": 18, "tarifaço": 18, "recessão": 18,
    "pib": 20, "ibc-br": 22, "atividade econômica": 18, "produção industrial": 18,
    "vendas no varejo": 18, "setor de serviços": 16, "caged": 20, "empregos formais": 18,
    "taxa de desemprego": 18, "pnad": 18, "pmi": 18, "balança comercial": 18,
    "exportações": 14, "importações": 14, "conta corrente": 18, "fluxo estrangeiro": 20,
    "investimento estrangeiro": 18, "petróleo": 16, "brent": 18, "opep": 18,
    "minério de ferro": 20, "commodities": 16, "ibovespa": 16, "dólar": 14, "câmbio": 14,
}

COOLDOWN_EVENTO_MINUTOS: Dict[str, int] = {
    "PROVENTOS": 1080, "BALANCO": 1440, "EVENTO_CORP": 1080, "GERAL_ATIVO": 480,
    "MACRO_EVENTO": 240, "RF_EVENTO": 300, "FIIS_GERAL": 360, "BDR_GERAL": 300,
}

REGEX_TICKER_B3 = re.compile(r"\b((?:[A-Z]{4}|[A-Z]\d[A-Z]{2})(?:3|4|5|6|11|31|32|33|34|35|39))\b")
REGEX_METRICAS_NUMERICAS = re.compile(
    r"([+-]?\d+(?:[.,]\d+)?\s*%|\b(?:R\$|US\$|€|£)\s*\d+(?:[.,]\d+)?|\b\d+(?:[.,]\d+)?\s*(?:milhões|bilhões|trilhões|mil|bi|tri|pontos-base|bps|pontos)\b)",
    flags=re.IGNORECASE,
)
REGEX_YOUTUBE_ID = re.compile(r"(?:youtube\.com/(?:watch\?v=|embed/|shorts/|live/)|youtu\.be/)([A-Za-z0-9_-]{11})", flags=re.IGNORECASE)
REGEX_INICIO_ORFAO = re.compile(
    r"^(?:A companhia\b|A empresa\b|O banco\b|A elétrica\b|A operadora\b|A seguradora\b|A instituição\b|O fundo\b|"
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
    "no ano", "nos últimos 12 meses", "segundo analistas", "pagamento", "acionistas", "cotistas",
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
    pilar_chave: str
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
# DETECÇÃO AMPLA (AÇÕES B3 + BDRs/ETFs + FIIs/IFIX + RENDA FIXA + ECONOMIA)
# ==============================================================================
def _contem_termo_exato(texto_low: str, termos: Tuple[str, ...]) -> bool:
    return any(re.search(r"(?<!\w)" + re.escape(k) + r"(?!\w)", texto_low) for k in termos)


def extrair_tickers_validos(texto: str) -> List[str]:
    if not texto:
        return []
    t_low = texto.lower()
    tem_contexto_fin = any(
        k in t_low for k in ("ação", "ações", "papel", "papéis", "bdr", "bdrs", "etf", "etfs", "fii", "fiagro", "cota", "dividendo", "jcp", "lucro", "balanço", "ebitda", "b3", "cvm", "provento", "emissão", "wall street", "nasdaq")
    )
    res: List[str] = []
    for t in dict.fromkeys(REGEX_TICKER_B3.findall(texto)):
        if t in FALSOS_TICKERS:
            continue
        if t in TICKER_PARA_EMPRESA or tem_contexto_fin or f"({t})" in texto:
            res.append(t)
    return res


def formatar_tickers_no_texto(texto_html_escapado: str) -> str:
    return REGEX_TICKER_B3.sub(
        lambda m: f"<b>#{m.group(1)}</b>" if (m.group(1) not in FALSOS_TICKERS and (m.group(1) in TICKER_PARA_EMPRESA or f"({m.group(1)})" in texto_html_escapado)) else m.group(0),
        texto_html_escapado,
    )


def detectar_ativos_b3_no_texto(texto_original: str) -> List[Tuple[str, str, str]]:
    if not texto_original:
        return []
    t_low = texto_original.lower()
    encontradas: List[Tuple[str, str, str]] = []
    vistos: Set[str] = set()

    for tk in extrair_tickers_validos(texto_original):
        if tk in TICKER_PARA_EMPRESA:
            ent_id, pilar = TICKER_PARA_EMPRESA[tk]
        else:
            ent_id = tk.lower()
            if tk.endswith(("31", "32", "33", "34", "35", "39")):
                pilar = "BDR"
            elif tk.endswith("11") and _contem_termo_exato(t_low, TERMOS_FIIS):
                pilar = "FIIS"
            else:
                pilar = "ACOES"
        if ent_id not in vistos:
            vistos.add(ent_id)
            encontradas.append((ent_id, pilar, tk))

    for ent_id, pilar, termos, tickers in EMPRESAS_B3_MAPA:
        if ent_id == "neoenergia" and (vistos & {"coelba", "celpe", "cosern", "elektro"}):
            continue
        if ent_id not in vistos and _contem_termo_exato(t_low, termos):
            vistos.add(ent_id)
            encontradas.append((ent_id, pilar, tickers[0]))

    for ent_id, pilar, tk, rx in (
        ("banco_do_brasil", "ACOES", "BBAS3", REGEX_BB_BANCO),
        ("vale", "ACOES", "VALE3", REGEX_VALE_MINERADORA),
        ("porto_seguro", "ACOES", "PSSA3", REGEX_PORTO_SEGURO),
        ("equatorial", "ACOES", "EQTL3", REGEX_EQUATORIAL),
        ("irb", "ACOES", "IRBR3", REGEX_IRB),
        ("vivo", "ACOES", "VIVT3", REGEX_VIVO_OPERADORA),
        ("tim", "ACOES", "TIMS3", REGEX_TIM_OPERADORA),
        ("light", "ACOES", "LIGT3", REGEX_LIGHT_ENERGIA),
        ("allied", "ACOES", "ALLD3", REGEX_ALLIED_TECH),
        ("alphabet", "BDR", "GOGL34", REGEX_GOOGLE_FIN),
        ("amd", "BDR", "A1MD34", REGEX_AMD_FIN),
    ):
        if ent_id not in vistos and rx.search(texto_original):
            vistos.add(ent_id)
            encontradas.append((ent_id, pilar, tk))

    return encontradas


def extrair_protagonistas_titulo(titulo: str) -> List[Tuple[str, str, str]]:
    entidades = detectar_ativos_b3_no_texto(titulo)
    if not entidades:
        return []
    if REGEX_RELATORIO_DE_BANCO.search(titulo):
        return detectar_ativos_b3_no_texto(REGEX_RELATORIO_DE_BANCO.sub(" ", titulo))
    return entidades


def _detectar_tema_em_texto(txt_low: str) -> Tuple[Optional[str], Optional[str]]:
    if _contem_termo_exato(txt_low, TERMOS_FIIS):
        return "fiis_geral", "FIIS"
    if _contem_termo_exato(txt_low, TERMOS_BDR):
        return "bdr_exterior", "BDR"
    if _contem_termo_exato(txt_low, ("tesouro direto", "tesouro ipca", "tesouro selic", "tesouro prefixado", "ntn-b", "títulos públicos", "título público")):
        return "rf_tesouro", "RENDA_FIXA"
    if _contem_termo_exato(txt_low, ("cdb", "cdbs", "lci", "lcis", "lca", "lcas", "cri", "cra", "debênture", "debêntures", "fgc", "renda fixa", "cdi", "di futuro", "curva de juros", "juros futuros", "isenção de ir", "poupança")):
        return "rf_credito_juros", "RENDA_FIXA"
    if _contem_termo_exato(txt_low, ("copom", "selic", "ata do copom", "taxa básica de juros", "taxa de juros", "galípolo", "galipolo", "banco central")):
        return "macro_selic", "MACRO"
    if _contem_termo_exato(txt_low, ("ipca", "ipca-15", "igp-m", "inflação", "boletim focus", "relatório focus")):
        return "macro_inflacao", "MACRO"
    if _contem_termo_exato(txt_low, ("arcabouço fiscal", "meta fiscal", "dívida pública", "déficit fiscal", "superávit primário", "pacote fiscal", "corte de gastos", "reforma tributária", "arrecadação federal", "contas públicas", "gastos públicos", "imposto de renda", "iof", "ministério da fazenda", "haddad")):
        return "macro_fiscal", "MACRO"
    if _contem_termo_exato(txt_low, ("pib", "pib do brasil", "crescimento do pib", "ibc-br", "atividade econômica", "produção industrial", "vendas no varejo", "setor de serviços", "caged", "empregos formais", "taxa de desemprego", "pnad", "pmi", "balança comercial", "conta corrente", "fluxo estrangeiro", "investimento estrangeiro")):
        return "macro_atividade", "MACRO"
    if _contem_termo_exato(txt_low, ("federal reserve", "fomc", "juros dos eua", "treasuries", "treasury", "payroll", "cpi", "pce", "inflação nos eua", "economia dos eua", "economia americana", "economia chinesa", "economia da china", "banco central europeu", "guerra comercial", "tarifas comerciais", "tarifas de importação", "tarifaço", "recessão")):
        return "macro_global", "MACRO"
    if _contem_termo_exato(txt_low, ("petróleo", "brent", "opep", "minério de ferro", "commodities")):
        return "macro_commodities", "MACRO"
    if _contem_termo_exato(txt_low, ("dólar", "câmbio", "ibovespa", "renda variável", "small caps")):
        return "macro_mercado", "MACRO"
    return None, None


def detectar_tema_macro_ou_rf(titulo: str, trecho: str = "") -> Tuple[Optional[str], Optional[str]]:
    tema, pilar = _detectar_tema_em_texto((titulo or "").lower())
    if tema:
        return tema, pilar
    return _detectar_tema_em_texto(f"{titulo} {(trecho or '')[:260]}".lower())


def titulo_eh_relevante_mercado(titulo: str, desc_bruta: str = "") -> bool:
    if extrair_protagonistas_titulo(titulo):
        return True
    tema_id, _ = detectar_tema_macro_ou_rf(titulo, limpar_html(desc_bruta)[:260])
    return bool(tema_id)


def definir_tag_curta_mercado(titulo: str, pilar_principal: str) -> str:
    t_low = titulo.lower()
    if pilar_principal == "RENDA_FIXA" or _contem_termo_exato(t_low, TERMOS_RENDA_FIXA):
        return "🏛️ <b>RENDA FIXA</b>"
    if _contem_termo_exato(t_low, ("dividendo", "dividendos", "jcp", "juros sobre capital", "proventos", "data-com", "data com", "bonificação", "rendimento por cota")):
        return "🟢 <b>DIVIDENDOS</b>"
    if _contem_termo_exato(t_low, ("balanço", "lucro", "lucra", "ebitda", "prejuízo", "receita líquida", "resultado trimestral", "1t25", "2t25", "3t25", "4t25", "1t26", "2t26", "3t26", "4t26", "1t27", "2t27", "3t27", "4t27")):
        return "🔵 <b>BALANÇOS</b>"
    if pilar_principal == "FIIS" or _contem_termo_exato(t_low, TERMOS_FIIS):
        return "🏢 <b>FIIs</b>"
    if pilar_principal == "BDR" or _contem_termo_exato(t_low, TERMOS_BDR):
        return "🌎 <b>BDR &amp; EXTERIOR</b>"
    return PILAR_LABELS.get(pilar_principal, "📊 <b>AÇÕES B3</b>")


def avaliar_impacto_financeiro(titulo: str, paragrafos: List[str]) -> AnaliseHeuristica:
    if not paragrafos:
        return AnaliseHeuristica(0, "DESCARTADA", "")

    lead = paragrafos[0]
    corpo_texto = " ".join(paragrafos)
    texto_completo = f"{titulo} {corpo_texto}"

    entidades_titulo = extrair_protagonistas_titulo(titulo)
    tema_id, pilar_tema = detectar_tema_macro_ou_rf(titulo, lead)

    if not entidades_titulo and not tema_id:
        return AnaliseHeuristica(0, "DESCARTADA", "")

    titulo_low = re.sub(r"\breceita federal\b", "órgão tributário", titulo.lower())
    lead_low = re.sub(r"\breceita federal\b", "órgão tributário", lead.lower())
    corpo_low = re.sub(r"\breceita federal\b", "órgão tributário", corpo_texto.lower())

    if entidades_titulo:
        ids_protag = [e[0] for e in entidades_titulo]
        pilar_principal = entidades_titulo[0][1]
        tickers_exp = [tk for tk in extrair_tickers_validos(texto_completo) if tk in TICKER_PARA_EMPRESA and TICKER_PARA_EMPRESA[tk][0] in ids_protag]
        tickers_finais = list(dict.fromkeys(tickers_exp + [tk for _, _, tk in entidades_titulo]))
    else:
        assert tema_id is not None and pilar_tema is not None
        ids_protag = [tema_id]
        pilar_principal = pilar_tema
        tickers_finais = []

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
    if pontos_eventos == 0:
        return AnaliseHeuristica(0, "DESCARTADA", "")

    score_final = 12 + pontos_eventos + min(len(metricas) * 3, 18)
    return AnaliseHeuristica(
        score_total=score_final,
        pilar_chave=pilar_principal,
        categoria_label=definir_tag_curta_mercado(titulo, pilar_principal),
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
    chaves: List[str] = []
    for ent in dict.fromkeys(entidades[:3]):
        if ent.startswith("macro_"):
            chaves.append(f"{ent}::MACRO_EVENTO")
        elif ent.startswith("rf_"):
            chaves.append(f"{ent}::RF_EVENTO")
        elif ent == "fiis_geral":
            chaves.append("fiis_geral::FIIS_GERAL")
        elif ent == "bdr_exterior":
            chaves.append("bdr_exterior::BDR_GERAL")
        elif _contem_termo_exato(foco, ("dividendo", "dividendos", "jcp", "juros sobre capital", "proventos", "data com", "data-com", "data ex", "data-ex", "bonificacao", "rendimento por cota")):
            chaves.append(f"{ent}::PROVENTOS")
        elif _contem_termo_exato(foco, ("balanco", "lucro liquido", "lucra", "lucro", "ebitda", "lajida", "prejuizo", "receita liquida", "resultado trimestral", "1t25", "2t25", "3t25", "4t25", "1t26", "2t26", "3t26", "4t26")):
            chaves.append(f"{ent}::BALANCO")
        elif _contem_termo_exato(foco, ("fato relevante", "fusao", "aquisicao", "recompra", "opa", "follow-on", "privatizacao", "leilao", "concessao", "recuperacao judicial", "debenture", "debentures", "emissao de cotas")):
            chaves.append(f"{ent}::EVENTO_CORP")
        else:
            chaves.append(f"{ent}::GERAL_ATIVO")
    return chaves


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
# LIMPEZA EDITORIAL, TRAFILATURA E NARRATIVA ENXUTA
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
    amostra = f"{t_low} {(inicio_texto or '')[:450].lower()}"
    if any(b in t_low for b in BLOQUEADOS_APENAS_NO_TITULO):
        return True
    return any(b in amostra for b in BLOQUEADOS_PROPAGANDA_CORRETORA)


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
    return len(REGEX_METRICAS_NUMERICAS.findall(p)) * 12 + len(extrair_tickers_validos(p)) * 18 + sum(w for t, w in PESOS_EVENTOS_FINANCEIROS.items() if t in p.lower())


def extrair_narrativa_enxuta(paragrafos: List[str], max_blocos: int = 3, max_por_bloco: int = 245, titulo: str = "") -> Optional[List[str]]:
    todas: List[str] = []
    tit_norm = normalizar_texto_dedup(titulo).rstrip(".!?") if titulo else ""
    for p in paragrafos:
        for f in dividir_em_frases_completas(p):
            if tit_norm and normalizar_texto_dedup(f).rstrip(".!?") == tit_norm:
                continue
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

    post_principal: Optional[str] = None
    blocos_escolhidos: Optional[List[str]] = None
    for max_blocos, max_bloco in ((3, 340), (3, 300), (3, 260), (2, 360), (2, 290)):
        blocos = extrair_narrativa_enxuta(paragrafos, max_blocos=max_blocos, max_por_bloco=max_bloco, titulo=t_limpo)
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

    if tamanho_texto_visivel(post_principal) <= TELEGRAM_CAPTION_LIMIT - 20:
        caption_curta = post_principal
    else:
        caption_curta = post_principal
        for max_blocos, max_bloco in ((3, 240), (3, 200), (2, 260), (2, 210), (2, 175)):
            bc = extrair_narrativa_enxuta(paragrafos, max_blocos=max_blocos, max_por_bloco=max_bloco, titulo=t_limpo)
            if not bc:
                continue
            cand_c = f"{cabecalho}\n\n<b>{titulo_html}</b>\n\n" + "\n\n".join(formatar_tickers_no_texto(html.escape(p)) for p in bc)
            if tamanho_texto_visivel(cand_c) <= TELEGRAM_CAPTION_LIMIT - 20:
                caption_curta = cand_c
                break

    return post_principal, caption_curta


# ==============================================================================
# CURADORIA EDITORIAL COM GEMINI (RENDA FIXA + RENDA VARIÁVEL + ECONOMIA + IMPACTO HONESTO)
# ==============================================================================
def _extrair_json_gemini(texto_bruto: str) -> Optional[Dict[str, Any]]:
    if not texto_bruto:
        return None
    s = texto_bruto.strip()
    m_fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, flags=re.DOTALL | re.IGNORECASE)
    if m_fenced:
        s = m_fenced.group(1)
    else:
        ini, fim = s.find("{"), s.rfind("}")
        if ini != -1 and fim > ini:
            s = s[ini : fim + 1]
    try:
        dados = json.loads(s)
        return dados if isinstance(dados, dict) else None
    except Exception:
        return None


def _normalizar_investimentos_afetados(lista_raw: Any, pilar_fallback: str = "") -> List[str]:
    validos: List[str] = []
    if isinstance(lista_raw, list):
        for item in lista_raw:
            if not isinstance(item, str):
                continue
            chave = "".join(
                c for c in unicodedata.normalize("NFKD", item.strip().lower()) if not unicodedata.combining(c)
            )
            chave = re.sub(r"[^a-z0-9\s]", " ", chave)
            chave = " ".join(chave.split())
            mapped = MAPA_INVESTIMENTOS_AFETADOS.get(chave)
            if mapped and mapped not in validos:
                validos.append(mapped)
    if not validos:
        mapa_pilar = {
            "ACOES": ["Ações"],
            "BDR": ["BDRs"],
            "FIIS": ["FIIs"],
            "RENDA_FIXA": ["Renda Fixa"],
            "MACRO": ["Economia", "Renda Fixa", "Renda Variável"],
        }
        validos = list(mapa_pilar.get(pilar_fallback, ["Renda Variável"]))
    return validos[:3]


def _formatar_lista_investimentos_pt(classes: List[str]) -> str:
    if not classes:
        return ""
    if len(classes) == 1:
        return classes[0]
    if len(classes) == 2:
        return f"{classes[0]} e {classes[1]}"
    return f"{', '.join(classes[:-1])} e {classes[-1]}"


def _limpar_prefixo_impacto(texto: str) -> str:
    return re.sub(
        r"^(?:📌\s*)?(?:no\s+radar\s+do\s+investidor|impacto\s+para\s+o\s+investidor|por\s+que\s+importa|para\s+o\s+investidor)\s*(?:\([^)]*\))?\s*[:\-–—]\s*",
        "",
        texto,
        flags=re.IGNORECASE,
    ).strip()


def _montar_html_de_blocos(
    cabecalho: str,
    titulo_final: str,
    blocos: List[str],
    prefixo_final_html: str = "",
) -> Tuple[Optional[str], Optional[str]]:
    t_limpo = limpar_html(titulo_final.strip())
    if t_limpo and t_limpo[-1] not in ".!?":
        t_limpo += "."
    titulo_html = formatar_tickers_no_texto(html.escape(t_limpo))

    blocos_limpos: List[str] = []
    tit_norm = normalizar_texto_dedup(t_limpo).rstrip(".!?")
    for idx, b in enumerate(blocos[:3]):
        b_limpo = limpar_html(str(b)).strip()
        if idx == len(blocos[:3]) - 1 and prefixo_final_html:
            b_limpo = _limpar_prefixo_impacto(b_limpo)
        if not b_limpo or normalizar_texto_dedup(b_limpo).rstrip(".!?") == tit_norm:
            continue
        if b_limpo[-1] not in ".!?":
            b_limpo += "."
        blocos_limpos.append(b_limpo)

    if len(blocos_limpos) < 2:
        return None, None

    def render(lista_p: List[str]) -> str:
        partes_html: List[str] = []
        for idx, p in enumerate(lista_p):
            p_html = formatar_tickers_no_texto(html.escape(p))
            if prefixo_final_html and idx == len(lista_p) - 1:
                partes_html.append(f"{prefixo_final_html}{p_html}")
            else:
                partes_html.append(p_html)
        return f"{cabecalho}\n\n<b>{titulo_html}</b>\n\n" + "\n\n".join(partes_html)

    post_principal = render(blocos_limpos)
    vis = tamanho_texto_visivel(post_principal)
    if vis > ALVO_MAX_CARACTERES_POST:
        ajustados = list(blocos_limpos)
        for limite_bloco in (310, 270, 235):
            cand_blocos = [truncar_frase_limpa(p, limite_bloco) or p for p in ajustados]
            cand_html = render(cand_blocos)
            if tamanho_texto_visivel(cand_html) <= ALVO_MAX_CARACTERES_POST:
                post_principal = cand_html
                blocos_limpos = cand_blocos
                vis = tamanho_texto_visivel(post_principal)
                break
        if vis > ALVO_MAX_CARACTERES_POST and len(blocos_limpos) == 3:
            cand2 = render([blocos_limpos[0], blocos_limpos[-1]])
            if MIN_CARACTERES_POST <= tamanho_texto_visivel(cand2) <= ALVO_MAX_CARACTERES_POST:
                post_principal = cand2
                blocos_limpos = [blocos_limpos[0], blocos_limpos[-1]]
                vis = tamanho_texto_visivel(post_principal)

    if not (MIN_CARACTERES_POST <= vis <= ALVO_MAX_CARACTERES_POST):
        return None, None

    if vis <= TELEGRAM_CAPTION_LIMIT - 20:
        caption_curta = post_principal
    else:
        caption_curta = post_principal
        for limite_c in (230, 195, 165):
            bc = [truncar_frase_limpa(p, limite_c) for p in blocos_limpos]
            bc = [p for p in bc if p]
            if len(bc) >= 2:
                cand_c = render(bc)
                if tamanho_texto_visivel(cand_c) <= TELEGRAM_CAPTION_LIMIT - 20:
                    caption_curta = cand_c
                    break
        if tamanho_texto_visivel(caption_curta) > TELEGRAM_CAPTION_LIMIT - 20 and len(blocos_limpos) >= 2:
            cand2 = render([truncar_frase_limpa(blocos_limpos[0], 330) or blocos_limpos[0], truncar_frase_limpa(blocos_limpos[-1], 290) or blocos_limpos[-1]])
            if tamanho_texto_visivel(cand2) <= TELEGRAM_CAPTION_LIMIT - 20:
                caption_curta = cand2

    return post_principal, caption_curta


def resetar_estado_sessao_gemini() -> None:
    _CACHE_MODELOS_GEMINI.clear()
    _MODELO_VENCEDOR_GEMINI.clear()
    _MODELOS_BLOQUEADOS_SESSAO.clear()
    _MODELOS_DEGRADADOS_SESSAO.clear()


def _pontuar_modelo_gemini(nome: str) -> int:
    """Prioriza exclusivamente modelos gratuitos da família Flash-Lite (500 RPD) e Flash,
    ignorando modelos pagos/0-cota (Pro, Ultra, Argon, Carbon, Hydrogen, 2.0, Image, TTS).
    """
    n = (nome or "").lower().strip()
    if not n.startswith("gemini-") or "flash" not in n or any(x in n for x in _MODELOS_GEMINI_EXCLUSAO):
        return -1
    m_ver = re.search(r"gemini-(\d+)(?:\.(\d+))?", n)
    major = int(m_ver.group(1)) if m_ver else 0
    minor = int(m_ver.group(2) or 0) if m_ver else 0
    if m_ver and (major, minor) < (2, 5):
        return -1
    ver_score = major * 100 + minor * 10
    base_familia = 2000 if "flash-lite" in n else 1000
    penalidade_preview = -15 if ("preview" in n or "exp" in n) else 0
    penalidade_latest = -20 if "latest" in n else 0
    return base_familia + ver_score + penalidade_preview + penalidade_latest


def _ordenar_modelos_sessao(api_key: str, modelos_ativos: Tuple[str, ...]) -> Tuple[str, ...]:
    bloqueados = _MODELOS_BLOQUEADOS_SESSAO.get(api_key, set())
    degradados = _MODELOS_DEGRADADOS_SESSAO.get(api_key, set())
    vencedor = _MODELO_VENCEDOR_GEMINI.get(api_key)

    ordenados: List[str] = []
    if vencedor and vencedor in modelos_ativos and vencedor not in bloqueados and vencedor not in degradados:
        ordenados.append(vencedor)
    for m in modelos_ativos:
        if m not in bloqueados and m not in degradados and m not in ordenados:
            ordenados.append(m)
    for m in modelos_ativos:
        if m not in bloqueados and m in degradados and m not in ordenados:
            ordenados.append(m)
    return tuple(ordenados)


def _registrar_sucesso_modelo_gemini(api_key: str, modelo: str) -> None:
    _MODELO_VENCEDOR_GEMINI[api_key] = modelo
    if api_key in _MODELOS_DEGRADADOS_SESSAO:
        _MODELOS_DEGRADADOS_SESSAO[api_key].discard(modelo)


def _registrar_bloqueio_modelo_gemini(api_key: str, modelo: str) -> None:
    _MODELOS_BLOQUEADOS_SESSAO.setdefault(api_key, set()).add(modelo)
    if _MODELO_VENCEDOR_GEMINI.get(api_key) == modelo:
        _MODELO_VENCEDOR_GEMINI.pop(api_key, None)


def _registrar_degradacao_modelo_gemini(api_key: str, modelo: str) -> None:
    _MODELOS_DEGRADADOS_SESSAO.setdefault(api_key, set()).add(modelo)
    if _MODELO_VENCEDOR_GEMINI.get(api_key) == modelo:
        _MODELO_VENCEDOR_GEMINI.pop(api_key, None)


def _extrair_erro_curto_gemini(texto: str) -> str:
    try:
        msg = json.loads(texto).get("error", {}).get("message", "")
        if msg:
            return " ".join(str(msg).split())[:140]
    except Exception:
        pass
    return " ".join(str(texto or "").split())[:140]


async def obter_modelos_gemini_ativos(client: httpx.AsyncClient, api_key: str) -> Tuple[str, ...]:
    """Consulta GET /v1beta/models para descobrir automaticamente os modelos gratuitos
    Flash-Lite e Flash ativos na conta, evitando modelos pagos (HTTP 429) e aposentados (HTTP 404).
    """
    if not api_key:
        return GEMINI_MODELS
    if api_key in _CACHE_MODELOS_GEMINI:
        return _CACHE_MODELOS_GEMINI[api_key]

    descobertos: List[str] = []
    try:
        resp = await client.get(
            f"{GEMINI_API_BASE}?pageSize=100",
            headers={**HEADERS_API_JSON, "x-goog-api-key": api_key},
            timeout=15.0,
        )
        if resp.status_code == 200:
            modelos_api = resp.json().get("models", [])
            ranqueados: List[Tuple[int, str]] = []
            for m in modelos_api:
                if not isinstance(m, dict):
                    continue
                metodos = m.get("supportedGenerationMethods") or []
                if "generateContent" not in metodos:
                    continue
                nome = str(m.get("name") or "").replace("models/", "").strip()
                sc = _pontuar_modelo_gemini(nome)
                if sc > 0:
                    ranqueados.append((sc, nome))
            ranqueados.sort(key=lambda x: x[0], reverse=True)
            descobertos = [nome for _, nome in ranqueados[:10]]
            if descobertos:
                logger.info("Modelos Gemini gratuitos (Flash/Lite) descobertos na API: %s", ", ".join(descobertos))
        elif resp.status_code in (401, 403):
            logger.error("Chave GEMINI_API_KEY rejeitada ao listar modelos (HTTP %s).", resp.status_code)
            _CACHE_MODELOS_GEMINI[api_key] = ()
            return ()
    except Exception as exc:
        logger.warning("Aviso ao listar modelos Gemini (usando fallback local): %s", str(exc)[:100])

    modelo_env = (os.environ.get("GEMINI_MODEL") or "").strip()
    if descobertos:
        lista = tuple(dict.fromkeys([m for m in (modelo_env, *descobertos) if m]))
    else:
        lista = GEMINI_MODELS
    _CACHE_MODELOS_GEMINI[api_key] = lista
    return lista


async def refinar_materia_com_gemini(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    titulo: str,
    paragrafos: List[str],
    analise: AnaliseHeuristica,
) -> Tuple[str, Optional[Tuple[str, str]]]:
    """Usa o Gemini (somente família Flash-Lite/Flash gratuita) para validar se a matéria agrega
    valor real a investidores de Renda Fixa, Renda Variável (Ações B3, BDRs, FIIs) e Economia,
    reescrevendo com Começo, Meio e Fim + pincelada final honesta de impacto.
    Retorna:
      - ("APROVADA", (post_html, caption_curta_html))
      - ("REJEITADA", None) -> IA confirmou que é irrelevante/ruído/paywall raso
      - ("FALLBACK", None)  -> Sem chave, erro de rede ou cota esgotada (usa redator local)
    """
    api_key = (os.environ.get("GEMINI_API_KEY") or GEMINI_API_KEY or "").strip().strip('"').strip("'")
    if not api_key:
        return "FALLBACK", None

    texto_base = "\n\n".join(paragrafos)[:6000]
    if len(texto_base) < MIN_TEXTO_BRUTO_PARA_IA:
        return "REJEITADA", None

    modelos_ativos = await obter_modelos_gemini_ativos(client, api_key)
    modelos_sessao = _ordenar_modelos_sessao(api_key, modelos_ativos) if modelos_ativos else ()
    if not modelos_sessao:
        return "FALLBACK", None

    tickers_str = ", ".join(analise.tickers[:3]) if analise.tickers else "Nenhum (Renda Fixa / Economia / Macro)"
    prompt = (
        "Você é um editor financeiro sênior escrevendo posts curtos, claros, didáticos e 100% honestos para um canal de "
        "investidores brasileiros no Telegram. Os seguidores do canal são investidores de:\n"
        "- Renda Fixa no geral (Tesouro Direto, CDB, LCI/LCA, CRI/CRA, Debêntures, Selic/CDI, IPCA e Curva de Juros)\n"
        "- Renda Variável no geral (Ações da B3, BDRs/ETFs, FIIs e FIAGROs)\n"
        "- Economia no geral (indicadores econômicos, inflação, juros, fiscal, PIB, emprego, câmbio, commodities e economia global que ajudam o investidor a entender o cenário)\n\n"
        f"PILAR DETECTADO: {analise.pilar_chave}\n"
        f"TICKER(S) DETECTADO(S): {tickers_str}\n"
        f"TÍTULO ORIGINAL: {titulo}\n"
        f"TEXTO DA MATÉRIA:\n{texto_base}\n\n"
        "TAREFA 1 — TESTE DE VALOR REAL PARA OS SEGUIDORES (CURADORIA EXIGENTE):\n"
        "PERGUNTA DECISIVA: Depois de ler o post em 20 segundos, um investidor de renda fixa/variável que gosta de economia vai sentir que a notícia REALMENTE agregou conhecimento útil sobre mercado ou economia?\n"
        "APROVE (aprovada=true) somente se a resposta for SIM e houver fato concreto relevante:\n"
        "- Renda Variável (Ações B3 / BDRs / ETFs / FIIs): dividendos, JCP, data-com, recompra, balanços/resultados, fusões/aquisições, fatos relevantes, leilões, dívida, guidance, vacância/emissões de FIIs, resultados de grandes BDRs (Apple, Nvidia, Microsoft, Amazon, Alphabet, Meta, Tesla, Berkshire, bancos EUA), Ibovespa, IFIX, S&P 500 ou explicação real de forte alta/queda.\n"
        "- Renda Fixa: Selic/Copom, taxas do Tesouro Direto e curva DI, CDB/LCI/LCA/CRI/CRA/debêntures, FGC, poupança, tributação/isenção de IR em investimentos.\n"
        "- Economia no geral que agrega: IPCA/inflação, Boletim Focus, PIB, IBC-Br, Caged/desemprego, produção industrial, varejo, balança comercial, arrecadação/contas públicas/arcabouço fiscal, dólar/câmbio, petróleo/minério de ferro, Fed/juros dos EUA, CPI/PCE, economia chinesa/europeia e tarifas comerciais com impacto real na economia.\n"
        "REJEITE (aprovada=false) sem dó se:\n"
        "- For ruído político partidário/declaração vaga de político sem medida econômica concreta, criptomoeda, esportes/futebol/patrocínio, polícia/crime, fofoca/entretenimento, lançamento de produto/série sem relevância financeira, assistencialismo puro (INSS, Bolsa Família, CadÚnico, Serasa limpa nome), clickbait vazio sem dado concreto ou propaganda de corretora/curso.\n"
        "- For uma nota rasa/irrelevante ou cortada por paywall que não entregue começo, meio e fim reais.\n"
        "- Não agregar valor genuíno ao investidor (NUNCA invente nem force importância para tentar aprovar).\n\n"
        "TAREFA 2 — REDAÇÃO COM COMEÇO, MEIO, FIM E PINCELADA FINAL HONESTA (se aprovada=true):\n"
        "Escreva em português claro, objetivo e jornalístico, somando entre 620 e 940 caracteres no total dos 3 blocos:\n"
        "- titulo: manchete direta e clara (1 frase, sem clickbait, sem nome de portal).\n"
        "- comeco (COMEÇO — O Fato, ~220 a 320 chars): o que aconteceu de concreto, citando o ativo/órgão/indicador e os números, taxas ou datas exatas da matéria.\n"
        "- meio (MEIO — Contexto e Decifração, ~220 a 320 chars): por que isso aconteceu, comparação com períodos anteriores, datas-chave ou explicação didática de termos técnicos da matéria (ex.: JCP, data-com, NTN-B, P/VP, curva DI, IBC-Br, CPI, BDR, vacância).\n"
        "- fim_impacto (FIM — Pincelada honesta para o investidor, ~180 a 280 chars): explique de forma objetiva e SEM EXAGERAR por que essa notícia importa na prática e como ela se conecta à economia e/ou aos investimentos afetados. NÃO comece com 'No radar do investidor:' (o rótulo já é adicionado automaticamente).\n"
        "- investimentos_afetados: array com 1 a 3 itens REALMENTE afetados, escolhendo SOMENTE entre estes valores exatos: ['Ações', 'BDRs', 'FIIs', 'Renda Fixa', 'Renda Variável', 'Economia']. Cite apenas os que têm ligação real e direta.\n"
        "- tag_evento: escolha EXATAMENTE uma opção entre: 'DIVIDENDOS', 'BALANCOS', 'ACOES', 'BDR', 'FIIS', 'RENDA_FIXA', 'MACRO'.\n"
        "- tickers: array com até 3 tickers oficiais da B3 (ações, BDRs, FIIs ou ETFs, ex.: ['PETR4'], ['NVDC34'], ['MXRF11']) que sejam realmente protagonistas; retorne [] se for matéria de Renda Fixa, economia geral, juros, macro ou setorial sem ativo específico.\n\n"
        "REGRAS INVIOLÁVEIS (ZERO MENTIRA):\n"
        "- NUNCA invente números, percentuais, taxas, datas ou fatos que não estejam no texto original.\n"
        "- NUNCA prometa ou garanta que um ativo vai subir ou cair; use linguagem fiel e responsável ('tende a', 'pode pressionar', 'reforça o fluxo de caixa', 'eleva a remuneração de títulos pós-fixados', 'ajuda a calibrar as expectativas de juros').\n"
        "- NUNCA faça recomendação de compra ou venda.\n"
        "- NUNCA use markdown (*, **, #), emojis no texto ou siglas de carteiras/estratégias.\n"
        "- NUNCA repita o título dentro do bloco 'comeco'.\n"
        "- Responda APENAS com um objeto JSON válido contendo as chaves: aprovada (boolean), motivo_rejeicao (string), titulo (string), comeco (string), meio (string), fim_impacto (string), investimentos_afetados (array de strings), tag_evento (string), tickers (array de strings)."
    )

    payload_base: Dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 3000,
            "responseMimeType": "application/json",
        },
    }

    dados_ia: Optional[Dict[str, Any]] = None
    modelo_usado = ""
    async with sem:
        for modelo in modelos_sessao:
            url = f"{GEMINI_API_BASE}/{modelo}:generateContent"
            try:
                resp = await client.post(
                    url,
                    headers={**HEADERS_API_JSON, "Content-Type": "application/json", "x-goog-api-key": api_key},
                    json=payload_base,
                    timeout=20.0,
                )
                if resp.status_code == 200:
                    body = resp.json()
                    cand0 = (body.get("candidates") or [{}])[0]
                    finish_reason = cand0.get("finishReason", "UNKNOWN")
                    partes = cand0.get("content", {}).get("parts", [])
                    texto_resp = "".join(
                        p.get("text", "") for p in partes if isinstance(p, dict) and not p.get("thought")
                    )
                    if not texto_resp:
                        texto_resp = "".join(p.get("text", "") for p in partes if isinstance(p, dict))
                    dados_ia = _extrair_json_gemini(texto_resp)
                    if dados_ia is not None and "aprovada" in dados_ia:
                        _registrar_sucesso_modelo_gemini(api_key, modelo)
                        modelo_usado = modelo
                        break
                    logger.warning("Gemini (%s | finishReason=%s) retornou JSON incompleto; tentando próximo modelo.", modelo, finish_reason)
                elif resp.status_code in (401, 403):
                    logger.error("Chave GEMINI_API_KEY rejeitada (HTTP %s): %s", resp.status_code, _extrair_erro_curto_gemini(resp.text))
                    break
                elif resp.status_code in (404, 429):
                    _registrar_bloqueio_modelo_gemini(api_key, modelo)
                    logger.warning("Gemini (%s) bloqueado nesta execução (HTTP %s: %s); pulando modelo.", modelo, resp.status_code, _extrair_erro_curto_gemini(resp.text))
                    continue
                elif resp.status_code in (400, 500, 503):
                    _registrar_degradacao_modelo_gemini(api_key, modelo)
                    logger.warning("Gemini (%s) instável/sobrecarregado (HTTP %s: %s); tentando próximo modelo.", modelo, resp.status_code, _extrair_erro_curto_gemini(resp.text))
                    continue
                else:
                    logger.warning("Gemini (%s) HTTP %s: %s", modelo, resp.status_code, _extrair_erro_curto_gemini(resp.text))
                    break
            except Exception as exc:
                _registrar_degradacao_modelo_gemini(api_key, modelo)
                logger.warning("Erro/timeout ao consultar Gemini (%s): %s", modelo, str(exc)[:100])
                continue

    if dados_ia is None:
        return "FALLBACK", None

    if dados_ia.get("aprovada") is False:
        logger.info("Descartada pelo Gemini (%s | %s): %s", modelo_usado or "ia", str(dados_ia.get("motivo_rejeicao", "irrelevante"))[:90], titulo)
        return "REJEITADA", None

    comeco_ia = str(dados_ia.get("comeco") or "").strip()
    meio_ia = str(dados_ia.get("meio") or "").strip()
    fim_ia = str(dados_ia.get("fim_impacto") or "").strip()
    if comeco_ia and meio_ia and fim_ia:
        parags_ia: Any = [comeco_ia, meio_ia, fim_ia]
    else:
        parags_ia = dados_ia.get("paragrafos")

    titulo_ia = str(dados_ia.get("titulo") or titulo).strip()
    if not isinstance(parags_ia, list) or len(parags_ia) < 2:
        return "FALLBACK", None

    texto_verif = f"{titulo_ia} {' '.join(str(p) for p in parags_ia)}".lower()
    if REGEX_SIGLA_BLOQUEADA.search(texto_verif):
        return "FALLBACK", None

    titulo_ia = re.sub(r"[*_`]+", "", titulo_ia).strip()
    parags_limpos = [re.sub(r"[*_`]+", "", str(p)).strip() for p in parags_ia]

    tag_ia = str(dados_ia.get("tag_evento") or "").strip().upper()
    mapa_tags = {
        "DIVIDENDOS": "🟢 <b>DIVIDENDOS</b>",
        "BALANCOS": "🔵 <b>BALANÇOS</b>",
        "ACOES": "📊 <b>AÇÕES B3</b>",
        "BDR": "🌎 <b>BDR &amp; EXTERIOR</b>",
        "FIIS": "🏢 <b>FIIs</b>",
        "RENDA_FIXA": "🏛️ <b>RENDA FIXA</b>",
        "MACRO": "🌐 <b>ECONOMIA &amp; MACRO</b>",
    }
    cat_label = mapa_tags.get(tag_ia, analise.categoria_label)

    if tag_ia in ("RENDA_FIXA", "MACRO"):
        tickers_finais: List[str] = []
    elif isinstance(dados_ia.get("tickers"), list):
        tks_ia = [
            str(t).strip().upper().lstrip("#")
            for t in dados_ia["tickers"]
            if isinstance(t, str)
        ]
        tickers_finais = [
            t for t in dict.fromkeys(tks_ia)
            if REGEX_TICKER_B3.fullmatch(t) and t not in FALSOS_TICKERS
        ][:3]
        if not tickers_finais and tag_ia in ("DIVIDENDOS", "BALANCOS", "FIIS", "ACOES", "BDR"):
            tickers_finais = analise.tickers[:3]
    else:
        tickers_finais = analise.tickers[:3]

    if tickers_finais:
        pills = " ".join(f"<code>#{t}</code>" for t in tickers_finais)
        cabecalho = f"<blockquote>{cat_label}  ·  {pills}</blockquote>"
    else:
        cabecalho = f"<blockquote>{cat_label}</blockquote>"

    classes_afetadas = _normalizar_investimentos_afetados(dados_ia.get("investimentos_afetados"), analise.pilar_chave)
    classes_fmt = _formatar_lista_investimentos_pt(classes_afetadas)
    prefixo_final = (
        f"📌 <b>No radar do investidor ({html.escape(classes_fmt)}):</b> "
        if classes_fmt
        else "📌 <b>No radar do investidor:</b> "
    )

    post_html, cap_curta = _montar_html_de_blocos(cabecalho, titulo_ia, parags_limpos, prefixo_final_html=prefixo_final)
    if not post_html or not cap_curta:
        return "FALLBACK", None

    return "APROVADA", (post_html, cap_curta)


# ==============================================================================
# MÍDIA GARANTIDA (IMAGEM NATIVA OU VÍDEO YT-DLP — NUNCA ENVIA TEXTO SECO)
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
        if not titulo_eh_relevante_mercado(title, desc_bruta):
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

    for cand in candidatos[:80]:
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

        # Valida mídia ANTES de acionar o Gemini (economiza cota e evita processar matéria sem foto/vídeo)
        img_ver: Optional[ImagemVerificada] = None
        for u_img in dict.fromkeys(u for u in (foto_og, foto_rss) if u):
            img_ver = await verificar_e_preparar_imagem(client, sem, u_img, link, set_hashes_img)
            if img_ver:
                break

        video_cand = video_rss or video_pag
        if not img_ver and not video_cand:
            stats["sem_img"] += 1
            continue

        status_ia, montagem = await refinar_materia_com_gemini(client, sem, title, paragrafos, analise)
        if status_ia == "REJEITADA":
            stats["desc"] += 1
            continue
        if status_ia == "FALLBACK" or not montagem:
            montagem = montar_mensagem_investidor(title, paragrafos, analise)
        if not montagem:
            stats["desc"] += 1
            continue
        post_html, caption_curta_html = montagem

        logger.info("Aprovada [Score %d | %s | %s | %.0f min | %d chars]: %s", analise.score_total, analise.pilar_chave, "Gemini" if status_ia == "APROVADA" else "Local", idade_min, tamanho_texto_visivel(post_html), title)
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
            resp = await (
                client.post(url, headers=HEADERS_API_JSON, data=data_fields, files=files, timeout=90.0)
                if files
                else client.post(url, headers=HEADERS_API_JSON, json=json_payload, timeout=30.0)
            )
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
        chave_gemini = (os.environ.get("GEMINI_API_KEY") or GEMINI_API_KEY or "").strip().strip('"').strip("'")
        if chave_gemini:
            modelos_ativos = await obter_modelos_gemini_ativos(client, chave_gemini)
            logger.info("Curadoria editorial Gemini ATIVA (modelos: %s).", ", ".join(modelos_ativos) if modelos_ativos else "nenhum")
        else:
            logger.info("GEMINI_API_KEY ausente — usando redator editorial local.")

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
