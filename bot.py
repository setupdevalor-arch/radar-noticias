import os
import sys
import json
import html
import time
import uuid
import random
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
# CONFIGURAÇÕES DO BOT E CANAL (24H POR DIA - SEM LINKS EXTERNOS)
# ==============================================================================
_PADRAO_TOKEN = base64.b64decode("OTAyNTk2MDg1NzpBQUhScGpvN3REaVUyQzhPLTJ6ZG9NNmFLazllTFFrczdqcw==").decode("utf-8")
_PADRAO_CHAT_ID = "-1003533060222"

HISTORICO_ARQUIVO = "historico_enviados.json"
MAX_HISTORICO = 800
MAX_NOTICIAS_POR_VEZ = 5   # Posta até 5 notícias novas por ciclo
MAX_IDADE_MINUTOS = 120    # Absorve atrasos do cron do GitHub sem perder matérias (o histórico evita repetição)
TZ_BRASILIA = timezone(timedelta(hours=-3))

# Botão aponta exclusivamente para o seu grupo dentro do Telegram
TEXTOS_BOTAO_GRUPO = [
    "💬 E agora? Veja a opinião do grupo",
    "📊 Como isso afeta o mercado? Debata aqui",
    "🗣️ Trocar ideia com outros investidores",
    "📈 Oportunidade ou risco? Opine no grupo",
    "💡 Veja a visão da nossa comunidade",
    "🔥 Assunto rendendo! Participe no grupo",
    "🤝 Entrar na comunidade de investidores",
    "💬 Comente essa notícia no nosso Grupo"
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"
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
    "https://br.cointelegraph.com/rss"
]

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


def gerar_botao_grupo_aleatorio():
    return {
        "inline_keyboard": [
            [
                {
                    "text": random.choice(TEXTOS_BOTAO_GRUPO),
                    "url": "https://t.me/OPapodeInvestidor"
                }
            ]
        ]
    }


def contem_termo(texto, lista_termos):
    for termo in lista_termos:
        if re.search(r'(?<!\w)' + re.escape(termo) + r'(?!\w)', texto, flags=re.IGNORECASE):
            return True
    return False


def normalizar_url(url):
    if not url:
        return None
    url = html.unescape(url.strip())
    if not url.startswith(("http://", "https://")):
        return None
    p = urllib.parse.urlsplit(url)
    caminho = urllib.parse.quote(urllib.parse.unquote(p.path), safe="/:@&+$,;=-_.~")
    return urllib.parse.urlunsplit((p.scheme, p.netloc, caminho, p.query, p.fragment))


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


def http_post_multipart(url, fields, file_field, file_bytes, filename="cover.jpg", content_type="image/jpeg", timeout=30):
    """Faz upload direto do arquivo de imagem quando o site de notícias bloqueia o download pelo Telegram."""
    boundary = f"----TelegramBoundary{uuid.uuid4().hex}"
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        body.extend(f"{value}\r\n".encode("utf-8"))

    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'.encode("utf-8"))
    body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
    body.extend(file_bytes)
    body.extend(f"\r\n--{boundary}--\r\n".encode("utf-8"))

    headers = {
        **HEADERS,
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Content-Length": str(len(body))
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


def remover_carimbo_data_g1(txt):
    if not txt:
        return ""
    txt = re.sub(
        r'\b\d{1,2}/\d{1,2}/\d{2,4}\s*(?:às\s*|-?\s*)?\d{1,2}[h:]\d{0,2}\b'
        r'(?:\s*[•·|\-–—]?\s*Atualizado\s*(?:há\s*|em\s*)?(?:\d+|um|uma|poucos|alguns)?\s*(?:minuto|hora|segundo|dia|mês|meses|ano)s?(?:\s+atrás)?)?',
        ' ',
        txt,
        flags=re.IGNORECASE
    )
    txt = re.sub(
        r'[•·|\-–—]?\s*\bAtualizado\s+(?:há\s+|em\s+)?(?:\d+|um|uma|poucos|alguns)\s*(?:minuto|hora|segundo|dia|mês|meses|ano)s?(?:\s+atrás)?\b',
        ' ',
        txt,
        flags=re.IGNORECASE
    )
    return " ".join(txt.split()).strip()


def limpar_html(txt):
    if not txt:
        return ""
    txt = re.sub(r'<(script|style|noscript|svg|head|nav|footer|header|aside|form)\b[^>]*>.*?</\1>', ' ', txt, flags=re.DOTALL | re.IGNORECASE)
    txt = re.sub(r'<[^>]+>', ' ', txt)
    txt = html.unescape(txt)
    # Remove qualquer URL solta do meio do texto para nunca gerar link clicável externo
    txt = re.sub(r'https?://\S+|www\.\S+', '', txt, flags=re.IGNORECASE)
    txt = remover_carimbo_data_g1(txt)
    return " ".join(txt.split()).strip()


def paragrafo_parece_codigo_ou_lixo(txt):
    if not txt or len(txt) < 40:
        return True
    sinais_codigo = (
        "{", "}", "@type", "@context", "schema.org", "dataLayer", "window.", "function(",
        "sameAs", "contentUrl", "ethicsPolicy", "isAccessibleForFree",
        "cssSelector", "gostaria de receber", "assine a", "exclusivo para assinantes",
        "inscreva-se", "todos os direitos reservados", "the post", "apareceu primeiro"
    )
    t_low = txt.lower()
    for s in sinais_codigo:
        if s in t_low:
            return True
    if txt.count('[') >= 3 or txt.count(']') >= 3:
        return True
    return False


def eh_mesma_noticia(novo_titulo, titulos_anteriores):
    tokens_novo = {
        p for p in re.findall(r'[a-z0-9áàéíóúâêîôûãõç]+', novo_titulo.lower())
        if len(p) > 3 and p not in STOPWORDS_DEDUP
    }
    if not tokens_novo:
        return False

    for antigo in titulos_anteriores:
        tokens_antigo = {
            p for p in re.findall(r'[a-z0-9áàéíóúâêîôûãõç]+', antigo.lower())
            if len(p) > 3 and p not in STOPWORDS_DEDUP
        }
        if not tokens_antigo:
            continue
        inter = tokens_novo & tokens_antigo
        ratio = len(inter) / min(len(tokens_novo), len(tokens_antigo))
        if len(inter) >= 2 and ratio >= 0.35:
            return True
    return False


def extrair_og_image_da_pagina(raw_html):
    for padrao in [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']'
    ]:
        m = re.search(padrao, raw_html, re.IGNORECASE)
        if m:
            url = normalizar_url(m.group(1))
            if url:
                return url
    return None


def extrair_midias_do_rss(item, desc_bruta):
    foto_url = None
    video_url = None
    audio_url = None

    enc = item.find("enclosure")
    if enc is not None and "url" in enc.attrib:
        url = normalizar_url(enc.attrib["url"])
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
            url = normalizar_url(m.attrib["url"])
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
            foto_url = normalizar_url(busca.group(1))

    return foto_url, video_url, audio_url


def obter_conteudo_e_imagem_artigo(url, desc_bruta, titulo):
    foto_og = None
    raw_html = ""

    try:
        raw_html = http_get(url, timeout=7).decode("utf-8", errors="replace")
        foto_og = extrair_og_image_da_pagina(raw_html)
    except Exception:
        pass

    if raw_html:
        html_sem_scripts = re.sub(
            r'<(head|script|style|svg|nav|footer|header|aside|form|noscript|iframe|button)\b[^>]*>.*?</\1>',
            ' ',
            raw_html,
            flags=re.DOTALL | re.IGNORECASE
        )

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
            txt = remover_carimbo_data_g1(txt)
            if not paragrafo_parece_codigo_ou_lixo(txt):
                blocos_limpos.append(txt)

            if sum(len(b) for b in blocos_limpos) >= 850:
                break

        if blocos_limpos:
            return " ".join(blocos_limpos), foto_og

    t_desc = limpar_caudas_wordpress(limpar_html(desc_bruta))
    t_desc = re.sub(r'^.*?\d{1,2}\s+de\s+[a-záéíóúâêîôûãõç]+\s+de\s+\d{4}\s*', '', t_desc, flags=re.IGNORECASE)
    t_desc = re.sub(r'^(?:Negócios|Economia|INFRA|Tecnologia|Weekend|Podcasts|Journal|Mercados|\s)+', '', t_desc, flags=re.IGNORECASE)
    t_desc = remover_carimbo_data_g1(t_desc)
    return t_desc, foto_og


def formatar_resumo_500_600(texto_bruto, titulo):
    if not texto_bruto:
        return ""

    t = limpar_caudas_wordpress(texto_bruto)
    t = limpar_html(t)

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
        f = remover_carimbo_data_g1(f.strip())
        if not f or len(f) < 25:
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
    links_historico, titulos_historico = carregar_historico()
    set_links = set(links_historico)
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
                content_el = item.find("{http://purl.org/rss/1.0/modules/content/}encoded")

                link = link_el.text.strip() if link_el is not None and link_el.text else ""
                title = html.unescape(title_el.text.strip()) if title_el is not None and title_el.text else ""
                desc_bruta = (
                    desc_el.text if desc_el is not None and desc_el.text
                    else (content_el.text if content_el is not None and content_el.text else "")
                )

                if not link or not title or link in set_links:
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

        if link in set_links or eh_mesma_noticia(title, titulos_rodada):
            continue

        foto, video, audio = extrair_midias_do_rss(item, desc_bruta)
        texto_artigo, foto_og = obter_conteudo_e_imagem_artigo(link, desc_bruta, title)
        foto = foto or foto_og

        if not foto and not video and not audio:
            continue

        resumo_formatado = formatar_resumo_500_600(texto_artigo, title)
        if not resumo_formatado or len(resumo_formatado) < 110:
            continue

        categoria = categorizar_materia(title, resumo_formatado)

        t_limpo = title.strip()
        if " - " in t_limpo:
            t_limpo, _ = t_limpo.rsplit(" - ", 1)
        if t_limpo and t_limpo[-1] not in ".!?":
            t_limpo += "."

        # Texto 100% nativo sem nenhum link externo
        post = (
            f"{categoria}\n\n"
            f"<b>{html.escape(t_limpo)}</b>\n\n"
            f"{html.escape(resumo_formatado)}"
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
        set_links.add(link)

        if len(noticias) >= limite:
            break

    noticias.reverse()
    return noticias, links_historico, titulos_historico


def enviar_post(token, chat_id, item):
    """Envia mídia nativa (áudio, vídeo ou foto) + botão do grupo, sem links para sites externos."""
    botao_sorteado = gerar_botao_grupo_aleatorio()

    if item.get("audio"):
        url_aud = f"https://api.telegram.org/bot{token}/sendAudio"
        payload_aud = {
            "chat_id": chat_id,
            "audio": item["audio"],
            "caption": item["texto"],
            "parse_mode": "HTML",
            "reply_markup": botao_sorteado
        }
        status, _ = http_post_json(url_aud, payload_aud, timeout=25)
        if status == 200:
            return True

    if item.get("video"):
        url_vid = f"https://api.telegram.org/bot{token}/sendVideo"
        payload_vid = {
            "chat_id": chat_id,
            "video": item["video"],
            "caption": item["texto"],
            "parse_mode": "HTML",
            "reply_markup": botao_sorteado
        }
        status, _ = http_post_json(url_vid, payload_vid, timeout=30)
        if status == 200:
            return True

    if item.get("imagem"):
        url_photo = f"https://api.telegram.org/bot{token}/sendPhoto"
        payload_photo = {
            "chat_id": chat_id,
            "photo": item["imagem"],
            "caption": item["texto"],
            "parse_mode": "HTML",
            "reply_markup": botao_sorteado
        }
        status, _ = http_post_json(url_photo, payload_photo, timeout=20)
        if status == 200:
            return True

        # Se o site da notícia bloquear o download direto pelo Telegram, baixa a foto e sobe o arquivo binário
        try:
            img_bytes = http_get(item["imagem"], timeout=10)
            fields = {
                "chat_id": chat_id,
                "caption": item["texto"],
                "parse_mode": "HTML",
                "reply_markup": json.dumps(botao_sorteado)
            }
            status_up, _ = http_post_multipart(url_photo, fields, "photo", img_bytes)
            if status_up == 200:
                return True
        except Exception as e:
            logging.warning(f"Falha no upload binário da imagem: {e}")

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
            logging.info(f"Notícia enviada com imagem nativa e botão: {noticia['titulo']}")
            time.sleep(3)
        else:
            logging.error(f"Falha ao enviar post com mídia: {noticia['titulo']}")
