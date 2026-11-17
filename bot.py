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
CHAT_ID_FIXO = ""  # Se tiver o @username do canal, coloque aqui (ex: "@radar_noticias")

HISTORICO_ARQUIVO = "historico_enviados.json"
MAX_HISTORICO = 300
MAX_NOTICIAS_POR_RODADA = 6  # Envia de 5 a 6 matérias separadas por vez

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

# Palavras para filtrar bobagens/fofocas irrelevantes
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


def extrair_imagem(item, desc_bruta):
    """Extrai URL da foto oficial da notícia a partir das tags do XML."""
    # 1. Enclosure
    enc = item.find("enclosure")
    if enc is not None and "url" in enc.attrib:
        url = enc.attrib["url"]
        if url.startswith("http") and not url.endswith((".mp3", ".mp4")):
            return url

    # 2. Media content / thumbnail
    for tag in ("{http://search.yahoo.com/mrss/}content", "{http://search.yahoo.com/mrss/}thumbnail"):
        m = item.find(tag)
        if m is not None and "url" in m.attrib:
            url = m.attrib["url"]
            if url.startswith("http"):
                return url

    # 3. Tag <img> dentro da descrição
    if desc_bruta:
        busca = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', desc_bruta, re.IGNORECASE)
        if busca:
            url = busca.group(1)
            if url.startswith("http"):
                return url

    return None


def formatar_post_informante(titulo, descricao):
    """Formata o texto no estilo limpo e de impacto de 'O Informante'."""
    t = titulo.strip()
    if ":" in t:
        prefixo, resto = t.split(":", 1)
        if len(prefixo.split()) <= 2 and prefixo.lower() in ("urgente", "alerta", "agora", "veja", "atenção"):
            t = resto.strip()

    t_lower = t.lower()
    # Adiciona emojis contextuais
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

    # Garante pontuação final
    if t and t[-1] not in ".!?":
        t += "."

    # Descrição resumida
    d = limpar_html(descricao)
    if d:
        # Remove título repetido no início da descrição
        if d.lower().startswith(t[:20].lower()):
            d = d[len(t):].strip()
        # Corta caudas caça-cliques
        d = re.sub(r'\s*[;:]\s*(veja|leia mais|saiba mais|confira).*$', '', d, flags=re.IGNORECASE).strip()
        if d and d[-1] not in ".!?":
            d += "."

    corpo = f"{icone}<b>{html.escape(t)}</b>"
    if d and len(d) > 20 and d.lower() != t.lower():
        # Limita o texto para não estourar a legenda de foto do Telegram (máx 1000 caracteres)
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

                # Evita notícias muito parecidas de portais diferentes
                assinatura = "".join(c for c in title.lower() if c.isalnum())[:35]
                if assinatura in titulos_vistos:
                    continue
                titulos_vistos.add(assinatura)

                img = extrair_imagem(item, desc_bruta)
                texto_formatado = formatar_post_informante(title, desc_bruta)

                novas.append({
                    "link": link,
                    "texto": texto_formatado,
                    "imagem": img
                })

                if len(novas) >= MAX_NOTICIAS_POR_RODADA:
                    return novas, historico
        except Exception as e:
            logging.warning(f"Aviso no feed {url_feed}: {e}")

    return novas, historico


def descobrir_chats(token):
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    try:
        dados = json.loads(http_get(url, timeout=10).decode("utf-8"))
        chats = []
        for upd in dados.get("result", []):
            for chave in ("channel_post", "edited_channel_post", "my_chat_member", "message"):
                bloco = upd.get(chave)
                if isinstance(bloco, dict):
                    chat = bloco.get("chat") or {}
                    cid = str(chat.get("id", ""))
                    if cid and cid not in chats:
                        chats.append(cid)
        return chats
    except Exception:
        return []


def enviar_post(token, chat_id, item):
    """Envia com foto (sendPhoto) se houver imagem; senão, envia texto (sendMessage)."""
    # 1. Tentativa de envio com Foto
    if item["imagem"]:
        url_photo = f"https://api.telegram.org/bot{token}/sendPhoto"
        payload_photo = {
            "chat_id": chat_id,
            "photo": item["imagem"],
            "caption": item["texto"],
            "parse_mode": "HTML"
        }
        status, resp = http_post_json(url_photo, payload_photo, timeout=20)
        logging.info(f"Envio de foto para {chat_id} -> HTTP {status}")
        if status == 200:
            return True

    # 2. Fallback para envio apenas em Texto
    url_msg = f"https://api.telegram.org/bot{token}/sendMessage"
    payload_msg = {
        "chat_id": chat_id,
        "text": item["texto"],
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    status, resp = http_post_json(url_msg, payload_msg, timeout=20)
    logging.info(f"Envio de texto para {chat_id} -> HTTP {status}")
    return status == 200


if __name__ == "__main__":
    env_token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    token = env_token if env_token else TOKEN_FIXO

    env_chat = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    chat_id = env_chat if env_chat else CHAT_ID_FIXO

    if not chat_id:
        descobertos = descobrir_chats(token)
        if descobertos:
            chat_id = descobertos[0]
            logging.info(f"Chat descoberto automaticamente: {chat_id}")
        else:
            logging.error("Nenhum CHAT_ID informado e nenhum chat encontrado no getUpdates.")
            print("::error title=Falta Chat ID::Coloque o bot no canal e envie uma mensagem de teste para ele descobrir o ID!")
            sys.exit(1)

    noticias, historico = carregar_noticias()
    if not noticias:
        logging.info("Nenhuma notícia nova no momento.")
        sys.exit(0)

    logging.info(f"Encontradas {len(noticias)} notícias novas para postar.")
    for item in noticias:
        sucesso = enviar_post(token, chat_id, item)
        if sucesso:
            historico.append(item["link"])
            salvar_historico(historico)
            time.sleep(3)  # Pausa entre posts para ficar cadenciado e seguro
