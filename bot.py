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
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

# Importações opcionais resilientes (não quebram o script caso ausentes localmente)
try:
    import trafilatura
except ImportError:
    trafilatura = None

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None

# ==============================================================================
# CONFIGURAÇÃO DE LOGGING DETALHADO
# ==============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("RadarMercadoBot")

# ==============================================================================
# PARÂMETROS DE OPERAÇÃO (CICLOS DE 5 MINUTOS)
# ==============================================================================
HISTORICO_ARQUIVO = os.getenv("HISTORICO_ARQUIVO", "historico_enviados.json")
MAX_HISTORICO = 1000
MAX_NOTICIAS_POR_VEZ = int(os.getenv("MAX_NOTICIAS_POR_VEZ", "5"))
# 180 min garante que atrasos de cache no RSS dos portais não descartem notícias novas
MAX_IDADE_MINUTOS = int(os.getenv("MAX_IDADE_MINUTOS", "180"))
CICLOS_INTERNOS = int(os.getenv("CICLOS_INTERNOS", "1"))
INTERVALO_SEGUNDOS = int(os.getenv("INTERVALO_SEGUNDOS", "300"))

LIMITE_LEGENDA_TELEGRAM = 1024
LIMITE_TEXTO_TELEGRAM = 4096
MAX_VIDEO_BYTES = 45 * 1024 * 1024
MAX_CONCORRENCIA_HTTP = 8
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


# ==============================================================================
# CREDENCIAIS E REDE NATIVA (SEM DEPENDÊNCIA FORÇADA)
# ==============================================================================
def obter_credenciais() -> Tuple[str, str, Any]:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    gemini_key = (os.getenv("GEMINI_API_KEY") or "").strip()

    if not token or not chat_id:
        logger.critical(
            "ERRO FATAL: TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID não configurados! "
            "Cadastre-os em Settings -> Secrets and variables -> Actions no GitHub."
        )
        sys.exit(1)

    cliente_gemini = None
    if gemini_key and genai is not None:
        try:
            cliente_gemini = genai.Client(api_key=gemini_key)
        except Exception as e:
            logger.warning(f"Falha ao iniciar cliente Gemini ({e}). Usando resumo rápido local.")

    return token, chat_id, cliente_gemini


def normalizar_url_midia(url: Optional[str]) -> Optional[str]:
    if not url or not isinstance(url, str):
        return None
    url = html.unescape(url.strip())
    if not url.startswith(("http://", "https://")):
        return None
    partes = urllib.parse.urlsplit(url)
    caminho = urllib.parse.quote(urllib.parse.unquote(partes.path), safe="/:@&+$,;=-_.~")
    return urllib.parse.urlunsplit((partes.scheme, partes.netloc, caminho, partes.query, partes.fragment))


def _sync_http_get(url: str, timeout: int = 10) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(10 * 1024 * 1024)


def _sync_http_post_json(url: str, payload: Dict[str, Any], timeout: int = 25) -> Tuple[int, str]:
    dados = json.dumps(payload).encode("utf-8")
    headers = {**HEADERS, "Content-Type": "application/json; charset=utf-8"}
    req = urllib.request.Request(url, data=dados, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        corpo = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        return e.code, corpo
    except Exception as e:
        return 0, str(e)


def _sync_http_post_multipart(
    url: str,
    fields: Dict[str, str],
    file_field: str,
    file_bytes: bytes,
    filename: str = "media.jpg",
    content_type: str = "image/jpeg",
    timeout: int = 60,
) -> Tuple[int, str]:
    boundary = f"----BotBoundary{uuid.uuid4().hex}"
    body = bytearray()

    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.extend(f"{value}\r\n".encode("utf-8"))

    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(
        f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode("utf-8")
    )
    body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
    body.extend(file_bytes)
    body.extend(f"\r\n--{boundary}--\r\n".encode("utf-8"))

    headers = {
        **HEADERS,
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body)),
    }
    req = urllib.request.Request(url, data=bytes(body), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        corpo = e.read().decode("utf-8", errors="replace") if e.fp else str(e)
        return e.code, corpo
    except Exception as e:
        return 0, str(e)


# ==============================================================================
# HISTÓRICO E FILTROS
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
            logger.error(f"Erro ao ler histórico: {e}")
    return [], []


def salvar_historico(links: List[str], titulos: List[str]) -> None:
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
        logger.error(f"Erro ao salvar histórico: {e}")


def gerar_botao_grupo_aleatorio() -> Dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": random.choice(TEXTOS_BOTAO_GRUPO), "url": URL_GRUPO_CTA}]
        ]
    }


def contem_termo(texto: str, lista_termos: Tuple[str, ...]) -> bool:
    for termo in lista_termos:
        if re.search(r"(?<!\w)" + re.escape(termo) + r"(?!\w)", texto, flags=re.IGNORECASE):
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
        if len(inter) >= 2 and (len(inter) / min(len(tokens_novo), len(tokens_antigo))) >= 0.38:
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


def limpar_html_basico(txt: str) -> str:
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
    txt = re.sub(r"The post .*? appeared first on .*$", "", txt, flags=re.IGNORECASE)
    txt = re.sub(r"O post .*? apareceu primeiro em .*$", "", txt, flags=re.IGNORECASE)
    return " ".join(txt.split()).strip()


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
# EXTRAÇÃO HÍBRIDA (TRAFILATURA + FALLBACK RSS) E SÍNTESE
# ==============================================================================
def extrair_og_image(raw_html: str) -> Optional[str]:
    for padrao in (
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
    ):
        m = re.search(padrao, raw_html, re.IGNORECASE)
        if m:
            url = normalizar_url_midia(m.group(1))
            if url:
                return url
    return None


def extrair_midias_rss(item: ET.Element, desc_bruta: str) -> Tuple[Optional[str], Optional[str]]:
    foto_url = None
    video_url = None

    for tag in ("enclosure", "{http://search.yahoo.com/mrss/}content", "{http://search.yahoo.com/mrss/}thumbnail"):
        el = item.find(tag)
        if el is not None and "url" in el.attrib:
            u = normalizar_url_midia(el.attrib["url"])
            tipo = el.attrib.get("type", "").lower()
            if u:
                if "video" in tipo or u.lower().endswith((".mp4", ".mov", ".m3u8")):
                    video_url = video_url or u
                elif not foto_url and ("image" in tipo or u.startswith("http")):
                    foto_url = u

    if not foto_url and desc_bruta:
        busca = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc_bruta, re.IGNORECASE)
        if busca:
            foto_url = normalizar_url_midia(busca.group(1))

    return foto_url, video_url


def _sync_extrair_artigo_completo(
    url: str, desc_bruta: str, titulo: str
) -> Tuple[str, Optional[str], str]:
    """
    Tenta extrair o artigo via Trafilatura ou parser de parágrafos.
    Se o portal bloquear o IP do GitHub (HTTP 403), usa a descrição do RSS sem descartar a notícia.
    """
    html_bruto = ""
    texto_extraido = ""
    foto_og = None

    try:
        html_bytes = _sync_http_get(url, timeout=8)
        html_bruto = html_bytes.decode("utf-8", errors="replace")
        foto_og = extrair_og_image(html_bruto)

        if trafilatura is not None:
            texto_extraido = trafilatura.extract(
                html_bruto,
                include_comments=False,
                include_tables=True,
                favor_precision=True,
            ) or ""

        if not texto_extraido or len(texto_extraido) < 120:
            html_limpo = re.sub(
                r"<(head|script|style|svg|nav|footer|header|aside|form|noscript)\b[^>]*>.*?</\1>",
                " ",
                html_bruto,
                flags=re.DOTALL | re.IGNORECASE,
            )
            paragrafos = re.findall(r"<p(?:\s[^>]*)?>(.*?)</p>", html_limpo, flags=re.DOTALL | re.IGNORECASE)
            blocos = []
            for p in paragrafos:
                t = limpar_html_basico(p)
                if len(t) >= 45 and "cookie" not in t.lower() and "assinante" not in t.lower():
                    blocos.append(t)
                if sum(len(b) for b in blocos) >= 1200:
                    break
            texto_extraido = " ".join(blocos)
    except Exception as e:
        logger.debug(f"Portal bloqueou raspagem direta ({url}): {e}")

    if not texto_extraido or len(texto_extraido.strip()) < 60:
        texto_extraido = limpar_html_basico(desc_bruta)

    if not texto_extraido or len(texto_extraido.strip()) < 30:
        texto_extraido = titulo

    return texto_extraido.strip(), foto_og, html_bruto


def formatar_resumo_local(texto_bruto: str, titulo: str) -> str:
    t = limpar_html_basico(texto_bruto)
    if not t or len(t) < 40:
        return f"{titulo}."

    frases = [f.strip() for f in re.split(r"(?<=[.!?])\s+", t) if len(f.strip()) > 25]
    selecionadas = []
    acumulado = 0
    for f in frases:
        if titulo and f.lower().startswith(titulo[:25].lower()):
            continue
        if acumulado + len(f) <= 580:
            selecionadas.append(f)
            acumulado += len(f)
        else:
            break

    if not selecionadas:
        res = t[:550].rsplit(" ", 1)[0].rstrip(",;:-") + "."
        return res

    if len(selecionadas) >= 3:
        return f"{selecionadas[0]}\n\n{' '.join(selecionadas[1:-1])}\n\n{selecionadas[-1]}"
    return "\n\n".join(selecionadas)


def _sync_gerar_sintese(cliente_gemini: Any, titulo: str, texto_artigo: str) -> str:
    if not cliente_gemini or types is None or len(texto_artigo) < 120:
        return formatar_resumo_local(texto_artigo, titulo)

    prompt = (
        "Você é um editor de mercado financeiro. Redija uma síntese jornalística "
        "em português do Brasil com base na notícia abaixo.\n"
        "REGRAS:\n"
        "1. Escreva exatamente 3 parágrafos curtos separados por linha em branco:\n"
        "   - Parágrafo 1: O fato principal de forma direta.\n"
        "   - Parágrafo 2: Contexto e números divulgados.\n"
        "   - Parágrafo 3: Impacto prático para o mercado e investidores.\n"
        "2. NÃO escreva rótulos como 'Fato:', 'Meio:' ou 'Impacto:'.\n"
        "3. NÃO repita o título e NÃO use asteriscos (**).\n"
        "4. O texto total deve ter entre 400 e 600 caracteres.\n\n"
        f"TÍTULO: {titulo}\n"
        f"MATÉRIA:\n{texto_artigo[:6000]}"
    )

    try:
        resp = cliente_gemini.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.2, max_output_tokens=400),
        )
        gerado = re.sub(r"[*_#`]+", "", (resp.text or "")).strip()
        if len(gerado) >= 100:
            return gerado
    except Exception as e:
        logger.warning(f"Fallback para resumo local (Gemini indisponível): {e}")

    return formatar_resumo_local(texto_artigo, titulo)


def montar_post_html(categoria: str, titulo: str, sintese: str, limite_chars: int) -> str:
    t_limpo = html.unescape(titulo).strip()
    if " - " in t_limpo:
        t_limpo, _ = t_limpo.rsplit(" - ", 1)
    if len(t_limpo) > 180:
        t_limpo = t_limpo[:177].rsplit(" ", 1)[0] + "..."
    if t_limpo and t_limpo[-1] not in ".!?":
        t_limpo += "."

    cabecalho = f"{categoria}\n\n<b>{html.escape(t_limpo)}</b>\n\n"
    corpo = html.escape(html.unescape(sintese.strip()))

    if corpo.lower() == html.escape(t_limpo).lower():
        return cabecalho.strip()

    orcamento = limite_chars - len(cabecalho) - 5
    if len(corpo) > orcamento:
        corpo = corpo[:orcamento].rsplit(" ", 1)[0].rstrip(",;:-") + "."

    return f"{cabecalho}{corpo}"


# ==============================================================================
# DOWNLOAD DE VÍDEO (YT-DLP) E PUBLICAÇÃO MULTIMÍDIA
# ==============================================================================
def _sync_baixar_video_ytdlp(url_video: str, pasta_temp: str) -> Optional[str]:
    if yt_dlp is None:
        return None
    template_saida = os.path.join(pasta_temp, f"vid_{uuid.uuid4().hex[:8]}.%(ext)s")
    ydl_opts = {
        "format": "best[ext=mp4][filesize<45M]/best[ext=mp4]/best[filesize<45M]",
        "outtmpl": template_saida,
        "max_filesize": MAX_VIDEO_BYTES,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 12,
        "retries": 1,
        "http_headers": HEADERS,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url_video, download=True)
        for arq in os.listdir(pasta_temp):
            caminho = os.path.join(pasta_temp, arq)
            if os.path.isfile(caminho) and 10_000 < os.path.getsize(caminho) <= MAX_VIDEO_BYTES:
                return caminho
    except Exception:
        pass
    return None


async def publicar_noticia_telegram(token: str, chat_id: str, noticia: Dict[str, Any]) -> bool:
    botao = gerar_botao_grupo_aleatorio()
    base_api = f"https://api.telegram.org/bot{token}"
    pasta_temp = tempfile.mkdtemp(prefix="radar_vid_")

    try:
        # 1. Tenta enviar Vídeo Nativo se existir
        if noticia.get("video"):
            caminho_vid = await asyncio.to_thread(_sync_baixar_video_ytdlp, noticia["video"], pasta_temp)
            if caminho_vid and os.path.exists(caminho_vid):
                with open(caminho_vid, "rb") as f:
                    vid_bytes = f.read()
                fields = {
                    "chat_id": chat_id,
                    "caption": noticia["legenda_midia"],
                    "parse_mode": "HTML",
                    "supports_streaming": "true",
                    "reply_markup": json.dumps(botao),
                }
                st, _ = await asyncio.to_thread(
                    _sync_http_post_multipart,
                    f"{base_api}/sendVideo",
                    fields,
                    "video",
                    vid_bytes,
                    "video.mp4",
                    "video/mp4",
                )
                if st == 200:
                    return True
    finally:
        shutil.rmtree(pasta_temp, ignore_errors=True)

    # 2. Tenta enviar Foto de Capa (por URL ou upload binário)
    if noticia.get("imagem"):
        payload_foto = {
            "chat_id": chat_id,
            "photo": noticia["imagem"],
            "caption": noticia["legenda_midia"],
            "parse_mode": "HTML",
            "reply_markup": botao,
        }
        st, _ = await asyncio.to_thread(_sync_http_post_json, f"{base_api}/sendPhoto", payload_foto)
        if st == 200:
            return True

        try:
            img_bytes = await asyncio.to_thread(_sync_http_get, noticia["imagem"], 10)
            fields = {
                "chat_id": chat_id,
                "caption": noticia["legenda_midia"],
                "parse_mode": "HTML",
                "reply_markup": json.dumps(botao),
            }
            st_up, _ = await asyncio.to_thread(
                _sync_http_post_multipart, f"{base_api}/sendPhoto", fields, "photo", img_bytes
            )
            if st_up == 200:
                return True
        except Exception:
            pass

    # 3. Fallback garantido para Mensagem de Texto com Preview
    payload_txt = {
        "chat_id": chat_id,
        "text": noticia["texto_mensagem"],
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
        "reply_markup": botao,
    }
    st_txt, resp_txt = await asyncio.to_thread(_sync_http_post_json, f"{base_api}/sendMessage", payload_txt)
    if st_txt == 200:
        return True

    logger.error(f"Erro da API do Telegram (HTTP {st_txt}): {resp_txt}")
    return False


# ==============================================================================
# VARREDURA DOS FEEDS E EXECUÇÃO DO CICLO
# ==============================================================================
async def processar_feed(
    url_feed: str, links_historico: set, agora_utc: datetime, sem: asyncio.Semaphore
) -> List[Dict[str, Any]]:
    itens = []
    try:
        async with sem:
            conteudo = await asyncio.to_thread(_sync_http_get, url_feed, 10)
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

            if not link or not title or link in links_historico or len(title) < 20:
                continue

            dt_pub = extrair_data_publicacao(item)
            if not dt_pub:
                continue

            idade_min = (agora_utc - dt_pub).total_seconds() / 60.0
            if idade_min > MAX_IDADE_MINUTOS or idade_min < -15.0:
                continue

            if eh_propaganda_ou_lixo(title, desc_bruta):
                continue

            itens.append({
                "dt_pub": dt_pub,
                "item": item,
                "link": link,
                "title": title,
                "
