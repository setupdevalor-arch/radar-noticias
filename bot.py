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
# CONFIGURAÇÃO DO BOT E CANAL
# ==============================================================================
TOKEN_FIXO = "9025960857:AAHRpjo7tDiU2C8O-2zdoM6aKk9eLQks7js"
CHAT_ID_FIXO = "-1003533060222"

HISTORICO_ARQUIVO = "historico_enviados.json"
MAX_HISTORICO = 300
MAX_NOTICIAS_POR_RODADA = 5  # Quantidade de notícias por envio

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

# Feeds de notícias em tempo real (G1, UOL, CNN Brasil, Metrópoles, Folha)
FEEDS_RSS = [
    "https://g1.globo.com/rss/g1/",
    "https://g1.globo.com/rss/g1/mundo/",
    "https://noticias.uol.com.br/ultimas/index.xml",
    "https://www.cnnbrasil.com.br/feed/",
    "https://www.metropoles.com/feed",
    "https://feeds.folha.uol.com.br/emcimadahora/rss091.xml",
    "https://news.google.com/rss?hl=pt-BR&gl=BR&ceid=BR:pt-419"
]

BLOQUEADOS = [
    "horóscopo", "signo", "loteria", "mega-sena", "bbb", "fofoca", "onde assistir",
    "escalação", "palpite", "resultado do jogo", "novela", "resumo do capítulo"
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
    """Extrai foto, vídeo ou áudio das tags XML do feed."""
    foto_url = None
    video_url = None
    audio_url = None

    enc = item.find("enclosure")
    if enc is not None and "url" in enc.attrib:
        url = enc.attrib["url"]
        tipo = enc.attrib.get("type", "").lower()
        if "video" in tipo or url.endswith((".mp4", ".mov")):
            video_url = url
        elif "audio" in tipo or url.endswith((".mp3", ".m4a", ".ogg")):
            audio_url = url
        elif "image" in tipo or url.endswith((".jpg", ".jpeg", ".png", ".webp")):
            foto_url = url

    for tag in ("{http://search.yahoo.com/mrss/}content", "{http://search.yahoo.com/mrss/}thumbnail"):
        m = item.find(tag)
        if m is not None and "url" in m.attrib:
            url = m.attrib["url"]
            tipo = m.attrib.get("type", "").lower()
            if "video" in tipo or url.endswith(".mp4"):
                video_url = video_url or url
            elif not foto_url and ("image" in tipo or url.startswith("http")):
                foto_url = url

    if not foto_url and desc_bruta:
        busca = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc_bruta, re.IGNORECASE)
        if busca and busca.group(1).startswith("http"):
            foto_url = busca.group(1)

    return foto_url, video_url, audio_url


def formatar_post_informante(titulo, descricao):
    t = titulo.strip()
    if ":" in t:
        prefixo, resto = t.split(":", 1)
        if len(prefixo.split()) <= 2 and prefixo.lower() in ("urgente", "alerta", "agora", "veja", "atenção"):
            t = resto.strip()

    t_lower = t.lower()
    if any(p in t_lower for p in ("voo", "avião", "aeroporto", "aéreo", "decolagem")):
        icone = "✈️ "
    elif any(p in t_lower for p in ("eua", "estados unidos", "trump", "biden", "washington")):
        icone = "🇺🇸 "
    elif any(p in t_lower for p in ("guerra", "ataque", "míssil", "explosão", "bomba")):
        icone = "🚨 "
    elif any(p in t_lower for p in ("brasil", "lula", "governo", "stf", "congresso", "senado")):
        icone = "🇧🇷 "
    else:
        icone = "🔴 "

    if t and t[-1] not in ".!?":
        t += "."

    d = limpar_html(descricao)
    if d:
        if d.lower().startswith(t[:20].lower()):
            d = d[len(t):].strip()
        d = re.sub(r'\s*[;:]\s*(veja|leia mais|saiba mais|confira).*$', '', d, flags=re.IGNORECASE).strip()
        if d and d[-1] not in ".!?":
            d += "."

    corpo = f"{icone}<b>{html.escape(t)}</b>"
    if d and len(d) > 20 and d.lower() != t.lower():
        if len(d) > 600:
            d = d[:597] + "..."
        corpo += f"\n\n{html.escape(d)}"

    return corpo


def carregar_noticias():
    historico = carregar_historico()
    novas = []
    titulos_vistos = set()

    for url_feed in FEEDS_RSS:
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

                assinatura = "".join(c for c in title.lower() if c.isalnum())[:35]
                if assinatura in titulos_vistos:
                    continue
                titulos_vistos.add(assinatura)

                foto, video, audio = extrair_midias(item, desc_bruta)
                texto_formatado = formatar_post_informante(title, desc_bruta)

                novas.append({
                    "link": link,
                    "texto": texto_formatado,
                    "imagem": foto,
                    "video": video,
                    "audio": audio
                })

                if len(novas) >= MAX_NOTICIAS_POR_RODADA:
                    return novas, historico
        except Exception as e:
            logging.warning(f"Aviso no feed {url_feed}: {e}")

    return novas, historico


def enviar_post(token, chat_id, item):
    """Envia vídeo, foto, áudio ou texto nativo."""
    # 1. Vídeo
    if item.get("video"):
        url_vid = f"https://api.telegram.org/bot{token}/sendVideo"
        payload_vid = {"chat_id": chat_id, "video": item["video"], "caption": item["texto"], "parse_mode": "HTML"}
        status, _ = http_post_json(url_vid, payload_vid, timeout=30)
        if status == 200:
            return True

    # 2. Imagem (Foto)
    if item.get("imagem"):
        url_photo = f"https://api.telegram.org/bot{token}/sendPhoto"
        payload_photo = {"chat_id": chat_id, "photo": item["imagem"], "caption": item["texto"], "parse_mode": "HTML"}
        status, _ = http_post_json(url_photo, payload_photo, timeout=20)
        if status == 200:
            return True

    # 3. Áudio
    if item.get("audio"):
        url_aud = f"https://api.telegram.org/bot{token}/sendAudio"
        payload_aud = {"chat_id": chat_id, "audio": item["audio"], "caption": item["texto"], "parse_mode": "HTML"}
        status, _ = http_post_json(url_aud, payload_aud, timeout=25)
        if status == 200:
            return True

    # 4. Fallback Texto
    url_msg = f"https://api.telegram.org/bot{token}/sendMessage"
    payload_msg = {"chat_id": chat_id, "text": item["texto"], "parse_mode": "HTML", "disable_web_page_preview": False}
    status, _ = http_post_json(url_msg, payload_msg, timeout=20)
    return status == 200


if __name__ == "__main__":
    env_token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    token = env_token if env_token else TOKEN_FIXO

    env_chat = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    chat_id = env_chat if env_chat else CHAT_ID_FIXO

    noticias, historico = carregar_noticias()
    if not noticias:
        logging.info("Nenhuma notícia nova no momento.")
        sys.exit(0)

    logging.info(f"Encontradas {len(noticias)} notícias para postar.")
    for item in noticias:
        sucesso = enviar_post(token, chat_id, item)
        if sucesso:
            historico.append(item["link"])
            salvar_historico(historico)
            time.sleep(3)
