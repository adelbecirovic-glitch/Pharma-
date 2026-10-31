"""Sucht Aktien im Aufwärtstrend, die knapp unter ihrem 50-Tage-Durchschnitt liegen,
und schreibt trend.json. Wird von .github/workflows/trend.yml ausgeführt.

Bestand: S&P 500, Nasdaq 100 und DAX 40 (Zusammensetzung wird bei jedem Lauf von
Wikipedia gelesen) plus die eigene Liste aus aktien/liste.json.

Aufwärtstrend heißt hier (alle drei Bedingungen):
  1. Kurs liegt über dem SMA 150
  2. SMA 50 liegt über dem SMA 150
  3. SMA 150 steigt (heute höher als vor 20 Handelstagen)
Knapp unter SMA 50 heißt: Kurs höchstens KNAPP Prozent unter dem SMA 50 (und nicht darüber).
"""
import io
import json
import sys
from datetime import datetime, timezone

import pandas as pd
import requests
import yfinance as yf

KNAPP = 5.0          # Prozent unter dem SMA 50, die noch als "knapp" gelten
STEIG_TAGE = 20      # SMA 150 heute vs. vor so vielen Handelstagen
UA = {"User-Agent": "Mozilla/5.0 (Aktien-Trendseite; GitHub Actions)"}


def wiki_tabellen(url):
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return pd.read_html(io.StringIO(r.text))


def spalte(df, *namen):
    """Findet eine Spalte, deren Überschrift einen der Namen enthält (auch bei mehrzeiligen Köpfen)."""
    for n in namen:
        for c in df.columns:
            text = " ".join(map(str, c)) if isinstance(c, tuple) else str(c)
            if n.lower() in text.strip().lower():
                return c
    return None


def index_liste(url, index, endung="", boerse_tv=""):
    """Liest die Bestandteile eines Index. Gibt Liste von dicts (name, yahoo, tv, index)."""
    # die größte Tabelle mit Ticker- und Namensspalte ist die Bestandsliste
    kandidaten = []
    for df in wiki_tabellen(url):
        t = spalte(df, "Symbol", "Ticker", "Kürzel")
        n = spalte(df, "Security", "Company", "Name", "Unternehmen")
        if t is not None and n is not None and len(df) >= 20:
            kandidaten.append((len(df), df, t, n))
    if not kandidaten:
        # Ausweichweg: Spalten am Inhalt erkennen (Ticker = kurze Großbuchstaben-Kürzel)
        import re
        for df in wiki_tabellen(url):
            if len(df) < 20:
                continue
            t = n = None
            for c in df.columns:
                werte = df[c].astype(str).str.strip()
                if t is None and werte.str.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?").mean() > 0.8:
                    t = c
                elif n is None and werte.str.len().mean() > 6 and werte.str.contains(r"[a-z]").mean() > 0.8:
                    n = c
            if t is not None and n is not None:
                kandidaten.append((len(df), df, t, n))
    for _, df, t, n in sorted(kandidaten, key=lambda k: -k[0])[:1]:
        aus = []
        for _, z in df.iterrows():
            sym = str(z[t]).strip()
            if not sym or sym == "nan":
                continue
            basis = sym.split(".")[0] if endung else sym
            yahoo = (basis + endung) if endung else sym.replace(".", "-")
            tv = (boerse_tv + ":" + basis) if boerse_tv else sym.replace(".", "")
            aus.append({"name": str(z[n]).strip(), "yahoo": yahoo, "tv": tv, "index": index})
        return aus
    koepfe = [f"{len(df)} Zeilen: " + " | ".join(map(str, df.columns))[:80] for df in wiki_tabellen(url) if len(df) >= 20]
    raise ValueError("keine Tabelle gefunden; Tabellenköpfe: " + " || ".join(koepfe))


QUELLEN = {}
FEHLER = {}


def nasdaq100():
    """Nasdaq-100-Bestand. Der englische Wikipedia-Artikel führt keine Tabelle mehr,
    daher mehrere Quellen nacheinander; die erste mit mindestens 90 Werten gewinnt."""
    gruende = []
    for url in ("https://stockanalysis.com/list/nasdaq-100-stocks/",
                "https://de.wikipedia.org/wiki/NASDAQ-100"):
        try:
            aus = index_liste(url, "Nasdaq 100")
            if len(aus) >= 90:
                return aus
            gruende.append(f"{url}: nur {len(aus)} Werte")
        except Exception as ex:
            gruende.append(f"{url}: {str(ex)[:80]}")
    try:
        h = dict(UA, Accept="application/json, text/plain, */*", Origin="https://www.nasdaq.com",
                 Referer="https://www.nasdaq.com/")
        r = requests.get("https://api.nasdaq.com/api/quote/list-type/nasdaq100", headers=h, timeout=15)
        r.raise_for_status()
        aus = []
        for z in r.json()["data"]["data"]["rows"]:
            sym = str(z.get("symbol", "")).strip()
            if sym:
                aus.append({"name": str(z.get("companyName", sym)).strip(), "yahoo": sym.replace(".", "-"),
                            "tv": sym.replace(".", ""), "index": "Nasdaq 100"})
        if len(aus) >= 90:
            return aus
        gruende.append(f"nasdaq.com: nur {len(aus)} Werte")
    except Exception as ex:
        gruende.append(f"nasdaq.com: {str(ex)[:80]}")
    raise ValueError(" / ".join(gruende))


def bestand():
    quellen = [
        ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", "S&P 500", "", ""),
        ("nasdaq", "Nasdaq 100", "", ""),
        ("https://en.wikipedia.org/wiki/DAX", "DAX", ".DE", "XETR"),
    ]
    alle = {}
    for url, idx, endung, tv in quellen:
        try:
            liste = nasdaq100() if url == "nasdaq" else index_liste(url, idx, endung, tv)
            print(f"{idx}: {len(liste)} Werte")
            QUELLEN[idx] = len(liste)
            for e in liste:
                if e["yahoo"] in alle:
                    alle[e["yahoo"]]["index"] += ", " + idx
                else:
                    alle[e["yahoo"]] = e
        except Exception as ex:
            print(f"Hinweis: {idx} nicht geladen ({ex})")
            QUELLEN[idx] = 0
            FEHLER[idx] = str(ex)[:300]
    try:
        with open("aktien/liste.json", encoding="utf-8") as f:
            for e in json.load(f):
                if e["yahoo"] in alle:
                    alle[e["yahoo"]]["index"] += ", Eigene Liste"
                else:
                    alle[e["yahoo"]] = {"name": e["name"], "yahoo": e["yahoo"], "tv": e.get("tv", ""), "index": "Eigene Liste"}
    except Exception as ex:
        print(f"Hinweis: eigene Liste nicht geladen ({ex})")
    return list(alle.values())


def auswerten(e, schluss):
    s = schluss.dropna()
    s = s[s > 0]
    if len(s) < 150 + STEIG_TAGE:
        return None
    preis = float(s.iloc[-1])
    vortag = float(s.iloc[-2])
    sma50 = float(s.iloc[-50:].mean())
    sma150 = float(s.iloc[-150:].mean())
    sma150_alt = float(s.iloc[-150 - STEIG_TAGE:-STEIG_TAGE].mean())
    sa50 = (preis / sma50 - 1) * 100
    aufwaerts = preis > sma150 and sma50 > sma150 and sma150 > sma150_alt
    if not (aufwaerts and -KNAPP <= sa50 <= 0):
        return None
    hoch52 = float(s.iloc[-252:].max())
    return {
        "name": e["name"], "s": e["yahoo"], "tv": e["tv"], "i": e["index"],
        "w": "EUR" if e["yahoo"].endswith(".DE") else "",
        "p": round(preis, 2), "c": round((preis / vortag - 1) * 100, 2),
        "sma50": round(sma50, 2), "sa50": round(sa50, 1),
        "sma150": round(sma150, 2), "sa150": round((preis / sma150 - 1) * 100, 1),
        "st150": round((sma150 / sma150_alt - 1) * 100, 1),
        "h52": round((preis / hoch52 - 1) * 100, 1),
        "d": s.index[-1].strftime("%Y-%m-%d"),
    }


def main():
    werte = bestand()
    if len(werte) < 100:
        sys.exit("Zu wenige Werte im Bestand, trend.json wird nicht überschrieben")
    symbole = [e["yahoo"] for e in werte]
    df = yf.download(symbole, period="14mo", interval="1d", auto_adjust=True,
                     group_by="ticker", threads=True, progress=False)
    treffer, geladen = [], 0
    for e in werte:
        try:
            schluss = df[e["yahoo"]]["Close"]
        except Exception:
            continue
        if schluss.dropna().empty:
            continue
        geladen += 1
        try:
            t = auswerten(e, schluss)
        except Exception as ex:
            print(f"Hinweis: {e['yahoo']}: {ex}")
            continue
        if t:
            treffer.append(t)
    print(f"{geladen} von {len(werte)} Werten geladen, {len(treffer)} Treffer")
    if geladen < len(werte) // 2:
        sys.exit("Zu wenige Kurse geladen, trend.json wird nicht überschrieben")
    treffer.sort(key=lambda t: -t["sa50"])
    aus = {"stand": datetime.now(timezone.utc).isoformat(timespec="minutes"),
           "geprueft": geladen, "quellen": QUELLEN, "fehler": FEHLER, "knapp": KNAPP, "aktien": treffer}
    with open("trend.json", "w", encoding="utf-8") as f:
        json.dump(aus, f, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
