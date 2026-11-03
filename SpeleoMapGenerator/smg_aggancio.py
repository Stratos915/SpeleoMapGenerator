# -*- coding: utf-8 -*-
"""SpeleoMap Generator - aggancio delle scansioni nel rilievo d'insieme.

Due modi di mettere una scansione al suo posto (l'aggancio sul terreno in comune e' in smg_register):
  - monconi:  fa combaciare gli estremi delle gallerie della scansione con i monconi delle altre parti
              (prosecuzioni rilevate in due volte);
  - perno:    ruota attorno a un punto dato (es. un ingresso) e poi sposta.
Tutti restituiscono i valori nella stessa forma della posizione manuale: rotazione attorno al centro della
scansione + spostamento E, N, Z, cosi' si possono rileggere e correggere nella tabella.
Solo numpy e scipy (niente librerie esterne)."""

import numpy as np


def yaw_m(deg):
    a = np.radians(deg)
    return np.array([[np.cos(a), -np.sin(a), 0.0], [np.sin(a), np.cos(a), 0.0], [0.0, 0.0, 1.0]])


def as_manual(xyz, R, t):
    """Trasformazione x' = R x + t espressa come rotazione attorno al centro + spostamento."""
    c = xyz.mean(axis=0)
    sh = R @ c + t - c
    yaw = float(np.degrees(np.arctan2(R[1, 0], R[0, 0])))
    return yaw, float(sh[0]), float(sh[1]), float(sh[2])


def apply_values(xyz, rot, de, dn, dz):
    c = xyz.mean(axis=0)
    return (xyz - c) @ yaw_m(rot).T + c + np.array([de, dn, dz])


# ------------------------------------------------------------------------------------------ perno
def pivot(xyz, px, py, rot, de=0.0, dn=0.0, dz=0.0):
    """Ruota di 'rot' gradi attorno al punto (px, py), poi sposta di E, N, Z."""
    R = yaw_m(rot)
    p = np.array([px, py, 0.0])
    t = p - R @ p + np.array([de, dn, dz])
    return xyz @ R.T + t, as_manual(xyz, R, t)


# ------------------------------------------------------------------------------------------ monconi
def _thin(m):
    """Assottigliamento di Zhang-Suen (scheletro di un'immagine binaria)."""
    img = np.pad(m.astype(np.uint8), 1)
    while True:
        changed = False
        for it in (0, 1):
            P = img
            p2, p3, p4 = P[:-2, 1:-1], P[:-2, 2:], P[1:-1, 2:]
            p5, p6, p7 = P[2:, 2:], P[2:, 1:-1], P[2:, :-2]
            p8, p9 = P[1:-1, :-2], P[:-2, :-2]
            c = P[1:-1, 1:-1]
            nb = [p2, p3, p4, p5, p6, p7, p8, p9]
            B = sum(x.astype(np.int32) for x in nb)
            A = sum(((nb[i] == 0) & (nb[(i + 1) % 8] == 1)).astype(np.int32) for i in range(8))
            if it == 0:
                cond = (p2 * p4 * p6 == 0) & (p4 * p6 * p8 == 0)
            else:
                cond = (p2 * p4 * p8 == 0) & (p2 * p6 * p8 == 0)
            rm = (c == 1) & (B >= 2) & (B <= 6) & (A == 1) & cond
            if rm.any():
                img[1:-1, 1:-1][rm] = 0
                changed = True
        if not changed:
            return img[1:-1, 1:-1].astype(bool)


def footprint(xyz, cell=0.3):
    from scipy import ndimage
    x0, y0 = xyz[:, 0].min() - 2, xyz[:, 1].min() - 2
    i = ((xyz[:, 0] - x0) / cell).astype(int)
    j = ((xyz[:, 1] - y0) / cell).astype(int)
    occ = np.zeros((i.max() + 3, j.max() + 3), int)
    np.add.at(occ, (i, j), 1)
    m = ndimage.binary_closing(occ >= 3, iterations=2)
    m = ndimage.binary_fill_holes(m)
    m = ndimage.binary_opening(m, iterations=1)
    return m, x0, y0, cell


def _crossings(sk):
    """Numero di passaggi 0->1 attorno a ogni pixel dello scheletro (1 = estremo, >=3 = biforcazione)."""
    P = np.pad(sk.astype(np.int8), 1)
    nb = [P[:-2, 1:-1], P[:-2, 2:], P[1:-1, 2:], P[2:, 2:], P[2:, 1:-1], P[2:, :-2], P[1:-1, :-2], P[:-2, :-2]]
    A = sum(((nb[i] == 0) & (nb[(i + 1) % 8] == 1)).astype(np.int32) for i in range(8))
    return np.where(sk, A, 0)


def gallery_ends(xyz, cell=0.3, minbranch=2.0):
    """Estremi delle gallerie (punti finali dello scheletro della pianta) con direzione uscente e quota del pavimento."""
    from scipy.spatial import cKDTree
    import heapq
    m, x0, y0, c = footprint(xyz, cell)
    sk = _thin(m)
    cr = _crossings(sk)
    out = []
    for e in np.argwhere(cr == 1):
        # cammino sullo scheletro dall'estremo, fino a 1,5 x minbranch o alla prima biforcazione
        dist = {tuple(e): 0.0}
        pq = [(0.0, tuple(e))]
        far, hit = 0.0, False
        while pq:
            d, cur = heapq.heappop(pq)
            if d > dist.get(cur, 1e9):
                continue
            if cr[cur] >= 3 and d > 0:
                hit = True
                far = max(far, d)
                continue
            far = max(far, d)
            if d > minbranch * 1.5:
                continue
            for a in (-1, 0, 1):
                for b in (-1, 0, 1):
                    if not (a or b):
                        continue
                    p = (cur[0] + a, cur[1] + b)
                    if 0 <= p[0] < sk.shape[0] and 0 <= p[1] < sk.shape[1] and sk[p]:
                        nd = d + c * (1.4142 if a and b else 1.0)
                        if nd < dist.get(p, 1e9):
                            dist[p] = nd
                            heapq.heappush(pq, (nd, p))
        if far < minbranch:
            continue
        ring = np.array([p for p, d in dist.items() if 1.2 <= d <= 3.0])
        if len(ring) == 0:
            continue
        p0 = np.array([x0 + (e[0] + .5) * c, y0 + (e[1] + .5) * c])
        p1 = np.array([x0 + (ring[:, 0].mean() + .5) * c, y0 + (ring[:, 1].mean() + .5) * c])
        if np.linalg.norm(p0 - p1) < 0.3:
            continue
        out.append(dict(p=p0, d=(p0 - p1) / np.linalg.norm(p0 - p1)))
    kd = cKDTree(xyz[:, :2])
    for o in out:
        idx = kd.query_ball_point(o["p"], 1.5)
        o["z"] = float(np.percentile(xyz[idx, 2], 3)) if idx else np.nan
    return out, (m, x0, y0, c)


def _r2(a):
    return np.array([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])


def match_stubs(src, tgt, max_gap=3.0, progress=None, prior=None):
    """Fa combaciare gli estremi delle gallerie della scansione 'src' con i monconi di 'tgt' (parti gia' sistemate).
    Prova ogni coppia estremo/moncone (rotazione libera), tiene la posizione con piu' raccordi e senza gallerie
    sovrapposte, poi la affina sui raccordi. Restituisce (xyz, (rot, dE, dN, dZ), descrizione) oppure None.
    prior = (rot, dE, dN, raggio): se dati, si tengono solo le soluzioni con spostamento entro 'raggio' m dai valori
    indicativi e rotazione entro 30 gradi (serve quando piu' gruppi di monconi sono compatibili)."""
    src_s = src[:: max(1, len(src) // 400000)]
    tgt_s = tgt[:: max(1, len(tgt) // 800000)]
    if progress:
        progress("Monconi: estremi delle gallerie...")
    ea, (ma, ax0, ay0, c) = gallery_ends(tgt_s)
    eb, (mb, bx0, by0, _) = gallery_ends(src_s)
    if len(ea) < 2 or len(eb) < 2:
        return None
    cb = np.argwhere(mb)
    Bc = np.c_[bx0 + (cb[:, 0] + .5) * c, by0 + (cb[:, 1] + .5) * c][::3]
    cands = []
    seeds = [(ia, ib, off) for ia in range(len(ea)) for ib in range(len(eb)) for off in (-20, -10, 0, 10, 20)]
    for ia, ib, off in seeds:
        a, b = ea[ia], eb[ib]
        # la direzione di un estremo e' approssimata (+-20 gradi): si provano anche rotazioni vicine
        ang = np.arctan2(-a["d"][1], -a["d"][0]) - np.arctan2(b["d"][1], b["d"][0]) + np.radians(off)
        Rm = _r2(ang)
        t = a["p"] - Rm @ b["p"]
        pairs = []
        for jb, b2 in enumerate(eb):
            p2, d2 = Rm @ b2["p"] + t, Rm @ b2["d"]
            for ja, a2 in enumerate(ea):
                if np.hypot(*(p2 - a2["p"])) < max_gap and d2 @ a2["d"] < -0.5:
                    pairs.append((ja, jb))
        Bt = Bc @ Rm.T + t
        ii = ((Bt[:, 0] - ax0) / c).astype(int)
        jj = ((Bt[:, 1] - ay0) / c).astype(int)
        ok = (ii >= 0) & (jj >= 0) & (ii < ma.shape[0]) & (jj < ma.shape[1])
        ins = np.zeros(len(Bt), bool)
        ins[ok] = ma[ii[ok], jj[ok]]
        near = np.zeros(len(Bt), bool)
        for ja, _ in pairs:
            near |= np.hypot(*(Bt - ea[ja]["p"]).T) < 4
        coll = float((ins & ~near).mean())
        # i raccordi devono avere la stessa differenza di quota (pavimenti che si continuano)
        dzs = [ea[ja]["z"] - eb[jb]["z"] for ja, jb in pairs]
        good = [p for p, d in zip(pairs, dzs) if abs(d - np.median(dzs)) < 1.0] if pairs else []
        n = len({p[0] for p in good})
        if n >= 2 and coll <= 0.05:
            cands.append((ang, good))
    if not cands:
        return None

    def refine(ang0, pairs):
        uniq = {}
        for ja, jb in pairs:
            uniq.setdefault(jb, ja)
        pr = [(ja, jb) for jb, ja in uniq.items()]
        fin = None
        for da in np.arange(-10, 10.01, 0.5):
            Rm = _r2(ang0 + np.radians(da))
            t = np.mean([ea[ja]["p"] - Rm @ eb[jb]["p"] for ja, jb in pr], axis=0)
            err = np.sqrt(np.mean([np.sum((ea[ja]["p"] - Rm @ eb[jb]["p"] - t) ** 2) for ja, jb in pr]))
            if fin is None or err < fin[0]:
                fin = (err, ang0 + np.radians(da), t)
        return len({ja for ja, _ in pr}), fin, pr

    def iterate(ang0, pairs):
        # si rifanno le coppie con la posizione affinata (puo' aggiungersi un raccordo o cambiarne uno), 3 volte
        n, fin, pr = refine(ang0, pairs)
        for _ in range(3):
            err, ang, t = fin
            Rm = _r2(ang)
            new = []
            for jb, b2 in enumerate(eb):
                p2, d2 = Rm @ b2["p"] + t, Rm @ b2["d"]
                dd = [(np.hypot(*(p2 - a2["p"])), ja) for ja, a2 in enumerate(ea) if d2 @ a2["d"] < -0.3]
                if dd and min(dd)[0] < max_gap:
                    new.append((min(dd)[1], jb))
            if len({ja for ja, _ in new}) < 2:
                break
            n2, fin2, pr2 = refine(ang, new)
            if (n2, -fin2[0]) <= (n, -fin[0]):
                break
            n, fin, pr = n2, fin2, pr2
        return n, fin, pr

    def check(r):
        # posizione finale: gallerie sovrapposte (fuori dai raccordi) e coerenza delle quote dei raccordi
        n, (err, ang, t), pr = r
        Bt = Bc @ _r2(ang).T + t
        ii = ((Bt[:, 0] - ax0) / c).astype(int)
        jj = ((Bt[:, 1] - ay0) / c).astype(int)
        ok = (ii >= 0) & (jj >= 0) & (ii < ma.shape[0]) & (jj < ma.shape[1])
        ins = np.zeros(len(Bt), bool)
        ins[ok] = ma[ii[ok], jj[ok]]
        near = np.zeros(len(Bt), bool)
        for ja, _ in pr:
            near |= np.hypot(*(Bt - ea[ja]["p"]).T) < 4
        coll = float((ins & ~near).mean())
        dzs = np.array([ea[ja]["z"] - eb[jb]["z"] for ja, jb in pr])
        spread = float(np.ptp(dzs)) if len(dzs) else 9.0
        return coll, spread

    res = []
    for a_, p_ in cands:
        r = iterate(a_, p_)
        coll, spread = check(r)
        if coll > 0.05 or spread > 1.5:
            continue
        R3 = np.eye(3)
        R3[:2, :2] = _r2(r[1][1])
        vv = as_manual(src, R3, np.array([r[1][2][0], r[1][2][1], 0.0]))
        if prior is not None and prior[3] > 0:
            dr = (vv[0] - prior[0] + 180.0) % 360.0 - 180.0
            if abs(dr) > 30.0 or np.hypot(vv[1] - prior[1], vv[2] - prior[2]) > prior[3]:
                continue
        res.append((r, coll, spread, vv))
    if not res:
        return None
    res.sort(key=lambda q: (-q[0][0], q[0][1][0] + 2.0 * q[1] + 0.3 * q[2]))
    if progress:
        for q in res[:3]:
            progress("Monconi: %d raccordi, scarto %.2f m, sovrapposizione %.3f, quote +-%.2f m" %
                     (q[0][0], q[0][1][0], q[1], q[2]))
    n, (err, ang, t), pr = res[0][0]
    # altre soluzioni diverse (per scegliere con i valori indicativi se quella proposta non e' giusta)
    alt = []
    for q in res[1:]:
        vv = q[3]
        if all(abs((vv[0] - a[0] + 180) % 360 - 180) > 5 or np.hypot(vv[1] - a[1], vv[2] - a[2]) > 3 for a in [res[0][3]] + alt):
            alt.append(vv)
        if len(alt) >= 3:
            break
    dzs = [ea[ja]["z"] - eb[jb]["z"] for ja, jb in pr if np.isfinite(ea[ja]["z"]) and np.isfinite(eb[jb]["z"])]
    dz = float(np.median(dzs)) if dzs else 0.0
    R3 = np.eye(3)
    R3[:2, :2] = _r2(ang)
    t3 = np.array([t[0], t[1], dz])
    vals = as_manual(src, R3, t3)
    how = ("monconi: %d raccordi (scarto medio %.2f m), rotazione %.1f gradi, spostamento E %.2f N %.2f Z %.2f m"
           % (len(pr), err, vals[0], vals[1], vals[2], vals[3]))
    if alt:
        how += "; altre posizioni possibili (rot/E/N): " + "; ".join("%.0f / %.0f / %.0f" % a[:3] for a in alt)
    return src @ R3.T + t3, vals, how
