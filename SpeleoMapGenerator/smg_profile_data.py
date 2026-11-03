# -*- coding: utf-8 -*-
"""SpeleoMap Generator - PROFILO LONGITUDINALE: dati campionati lungo l'asse."""
import csv
import numpy as np


def profile_samples(s_all, o_all, z, length, step=0.5, half_width=0.75, thickness=0.30,
                    min_height=0.3, min_count=5):
    """Campiona la cavita' lungo l'asse ogni 'step' metri.
    Per ogni progressiva: punti entro +/- thickness/2 lungo l'asse.
    - z_floor / z_roof: quote basse/alte (2 e 98 percentile) dei punti entro +/- half_width dall'asse
    - width: larghezza della galleria (2-98 percentile della distanza laterale di tutti i punti della fetta)
    - z_roof vuoto dove la volta non e' stata ripresa (altezza < min_height)."""
    order = np.argsort(s_all)
    ss, oo, zz = s_all[order], o_all[order], z[order]
    out = []
    n = int(np.floor(length / step)) + 1
    for i in range(n):
        s0 = i * step
        a = np.searchsorted(ss, s0 - thickness / 2.0)
        b = np.searchsorted(ss, s0 + thickness / 2.0)
        row = dict(chainage=round(s0, 2), z_floor=None, z_roof=None, z_center=None, width=None, height=None, n_points=int(b - a),
                   left=None, right=None)
        if b - a >= min_count:
            o_sl, z_sl = oo[a:b], zz[a:b]
            row["width"] = round(float(np.percentile(o_sl, 98) - np.percentile(o_sl, 2)), 2)
            # distanza laterale positiva = a sinistra guardando verso la progressiva crescente
            row["left"] = round(max(0.0, float(np.percentile(o_sl, 98))), 2)
            row["right"] = round(max(0.0, -float(np.percentile(o_sl, 2))), 2)
            m = np.abs(o_sl) <= half_width
            if m.sum() >= min_count:
                zf = float(np.percentile(z_sl[m], 2))
                zr = float(np.percentile(z_sl[m], 98))
                row["z_floor"] = round(zf, 2)
                if zr - zf >= min_height:
                    row["z_roof"] = round(zr, 2)
                    row["z_center"] = round((zf + zr) / 2.0, 2)
                    row["height"] = round(zr - zf, 2)
        out.append(row)
    return out


def value_at(samples, s, key):
    """Valore interpolato del campo 'key' alla progressiva s (ignora i campioni vuoti)."""
    pts = [(r["chainage"], r[key]) for r in samples if r[key] is not None]
    if not pts:
        return None
    xs = np.array([p[0] for p in pts])
    ys = np.array([p[1] for p in pts])
    if s <= xs[0]:
        return float(ys[0])
    if s >= xs[-1]:
        return float(ys[-1])
    return round(float(np.interp(s, xs, ys)), 2)


def write_profile_csv(path, samples):
    keys = ["chainage", "z_floor", "z_roof", "z_center", "width", "height", "n_points"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(keys)
        for r in samples:
            w.writerow(["" if r[k] is None else str(r[k]).replace(".", ",") for k in keys])
