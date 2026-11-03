# -*- coding: utf-8 -*-
"""SpeleoMap Generator - RILIEVO D'INSIEME: piu' grotte gia' elaborate (parti) + scansioni di collegamento
(strade, sentieri, tetti, pezze) -> un LAS d'insieme ordinato, le tavole PDF d'insieme e il 3D percorribile.

Parti: gruppi del progetto creati dal plugin (uno per grotta), nella loro posizione definitiva.
Collegamenti: file LAS aggiuntivi, posizionati
  - "gps":     come sono (posizione del telefono);
  - "manuale": rotazione attorno al proprio centro + spostamento E, N, Z dati dall'utente;
  - "aggancia": i valori dati sono solo indicativi; il programma affina (+/-20 gradi, qualche metro)
               sulle parti e sui collegamenti gia' posizionati (vedi smg_register).
"""
import os
import json
import struct
import datetime

import numpy as np

from qgis.core import QgsProject, QgsPointXY, QgsGeometry


# ----------------------------------------------------------------------------------------- LAS grezzi
def las_read(path):
    """Legge un LAS (formati 0-10, non compresso): intestazione+VLR, record grezzi, coordinate in metri."""
    with open(path, "rb") as fh:
        b = fh.read()
    if b[:4] != b"LASF":
        raise RuntimeError("%s non e' un file LAS (i LAZ compressi non sono supportati)." % os.path.basename(path))
    minor = b[25]
    off, = struct.unpack("<I", b[96:100])
    fmt = b[104] & 0x3F
    rl, = struct.unpack("<H", b[105:107])
    n, = struct.unpack("<I", b[107:111])
    if minor >= 4:
        n64, = struct.unpack("<Q", b[247:255])
        n = n64 or n
    sc = np.array(struct.unpack("<3d", b[131:155]))
    of = np.array(struct.unpack("<3d", b[155:179]))
    rec = np.frombuffer(b, np.uint8, count=n * rl, offset=off).reshape(n, rl).copy()
    ints = rec[:, :12].copy().view("<i4").reshape(n, 3)
    xyz = ints * sc + of
    return dict(path=path, header=b[:off], fmt=fmt, rl=rl, minor=minor, rec=rec, xyz=xyz)


def las_write(path, items):
    """items: [(las_dict, xyz_nuove, point_source_id)] con lo stesso formato di punto. Scrive un LAS unico."""
    L0 = items[0][0]
    for L, _, _ in items[1:]:
        if L["fmt"] != L0["fmt"] or L["rl"] != L0["rl"]:
            raise RuntimeError("Le scansioni hanno formati di punto diversi (%s): esportale tutte con lo stesso programma."
                               % ", ".join(os.path.basename(i[0]["path"]) for i in items))
    xyz = np.vstack([x for _, x, _ in items])
    of = np.floor(xyz.mean(axis=0))
    psid_off = 20 if L0["fmt"] >= 6 else 18
    recs = []
    for L, x, sid in items:
        r = L["rec"].copy()
        q = np.round((x - of) / 1e-4).astype("<i4")
        r[:, :12] = q.view(np.uint8).reshape(len(q), 12)
        r[:, psid_off:psid_off + 2] = np.full(len(q), sid, "<u2").view(np.uint8).reshape(len(q), 2)
        recs.append(r)
    rec = np.vstack(recs)
    n = len(rec)
    h = bytearray(L0["header"])
    struct.pack_into("<3d", h, 131, 1e-4, 1e-4, 1e-4)
    struct.pack_into("<3d", h, 155, *of)
    struct.pack_into("<I", h, 107, n if n < 2 ** 32 else 0)
    struct.pack_into("<5I", h, 111, n if n < 2 ** 32 else 0, 0, 0, 0, 0)
    struct.pack_into("<6d", h, 179, xyz[:, 0].max(), xyz[:, 0].min(), xyz[:, 1].max(), xyz[:, 1].min(),
                     xyz[:, 2].max(), xyz[:, 2].min())
    if L0["minor"] >= 4 and len(h) >= 375:
        struct.pack_into("<Q", h, 247, n)
        struct.pack_into("<15Q", h, 255, n, *([0] * 14))
        struct.pack_into("<Q", h, 235, 0)                       # niente EVLR
    with open(path, "wb") as fh:
        fh.write(bytes(h))
        fh.write(rec.tobytes())
    return n


def las_epsg(path):
    """Codice EPSG dichiarato nel LAS (chiavi GeoTIFF 3072/2048 o WKT con AUTHORITY), oppure None."""
    try:
        with open(path, "rb") as fh:
            b = fh.read(65536)
        hs, = struct.unpack("<H", b[94:96])
        nv, = struct.unpack("<I", b[100:104])
        o = hs
        for _ in range(nv):
            rid, ln = struct.unpack("<HH", b[o + 18:o + 22])
            pay = b[o + 54:o + 54 + ln]
            if rid == 34735 and ln >= 8:
                k = struct.unpack("<%dH" % (ln // 2), pay)
                for j in range(4, len(k) - 3, 4):
                    if k[j] in (3072, 2048) and k[j + 1] == 0 and k[j + 3] not in (0, 32767):
                        return int(k[j + 3])
            if rid in (2112, 2111):
                import re
                m = re.findall(r'AUTHORITY\["EPSG","(\d+)"\]', pay.decode("latin-1"))
                if m:
                    return int(m[-1])
            o += 54 + ln
    except Exception:
        return None
    return None


# ----------------------------------------------------------------------------------------- parti
def find_part_groups(prefix):
    """Gruppi del progetto creati dal plugin che contengono una pianta (le possibili parti)."""
    out = []
    for g in QgsProject.instance().layerTreeRoot().children():
        if hasattr(g, "findGroup") and g.name().startswith(prefix) and g.findGroup("Pianta") is not None:
            out.append(g.name())
    return out


def part_info(group, prefix):
    """Asse, ingressi, stazioni, cartella e LAS di una parte, dal gruppo del progetto (e dal file _parametri.json)."""
    root = QgsProject.instance().layerTreeRoot()
    g = root.findGroup(group)
    if g is None:
        raise RuntimeError("Gruppo '%s' non trovato nel progetto." % group)
    lay = {n.layer().name(): n.layer() for n in g.findGroup("Pianta").findLayers() if n.layer() is not None}
    if "asse" not in lay or "contorno" not in lay:
        raise RuntimeError("Il gruppo '%s' non ha asse e contorno: rigenera la parte con il plugin." % group)
    src = lay["contorno"].source().split("|")[0]
    folder = os.path.dirname(src)
    base = os.path.basename(src).replace("_speleomap.gpkg", "")
    meta = {}
    jp = os.path.join(folder, base + "_parametri.json")
    if os.path.exists(jp):
        try:
            with open(jp, encoding="utf-8") as fh:
                meta = json.load(fh)
        except Exception:
            meta = {}
    axis = QgsGeometry(next(lay["asse"].getFeatures()).geometry())
    stations = []
    sp = os.path.join(folder, base + "_stazioni.csv")
    if os.path.exists(sp):
        import csv
        with open(sp, encoding="utf-8") as fh:
            for r in csv.DictReader(fh, delimiter=";"):
                v = {k: (x or "").replace(",", ".") for k, x in r.items()}
                stations.append((v["stazione"], float(v["progressiva_m"]), float(v["est"]), float(v["nord"]),
                                 float(v["quota_pavimento"])))
    ents = []
    if "ingresso" in lay:
        for f in lay["ingresso"].getFeatures():
            p = f.geometry().asPoint()
            s = axis.lineLocatePoint(QgsGeometry.fromPointXY(QgsPointXY(p)))
            name = min(stations, key=lambda t: (t[2] - p.x()) ** 2 + (t[3] - p.y()) ** 2)[0] if stations else "?"
            ents.append(dict(xy=(p.x(), p.y()), s=float(s), station=name))
    return dict(group=group, title=group[len(prefix):], axis=axis, length=axis.length(), stations=stations,
                entrances=ents, folder=folder, base=base, las=meta.get("las_path", ""), meta=meta)


# ----------------------------------------------------------------------------------------- collegamenti
def apply_manual(xyz, rot, de, dn, dz):
    """Rotazione 'rot' gradi (antioraria, attorno al centro della scansione) + spostamento E, N, Z."""
    from .smg_register import yaw_matrix
    c = xyz.mean(axis=0)
    R = yaw_matrix(rot)
    return (xyz - c) @ R.T + c + np.array([de, dn, dz]), R, c


def place_connection(L, conn, targets, progress=None):
    """Posiziona un collegamento. Restituisce (xyz, descrizione della trasformazione, valori finali rot/dE/dN/dZ)."""
    mode = conn.get("mode", "aggancia")
    rot, de, dn, dz = (float(conn.get(k) or 0.0) for k in ("rot", "de", "dn", "dz"))
    if mode == "gps":
        return L["xyz"].copy(), "posizione GPS del telefono", (0.0, 0.0, 0.0, 0.0)
    if mode == "manuale":
        x, _, _ = apply_manual(L["xyz"], rot, de, dn, dz)
        return x, "manuale: rotazione %.1f gradi, spostamento E %.2f N %.2f Z %.2f m" % (rot, de, dn, dz), (rot, de, dn, dz)
    if mode == "perno":
        from .smg_aggancio import pivot
        pv = parse_en(conn.get("perno"))
        if pv is None:
            raise RuntimeError("%s: per 'Ruota attorno a un punto' scrivi le coordinate E N del perno nella colonna "
                               "'Perno'." % os.path.basename(L["path"]))
        x, vals = pivot(L["xyz"], pv[0], pv[1], rot, de, dn, dz)
        return x, ("ruotata di %.1f gradi attorno al punto E %.1f N %.1f, poi spostata E %.2f N %.2f Z %.2f m "
                   "(come posizione manuale: rotazione %.1f, E %.2f N %.2f Z %.2f)" % ((rot, pv[0], pv[1], de, dn, dz) + vals)), vals
    if mode == "monconi":
        from .smg_aggancio import match_stubs
        if targets is None or not len(targets):
            raise RuntimeError("'Monconi' serve una parte gia' sistemata a cui agganciarsi (mettila prima nella tabella).")
        prior = (rot, de, dn, float(conn.get("raggio") or 25.0)) if (rot or de or dn) else None
        r = match_stubs(L["xyz"], targets, progress=progress, prior=prior)
        if r is None:
            x, _, _ = apply_manual(L["xyz"], rot, de, dn, dz)
            return x, ("monconi: nessuna posizione con almeno due raccordi senza gallerie sovrapposte; usati i valori "
                       "indicativi rotazione %.1f, E %.2f N %.2f Z %.2f" % (rot, de, dn, dz)), (rot, de, dn, dz)
        return r[0], r[2], r[1]
    from .smg_register import register_scan
    src = L["xyz"][:: max(1, len(L["xyz"]) // 300000)]
    tgt = targets[:: max(1, len(targets) // 1500000)]
    r = register_scan(src, tgt, yaw0=rot, yaw_range=20.0, shift0=(de, dn), max_shift=float(conn.get("raggio") or 20.0),
                      progress=progress)
    if r is None:
        x, _, _ = apply_manual(L["xyz"], rot, de, dn, dz)
        return x, ("aggancio non riuscito (nessuna sovrapposizione): usati i valori indicativi rotazione %.1f, "
                   "E %.2f N %.2f Z %.2f" % (rot, de, dn, dz)), (rot, de, dn, dz)
    c = L["xyz"].mean(axis=0)
    sh = r["R"] @ c + r["t"] - c
    x = L["xyz"] @ r["R"].T + r["t"]
    return x, ("agganciato: rotazione %.1f gradi, spostamento E %.2f N %.2f Z %.2f m, %d punti in comune "
               "(scarto mediano %.2f m)" % (r["yaw"], sh[0], sh[1], sh[2], r["inliers"], r["median"])), \
        (r["yaw"], float(sh[0]), float(sh[1]), float(sh[2]))


def outline_layer(xyz, name, crs, fill, stroke):
    from qgis.core import QgsFillSymbol, QgsSingleSymbolRenderer
    from .smg_geometry import occupancy_polygon
    from .smg_layers import mem_layer, add_rows
    step = max(1, len(xyz) // 400000)
    g = occupancy_polygon(xyz[::step, 0], xyz[::step, 1], 0.4, min_count=1, min_area=2.0, keep_holes=False)
    if g is None:
        return None
    lyr = mem_layer("Polygon", name, crs, [("nome", "s")])
    add_rows(lyr, [(g, [name])])
    lyr.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple(
        {"color": fill, "outline_color": stroke, "outline_width": "0.35", "outline_style": "dash"})))
    return lyr


# ----------------------------------------------------------------------------------------- giro 3D
def tour_points(parts, step=3.0):
    """Punti di passaggio del giro: per ogni parte si entra dall'ingresso piu' vicino alla parte precedente,
    si va dritto fino al fondo piu' lontano, si torna, si percorre l'altro ramo e si esce dall'ingresso verso
    la parte successiva; nell'ultima parte, se ha piu' ingressi, si esce dall'altro e si torna a quello d'arrivo."""
    def along(geom, a, b):
        k = max(1, int(abs(b - a) / step))
        return [(geom.interpolate(float(s)).asPoint().x(), geom.interpolate(float(s)).asPoint().y())
                for s in np.linspace(a, b, k + 1)]

    def ent_point(e, geom):
        # appena fuori dall'ingresso (1,5 m dall'asse, dal lato dell'ingresso)
        p = np.array(e["xy"])
        q = geom.interpolate(e["s"]).asPoint()
        v = p - np.array([q.x(), q.y()])
        nv = np.linalg.norm(v)
        return tuple(p + (v / nv * 1.5 if nv > 1e-6 else 0))

    pts, groups = [], []
    n = len(parts)
    for i, P in enumerate(parts):
        n0 = len(pts)
        geom, Lg, ents = P["axis"], P["length"], P["entrances"]
        prev_xy = np.array(pts[-1]) if pts else None
        nxt = parts[i + 1] if i + 1 < n else None
        # ingresso d'arrivo e d'uscita
        def nearest_ent(xy):
            if not ents:
                return None
            return min(ents, key=lambda e: np.hypot(e["xy"][0] - xy[0], e["xy"][1] - xy[1]))
        exit_e = None
        if nxt is not None and ents:
            tx = [e["xy"] for e in nxt["entrances"]] or [(nxt["axis"].centroid().asPoint().x(), nxt["axis"].centroid().asPoint().y())]
            exit_e = min(ents, key=lambda e: min(np.hypot(e["xy"][0] - t[0], e["xy"][1] - t[1]) for t in tx))
        if prev_xy is not None:
            arr = nearest_ent(prev_xy)
            s_in = arr["s"] if arr else min((0.0, Lg), key=lambda s: np.hypot(*(np.array(
                [geom.interpolate(s).asPoint().x(), geom.interpolate(s).asPoint().y()]) - prev_xy)))
            if arr:
                pts.append(ent_point(arr, geom))
            far, near = (Lg, 0.0) if (Lg - s_in) >= s_in else (0.0, Lg)
            pts += along(geom, s_in, far) + along(geom, far, s_in) + along(geom, s_in, near)
            if nxt is None and arr is not None and len(ents) > 1:
                other = min((e for e in ents if e is not arr), key=lambda e: abs(e["s"] - near))
                pts += along(geom, near, other["s"]) + [ent_point(other, geom), ent_point(arr, geom)]
            elif exit_e is not None:
                pts += along(geom, near, exit_e["s"]) + [ent_point(exit_e, geom)]
        else:
            # prima parte: si parte dal fondo piu' lontano dall'uscita, si percorre tutto e si esce
            s_out = exit_e["s"] if exit_e else Lg
            start = 0.0 if abs(s_out - 0.0) > abs(Lg - s_out) else Lg
            other = Lg - start
            pts += along(geom, start, other) + along(geom, other, s_out)
            if exit_e is not None:
                pts.append(ent_point(exit_e, geom))
        groups += [i] * (len(pts) - n0)
    return pts, groups


def parse_en(txt):
    """'609527 4033729' (anche con '35 S' davanti o con virgole) -> (E, N); None se non sono coordinate."""
    import re
    if not txt:
        return None
    nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", str(txt).replace(",", " "))]
    big = [x for x in nums if abs(x) > 1000]
    return (big[-2], big[-1]) if len(big) >= 2 else None


def entrance_floor(P):
    """Quota del pavimento all'ingresso (prima stazione d'ingresso) di una parte elaborata, o None."""
    if not P.get("entrances"):
        return None
    name = P["entrances"][0]["station"]
    st = [s for s in P["stations"] if s[0] == name]
    return float(st[0][4]) if st else None


def floor_near(xyz, xy, radius=5.0, cell=0.5):
    """Pavimento della galleria vicino a un punto (E N) dato dal GPS: la nuvola entro `radius` m viene divisa in
    celle da `cell` m, per ogni cella si prende il pavimento (3o percentile), poi il livello piu' basso tra le celle
    (5o percentile), cosi' il bordo alto del fossato o la volta non vengono presi per il pavimento d'ingresso."""
    if xyz is None or xy is None:
        return None
    m = np.hypot(xyz[:, 0] - xy[0], xyz[:, 1] - xy[1]) < radius
    p = xyz[m]
    if len(p) < 50:
        return None
    ij = np.floor((p[:, :2] - np.array(xy[:2])) / cell).astype(np.int64)
    key = ij[:, 0] * 100000 + ij[:, 1]
    o = np.argsort(key)
    key, z = key[o], p[o, 2]
    _, st, cnt = np.unique(key, return_index=True, return_counts=True)
    fl = [np.percentile(z[s:s + c], 3) for s, c in zip(st, cnt) if c >= 10]
    return float(np.percentile(fl, 5)) if len(fl) >= 5 else None


def floor_axis(xyz, stations, cell=0.5, max_step=0.7):
    """Asse sul pavimento: le stazioni dell'asse automatico che sono finite su una superficie alta (sopra un muro,
    su una volta, piu' di 1,5 m sopra il pavimento piu' basso entro 5 m) vengono sostituite dal cammino sul
    pavimento tra le stazioni buone vicine (gradini sotto 'max_step' m, salite sopra il livello delle due stazioni
    molto penalizzate). Restituisce (lista di (x, y), stazioni tolte) oppure (None, motivo)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import dijkstra
    n = len(stations)
    if n < 4:
        return None, "asse troppo corto"
    sx = np.array([t[2] for t in stations], dtype=float)
    sy = np.array([t[3] for t in stations], dtype=float)
    own = np.array([t[4] for t in stations], dtype=float)
    pad = 4.0
    x0, y0 = sx.min() - pad, sy.min() - pad
    nx, ny = int((sx.max() + pad - x0) / cell) + 1, int((sy.max() + pad - y0) / cell) + 1
    if nx * ny > 3000000:
        return None, "area troppo grande"
    m = (xyz[:, 0] >= x0) & (xyz[:, 0] < x0 + nx * cell) & (xyz[:, 1] >= y0) & (xyz[:, 1] < y0 + ny * cell)
    Q = xyz[m]
    key = ((Q[:, 0] - x0) / cell).astype(np.int64) * ny + ((Q[:, 1] - y0) / cell).astype(np.int64)
    o = np.lexsort((Q[:, 2], key))
    k, z = key[o], Q[o, 2]
    u, st, cn = np.unique(k, return_index=True, return_counts=True)
    N = nx * ny
    fl = np.full(N, np.nan)
    good = cn >= 3
    fl[u[good]] = z[st[good] + (cn[good] * 0.03).astype(np.int64)]
    known = ~np.isnan(fl)

    def near_cells(x, y, r):
        cx, cy = int((x - x0) / cell), int((y - y0) / cell)
        rr = int(r / cell) + 1
        X, Y = np.meshgrid(np.arange(cx - rr, cx + rr + 1), np.arange(cy - rr, cy + rr + 1), indexing="ij")
        X, Y = X.ravel(), Y.ravel()
        v = (X >= 0) & (X < nx) & (Y >= 0) & (Y < ny)
        c = X[v] * ny + Y[v]
        c = c[known[c]]
        d = np.hypot((c // ny + 0.5) * cell + x0 - x, (c % ny + 0.5) * cell + y0 - y)
        return c[d <= r], d[d <= r]

    upper = np.zeros(n, bool)
    for i in range(n):
        c1, d1 = near_cells(sx[i], sy[i], 1.0)
        if not len(c1):                           # stazione sul vuoto: l'asse salta, si ripassa sul pavimento
            upper[i] = True
            continue
        own[i] = fl[c1[np.argmin(d1)]]            # pavimento letto dalla nuvola sotto la stazione
        c, d = near_cells(sx[i], sy[i], 5.0)
        if len(c) >= 5:
            low = np.sort(fl[c])[2]               # terza cella piu' bassa (una o due celle spurie non contano)
            upper[i] = own[i] > low + 1.5
    if not upper.any():
        return None, "asse gia' sul pavimento"
    if upper.sum() > 0.5 * n:
        return None, "troppe stazioni in alto"
    # grafo del pavimento (passi di 1 e 2 celle, gradini sotto max_step)
    idx = np.arange(N).reshape(nx, ny)
    R, C, D = [], [], []
    for di, dj in ((1, 0), (0, 1), (1, 1), (1, -1), (2, 0), (0, 2)):
        A2 = idx[0:nx - di, max(0, -dj):ny - max(0, dj)]
        B2 = idx[di:nx, max(0, dj):ny - max(0, -dj)] if dj >= 0 else idx[di:nx, 0:ny + dj]
        a, b = A2.ravel(), B2.ravel()
        ok = known[a] & known[b]
        a, b = a[ok], b[ok]
        ok = np.abs(fl[a] - fl[b]) < max_step
        R.append(a[ok]); C.append(b[ok]); D.append(np.full(ok.sum(), cell * np.hypot(di, dj)))
    R, C, D = np.concatenate(R), np.concatenate(C), np.concatenate(D)

    def snap(i):
        c, d = near_cells(sx[i], sy[i], 1.5)
        if not len(c):
            return None
        sel = np.abs(fl[c] - own[i]) < max_step
        return int(c[sel][np.argmin(d[sel])]) if sel.any() else int(c[np.argmin(d)])

    out, dropped = [], []
    i = 0
    while i < n:
        if not upper[i]:
            out.append((sx[i], sy[i]))
            i += 1
            continue
        j = i
        while j < n and upper[j]:
            j += 1
        if i == 0 or j == n:                      # tratto alto all'inizio o in fondo: si toglie
            dropped += [stations[q][0] for q in range(i, j)]
            i = j
            continue
        ca, cb = snap(i - 1), snap(j)
        path = None
        if ca is not None and cb is not None:
            zr = max(fl[ca], fl[cb]) + max_step
            pen = 1.0 + 20.0 * np.clip(fl - zr, 0, None)
            W = D * 0.5 * (pen[R] + pen[C])
            G = coo_matrix((W, (R, C)), shape=(N, N)).tocsr()
            dm, pred = dijkstra(G, directed=False, indices=ca, return_predecessors=True)
            if np.isfinite(dm[cb]):
                seg = [cb]
                while seg[-1] != ca:
                    seg.append(int(pred[seg[-1]]))
                seg = seg[::-1]
                if np.nanmax(fl[seg]) <= zr + 0.5:
                    path = seg
        if path is None:                          # nessun passaggio basso: si lascia l'asse com'era
            out += [(sx[q], sy[q]) for q in range(i, j)]
        else:
            P = np.c_[(np.array(path) // ny + 0.5) * cell + x0, (np.array(path) % ny + 0.5) * cell + y0]
            sm = np.array([P[max(0, q - 2):q + 3].mean(axis=0) for q in range(len(P))])
            out += [tuple(p) for p in sm[1:-1]]
            dropped += [stations[q][0] for q in range(i, j)]
        i = j
    if not dropped:
        return None, "nessun passaggio basso trovato"
    res = [out[0]]
    for p in out[1:]:
        if np.hypot(p[0] - res[-1][0], p[1] - res[-1][1]) >= 0.4:
            res.append(p)
    return [(float(a), float(b)) for a, b in res], dropped


def axis_station_near(axis, xy, step=2.0):
    """Nome della stazione (S0, S1, ... ogni 'step' m lungo l'asse) piu' vicina al punto xy."""
    A = np.asarray(axis, dtype=float)
    seg = np.hypot(np.diff(A[:, 0]), np.diff(A[:, 1]))
    s = np.r_[0.0, np.cumsum(seg)]
    ss = np.arange(0.0, s[-1] + 1e-6, step)
    px, py = np.interp(ss, s, A[:, 0]), np.interp(ss, s, A[:, 1])
    k = int(np.argmin(np.hypot(px - xy[0], py - xy[1])))
    return "S%d" % k, float(np.hypot(px[k] - xy[0], py[k] - xy[1]))


def axis_layer_from(axis, crs):
    from qgis.core import QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY
    lyr = QgsVectorLayer("LineString?crs=%s" % crs.authid(), "asse_sul_pavimento", "memory")
    f = QgsFeature()
    f.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y in axis]))
    lyr.dataProvider().addFeatures([f])
    lyr.updateExtents()
    return lyr


def process_new_part(p, cfg, step):
    """Elabora un LAS non ancora elaborato come grotta singola (stesse impostazioni della finestra principale),
    nella sottocartella della parte; restituisce il nome del gruppo creato nel progetto."""
    from .speleomap_engine import generate
    d = dict(cfg.get("part_defaults") or {})
    title = (p.get("title") or os.path.splitext(os.path.basename(p["las"]))[0]).strip()
    step("Elaborazione della parte %s (%s)..." % (p["label"], title))
    out = os.path.join(cfg["out_dir"], "parti", title.replace(" ", "_").replace("'", ""))
    params = dict(las_path=p["las"], crs=cfg["crs"], cave_name=title, location=d.get("location", ""),
                  authors=d.get("authors", ""), survey_date=d.get("survey_date", ""), station_step=2.0, section_step=5.0,
                  section_thickness=0.10, profile_halfwidth=0.30, out_dir=out, axis_layer=p.get("axis_layer"),
                  entrance_station=("" if parse_en(p.get("entrance")) else (p.get("entrance") or "").strip()),
                  entrance_latlon=None, entrance_alt=None, group=d.get("group", ""), logo=d.get("logo", ""),
                  drafter=d.get("drafter", ""), instruments=d.get("instruments", "Scansione LiDAR"), grade=d.get("grade", ""),
                  oggetto="Pianta - Profilo longitudinale - Sezioni",
                  terrain=dict(enable=True, basemap=d.get("basemap", "esri"), dem="auto", dem_file="", catasto=False,
                               buffer=30, profile_line=True, align=False, lidar_path=""),
                  entrance_style=None, viewer3d=False, viewer_points=1000000,
                  survey_type=p.get("kind") or "grotta", gallery_below=2.5, gallery_layer=None, access_layer=None,
                  access_quota=None, shift_mode="none", known_point=None, shift_dx=0, shift_dy=0, shift_rot=0)
    generate(params, progress=lambda m: step("Parte %s: %s" % (p["label"], m)))
    return cfg["prefix"] + title


# ----------------------------------------------------------------------------------------- tutto
def build_all(cfg, progress=None):
    """cfg: name, out_dir, crs, prefix, parts=[dict(group, label, title, las)],
    connections=[dict(las, label, mode, rot, de, dn, dz, raggio)], pdf, las, viewer3d, meta (cartiglio)."""
    from .smg_union import build_union
    from . import smg_export_3d as X
    step = progress or (lambda m: None)
    name, out_dir, crs = cfg["name"], cfg["out_dir"], cfg["crs"]
    os.makedirs(out_dir, exist_ok=True)
    safe = name.replace(" ", "_").replace("'", "")
    # controllo del sistema di riferimento dichiarato nei LAS
    want = crs.postgisSrid()
    for q in list(cfg["parts"]) + list(cfg.get("connections") or []):
        e = las_epsg(q["las"]) if q.get("las") and os.path.exists(q["las"]) else None
        if e and want and e != want and not cfg.get("crs_confirmed"):
            raise RuntimeError("Il file %s e' in EPSG:%d ma il rilievo d'insieme e' impostato su %s: scegli EPSG:%d "
                               "come sistema di riferimento." % (os.path.basename(q["las"]), e, crs.authid(), e))
    report = ["Rilievo d'insieme '%s' - SpeleoMap Generator - %s" % (name, datetime.date.today().strftime("%d/%m/%Y")), ""]
    parts, lasd, part_how = [], [], []
    for p in cfg["parts"]:
        how = "posizione definitiva"
        if not p.get("group"):                       # LAS nuovo: posizione (se chiesta), poi elaborazione
            mode = p.get("mode") or "gps"
            if mode in ("aggancia", "monconi") and not lasd:
                mode = "manuale"                     # la prima parte non ha a cosa agganciarsi
            if mode in ("aggancia", "manuale", "monconi", "perno"):
                step("Posizione della parte %s..." % p["label"])
                L0 = las_read(p["las"])
                tg = np.vstack([L["xyz"][:: max(1, len(L["xyz"]) // 800000)] for L in lasd]) if lasd else None
                x, how, _ = place_connection(L0, dict(p, mode=mode), tg, progress=step)
                title = (p.get("title") or os.path.splitext(os.path.basename(p["las"]))[0]).strip()
                d = os.path.join(out_dir, "parti", title.replace(" ", "_").replace("'", ""))
                os.makedirs(d, exist_ok=True)
                pl = os.path.join(d, title.replace(" ", "_").replace("'", "") + "_posizionato.las")
                las_write(pl, [(L0, x, 0)])
                p = dict(p, las=pl)
            else:
                how = "posizione GPS del telefono"
            p = dict(p, group=process_new_part(p, cfg, step))
        info = part_info(p["group"], cfg["prefix"])
        info.update(label=p["label"], title=p.get("title") or info["title"], las=p.get("las") or info["las"])
        if not info["las"] or not os.path.exists(info["las"]):
            raise RuntimeError("Manca il file LAS della parte %s (%s): indicalo nella tabella." % (info["label"], info["title"]))
        step("Lettura del LAS della parte %s..." % info["label"])
        lasd.append(las_read(info["las"]))
        parts.append(info)
        part_how.append(how)
    # ingressi: dati come coordinate (E N) -> stazione piu' vicina; ingressi allo stesso piano: ogni parte indicata
    # viene alzata/abbassata finche' il pavimento del suo ingresso e' alla quota comune (data dall'utente o, se manca,
    # la mediana degli ingressi), poi rielaborata con l'ingresso giusto
    lv = [x.strip().upper() for x in (cfg.get("level_parts") or "").replace(";", ",").split(",") if x.strip()]
    plan = {}
    for i, P in enumerate(parts):
        src = next((q for q in cfg["parts"] if q.get("label") == P["label"]), {})
        xy = parse_en(src.get("entrance"))
        cur = P["entrances"][0]["station"] if P["entrances"] else None
        est = cur
        if xy is not None and P["stations"]:
            s0 = min(P["stations"], key=lambda t: np.hypot(t[2] - xy[0], t[3] - xy[1]))
            est = s0[0]
            part_how[i] += "; ingresso dalle coordinate %.1f %.1f -> %s (%.1f m)" % (xy[0], xy[1], est,
                                                                                   np.hypot(s0[2] - xy[0], s0[3] - xy[1]))
        zfl = next((t[4] for t in P["stations"] if t[0] == est), None)
        ax = None
        if xy is not None:
            zc = floor_near(lasd[i]["xyz"], xy)
            if zc is not None:
                part_how[i] += "; pavimento all'ingresso %.2f m" % zc
                zfl = zc
        plan[i] = dict(src=src, est=est, cur=cur, z=zfl, dz=0.0, axis=ax)
    if lv:
        zin = {P["label"].upper(): plan[i]["z"] for i, P in enumerate(parts) if P["label"].upper() in lv}
        bad = [k for k in lv if zin.get(k) is None]
        if bad:
            raise RuntimeError("Ingressi allo stesso piano: per le parti %s manca un ingresso riconosciuto (indicalo "
                               "come stazione o coordinate E N)." % ", ".join(bad))
        zref = cfg.get("level_z")
        zref = float(zref) if zref not in (None, "") else float(np.median(list(zin.values())))
        for i, P in enumerate(parts):
            if P["label"].upper() in zin:
                plan[i]["dz"] = zref - zin[P["label"].upper()]
                part_how[i] += "; ingresso portato a %.2f m (%+.2f m)" % (zref, plan[i]["dz"])
    for i, P in enumerate(parts):
        pl_ = plan[i]
        if abs(pl_["dz"]) < 0.03 and pl_["est"] == pl_["cur"] and pl_.get("axis") is None:
            continue
        step("Parte %s: ingresso %s%s, rielaborazione..." % (P["label"], pl_["est"],
                                                           (", quota %+.2f m" % pl_["dz"]) if abs(pl_["dz"]) >= 0.03 else ""))
        L0 = lasd[i]
        pl = P["las"]
        if abs(pl_["dz"]) >= 0.03:
            d = os.path.join(out_dir, "parti", P["title"].replace(" ", "_").replace("'", ""))
            os.makedirs(d, exist_ok=True)
            pl = os.path.join(d, P["title"].replace(" ", "_").replace("'", "") + "_quota_ingresso.las")
            las_write(pl, [(L0, L0["xyz"] + np.array([0.0, 0.0, pl_["dz"]]), 0)])
        q = dict(pl_["src"], las=pl, title=P["title"], group="", entrance=pl_["est"] or "",
                 kind=pl_["src"].get("kind") or P["meta"].get("survey_type") or "grotta",
                 axis_layer=(axis_layer_from(pl_["axis"], crs) if pl_.get("axis") else None))
        grp = process_new_part(q, cfg, step)
        info = part_info(grp, cfg["prefix"])
        info.update(label=P["label"], title=P["title"], las=pl)
        parts[i] = info
        lasd[i] = las_read(pl)
    targets = np.vstack([L["xyz"][:: max(1, len(L["xyz"]) // 800000)] for L in lasd])
    # collegamenti
    conns = []
    for k, c in enumerate(cfg.get("connections") or []):
        step("Collegamento %d: %s..." % (k + 1, os.path.basename(c["las"])))
        L = las_read(c["las"])
        x, how, vals = place_connection(L, c, targets, progress=step)
        conns.append(dict(c, L=L, xyz=x, how=how, vals=vals))
        targets = np.vstack([targets, x[:: max(1, len(x) // 400000)]])
    for i, (P, L) in enumerate(zip(parts, lasd)):
        report.append("  %d = parte %s - %s: %s (%s)" % (i, P["label"], P["title"], os.path.basename(L["path"]), part_how[i]))
    for j, c in enumerate(conns):
        report.append("  %d = %s: %s - %s" % (len(parts) + j, c.get("label") or "collegamento",
                                             os.path.basename(c["las"]), c["how"]))
    res = dict(connections=[dict(las=c["las"], how=c["how"], vals=c["vals"]) for c in conns])
    las_out = os.path.join(out_dir, safe + "_tutte_le_scansioni.las")
    if cfg.get("las", True) or cfg.get("viewer3d", True):
        step("Scrittura del LAS d'insieme...")
        items = [(L, L["xyz"], i) for i, L in enumerate(lasd)] + [(c["L"], c["xyz"], len(lasd) + j) for j, c in enumerate(conns)]
        npt = las_write(las_out, items)
        res["las"] = las_out
        with open(os.path.join(out_dir, safe + "_LEGGIMI.txt"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(report[:1] + ["", "%s: %d punti; campo point_source_id:" % (os.path.basename(las_out), npt)]
                               + report[2:]) + "\n")
    # elementi di collegamento come contorni (pianta d'insieme e inquadramento)
    extras = []
    if conns:
        from .smg_layers import persist_layers
        items = []
        cols = [("200,200,200,90", "110,110,110"), ("190,150,100,110", "120,80,40"), ("160,200,160,100", "60,110,60")]
        for j, c in enumerate(conns):
            fill, stroke = cols[j % len(cols)]
            lyr = outline_layer(c["xyz"], c.get("label") or os.path.basename(c["las"]), crs, fill, stroke)
            if lyr is not None:
                items.append((lyr, "collegamento_%d" % (j + 1)))
        if items:
            L2 = persist_layers(os.path.join(out_dir, safe + "_collegamenti.gpkg"), items)
            for l in L2:
                QgsProject.instance().addMapLayer(l, False)
            extras = [dict(layer=l, legend=l.name()) for l in L2]
    res["keep"] = [e["layer"] for e in extras]
    if cfg.get("pdf", True):
        step("Tavole PDF d'insieme...")
        info = list(cfg.get("info_extra") or [])
        if conns:
            info.append("Collegamenti: " + "; ".join("%s (%s)" % (c.get("label") or os.path.basename(c["las"]),
                                                                   c["how"].split(":")[0]) for c in conns))
        u = build_union([dict(label=P["label"], title=P["title"], group=P["group"], length=P["length"]) for P in parts],
                        name, out_dir, crs, info_extra=info, meta=cfg.get("meta"),
                        layout_name="%s%s" % (cfg["prefix"], name), extras=extras)
        res["pdf"] = u["pdf"]
        res["keep"] += u.get("keep", [])
        if cfg.get("qgz", True):
            step("Progetto QGIS esportabile...")
            try:
                from .smg_qgz import export_project
                res["qgz"] = export_project(out_dir, safe, name, crs, groups=[P["group"] for P in parts],
                                            layouts=[u["layout"]], extra_layers=res["keep"], progress=step)
            except Exception as e:
                step("Progetto QGIS non creato (%s)" % e)
    if cfg.get("viewer3d", True):
        step("Percorso calpestabile e vista 3D...")
        xyz, rgb, n = X.read_las_sample(las_out, 4000000)
        pts, groups = tour_points(parts)
        tour, tbreaks = X.walk_tour(xyz, pts, groups=groups)
        if not tour:
            raise RuntimeError("Percorso 3D non calcolabile: controlla che le parti e i collegamenti si tocchino.")
        st, ents = [], []
        for P in parts:
            st += [["%s-%s" % (P["label"], s[0]), 0.0, s[2], s[3], s[4]] for s in P["stations"]]
            ents += ["%s-%s" % (P["label"], e["station"]) for e in P["entrances"]]
        if tour:
            T = np.array([[a[1], a[2], a[4]] for a in tour])
            for s in st:
                d = np.hypot(T[:, 0] - s[2], T[:, 1] - s[3]) + 0.5 * np.abs(T[:, 2] - 1.6 - s[4])
                s[1] = float(tour[int(np.argmin(d))][0])
        html = os.path.join(out_dir, safe + "_3d.html")
        X.write_viewer(html, las_out, tour or [], [tuple(s) for s in st], ents,
                       "%s (%s)" % (name, ", ".join(P["title"] for P in parts)), "%s %s" % (crs.authid(), crs.description()),
                       max_points=4000000, tour=tour, tour_breaks=tbreaks)
        res["viewer3d"] = html
        res["tour_length"] = tour[-1][0] if tour else 0.0
        if not cfg.get("las", True):
            try:
                os.remove(las_out)
            except Exception:
                pass
    res["report"] = report
    return res
