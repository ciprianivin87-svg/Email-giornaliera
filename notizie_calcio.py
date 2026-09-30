#!/usr/bin/env python3
"""
Raccoglie le ultime news di calcio dai principali siti sportivi italiani (RSS),
costruisce una email HTML e la invia via SMTP.

Variabili d'ambiente richieste (per l'invio):
    MAIL_USERNAME  -> indirizzo Gmail mittente
    MAIL_PASSWORD  -> password per le app di Google
    MAIL_TO        -> destinatario

Uso:
    python notizie_calcio.py              # raccoglie e invia
    python notizie_calcio.py --dry-run    # salva solo anteprima.html, niente invio
"""

import argparse
import html
import os
import re
import smtplib
import ssl
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parsedate_to_datetime

# ----------------------------------------------------------------------------
# CONFIGURAZIONE
# ----------------------------------------------------------------------------
# Se un feed smette di funzionare, basta correggere o rimuovere la riga.
FONTI = [
    {"nome": "Gazzetta dello Sport", "colore": "#e6007e", "url": "https://www.gazzetta.it/rss/calcio.xml"},
    {"nome": "Corriere dello Sport", "colore": "#0057b8", "url": "https://www.corrieredellosport.it/rss/calcio"},
    {"nome": "Tuttosport", "colore": "#f5a300", "url": "https://www.tuttosport.com/rss/calcio"},
    {"nome": "Sky Sport", "colore": "#0072c9", "url": "https://sport.sky.it/rss/calcio.xml"},
    {"nome": "ANSA Calcio", "colore": "#00a86b", "url": "https://www.ansa.it/sito/notizie/sport/calcio/calcio_rss.xml"},
]

NEWS_PER_FONTE = 5      # quante notizie mostrare per ogni sito
TIMEOUT = 15            # secondi di attesa per ogni feed
USER_AGENT = "Mozilla/5.0 (compatible; NotizieCalcioBot/1.0)"

MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
        "agosto", "settembre", "ottobre", "novembre", "dicembre"]
GIORNI = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]


# ----------------------------------------------------------------------------
# RACCOLTA NEWS
# ----------------------------------------------------------------------------
def _local(tag):
    """Nome del tag senza namespace."""
    return tag.rsplit("}", 1)[-1]


def _pulisci(testo, max_len=170):
    testo = html.unescape(testo or "")
    testo = re.sub(r"<[^>]+>", " ", testo)
    testo = re.sub(r"\s+", " ", testo).strip()
    if len(testo) > max_len:
        testo = testo[:max_len].rsplit(" ", 1)[0] + "…"
    return testo


def _data(valore):
    if not valore:
        return None
    try:
        d = parsedate_to_datetime(valore)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(valore.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def parse_feed(contenuto):
    """Legge un feed RSS/Atom e restituisce una lista di dizionari."""
    radice = ET.fromstring(contenuto)
    articoli = []

    for item in radice.iter():
        if _local(item.tag) not in ("item", "entry"):
            continue

        titolo = link = descrizione = data = immagine = ""
        for el in item:
            nome = _local(el.tag)
            if nome == "title":
                titolo = _pulisci(el.text, 200)
            elif nome == "link":
                link = (el.text or el.attrib.get("href", "")).strip()
            elif nome in ("description", "summary", "content", "encoded") and not descrizione:
                descrizione = el.text or ""
            elif nome in ("pubDate", "published", "updated", "date") and not data:
                data = el.text
            elif nome in ("enclosure", "content", "thumbnail") and el.attrib.get("url"):
                tipo = el.attrib.get("type", "image")
                if tipo.startswith("image") and not immagine:
                    immagine = el.attrib["url"]

        if not immagine:
            m = re.search(r'<img[^>]+src=["\']([^"\']+)', html.unescape(descrizione))
            if m:
                immagine = m.group(1)

        if titolo and link:
            articoli.append({
                "titolo": titolo,
                "link": link,
                "descrizione": _pulisci(descrizione),
                "data": _data(data),
                "immagine": immagine if immagine.startswith("http") else "",
            })
    return articoli


def scarica_fonte(fonte):
    req = urllib.request.Request(fonte["url"], headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        contenuto = r.read()
    articoli = parse_feed(contenuto)
    articoli.sort(key=lambda a: a["data"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return articoli[:NEWS_PER_FONTE]


def raccogli_news():
    risultati = []
    for fonte in FONTI:
        try:
            articoli = scarica_fonte(fonte)
            print(f"[ok]     {fonte['nome']}: {len(articoli)} notizie")
        except Exception as e:  # un feed rotto non deve bloccare tutto
            print(f"[errore] {fonte['nome']}: {e}", file=sys.stderr)
            continue
        if articoli:
            risultati.append({**fonte, "articoli": articoli})
    return risultati


# ----------------------------------------------------------------------------
# LAYOUT EMAIL
# ----------------------------------------------------------------------------
def _esc(t):
    return html.escape(t or "", quote=True)


def _ora(a):
    if not a["data"]:
        return ""
    return a["data"].astimezone().strftime("%H:%M")


def _card_principale(a, fonte):
    img = ""
    if a["immagine"]:
        img = (f'<a href="{_esc(a["link"])}"><img src="{_esc(a["immagine"])}" width="560" alt="" '
               f'style="display:block;width:100%;max-width:560px;height:auto;border-radius:14px 14px 0 0;"></a>')
    radius = "0 0 14px 14px" if a["immagine"] else "14px"
    return f"""
    <tr><td style="padding:0 20px 8px 20px;">
      {img}
      <div style="background:#ffffff;border-radius:{radius};padding:18px 20px 20px 20px;border:1px solid #e6ebf2;">
        <span style="display:inline-block;background:{fonte['colore']};color:#fff;font-size:11px;font-weight:700;
                     letter-spacing:.6px;text-transform:uppercase;padding:4px 10px;border-radius:20px;">{_esc(fonte['nome'])}</span>
        <h2 style="margin:12px 0 8px 0;font-size:22px;line-height:1.25;color:#0b1220;">
          <a href="{_esc(a['link'])}" style="color:#0b1220;text-decoration:none;">{_esc(a['titolo'])}</a></h2>
        <p style="margin:0 0 14px 0;font-size:14px;line-height:1.55;color:#4a5568;">{_esc(a['descrizione'])}</p>
        <a href="{_esc(a['link'])}" style="display:inline-block;background:#00c37a;color:#04121f;font-weight:700;
           font-size:13px;text-decoration:none;padding:10px 18px;border-radius:8px;">Leggi la notizia →</a>
      </div>
    </td></tr>"""


def _riga_news(a):
    thumb = ""
    if a["immagine"]:
        thumb = (f'<td width="84" valign="top" style="padding-right:14px;">'
                 f'<img src="{_esc(a["immagine"])}" width="84" height="64" alt="" '
                 f'style="display:block;width:84px;height:64px;object-fit:cover;border-radius:8px;"></td>')
    ora = _ora(a)
    ora_html = f'<span style="color:#8a94a6;font-size:11px;">{ora}</span>' if ora else ""
    return f"""
      <tr><td style="padding:12px 0;border-top:1px solid #edf0f5;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
          {thumb}
          <td valign="top">
            <a href="{_esc(a['link'])}" style="color:#0b1220;font-size:15px;font-weight:600;line-height:1.35;
               text-decoration:none;">{_esc(a['titolo'])}</a><br>{ora_html}
          </td>
        </tr></table>
      </td></tr>"""


def _sezione(fonte, articoli):
    righe = "".join(_riga_news(a) for a in articoli)
    return f"""
    <tr><td style="padding:14px 20px 4px 20px;">
      <div style="background:#ffffff;border-radius:14px;border:1px solid #e6ebf2;
                  border-left:5px solid {fonte['colore']};padding:16px 18px 6px 18px;">
        <div style="font-size:13px;font-weight:800;letter-spacing:.8px;text-transform:uppercase;
                    color:{fonte['colore']};padding-bottom:8px;">{_esc(fonte['nome'])}</div>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{righe}</table>
      </div>
    </td></tr>"""


def costruisci_html(fonti_news):
    adesso = datetime.now()
    data_it = f"{GIORNI[adesso.weekday()].capitalize()} {adesso.day} {MESI[adesso.month - 1]} {adesso.year}"

    # Notizia in evidenza: la prima con immagine tra le più recenti
    tutte = [(f, a) for f in fonti_news for a in f["articoli"]]
    tutte.sort(key=lambda x: x[1]["data"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    evidenza = next(((f, a) for f, a in tutte if a["immagine"]), tutte[0] if tutte else None)

    corpo = ""
    if evidenza:
        f, a = evidenza
        corpo += _card_principale(a, f)
        # Evita duplicati nelle liste
        for fonte in fonti_news:
            fonte["articoli"] = [x for x in fonte["articoli"] if x["link"] != a["link"]]
    for fonte in fonti_news:
        if fonte["articoli"]:
            corpo += _sezione(fonte, fonte["articoli"])

    if not corpo:
        corpo = ('<tr><td style="padding:30px 20px;text-align:center;color:#4a5568;">'
                 'Nessuna notizia disponibile al momento.</td></tr>')

    totale = len(tutte)
    return f"""<!DOCTYPE html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Notizie Calcio</title></head>
<body style="margin:0;padding:0;background:#eef2f7;font-family:'Segoe UI',Helvetica,Arial,sans-serif;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eef2f7;">
<tr><td align="center" style="padding:20px 8px;">
  <table role="presentation" width="600" cellpadding="0" cellspacing="0"
         style="width:100%;max-width:600px;">
    <tr><td style="background:#0b1220;background-image:linear-gradient(135deg,#0b1220 0%,#0f3d2e 100%);
                   border-radius:18px;padding:30px 26px;text-align:left;">
      <div style="font-size:12px;letter-spacing:2px;text-transform:uppercase;color:#00e58f;font-weight:700;">
        ⚽ Rassegna stampa</div>
      <div style="font-size:30px;font-weight:800;color:#ffffff;margin:6px 0 4px 0;">Notizie di Calcio</div>
      <div style="font-size:14px;color:#b7c4d6;">{data_it} · {totale} notizie da {len(fonti_news)} fonti</div>
    </td></tr>
    <tr><td style="height:16px;font-size:0;line-height:0;">&nbsp;</td></tr>
    {corpo}
    <tr><td style="padding:22px 20px 6px 20px;text-align:center;font-size:12px;color:#8a94a6;line-height:1.6;">
      Email generata automaticamente con GitHub Actions.<br>
      Le notizie appartengono ai rispettivi editori: clicca per leggere l'articolo completo.
    </td></tr>
  </table>
</td></tr></table>
</body></html>"""


def costruisci_testo(fonti_news):
    """Versione testuale per i client che non mostrano l'HTML."""
    righe = ["NOTIZIE DI CALCIO", ""]
    for fonte in fonti_news:
        righe.append(fonte["nome"].upper())
        for a in fonte["articoli"]:
            righe.append(f"- {a['titolo']}\n  {a['link']}")
        righe.append("")
    return "\n".join(righe)


# ----------------------------------------------------------------------------
# INVIO EMAIL
# ----------------------------------------------------------------------------
def invia_email(html_body, testo):
    utente = os.environ.get("MAIL_USERNAME")
    password = os.environ.get("MAIL_PASSWORD")
    destinatario = os.environ.get("MAIL_TO")
    if not (utente and password and destinatario):
        sys.exit("Errore: servono le variabili MAIL_USERNAME, MAIL_PASSWORD e MAIL_TO.")

    msg = EmailMessage()
    msg["Subject"] = f"⚽ Notizie di calcio · {datetime.now().day} {MESI[datetime.now().month - 1]}"
    msg["From"] = f"Notizie Calcio <{utente}>"
    msg["To"] = destinatario
    msg.set_content(testo)
    msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as server:
        server.login(utente, password)
        server.send_message(msg)
    print(f"Email inviata a {destinatario}")


# ----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="salva anteprima.html senza inviare")
    args = parser.parse_args()

    fonti_news = raccogli_news()
    if not fonti_news:
        sys.exit("Nessun feed ha restituito notizie: controlla gli URL in FONTI.")

    testo = costruisci_testo(fonti_news)
    corpo_html = costruisci_html(fonti_news)

    if args.dry_run:
        with open("anteprima.html", "w", encoding="utf-8") as f:
            f.write(corpo_html)
        print("Anteprima salvata in anteprima.html")
    else:
        invia_email(corpo_html, testo)


if __name__ == "__main__":
    main()
