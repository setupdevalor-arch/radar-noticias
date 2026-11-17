import os
import sys
import json
import html
import time
import logging
import re
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ==============================================================================
# CONFIGURAÇÕES DO BOT E CANAL
# ==============================================================================
TOKEN_FIXO = "9025960857:AAHRpjo7tDiU2C8O-2zdoM6aKk9eLQks7js"
CHAT_ID_FIXO = "-1003533060222"

HISTORICO_ARQUIVO = "historico_enviados.json"
MAX_HISTORICO = 600
MAX_NOTICIAS_POR_VEZ = 1  # 1 notícia por vez a cada 5 minutos

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

# Feeds de Notícias: Negócios, Economia, Política, Geopolítica, M&A e Cripto
FEEDS = [
    # 1. Negócios, M&A e Empresas
    "https://braziljournal.com/feed",
    "https://exame.com/feed/",
    # 2. Economia & Mercado Financeiro
    "https://www.infomoney.com.br/feed/",
    "https://www.infomoney.com.br/mercados/feed/",
    "https://www.suno.com.br/noticias/feed/",
    "https://www.moneytimes.com.br/feed/",
    "https://www.seudinheiro.com/feed/",
    "https://einvestidor.estadao.com.br/feed/",
    "https://g1.globo.com/rss/g1/economia/",
    # 3. Política & Decisões Fiscais
    "https://www.infomoney.com.br/politica/feed/",
    "https://g1.globo.com/rss/g1/politica/",
    "https://www.cnnbrasil.com.br/politica/feed/",
    "https://feeds.folha.uol.com.br/poder/rss091.xml",
    # 4. Geopolítica & Mercado Internacional
    "https://g1.globo.com/rss/g1/mundo/",
    "https://www.cnnbrasil.com.br/internacional/feed/",
    "https://feeds.folha.uol.com.br/mundo/rss091.xml",
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
    txt = re.sub(r'<[^>]+>', '', txt)
    txt = html.unescape(txt)
    return " ".join(txt.split()).strip()


def eh_mesma_noticia(novo_titulo, titulos_anteriores):
    """Evita postar notícias repetidas de jornais diferentes sobre o mesmo fato."""
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


def extrair_midias(item, desc_bruta):
    """Detecta imagem, vídeo ou áudio (podcast/resumo) anexados à matéria."""
    foto_url = None
    video_url = None
    audio_url = None

    # 1. Enclosure
    enc = item.find("enclosure")
    if enc is not None and "url" in enc.attrib:
        url = enc.attrib["url"]
        tipo = enc.attrib.get("type", "").lower()
        if "video" in tipo or url.endswith((".mp4", ".mov")):
            video_url = url
        elif "audio" in tipo or url.endswith((".mp3", ".m4a", ".ogg", ".wav")):
            audio_url = url
        elif "image" in tipo or url.endswith((".jpg", ".jpeg", ".png", ".webp")):
            foto_url = url

    # 2. Tags Media RSS
    for tag in ("{http://search.yahoo.com/mrss/}content", "{http://search.yahoo.com/mrss/}thumbnail"):
        m = item.find(tag)
        if m is not None and "url" in m.attrib:
            url = m.attrib["url"]
            tipo = m.attrib.get("type", "").lower()
            if "video" in tipo or url.endswith(".mp4"):
                video_url = video_url or url
            elif "audio" in tipo or url.endswith(".mp3"):
                audio_url = audio_url or url
            elif not foto_url and ("image" in tipo or url.startswith("http")):
                foto_url = url

    # 3. Imagem no corpo HTML da descrição
    if not foto_url and desc_bruta:
        busca = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc_bruta, re.IGNORECASE)
        if busca and busca.group(1).startswith("http"):
            foto_url = busca.group(1)

    return foto_url, video_url, audio_url


def obter_texto_completo_artigo(url, desc_bruta):
    """
    Garante texto suficiente para um resumo rico de 500 a 600 caracteres.
    Se o RSS for curto, busca os primeiros parágrafos diretamente da página da notícia.
    """
    t_limpo = limpar_html(desc_bruta)
    t_limpo = limpar_caudas_wordpress(t_limpo)

    if len(t_limpo) >= 480:
        return t_limpo

    try:
        raw_html = http_get(url, timeout=5).decode("utf-8", errors="replace")
        paragrafos = re.findall(r'<p[^>]*>(.*?)</p>', raw_html, flags=re.DOTALL | re.IGNORECASE)
        blocos = []
        for p in paragrafos:
            txt = limpar_html(p)
            if len(txt) < 35 or any(b in txt.lower() for b in ("leia também", "inscreva-se", "newsletter", "publicidade", "compartilhe", "foto:", "crédito:", "the post", "apareceu primeiro")):
                continue
            blocos.append(txt)
            if sum(len(b) for b in blocos) >= 700:
                break
        if blocos:
            return " ".join(blocos)
    except Exception:
        pass

    return t_limpo


def formatar_resumo_500_600(texto_bruto, titulo):
    """
    Produz resumo com início, meio e fim completos, na média de 500 a 600 caracteres.
    Organizado em parágrafos agradáveis, terminando sempre com pontuação final.
    """
    if not texto_bruto:
        return ""

    t = limpar_caudas_wordpress(texto_bruto)
    t = re.sub(r'<figure[^>]*>.*?</figure>', '', t, flags=re.DOTALL | re.IGNORECASE)
    t = re.sub(r'<img[^>]*>', '', t, flags=re.IGNORECASE)
    t = re.sub(r'<script[^>]*>.*?</script>', '', t, flags=re.DOTALL | re.IGNORECASE)
    t = limpar_html(t)

    # Remove legendas residuais de imagem no início
    t = re.sub(r'^.*?(?:Foto|Crédito|Reprodução|BBC|BCC|Reuters|G1|Estadão|Folha)\s*[:\-–—]?\s*(?=[A-Z])', '', t, flags=re.IGNORECASE)
    t = re.sub(r'^[^\.\!\?]*?(?:x\s*Real|Dólar\s*x|Foto:|Crédito:)[^\.\!\?]*?(?=[A-Z][a-z]+ [a-z]+ [a-z]+)', '', t, flags=re.IGNORECASE)

    # Remove repetição do título no início do texto
    if titulo and t.lower().startswith(titulo[:25].lower()):
        t = t[len(titulo):].strip()
        t = re.sub(r'^[:\-–—\s]+', '', t)

    # Remove caudas caça-cliques
    t = re.sub(r'\s*[;:]\s*(veja mais|leia mais|saiba mais|confira|entenda).*$', '', t, flags=re.IGNORECASE).strip()

    # Seleção de frases completas visando a média de 500 a 600 caracteres
    frases = re.split(r'(?<=[.!?])\s+', t)
    frases_selecionadas = []
    tamanho_acumulado = 0

    for f in frases:
        f = f.strip()
        if not f:
            continue
        if tamanho_acumulado + len(f) <= 650:
            frases_selecionadas.append(f)
            tamanho_acumulado += len(f)
        elif tamanho_acumulado < 480:
            frases_selecionadas.append(f)
            tamanho_acumulado += len(f)
            break
        else:
            break

    if not frases_selecionadas and frases:
        frases_selecionadas.append(frases[0])

    # Organiza em parágrafos para leitura fluida
    if len(frases_selecionadas) >= 3:
        p1 = " ".join(frases_selecionadas[:2])
        p2 = " ".join(frases_selecionadas[2:])
        resultado = f"{p1}\n\n{p2}".strip()
    else:
        resultado = " ".join(frases_selecionadas).strip()

    if resultado and resultado[-1] not in ".!?":
        resultado += "."

    return resultado


def formatar_post_completo(titulo, desc_bruta, url):
    """Categoriza com base nos 9 pilares estratégicos do portal financeiro."""
    t = titulo.strip()
    if " - " in t:
        t, _ = t.rsplit(" - ", 1)

    t_busca = (t + " " + desc_bruta).lower()

    # Identificação por relevância temática
    if any(p in t_busca for p in ("urgente", "breaking", "plantão", "circuit breaker", "ataque terrorista", "guerra declarada")):
        categoria = "🚨 <b>RADAR URGENTE</b>"
    elif any(p in t_busca for p in ("dividendo", "provento", "jcp", "juros sobre capital", "data com", "bonificação em ações", "rendimento")):
        categoria = "💰 <b>DIVIDENDOS &amp; PROVENTOS</b>"
    elif any(p in t_busca for p in ("petróleo", "brent", "wti", "opep", "minério de ferro", "dalian", "commodities", "commodity", "soja", "milho", "safra", "ouro", "gás natural")):
        categoria = "🛢️ <b>COMMODITIES &amp; ENERGIA</b>"
    elif any(p in t_busca for p in ("fusão", "aquisição", "compra fatia", "m&a", "incorporação", "ipo", "oferta pública", "reestruturação", "privatização", "joint venture", "concessão")):
        categoria = "🤝 <b>NEGÓCIOS &amp; EMPRESAS</b>"
    elif any(p in t_busca for p in ("inteligência artificial", "chatgpt", "openai", "nvidia", "semicondutores", "chips", "apple", "microsoft", "google", "meta", "big tech", "tsmc")):
        categoria = "🤖 <b>TECNOLOGIA &amp; IA</b>"
    elif any(p in t_busca for p in ("bitcoin", "btc", "ethereum", "eth", "cripto", "criptomoeda", "solana", "drex", "blockchain", "tokenização")):
        categoria = "🪙 <b>CRIPTOATIVOS</b>"
    elif any(p in t_busca for p in ("lula", "governo", "congresso", "stf", "senado", "câmara", "ministro", "haddad", "fazenda", "reforma tributária", "arcabouço", "planalto", "orçamento")):
        categoria = "🏛️ <b>POLÍTICA &amp; PODER</b>"
    elif any(p in t_busca for p in ("eua", "trump", "biden", "china", "guerra", "rússia", "ucrânia", "oriente médio", "israel", "otan", "taiwan", "bce", "europa", "geopolítica")):
        categoria = "🌍 <b>GEOPOLÍTICA &amp; MUNDO</b>"
    elif any(p in t_busca for p in ("dólar", "selic", "copom", "ipca", "inflação", "banco central", "fed", "juros", "pib", "câmbio", "focus")):
        categoria = "💵 <b>MACROECONOMIA &amp; CÂMBIO</b>"
    elif any(p in t_busca for p in ("lucro", "balanço", "prejuízo", "receita", "ebitda", "petrobras", "vale", "itaú", "bradesco", "b3", "bolsa", "ibovespa", "ações", "nyse", "nasdaq", "s&p 500")):
        categoria = "📊 <b>MERCADO &amp; AÇÕES</b>"
    else:
        categoria = "⚡️ <b>RADAR DE MERCADO</b>"

    if t and t[-1] not in ".!?":
        t += "."

    texto_artigo = obter_texto_completo_artigo(url, desc_bruta)
    resumo_formatado = formatar_resumo_500_600(texto_artigo, t)

    post = f"{categoria}\n\n<b>{html.escape(t)}</b>"
    if resumo_formatado and len(resumo_formatado) > 25 and resumo_formatado.lower() != t.lower():
        post += f"\n\n{html.escape(resumo_formatado)}"

    return post


def carregar_proximas_noticias(limite=MAX_NOTICIAS_POR_VEZ):
    links_historico, titulos_historico = carregar_historico()
    titulos_rodada = list(titulos_historico)
    noticias = []

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

                # Filtro Anti-Propaganda
                if eh_propaganda_ou_lixo(title, desc_bruta):
                    continue

                # Anti-Repetição Semântica
                if eh_mesma_noticia(title, titulos_rodada):
                    continue

                foto, video, audio = extrair_midias(item, desc_bruta)
                texto = formatar_post_completo(title, desc_bruta, link)

                noticias.append({
                    "link": link,
                    "titulo": title,
                    "texto": texto,
                    "imagem": foto,
                    "video": video,
                    "audio": audio
                })
                titulos_rodada.append(title)

                if len(noticias) >= limite:
                    return noticias, links_historico, titulos_historico
        except Exception as e:
            logging.warning(f"Aviso no feed {url_feed}: {e}")

    return noticias, links_historico, titulos_historico


def enviar_post(token, chat_id, item):
    """Envia com áudio, vídeo, foto ou texto nativo."""
    # 1. Áudio (Podcast / Resumo em voz)
    if item.get("audio"):
        url_aud = f"https://api.telegram.org/bot{token}/sendAudio"
        payload_aud = {"chat_id": chat_id, "audio": item["audio"], "caption": item["texto"], "parse_mode": "HTML"}
        status, _ = http_post_json(url_aud, payload_aud, timeout=25)
        if status == 200:
            return True

    # 2. Vídeo
    if item.get("video"):
        url_vid = f"https://api.telegram.org/bot{token}/sendVideo"
        payload_vid = {"chat_id": chat_id, "video": item["video"], "caption": item["texto"], "parse_mode": "HTML"}
        status, _ = http_post_json(url_vid, payload_vid, timeout=30)
        if status == 200:
            return True

    # 3. Foto de capa em alta definição
    if item.get("imagem"):
        url_photo = f"https://api.telegram.org/bot{token}/sendPhoto"
        payload_photo = {"chat_id": chat_id, "photo": item["imagem"], "caption": item["texto"], "parse_mode": "HTML"}
        status, _ = http_post_json(url_photo, payload_photo, timeout=20)
        if status == 200:
            return True

    # 4. Fallback em texto
    url_msg = f"https://api.telegram.org/bot{token}/sendMessage"
    payload_msg = {"chat_id": chat_id, "text": item["texto"], "parse_mode": "HTML", "disable_web_page_preview": False}
    status, _ = http_post_json(url_msg, payload_msg, timeout=20)
    return status == 200


if __name__ == "__main__":
    env_token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    token = env_token if env_token else TOKEN_FIXO

    env_chat = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    chat_id = env_chat if env_chat else CHAT_ID_FIXO

    noticias, links_historico, titulos_historico = carregar_proximas_noticias(limite=MAX_NOTICIAS_POR_VEZ)
    if not noticias:
        logging.info("Nenhuma novidade inédita no momento.")
        sys.exit(0)

    logging.info(f"Encontradas {len(noticias)} notícias inéditas para envio.")
    for noticia in noticias:
        sucesso = enviar_post(token, chat_id, noticia)
        if sucesso:
            links_historico.append(noticia["link"])
            titulos_historico.append(noticia["titulo"])
            salvar_historico(links_historico, titulos_historico)
            logging.info(f"Notícia enviada: {noticia['titulo']}")
            time.sleep(3)
        else:
            logging.error("Falha ao enviar post para o Telegram.")
