# -*- coding: utf-8 -*-
"""SpeleoMap Generator - EXPORT SPELEOLOGICO: Survex (.svx) e Compass (.dat)."""
import math
import re

FT = 0.3048


def _date_parts(text):
    """'07/09/2026' o '2026-09-07' -> (anno, mese, giorno) oppure None."""
    t = (text or "").strip()
    m = re.match(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})$", t)
    if m:
        return int(m.group(3)), int(m.group(2)), int(m.group(1))
    m = re.match(r"^(\d{4})[/.\-](\d{1,2})[/.\-](\d{1,2})$", t)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    return None


def _safe(name):
    return re.sub(r"[^A-Za-z0-9_]", "_", name or "grotta").strip("_") or "grotta"


def build_shots(stations):
    """stations: lista ordinata di dict(id, e, n, z). Tiri tra stazioni consecutive con azimut di RETICOLO."""
    shots = []
    for a, b in zip(stations[:-1], stations[1:]):
        de, dn = b["e"] - a["e"], b["n"] - a["n"]
        dh = math.hypot(de, dn)
        az = (math.degrees(math.atan2(de, dn)) + 360.0) % 360.0
        if a["z"] is not None and b["z"] is not None:
            dz = b["z"] - a["z"]
            tape = math.hypot(dh, dz)
            clino = math.degrees(math.atan2(dz, dh))
        else:
            tape, clino = dh, None
        shots.append(dict(frm=a["id"], to=b["id"], tape=tape, az=az, clino=clino))
    return shots


def write_svx(path, cave_name, stations, entrance, meta):
    shots = build_shots(stations)
    ent = [s for s in stations if s["id"] == entrance][0]
    sid = _safe(cave_name).lower()
    L = []
    L.append("; %s - rilievo esportato da SpeleoMap Generator" % cave_name)
    L.append("; Azimut riferiti al NORD DI RETICOLO del sistema %s, declinazione 0:" % meta.get("crs", ""))
    L.append("; la poligonale si ricostruisce esattamente sulle coordinate del rilievo.")
    L.append("; Tiri = segmenti tra stazioni consecutive dell'asse; quote = pavimento alla stazione.")
    if meta.get("grade"):
        L.append("; Grado del rilievo: %s" % meta["grade"])
    if meta.get("instruments"):
        L.append("; Strumenti: %s" % meta["instruments"])
    L.append("*begin %s" % sid)
    L.append('*title "%s"' % cave_name.replace('"', "'"))
    dp = _date_parts(meta.get("date"))
    if dp:
        L.append("*date %04d.%02d.%02d" % dp)
    for person in [p.strip() for p in (meta.get("team") or "").split(",") if p.strip()]:
        L.append('*team "%s"' % person.replace('"', "'"))
    L.append("*units tape metres")
    L.append("*units compass degrees")
    L.append("*units clino degrees")
    L.append("*fix %s %.3f %.3f %.3f" % (ent["id"], ent["e"], ent["n"], ent["z"] if ent["z"] is not None else 0.0))
    for _e in (meta.get("entrances") or [ent["id"]]):
        L.append("*entrance %s" % _e)
    L.append("")
    L.append("*data normal from to tape compass clino")
    for s in shots:
        L.append("%-6s %-6s %8.3f %7.2f %7s" % (s["frm"], s["to"], s["tape"], s["az"],
                                                  ("%.2f" % s["clino"]) if s["clino"] is not None else "0.00"))
    if any(st.get("lrud") for st in stations):
        L.append("")
        L.append("; pareti (sinistra destra alto basso) misurate dalla nuvola di punti")
        L.append("*data passage station left right up down")
        for st in stations:
            lr = st.get("lrud") or (None, None, None, None)
            vals = ["-" if v is None else "%.2f" % v for v in lr]
            L.append("%-6s %6s %6s %6s %6s" % (st["id"], vals[0], vals[1], vals[2], vals[3]))
    L.append("*end %s" % sid)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(L) + "\n")
    return path


def write_compass_dat(path, cave_name, stations, meta):
    shots = build_shots(stations)
    lrud = {st["id"]: st.get("lrud") for st in stations}
    dp = _date_parts(meta.get("date"))
    out = []
    out.append(cave_name[:80])
    out.append("SURVEY NAME: %s" % _safe(cave_name).upper()[:12])
    date = ("%d %d %d" % (dp[1], dp[2], dp[0])) if dp else "1 1 2000"
    out.append("SURVEY DATE: %s  COMMENT:Esportato da SpeleoMap Generator - azimut di reticolo %s, declinazione 0" % (date, meta.get("crs", "")))
    out.append("SURVEY TEAM:")
    out.append((meta.get("team") or "")[:100])
    out.append("DECLINATION: 0.00  FORMAT: DMMDLRUDLADN  CORRECTIONS: 0.00 0.00 0.00")
    out.append("")
    out.append("FROM TO LENGTH BEARING INC LEFT UP DOWN RIGHT FLAGS COMMENTS")
    out.append("")

    def ft(v):
        return "%.2f" % (v / FT) if v is not None else "-9.90"
    for s in shots:
        l = lrud.get(s["frm"]) or (None, None, None, None)
        left, right, up, down = l
        inc = "%.2f" % s["clino"] if s["clino"] is not None else "0.00"
        out.append("%s %s %s %.2f %s %s %s %s %s" % (s["frm"], s["to"], ft(s["tape"]), s["az"], inc,
                                                      ft(left), ft(up), ft(down), ft(right)))
    out.append("\x0c")
    with open(path, "w", encoding="ascii", errors="replace", newline="\r\n") as fh:
        fh.write("\n".join(out) + "\n")
    return path
