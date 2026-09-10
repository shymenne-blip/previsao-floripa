"""
Previsão diária do tempo no Telegram — Florianópolis (Centro)
Fonte: Open-Meteo (gratuita, sem chave). Sem dependências externas.

Variáveis de ambiente:
  TELEGRAM_BOT_TOKEN  token do @BotFather
  TELEGRAM_CHAT_ID    seu chat id
  ANTHROPIC_API_KEY   chave da API da Anthropic (console.anthropic.com)
  CLAUDE_MODEL        (opcional) padrão: claude-haiku-4-5
  DRY_RUN=1           (opcional) só imprime, não envia

Divisão de responsabilidades:
  - A REGRA (código) decide se é bom dia para lavar roupa e calcula os números.
  - O CLAUDE só redige a abertura da mensagem e uma dica prática.
  - Os números vão num bloco fixo, fora do texto do Claude.
  - Se o Claude falhar ou fugir do veredito, a mensagem sai sem ele.
"""
import html
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime

LAT, LON = -27.5954, -48.5480          # Centro de Florianópolis
TZ = "America/Sao_Paulo"
JANELA_SECAGEM = range(9, 17)          # 9h às 16h59: horário útil do varal

# Limiares ajustáveis. Floripa é úmida: calibre com a sua experiência.
CHUVA_PROB_NAO = 60      # % — acima disso, não lave
CHUVA_MM_NAO = 1.0       # mm na janela
CHUVA_PROB_RISCO = 30
CHUVA_MM_RISCO = 0.2
UMIDADE_RISCO = 85       # % média na janela
UMIDADE_OTIMA = 70
VENTO_BOM = 10           # km/h
NUVENS_BOA = 50          # %


def buscar_previsao():
    params = {
        "latitude": LAT, "longitude": LON, "timezone": TZ, "forecast_days": 1,
        "hourly": "temperature_2m,relative_humidity_2m,precipitation_probability,"
                  "precipitation,wind_speed_10m,cloud_cover",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,"
                 "precipitation_probability_max",
    }
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def media(v):
    v = [x for x in v if x is not None]
    return sum(v) / len(v) if v else 0


def analisar(dados):
    h = dados["hourly"]
    idx = [i for i, t in enumerate(h["time"])
           if datetime.fromisoformat(t).hour in JANELA_SECAGEM]
    pick = lambda k: [h[k][i] for i in idx]

    janela = {
        "prob_max": max(x or 0 for x in pick("precipitation_probability")),
        "chuva_mm": sum(x or 0 for x in pick("precipitation")),
        "umidade": media(pick("relative_humidity_2m")),
        "vento": media(pick("wind_speed_10m")),
        "nuvens": media(pick("cloud_cover")),
    }

    if janela["prob_max"] >= CHUVA_PROB_NAO or janela["chuva_mm"] >= CHUVA_MM_NAO:
        veredito = "❌ Não. Alto risco de chuva durante o dia."
    elif (janela["prob_max"] >= CHUVA_PROB_RISCO or janela["chuva_mm"] >= CHUVA_MM_RISCO
          or janela["umidade"] >= UMIDADE_RISCO):
        veredito = "⚠️ Arriscado. Chance de chuva ou umidade alta; prefira varal coberto."
    elif janela["umidade"] <= UMIDADE_OTIMA and (
            janela["vento"] >= VENTO_BOM or janela["nuvens"] <= NUVENS_BOA):
        veredito = "✅ Ótimo dia para lavar roupa."
    else:
        veredito = "🙂 Dá para lavar, mas a secagem deve ser lenta."

    d = dados["daily"]
    return {
        "data": d["time"][0],
        "tmin": d["temperature_2m_min"][0],
        "tmax": d["temperature_2m_max"][0],
        "chuva_dia": d["precipitation_sum"][0] or 0,
        "prob_dia": d["precipitation_probability_max"][0] or 0,
        "umidade_dia": media(h["relative_humidity_2m"]),
        "janela": janela,
        "veredito": veredito,
    }


def bloco_dados(a):
    dia = datetime.fromisoformat(a["data"]).strftime("%d/%m")
    j = a["janela"]
    return (
        f"🌦 <b>Florianópolis — {dia}</b>\n"
        f"🌡 {a['tmin']:.0f}°C a {a['tmax']:.0f}°C\n"
        f"🌧 Chuva: {a['chuva_dia']:.1f} mm (prob. máx. {a['prob_dia']:.0f}%)\n"
        f"💧 Umidade média: {a['umidade_dia']:.0f}%\n"
        f"👕 {a['veredito']}\n"
        f"<i>9h–17h: chuva {j['chuva_mm']:.1f} mm, prob. {j['prob_max']:.0f}%, "
        f"umidade {j['umidade']:.0f}%, vento {j['vento']:.0f} km/h, "
        f"nuvens {j['nuvens']:.0f}%</i>"
    )


PROMPT_SISTEMA = """Você escreve a abertura de uma mensagem matinal de previsão do tempo \
para uma moradora do Centro de Florianópolis, enviada pelo Telegram.

Regras obrigatórias:
- Português do Brasil, tom leve e direto, no máximo 3 frases curtas.
- O veredito sobre lavar roupa já foi decidido. Repita o sentido dele; \
nunca o contradiga nem o suavize.
- Inclua uma dica prática coerente com os dados (ex.: guarda-chuva, \
estender cedo, varal coberto, recolher antes de determinado horário).
- Não invente números nem informações que não estejam nos dados.
- Não repita a lista de números: ela é exibida logo abaixo do seu texto.
- Texto puro, sem HTML, sem markdown, sem saudação com nome."""


def redigir_com_claude(a):
    chave = os.environ.get("ANTHROPIC_API_KEY")
    if not chave:
        return None
    dados = {k: v for k, v in a.items()}
    corpo = json.dumps({
        "model": os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5"),
        "max_tokens": 300,
        "system": PROMPT_SISTEMA,
        "messages": [{"role": "user", "content":
                      "Dados de hoje (JSON):\n" + json.dumps(dados, ensure_ascii=False)}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=corpo, method="POST",
        headers={"x-api-key": chave, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.load(r)
        texto = "".join(b.get("text", "") for b in resp["content"]
                        if b.get("type") == "text").strip()
    except Exception as e:
        print(f"Claude indisponível, seguindo sem ele: {e}")
        return None
    return texto if texto_confiavel(texto, a) else None


def texto_confiavel(texto, a):
    """Trava de segurança: descarta textos vazios, longos ou que contradizem a regra."""
    if not texto or len(texto) > 600:
        return False
    t = texto.lower()
    negativo = a["veredito"].startswith(("❌", "⚠️"))
    if negativo and any(p in t for p in ("ótimo dia para lavar", "pode lavar tranquil",
                                           "dia perfeito para lavar")):
        print("Claude contradisse o veredito; texto descartado.")
        return False
    return True


def montar_mensagem(a, texto_claude=None):
    dados = bloco_dados(a)
    if texto_claude:
        return html.escape(texto_claude) + "\n\n" + dados
    return dados


def enviar_telegram(texto):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    corpo = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": texto, "parse_mode": "HTML"}).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    with urllib.request.urlopen(url, data=corpo, timeout=30) as r:
        if not json.load(r).get("ok"):
            raise RuntimeError("Telegram recusou a mensagem")


def main():
    analise = analisar(buscar_previsao())
    texto = montar_mensagem(analise, redigir_com_claude(analise))
    if os.environ.get("DRY_RUN"):
        print(texto)
    else:
        enviar_telegram(texto)
        print("Mensagem enviada.")


if __name__ == "__main__":
    main()
