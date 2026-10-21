"""Holt aktuelle Kurse und Kennzahlen für alle Firmen aus tickers.json und schreibt prices.json.

Wird von .github/workflows/kurse.yml ausgeführt. Ausgabe:
{"stand": "...", "fund_stand": "...", "kurse": {"LLY": {...}, ...}}
Felder je Firma:
  p   letzter Kurs              c   Veränderung zum Vortag in %
  w   Währung                   s   verwendetes Yahoo-Symbol
  m   Börsenwert in Mrd. USD    yc  Kursveränderung über 1 Jahr in %
  yh  Abstand zum 52-Wochen-Hoch in % (0 = am Hoch, negativ = darunter)
  pe  KGV (letzte 12 Monate)    fpe KGV (erwartet)    n  Name bei Yahoo   (1x täglich)
"""
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

import yfinance as yf

RAW = "https://raw.githubusercontent.com/{repo}/kurse/prices.json"

with open("tickers.json", encoding="utf-8") as f:
    tickers = json.load(f)

# Ersatzsymbole, falls das Hauptsymbol bei Yahoo gerade nichts liefert
ERSATZ = {"RO.SW": ["ROG.SW", "RHHBY"], "HLUN-B.CO": ["HLUN-A.CO", "HLBBF"]}

jetzt = datetime.now(timezone.utc)

# Vorherige Datei laden, um tägliche Kennzahlen (KGV) weiterzuverwenden
alt = {}
try:
    import os
    repo = os.environ.get("GITHUB_REPOSITORY", "adelbecirovic-glitch/Pharma-")
    with urllib.request.urlopen(RAW.format(repo=repo), timeout=20) as r:
        alt = json.load(r)
except Exception as e:
    print("Keine vorherige prices.json:", e)

fund_alt = alt.get("fund_stand")
fund_neu = True
if fund_alt:
    try:
        fund_neu = jetzt - datetime.fromisoformat(fund_alt) > timedelta(hours=20)
    except Exception:
        pass
# Firmennamen fehlen noch -> Kennzahlen einmalig sofort neu laden
if any("n" not in v for v in (alt.get("kurse") or {}).values()):
    fund_neu = True
print("KGV werden neu geladen" if fund_neu else "KGV aus vorheriger Datei übernommen")


def num(x):
    try:
        x = float(x)
        return x if x == x else None  # NaN -> None
    except Exception:
        return None


def hole(symbol):
    for s in [symbol] + ERSATZ.get(symbol, []):
        try:
            fi = yf.Ticker(s).fast_info
            preis = num(fi.get("lastPrice"))
            if preis is not None:
                return s, fi, preis
        except Exception:
            pass
    raise ValueError("kein Kurs")


fx_cache = {"USD": 1.0}


def fx(waehrung):
    """Umrechnungsfaktor von der Kurswährung nach USD (GBp = Pence)."""
    if waehrung in fx_cache:
        return fx_cache[waehrung]
    basis, teiler = (waehrung, 1.0)
    if waehrung in ("GBp", "GBX"):
        basis, teiler = ("GBP", 100.0)
    if waehrung == "ILA":
        basis, teiler = ("ILS", 100.0)
    wert = None
    try:
        wert = num(yf.Ticker(f"{basis}USD=X").fast_info.get("lastPrice"))
    except Exception:
        pass
    fx_cache[waehrung] = wert / teiler if wert else None
    return fx_cache[waehrung]


kurse = {}
fehler = []
for kuerzel, symbol in tickers.items():
    try:
        symbol, fi, preis = hole(symbol)
        w = fi.get("currency") or ""
        e = {"p": round(preis, 2), "w": w, "s": symbol}
        vortag = num(fi.get("previousClose"))
        if vortag:
            e["c"] = round((preis / vortag - 1) * 100, 2)
        hoch = num(fi.get("yearHigh"))
        if hoch:
            e["yh"] = round((preis / hoch - 1) * 100, 1)
        jc = num(fi.get("yearChange"))
        if jc is not None:
            e["yc"] = round(jc * 100, 1)
        mc = num(fi.get("marketCap"))
        f = fx(w)
        if mc and f:
            e["m"] = round(mc * f / 1e9, 1)
        # KGV: einmal täglich frisch, sonst aus der letzten Datei
        vorher = (alt.get("kurse") or {}).get(kuerzel, {})
        if fund_neu:
            try:
                info = yf.Ticker(symbol).info
                pe, fpe = num(info.get("trailingPE")), num(info.get("forwardPE"))
                if pe and 0 < pe < 1000:
                    e["pe"] = round(pe, 1)
                if fpe and 0 < fpe < 1000:
                    e["fpe"] = round(fpe, 1)
                name = info.get("shortName") or info.get("longName")
                if name:
                    e["n"] = name
            except Exception as ex:
                fehler.append(f"{kuerzel}: KGV nicht geladen ({ex})")
                for k in ("pe", "fpe", "n"):
                    if k in vorher:
                        e[k] = vorher[k]
        else:
            for k in ("pe", "fpe", "n"):
                if k in vorher:
                    e[k] = vorher[k]
        kurse[kuerzel] = e
    except Exception as ex:  # einzelne Ausfälle dürfen den Lauf nicht stoppen
        fehler.append(f"{kuerzel} ({symbol}): {ex}")

print(f"{len(kurse)} von {len(tickers)} Kursen geladen")
for f_ in fehler:
    print("Hinweis:", f_)

if len(kurse) < len(tickers) // 2:
    sys.exit("Zu wenige Kurse geladen, prices.json wird nicht überschrieben")

ausgabe = {"stand": jetzt.isoformat(timespec="minutes"),
           "fund_stand": jetzt.isoformat(timespec="minutes") if fund_neu else fund_alt,
           "kurse": kurse}
with open("prices.json", "w", encoding="utf-8") as f:
    json.dump(ausgabe, f, ensure_ascii=False, separators=(",", ":"))
