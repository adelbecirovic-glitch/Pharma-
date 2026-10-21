"""Holt aktuelle Kurse für alle Firmen aus tickers.json und schreibt prices.json.

Wird von .github/workflows/kurse.yml ausgeführt. Ausgabe:
{"stand": "<ISO-Zeit UTC>", "kurse": {"LLY": {"p": 812.3, "c": 1.24, "w": "USD", "s": "LLY"}, ...}}
p = letzter Kurs, c = Veränderung zum Vortag in Prozent, w = Währung, s = Yahoo-Symbol
"""
import json
import sys
from datetime import datetime, timezone

import yfinance as yf

with open("tickers.json", encoding="utf-8") as f:
    tickers = json.load(f)

kurse = {}
fehler = []
for kuerzel, symbol in tickers.items():
    try:
        fi = yf.Ticker(symbol).fast_info
        preis = fi.get("lastPrice")
        vortag = fi.get("previousClose")
        if preis is None or preis != preis:  # None oder NaN
            raise ValueError("kein Kurs")
        eintrag = {"p": round(float(preis), 2), "w": fi.get("currency") or "", "s": symbol}
        if vortag:
            eintrag["c"] = round((float(preis) / float(vortag) - 1) * 100, 2)
        kurse[kuerzel] = eintrag
    except Exception as e:  # einzelne Ausfälle dürfen den Lauf nicht stoppen
        fehler.append(f"{kuerzel} ({symbol}): {e}")

print(f"{len(kurse)} von {len(tickers)} Kursen geladen")
for f_ in fehler:
    print("Fehler:", f_)

if len(kurse) < len(tickers) // 2:
    sys.exit("Zu wenige Kurse geladen, prices.json wird nicht überschrieben")

with open("prices.json", "w", encoding="utf-8") as f:
    json.dump({"stand": datetime.now(timezone.utc).isoformat(timespec="minutes"), "kurse": kurse},
              f, ensure_ascii=False, separators=(",", ":"))
