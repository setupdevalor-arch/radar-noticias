import os
import sys
import json
import html
import time
import uuid
import logging
import re
import base64
import email.utils
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ==============================================================================
# CONFIGURAÇÕES DO BOT E CANAL (24H POR DIA - DE SEGUNDA A SEGUNDA)
# ==============================================================================
_PADRAO_TOKEN = base64.b64decode("OTAyNTk2MDg1NzpBQUhScGpvN3REaVUyQzhPLTJ6ZG9NNmFLazllTFFrczdqcw==").decode("utf-8")
_PADRAO_CHAT_ID = "-1003533060222"

HISTORICO_ARQUIVO = "historico_enviados.json"
MAX_HISTORICO = 800
MAX_NOTICIAS_POR_VEZ = 5  # Se saírem até 5 notícias novas no ciclo de 5 min, posta todas sem pular
MAX_IDADE_MINUTOS = 25    # Só aceita notícias frescas dos últimos 25 minutos (nunca posta notícia velha)
TZ_BRASILIA = timezone(timedelta(hours=-3))

# CTA com link camuflado no final de cada notícia
CTA_GRUPO = '💬 <a href="https://t.me/OPapodeInvestidor">Entre na nossa comunidade de investidores e participe da conversa</a>'

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

# Feeds de Notícias Abertos (Sem Paywall): Negócios, Economia, Política, Geopolítica, M&A e Cripto
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
    "https://br.cointelegraph.com/rss"
]

# BLOQUEIO RIGOROSO DE PROPAGANDAS E RUÍDOS
BLOQUEADOS_PROPAGANDA = [
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
    "escalação", "futebol", "novela"
]

STOPWORDS_DEDUP = {
    "aprova", "anuncia", "diz", "após", "apos", "sobre", "pode", "novo", "nova",
    "nesta", "neste", "segundo", "veja", "confira", "contra", "entre", "mais",
    "para", "como", "onde", "quando", "quem", "qual", "está", "esta", "estão",
    "foram", "será", "pagamento", "tem", "faz", "vai", "com", "por", "que", "dos", "das",
    "fecham", "fecha", "sobe", "cai", "alta", "queda", "dias", "pontos"
}


def contem_termo(texto, lista_termos):
    """Verifica palavras inteiras com limite de palavra (evita que 'eth' bata em 'ethics' ou 'fed' em 'federal')."""
    for termo in lista_termos:
        if re.search(r'\b' + re.escape(termo) + r'\b', texto, flags=re.IGNORECASE):
            return True
    return False


def http_get(url, timeout=10):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def http_post_json(url, payload, timeout=25):
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


def enviar_video_multipart(token, chat_id, video_bytes, legenda, timeout=45):
    """Faz upload direto do arquivo de vídeo (.mp4) para o Telegram caso o portal bloqueie link direto."""
    url = f"https://api.telegram.org/bot{token}/sendVideo"
    boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
    partes = []

    campos = {
        "chat_id": str(chat_id),
        "caption": legenda,
        "parse_mode": "HTML",
        "supports_streaming": "true"
    }
    for nome, valor in campos.items():
        partes.append(f"--{boundary}\r\n".encode("utf-8"))
        partes.append(f'Content-Disposition: form-data; name="{nome}"\r\n\r\n'.encode("utf-8"))
        partes.append(f"{valor}\r\n".encode("utf-8"))

    partes.append(f"--{boundary}\r\n".encode("utf-8"))
    partes.append(b'Content-Disposition: form-data; name="video"; filename="noticia.mp4"\r\n')
    partes.append(b"Content-Type: video/mp4\r\n\r\n")
    partes.append(video_bytes)
    partes.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))

    corpo = b"".join(partes)
    headers = {**HEADERS, "Content-Type": f"multipart/form-data; boundary={boundary}"}
    req = urllib.request.Request(url, data=corpo, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def carregar_historico():
    if os.path.exists(HISTORICO_ARQUIVO):
        try:
            with open(HISTORICO_ARQUIVO, "r", encoding="utf-8") as f:
                dados = json.load(f)
                if isinstance(dados, dict):
                    return dados.get("links", []), dados.get("titulos", [])
                elif isinstance(dados, list):
                    return dados, []
        except Exception as e:
            logging.error(f"Erro ao ler histórico: {e}")
    return [], []


def salvar_historico(links, titulos):
    try:
        dados = {
            "links": list(dict.fromkeys(links))[-MAX_HISTORICO:],
            "titulos": list(dict.fromkeys(titulos))[-MAX_HISTORICO:]
        }
        with open(HISTORICO_ARQUIVO, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Erro ao salvar histórico: {e}")


def extrair_data_publicacao(item):
    """Extrai a data/hora exata de publicação do item RSS em UTC."""
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


def eh_propaganda_ou_lixo(titulo, descricao):
    texto = (titulo + " " + descricao).lower()
    for b in BLOQUEADOS_PROPAGANDA:
        if b in texto:
            return True
    return False


def limpar_caudas_wordpress(texto):
    t = re.sub(r'The post .*? appeared first on .*$', '', texto, flags=re.IGNORECASE)
    t = re.sub(r'O post .*? apareceu primeiro em .*$', '', t, flags=re.IGNORECASE)
    t = re.sub(r'appeared first on .*$', '', t, flags=re.IGNORECASE)
    return t.strip()


def limpar_html(txt):
    if not txt:
        return ""
    txt = re.sub(r'<(script|style|noscript|svg|head|nav|footer|header|aside|form)\b[^>]*>.*?</\1>', ' ', txt, flags=re.DOTALL | re.IGNORECASE)
    txt = re.sub(r'<[^>]+>', ' ', txt)
    txt = html.unescape(txt)
    return " ".join(txt.split()).strip()


def paragrafo_parece_codigo_ou_lixo(txt):
    """Bloqueia qualquer trecho que contenha código JSON, JavaScript, URLs soltas ou avisos de paywall."""
    if not txt or len(txt) < 45:
        return True
    sinais_codigo = (
        "{", "}", "@type", "@context", "schema.org", "dataLayer", "window.", "function(",
        "sameAs", "http://", "https://", "contentUrl", "ethicsPolicy", "isAccessibleForFree",
        "cssSelector", "gostaria de receber", "últimas notícias", "assine a", "exclusivo para assinantes",
        "leia também", "inscreva-se", "newsletter", "compartilhe", "foto:", "crédito:",
        "publicidade", "todos os direitos reservados", "the post", "apareceu primeiro",
        "podcasts", "weekend"
    )
    t_low = txt.lower()
    for s in sinais_codigo:
        if s in t_low:
            return True
    if txt.count('"') >= 6 or txt.count('[') >= 2 or txt.count(']') >= 2:
        return True
    return False


def eh_mesma_noticia(novo_titulo, titulos_anteriores):
    """Evita postar notícias repetidas sobre o mesmo fato."""
    tokens_novo = {
        p for p in re.findall(r'[a-záéíóúâêîôûãõç]+', novo_titulo.lower())
        if len(p) > 3 and p not in STOPWORDS_DEDUP
    }
    if not tokens_novo:
        return False

    for antigo in titulos_anteriores:
        tokens_antigo = {
            p for p in re.findall(r'[a-záéíóúâêîôûãõç]+', antigo.lower())
            if len(p) > 3 and p not in STOPWORDS_DEDUP
        }
        if not tokens_antigo:
            continue
        inter = tokens_novo & tokens_antigo
        ratio = len(inter) / min(len(tokens_novo), len(tokens_antigo))
        if len(inter) >= 2 and ratio >= 0.35:
            return True
    return False


def extrair_video_da_pagina(raw_html):
    """
    Detecta se a página da notícia possui um arquivo de vídeo real (.mp4 / .mov)
    para enviar o vídeo em movimento em vez de apenas um frame estático.
    """
    if not raw_html:
        return None
    padroes = [
        r'<meta[^>]+property=["\']og:video:secure_url["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:video:secure_url["\']',
        r'<meta[^>]+property=["\']og:video:url["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:video:url["\']',
        r'<meta[^>]+property=["\']og:video["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:video["\']',
        r'<meta[^>]+name=["\']twitter:player:stream["\'][^>]+content=["\']([^"\']+)["\']',
        r'<source[^>]+src=["\'](https?://[^"\']+\.(?:mp4|mov)[^"\']*)["\']',
        r'<video[^>]+src=["\'](https?://[^"\']+\.(?:mp4|mov)[^"\']*)["\']',
        r'"contentUrl"\s*:\s*"(https?://[^"\s]+\.(?:mp4|mov)[^"\s]*)"',
        r'"videoUrl"\s*:\s*"(https?://[^"\s]+\.(?:mp4|mov)[^"\s]*)"'
    ]
    for padrao in padroes:
        m = re.search(padrao, raw_html, re.IGNORECASE)
        if m:
            url = m.group(1).strip().replace("\\/", "/")
            if url.startswith("http") and (".mp4" in url.lower() or ".mov" in url.lower()):
                return url
    return None


def extrair_og_image_da_pagina(raw_html):
    """Busca a foto oficial de capa (OpenGraph / Twitter Card) na página do artigo."""
    for padrao in [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']'
    ]:
        m = re.search(padrao, raw_html, re.IGNORECASE)
        if m and m.group(1).startswith("http"):
            return m.group(1).strip()
    return None


def extrair_midias_do_rss(item, desc_bruta):
    """Detecta imagem, vídeo ou áudio dentro das tags do RSS."""
    foto_url = None
    video_url = None
    audio_url = None

    # 1. Verifica todas as tags <enclosure>
    for enc in item.findall("enclosure"):
        url = enc.attrib.get("url", "").strip()
        tipo = enc.attrib.get("type", "").lower()
        if not url:
            continue
        if "video" in tipo or any(ext in url.lower() for ext in (".mp4", ".mov")):
            video_url = video_url or url
        elif "audio" in tipo or any(ext in url.lower() for ext in (".mp3", ".m4a", ".ogg", ".wav")):
            audio_url = audio_url or url
        elif "image" in tipo or any(ext in url.lower() for ext in (".jpg", ".jpeg", ".png", ".webp")):
            foto_url = foto_url or url

    # 2. Verifica todas as tags Media RSS (<media:content>, <media:thumbnail>)
    for tag in ("{http://search.yahoo.com/mrss/}content", "{http://search.yahoo.com/mrss/}thumbnail"):
        for m in item.findall(tag):
            url = m.attrib.get("url", "").strip()
            tipo = m.attrib.get("type", "").lower()
            medium = m.attrib.get("medium", "").lower()
            if not url:
                continue
            if "video" in tipo or medium == "video" or ".mp4" in url.lower() or ".mov" in url.lower():
                video_url = video_url or url
            elif "audio" in tipo or medium == "audio" or ".mp3" in url.lower():
                audio_url = audio_url or url
            elif not foto_url and ("image" in tipo or medium == "image" or url.startswith("http")):
                foto_url = url

    # 3. Procura vídeo e imagem dentro do HTML do RSS
    if desc_bruta:
        if not video_url:
            v_match = re.search(r'(?:src|href)=["\'](https?://[^"\']+\.(?:mp4|mov)[^"\']*)["\']', desc_bruta, re.IGNORECASE)
            if v_match:
                video_url = v_match.group(1)
        if not foto_url:
            busca = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc_bruta, re.IGNORECASE)
            if busca and busca.group(1).startswith("http"):
                foto_url = busca.group(1)

    return foto_url, video_url, audio_url


def obter_conteudo_e_midias_artigo(url, desc_bruta, titulo):
    """
    Visita a matéria para obter vídeo real (se houver), foto de capa (og:image) e parágrafos limpos,
    eliminando totalmente tags <head>, <script>, JSON-LD, menus e rodapés.
    """
    foto_og = None
    video_pg = None
    raw_html = ""

    try:
        raw_html = http_get(url, timeout=6).decode("utf-8", errors="replace")
        video_pg = extrair_video_da_pagina(raw_html)
        foto_og = extrair_og_image_da_pagina(raw_html)
    except Exception:
        pass

    if raw_html:
        # Remove blocos inteiros de script, head, style, nav, footer antes de buscar <p>
        html_sem_scripts = re.sub(
            r'<(head|script|style|svg|nav|footer|header|aside|form|noscript|iframe|button)\b[^>]*>.*?</\1>',
            ' ',
            raw_html,
            flags=re.DOTALL | re.IGNORECASE
        )

        # Usa <p(?:\s[^>]*)?> para nunca confundir <p> com <path>, <picture> ou <param>
        paragrafos = re.findall(r'<p(?:\s[^>]*)?>(.*?)</p>', html_sem_scripts, flags=re.DOTALL | re.IGNORECASE)
        blocos_limpos = []

        for p in paragrafos:
            txt = limpar_html(p)
            if paragrafo_parece_codigo_ou_lixo(txt):
                continue

            if re.search(r'\d{1,2}\s+de\s+[a-záéíóúâêîôûãõç]+\s+de\s+\d{4}', txt, re.IGNORECASE):
                continue

            if titulo and titulo[:30].lower() in txt.lower():
                continue

            txt = re.sub(r'^(?:Negócios|Economia|INFRA|Tecnologia|Weekend|Podcasts|Journal|Mercados|\s)+', '', txt, flags=re.IGNORECASE)
            if not paragrafo_parece_codigo_ou_lixo(txt):
                blocos_limpos.append(txt)

            if sum(len(b) for b in blocos_limpos) >= 800:
                break

        if blocos_limpos:
            return " ".join(blocos_limpos), foto_og, video_pg

    # Fallback: usa a descrição do RSS se for texto jornalístico válido
    t_desc = limpar_caudas_wordpress(limpar_html(desc_bruta))
    t_desc = re.sub(r'^.*?\d{1,2}\s+de\s+[a-záéíóúâêîôûãõç]+\s+de\s+\d{4}\s*', '', t_desc, flags=re.IGNORECASE)
    t_desc = re.sub(r'^(?:Negócios|Economia|INFRA|Tecnologia|Weekend|Podcasts|Journal|Mercados|\s)+', '', t_desc, flags=re.IGNORECASE)
    if paragrafo_parece_codigo_ou_lixo(t_desc):
        return "", foto_og, video_pg
    return t_desc, foto_og, video_pg


def formatar_resumo_500_600(texto_bruto, titulo):
    """
    Produz resumo com início, meio e fim completos, na média exata de 500 a 600 caracteres.
    Nunca ultrapassa o limite de legenda de mídia do Telegram.
    """
    if not texto_bruto:
        return ""

    t = limpar_caudas_wordpress(texto_bruto)
    t = limpar_html(t)

    if paragrafo_parece_codigo_ou_lixo(t):
        return ""

    t = re.sub(r'^.*?(?:Foto|Crédito|Reprodução|BBC|BCC|Reuters|G1|Estadão|Folha)\s*[:\-–—]?\s*(?=[A-Z])', '', t, flags=re.IGNORECASE)
    t = re.sub(r'^[^\.\!\?]*?(?:x\s*Real|Dólar\s*x|Foto:|Crédito:)[^\.\!\?]*?(?=[A-Z][a-z]+ [a-z]+ [a-z]+)', '', t, flags=re.IGNORECASE)

    if titulo and t.lower().startswith(titulo[:25].lower()):
        t = t[len(titulo):].strip()
        t = re.sub(r'^[:\-–—\s]+', '', t)

    t = re.sub(r'\s*[;:]\s*(veja mais|leia mais|saiba mais|confira|entenda).*$', '', t, flags=re.IGNORECASE).strip()

    frases = re.split(r'(?<=[.!?])\s+', t)
    frases_selecionadas = []
    tamanho_acumulado = 0

    for f in frases:
        f = f.strip()
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


def categorizar_materia(titulo, texto):
    """
    Categoriza com base nos 9 pilares estratégicos usando palavras inteiras (\b)
    e dando prioridade ao título da matéria.
    """
    for escopo in (titulo, titulo + " " + texto):
        if contem_termo(escopo, ("urgente", "breaking", "plantão", "circuit breaker", "ataque terrorista", "guerra declarada")):
            return "🚨 <b>RADAR URGENTE</b>"
        if contem_termo(escopo, ("dividendo", "dividendos", "provento", "proventos", "jcp", "juros sobre capital", "data com", "bonificação")):
            return "💰 <b>DIVIDENDOS &amp; PROVENTOS</b>"
        if contem_termo(escopo, (
            "lula", "congresso", "stf", "senado", "senador", "câmara", "camara", "deputado", "deputados",
            "motta", "lira", "haddad", "planalto", "reforma tributária", "arcabouço", "orçamento",
            "eleição", "eleições", "candidatura", "partido", "pl", "pt", "governador", "prefeito", "pec"
        )):
            return "🏛️ <b>POLÍTICA &amp; PODER</b>"
        if contem_termo(escopo, (
            "eua", "trump", "biden", "china", "guerra", "rússia", "ucrânia", "oriente médio",
            "israel", "irã", "otan", "taiwan", "bce", "união europeia", "geopolítica", "casa branca", "tarifas"
        )):
            return "🌍 <b>GEOPOLÍTICA &amp; MUNDO</b>"
        if contem_termo(escopo, (
            "energia", "leilão de energia", "leilões de energia", "eletricidade", "setor elétrico",
            "petróleo", "brent", "wti", "opep", "minério", "dalian", "commodities", "commodity",
            "soja", "milho", "safra", "ouro", "gás natural", "combustível", "gasolina"
        )):
            return "🛢️ <b>COMMODITIES &amp; ENERGIA</b>"
        if contem_termo(escopo, (
            "fusão", "aquisição", "m&a", "incorporação", "ipo", "oferta pública",
            "reestruturação", "privatização", "joint venture", "concessão", "recuperação judicial"
        )):
            return "🤝 <b>NEGÓCIOS &amp; EMPRESAS</b>"
        if contem_termo(escopo, (
            "inteligência artificial", "chatgpt", "openai", "nvidia", "semicondutores",
            "chips", "apple", "microsoft", "google", "meta", "big tech", "tsmc"
        )):
            return "🤖 <b>TECNOLOGIA &amp; IA</b>"
        if contem_termo(escopo, (
            "bitcoin", "btc", "ethereum", "cripto", "criptomoeda", "criptomoedas",
            "criptoativos", "solana", "drex", "blockchain", "tokenização"
        )):
            return "🪙 <b>CRIPTOATIVOS</b>"
        if contem_termo(escopo, (
            "dólar", "selic", "copom", "ipca", "inflação", "banco central", "fed",
            "juros", "pib", "câmbio", "boletim focus", "tesouro direto"
        )):
            return "💵 <b>MACROECONOMIA &amp; CÂMBIO</b>"
        if contem_termo(escopo, (
            "lucro", "balanço", "prejuízo", "receita", "ebitda", "petrobras", "vale",
            "itaú", "bradesco", "b3", "bolsa", "ibovespa", "ações", "nyse", "nasdaq", "s&p 500"
        )):
            return "📊 <b>MERCADO &amp; AÇÕES</b>"

    return "⚡️ <b>RADAR DE MERCADO</b>"


def carregar_proximas_noticias(limite=MAX_NOTICIAS_POR_VEZ):
    """
    Varredura 24h por dia, de segunda a segunda.
    Captura TODAS as notícias novas que saíram nos últimos minutos (até 5 por ciclo de 5 min),
    priorizando vídeo real (.mp4) sobre foto estática, e adicionando o CTA camuflado no final.
    """
    links_historico, titulos_historico = carregar_historico()
    titulos_rodada = list(titulos_historico)
    agora_utc = datetime.now(timezone.utc)
    candidatos = []

    for url_feed in FEEDS:
        try:
            conteudo = http_get(url_feed, timeout=10)
            root = ET.fromstring(conteudo)
            for item in root.findall(".//item"):
                link_el = item.find("link")
                title_el = item.find("title")
                desc_el = item.find("description")

                link = link_el.text.strip() if link_el is not None and link_el.text else ""
                title = title_el.text.strip() if title_el is not None and title_el.text else ""
                desc_bruta = desc_el.text if desc_el is not None and desc_el.text else ""

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

                candidatos.append({
                    "dt_pub": dt_pub,
                    "item": item,
                    "link": link,
                    "title": title,
                    "desc_bruta": desc_bruta
                })
        except Exception as e:
            logging.warning(f"Aviso no feed {url_feed}: {e}")

    candidatos.sort(key=lambda x: x["dt_pub"], reverse=True)

    noticias = []
    for cand in candidatos:
        title = cand["title"]
        link = cand["link"]
        desc_bruta = cand["desc_bruta"]
        item = cand["item"]

        if eh_mesma_noticia(title, titulos_rodada):
            continue

        foto, video, audio = extrair_midias_do_rss(item, desc_bruta)
        texto_artigo, foto_og, video_pg = obter_conteudo_e_midias_artigo(link, desc_bruta, title)
        video = video or video_pg
        foto = foto or foto_og

        if not foto and not video and not audio:
            continue

        resumo_formatado = formatar_resumo_500_600(texto_artigo, title)
        if not resumo_formatado or len(resumo_formatado) < 120:
            continue

        categoria = categorizar_materia(title, resumo_formatado)

        t_limpo = title.strip()
        if " - " in t_limpo:
            t_limpo, _ = t_limpo.rsplit(" - ", 1)
        if t_limpo and t_limpo[-1] not in ".!?":
            t_limpo += "."

        post = (
            f"{categoria}\n\n"
            f"<b>{html.escape(t_limpo)}</b>\n\n"
            f"{html.escape(resumo_formatado)}\n\n"
            f"{CTA_GRUPO}"
        )

        noticias.append({
            "link": link,
            "titulo": title,
            "texto": post,
            "imagem": foto,
            "video": video,
            "audio": audio
        })
        titulos_rodada.append(title)

        if len(noticias) >= limite:
            break

    noticias.reverse()
    return noticias, links_historico, titulos_historico


def enviar_post(token, chat_id, item):
    """
    Prioridade 1: VÍDEO REAL (envia como vídeo reproduzível no Telegram, nunca apenas frame estático).
    Prioridade 2: ÁUDIO (podcast/resumo).
    Prioridade 3: FOTO DE CAPA (apenas quando a matéria não possuir vídeo).
    """
    # 1. Prioridade Máxima: Vídeo Real
    if item.get("video"):
        url_vid = f"https://api.telegram.org/bot{token}/sendVideo"
        payload_vid = {
            "chat_id": chat_id,
            "video": item["video"],
            "caption": item["texto"],
            "parse_mode": "HTML",
            "supports_streaming": True
        }
        status, _ = http_post_json(url_vid, payload_vid, timeout=30)
        if status == 200:
            return True

        # Se o servidor do jornal bloquear download direto pelo Telegram, baixa o .mp4 e faz upload nativo
        try:
            video_bytes = http_get(item["video"], timeout=25)
            if video_bytes and len(video_bytes) <= 48 * 1024 * 1024:
                if enviar_video_multipart(token, chat_id, video_bytes, item["texto"]):
                    return True
        except Exception as e:
            logging.warning(f"Aviso ao baixar vídeo para upload direto: {e}")

    # 2. Áudio
    if item.get("audio"):
        url_aud = f"https://api.telegram.org/bot{token}/sendAudio"
        payload_aud = {"chat_id": chat_id, "audio": item["audio"], "caption": item["texto"], "parse_mode": "HTML"}
        status, _ = http_post_json(url_aud, payload_aud, timeout=25)
        if status == 200:
            return True

    # 3. Foto de capa em alta definição (quando a notícia não tem vídeo)
    if item.get("imagem"):
        url_photo = f"https://api.telegram.org/bot{token}/sendPhoto"
        payload_photo = {"chat_id": chat_id, "photo": item["imagem"], "caption": item["texto"], "parse_mode": "HTML"}
        status, _ = http_post_json(url_photo, payload_photo, timeout=20)
        if status == 200:
            return True

    return False


if __name__ == "__main__":
    env_token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    token = env_token if env_token else _PADRAO_TOKEN

    env_chat = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    chat_id = env_chat if env_chat else _PADRAO_CHAT_ID

    noticias, links_historico, titulos_historico = carregar_proximas_noticias(limite=MAX_NOTICIAS_POR_VEZ)
    if not noticias:
        logging.info("Nenhuma notícia nova nos últimos minutos. Aguardando próximo ciclo de 5 min.")
        sys.exit(0)

    logging.info(f"Encontradas {len(noticias)} notícias quentes para envio neste ciclo.")
    for noticia in noticias:
        sucesso = enviar_post(token, chat_id, noticia)
        if sucesso:
            links_historico.append(noticia["link"])
            titulos_historico.append(noticia["titulo"])
            salvar_historico(links_historico, titulos_historico)
            logging.info(f"Notícia enviada: {noticia['titulo']}")
            time.sleep(3)
        else:
            logging.error("Falha ao enviar post com mídia para o Telegram.")
