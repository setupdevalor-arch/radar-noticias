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
MAX_HISTORICO = 400
MAX_NOTICIAS_POR_VEZ = 1  # 1 notícia por vez a cada 5 minutos

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

# Feeds 100% voltados para Mercado Financeiro, Ações, Macro e Cripto
FEEDS_MERCADO = [
    "https://www.infomoney.com.br/feed/",
    "https://www.infomoney.com.br/mercados/feed/",
    "https://www.suno.com.br/noticias/feed/",
    "https://www.moneytimes.com.br/feed/",
    "https://www.seudinheiro.com/feed/",
    "https://einvestidor.estadao.com.br/feed/",
    "https://g1.globo.com/rss/g1/economia/",
    "https://portaldobitcoin.uol.com.br/feed/",
    "https://br.cointelegraph.com/rss"
]

# Bloqueio de FIIs, fofocas e matérias de consumo/varejo não financeiro
BLOQUEADOS = [
    "fundo imobiliário", "fundos imobiliários", "fii ", "fiis", "fiagro", "ifix",
    "por cota", "cotistas", "rendimento por cota", "horóscopo", "signo", "loteria",
    "mega-sena", "bbb", "fofoca", "onde assistir", "escalação", "futebol", "novela"
]


def http_get(url, timeout=12):
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
                if isinstance(dados, list):
                    return dados
        except Exception as e:
            logging.error(f"Erro ao ler histórico: {e}")
    return []


def salvar_historico(historico):
    try:
        vistos = set()
        unico = []
        for link in historico:
            if link not in vistos:
                vistos.add(link)
                unico.append(link)
        with open(HISTORICO_ARQUIVO, "w", encoding="utf-8") as f:
            json.dump(unico[-MAX_HISTORICO:], f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Erro ao salvar histórico: {e}")


def limpar_html(txt):
    if not txt:
        return ""
    txt = re.sub(r'<[^>]+>', '', txt)
    txt = html.unescape(txt)
    return " ".join(txt.split()).strip()


def extrair_midias(item, desc_bruta):
    """Detecta imagem, vídeo ou áudio (podcast/resumo) anexados à notícia."""
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

    # 2. Tags do padrão Media RSS
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

    # 3. Busca imagem no corpo HTML da descrição
    if not foto_url and desc_bruta:
        busca = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc_bruta, re.IGNORECASE)
        if busca and busca.group(1).startswith("http"):
            foto_url = busca.group(1)

    return foto_url, video_url, audio_url


def formatar_post_mercado(titulo, descricao):
    """Formata a notícia no padrão executivo de Breaking News de Mercado."""
    t = titulo.strip()
    if " - " in t:
        t, _ = t.rsplit(" - ", 1)

    t_lower = t.lower()

    # Ícone e categoria contextual do mercado
    if any(p in t_lower for p in ("dividendo", "provento", "jcp", "juros sobre capital", "data com")):
        icone = "💰 <b>DIVIDENDOS</b>"
    elif any(p in t_lower for p in ("lucro", "balanço", "prejuízo", "receita", "ebitda", "trimestre")):
        icone = "📊 <b>RESULTADOS</b>"
    elif any(p in t_lower for p in ("dólar", "selic", "copom", "ipca", "inflação", "fed", "fiscal", "pib")):
        icone = "💵 <b>MACRO & CÂMBIO</b>"
    elif any(p in t_lower for p in ("petrobras", "petr4", "vale", "vale3", "banco do brasil", "bbas3", "itaú", "itub4")):
        icone = "🏛️ <b>EMPRESAS & B3</b>"
    elif any(p in t_lower for p in ("bitcoin", "btc", "ethereum", "cripto", "solana")):
        icone = "🪙 <b>CRIPTOATIVOS</b>"
    elif any(p in t_lower for p in ("eua", "wall street", "s&p", "nasdaq", "china", "petróleo", "brent")):
        icone = "🌍 <b>MERCADO GLOBAL</b>"
    else:
        icone = "⚡️ <b>RADAR DE MERCADO</b>"

    if t and t[-1] not in ".!?":
        t += "."

    d = limpar_html(descricao)
    if d:
        if d.lower().startswith(t[:20].lower()):
            d = d[len(t):].strip()
        d = re.sub(r'\s*[;:]\s*(veja|leia mais|saiba mais|confira|entenda).*$', '', d, flags=re.IGNORECASE).strip()
        if d and d[-1] not in ".!?":
            d += "."

    post = f"{icone}\n\n<b>{html.escape(t)}</b>"
    if d and len(d) > 25 and d.lower() != t.lower():
        if len(d) > 550:
            d = d[:547] + "..."
        post += f"\n\n{html.escape(d)}"

    return post


def carregar_proxima_noticia():
    historico = carregar_historico()

    for url_feed in FEEDS_MERCADO:
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

                if not link or not title or link in historico:
                    continue

                if len(title) < 25 or any(b in title.lower() for b in BLOQUEADOS):
                    continue

                foto, video, audio = extrair_midias(item, desc_bruta)
                texto = formatar_post_mercado(title, desc_bruta)

                # Retorna a primeira matéria inédita encontrada
                return {
                    "link": link,
                    "texto": texto,
                    "imagem": foto,
                    "video": video,
                    "audio": audio
                }, historico
        except Exception as e:
            logging.warning(f"Aviso no feed {url_feed}: {e}")

    return None, historico


def enviar_post(token, chat_id, item):
    """Envia áudio, vídeo, foto ou texto nativo para o Telegram."""
    # 1. Se tiver resumo em Áudio (Podcast / Resumo falado)
    if item.get("audio"):
        url_aud = f"https://api.telegram.org/bot{token}/sendAudio"
        payload_aud = {"chat_id": chat_id, "audio": item["audio"], "caption": item["texto"], "parse_mode": "HTML"}
        status, resp = http_post_json(url_aud, payload_aud, timeout=25)
        logging.info(f"Envio de áudio para {chat_id} -> HTTP {status}")
        if status == 200:
            return True

    # 2. Se tiver Vídeo
    if item.get("video"):
        url_vid = f"https://api.telegram.org/bot{token}/sendVideo"
        payload_vid = {"chat_id": chat_id, "video": item["video"], "caption": item["texto"], "parse_mode": "HTML"}
        status, resp = http_post_json(url_vid, payload_vid, timeout=30)
        logging.info(f"Envio de vídeo para {chat_id} -> HTTP {status}")
        if status == 200:
            return True

    # 3. Se tiver Imagem (Foto de capa em alta definição)
    if item.get("imagem"):
        url_photo = f"https://api.telegram.org/bot{token}/sendPhoto"
        payload_photo = {"chat_id": chat_id, "photo": item["imagem"], "caption": item["texto"], "parse_mode": "HTML"}
        status, resp = http_post_json(url_photo, payload_photo, timeout=20)
        logging.info(f"Envio de foto para {chat_id} -> HTTP {status}")
        if status == 200:
            return True

    # 4. Fallback Texto
    url_msg = f"https://api.telegram.org/bot{token}/sendMessage"
    payload_msg = {"chat_id": chat_id, "text": item["texto"], "parse_mode": "HTML", "disable_web_page_preview": False}
    status, resp = http_post_json(url_msg, payload_msg, timeout=20)
    logging.info(f"Envio de texto para {chat_id} -> HTTP {status}")
    return status == 200


if __name__ == "__main__":
    env_token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    token = env_token if env_token else TOKEN_FIXO

    env_chat = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    chat_id = env_chat if env_chat else CHAT_ID_FIXO

    noticia, historico = carregar_proxima_noticia()
    if not noticia:
        logging.info("Nenhuma novidade do mercado no momento.")
        sys.exit(0)

    sucesso = enviar_post(token, chat_id, noticia)
    if sucesso:
        historico.append(noticia["link"])
        salvar_historico(historico)
        logging.info(f"Notícia enviada com sucesso: {noticia['link']}")
    else:
        logging.error("Falha ao enviar notícia para o Telegram.")
        sys.exit(1)
