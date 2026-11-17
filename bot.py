import asyncio
import email.utils
import html
import json
import logging
import os
import random
import re
import shutil
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
import trafilatura
import yt_dlp
from google import genai
from google.genai import types

# ==============================================================================
# CONFIGURAÇÃO DE LOGGING ESTRUTURADO
# ==============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(funcName)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("RadarMercadoBot")

# ==============================================================================
# CONFIGURAÇÕES E CONSTANTES DE PRODUÇÃO (SERVERLESS / GITHUB ACTIONS)
# ==============================================================================
HISTORICO_ARQUIVO = os.getenv("HISTORICO_ARQUIVO", "historico_enviados.json")
MAX_HISTORICO = 1000
MAX_NOTICIAS_POR_VEZ = int(os.getenv("MAX_NOTICIAS_POR_VEZ", "4"))
# 90 minutos absorve o atraso (jitter) do cron gratuito do GitHub Actions
MAX_IDADE_MINUTOS = int(os.getenv("MAX_IDADE_MINUTOS", "90"))
LIMITE_LEGENDA_TELEGRAM = 1024
LIMITE_TEXTO_TELEGRAM = 4096
MAX_VIDEO_BYTES = 45 * 1024 * 1024  # 45 MB (abaixo do limite de 50 MB da Bot API)
MAX_CONCORRENCIA_HTTP = 6
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
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

URL_GRUPO_CTA = os.getenv("TELEGRAM_GROUP_URL", "https://t.me/OPapodeInvestidor")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}

FEEDS = [
    # 1. Economia & Mercado Financeiro
    "https://www.infomoney.com.br/feed/",
    "https://www.infomoney.com.br/mercados/feed/",
    "https://www.moneytimes.com.br/feed/",
    "https://www.seudinheiro.com/feed/",
    "https://www.suno.com.br/noticias/feed/",
    "https://einvestidor.estadao.com.br/feed/",
    "https://g1.globo.com/rss/g1/economia/",
    # 2. Negócios, M&A e Empresas
    "https://braziljournal.com/feed",
    "https://exame.com/feed/",
    # 3. Política & Decisões de Brasília
    "https://www.infomoney.com.br/politica/feed/",
    "https://g1.globo.com/rss/g1/politica/",
    "https://www.cnnbrasil.com.br/politica/feed/",
    # 4. Geopolítica & Mercado Internacional
    "https://g1.globo.com/rss/g1/mundo/",
    "https://www.cnnbrasil.com.br/internacional/feed/",
    "https://feeds.bbci.co.uk/portuguese/rss.xml",
    # 5. Criptoativos & Ativos Digitais
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


# ==============================================================================
# VALIDAÇÃO DE CREDENCIAIS E CLIENTE GEMINI
# ==============================================================================
def obter_credenciais() -> Tuple[str, str, Optional[genai.Client]]:
    """Valida secrets obrigatórios do ambiente e inicializa o cliente Gemini."""
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    gemini_key = (os.getenv("GEMINI_API_KEY") or "").strip()

    if not token or not chat_id:
        logger.critical(
            "Credenciais ausentes: defina TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID nos Secrets."
        )
        sys.exit(1)

    cliente_gemini = None
    if gemini_key:
        try:
            cliente_gemini = genai.Client(api_key=gemini_key)
        except Exception as e:
            logger.error(f"Erro ao inicializar cliente Gemini: {e}")
    else:
        logger.warning("GEMINI_API_KEY ausente. O bot usará o resumo de contingência local.")

    return token, chat_id, cliente_gemini


# ==============================================================================
# PERSISTÊNCIA ATÔMICA DE HISTÓRICO
# ==============================================================================
def carregar_historico() -> Tuple[List[str], List[str]]:
    if os.path.exists(HISTORICO_ARQUIVO):
        try:
            with open(HISTORICO_ARQUIVO, "r", encoding="utf-8") as f:
                dados = json.load(f)
                if isinstance(dados, dict):
                    return dados.get("links", []), dados.get("titulos", [])
                if isinstance(dados, list):
                    return dados, []
        except Exception as e:
            logger.error(f"Erro ao ler histórico '{HISTORICO_ARQUIVO}': {e}")
    return [], []


def salvar_historico(links: List[str], titulos: List[str]) -> None:
    """Gravação atômica para garantir integridade antes do git commit no runner."""
    dados = {
        "links": list(dict.fromkeys(links))[-MAX_HISTORICO:],
        "titulos": list(dict.fromkeys(titulos))[-MAX_HISTORICO:],
        "atualizado_em": datetime.now(timezone.utc).isoformat(),
    }
    diretorio = os.path.dirname(os.path.abspath(HISTORICO_ARQUIVO)) or "."
    try:
        with tempfile.NamedTemporaryFile("w", dir=diretorio, delete=False, encoding="utf-8") as tmp:
            json.dump(dados, tmp, ensure_ascii=False, indent=2)
            caminho_tmp = tmp.name
        os.replace(caminho_tmp, HISTORICO_ARQUIVO)
    except Exception as e:
        logger.error(f"Erro ao salvar histórico atomicamente: {e}")


# ==============================================================================
# FILTROS, CATEGORIZAÇÃO E DEDUPLICAÇÃO
# ==============================================================================
def gerar_botao_grupo_aleatorio() -> Dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": random.choice(TEXTOS_BOTAO_GRUPO), "url": URL_GRUPO_CTA}]
        ]
    }


def contem_termo(texto: str, lista_termos: Tuple[str, ...]) -> bool:
    for termo in lista_termos:
        padrao = r"(?<!\w)" + re.escape(termo) + r"(?!\w)"
        if re.search(padrao, texto, flags=re.IGNORECASE):
            return True
    return False


def eh_propaganda_ou_lixo(titulo: str, descricao: str) -> bool:
    texto = f"{titulo} {descricao}".lower()
    return any(b in texto for b in BLOQUEADOS_PROPAGANDA)


def eh_mesma_noticia(novo_titulo: str, titulos_anteriores: List[str]) -> bool:
    padrao_token = r"[a-z0-9áàéíóúâêîôûãõçü]+"
    tokens_novo = {
        p for p in re.findall(padrao_token, novo_titulo.lower())
        if len(p) > 3 and p not in STOPWORDS_DEDUP
    }
    if not tokens_novo:
        return False

    for antigo in titulos_anteriores:
        tokens_antigo = {
            p for p in re.findall(padrao_token, antigo.lower())
            if len(p) > 3 and p not in STOPWORDS_DEDUP
        }
        if not tokens_antigo:
            continue
        inter = tokens_novo & tokens_antigo
        ratio = len(inter) / min(len(tokens_novo), len(tokens_antigo))
        if len(inter) >= 2 and ratio >= 0.35:
            return True
    return False


def extrair_data_publicacao(item: ET.Element) -> Optional[datetime]:
    for tag in (
        "pubDate",
        "{http://purl.org/dc/elements/1.1/}date",
        "published",
        "updated",
        "{http://www.w3.org/2005/Atom}published",
        "{http://www.w3.org/2005/Atom}updated",
    ):
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


# ==============================================================================
# 1. SCRAPING COM TRAFILATURA E SÍNTESE COM GEMINI FLASH
# ==============================================================================
def _sync_extrair_artigo_trafilatura(url: str, desc_fallback: str) -> Tuple[str, str]:
    """
    Baixa a página completa e extrai o texto jornalístico limpo usando Trafilatura.
    Retorna (texto_limpo, html_bruto).
    """
    html_bruto = ""
    texto_extraido = ""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        resp.raise_for_status()
        html_bruto = resp.text
        texto_extraido = trafilatura.extract(
            html_bruto,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
            favor_precision=True,
        ) or ""
    except Exception as e:
        logger.debug(f"Falha ao baixar/extrair com Trafilatura ({url}): {e}")

    if not texto_extraido or len(texto_extraido.strip()) < 150:
        # Fallback para descrição do RSS limpa
        texto_extraido = trafilatura.extract(desc_fallback) or re.sub(r"<[^>]+>", " ", desc_fallback)
        texto_extraido = html.unescape(" ".join(texto_extraido.split()))

    return texto_extraido.strip(), html_bruto


def resumo_contingencia_local(texto_bruto: str, titulo: str) -> str:
    """Fallback determinístico caso a API gratuita do Gemini atinja limite de taxa."""
    if not texto_bruto:
        return ""
    frases = [f.strip() for f in re.split(r"(?<=[.!?])\s+", texto_bruto) if len(f.strip()) > 35]
    selecionadas = []
    acumulado = 0
    for f in frases:
        if titulo and f.lower().startswith(titulo[:25].lower()):
            continue
        if acumulado + len(f) <= 600:
            selecionadas.append(f)
            acumulado += len(f)
        else:
            break
    if not selecionadas:
        return texto_bruto[:550].rsplit(" ", 1)[0] + "."
    if len(selecionadas) >= 3:
        return f"{selecionadas[0]}\n\n{' '.join(selecionadas[1:-1])}\n\n{selecionadas[-1]}"
    return "\n\n".join(selecionadas)


def _sync_gerar_sintese_gemini(
    cliente_gemini: Optional[genai.Client], titulo: str, texto_artigo: str
) -> str:
    """
    Chama o Gemini Flash (Free Tier) para redigir o resumo em 3 blocos:
    Começo (Fato), Meio (Contexto/Números) e Fim (Impacto no Mercado).
    """
    if not cliente_gemini:
        return resumo_contingencia_local(texto_artigo, titulo)

    prompt = (
        "Você é um editor-chefe de mercado financeiro. Redija uma síntese jornalística "
        "densa, direta e analítica em português do Brasil com base na notícia abaixo.\n\n"
        "REGRAS OBRIGATÓRIAS:\n"
        "1. Estruture o texto exatamente em 3 parágrafos curtos separados por uma linha em branco:\n"
        "   - Parágrafo 1 (Começo - O Fato): O acontecimento central de forma direta e objetiva.\n"
        "   - Parágrafo 2 (Meio - Contexto e Números): Valores, percentuais, indicadores e contexto factual.\n"
        "   - Parágrafo 3 (Fim - Impacto no Mercado): A leitura de impacto prático para investidores, ativos, juros ou câmbio.\n"
        "2. NÃO escreva rótulos como 'Começo:', 'Fato:', 'Meio:' ou 'Impacto:'. Escreva apenas o texto corrido dos 3 parágrafos.\n"
        "3. NÃO repita o título da notícia.\n"
        "4. NÃO use formatação Markdown (proibido usar asteriscos ** ou #).\n"
        "5. O texto total (somando os 3 parágrafos) DEVE ter entre 420 e 620 caracteres.\n\n"
        f"TÍTULO: {titulo}\n"
        f"CONTEÚDO DA MATÉRIA:\n{texto_artigo[:7000]}"
    )

    try:
        resposta = cliente_gemini.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=450,
            ),
        )
        texto_gerado = (resposta.text or "").strip()
        # Remove eventuais marcações Markdown caso o modelo insira
        texto_gerado = re.sub(r"[*_#`]+", "", texto_gerado).strip()
        if len(texto_gerado) >= 150:
            return texto_gerado
    except Exception as e:
        logger.warning(f"Aviso na API Gemini (usando fallback local): {e}")

    return resumo_contingencia_local(texto_artigo, titulo)


def montar_post_html(categoria: str, titulo: str, sintese: str, limite_chars: int) -> str:
    """Formata o post em HTML escapado garantindo que nunca estoure o limite do Telegram."""
    t_limpo = html.unescape(titulo).strip()
    if " - " in t_limpo:
        t_limpo, _ = t_limpo.rsplit(" - ", 1)
    if len(t_limpo) > 180:
        t_limpo = t_limpo[:177].rsplit(" ", 1)[0] + "..."
    if t_limpo and t_limpo[-1] not in ".!?":
        t_limpo += "."

    cabecalho = f"{categoria}\n\n<b>{html.escape(t_limpo)}</b>\n\n"
    corpo = html.escape(html.unescape(sintese.strip()))

    orcamento = limite_chars - len(cabecalho) - 5
    if len(corpo) > orcamento:
        corpo = corpo[:orcamento].rsplit(" ", 1)[0].rstrip(",;:-") + "."

    return f"{cabecalho}{corpo}"


# ==============================================================================
# 2. EXTRAÇÃO E DOWNLOAD DE VÍDEO COM YT-DLP
# ==============================================================================
def detectar_url_video_candidata(
    item_rss: ET.Element, html_artigo: str, url_artigo: str
) -> Optional[str]:
    """
    Identifica se a matéria possui vídeo no RSS ou players embutidos no HTML
    (YouTube, Globoplay, Dailymotion, Vimeo, JWPlayer ou tag <video>).
    """
    # 1. Verifica tags de mídia no próprio XML do RSS
    for tag in ("enclosure", "{http://search.yahoo.com/mrss/}content"):
        el = item_rss.find(tag)
        if el is not None and "url" in el.attrib:
            u = el.attrib["url"].strip()
            tipo = el.attrib.get("type", "").lower()
            if "video" in tipo or u.lower().endswith((".mp4", ".mov", ".m3u8")):
                return u

    if not html_artigo:
        return None

    # 2. Procura iframes de vídeo (YouTube, Vimeo, Dailymotion, Players de notícia)
    padroes_embed = (
        r'<iframe[^>]+src=["\'](https?://(?:www\.)?(?:youtube\.com/embed/[^"\'?]+|youtu\.be/[^"\'?]+|player\.vimeo\.com/video/\d+|www\.dailymotion\.com/embed/video/[^"\'?]+))["\']',
        r'<video[^>]+src=["\'](https?://[^"\']+\.(?:mp4|m3u8)[^"\']*)["\']',
        r'<source[^>]+src=["\'](https?://[^"\']+\.(?:mp4|m3u8)[^"\']*)["\']',
        r'<meta[^>]+property=["\']og:video(?::secure_url|:url)?["\'][^>]+content=["\'](https?://[^"\']+)["\']',
    )
    for padrao in padroes_embed:
        m = re.search(padrao, html_artigo, flags=re.IGNORECASE)
        if m:
            return html.unescape(m.group(1).strip())

    # 3. Se a página for de portal com suporte nativo no yt-dlp e contiver indício de player
    if any(marcador in html_artigo.lower() for marcador in ("video-player", "wp-video", "data-video-id", "vjs-tech")):
        return url_artigo

    return None


def _sync_baixar_video_ytdlp(url_video: str, pasta_temp: str) -> Optional[str]:
    """
    Baixa o vídeo temporariamente no runner via yt-dlp garantindo formato MP4
    e tamanho máximo de 45 MB compatível com o Telegram.
    """
    template_saida = os.path.join(pasta_temp, f"video_{uuid.uuid4().hex[:8]}.%(ext)s")
    ydl_opts = {
        "format": "best[ext=mp4][filesize<45M]/best[ext=mp4]/best[filesize<45M]",
        "outtmpl": template_saida,
        "max_filesize": MAX_VIDEO_BYTES,
        "noplaylist": True,
        "playlist_items": "1",
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 15,
        "retries": 2,
        "merge_output_format": "mp4",
        "http_headers": HEADERS,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url_video, download=True)

        for nome_arq in os.listdir(pasta_temp):
            caminho_completo = os.path.join(pasta_temp, nome_arq)
            if os.path.isfile(caminho_completo):
                tamanho = os.path.getsize(caminho_completo)
                if 10_000 < tamanho <= MAX_VIDEO_BYTES:
                    return caminho_completo
                logger.warning(f"Vídeo descartado por tamanho fora do limite ({tamanho} bytes).")
    except Exception as e:
        logger.info(f"yt-dlp não obteve vídeo válido de {url_video}: {e}")

    return None


# ==============================================================================
# 3. ENVIO PARA O TELEGRAM (SEND_VIDEO COM FALLBACK PARA SEND_MESSAGE)
# ==============================================================================
def _sync_enviar_video_telegram(
    token: str, chat_id: str, caminho_video: str, legenda_html: str, reply_markup: Dict[str, Any]
) -> Tuple[int, str]:
    """Faz upload multipart do arquivo MP4 local via endpoint sendVideo."""
    url = f"https://api.telegram.org/bot{token}/sendVideo"
    data = {
        "chat_id": chat_id,
        "caption": legenda_html,
        "parse_mode": "HTML",
        "supports_streaming": "true",
        "reply_markup": json.dumps(reply_markup),
    }
    try:
        with open(caminho_video, "rb") as arquivo_binario:
            files = {"video": (os.path.basename(caminho_video), arquivo_binario, "video/mp4")}
            resp = requests.post(url, data=data, files=files, timeout=90)
            return resp.status_code, resp.text
    except Exception as e:
        return 0, str(e)


def _sync_enviar_mensagem_telegram(
    token: str, chat_id: str, texto_html: str, reply_markup: Dict[str, Any]
) -> Tuple[int, str]:
    """Envia post via sendMessage com preview nativo ativado."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": texto_html,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
        "reply_markup": reply_markup,
    }
    try:
        resp = requests.post(url, json=payload, timeout=25)
        return resp.status_code, resp.text
    except Exception as e:
        return 0, str(e)


async def publicar_noticia_telegram(
    token: str, chat_id: str, noticia: Dict[str, Any]
) -> bool:
    """
    Orquestra o download temporário do vídeo, envio via sendVideo, limpeza
    obrigatória do arquivo local e fallback automático para sendMessage.
    """
    botao = gerar_botao_grupo_aleatorio()
    url_video = noticia.get("video_candidato")
    pasta_temp = tempfile.mkdtemp(prefix="radar_vid_")

    try:
        if url_video:
            logger.info(f"Tentando baixar vídeo com yt-dlp: {url_video}")
            caminho_video = await asyncio.to_thread(_sync_baixar_video_ytdlp, url_video, pasta_temp)

            if caminho_video and os.path.exists(caminho_video):
                for tentativa in range(1, 3):
                    status, resp = await asyncio.to_thread(
                        _sync_enviar_video_telegram,
                        token,
                        chat_id,
                        caminho_video,
                        noticia["legenda_video"],
                        botao,
                    )
                    if status == 200:
                        logger.info("Vídeo enviado nativamente via sendVideo.")
                        return True
                    if status == 429:
                        espera = 10
                        try:
                            espera = int(json.loads(resp).get("parameters", {}).get("retry_after", 10)) + 1
                        except Exception:
                            pass
                        logger.warning(f"Flood control no sendVideo. Aguardando {espera}s...")
                        await asyncio.sleep(espera)
                    else:
                        logger.warning(f"Falha no sendVideo (HTTP {status}): {resp}")
                        break
    finally:
        # Limpeza obrigatória do diretório e arquivos temporários de vídeo pós-envio
        shutil.rmtree(pasta_temp, ignore_errors=True)

    # Fallback para sendMessage caso não exista vídeo na página ou o download falhe
    for tentativa in range(1, 4):
        status, resp = await asyncio.to_thread(
            _sync_enviar_mensagem_telegram,
            token,
            chat_id,
            noticia["texto_mensagem"],
            botao,
        )
        if status == 200:
            logger.info("Notícia publicada via sendMessage (fallback sem vídeo).")
            return True
        if status == 429:
            espera = 8
            try:
                espera = int(json.loads(resp).get("parameters", {}).get("retry_after", 8)) + 1
            except Exception:
                pass
            logger.warning(f"Flood control no sendMessage. Aguardando {espera}s...")
            await asyncio.sleep(espera)
        elif status in (0, 500, 502, 503, 504):
            await asyncio.sleep(2 ** tentativa)
        else:
            logger.error(f"Erro definitivo no sendMessage (HTTP {status}): {resp}")
            return False

    return False


# ==============================================================================
# VARREDURA CONCORRENTE DOS FEEDS E PIPELINE PRINCIPAL
# ==============================================================================
def _sync_buscar_feed(url_feed: str) -> bytes:
    resp = requests.get(url_feed, headers=HEADERS, timeout=10)
    resp.raise_for_status()
    return resp.content


async def processar_feed(
    url_feed: str, links_historico: set, agora_utc: datetime, sem: asyncio.Semaphore
) -> List[Dict[str, Any]]:
    itens_validos = []
    try:
        async with sem:
            conteudo = await asyncio.to_thread(_sync_buscar_feed, url_feed)
        root = ET.fromstring(conteudo)

        for item in root.findall(".//item"):
            link_el = item.find("link")
            title_el = item.find("title")
            desc_el = item.find("description")
            content_el = item.find("{http://purl.org/rss/1.0/modules/content/}encoded")

            link = link_el.text.strip() if link_el is not None and link_el.text else ""
            title = html.unescape(title_el.text.strip()) if title_el is not None and title_el.text else ""
            desc_bruta = (
                desc_el.text
                if desc_el is not None and desc_el.text
                else (content_el.text if content_el is not None and content_el.text else "")
            )

            if not link or not title or link in links_historico:
                continue
            if len(title) < 25:
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
    except Exception as e:
        logger.warning(f"Aviso ao ler feed {url_feed}: {e}")

    return itens_validos


async def coletar_e_preparar_noticias(
    cliente_gemini: Optional[genai.Client], limite: int = MAX_NOTICIAS_POR_VEZ
) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
    links_historico, titulos_historico = carregar_historico()
    set_links = set(links_historico)
    titulos_rodada = list(titulos_historico)
    agora_utc = datetime.now(timezone.utc)
    sem = asyncio.Semaphore(MAX_CONCORRENCIA_HTTP)

    resultados_feeds = await asyncio.gather(
        *(processar_feed(url, set_links, agora_utc, sem) for url in FEEDS)
    )

    candidatos = [item for sublista in resultados_feeds for item in sublista]
    candidatos.sort(key=lambda x: x["dt_pub"], reverse=True)

    noticias_prontas = []
    for cand in candidatos:
        title = cand["title"]
        link = cand["link"]
        desc_bruta = cand["desc_bruta"]
        item_rss = cand["item"]

        if link in set_links or eh_mesma_noticia(title, titulos_rodada):
            continue

        # 1. Extração completa com Trafilatura
        async with sem:
            texto_artigo, html_bruto = await asyncio.to_thread(
                _sync_extrair_artigo_trafilatura, link, desc_bruta
            )

        if not texto_artigo or len(texto_artigo) < 140:
            continue

        # 2. Síntese com Gemini Flash (Começo: Fato, Meio: Números, Fim: Impacto)
        sintese = await asyncio.to_thread(
            _sync_gerar_sintese_gemini, cliente_gemini, title, texto_artigo
        )
        if not sintese or len(sintese) < 120:
            continue

        categoria = categorizar_materia(title, f"{texto_artigo[:500]} {sintese}")
        legenda_video = montar_post_html(categoria, title, sintese, LIMITE_LEGENDA_TELEGRAM)
        # No fallback sendMessage, adicionamos link invisível no início para forçar o card de preview da página
        link_preview = f'<a href="{html.escape(link)}">&#8205;</a>'
        texto_mensagem = link_preview + montar_post_html(categoria, title, sintese, LIMITE_TEXTO_TELEGRAM - 150)

        video_candidato = detectar_url_video_candidata(item_rss, html_bruto, link)

        noticias_prontas.append({
            "link": link,
            "titulo": title,
            "legenda_video": legenda_video,
            "texto_mensagem": texto_mensagem,
            "video_candidato": video_candidato,
        })

        titulos_rodada.append(title)
        set_links.add(link)

        # Pausa curta para respeitar o limite de 15 RPM do Gemini Free Tier
        if cliente_gemini:
            await asyncio.sleep(4)

        if len(noticias_prontas) >= limite:
            break

    noticias_prontas.reverse()
    return noticias_prontas, links_historico, titulos_historico


async def main() -> None:
    token, chat_id, cliente_gemini = obter_credenciais()

    noticias, links_historico, titulos_historico = await coletar_e_preparar_noticias(
        cliente_gemini=cliente_gemini, limite=MAX_NOTICIAS_POR_VEZ
    )

    if not noticias:
        logger.info("Nenhuma notícia inédita encontrada neste ciclo.")
        return

    logger.info(f"Iniciando envio de {len(noticias)} notícias sintetizadas.")
    for idx, noticia in enumerate(noticias, start=1):
        sucesso = await publicar_noticia_telegram(token, chat_id, noticia)
        if sucesso:
            links_historico.append(noticia["link"])
            titulos_historico.append(noticia["titulo"])
            salvar_historico(links_historico, titulos_historico)
            logger.info(f"[{idx}/{len(noticias)}] Publicada com sucesso: {noticia['titulo']}")
            if idx < len(noticias):
                await asyncio.sleep(3)
        else:
            logger.error(f"Falha ao publicar notícia: {noticia['titulo']}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Execução encerrada manualmente.")
