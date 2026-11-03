# -*- coding: utf-8 -*-
"""SpeleoMap Generator - REGISTRAZIONE di una scansione di collegamento (strada, sentiero, pezza) sulle parti gia'
elaborate. Solo rotazione attorno alla verticale + traslazione (le scansioni del telefono sono gia' a piombo).

1. ricerca grossolana: per ogni rotazione (passo 'yaw_step') la traslazione che fa sovrapporre di piu' le impronte
   in pianta (correlazione su griglia, FFT);
2. affinamento dei candidati migliori punto-piano (ICP) anche in quota;
3. vince il candidato con piu' punti a meno di 20 cm dalle parti.
"""
import numpy as np


def yaw_matrix(deg):
    c, s = np.cos(np.radians(deg)), np.sin(np.radians(deg))
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def voxel(p, s):
    k = np.floor(p / s).astype(np.int64)
    _, i = np.unique(k, axis=0, return_index=True)
    return p[i]


def normals(p, k=12):
    from scipy.spatial import cKDTree
    _, i = cKDTree(p).query(p, k)
    q = p[i] - p[i].mean(1, keepdims=True)
    w, v = np.linalg.eigh(np.einsum("nki,nkj->nij", q, q))
    return v[:, :, 0]


def floor_grid(p, x0, y0, nx, ny, cell):
    """Quota minima (pavimento) per cella; NaN dove non ci sono punti."""
    ix = ((p[:, 0] - x0) / cell).astype(np.int64)
    iy = ((p[:, 1] - y0) / cell).astype(np.int64)
    m = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
    F = np.full(nx * ny, np.inf)
    np.minimum.at(F, ix[m] * ny + iy[m], p[m, 2])
    F[np.isinf(F)] = np.nan
    return F.reshape(nx, ny)


def floor_score(FT, x0, y0, cell, pts):
    """Coerenza dei pavimenti dove la scansione cade sopra le parti: celle concordi - 1,5 x celle in conflitto
    (dopo aver tolto lo scarto di quota mediano). Restituisce (punteggio, dz, concordi, conflitti)."""
    nx, ny = FT.shape
    FS = floor_grid(pts, x0, y0, nx, ny, cell)
    m = ~np.isnan(FS) & ~np.isnan(FT)
    if m.sum() < 10:
        return -1e9, 0.0, 0, 0
    d = FT[m] - FS[m]
    dz = float(np.median(d))
    ok = np.abs(d - dz) < 0.3
    return float(ok.sum() - 1.5 * (~ok).sum()), dz, int(ok.sum()), int((~ok).sum())


def icp_yaw(a, na, b, yaw, t, it=40, maxd=1.0):
    """Punto-piano con 4 gradi di liberta' (rotazione verticale + traslazione 3D)."""
    from scipy.spatial import cKDTree
    T = cKDTree(a)
    t = np.array(t, dtype=np.float64)
    for k in range(it):
        R = yaw_matrix(yaw)
        bb = b @ R.T + t
        d, i = T.query(bb)
        m = d < maxd
        if m.sum() < 20:
            break
        if k > 8:
            m &= d < max(0.12, np.percentile(d[m], 70))
        p, q, n = bb[m], a[i[m]], na[i[m]]
        A = np.c_[n[:, 0] * -p[:, 1] + n[:, 1] * p[:, 0], n]
        r = ((q - p) * n).sum(1)
        x = np.linalg.lstsq(A, r, rcond=None)[0]
        t = yaw_matrix(np.degrees(x[0])) @ t + x[1:]
        yaw += np.degrees(x[0])
    d, _ = T.query(b @ yaw_matrix(yaw).T + t)
    return yaw, t, int((d < 0.2).sum()), float(np.median(d[d < 1.0])) if (d < 1.0).any() else 9.0


def register_scan(src, tgt, yaw_step=5.0, cell=0.5, top=8, max_shift=30.0, progress=None,
                  yaw0=0.0, yaw_range=180.0, shift0=(0.0, 0.0)):
    """src, tgt: array Nx3 (metri, stesso sistema). Restituisce dict(R, t, yaw, shift, inliers, frac, median)
    con x' = R x + t; None se non c'e' nessuna sovrapposizione credibile.
    max_shift: di quanto puo' spostarsi al massimo il centro della scansione rispetto alla posizione GPS (m):
    la rotazione invece e' libera (la bussola del telefono sbaglia spesso anche di molto)."""
    from scipy.signal import fftconvolve
    O = tgt.mean(0)
    a = voxel(tgt - O, 0.12)
    b0 = voxel(src - O, 0.25)
    if len(a) < 100 or len(b0) < 100:
        return None
    na = normals(a)
    tx0, ty0 = a[:, 0].min(), a[:, 1].min()
    nxT = int((a[:, 0].max() - tx0) / cell) + 1
    nyT = int((a[:, 1].max() - ty0) / cell) + 1
    G = np.zeros((nxT, nyT), np.float32)
    G[((a[:, 0] - tx0) / cell).astype(int), ((a[:, 1] - ty0) / cell).astype(int)] = 1.0
    FT = floor_grid(a, tx0, ty0, nxT, nyT, cell)
    cands = []
    yaws = np.arange(-180.0, 180.0, yaw_step) if yaw_range >= 180 else \
        np.arange(yaw0 - yaw_range, yaw0 + yaw_range + 1e-6, yaw_step)
    for yaw in yaws:
        bb = b0 @ yaw_matrix(yaw).T
        sx0, sy0 = bb[:, 0].min(), bb[:, 1].min()
        nxS = int((bb[:, 0].max() - sx0) / cell) + 1
        nyS = int((bb[:, 1].max() - sy0) / cell) + 1
        S = np.zeros((nxS, nyS), np.float32)
        S[((bb[:, 0] - sx0) / cell).astype(int), ((bb[:, 1] - sy0) / cell).astype(int)] = 1.0
        C = fftconvolve(G, S[::-1, ::-1], mode="full")
        # vincolo GPS: il centro della scansione (c -> R c + t) non si sposta piu' di max_shift
        c = b0.mean(0)
        ex = c[:2] - (yaw_matrix(yaw) @ c)[:2] + np.asarray(shift0, dtype=np.float64)
        IX, IY = np.meshgrid(np.arange(C.shape[0]), np.arange(C.shape[1]), indexing="ij")
        TX = tx0 + (IX - nxS + 1) * cell - sx0
        TY = ty0 + (IY - nyS + 1) * cell - sy0
        C = np.where(np.hypot(TX - ex[0], TY - ex[1]) <= max_shift, C, -1.0)
        # i 6 picchi di sovrapposizione migliori (distanti tra loro), ognuno valutato sulla coerenza dei pavimenti
        Cw = C.copy()
        for _ in range(6):
            j = int(np.argmax(Cw))
            if Cw.flat[j] <= 0:
                break
            ix, iy = np.unravel_index(j, Cw.shape)
            Cw[max(0, ix - 8):ix + 9, max(0, iy - 8):iy + 9] = -1.0
            dx = tx0 + (ix - nxS + 1) * cell - sx0
            dy = ty0 + (iy - nyS + 1) * cell - sy0
            sc, dz, nok, nko = floor_score(FT, tx0, ty0, cell, bb + np.array([dx, dy, 0.0]))
            cands.append((sc, yaw, dx, dy, dz))
    cands.sort(key=lambda c: -c[0])
    best = None
    for k, (score, yaw, dx, dy, dz) in enumerate(cands[:top]):
        if progress:
            progress("Aggancio: affinamento %d di %d (rotazione %.0f gradi)" % (k + 1, top, yaw))
        y2, t2, inl, med = icp_yaw(a, na, b0, yaw, [dx, dy, dz], it=40, maxd=1.5)
        y2, t2, inl, med = icp_yaw(a, na, b0, y2, t2, it=25, maxd=0.5)
        sc2 = floor_score(FT, tx0, ty0, cell, b0 @ yaw_matrix(y2).T + t2)[0]
        if best is None or sc2 > best[4]:
            best = (y2, t2, inl, med, sc2)
    if best is None or best[2] < 50:
        return None
    yaw, t, inl, med, _ = best
    R = yaw_matrix(yaw)
    tw = t + O - R @ O
    c = src.mean(0)
    return dict(R=R, t=tw, yaw=float(((yaw + 180) % 360) - 180), shift=float(np.linalg.norm((R @ c + tw - c)[:2])),
                dz=float((R @ c + tw - c)[2]), inliers=inl, frac=inl / float(len(b0)), median=med)
