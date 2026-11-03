# -*- coding: utf-8 -*-
"""SpeleoMap Generator - EXPORT 3D (Fase 5).

- write_viewer(): visualizzatore 3D in un unico file HTML (nuvola colorata, asse, stazioni,
  ingressi) con percorso della camera lungo l'asse. Funziona anche senza Internet:
  three.js viene scaricato una volta e conservato nella cartella vendor/ del plugin.
- layers_3d(): layer dell'asse e delle stazioni con la quota (Z), per la vista 3D di QGIS.
"""
import os
import json
import base64
import struct

import numpy as np

THREE_VER = "0.147.0"
_CDN = "https://cdn.jsdelivr.net/npm/three@%s/" % THREE_VER
_VENDOR = (("three.min.js", "build/three.min.js"),
           ("OrbitControls.js", "examples/js/controls/OrbitControls.js"))


def _vendor_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")


def _three_sources():
    """Codice di three.js e OrbitControls: dalla cartella vendor/, altrimenti scaricato e salvato.
    Restituisce (lista_codici, inline). Se non c'e' rete torna ai link CDN (inline=False)."""
    vd = _vendor_dir()
    codes = []
    for fn, rel in _VENDOR:
        p = os.path.join(vd, fn)
        if not os.path.exists(p) or os.path.getsize(p) < 1000:
            try:
                from qgis.core import QgsBlockingNetworkRequest
                from qgis.PyQt.QtNetwork import QNetworkRequest
                from qgis.PyQt.QtCore import QUrl
                r = QgsBlockingNetworkRequest()
                err = r.get(QNetworkRequest(QUrl(_CDN + rel)))
                data = bytes(r.reply().content())
                if err != 0 or len(data) < 1000:
                    raise RuntimeError("download fallito")
                os.makedirs(vd, exist_ok=True)
                with open(p, "wb") as fh:
                    fh.write(data)
            except Exception:
                return [_CDN + rel for _, rel in _VENDOR], False
        with open(p, "r", encoding="utf-8") as fh:
            codes.append(fh.read())
    return codes, True


def read_las_sample(path, max_points=400000, seed=0):
    """Sottoinsieme casuale (riproducibile) dei punti del LAS con X, Y, Z e colore RGB se presente."""
    with open(path, "rb") as fh:
        h = fh.read(375)
    fmt = h[104] & 0x3F
    ver_minor = h[25]
    offset = struct.unpack("<I", h[96:100])[0]
    rec_len = struct.unpack("<H", h[105:107])[0]
    n = struct.unpack("<I", h[107:111])[0]
    if ver_minor >= 4:
        n = struct.unpack("<Q", h[247:255])[0] or n
    sc = struct.unpack("<3d", h[131:155])
    of = struct.unpack("<3d", h[155:179])
    mm = np.memmap(path, dtype=np.uint8, mode="r", offset=offset, shape=(n, rec_len))
    if n > max_points:
        idx = np.sort(np.random.default_rng(seed).choice(n, max_points, replace=False))
        rec = np.asarray(mm[idx])
    else:
        rec = np.asarray(mm[:])
    del mm
    raw = np.ascontiguousarray(rec[:, :12]).view("<i4").reshape(-1, 3)
    xyz = raw * np.array(sc) + np.array(of)
    rgb_off = {2: 20, 3: 28, 5: 28, 7: 30, 8: 30, 10: 30}.get(fmt)
    rgb = None
    if rgb_off is not None and rec_len >= rgb_off + 6:
        c = np.ascontiguousarray(rec[:, rgb_off:rgb_off + 6]).view("<u2").reshape(-1, 3).astype(np.float64)
        if c.max() > 255:
            c = c / 256.0
        if c.max() > 0:
            rgb = np.clip(c, 0, 255).astype(np.uint8)
    return xyz, rgb, n


def walk_grid(xyz, x0, x1, y0, y1, cell=0.2):
    """Griglia calpestabile (passo 'cell' m) sull'area indicata: pavimento (3 percentile dei punti della cella),
    ostacolo se ci sono punti tra 0,5 e 1,0 m sopra il pavimento (pareti, massi), volta = primo punto oltre 1,0 m,
    distanza dagli ostacoli e grafo dei passaggi (no ostacoli, gradini < 0,6 m)."""
    from scipy import ndimage
    from scipy.sparse import coo_matrix
    m = (xyz[:, 0] > x0) & (xyz[:, 0] < x1) & (xyz[:, 1] > y0) & (xyz[:, 1] < y1)
    Q = xyz[m]
    nx, ny = int((x1 - x0) / cell) + 1, int((y1 - y0) / cell) + 1
    if len(Q) < 1000 or nx * ny > 4000000:
        return None
    ix = ((Q[:, 0] - x0) / cell).astype(np.int64)
    iy = ((Q[:, 1] - y0) / cell).astype(np.int64)
    key = ix * ny + iy
    o = np.lexsort((Q[:, 2], key))
    k, z = key[o], Q[o, 2]
    u, start, cnt = np.unique(k, return_index=True, return_counts=True)
    N = nx * ny
    floor = np.full(N, np.nan)
    floor[u] = z[start + (cnt * 0.03).astype(np.int64)]
    count = np.zeros(N, np.int64)
    count[u] = cnt
    rel = z - floor[k]
    obs_n = np.bincount(k[(rel > 0.5) & (rel < 1.0)], minlength=N)
    ceil = np.full(N, np.inf)
    hi = rel > 1.0
    np.minimum.at(ceil, k[hi], z[hi])
    known = count >= 3
    obst = known & (obs_n >= 3)
    hole = ~known
    dist = (ndimage.distance_transform_edt((~obst).reshape(nx, ny)) * cell).ravel()
    cost = 1.0 + 6.0 * np.exp(-dist / 0.35) + np.where(hole, 4.0, 0.0)
    fl_f = np.where(np.isnan(floor), np.nanmedian(floor), floor)
    idx = np.arange(N).reshape(nx, ny)
    rows, cols, wts = [], [], []
    for a2, b2, diag in ((idx[:-1, :], idx[1:, :], False), (idx[:, :-1], idx[:, 1:], False),
                         (idx[:-1, :-1], idx[1:, 1:], True), (idx[:-1, 1:], idx[1:, :-1], True)):
        a, b = a2.ravel(), b2.ravel()
        ok = (~obst[a]) & (~obst[b]) & ((np.abs(fl_f[a] - fl_f[b]) < 0.6) | hole[a] | hole[b])
        a, b = a[ok], b[ok]
        rows.append(a)
        cols.append(b)
        wts.append(cell * (1.4142 if diag else 1.0) * 0.5 * (cost[a] + cost[b]))
    G = coo_matrix((np.concatenate(wts), (np.concatenate(rows), np.concatenate(cols))), shape=(N, N)).tocsr()
    # grafo "solo terreno rilevato" (niente celle vuote): per passare da una parte all'altra
    # (si colmano solo i buchi piccoli, fino a ~2,4 m, tra scansioni che si toccano)
    kn = ndimage.binary_dilation(known.reshape(nx, ny), iterations=6).ravel()
    ra, ca_, wa = np.concatenate(rows), np.concatenate(cols), np.concatenate(wts)
    kk = kn[ra] & kn[ca_]
    GK = coo_matrix((wa[kk], (ra[kk], ca_[kk])), shape=(N, N)).tocsr()
    # grafo di ripiego: attraversa anche ostacoli e gradini, ma a caro prezzo (passaggi stretti o bassi
    # che la griglia vede chiusi); si usa solo se nel grafo normale due punti non sono collegati
    cost2 = cost + np.where(obst, 30.0, 0.0)
    rows, cols, wts = [], [], []
    for a2, b2, diag in ((idx[:-1, :], idx[1:, :], False), (idx[:, :-1], idx[:, 1:], False),
                         (idx[:-1, :-1], idx[1:, 1:], True), (idx[:-1, 1:], idx[1:, :-1], True)):
        a, b = a2.ravel(), b2.ravel()
        rows.append(a)
        cols.append(b)
        wts.append(cell * (1.4142 if diag else 1.0) * 0.5 * (cost2[a] + cost2[b]))
    G2 = coo_matrix((np.concatenate(wts), (np.concatenate(rows), np.concatenate(cols))), shape=(N, N)).tocsr()
    return dict(G2=G2, GK=GK, x0=x0, y0=y0, nx=nx, ny=ny, cell=cell, floor=floor, ceil=ceil, known=known, obst=obst, dist=dist, G=G)


def _cxy(g, c):
    c = np.asarray(c)
    return np.c_[(c // g["ny"] + 0.5) * g["cell"] + g["x0"], (c % g["ny"] + 0.5) * g["cell"] + g["y0"]]


def walk_snap(g, px, py, radius=1.2):
    """Cella libera migliore vicino a (px, py): vicina al punto e lontana dagli ostacoli."""
    cell, nx, ny = g["cell"], g["nx"], g["ny"]
    r = int(radius / cell) + 1
    gx, gy = np.meshgrid(np.arange(-r, r + 1), np.arange(-r, r + 1), indexing="ij")
    cx, cy = int((px - g["x0"]) / cell), int((py - g["y0"]) / cell)
    X, Y = cx + gx.ravel(), cy + gy.ravel()
    v = (X >= 0) & (X < nx) & (Y >= 0) & (Y < ny)
    c = X[v] * ny + Y[v]
    c = c[(~g["obst"][c]) & g["known"][c]]
    if len(c) == 0:
        return None
    xy = _cxy(g, c)
    dd = np.hypot(xy[:, 0] - px, xy[:, 1] - py)
    sc = dd - 0.8 * np.minimum(g["dist"][c], 0.8)
    sc[dd > radius] = 1e9
    j = int(np.argmin(sc))
    return int(c[j]) if sc[j] < 1e8 else None


def walk_route(g, pts, groups=None):
    """Cammino sulla griglia che passa per i punti 'pts' [(x, y), ...] nell'ordine dato.
    groups: per ogni punto la parte a cui appartiene; tra punti della stessa parte, se il passaggio libero manca,
    si attraversa il punto meno ostruito (grafo di ripiego); tra parti diverse non collegate da terreno rilevato
    si salta (nessun attraversamento della roccia). Restituisce (celle, indice del punto di ogni cella, salti),
    salti = indici delle celle dove il cammino riparte dopo un salto."""
    from scipy.sparse.csgraph import dijkstra
    wps = [(i, walk_snap(g, x, y) or walk_snap(g, x, y, radius=2.5)) for i, (x, y) in enumerate(pts)]
    wps = [(i, c) for i, c in wps if c is not None]
    if len(wps) < 2:
        return None, None, []
    path_c, path_w, jumps = [], [], []
    for (ia, ca), (ib, cb) in zip(wps[:-1], wps[1:]):
        if ca == cb:
            continue
        same = groups is None or groups[ia] == groups[ib]
        dmat, pred = dijkstra(g["G"] if same else g["GK"], directed=False, indices=ca, return_predecessors=True)
        if not np.isfinite(dmat[cb]) and same:
            dmat, pred = dijkstra(g["G2"], directed=False, indices=ca, return_predecessors=True)
        if not np.isfinite(dmat[cb]):
            if path_c:
                jumps.append(len(path_c))                     # salto: si riparte da cb
            path_c.append(cb)
            path_w.append(ib)
            continue
        seg = [cb]
        while seg[-1] != ca:
            seg.append(int(pred[seg[-1]]))
        seg = seg[::-1]
        if path_c:
            seg = seg[1:]
        path_c += seg
        path_w += [ia] * len(seg)
    return np.array(path_c), np.array(path_w), jumps


def walk_samples(g, path_c, s_of_cell, step=0.25, eye_h=1.6):
    """Leviga il tracciato senza farlo entrare negli ostacoli, calcola pavimento e quota d'occhio
    (1,6 m sopra il pavimento; nei passaggi piu' bassi di 1,9 m il 60% dell'altezza) e ricampiona ogni 'step'."""
    pxy = _cxy(g, path_c)
    kk = 3
    sm = np.array([pxy[max(0, i - kk):i + kk + 1].mean(axis=0) for i in range(len(pxy))])
    # dove la levigatura finirebbe su un ostacolo si torna al centro della cella del cammino
    ci = ((sm[:, 0] - g["x0"]) / g["cell"]).astype(np.int64).clip(0, g["nx"] - 1) * g["ny"] + \
         ((sm[:, 1] - g["y0"]) / g["cell"]).astype(np.int64).clip(0, g["ny"] - 1)
    bad = g["obst"][ci] | (g["dist"][ci] < 0.25)
    sm[bad] = pxy[bad]
    fl = np.array([g["floor"][c] if g["known"][c] else np.nan for c in path_c])
    ce = np.array([g["ceil"][c] if g["known"][c] else np.inf for c in path_c])
    ok = ~np.isnan(fl)
    if ok.sum() < 2:
        return None
    fl = np.interp(np.arange(len(fl)), np.where(ok)[0], fl[ok])
    n = len(fl)
    # pavimento: mediana su ~1 m (toglie i punti spuri) poi media su ~1 m (gradini addolciti, non spianati)
    flm = np.array([np.median(fl[max(0, i - 2):i + 3]) for i in range(n)])
    flm = np.array([flm[max(0, i - 2):i + 3].mean() for i in range(n)])
    # altezza dell'occhio SOPRA il pavimento, levigata a parte: cosi' resta sempre ad altezza d'uomo
    h = np.clip(ce - flm, 0.0, 10.0)
    off = np.where(h < eye_h + 0.3, np.maximum(0.6, h * 0.6), eye_h)
    off = np.array([np.median(off[max(0, i - 5):i + 6]) for i in range(n)])
    off = np.array([off[max(0, i - 3):i + 4].mean() for i in range(n)])
    eye = flm + off
    us, ui = np.unique(s_of_cell, return_index=True)
    ss = np.arange(us[0], us[-1] + 1e-6, step)
    return (ss, np.interp(ss, us, sm[ui, 0]), np.interp(ss, us, sm[ui, 1]),
            np.interp(ss, us, flm[ui]), np.interp(ss, us, eye[ui]))


def walk_tour(xyz, pts, cell=0.2, pad=5.0, groups=None):
    """Percorso unico calpestabile attraverso i punti 'pts' (anche di grotte diverse, collegate da strade o sentieri
    rilevati). groups: parte di ogni punto (vedi walk_route). Restituisce (asse per il visore
    [(s, x, y, z_linea, z_occhio)], salti [s]) con s = metri percorsi; (None, []) se non calcolabile."""
    P = np.asarray(pts, dtype=np.float64)
    g = walk_grid(xyz, P[:, 0].min() - pad, P[:, 0].max() + pad, P[:, 1].min() - pad, P[:, 1].max() + pad, cell)
    if g is None:
        return None, []
    path_c, _, jumps = walk_route(g, P, groups)
    if path_c is None:
        return None, []
    out, breaks, s0 = [], [], 0.0
    cuts = [0] + list(jumps) + [len(path_c)]
    for a, b in zip(cuts[:-1], cuts[1:]):
        pc = path_c[a:b]
        if len(pc) < 2:
            continue
        xy = _cxy(g, pc)
        s_c = s0 + np.r_[0, np.cumsum(np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1])))]
        r = walk_samples(g, pc, s_c)
        if r is None:
            continue
        ss, xs, ys, fl, eye = r
        if out:
            breaks.append(float(ss[0]))
        out += [(float(ss[i]), float(xs[i]), float(ys[i]), float(fl[i] + 0.15), float(eye[i])) for i in range(len(ss))]
        s0 = out[-1][0] + 0.5
    return (out or None), breaks


def _walk_grid_path(xyz, P, cell=0.2):
    """Percorso calpestabile lungo un tratto d'asse P (array s,x,y,zc,ze): punti di passaggio ogni ~3 m,
    progressiva riportata a quella dell'asse. Restituisce (s, x, y, eye) o None."""
    g = walk_grid(xyz, P[:, 1].min() - 3, P[:, 1].max() + 3, P[:, 2].min() - 3, P[:, 2].max() + 3, cell)
    if g is None:
        return None
    wp_s = np.arange(P[0, 0], P[-1, 0] + 1e-6, 3.0)
    if wp_s[-1] < P[-1, 0] - 0.5:
        wp_s = np.append(wp_s, P[-1, 0])
    pts = [(np.interp(v, P[:, 0], P[:, 1]), np.interp(v, P[:, 0], P[:, 2])) for v in wp_s]
    path_c, path_w, _ = walk_route(g, pts)
    if path_c is None:
        return None
    xy = _cxy(g, path_c)
    ln = np.r_[0, np.cumsum(np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1])))]
    s_c = np.empty(len(path_c))
    for w in np.unique(path_w):                    # progressiva proporzionale tra due punti di passaggio
        m = np.where(path_w == w)[0]
        sa, sb = wp_s[w], wp_s[min(w + 1, len(wp_s) - 1)]
        l0, l1 = ln[m[0]], ln[m[-1]]
        s_c[m] = sa + (sb - sa) * ((ln[m] - l0) / (l1 - l0) if l1 > l0 else 0.0)
    s_c = np.maximum.accumulate(s_c)
    r = walk_samples(g, path_c, s_c)
    if r is None:
        return None
    ss, xs, ys, fl, eye = r
    return ss, xs, ys, eye


def _center_path(t3, xyz, xs, ys, eye, r=1.2, band=0.4, clear=0.6, max_off=1.0):
    """Allontana il percorso dalle pareti all'altezza degli occhi (spinta dai punti piu' vicini di 'clear'),
    con spostamento massimo 'max_off' dall'asse, poi leviga: la camera resta nel vuoto della galleria."""
    n = len(xs)
    ox, oy = np.zeros(n), np.zeros(n)
    for i in range(n):
        px, py = xs[i], ys[i]
        for _ in range(8):
            idx = t3.query_ball_point([px, py, eye[i]], r)
            if not idx:
                break
            P = xyz[idx]
            P = P[np.abs(P[:, 2] - eye[i]) < band]
            if len(P) == 0:
                break
            dx, dy = px - P[:, 0], py - P[:, 1]
            d = np.hypot(dx, dy)
            m = d < clear
            if not m.any():
                break
            w = (clear - d[m]) / np.maximum(d[m], 0.02)
            vx, vy = float((dx[m] * w).sum()), float((dy[m] * w).sum())
            nv = np.hypot(vx, vy)
            if nv < 1e-9:
                break
            px += 0.12 * vx / nv
            py += 0.12 * vy / nv
            ddx, ddy = px - xs[i], py - ys[i]
            dd = np.hypot(ddx, ddy)
            if dd > max_off:
                px, py = xs[i] + ddx / dd * max_off, ys[i] + ddy / dd * max_off
        ox[i], oy[i] = px - xs[i], py - ys[i]
    k = 4
    ox = np.array([ox[max(0, i - k):i + k + 1].mean() for i in range(n)])
    oy = np.array([oy[max(0, i - k):i + k + 1].mean() for i in range(n)])
    return xs + ox, ys + oy


def _walk_axis(xyz, axis3d, step=0.25):
    """Ricampiona l'asse ogni 'step' m; la quota d'occhio (dal profilo) viene tenuta tra 0,9 e 1,8 m sopra
    il pavimento letto dalla nuvola e levigata; segnala i salti tra parti separate (niente volo nel vuoto)."""
    try:
        from scipy.spatial import cKDTree
    except Exception:
        return axis3d, []
    A = np.array([[a[0], a[1], a[2], a[3], (a[4] if len(a) > 4 else a[3])] for a in axis3d], dtype=np.float64)
    if len(A) < 2:
        return axis3d, []
    seg = np.hypot(np.diff(A[:, 1]), np.diff(A[:, 2]))
    jump = np.where(seg > 5.0)[0]                       # salti (parti separate): non si interpola attraverso
    pieces, start = [], 0
    for j in list(jump) + [len(A) - 1]:
        pieces.append(A[start:j + 1])
        start = j + 1
    tree = cKDTree(xyz[:, :2])
    xyz3 = xyz[::2] if len(xyz) > 1500000 else xyz
    t3 = cKDTree(xyz3)
    out, breaks = [], []
    for k, P in enumerate(pieces):
        if len(P) == 0:
            continue
        gp = None
        if len(P) > 1:
            try:
                gp = _walk_grid_path(xyz, P)
            except Exception:
                gp = None
        if gp is not None:
            ss, xs, ys, eye = gp
            zc = np.interp(ss, P[:, 0], P[:, 3])
            if k > 0 and out:
                breaks.append(float(ss[0]))
            for i in range(len(ss)):
                out.append((float(ss[i]), float(xs[i]), float(ys[i]), float(zc[i]), float(eye[i])))
            continue
        if len(P) == 1:
            ss = P[:, 0]
        else:
            ss = np.arange(P[0, 0], P[-1, 0] + 1e-6, step)
            if ss[-1] < P[-1, 0] - 1e-6:
                ss = np.append(ss, P[-1, 0])
        xs = np.interp(ss, P[:, 0], P[:, 1])
        ys = np.interp(ss, P[:, 0], P[:, 2])
        zc = np.interp(ss, P[:, 0], P[:, 3])
        prior = np.interp(ss, P[:, 0], P[:, 4]) - 1.6
        fl = []
        last = None
        for i in range(len(ss)):
            idx = tree.query_ball_point([xs[i], ys[i]], 0.35)
            if len(idx) < 15:
                idx = tree.query_ball_point([xs[i], ys[i]], 0.7)
            pr = prior[i] if last is None else 0.5 * (prior[i] + last)
            f = None
            if len(idx) >= 5:
                z = xyz[idx, 2]
                zz = z[(z > pr - 2.0) & (z < pr + 1.2)]
                if len(zz) >= 5:
                    f = float(np.percentile(zz, 5))
            if f is None:
                f = last if last is not None else prior[i]
            last = f
            fl.append(f)
        fl = np.array(fl)
        n = len(fl)
        w = 4                                             # mediana su ~2 m, poi media su ~1 m
        flm = np.array([np.median(fl[max(0, i - w):i + w + 1]) for i in range(n)])
        # quota d'occhio: quella del profilo (pavimento + 1,6 m sotto la volta), tenuta tra 0,9 e 1,8 m
        # sopra il pavimento letto dalla nuvola, cosi' non scende sotto il piano ne' vola nei vuoti
        eye = np.clip(prior + 1.6, flm + 0.9, flm + 1.8)
        eye = np.array([np.median(eye[max(0, i - w):i + w + 1]) for i in range(n)])
        eye = np.array([eye[max(0, i - 2):i + 3].mean() for i in range(n)])
        xs, ys = _center_path(t3, xyz3, xs, ys, eye)
        # passaggi bassi: se sopra il pavimento c'e' una volta sotto i 2,2 m, l'occhio va a ~60% dell'altezza
        low = np.array(eye)
        for i in range(n):
            idx = t3.query_ball_point([xs[i], ys[i], flm[i] + 1.3], 1.0)
            if not idx:
                continue
            Q = xyz3[idx]
            Q = Q[np.hypot(Q[:, 0] - xs[i], Q[:, 1] - ys[i]) < 0.3]
            up = Q[(Q[:, 2] > flm[i] + 0.4) & (Q[:, 2] < flm[i] + 2.2), 2]
            if len(up) >= 8:
                c = float(np.percentile(up, 10))
                low[i] = min(eye[i], flm[i] + max(0.5, (c - flm[i]) * 0.6))
        low = np.array([np.median(low[max(0, i - w):i + w + 1]) for i in range(n)])
        eye = np.array([low[max(0, i - 2):i + 3].mean() for i in range(n)])
        xs, ys = _center_path(t3, xyz3, xs, ys, eye, max_off=0.5)
        if k > 0 and out:
            breaks.append(float(ss[0]))
        for i in range(n):
            out.append((float(ss[i]), float(xs[i]), float(ys[i]), float(zc[i]), float(eye[i])))
    return out, breaks


def write_viewer(out_html, las_path, axis3d, stations3d, entrances, cave_name, crs_name, max_points=1000000, prep=None, tour=None, tour_breaks=None):
    """axis3d: [(s, x, y, z)], stations3d: [(id, s, x, y, z)], entrances: [id stazione].
    prep (Fase 7): correzione di posizione/quote e, per gli ipogei, il resto del rilievo mostrato attenuato."""
    xyz, rgb, n_all = read_las_sample(las_path, max_points)
    if prep is not None:
        isg = prep.get("is_gallery_raw")
        if isg is not None:
            g = isg(xyz[:, 0], xyz[:, 1])
            if rgb is not None:
                rgb = rgb.copy()
                rgb[~g] = (rgb[~g].astype(np.float64) * 0.25 + 150).astype(np.uint8)
        sh = prep["shift"]
        nx, ny = sh.xy(xyz[:, 0], xyz[:, 1])
        xyz = np.column_stack([nx, ny, xyz[:, 2] + sh.dz])
    if tour:                                       # percorso gia' calcolato (es. insieme di piu' grotte)
        axis3d, breaks = tour, list(tour_breaks or [])
    else:
        axis3d, breaks = _walk_axis(xyz, axis3d)
    ax = np.array([[a[1], a[2], a[3]] for a in axis3d], dtype=np.float64)
    nvox = len(np.unique(np.floor(xyz / 0.10).astype(np.int64), axis=0))
    spacing = float(np.clip(np.sqrt(nvox * 0.01 / max(1, len(xyz))), 0.01, 0.20))
    o = np.array([ax[0, 0], ax[0, 1], ax[0, 2]])            # origine locale = inizio dell'asse
    loc = xyz - o
    mn = loc.min(axis=0)
    span = loc.max(axis=0) - mn
    span[span == 0] = 1.0
    q = np.round((loc - mn) / span * 65535.0).astype("<u2")
    meta = dict(
        name=cave_name, crs=crs_name, n_all=int(n_all), n=int(len(q)),
        origin=[float(v) for v in o], qmin=[float(v) for v in mn], qspan=[float(v) for v in span],
        rgb=rgb is not None, spacing=round(spacing, 4),
        axis=[[round(float(a[0]), 3), round(float(a[1] - o[0]), 3), round(float(a[2] - o[1]), 3), round(float(a[3] - o[2]), 3),
               round(float((a[4] if len(a) > 4 else a[3]) - o[2]), 3)] for a in axis3d],
        stations=[[str(s[0]), round(float(s[1]), 2), round(float(s[2] - o[0]), 3), round(float(s[3] - o[1]), 3),
                   round(float(s[4] - o[2]), 3)] for s in stations3d],
        entrances=[str(e) for e in entrances],
        breaks=[round(b, 3) for b in breaks],
    )
    pos_b64 = base64.b64encode(q.tobytes()).decode("ascii")
    col_b64 = base64.b64encode(rgb.tobytes()).decode("ascii") if rgb is not None else ""
    codes, inline = _three_sources()
    if inline:
        libs = "\n".join("<script>\n%s\n</script>" % c.replace("</script", "<\\/script") for c in codes)
    else:
        libs = "\n".join('<script src="%s"></script>' % u for u in codes)
    html = (_HTML.replace("__TITLE__", _esc(cave_name))
            .replace("__LIBS__", libs)
            .replace("__META__", json.dumps(meta, ensure_ascii=True))
            .replace("__POS__", pos_b64)
            .replace("__COL__", col_b64))
    with open(out_html, "w", encoding="utf-8") as fh:
        fh.write(html)
    return dict(path=out_html, points=int(len(q)), points_total=int(n_all), rgb=rgb is not None, offline=inline)


def _esc(t):
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def layers_3d(crs, axis3d, stations3d):
    """Layer in memoria con Z: asse (LineStringZ) e stazioni (PointZ), con simboli 3D se disponibili."""
    from qgis.core import QgsVectorLayer, QgsFeature, QgsGeometry, QgsPoint, QgsField, QgsLineString
    from qgis.PyQt.QtCore import QVariant
    a = QgsVectorLayer("LineStringZ", "asse 3D", "memory")
    a.setCrs(crs)
    a.dataProvider().addAttributes([QgsField("lunghezza", QVariant.Double)])
    a.updateFields()
    f = QgsFeature(a.fields())
    f.setGeometry(QgsGeometry(QgsLineString([QgsPoint(p[1], p[2], p[3]) for p in axis3d])))
    f.setAttributes([float(axis3d[-1][0])])
    a.dataProvider().addFeatures([f])
    a.updateExtents()
    s = QgsVectorLayer("PointZ", "stazioni 3D", "memory")
    s.setCrs(crs)
    s.dataProvider().addAttributes([QgsField("station_id", QVariant.String), QgsField("s_m", QVariant.Double),
                                    QgsField("z", QVariant.Double)])
    s.updateFields()
    fs = []
    for st in stations3d:
        g = QgsFeature(s.fields())
        g.setGeometry(QgsGeometry(QgsPoint(st[2], st[3], st[4])))
        g.setAttributes([str(st[0]), float(st[1]), float(st[4])])
        fs.append(g)
    s.dataProvider().addFeatures(fs)
    s.updateExtents()
    return a, s


def style_3d(axis_l, st_l):
    """Simboli 3D (linea rossa, sfere verdi). Se il modulo 3D di QGIS manca non fa nulla."""
    try:
        from qgis.core import Qgis
        from qgis._3d import (QgsLine3DSymbol, QgsPoint3DSymbol, QgsVectorLayer3DRenderer, QgsPhongMaterialSettings)
        from qgis.PyQt.QtGui import QColor
        m = QgsPhongMaterialSettings()
        m.setAmbient(QColor(220, 0, 0))
        m.setDiffuse(QColor(220, 0, 0))
        ls = QgsLine3DSymbol()
        ls.setAltitudeClamping(Qgis.AltitudeClamping.Absolute)
        ls.setRenderAsSimpleLines(True)
        ls.setWidth(3.0)
        ls.setMaterialSettings(m)
        axis_l.setRenderer3D(QgsVectorLayer3DRenderer(ls))
        m2 = QgsPhongMaterialSettings()
        m2.setAmbient(QColor(0, 150, 0))
        m2.setDiffuse(QColor(0, 150, 0))
        ps = QgsPoint3DSymbol()
        ps.setAltitudeClamping(Qgis.AltitudeClamping.Absolute)
        ps.setShape(Qgis.Point3DShape.Sphere)
        ps.setShapeProperties({"radius": 0.15})
        ps.setMaterialSettings(m2)
        st_l.setRenderer3D(QgsVectorLayer3DRenderer(ps))
        return True
    except Exception:
        return False


_HTML = r"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ - vista 3D</title>
<style>
  html,body{margin:0;height:100%;background:#101214;color:#e8e8e8;font:13px/1.35 -apple-system,Helvetica,Arial,sans-serif;overflow:hidden}
  #c{position:fixed;left:0;top:0;width:100vw;height:100vh;display:block}
  #panel{position:fixed;left:12px;top:12px;width:290px;background:rgba(20,22,26,.88);border:1px solid #333;border-radius:8px;padding:12px 14px}
  #panel h1{font-size:16px;margin:0 0 2px}
  #panel .sub{color:#9aa;font-size:11px;margin-bottom:10px}
  .row{display:flex;align-items:center;gap:8px;margin:7px 0}
  .row label{width:84px;color:#bbb}
  .row input[type=range]{flex:1}
  button{background:#2b6cb0;color:#fff;border:0;border-radius:5px;padding:6px 10px;font-size:13px;cursor:pointer}
  button.sec{background:#3a3f47}
  button.on{background:#c05621}
  #info{position:fixed;left:50%;bottom:14px;transform:translateX(-50%);background:rgba(20,22,26,.88);border:1px solid #333;border-radius:6px;padding:6px 12px;white-space:nowrap}
  #help{color:#889;font-size:11px;margin-top:8px}
  #err{position:fixed;inset:0;display:none;align-items:center;justify-content:center;text-align:center;padding:30px;color:#fbb}
</style>
</head>
<body>
<canvas id="c"></canvas>
<div id="panel">
  <h1>__TITLE__</h1>
  <div class="sub" id="sub"></div>
  <div class="row"><button id="bWalk">&#9654; Percorri l'asse</button><button id="bFree" class="sec">Vista libera</button></div>
  <div class="row"><label>Progressiva</label><input id="sPos" type="range" min="0" max="1000" value="0"></div>
  <div class="row"><label>Velocit&agrave;</label><input id="sSpd" type="range" min="1" max="30" value="6"></div>
  <div class="row"><label>Punti</label><input id="sSize" type="range" min="3" max="40" value="10"></div>
  <div class="row"><label>Colori</label><button id="bCol" class="sec">Quota</button><button id="bAx" class="sec on">Asse</button><button id="bLab" class="sec on">Stazioni</button></div>
  <div id="help">Vista libera: trascina per ruotare, rotella per lo zoom, tasto destro per spostare.<br>Percorso: spazio = avvia/ferma, frecce = avanti/indietro, trascina per guardarti intorno.</div>
</div>
<div id="info"></div>
<div id="err"></div>
__LIBS__
<script>
"use strict";
const META = __META__;
const POS_B64 = "__POS__";
const COL_B64 = "__COL__";
function b64(s){const b=atob(s),u=new Uint8Array(b.length);for(let i=0;i<b.length;i++)u[i]=b.charCodeAt(i);return u;}
function fail(m){const e=document.getElementById('err');e.style.display='flex';e.textContent=m;}
if(typeof THREE==='undefined'){fail("Impossibile caricare three.js: apri il file con una connessione a Internet attiva.");}
else{
// geo (x est, y nord, z quota) -> three (x, y alto, z sud)
const G=(x,y,z)=>new THREE.Vector3(x,z,-y);
const renderer=new THREE.WebGLRenderer({canvas:document.getElementById('c'),antialias:true});
renderer.setPixelRatio(Math.min(2,window.devicePixelRatio||1));
const scene=new THREE.Scene();scene.background=new THREE.Color(0x101214);
const camera=new THREE.PerspectiveCamera(60,1,0.05,2000);
// nuvola di punti
const q=new Uint16Array(b64(POS_B64).buffer);const n=META.n;
const pos=new Float32Array(n*3);let zmin=1e9,zmax=-1e9;
for(let i=0;i<n;i++){
  const x=META.qmin[0]+q[3*i]/65535*META.qspan[0], y=META.qmin[1]+q[3*i+1]/65535*META.qspan[1], z=META.qmin[2]+q[3*i+2]/65535*META.qspan[2];
  pos[3*i]=x;pos[3*i+1]=z;pos[3*i+2]=-y;if(z<zmin)zmin=z;if(z>zmax)zmax=z;}
const geo=new THREE.BufferGeometry();geo.setAttribute('position',new THREE.BufferAttribute(pos,3));
const colRGB=new Float32Array(n*3),colZ=new Float32Array(n*3);
const rgb=META.rgb?b64(COL_B64):null;
function ramp(t){const s=[[0.17,0.11,0.55],[0.0,0.55,0.75],[0.35,0.78,0.3],[0.98,0.85,0.2],[0.85,0.2,0.15]];
  t=Math.max(0,Math.min(1,t))*(s.length-1);const k=Math.min(s.length-2,Math.floor(t)),f=t-k;return [0,1,2].map(j=>s[k][j]*(1-f)+s[k+1][j]*f);}
for(let i=0;i<n;i++){const c=ramp((pos[3*i+1]-zmin)/((zmax-zmin)||1));colZ[3*i]=c[0];colZ[3*i+1]=c[1];colZ[3*i+2]=c[2];
  if(rgb){colRGB[3*i]=rgb[3*i]/255;colRGB[3*i+1]=rgb[3*i+1]/255;colRGB[3*i+2]=rgb[3*i+2]/255;}}
let useRGB=!!rgb;
geo.setAttribute('color',new THREE.BufferAttribute(useRGB?colRGB:colZ,3));
const SP=META.spacing||0.04;
const mat=new THREE.PointsMaterial({size:SP*1.2,vertexColors:true,sizeAttenuation:true});
scene.add(new THREE.Points(geo,mat));
// asse
const A=META.axis;const L=A[A.length-1][0];
const axPts=A.map(a=>G(a[1],a[2],a[3]));
const segP=[];for(let i=1;i<axPts.length;i++){if(axPts[i].distanceTo(axPts[i-1])<5){segP.push(axPts[i-1],axPts[i]);}}
const axLine=new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(segP),new THREE.LineBasicMaterial({color:0xff3030}));
const BR=META.breaks||[];let prevS=0;
function nextBreak(s){for(const b of BR){if(b>s+1e-6)return b;}return 1e9;}
function prevBreak(s){let r=-1e9;for(const b of BR){if(b<=s+1e-6)r=b;}return r;}
scene.add(axLine);
// etichette
function label(txt,color,h){const cv=document.createElement('canvas');const ctx=cv.getContext('2d');ctx.font='bold 44px Helvetica,Arial';
  const w=Math.ceil(ctx.measureText(txt).width)+24;cv.width=w;cv.height=64;ctx.font='bold 44px Helvetica,Arial';
  ctx.fillStyle='rgba(0,0,0,.55)';ctx.fillRect(0,0,w,64);ctx.fillStyle=color;ctx.textBaseline='middle';ctx.fillText(txt,12,34);
  const sp=new THREE.Sprite(new THREE.SpriteMaterial({map:new THREE.CanvasTexture(cv),depthTest:false,transparent:true}));
  sp.scale.set(h*w/64,h,1);sp.renderOrder=10;return sp;}
const labs=new THREE.Group();scene.add(labs);
const entSet=new Set(META.entrances);
for(const s of META.stations){
  const p=G(s[2],s[3],s[4]);const ent=entSet.has(s[0]);
  const m=new THREE.Mesh(new THREE.SphereGeometry(ent?0.18:0.08,12,8),new THREE.MeshBasicMaterial({color:ent?0x30e060:0xffd040}));
  m.position.copy(p);labs.add(m);
  const sp=label(ent?('Ingresso '+s[0]):s[0],ent?'#5cff8a':'#ffe08a',ent?0.35:0.18);sp.position.copy(p).add(new THREE.Vector3(0,ent?0.7:0.3,0));labs.add(sp);
  m.userData.near=true;sp.userData.near=true;}
// interpolazione lungo l'asse
function at(s,k){k=k||4;s=Math.max(0,Math.min(L,s));let lo=0,hi=A.length-1;while(hi-lo>1){const m=(lo+hi)>>1;if(A[m][0]<=s)lo=m;else hi=m;}
  const a=A[lo],b=A[hi],t=(b[0]-a[0])>0?(s-a[0])/(b[0]-a[0]):0;return G(a[1]+(b[1]-a[1])*t,a[2]+(b[2]-a[2])*t,a[k]+(b[k]-a[k])*t);}
function zAt(s){s=Math.max(0,Math.min(L,s));let lo=0,hi=A.length-1;while(hi-lo>1){const m=(lo+hi)>>1;if(A[m][0]<=s)lo=m;else hi=m;}
  const a=A[lo],b=A[hi],t=(b[0]-a[0])>0?(s-a[0])/(b[0]-a[0]):0;return a[3]+(b[3]-a[3])*t;}
function nearestStation(s){let best=null,d=1e9;for(const st of META.stations){const dd=Math.abs(st[1]-s);if(dd<d){d=dd;best=st;}}return d<6?best:null;}
// controlli
const ctl=new THREE.OrbitControls(camera,renderer.domElement);ctl.enableDamping=true;
const bb=new THREE.Box3().setFromBufferAttribute(geo.attributes.position);const ctr=bb.getCenter(new THREE.Vector3());const sz=bb.getSize(new THREE.Vector3()).length();
function freeView(){mode='free';if(typeof applySize==='function')applySize();ctl.enabled=true;ctl.target.copy(ctr);camera.position.copy(ctr).add(new THREE.Vector3(sz*0.45,sz*0.55,sz*0.45));ctl.update();setBtns();}
let mode='free',playing=false,s=0,yaw=0,pitch=0,camPos=null,camLook=null;
const elInfo=document.getElementById('info'),sPos=document.getElementById('sPos'),sSpd=document.getElementById('sSpd');
function setBtns(){document.getElementById('bWalk').innerHTML=(mode==='walk'&&playing)?'&#10074;&#10074; Pausa':'&#9654; Percorri l\'asse';
  document.getElementById('bFree').className=mode==='free'?'on':'sec';}
function startWalk(){if(mode!=='walk'){mode='walk';ctl.enabled=false;camPos=null;if(s>=L-0.01)s=0;applySize();}playing=!playing;setBtns();}
document.getElementById('bWalk').onclick=startWalk;
document.getElementById('bFree').onclick=()=>{playing=false;freeView();};
sPos.oninput=()=>{s=sPos.value/1000*L;if(mode!=='walk'){mode='walk';ctl.enabled=false;camPos=null;applySize();}setBtns();};
function applySize(){const f=document.getElementById('sSize').value/10;mat.size=SP*f*(mode==='walk'?2.0:1.2);}
document.getElementById('sSize').oninput=applySize;
document.getElementById('bCol').onclick=e=>{if(!rgb)return;useRGB=!useRGB;geo.setAttribute('color',new THREE.BufferAttribute(useRGB?colRGB:colZ,3));e.target.textContent=useRGB?'Quota':'Colori reali';};
document.getElementById('bAx').onclick=e=>{axLine.visible=!axLine.visible;e.target.className=axLine.visible?'sec on':'sec';};
document.getElementById('bLab').onclick=e=>{labs.visible=!labs.visible;e.target.className=labs.visible?'sec on':'sec';};
if(!rgb){document.getElementById('bCol').textContent='Solo quota';}
let drag=null;renderer.domElement.addEventListener('pointerdown',e=>{if(mode==='walk')drag=[e.clientX,e.clientY,yaw,pitch];});
window.addEventListener('pointerup',()=>drag=null);
window.addEventListener('pointermove',e=>{if(drag){yaw=drag[2]-(e.clientX-drag[0])*0.005;pitch=Math.max(-1.2,Math.min(1.2,drag[3]-(e.clientY-drag[1])*0.005));}});
window.addEventListener('keydown',e=>{if(e.code==='Space'){e.preventDefault();startWalk();}
  if(e.code==='ArrowUp'||e.code==='ArrowRight'){if(mode!=='walk'){mode='walk';ctl.enabled=false;camPos=null;}s=Math.min(L,s+1);setBtns();}
  if(e.code==='ArrowDown'||e.code==='ArrowLeft'){if(mode!=='walk'){mode='walk';ctl.enabled=false;camPos=null;}s=Math.max(0,s-1);setBtns();}});
document.getElementById('sub').textContent=META.n.toLocaleString('it-IT')+' di '+META.n_all.toLocaleString('it-IT')+' punti - asse '+L.toFixed(1)+' m - '+META.crs;
function resize(){const w=window.innerWidth,h=window.innerHeight;renderer.setSize(w,h,true);camera.aspect=w/h;camera.updateProjectionMatrix();}
window.addEventListener('resize',resize);resize();freeView();
let last=performance.now();
function tick(now){const dt=Math.min(0.1,(now-last)/1000);last=now;
  if(mode==='walk'){
    if(playing){s+=dt*sSpd.value/4;if(s>=L){s=L;playing=false;setBtns();}}
    for(const b of BR){if(s>b-0.5&&s<b){s=(prevS<=s)?b:b-0.5;}}for(const b of BR){if((prevS<b)!==(s<b)){camPos=null;}}prevS=s;
    const nb=nextBreak(s),pb=prevBreak(s);const p=at(s),ahead=at(Math.min(L,s+2.5,nb-0.05)),back=at(Math.max(0,s-2.5,pb));
    let dir=(s<L-0.05&&nb-s>0.3)?ahead.clone().sub(p):p.clone().sub(back);dir.y*=0.5;if(dir.lengthSq()<1e-6)dir.set(1,0,0);dir.normalize();
    const flat=new THREE.Vector3(dir.x,0,dir.z).normalize();const side=new THREE.Vector3().crossVectors(flat,new THREE.Vector3(0,1,0));
    const d2=flat.clone().multiplyScalar(Math.cos(yaw)).add(side.clone().multiplyScalar(-Math.sin(yaw)));
    const look=p.clone().add(new THREE.Vector3(d2.x*Math.cos(pitch),dir.y+Math.sin(pitch),d2.z*Math.cos(pitch)).multiplyScalar(3));
    if(!camPos){camPos=p.clone();camLook=look.clone();}camPos.lerp(p,0.3);camLook.lerp(look,0.2);
    camera.position.copy(camPos);camera.lookAt(camLook);
    for(const o of labs.children){{const dd=o.position.distanceTo(camPos);o.visible=dd<10&&dd>1.2;}}
    sPos.value=Math.round(s/L*1000);
    const st=nearestStation(s);const zz=zAt(s)+META.origin[2];
    elInfo.textContent='Progressiva '+s.toFixed(1)+' m  -  quota asse '+zz.toFixed(1)+' m'+(st?'  -  vicino a '+st[0]:'  -  tratto di collegamento');
  }else{for(const o of labs.children)o.visible=true;ctl.update();elInfo.textContent='Vista libera  -  premi "Percorri l\'asse" per entrare dalla stazione iniziale';}
  renderer.render(scene,camera);requestAnimationFrame(tick);}
requestAnimationFrame(tick);
}
</script>
</body>
</html>
"""
