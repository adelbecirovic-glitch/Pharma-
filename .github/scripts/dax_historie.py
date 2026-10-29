"""Lädt die Kurshistorie des DAX (^GDAXI) für die Muster-Suche im DAX-Cockpit
und schreibt dax-historie.json.

Wird von .github/workflows/dax-historie.yml ausgeführt.

Inhalt:
  tag     – Tageskerzen seit Beginn der Yahoo-Historie (ca. 1987)
  stunde  – Stundenkerzen der letzten ~2 Jahre (mehr gibt Yahoo nicht her)
Jede Reihe: t (Unix-Sekunden), o, h, l, c (auf 1 Nachkommastelle gerundet).
Die 4-Stunden-Kerzen baut die Seite selbst aus den Stundenkerzen.
"""
import json
import sys
from datetime import datetime, timezone

import yfinance as yf

SYMBOL = "^GDAXI"


def reihe(df):
    out = {"t": [], "o": [], "h": [], "l": [], "c": []}
    for ts, row in df.iterrows():
        try:
            o, h, l, c = (float(row[k]) for k in ("Open", "High", "Low", "Close"))
        except Exception:
            continue
        if any(x != x or x <= 0 for x in (o, h, l, c)):
            continue
        # Yahoo liefert bei alten Tagesdaten manchmal o=h=l=0 oder unsinnige Werte
        h = max(h, o, c)
        l = min(l, o, c)
        out["t"].append(int(ts.timestamp()))
        for k, v in (("o", o), ("h", h), ("l", l), ("c", c)):
            out[k].append(round(v, 1))
    return out


def main():
    t = yf.Ticker(SYMBOL)
    tag = t.history(period="max", interval="1d", auto_adjust=False)
    stunde = t.history(period="730d", interval="1h", auto_adjust=False)
    if tag is None or len(tag) < 1000:
        print("Zu wenige Tagesdaten erhalten:", 0 if tag is None else len(tag))
        sys.exit(1)
    daten = {
        "symbol": SYMBOL,
        "stand": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tag": reihe(tag),
        "stunde": reihe(stunde) if stunde is not None and len(stunde) else None,
    }
    with open("dax-historie.json", "w", encoding="utf-8") as f:
        json.dump(daten, f, separators=(",", ":"))
    print("Tage:", len(daten["tag"]["t"]), "Stunden:", len(daten["stunde"]["t"]) if daten["stunde"] else 0)


if __name__ == "__main__":
    main()
