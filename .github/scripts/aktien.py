"""Berechnet für alle Aktien aus aktien/liste.json Allzeithoch/-tief, 52-Wochen-Spanne
sowie Unterstützungen und Widerstände und schreibt aktien.json.

Wird von .github/workflows/aktien.yml ausgeführt.

Unterstützungen/Widerstände: Wendepunkte (lokale Hochs/Tiefs) der letzten 3 Jahre auf
Tagesbasis werden gesucht und Wendepunkte mit ähnlichem Kurs (innerhalb einer Toleranz)
zu einer Zone zusammengefasst. Je öfter eine Zone berührt wurde und je jünger die
Berührung, desto stärker. Zonen unter dem Kurs = Unterstützung, darüber = Widerstand.
"""
import json
import sys
from datetime import datetime, timezone

import yfinance as yf

FENSTER = 8          # Tage links/rechts, die ein Wendepunkt überragen muss
JAHRE = 3            # Zeitraum für Wendepunkte
ANZAHL = 3           # Anzahl Unterstützungen bzw. Widerstände je Aktie


def num(x):
    try:
        x = float(x)
        return x if x == x else None
    except Exception:
        return None


def wendepunkte(hoch, tief, fenster=FENSTER):
    """Liefert Liste (index, kurs, art) mit art 'H' (Hoch) oder 'T' (Tief)."""
    n = len(hoch)
    pkt = []
    for i in range(fenster, n - fenster):
        h = hoch[i]
        if h == max(hoch[i - fenster:i + fenster + 1]):
            pkt.append((i, h, "H"))
        t = tief[i]
        if t == min(tief[i - fenster:i + fenster + 1]):
            pkt.append((i, t, "T"))
    return pkt


def zonen(pkt, n, toleranz):
    """Fasst Wendepunkte mit ähnlichem Kurs zusammen. Gibt Zonen mit Stärke zurück."""
    pkt = sorted(pkt, key=lambda p: p[1])
    gruppen = []
    for p in pkt:
        if gruppen and abs(p[1] / gruppen[-1]["mitte"] - 1) <= toleranz:
            g = gruppen[-1]
            g["pkt"].append(p)
            g["mitte"] = sum(q[1] for q in g["pkt"]) / len(g["pkt"])
        else:
            gruppen.append({"pkt": [p], "mitte": p[1]})
    aus = []
    for g in gruppen:
        idx = [q[0] for q in g["pkt"]]
        # jüngere Berührungen zählen mehr (0,5 .. 1,0)
        staerke = sum(0.5 + 0.5 * i / max(n - 1, 1) for i in idx)
        aus.append({"kurs": g["mitte"], "beruehrt": len(idx), "letzter": max(idx), "staerke": staerke})
    return aus


def berechne_level(df, preis):
    """Gibt (unterstuetzungen, widerstaende) als Listen von dicts zurück."""
    if len(df) < 2 * FENSTER + 5:
        return [], []
    hoch = [float(x) for x in df["High"]]
    tief = [float(x) for x in df["Low"]]
    schluss = [float(x) for x in df["Close"]]
    daten = [d.strftime("%Y-%m-%d") for d in df.index]
    n = len(hoch)

    # Toleranz an die Schwankung der Aktie anpassen (durchschnittliche Tagesspanne)
    spannen = [(hoch[i] - tief[i]) / schluss[i] for i in range(max(0, n - 100), n) if schluss[i]]
    tagesspanne = sum(spannen) / len(spannen) if spannen else 0.02
    toleranz = min(max(tagesspanne * 0.9, 0.012), 0.04)

    pkt = wendepunkte(hoch, tief)
    # die letzten Tage sind noch nicht bestätigt, Hoch/Tief daraus trotzdem mitnehmen
    rest = range(max(0, n - FENSTER), n)
    if rest:
        ih = max(rest, key=lambda i: hoch[i])
        it = min(rest, key=lambda i: tief[i])
        pkt += [(ih, hoch[ih], "H"), (it, tief[it], "T")]

    z = zonen(pkt, n, toleranz)
    # Einzelne Berührungen nur behalten, wenn es sonst zu wenige Zonen gibt
    def waehle(kandidaten):
        stark = [k for k in kandidaten if k["beruehrt"] >= 2]
        if len(stark) < ANZAHL:
            stark += sorted([k for k in kandidaten if k["beruehrt"] < 2],
                            key=lambda k: -k["staerke"])[:ANZAHL - len(stark)]
        return stark

    abstand = toleranz * 0.5  # Zonen direkt am Kurs weder als Stütze noch Widerstand zählen
    unten = waehle([k for k in z if k["kurs"] < preis * (1 - abstand)])
    oben = waehle([k for k in z if k["kurs"] > preis * (1 + abstand)])
    unten = sorted(unten, key=lambda k: -k["kurs"])[:ANZAHL]
    oben = sorted(oben, key=lambda k: k["kurs"])[:ANZAHL]

    def fmt(k):
        return {"k": round(k["kurs"], 2), "b": k["beruehrt"], "d": daten[k["letzter"]],
                "a": round((k["kurs"] / preis - 1) * 100, 1)}

    return [fmt(k) for k in unten], [fmt(k) for k in oben]


def bereinige(df):
    """Entfernt einzelne Fehlkurse (Tage, deren Hoch/Tief extrem vom Umfeld abweicht)."""
    df = df[(df["High"] > 0) & (df["Low"] > 0) & (df["Close"] > 0)]
    umfeld = df["Close"].rolling(11, center=True, min_periods=3).median()
    ok = (df["Low"] > umfeld * 0.5) & (df["High"] < umfeld * 2.0)
    raus = int((~ok).sum())
    if raus:
        print(f"  {raus} Fehlkurs(e) entfernt")
    return df[ok]


# Kurse in Unterwährung (Pence, Cent) in die Hauptwährung umrechnen
UNTERWAEHRUNG = {"GBp": ("GBP", 100), "GBX": ("GBP", 100), "ZAc": ("ZAR", 100), "ZAC": ("ZAR", 100), "ILA": ("ILS", 100)}


def verarbeite(eintrag):
    t = yf.Ticker(eintrag["yahoo"])
    df = t.history(period="max", interval="1d", auto_adjust=False)
    df = df.dropna(subset=["High", "Low", "Close"])
    df = bereinige(df)
    if df.empty:
        raise ValueError("keine Kursdaten")
    preis = None
    vortag = None
    waehrung = ""
    try:
        fi = t.fast_info
        preis = num(fi.get("lastPrice"))
        vortag = num(fi.get("previousClose"))
        waehrung = fi.get("currency") or ""
    except Exception:
        pass
    if preis is None:
        preis = float(df["Close"].iloc[-1])
    if vortag is None and len(df) > 1:
        vortag = float(df["Close"].iloc[-2])
    if waehrung in UNTERWAEHRUNG:
        waehrung, teiler = UNTERWAEHRUNG[waehrung]
        df = df.copy()
        for spalte in ("High", "Low", "Close"):
            df[spalte] = df[spalte] / teiler
        preis /= teiler
        if vortag:
            vortag /= teiler

    ath_i = df["High"].idxmax()
    atl_i = df["Low"].idxmin()
    ath = max(float(df["High"].max()), preis)
    atl = min(float(df["Low"].min()), preis)
    jahr = df.iloc[-252:]
    j_hoch = max(float(jahr["High"].max()), preis)
    j_tief = min(float(jahr["Low"].min()), preis)

    unten, oben = berechne_level(df.iloc[-252 * JAHRE:], preis)
    # Zu wenige Zonen in den letzten Jahren (z. B. weit unter altem Hoch): ältere Historie ergänzen
    if (len(unten) < ANZAHL or len(oben) < ANZAHL) and len(df) > 252 * JAHRE:
        u2, o2 = berechne_level(df, preis)
        if len(oben) < ANZAHL:
            oben += [x for x in o2 if all(abs(x["k"] / y["k"] - 1) > 0.02 for y in oben)]
            oben = sorted(oben, key=lambda x: x["k"])[:ANZAHL]
        if len(unten) < ANZAHL:
            unten += [x for x in u2 if all(abs(x["k"] / y["k"] - 1) > 0.02 for y in unten)]
            unten = sorted(unten, key=lambda x: -x["k"])[:ANZAHL]

    e = {
        "name": eintrag["name"], "s": eintrag["yahoo"], "tv": eintrag.get("tv", ""),
        "w": waehrung, "p": round(preis, 2),
        "ath": round(ath, 2), "ath_d": ath_i.strftime("%Y-%m-%d"),
        "atl": round(atl, 2), "atl_d": atl_i.strftime("%Y-%m-%d"),
        "seit": df.index[0].strftime("%Y-%m-%d"),
        "jh": round(j_hoch, 2), "jt": round(j_tief, 2),
        "u": unten, "r": oben,
    }
    if vortag:
        e["c"] = round((preis / vortag - 1) * 100, 2)
    return e


def main():
    with open("aktien/liste.json", encoding="utf-8") as f:
        liste = json.load(f)
    aktien, fehler = [], []
    for eintrag in liste:
        try:
            aktien.append(verarbeite(eintrag))
        except Exception as ex:
            fehler.append(f"{eintrag['name']} ({eintrag['yahoo']}): {ex}")
            aktien.append({"name": eintrag["name"], "s": eintrag["yahoo"], "tv": eintrag.get("tv", ""), "fehler": True})
    ok = sum(1 for a in aktien if not a.get("fehler"))
    print(f"{ok} von {len(liste)} Aktien berechnet")
    for f_ in fehler:
        print("Hinweis:", f_)
    if ok < max(1, len(liste) // 2):
        sys.exit("Zu wenige Aktien geladen, aktien.json wird nicht überschrieben")
    aus = {"stand": datetime.now(timezone.utc).isoformat(timespec="minutes"), "aktien": aktien}
    with open("aktien.json", "w", encoding="utf-8") as f:
        json.dump(aus, f, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
