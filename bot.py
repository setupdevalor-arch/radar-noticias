import asyncio
import email.utils
import html
import json
import logging
import os
import random
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

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
# CONFIGURAÇÕES E CONSTANTES DE PRODUÇÃO
# ==============================================================================
HISTORICO_ARQUIVO = os.getenv("HISTORICO_ARQUIVO", "historico_enviados.json")
MAX_HISTORICO = 800
MAX_NOTICIAS_POR_VEZ = 5
MAX_IDADE_MINUTOS = 25
LIMITE_LEGENDA_TELEGRAM = 1024
MAX_CONCORRENCIA_HTTP = 6
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
        "Chrome/124.0.0.0 Safari/537.36"
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
# CAMADA DE REDE E SEGURANÇA (ASSÍNCRONA E NÃO-BLOQUEANTE)
# ==============================================================================
def obter_credenciais() -> Tuple[str, str]:
    """Valida e recupera credenciais exclusivamente do ambiente."""
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

    if not token or not chat_id:
        logger.critical(
            "Variáveis de ambiente obrigatórias ausentes: "
            "defina TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID."
        )
        sys.exit(1)

    return token, chat_id


def normalizar_url_midia(url: Optional[str]) -> Optional[str]:
    """Sanitiza URLs de mídia evitando que espaços ou caracteres quebrem o Telegram."""
    if not url or not isinstance(url, str):
        return None
    url = html.unescape(url.strip())
    if not url.startswith(("http://", "https://")):
        return None
    partes = urllib.parse.urlsplit(url)
    caminho_seguro = urllib.parse.quote(urllib.parse.unquote(partes.path), safe="/:@&+$,;=-_.~")
    return urllib.parse.urlunsplit((partes.scheme, partes.netloc, caminho_seguro, partes.query, partes.fragment))


def _sync_http_get(url: str, timeout: int = 10) -> bytes:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        # Limita leitura a 10 MB para evitar exaustão de memória
        return resp.read(10 * 1024 * 1024)


async def async_http_get(url: str, sem: asyncio.Semaphore, timeout: int = 10) -> bytes:
    """Executa requisição GET em thread separada sem bloquear o event loop."""
    async with sem:
        return await asyncio.to_thread(_sync_http_get, url, timeout)


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
    timeout: int = 30,
) -> Tuple[int, str]:
    """Envia mídia binária via multipart/form-data quando a URL direta falha no Telegram."""
    boundary = f"----TelegramBotBoundary{uuid.uuid4().hex}"
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


async def post_telegram_com_retry(
    url: str, payload: Dict[str, Any], timeout: int = 25, max_tentativas: int = 3
) -> Tuple[int, str]:
    """Gerencia envio para a API do Telegram respeitando Flood Control (HTTP 429) e erros 5xx."""
    for tentativa in range(1, max_tentativas + 1):
        status, resposta = await asyncio.to_thread(_sync_http_post_json, url, payload, timeout)

        if status == 200:
            return status, resposta

        if status == 429:
            retry_after = 5
            try:
                dados_erro = json.loads(resposta)
                retry_after = int(dados_erro.get("parameters", {}).get("retry_after", 5)) + 1
            except Exception:
                pass
            logger.warning(f"Rate limit atingido (HTTP 429). Aguardando {retry_after}s antes de tentar novamente.")
            await asyncio.sleep(retry_after)
            continue

        if status in (0, 500, 502, 503, 504) and tentativa < max_tentativas:
            espera = 2 ** tentativa
            logger.warning(f"Erro transitório HTTP {status} (tentativa {tentativa}). Aguardando {espera}s...")
            await asyncio.sleep(espera)
            continue

        return status, resposta

    return 0, "Limite de tentativas excedido"


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
    """Salva o histórico de forma atômica evitando corrupção de arquivo em caso de falha."""
    dados = {
        "links": list(dict.fromkeys(links))[-MAX_HISTORICO:],
        "titulos": list(dict.fromkeys(titulos))[-MAX_HISTORICO:],
    }
    diretorio = os.path.dirname(os.path.abspath(HISTORICO_ARQUIVO)) or "."
    try:
        with tempfile.NamedTemporaryFile("w", dir=diretorio, delete=False, encoding="utf-8") as tmp_file:
            json.dump(dados, tmp_file, ensure_ascii=False, indent=2)
            caminho_tmp = tmp_file.name
        os.replace(caminho_tmp, HISTORICO_ARQUIVO)
    except Exception as e:
        logger.error(f"Erro ao salvar histórico de forma atômica: {e}")


# ==============================================================================
# PARSING, SANITIZAÇÃO E REGRAS DE NEGÓCIO
# ==============================================================================
def gerar_botao_grupo_aleatorio() -> Dict[str, Any]:
    return {
        "inline_keyboard": [
            [
                {
                    "text": random.choice(TEXTOS_BOTAO_GRUPO),
                    "url": URL_GRUPO_CTA,
                }
            ]
        ]
    }


def contem_termo(texto: str, lista_termos: Tuple[str, ...]) -> bool:
    """Verifica termos inteiros com segurança mesmo quando contêm símbolos como '&'."""
    for termo in lista_termos:
        padrao = r"(?<!\w)" + re.escape(termo) + r"(?!\w)"
        if re.search(padrao, texto, flags=re.IGNORECASE):
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
    txt = remover_carimbo_data_g1(txt)
    return " ".join(txt.split()).strip()


def paragrafo_parece_codigo_ou_lixo(txt: str) -> bool:
    if not txt or len(txt) < 45:
        return True
    sinais_codigo = (
        "{", "}", "@type", "@context", "schema.org", "dataLayer", "window.", "function(",
        "sameAs", "http://", "https://", "contentUrl", "ethicsPolicy", "isAccessibleForFree",
        "cssSelector", "gostaria de receber", "últimas notícias", "assine a", "exclusivo para assinantes",
        "leia também", "inscreva-se", "newsletter", "compartilhe", "foto:", "crédito:",
        "publicidade", "todos os direitos reservados", "the post", "apareceu primeiro",
        "podcasts", "weekend",
    )
    t_low = txt.lower()
    if any(s in t_low for s in sinais_codigo):
        return True
    if txt.count('"') >= 6 or txt.count("[") >= 2 or txt.count("]") >= 2:
        return True
    return False


def eh_mesma_noticia(novo_titulo: str, titulos_anteriores: List[str]) -> bool:
    """Deduplica notícias preservando tickers da bolsa (ex: PETR4, VALE3) e acentuação completa."""
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


def extrair_og_image_da_pagina(raw_html: str) -> Optional[str]:
    for padrao in (
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
    ):
        m = re.search(padrao, raw_html, re.IGNORECASE)
        if m:
            url = normalizar_url_midia(m.group(1))
            if url:
                return url
    return None


def extrair_midias_do_rss(
    item: ET.Element, desc_bruta: str
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    foto_url = None
    video_url = None
    audio_url = None

    enc = item.find("enclosure")
    if enc is not None and "url" in enc.attrib:
        url = normalizar_url_midia(enc.attrib["url"])
        tipo = enc.attrib.get("type", "").lower()
        if url:
            if "video" in tipo or url.lower().endswith((".mp4", ".mov")):
                video_url = url
            elif "audio" in tipo or url.lower().endswith((".mp3", ".m4a", ".ogg", ".wav")):
                audio_url = url
            elif "image" in tipo or url.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                foto_url = url

    for tag in ("{http://search.yahoo.com/mrss/}content", "{http://search.yahoo.com/mrss/}thumbnail"):
        m = item.find(tag)
        if m is not None and "url" in m.attrib:
            url = normalizar_url_midia(m.attrib["url"])
            tipo = m.attrib.get("type", "").lower()
            if url:
                if "video" in tipo or url.lower().endswith(".mp4"):
                    video_url = video_url or url
                elif "audio" in tipo or url.lower().endswith(".mp3"):
                    audio_url = audio_url or url
                elif not foto_url and ("image" in tipo or url.startswith("http")):
                    foto_url = url

    if not foto_url and desc_bruta:
        busca = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc_bruta, re.IGNORECASE)
        if busca:
            foto_url = normalizar_url_midia(busca.group(1))

    return foto_url, video_url, audio_url


async def obter_conteudo_e_imagem_artigo(
    url: str, desc_bruta: str, titulo: str, sem: asyncio.Semaphore
) -> Tuple[str, Optional[str]]:
    foto_og = None
    raw_html = ""

    try:
        conteudo_bytes = await async_http_get(url, sem, timeout=7)
        raw_html = conteudo_bytes.decode("utf-8", errors="replace")
        foto_og = extrair_og_image_da_pagina(raw_html)
    except Exception as e:
        logger.debug(f"Falha ao raspar página do artigo ({url}): {e}")

    if raw_html:
        html_sem_scripts = re.sub(
            r"<(head|script|style|svg|nav|footer|header|aside|form|noscript|iframe|button)\b[^>]*>.*?</\1>",
            " ",
            raw_html,
            flags=re.DOTALL | re.IGNORECASE,
        )

        paragrafos = re.findall(r"<p(?:\s[^>]*)?>(.*?)</p>", html_sem_scripts, flags=re.DOTALL | re.IGNORECASE)
        blocos_limpos = []

        for p in paragrafos:
            txt = limpar_html(p)
            if paragrafo_parece_codigo_ou_lixo(txt):
                continue
            if re.search(r"\d{1,2}\s+de\s+[a-záéíóúâêîôûãõç]+\s+de\s+\d{4}", txt, re.IGNORECASE):
                continue
            if titulo and titulo[:30].lower() in txt.lower():
                continue

            txt = re.sub(
                r"^(?:Negócios|Economia|INFRA|Tecnologia|Weekend|Podcasts|Journal|Mercados|\s)+",
                "",
                txt,
                flags=re.IGNORECASE,
            )
            txt = remover_carimbo_data_g1(txt)
            if not paragrafo_parece_codigo_ou_lixo(txt):
                blocos_limpos.append(txt)

            if sum(len(b) for b in blocos_limpos) >= 800:
                break

        if blocos_limpos:
            return " ".join(blocos_limpos), foto_og

    t_desc = limpar_caudas_wordpress(limpar_html(desc_bruta))
    t_desc = re.sub(
        r"^.*?\d{1,2}\s+de\s+[a-záéíóúâêîôûãõç]+\s+de\s+\d{4}\s*",
        "",
        t_desc,
        flags=re.IGNORECASE,
    )
    t_desc = re.sub(
        r"^(?:Negócios|Economia|INFRA|Tecnologia|Weekend|Podcasts|Journal|Mercados|\s)+",
        "",
        t_desc,
        flags=re.IGNORECASE,
    )
    t_desc = remover_carimbo_data_g1(t_desc)
    if paragrafo_parece_codigo_ou_lixo(t_desc):
        return "", foto_og
    return t_desc, foto_og


def formatar_resumo_500_600(texto_bruto: str, titulo: str) -> str:
    if not texto_bruto:
        return ""

    t = limpar_caudas_wordpress(texto_bruto)
    t = limpar_html(t)

    if paragrafo_parece_codigo_ou_lixo(t):
        return ""

    t = re.sub(
        r"^.*?(?:Foto|Crédito|Reprodução|BBC|BCC|Reuters|G1|Estadão|Folha)\s*[:\-–—]?\s*(?=[A-Z])",
        "",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"^[^\.\!\?]*?(?:x\s*Real|Dólar\s*x|Foto:|Crédito:)[^\.\!\?]*?(?=[A-Z][a-z]+ [a-z]+ [a-z]+)",
        "",
        t,
        flags=re.IGNORECASE,
    )

    if titulo and t.lower().startswith(titulo[:25].lower()):
        t = t[len(titulo):].strip()
        t = re.sub(r"^[:\-–—\s]+", "", t)

    t = re.sub(
        r"\s*[;:]\s*(veja mais|leia mais|saiba mais|confira|entenda).*$",
        "",
        t,
        flags=re.IGNORECASE,
    ).strip()

    frases = re.split(r"(?<=[.!?])\s+", t)
    frases_selecionadas = []
    tamanho_acumulado = 0

    for f in frases:
        f = remover_carimbo_data_g1(f.strip())
        if not f or paragrafo_parece_codigo_ou_lixo(f):
            continue
        if len(f) > 620:
            f = f[:580].rsplit(" ", 1)[0].rstrip(",;:-") + "."
        if tamanho_acumulado + len(f) <= 620:
            frases_selecionadas.append(f)
            tamanho_acumulado += len(f)
        elif tamanho_acumulado < 460:
            frases_selecionadas.append(f)
            tamanho_acumulado += len(f)
            break
        else:
            break

    if not frases_selecionadas:
        return ""

    if len(frases_selecionadas) >= 3:
        p1 = " ".join(frases_selecionadas[:2])
        p2 = " ".join(frases_selecionadas[2:])
        resultado = f"{p1}\n\n{p2}".strip()
    else:
        resultado = " ".join(frases_selecionadas).strip()

    if resultado and resultado[-1] not in ".!?":
        resultado += "."

    if len(resultado) > 660:
        resultado = resultado[:640].rsplit(".", 1)[0] + "."

    return resultado


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


def montar_legenda_segura(categoria: str, titulo: str, resumo: str) -> str:
    """
    Evita duplo escape HTML e garante que o texto final jamais ultrapasse
    o limite de 1024 caracteres da API de mídia do Telegram.
    """
    t_limpo = html.unescape(titulo).strip()
    if " - " in t_limpo:
        t_limpo, _ = t_limpo.rsplit(" - ", 1)
    if len(t_limpo) > 180:
        t_limpo = t_limpo[:177].rsplit(" ", 1)[0] + "..."
    if t_limpo and t_limpo[-1] not in ".!?":
        t_limpo += "."

    cabecalho = f"{categoria}\n\n<b>{html.escape(t_limpo)}</b>\n\n"
    resumo_escapado = html.escape(html.unescape(resumo))

    orcamento_resumo = LIMITE_LEGENDA_TELEGRAM - len(cabecalho) - 5
    if len(resumo_escapado) > orcamento_resumo:
        resumo_escapado = resumo_escapado[:orcamento_resumo].rsplit(" ", 1)[0].rstrip(",;:-") + "."

    return f"{cabecalho}{resumo_escapado}"


# ==============================================================================
# COLETOR DE FEEDS E ENVIO COM FALLBACK
# ==============================================================================
async def processar_feed(
    url_feed: str, links_historico: set, agora_utc: datetime, sem: asyncio.Semaphore
) -> List[Dict[str, Any]]:
    itens_validos = []
    try:
        conteudo = await async_http_get(url_feed, sem, timeout=10)
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
        logger.warning(f"Falha ao ler feed {url_feed}: {e}")

    return itens_validos


async def carregar_proximas_noticias(
    limite: int = MAX_NOTICIAS_POR_VEZ,
) -> Tuple[List[Dict[str, Any]], List[str], List[str]]:
    links_historico, titulos_historico = carregar_historico()
    set_links = set(links_historico)
    titulos_rodada = list(titulos_historico)
    agora_utc = datetime.now(timezone.utc)
    sem = asyncio.Semaphore(MAX_CONCORRENCIA_HTTP)

    # Busca todos os 17 feeds em paralelo
    resultados_feeds = await asyncio.gather(
        *(processar_feed(url, set_links, agora_utc, sem) for url in FEEDS)
    )

    candidatos = [item for sublista in resultados_feeds for item in sublista]
    candidatos.sort(key=lambda x: x["dt_pub"], reverse=True)

    noticias = []
    for cand in candidatos:
        title = cand["title"]
        link = cand["link"]
        desc_bruta = cand["desc_bruta"]
        item = cand["item"]

        if link in set_links or eh_mesma_noticia(title, titulos_rodada):
            continue

        foto, video, audio = extrair_midias_do_rss(item, desc_bruta)
        texto_artigo, foto_og = await obter_conteudo_e_imagem_artigo(link, desc_bruta, title, sem)
        foto = foto or foto_og

        if not foto and not video and not audio:
            continue

        resumo_formatado = formatar_resumo_500_600(texto_artigo, title)
        if not resumo_formatado or len(resumo_formatado) < 120:
            continue

        categoria = categorizar_materia(title, resumo_formatado)
        post = montar_legenda_segura(categoria, title, resumo_formatado)

        noticias.append({
            "link": link,
            "titulo": title,
            "texto": post,
            "imagem": foto,
            "video": video,
            "audio": audio,
        })
        titulos_rodada.append(title)
        set_links.add(link)

        if len(noticias) >= limite:
            break

    noticias.reverse()
    return noticias, links_historico, titulos_historico


async def enviar_post(
    token: str, chat_id: str, item: Dict[str, Any], sem: asyncio.Semaphore
) -> bool:
    """
    Tenta enviar mídia via URL direta. Se o servidor de notícias bloquear o crawler
    do Telegram, faz o download binário local da imagem e envia via multipart,
    com fallback final para mensagem de texto.
    """
    botao_sorteado = gerar_botao_grupo_aleatorio()
    base_api = f"https://api.telegram.org/bot{token}"

    for chave_midia, endpoint, timeout in (
        ("audio", "sendAudio", 25),
        ("video", "sendVideo", 30),
        ("imagem", "sendPhoto", 20),
    ):
        midia_url = item.get(chave_midia)
        if not midia_url:
            continue

        campo_api = "photo" if chave_midia == "imagem" else chave_midia
        payload = {
            "chat_id": chat_id,
            campo_api: midia_url,
            "caption": item["texto"],
            "parse_mode": "HTML",
            "reply_markup": botao_sorteado,
        }
        status, resp = await post_telegram_com_retry(f"{base_api}/{endpoint}", payload, timeout=timeout)
        if status == 200:
            return True

        logger.warning(f"Falha ao enviar {campo_api} por URL (HTTP {status}): {resp}")

        # Contingência Nível 2: Upload binário direto para fotos bloqueadas por hotlink/CDN
        if chave_midia == "imagem":
            try:
                img_bytes = await async_http_get(midia_url, sem, timeout=10)
                fields = {
                    "chat_id": chat_id,
                    "caption": item["texto"],
                    "parse_mode": "HTML",
                    "reply_markup": json.dumps(botao_sorteado),
                }
                status_up, resp_up = await asyncio.to_thread(
                    _sync_http_post_multipart,
                    f"{base_api}/sendPhoto",
                    fields,
                    "photo",
                    img_bytes,
                )
                if status_up == 200:
                    logger.info("Imagem enviada com sucesso via fallback binário (multipart).")
                    return True
                logger.warning(f"Fallback multipart falhou (HTTP {status_up}): {resp_up}")
            except Exception as e:
                logger.warning(f"Não foi possível baixar imagem para upload de contingência: {e}")

    # Contingência Nível 3: Envia como texto para não perder furo de reportagem
    payload_txt = {
        "chat_id": chat_id,
        "text": item["texto"],
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
        "reply_markup": botao_sorteado,
    }
    status_txt, resp_txt = await post_telegram_com_retry(f"{base_api}/sendMessage", payload_txt, timeout=15)
    if status_txt == 200:
        logger.info("Notícia enviada via fallback de texto (sendMessage).")
        return True

    logger.error(f"Falha definitiva ao publicar notícia no Telegram (HTTP {status_txt}): {resp_txt}")
    return False


async def main() -> None:
    token, chat_id = obter_credenciais()
    sem = asyncio.Semaphore(MAX_CONCORRENCIA_HTTP)

    noticias, links_historico, titulos_historico = await carregar_proximas_noticias(
        limite=MAX_NOTICIAS_POR_VEZ
    )
    if not noticias:
        logger.info("Nenhuma notícia nova nos últimos minutos. Aguardando próximo ciclo.")
        return

    logger.info(f"Encontradas {len(noticias)} notícias qualificadas para envio neste ciclo.")
    for indice, noticia in enumerate(noticias, start=1):
        sucesso = await enviar_post(token, chat_id, noticia, sem)
        if sucesso:
            links_historico.append(noticia["link"])
            titulos_historico.append(noticia["titulo"])
            salvar_historico(links_historico, titulos_historico)
            logger.info(f"[{indice}/{len(noticias)}] Publicada com sucesso: {noticia['titulo']}")
            if indice < len(noticias):
                await asyncio.sleep(3)
        else:
            logger.error(f"Não foi possível publicar: {noticia['titulo']}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Execução interrompida manualmente.")
