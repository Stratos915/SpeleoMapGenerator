# -*- coding: utf-8 -*-
"""SpeleoMap Generator - RILIEVO: correzione della posizione, soglia d'accesso e separazione delle gallerie (Fase 7).

Convenzione: i layer disegnati dall'utente (punto d'accesso, poligono "limite gallerie") stanno nel sistema
del LAS cosi' come QGIS lo mostra (prima della correzione di posizione). Le tavole escono corrette.

Tipi di rilievo:
  grotta  -> si usa tutta la nuvola (comportamento delle versioni precedenti)
  ipogeo  -> si tengono solo le gallerie: pavimento almeno 'below' m sotto la soglia d'accesso,
             oppure l'area del poligono disegnato. Il resto serve solo come riferimento.
"""
import math
import re

import numpy as np


# ------------------------------------------------------------------ utilita'
def parse_known_point(text, crs):
    """Punto noto: 'lat, lon' (gradi) oppure 'E, N' (metri nel sistema del rilievo). Restituisce (E, N) o None."""
    if not text or not str(text).strip():
        return None
    nums = [float(v.replace(",", ".")) for v in re.findall(r"-?\d+(?:[.,]\d+)?", str(text).replace(", ", " ; "))]
    if len(nums) < 2:
        raise ValueError("servono due numeri")
    a, b = nums[0], nums[1]
    if abs(a) > 1000 or abs(b) > 1000:
        return float(a), float(b)
    from qgis.core import QgsCoordinateTransform, QgsCoordinateReferenceSystem, QgsProject, QgsPointXY
    p = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"), crs, QgsProject.instance()).transform(QgsPointXY(b, a))
    return p.x(), p.y()


class Shift:
    """Rototraslazione planimetrica (rotazione in gradi antioraria attorno a cx, cy, poi traslazione) e quota."""

    def __init__(self, dx=0.0, dy=0.0, rot=0.0, cx=0.0, cy=0.0, dz=0.0):
        self.dx, self.dy, self.rot, self.cx, self.cy, self.dz = float(dx), float(dy), float(rot), float(cx), float(cy), float(dz)

    def is_identity(self):
        return self.dx == 0 and self.dy == 0 and self.rot == 0 and self.dz == 0

    def xy(self, x, y):
        if self.rot:
            a = math.radians(self.rot)
            ca, sa = math.cos(a), math.sin(a)
            x, y = self.cx + (x - self.cx) * ca - (y - self.cy) * sa, self.cy + (x - self.cx) * sa + (y - self.cy) * ca
        return x + self.dx, y + self.dy

    def inv_xy(self, x, y):
        x, y = x - self.dx, y - self.dy
        if self.rot:
            a = math.radians(-self.rot)
            ca, sa = math.cos(a), math.sin(a)
            x, y = self.cx + (x - self.cx) * ca - (y - self.cy) * sa, self.cy + (x - self.cx) * sa + (y - self.cy) * ca
        return x, y

    def describe(self):
        if self.dx == 0 and self.dy == 0 and self.rot == 0:
            return ""
        t = "Posizione corretta: dE %+.2f m, dN %+.2f m" % (self.dx, self.dy)
        if self.rot:
            t += ", rotazione %+.2f gradi" % self.rot
        return t


# ------------------------------------------------------------------ griglia del pavimento
class FloorGrid:
    """Per ogni cella (lato 'cell' m) la quota piu' bassa dei punti: e' il pavimento visto dall'alto."""

    def __init__(self, x, y, z, cell=0.2):
        self.c = float(cell)
        self.x0, self.y0 = float(x.min()) - cell, float(y.min()) - cell
        self.W = int((x.max() - self.x0) / cell) + 2
        self.H = int((y.max() - self.y0) / cell) + 2
        self.key = self.keys(x, y)
        fl = np.full(self.W * self.H, np.inf)
        np.minimum.at(fl, self.key, z)
        self.floor = fl.reshape(self.H, self.W)
        self.occ = np.isfinite(self.floor)

    def keys(self, x, y):
        ix = np.clip(((x - self.x0) / self.c).astype(np.int64), 0, self.W - 1)
        iy = np.clip(((y - self.y0) / self.c).astype(np.int64), 0, self.H - 1)
        return iy * self.W + ix

    def cell_of(self, px, py):
        return int((py - self.y0) / self.c), int((px - self.x0) / self.c)

    def center(self, i, j):
        return self.x0 + (j + 0.5) * self.c, self.y0 + (i + 0.5) * self.c

    def flat(self, tol=0.08):
        f = np.where(self.occ, self.floor, np.nan)
        pad = np.pad(f, 1, constant_values=np.nan)
        d = np.zeros(f.shape)
        for di, dj in ((0, 1), (1, 0), (0, -1), (-1, 0)):
            nb = pad[1 + di:1 + di + f.shape[0], 1 + dj:1 + dj + f.shape[1]]
            dd = np.abs(nb - f)
            d = np.fmax(d, np.nan_to_num(dd, nan=0.0))
        return self.occ & (d < tol)

    def mask_from_polygon(self, geom_raw):
        """Celle il cui centro sta dentro la geometria (QgsGeometry nel sistema del LAS)."""
        from qgis.core import QgsGeometry, QgsPointXY
        m = np.zeros((self.H, self.W), bool)
        bb = geom_raw.boundingBox()
        i0, j0 = self.cell_of(bb.xMinimum(), bb.yMinimum())
        i1, j1 = self.cell_of(bb.xMaximum(), bb.yMaximum())
        for i in range(max(0, i0), min(self.H, i1 + 1)):
            for j in range(max(0, j0), min(self.W, j1 + 1)):
                cx, cy = self.center(i, j)
                if geom_raw.contains(QgsPointXY(cx, cy)):
                    m[i, j] = True
        return m


def plateau_level(fg, min_area=2.0, step=0.1):
    """Quota del pianerottolo piano piu' alto con almeno min_area m2 (la soglia d'accesso se non e' indicato altro)."""
    flat = fg.flat()
    vals = fg.floor[flat]
    if vals.size == 0:
        return None
    bins = np.arange(np.floor(vals.min() / step) * step, vals.max() + 2 * step, step)
    h, e = np.histogram(vals, bins=bins)
    hs = np.convolve(h, np.ones(3), mode="same")
    good = np.nonzero(hs >= min_area / (fg.c * fg.c))[0]
    if good.size == 0:
        return None
    k = good.max()
    lo, hi = e[max(0, k - 1)], e[min(len(e) - 1, k + 2)]
    return float(np.median(vals[(vals >= lo) & (vals <= hi)]))


def threshold_near(fg, px, py, radius=6.0, min_area=0.5):
    """Soglia vicino a un punto d'accesso: il ripiano piano piu' alto (>= min_area m2) entro 'radius' m,
    misurato sulle sue celle piu' vicine al punto (cioe' in cima alla scala)."""
    from scipy import ndimage
    i, j = fg.cell_of(px, py)
    ii, jj = np.mgrid[0:fg.H, 0:fg.W]
    dist = np.hypot(ii - i, jj - j) * fg.c
    flat = fg.flat() & (dist <= radius)
    if not flat.any():
        return None
    vals = fg.floor[flat]
    step = 0.1
    bins = np.arange(np.floor(vals.min() / step) * step, vals.max() + 2 * step, step)
    h, e = np.histogram(vals, bins=bins)
    hs = np.convolve(h, np.ones(3), mode="same")
    good = np.nonzero(hs >= min_area / (fg.c * fg.c))[0]
    if good.size == 0:
        return None
    k = good.max()
    lev = float(np.median(vals[(vals >= e[max(0, k - 1)]) & (vals <= e[min(len(e) - 1, k + 2)])]))
    near = flat & (np.abs(fg.floor - lev) < 0.35)
    lab, n = ndimage.label(near)
    if n == 0:
        return lev
    d_in = np.where(lab > 0, dist, np.inf)
    dmin = d_in.min()
    sel = (lab > 0) & (dist <= dmin + 1.0)
    return float(np.median(fg.floor[sel]))


def gallery_mask(fg, threshold, below=2.5, min_area=1.0, polygon_mask=None):
    """Celle delle gallerie: dentro il poligono dell'utente, oppure pavimento almeno 'below' m sotto la soglia.
    Si tengono le componenti connesse di almeno min_area m2."""
    from scipy import ndimage
    if polygon_mask is not None:
        m = fg.occ & polygon_mask
    else:
        m = fg.occ & (fg.floor <= threshold - below)
        m = (ndimage.binary_closing(m, iterations=2) & fg.occ) | m
    lab, n = ndimage.label(m)
    if n == 0:
        return m
    sizes = ndimage.sum(np.ones_like(lab), lab, index=np.arange(1, n + 1)) * fg.c * fg.c
    return np.isin(lab, np.nonzero(sizes >= min_area)[0] + 1)


def auto_access(fg, gal, threshold):
    """Accesso automatico: la cella di galleria piu' vicina al ripiano della soglia (celle fuori galleria
    con pavimento entro 0,3 m dalla soglia)."""
    from scipy import ndimage
    land = fg.occ & ~gal & (np.abs(fg.floor - threshold) < 0.3)
    if not land.any() or not gal.any():
        return None
    dist = ndimage.distance_transform_edt(~land)
    gi, gj = np.nonzero(gal)
    k = int(np.argmin(dist[gi, gj]))
    return fg.center(gi[k], gj[k])


def mask_polygon(fg, gal):
    """Poligono (QgsGeometry, sistema del LAS) delle celle di galleria: e' la proposta modificabile dall'utente."""
    from qgis.core import QgsGeometry, QgsRectangle
    parts = []
    for i in range(fg.H):
        row = gal[i]
        j = 0
        while j < fg.W:
            if row[j]:
                j0 = j
                while j < fg.W and row[j]:
                    j += 1
                x0, y0 = fg.x0 + j0 * fg.c, fg.y0 + i * fg.c
                parts.append(QgsGeometry.fromRect(QgsRectangle(x0, y0, fg.x0 + j * fg.c, y0 + fg.c)))
            else:
                j += 1
    if not parts:
        return None
    g = QgsGeometry.unaryUnion(parts)
    return g.simplify(fg.c * 0.5)


# ------------------------------------------------------------------ grotte con parti esterne (cielo aperto)
class SkySplit:
    """Separa la parte coperta (c'e' una volta sopra il pavimento) dalla parte a cielo aperto.
    La grotta e' la piu' grande zona coperta; gli ingressi sono i tratti dove tocca l'esterno."""

    def __init__(self, x, y, z, cell=0.25, gap=1.0, min_ext=15.0):
        from scipy import ndimage
        self.c = float(cell)
        self.x0, self.y0 = float(x.min()) - cell, float(y.min()) - cell
        self.W = int((x.max() - self.x0) / cell) + 2
        self.H = int((y.max() - self.y0) / cell) + 2
        ix = ((x - self.x0) / cell).astype(np.int64)
        iy = ((y - self.y0) / cell).astype(np.int64)
        key = iy * self.W + ix
        self.key = key
        o = np.lexsort((z, key))
        k, zz = key[o], z[o]
        st = np.r_[0, np.nonzero(np.diff(k))[0] + 1]
        en = np.r_[st[1:], len(k)]
        occ = np.zeros(self.W * self.H, bool)
        cov = np.zeros(self.W * self.H, bool)
        fl = np.full(self.W * self.H, np.nan)
        cnt = en - st
        occ[k[st]] = True
        fl[k[st]] = zz[st]
        # massimo salto verticale tra punti consecutivi della colonna
        d = np.diff(zz)
        d[np.diff(k) != 0] = 0.0
        grp = np.repeat(np.arange(len(st)), cnt)[:-1]
        mx = np.zeros(len(st))
        np.maximum.at(mx, grp, d)
        cov[k[st]] = (mx >= gap) & (cnt >= 4)
        self.occ = occ.reshape(self.H, self.W)
        cov = cov.reshape(self.H, self.W)
        self.floor = fl.reshape(self.H, self.W)
        covc = (ndimage.binary_closing(cov, iterations=3) & self.occ) | cov
        lab, n = ndimage.label(covc)
        if n == 0:
            raise RuntimeError("Nella nuvola non trovo parti coperte da una volta: usa il tipo 'Grotta'.")
        sz = ndimage.sum(np.ones_like(lab), lab, range(1, n + 1))
        cave = lab == (int(np.argmax(sz)) + 1)
        cave = (ndimage.binary_fill_holes(cave) & self.occ) | cave
        self.cave = cave
        opn = self.occ & ~cave
        ext = ndimage.binary_opening(opn, iterations=3)
        lab2, n2 = ndimage.label(ext)
        sz2 = ndimage.sum(np.ones_like(lab2), lab2, range(1, n2 + 1)) * cell * cell if n2 else np.array([])
        self.ext_lab = lab2
        self.ext_ids = [i + 1 for i in np.argsort(-sz2) if sz2[i] >= min_ext]
        self.ext_area = {i + 1: float(sz2[i]) for i in range(n2)}

    def entrances(self, merge=6.0, min_width=1.0):
        """[(xc, yc, z_floor, area_esterno, (xa, ya), (xb, yb))] ordinati: prima l'esterno piu' grande."""
        from scipy import ndimage
        out = []
        for eid in self.ext_ids:
            near = ndimage.binary_dilation(self.ext_lab == eid, iterations=4)
            gate = self.cave & near
            lab, n = ndimage.label(ndimage.binary_dilation(gate, iterations=4))
            for k in range(1, n + 1):
                m = (lab == k) & gate
                if m.sum() * self.c * self.c < 0.5:
                    continue
                ii, jj = np.nonzero(m)
                px = self.x0 + (jj + 0.5) * self.c
                py = self.y0 + (ii + 0.5) * self.c
                # estremi della soglia: la coppia di celle piu' lontane
                P = np.column_stack([px, py])
                a = P[np.argmax(np.hypot(*(P - P.mean(0)).T))]
                b = P[np.argmax(np.hypot(*(P - a).T))]
                out.append((float(px.mean()), float(py.mean()), float(np.nanmedian(self.floor[m])),
                            self.ext_area[eid], (float(a[0]), float(a[1])), (float(b[0]), float(b[1])), P))
        # contatti vicini (meno di merge m) sono lo stesso ingresso: si tiene il piu' largo
        wid = lambda e: math.hypot(e[4][0] - e[5][0], e[4][1] - e[5][1])
        keep = []
        def segd(px, py, a, b):
            ax, ay, bx, by = a[0], a[1], b[0], b[1]
            L2 = (bx - ax) ** 2 + (by - ay) ** 2
            t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / L2))
            return math.hypot(px - (ax + t * (bx - ax)), py - (ay + t * (by - ay)))
        for e in sorted(out, key=lambda e: -wid(e)):
            if all(math.hypot(e[0] - k[0], e[1] - k[1]) >= merge and segd(e[0], e[1], k[4], k[5]) >= 3.0 for k in keep):
                keep.append(e)
        keep = [e for e in keep if wid(e) >= min_width] or keep[:1]
        # ordine: prima l'ingresso verso l'esterno piu' grande, poi il piu' largo
        return sorted(keep, key=lambda e: (-e[3], -wid(e)))

    def keep(self):
        return self.cave.ravel()[self.key]


# ------------------------------------------------------------------ preparazione del rilievo
def prepare_survey(x, y, z, params, crs, step=lambda m: None):
    """Restituisce dict con x, y, z (eventualmente filtrati e corretti) e le informazioni sugli ingressi.
    Tipi: 'grotta' (tutto il rilievo), 'esterno' (grotta con parti a cielo aperto: si tiene la parte coperta,
    gli ingressi sono dove tocca l'esterno), 'ipogeo' (gallerie sotto la soglia d'accesso).
    Con tipo 'grotta', nessuna correzione e nessun punto disegnato, i punti tornano identici."""
    kind = (params.get("survey_type") or "grotta").lower()
    out = dict(kind=kind, x=x, y=y, z=z, shift=Shift(), access=None, access_raw=None, access_z=None,
               access_src="", threshold_raw=None, quota_src="", context=None, gallery_raw=None, notes=[],
               is_gallery_raw=None, entrances=None)
    # punti d'ingresso/accesso disegnati dall'utente (sistema del LAS): uno o piu'
    drawn = []
    al = params.get("access_layer")
    if al is not None and al.isValid() and al.featureCount() > 0:
        tr = None
        if al.crs().isValid() and al.crs() != crs:
            from qgis.core import QgsCoordinateTransform, QgsProject
            tr = QgsCoordinateTransform(al.crs(), crs, QgsProject.instance())
        for f in al.getFeatures():
            g = f.geometry()
            if g is None or g.isEmpty():
                continue
            if tr is not None:
                g.transform(tr)
            for p in (g.asMultiPoint() if g.isMultipart() else [g.asPoint()]):
                drawn.append((p.x(), p.y()))
    acc_raw = drawn[0] if drawn else None
    if drawn:
        out["access_src"] = "punto disegnato" if len(drawn) == 1 else "%d punti disegnati" % len(drawn)
    ent_raw = []          # [(x, y, z_pavimento, corda o None)] nel sistema del LAS

    if kind == "esterno":
        step("Grotta e parti esterne: ricerca della volta e degli ingressi...")
        sp = SkySplit(x, y, z)
        found = sp.entrances()
        if drawn:
            for (px, py) in drawn:
                i, j = int((py - sp.y0) / sp.c), int((px - sp.x0) / sp.c)
                w = sp.floor[max(0, i - 2):i + 3, max(0, j - 2):j + 3]
                zf = float(np.nanmin(w)) if np.isfinite(w).any() else None
                near = [e for e in found if math.hypot(e[0] - px, e[1] - py) < 4.0]
                ch = (near[0][4], near[0][5], near[0][6]) if near else None
                ent_raw.append((px, py, zf if zf is not None else (near[0][2] if near else None), ch))
        else:
            ent_raw = [(e[0], e[1], e[2], (e[4], e[5], e[6])) for e in found]
            out["access_src"] = "automatico (%d)" % len(found)
        if not ent_raw:
            out["notes"].append("Nessun ingresso trovato tra grotta ed esterno")
        keep = sp.keep()
        out["notes"].append("Grotta: parte coperta del rilievo; il resto (a cielo aperto) solo come riferimento")
        _sp = sp

        def _isin(xx, yy):
            ix = ((xx - _sp.x0) / _sp.c).astype(np.int64)
            iy = ((yy - _sp.y0) / _sp.c).astype(np.int64)
            ok = (ix >= 0) & (ix < _sp.W) & (iy >= 0) & (iy < _sp.H)
            r = np.zeros(len(xx), bool)
            r[ok] = _sp.cave[iy[ok], ix[ok]]
            return r
        out["is_gallery_raw"] = _isin
        ctx = ~keep
        dec = max(1, int(ctx.sum() // 60000))
        out["context"] = (x[ctx][::dec], y[ctx][::dec], z[ctx][::dec])
        x, y, z = x[keep], y[keep], z[keep]
        if ent_raw and ent_raw[0][2] is not None:
            out["threshold_raw"] = ent_raw[0][2]
        acc_raw = (ent_raw[0][0], ent_raw[0][1]) if ent_raw else acc_raw
        step("Grotta: %d punti su %d, %d ingressi" % (len(x), len(keep), len(ent_raw)))

    elif kind == "ipogeo":
        step("Ipogeo: pavimento, soglia d'accesso e gallerie...")
        fg = FloorGrid(x, y, z, 0.2)
        thr = threshold_near(fg, *acc_raw) if acc_raw else None
        if thr is None:
            thr = plateau_level(fg)
        if thr is None:
            raise RuntimeError("Non trovo il ripiano della soglia d'accesso: disegna il punto d'accesso sulla nuvola.")
        poly_mask = None
        gl = params.get("gallery_layer")
        if gl is not None and gl.isValid() and gl.featureCount() > 0:
            from qgis.core import QgsGeometry, QgsCoordinateTransform, QgsProject
            gg = QgsGeometry.unaryUnion([f.geometry().makeValid() for f in gl.getFeatures()])
            if gl.crs().isValid() and gl.crs() != crs:
                gg.transform(QgsCoordinateTransform(gl.crs(), crs, QgsProject.instance()))
            poly_mask = fg.mask_from_polygon(gg)
            out["notes"].append("Gallerie delimitate dal poligono disegnato")
        below = float(params.get("gallery_below") or 2.5)
        gal = gallery_mask(fg, thr, below, polygon_mask=poly_mask)
        if not gal.any():
            raise RuntimeError("Nessuna galleria trovata sotto la soglia (%.2f m): riduci il dislivello minimo o disegna il limite." % thr)
        if poly_mask is None:
            out["notes"].append("Gallerie: pavimento almeno %.1f m sotto la soglia d'accesso" % below)
        if acc_raw is None:
            acc_raw = auto_access(fg, gal, thr)
            out["access_src"] = "automatico"
        out["threshold_raw"] = thr
        # sopra la soglia d'accesso non ci sono gallerie (stanze, scale, edifici): si tiene fino a soglia + 0,3 m
        keep = gal.ravel()[fg.key] & (z <= thr + float(params.get("gallery_top") or 0.3))
        out["gallery_raw"] = mask_polygon(fg, gal)
        _fg, _gal = fg, gal

        def _isgal(xx, yy):
            ix = ((xx - _fg.x0) / _fg.c).astype(np.int64)
            iy = ((yy - _fg.y0) / _fg.c).astype(np.int64)
            ok = (ix >= 0) & (ix < _fg.W) & (iy >= 0) & (iy < _fg.H)
            r = np.zeros(len(xx), bool)
            r[ok] = _gal[iy[ok], ix[ok]]
            return r
        out["is_gallery_raw"] = _isgal
        ctx = ~keep
        dec = max(1, int(ctx.sum() // 60000))
        out["context"] = (x[ctx][::dec], y[ctx][::dec], z[ctx][::dec])
        x, y, z = x[keep], y[keep], z[keep]
        ent_raw = [(acc_raw[0], acc_raw[1], thr, None)] + [(p[0], p[1], thr, None) for p in drawn[1:]]
        step("Gallerie: %d punti su %d (soglia nel rilievo %.2f m)" % (len(x), len(keep), thr))

    elif drawn:
        ent_raw = [(p[0], p[1], None, None) for p in drawn]

    # correzione della posizione
    mode = (params.get("shift_mode") or "none").lower()
    sh = Shift()
    if mode == "dxdy":
        c = acc_raw or (float(np.mean(x)), float(np.mean(y)))
        sh = Shift(params.get("shift_dx") or 0.0, params.get("shift_dy") or 0.0, params.get("shift_rot") or 0.0, c[0], c[1])
    elif mode == "punto":
        kp = params.get("known_point")
        if kp is None:
            out["notes"].append("Correzione con punto noto non applicata: manca il punto")
        elif acc_raw is None:
            out["notes"].append("Correzione con punto noto non applicata: manca il punto d'accesso sulla nuvola")
        else:
            sh = Shift(kp[0] - acc_raw[0], kp[1] - acc_raw[1], params.get("shift_rot") or 0.0, acc_raw[0], acc_raw[1])

    # quote assolute: la quota inserita e' quella della soglia/ingresso principale
    q = params.get("access_quota")
    if q is not None and out["threshold_raw"] is not None:
        sh.dz = float(q) - out["threshold_raw"]
        out["quota_src"] = "inserita"
    if out["threshold_raw"] is not None:
        out["access_z"] = out["threshold_raw"] + sh.dz

    if not (sh.dx == 0 and sh.dy == 0 and sh.rot == 0):
        x, y = sh.xy(x, y)
        if out["context"] is not None:
            cx_, cy_ = sh.xy(out["context"][0], out["context"][1])
            out["context"] = (cx_, cy_, out["context"][2])
    if sh.dz:
        z = z + sh.dz
        if out["context"] is not None:
            out["context"] = (out["context"][0], out["context"][1], out["context"][2] + sh.dz)
    out["shift"] = sh
    if acc_raw is not None:
        out["access_raw"] = acc_raw
        out["access"] = sh.xy(acc_raw[0], acc_raw[1])
    if ent_raw:
        ents = []
        for (px, py, zf, ch) in ent_raw:
            ex, ey = sh.xy(px, py)
            chc, gate = None, None
            if ch is not None:
                a = sh.xy(ch[0][0], ch[0][1])
                b = sh.xy(ch[1][0], ch[1][1])
                chc = (a, b)
                if len(ch) > 2 and ch[2] is not None:
                    gx, gy = sh.xy(ch[2][:, 0], ch[2][:, 1])
                    gate = list(zip(np.asarray(gx).tolist(), np.asarray(gy).tolist()))
            ents.append(dict(xy=(float(ex), float(ey)), z=(zf + sh.dz) if zf is not None else None, chord=chc, gate=gate))
        out["entrances"] = ents
    out["x"], out["y"], out["z"] = x, y, z
    if sh.describe():
        out["notes"].append(sh.describe())
    return out


def gallery_layer_raw(crs, geom, path_gpkg, table="limite_gallerie"):
    """Salva la proposta delle gallerie (sistema del LAS) come poligono modificabile."""
    from qgis.core import QgsVectorLayer, QgsFeature, QgsField, QgsVectorFileWriter, QgsCoordinateTransformContext
    from qgis.PyQt.QtCore import QVariant
    vl = QgsVectorLayer("Polygon", "limite gallerie (sulla nuvola LAS)", "memory")
    vl.setCrs(crs)
    vl.dataProvider().addAttributes([QgsField("nota", QVariant.String)])
    vl.updateFields()
    f = QgsFeature(vl.fields())
    f.setGeometry(geom)
    f.setAttributes(["proposta automatica: modificala e usala come limite delle gallerie"])
    vl.dataProvider().addFeatures([f])
    o = QgsVectorFileWriter.SaveVectorOptions()
    o.driverName = "GPKG"
    o.layerName = table
    o.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
    QgsVectorFileWriter.writeAsVectorFormatV3(vl, path_gpkg, QgsCoordinateTransformContext(), o)
    lyr = QgsVectorLayer("%s|layername=%s" % (path_gpkg, table), "limite gallerie (sulla nuvola LAS)", "ogr")
    return lyr
