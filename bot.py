import os
import sys
import json
import html
import time
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
MAX_NOTICIAS_POR_VEZ = 1
MAX_IDADE_MINUTOS = 45  # Só aceita notícias que saíram nos últimos 45 minutos (NUNCA posta notícia antiga)
TZ_BRASILIA = timezone(timedelta(hours=-3))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

# Feeds de Notícias: Negócios, Economia, Política, Geopolítica, M&A e Cripto (Varredura 24/7)
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
    txt = re.sub(r'<[^>]+>', '', txt)
    txt = html.unescape(txt)
    return " ".join(txt.split()).strip()


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


def obter_conteudo_e_imagem_artigo(url, desc_bruta, titulo):
    """
    Visita a matéria para obter a foto de capa (og:image) e parágrafos limpos,
    removendo menus de navegação, breadcrumbs, datas e links residuais.
    """
    foto_og = None
    raw_html = ""

    try:
        raw_html = http_get(url, timeout=6).decode("utf-8", errors="replace")
        foto_og = extrair_og_image_da_pagina(raw_html)
    except Exception:
        pass

    # Extração de parágrafos reais do artigo
    if raw_html:
        paragrafos = re.findall(r'<p[^>]*>(.*?)</p>', raw_html, flags=re.DOTALL | re.IGNORECASE)
        blocos_limpos = []

        for p in paragrafos:
            txt = limpar_html(p)
            if len(txt) < 40:
                continue

            # Remove menus de navegação, cabeçalhos com datas e ruídos
            if re.search(r'\d{1,2}\s+de\s+[a-záéíóúâêîôûãõç]+\s+de\s+\d{4}', txt, re.IGNORECASE):
                continue
            if any(b in txt.lower() for b in (
                "podcasts", "weekend", "newsletter", "inscreva-se", "leia mais", "leia também",
                "compartilhe", "foto:", "crédito:", "publicidade", "assinante", "todos os direitos reservados",
                "the post", "apareceu primeiro"
            )):
                continue

            # Remove se o parágrafo repete o título com categorias grudadas
            if titulo and titulo[:30].lower() in txt.lower():
                continue

            # Remove palavras residuais de menu grudadas no início
            txt = re.sub(r'^(?:Negócios|Economia|INFRA|Tecnologia|Weekend|Podcasts|Journal|Mercados|\s)+', '', txt, flags=re.IGNORECASE)

            blocos_limpos.append(txt)
            if sum(len(b) for b in blocos_limpos) >= 800:
                break

        if blocos_limpos:
            return " ".join(blocos_limpos), foto_og

    # Fallback se não conseguir abrir o site: usa a descrição do RSS limpa
    t_desc = limpar_caudas_wordpress(limpar_html(desc_bruta))
    t_desc = re.sub(r'^.*?\d{1,2}\s+de\s+[a-záéíóúâêîôûãõç]+\s+de\s+\d{4}\s*', '', t_desc, flags=re.IGNORECASE)
    t_desc = re.sub(r'^(?:Negócios|Economia|INFRA|Tecnologia|Weekend|Podcasts|Journal|Mercados|\s)+', '', t_desc, flags=re.IGNORECASE)
    return t_desc, foto_og


def formatar_resumo_500_600(texto_bruto, titulo):
    """
    Produz resumo com início, meio e fim completos, na média exata de 500 a 600 caracteres.
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


def categorizar_materia(titulo, texto):
    """Categoriza com base nos 9 pilares estratégicos de mercado com alta precisão."""
    t_busca = (titulo + " " + texto).lower()

    if any(p in t_busca for p in ("urgente", "breaking", "plantão", "circuit breaker", "ataque terrorista", "guerra declarada")):
        return "🚨 <b>RADAR URGENTE</b>"
    elif any(p in t_busca for p in ("dividendo", "provento", "jcp", "juros sobre capital", "data com", "bonificação em ações")):
        return "💰 <b>DIVIDENDOS &amp; PROVENTOS</b>"
    elif any(p in t_busca for p in ("energia", "leilão de energia", "leilões de energia", "eletricidade", "setor elétrico", "petróleo", "brent", "wti", "opep", "minério de ferro", "dalian", "commodities", "commodity", "soja", "milho", "safra", "ouro", "gás natural", "combustível", "gasolina")):
        return "🛢️ <b>COMMODITIES &amp; ENERGIA</b>"
    elif any(p in t_busca for p in ("fusão", "aquisição", "compra fatia", "m&a", "incorporação", "ipo", "oferta pública", "reestruturação", "privatização", "joint venture", "concessão")):
        return "🤝 <b>NEGÓCIOS &amp; EMPRESAS</b>"
    elif any(p in t_busca for p in ("inteligência artificial", "chatgpt", "openai", "nvidia", "semicondutores", "chips", "apple", "microsoft", "google", "meta", "big tech", "tsmc")):
        return "🤖 <b>TECNOLOGIA &amp; IA</b>"
    elif any(p in t_busca for p in ("bitcoin", "btc", "ethereum", "eth", "cripto", "criptomoeda", "solana", "drex", "blockchain", "tokenização")):
        return "🪙 <b>CRIPTOATIVOS</b>"
    elif any(p in t_busca for p in ("lula", "congresso", "stf", "senado", "câmara dos deputados", "haddad", "ministério da fazenda", "reforma tributária", "arcabouço fiscal", "palácio do planalto", "orçamento da união")):
        return "🏛️ <b>POLÍTICA &amp; PODER</b>"
    elif any(p in t_busca for p in ("eua", "trump", "biden", "china", "guerra", "rússia", "ucrânia", "oriente médio", "israel", "otan", "taiwan", "bce", "europa", "geopolítica")):
        return "🌍 <b>GEOPOLÍTICA &amp; MUNDO</b>"
    elif any(p in t_busca for p in ("dólar", "selic", "copom", "ipca", "inflação", "banco central", "fed", "juros", "pib", "câmbio", "focus")):
        return "💵 <b>MACROECONOMIA &amp; CÂMBIO</b>"
    elif any(p in t_busca for p in ("lucro", "balanço", "prejuízo", "receita", "ebitda", "petrobras", "vale", "itaú", "bradesco", "b3", "bolsa", "ibovespa", "ações", "nyse", "nasdaq", "s&p 500")):
        return "📊 <b>MERCADO &amp; AÇÕES</b>"
    else:
        return "⚡️ <b>RADAR DE MERCADO</b>"


def carregar_proximas_noticias(limite=MAX_NOTICIAS_POR_VEZ):
    """
    Varredura 24h por dia, de segunda a segunda.
    Aceita SOMENTE notícias publicadas nos últimos 45 minutos (MAX_IDADE_MINUTOS).
    Se não saiu notícia nova nesses minutos, retorna vazio e aguarda o próximo ciclo de 5 min.
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

                # REGRA DE TEMPO REAL: Só aceita notícias que acabaram de sair (últimos 45 minutos)
                dt_pub = extrair_data_publicacao(item)
                if not dt_pub:
                    continue

                idade_minutos = (agora_utc - dt_pub).total_seconds() / 60.0
                if idade_minutos > MAX_IDADE_MINUTOS or idade_minutos < -10.0:
                    continue

                # Filtro Anti-Propaganda
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

    # Ordena TODAS as notícias pela mais recente primeiro (últimos minutos no topo)
    candidatos.sort(key=lambda x: x["dt_pub"], reverse=True)

    noticias = []
    for cand in candidatos:
        title = cand["title"]
        link = cand["link"]
        desc_bruta = cand["desc_bruta"]
        item = cand["item"]

        # Anti-Repetição Semântica
        if eh_mesma_noticia(title, titulos_rodada):
            continue

        # Detecta mídias do RSS
        foto, video, audio = extrair_midias_do_rss(item, desc_bruta)

        # Busca corpo limpo do artigo e imagem da página (og:image)
        texto_artigo, foto_og = obter_conteudo_e_imagem_artigo(link, desc_bruta, title)
        foto = foto or foto_og

        # REGRA CRUCIAL: Só envia se TIVER IMAGEM, VÍDEO OU ÁUDIO (nunca envia texto pelado)
        if not foto and not video and not audio:
            continue

        # Formata resumo rico de 500 a 600 caracteres
        resumo_formatado = formatar_resumo_500_600(texto_artigo, title)
        categoria = categorizar_materia(title, resumo_formatado)

        t_limpo = title.strip()
        if " - " in t_limpo:
            t_limpo, _ = t_limpo.rsplit(" - ", 1)
        if t_limpo and t_limpo[-1] not in ".!?":
            t_limpo += "."

        post = f"{categoria}\n\n<b>{html.escape(t_limpo)}</b>"
        if resumo_formatado and len(resumo_formatado) > 25:
            post += f"\n\n{html.escape(resumo_formatado)}"

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

    # 4. Fallback em texto (só se tudo falhar)
    url_msg = f"https://api.telegram.org/bot{token}/sendMessage"
    payload_msg = {"chat_id": chat_id, "text": item["texto"], "parse_mode": "HTML", "disable_web_page_preview": False}
    status, _ = http_post_json(url_msg, payload_msg, timeout=20)
    return status == 200


if __name__ == "__main__":
    env_token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    token = env_token if env_token else _PADRAO_TOKEN

    env_chat = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    chat_id = env_chat if env_chat else _PADRAO_CHAT_ID

    noticias, links_historico, titulos_historico = carregar_proximas_noticias(limite=MAX_NOTICIAS_POR_VEZ)
    if not noticias:
        logging.info("Nenhuma notícia nova nos últimos 45 minutos. Aguardando próximo ciclo de 5 min.")
        sys.exit(0)

    logging.info(f"Encontradas {len(noticias)} notícias quentes para envio.")
    for noticia in noticias:
        sucesso = enviar_post(token, chat_id, noticia)
        if sucesso:
            links_historico.append(noticia["link"])
            titulos_historico.append(noticia["titulo"])
            salvar_historico(links_historico, titulos_historico)
            logging.info(f"Notícia enviada com imagem: {noticia['titulo']}")
            time.sleep(3)
        else:
            logging.error("Falha ao enviar post para o Telegram.")
