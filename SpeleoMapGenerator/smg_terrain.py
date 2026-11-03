# -*- coding: utf-8 -*-
"""SpeleoMap Generator - CONTESTO TERRENO: basemap, DEM (Copernicus GLO-30), ombreggiatura, curve di livello."""
import math
import os

from osgeo import gdal, ogr, osr
from qgis.core import (QgsRasterLayer, QgsVectorLayer, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                       QgsProject, QgsRectangle, QgsLineSymbol, QgsSingleSymbolRenderer, QgsPalLayerSettings,
                       QgsTextFormat, QgsTextBufferSettings, QgsVectorLayerSimpleLabeling, QgsRuleBasedRenderer, Qgis)
from qgis.PyQt.QtGui import QColor, QFont

BASEMAPS = {
    "osm": ("OpenStreetMap", "type=xyz&url=https://tile.openstreetmap.org/{z}/{x}/{y}.png&zmax=19&zmin=0", "wms",
            "(c) OpenStreetMap contributors"),
    "esri": ("Esri World Imagery", "type=xyz&url=https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}&zmax=19&zmin=0",
             "wms", "Immagini (c) Esri, Maxar, Earthstar Geographics"),
}


def basemap_layer(kind):
    if kind not in BASEMAPS:
        return None, ""
    title, uri, prov, credit = BASEMAPS[kind]
    lyr = QgsRasterLayer(uri, title, prov)
    return (lyr if lyr.isValid() else None), credit


def _cop_tile(lat, lon):
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    name = "Copernicus_DSM_COG_10_%s%02d_00_%s%03d_00_DEM" % (ns, abs(lat), ew, abs(lon))
    return "/vsicurl/https://copernicus-dem-30m.s3.amazonaws.com/%s/%s.tif" % (name, name)


def copernicus_dem(extent, crs, out_tif, res=5.0):
    """Scarica e ritaglia il DEM Copernicus GLO-30 sull'estensione (nel CRS del rilievo), ricampionato a 'res' m."""
    tr = QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())
    ll = tr.transformBoundingBox(extent)
    tiles = []
    for la in range(int(math.floor(ll.yMinimum())), int(math.floor(ll.yMaximum())) + 1):
        for lo in range(int(math.floor(ll.xMinimum())), int(math.floor(ll.xMaximum())) + 1):
            tiles.append(_cop_tile(la, lo))
    gdal.SetConfigOption("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    ds = gdal.Warp(out_tif, tiles, dstSRS=crs.authid(), outputBounds=(extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum()),
                   xRes=res, yRes=res, resampleAlg="cubicspline", format="GTiff", creationOptions=["COMPRESS=DEFLATE"])
    if ds is None:
        raise RuntimeError("Download del DEM Copernicus non riuscito (controlla la connessione).")
    band = ds.GetRasterBand(1)
    mn, mx = band.ComputeRasterMinMax(False)
    ds = None
    return out_tif, (mn, mx)


def hillshade(dem_tif, out_tif):
    ds = gdal.DEMProcessing(out_tif, dem_tif, "hillshade", azimuth=315, altitude=45, zFactor=1.0, computeEdges=True,
                            creationOptions=["COMPRESS=DEFLATE"])
    ds = None
    return out_tif


def contour_interval(relief):
    for iv in (1, 2, 5, 10, 20, 25, 50, 100):
        if relief / iv <= 20:
            return iv
    return 100


def contours(dem_tif, out_gpkg, interval):
    src = gdal.Open(dem_tif)
    drv = ogr.GetDriverByName("GPKG")
    if os.path.exists(out_gpkg):
        drv.DeleteDataSource(out_gpkg)
    dst = drv.CreateDataSource(out_gpkg)
    srs = osr.SpatialReference()
    srs.ImportFromWkt(src.GetProjection())
    lyr = dst.CreateLayer("curve_livello", srs, ogr.wkbLineString)
    lyr.CreateField(ogr.FieldDefn("id", ogr.OFTInteger))
    lyr.CreateField(ogr.FieldDefn("quota", ogr.OFTReal))
    gdal.ContourGenerate(src.GetRasterBand(1), float(interval), 0.0, [], 0, 0.0, lyr, 0, 1)
    dst = None
    src = None
    return out_gpkg


def style_contours(vl, interval, on_image=False):
    from qgis.core import QgsSymbolLayer, QgsProperty
    major = interval * 5
    sym = QgsLineSymbol.createSimple({"line_color": "255,205,120" if on_image else "140,95,50", "line_width": "0.15"})
    sym.symbolLayer(0).setDataDefinedProperty(QgsSymbolLayer.PropertyStrokeWidth,
                                              QgsProperty.fromExpression("if(\"quota\" %% %s = 0, 0.35, 0.15)" % major))
    vl.setRenderer(QgsSingleSymbolRenderer(sym))
    pal = QgsPalLayerSettings()
    pal.enabled = True
    pal.fieldName = "format_number(\"quota\", 0)"
    pal.isExpression = True
    try:
        pal.placement = Qgis.LabelPlacement.Line
    except Exception:
        pal.placement = QgsPalLayerSettings.Line
    fmt = QgsTextFormat()
    fmt.setFont(QFont("Arial"))
    fmt.setSize(6)
    fmt.setColor(QColor(120, 80, 40))
    buf = QgsTextBufferSettings()
    buf.setEnabled(True)
    buf.setSize(0.6)
    buf.setColor(QColor("white"))
    fmt.setBuffer(buf)
    pal.setFormat(fmt)
    try:
        from qgis.core import QgsPropertyCollection, QgsPalLayerSettings as P
        pal.dataDefinedProperties().setProperty(P.Show, __import__("qgis.core", fromlist=["QgsProperty"]).QgsProperty.fromExpression("\"quota\" %% %s = 0" % major))
    except Exception:
        pass
    vl.setLabeling(QgsVectorLayerSimpleLabeling(pal))
    vl.setLabelsEnabled(True)



# ---------------------------------------------------------------- Italia
ITALY_LL = (35.3, 47.2, 6.5, 18.7)   # lat min, lat max, lon min, lon max (indicativo)


def in_italy(lat, lon):
    return ITALY_LL[0] <= lat <= ITALY_LL[1] and ITALY_LL[2] <= lon <= ITALY_LL[3]


def _download(url, fn):
    from qgis.core import QgsBlockingNetworkRequest
    from qgis.PyQt.QtNetwork import QNetworkRequest
    from qgis.PyQt.QtCore import QUrl
    r = QgsBlockingNetworkRequest()
    err = r.get(QNetworkRequest(QUrl(url)))
    data = bytes(r.reply().content())
    if err != 0 or data[:2] not in (b"II", b"MM"):
        raise RuntimeError("il server non ha restituito un GeoTIFF (%s)" % data[:120])
    with open(fn, "wb") as fh:
        fh.write(data)
    return fn


def tinitaly_dem(extent, crs, out_tif, res=5.0):
    """DEM TINITALY 1.1 (INGV, 10 m, CC BY 4.0) via WCS, riproiettato e ricampionato sul rilievo."""
    tr = QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem("EPSG:32632"), QgsProject.instance())
    bb = tr.transformBoundingBox(extent)
    bb.grow(30)
    x0, y0, x1, y1 = [int(math.floor(bb.xMinimum())), int(math.floor(bb.yMinimum())), int(math.ceil(bb.xMaximum())), int(math.ceil(bb.yMaximum()))]
    url = ("https://tinitaly.pi.ingv.it/TINItaly_1_1/wcs?service=WCS&version=1.0.0&request=GetCoverage"
           "&coverage=TINItaly_1_1:tinitaly_dem&crs=EPSG:32632&bbox=%d,%d,%d,%d&width=%d&height=%d&format=GeoTIFF"
           % (x0, y0, x1, y1, max(1, (x1 - x0) // 10), max(1, (y1 - y0) // 10)))
    tmp = out_tif.replace(".tif", "_tinitaly_orig.tif")
    _download(url, tmp)
    src = gdal.Open(tmp)
    mn, mx = src.GetRasterBand(1).ComputeRasterMinMax(False)
    src = None
    if mx <= 0:
        raise RuntimeError("TINITALY non copre quest'area (fuori dall'Italia)")
    ds = gdal.Warp(out_tif, tmp, dstSRS=crs.authid(), outputBounds=(extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum()),
                   xRes=res, yRes=res, resampleAlg="cubicspline", format="GTiff", creationOptions=["COMPRESS=DEFLATE"])
    b = ds.GetRasterBand(1)
    mn, mx = b.ComputeRasterMinMax(False)
    ds = None
    return out_tif, (mn, mx)


def file_dem(path, extent, crs, out_tif, res=None):
    """DEM da un file GeoTIFF dell'utente (es. LiDAR regionale 1-2 m), ritagliato e riproiettato."""
    src = gdal.Open(path)
    if src is None:
        raise RuntimeError("impossibile aprire il file DEM %s" % path)
    if res is None:
        gt = src.GetGeoTransform()
        _srs = osr.SpatialReference(wkt=src.GetProjection()) if src.GetProjection() else None
        if _srs is not None and _srs.IsGeographic():
            res = 5.0                                  # DEM in gradi (es. 1"): ricampionato a 5 m
        else:
            res = max(1.0, abs(gt[1])) if abs(gt[1]) > 0.5 else 1.0
    src = None
    ds = gdal.Warp(out_tif, path, dstSRS=crs.authid(), outputBounds=(extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum()),
                   xRes=res, yRes=res, resampleAlg="bilinear", format="GTiff", creationOptions=["COMPRESS=DEFLATE"])
    if ds is None:
        raise RuntimeError("ritaglio del DEM non riuscito")
    b = ds.GetRasterBand(1)
    mn, mx = b.ComputeRasterMinMax(False)
    ds = None
    return out_tif, (mn, mx)


LOCAL_DEM_DIR = os.path.join(os.path.expanduser("~"), "SpeleoMap", "DEM")


def local_dem(extent, crs, folder=None):
    """Cerca nella cartella dei DEM locali (~/SpeleoMap/DEM, anche nelle sottocartelle) un raster che copra tutta
    l'estensione; se ce ne sono piu' d'uno sceglie quello a risoluzione piu' fine. Restituisce il percorso o None."""
    folder = folder or LOCAL_DEM_DIR
    if not os.path.isdir(folder):
        return None
    best = None
    for dp, _, files in os.walk(folder):
        for fn in files:
            if os.path.splitext(fn)[1].lower() not in (".tif", ".tiff", ".vrt", ".img", ".asc"):
                continue
            p = os.path.join(dp, fn)
            try:
                ds = gdal.Open(p)
                if ds is None or not ds.GetProjection():
                    continue
                gt = ds.GetGeoTransform()
                x0, y1 = gt[0], gt[3]
                x1, y0 = x0 + gt[1] * ds.RasterXSize, y1 + gt[5] * ds.RasterYSize
                rcrs = QgsCoordinateReferenceSystem()
                rcrs.createFromWkt(ds.GetProjection())
                bb = QgsCoordinateTransform(crs, rcrs, QgsProject.instance()).transformBoundingBox(extent)
                if bb.xMinimum() >= min(x0, x1) and bb.xMaximum() <= max(x0, x1) and \
                        bb.yMinimum() >= min(y0, y1) and bb.yMaximum() <= max(y0, y1):
                    res = abs(gt[1]) * (111000.0 if rcrs.isGeographic() else 1.0)
                    if best is None or res < best[0]:
                        best = (res, p)
                ds = None
            except Exception:
                continue
    return best[1] if best else None


DEM_CREDITS = {
    "locale": "DEM locale (cartella SpeleoMap/DEM)",
    "copernicus": "DEM Copernicus GLO-30 (DSM 30 m) (c) DLR/Airbus/ESA",
    "tinitaly": "DEM TINITALY 1.1 (10 m) INGV, CC BY 4.0 - Tarquini et al. 2023",
    "file": "DEM da file fornito dal rilevatore",
    "lidar": "DTM LiDAR 1 m MASE - Piano Straordinario di Telerilevamento, CC BY 4.0 (TINITALY dove manca)",
}

# ------------------------------------------------------------------ LiDAR nazionale (MASE, Piano Straordinario di Telerilevamento)
LIDAR_PORTAL = "https://sim.mase.gov.it/portalediaccesso/mappe/#/viewer/new"
_LIDAR_WFS = "http://wms.pcn.minambiente.it/ogc?map=/ms_ogc/wfs/QU_Lidar.map&SERVICE=WFS&VERSION=1.0.0&REQUEST=GetFeature"


def lidar_index(extent, crs):
    """Tavole LiDAR (1 m e 2 m) che coprono l'estensione, dal quadro d'unione pubblico del MASE.
    Restituisce [dict(tavola, res, regione, data, rilievo, bbox=(lon0, lat0, lon1, lat1))]."""
    import re
    from qgis.core import QgsBlockingNetworkRequest
    from qgis.PyQt.QtNetwork import QNetworkRequest
    from qgis.PyQt.QtCore import QUrl
    ll = QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance()).transformBoundingBox(extent)
    out = []
    for lay in ("EL.QUADRO.UNIONE.LIDAR.0101", "EL.QUADRO.UNIONE.LIDAR.0202"):
        u = _LIDAR_WFS + "&TYPENAME=%s&BBOX=%.6f,%.6f,%.6f,%.6f&MAXFEATURES=200" % (lay, ll.xMinimum(), ll.yMinimum(), ll.xMaximum(), ll.yMaximum())
        rq = QgsBlockingNetworkRequest()
        if rq.get(QNetworkRequest(QUrl(u))) != 0:
            raise RuntimeError("quadro d'unione LiDAR non raggiungibile")
        txt = bytes(rq.reply().content()).decode("latin-1", "ignore")
        for m in re.finditer(r"<gml:featureMember>(.*?)</gml:featureMember>", txt, re.S):
            f = m.group(1)

            def val(tag):
                v = re.search(r"<ms:%s>([^<]*)</ms:%s>" % (tag, tag), f)
                return v.group(1).strip() if v else ""
            box = re.search(r"<gml:coordinates>([\d.\-]+),([\d.\-]+) ([\d.\-]+),([\d.\-]+)</gml:coordinates>", f)
            d = val("data")
            out.append(dict(tavola=val("tavola"), res=float(val("risoluzion") or 0) or (1.0 if lay.endswith("0101") else 2.0),
                            regione=val("regione"), rilievo=val("rilievo"),
                            data=("20%s-%s-%s" % (d[0:2], d[2:4], d[4:6])) if len(d) == 6 else d,
                            bbox=tuple(float(v) for v in box.groups()) if box else None))
        if out:
            break          # se c'e' il passo da 1 m non servono le tavole da 2 m
    return out


def lidar_report(tiles, path, name=""):
    """File di testo con le tavole LiDAR da scaricare e le istruzioni."""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("DTM LiDAR del Ministero dell'Ambiente (MASE) per %s\n\n" % (name or "il rilievo"))
        if not tiles:
            fh.write("Nessuna tavola LiDAR copre quest'area: si usa TINITALY 10 m.\n")
            return path
        fh.write("Tavole che coprono l'area (passo %.0f m):\n" % tiles[0]["res"])
        for t in tiles:
            fh.write("  %s  - %s, rilievo del %s\n" % (t["tavola"], t["regione"], t["data"]))
        fh.write("\nCome scaricarle (gratis, licenza CC BY 4.0):\n"
                 "  1. apri %s\n"
                 "  2. 'Seleziona area' > 'Aggiungi livello' > catalogo LIDAR > griglia 1x1\n"
                 "  3. disegna un poligono attorno alla grotta (doppio clic per chiudere)\n"
                 "  4. 'Aggiungi dati al carrello' > nel carrello scegli DTM > 'Scarica tutto'\n"
                 "  5. nel plugin, scheda Terreno: DEM = LiDAR 1 m e indica lo ZIP (o la cartella) scaricato.\n" % LIDAR_PORTAL)
    return path


def lidar_dem(src, extent, crs, out_tif, res=1.0, fill=None):
    """DTM dal LiDAR scaricato dal portale PST (ZIP, cartella o singolo GeoTIFF), unito, ritagliato e
    riproiettato sul rilievo. 'fill' (GeoTIFF gia' nel sistema del rilievo, es. TINITALY) riempie i vuoti."""
    import glob
    import zipfile
    src = (src or "").strip()
    if not src or not os.path.exists(src):
        raise RuntimeError("indica lo ZIP o la cartella del LiDAR scaricato dal portale")
    work = src
    if src.lower().endswith(".zip"):
        work = os.path.join(os.path.dirname(out_tif), "_lidar_" + os.path.splitext(os.path.basename(src))[0])
        if not os.path.isdir(work):
            with zipfile.ZipFile(src) as z:
                z.extractall(work)
    if os.path.isdir(work):
        tifs = [p for p in glob.glob(os.path.join(work, "**", "*.tif*"), recursive=True)]
        # gli ZIP del portale contengono anche altri file: si tengono i DTM (non i DSM)
        dtm = [p for p in tifs if "dtm" in os.path.basename(p).lower()]
        tifs = dtm or [p for p in tifs if "dsm" not in os.path.basename(p).lower()] or tifs
    else:
        tifs = [work]
    if not tifs:
        raise RuntimeError("nessun GeoTIFF nel LiDAR indicato")
    vrt = out_tif + ".lidar.vrt"
    gdal.BuildVRT(vrt, tifs)
    tmp = out_tif + ".lidar.tif"
    b = (extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum())
    ds = gdal.Warp(tmp, vrt, dstSRS=crs.authid(), outputBounds=b, xRes=res, yRes=res, resampleAlg="bilinear",
                   dstNodata=-9999, format="GTiff", creationOptions=["COMPRESS=DEFLATE"])
    if ds is None:
        raise RuntimeError("ritaglio del LiDAR non riuscito")
    arr = ds.GetRasterBand(1).ReadAsArray()
    ds = None
    cover = float((arr > -9000).mean()) if arr.size else 0.0
    if cover < 0.02:
        raise RuntimeError("le tavole LiDAR indicate non coprono l'area del rilievo")
    if fill and os.path.exists(fill) and cover < 0.999:
        # LiDAR sopra, TINITALY sotto dove il LiDAR manca
        vrt2 = out_tif + ".fill.vrt"
        gdal.BuildVRT(vrt2, [fill, tmp], resolution="user", xRes=res, yRes=res, outputBounds=b, srcNodata=-9999, VRTNodata=-9999)
        ds = gdal.Translate(out_tif, vrt2, format="GTiff", creationOptions=["COMPRESS=DEFLATE"])
    else:
        ds = gdal.Translate(out_tif, tmp, format="GTiff", creationOptions=["COMPRESS=DEFLATE"])
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(-9999)
    mn, mx = band.ComputeRasterMinMax(False)
    ds = None
    for p in (vrt, tmp, out_tif + ".fill.vrt"):
        try:
            os.remove(p)
        except OSError:
            pass
    return out_tif, (mn, mx), cover


def catasto_it_layer():
    """Particelle catastali (Agenzia delle Entrate, WMS INSPIRE) - visibili a scala grande."""
    uri = ("contextualWMSLegend=0&crs=EPSG:4258&dpiMode=7&format=image/png&layers=CP.CadastralParcel&styles="
           "&url=https://wms.cartografia.agenziaentrate.gov.it/inspire/wms/ows01.php")
    lyr = QgsRasterLayer(uri, "particelle catastali (AdE)", "wms")
    if not lyr.isValid():
        return None
    try:
        lyr.renderer().setOpacity(0.45)
    except Exception:
        pass
    return lyr


def catasto_it_raster(extent, crs, out_tif, max_px=6000):
    """Particelle catastali (Agenzia delle Entrate) scaricate a pezzi (max 2048 px, limite del server) e unite
    in un GeoTIFF RGBA: fondo trasparente e bordi delle particelle in giallo, leggibili sull'ortofoto.
    Cosi' l'esportazione del PDF non dipende piu' dai tempi del server."""
    import numpy as np
    from qgis.core import QgsBlockingNetworkRequest
    from qgis.PyQt.QtNetwork import QNetworkRequest
    from qgis.PyQt.QtCore import QUrl
    c = QgsCoordinateTransform(crs, QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance()).transform(extent.center())
    zone = min(34, max(32, int((c.x() + 180) // 6) + 1))
    wcrs = QgsCoordinateReferenceSystem("EPSG:258%d" % zone)
    bb = QgsCoordinateTransform(crs, wcrs, QgsProject.instance()).transformBoundingBox(extent)
    w, h = bb.width(), bb.height()
    res = max(0.20, max(w, h) / float(max_px))
    W, H = int(math.ceil(w / res)), int(math.ceil(h / res))
    img = np.zeros((4, H, W), dtype=np.uint8)
    T = 2000
    url0 = ("https://wms.cartografia.agenziaentrate.gov.it/inspire/wms/ows01.php?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap"
            "&LAYERS=CP.CadastralParcel&STYLES=&CRS=EPSG:258%d&FORMAT=image/png&TRANSPARENT=TRUE" % zone)
    for r0 in range(0, H, T):
        for c0 in range(0, W, T):
            tw, th = min(T, W - c0), min(T, H - r0)
            x0 = bb.xMinimum() + c0 * res
            y1 = bb.yMaximum() - r0 * res
            u = url0 + "&BBOX=%.3f,%.3f,%.3f,%.3f&WIDTH=%d&HEIGHT=%d" % (x0, y1 - th * res, x0 + tw * res, y1, tw, th)
            rq = QgsBlockingNetworkRequest()
            err = rq.get(QNetworkRequest(QUrl(u)))
            data = bytes(rq.reply().content())
            if err != 0 or data[:4] != b"\x89PNG":
                raise RuntimeError("il server del catasto non ha risposto (%s)" % data[:80])
            vm = "/vsimem/smg_cat_%d_%d.png" % (r0, c0)
            gdal.FileFromMemBuffer(vm, data)
            ds = gdal.Open(vm)
            a = ds.ReadAsArray()
            ds = None
            gdal.Unlink(vm)
            if a.ndim == 2:
                a = np.stack([a, a, a, np.full_like(a, 255)])
            elif a.shape[0] == 3:
                a = np.concatenate([a, np.full((1,) + a.shape[1:], 255, dtype=a.dtype)])
            img[:, r0:r0 + th, c0:c0 + tw] = a[:4, :th, :tw]
    rr, gg, bl, al = [img[i].astype(np.int32) for i in range(4)]
    line = (al > 0) & (rr + gg + bl < 300)
    red = (al > 0) & (rr > 170) & (gg < 120) & (bl < 120)
    # bordi ingrossati di 1 pixel per la stampa
    thick = line.copy()
    thick[1:, :] |= line[:-1, :]
    thick[:, 1:] |= line[:, :-1]
    out = np.zeros_like(img)
    out[0][thick], out[1][thick], out[2][thick], out[3][thick] = 255, 215, 0, 255
    out[0][red], out[3][red] = 230, 255
    drv = gdal.GetDriverByName("GTiff")
    ds = drv.Create(out_tif, W, H, 4, gdal.GDT_Byte, ["COMPRESS=DEFLATE", "PHOTOMETRIC=RGB", "ALPHA=YES"])
    ds.SetGeoTransform((bb.xMinimum(), res, 0, bb.yMaximum(), 0, -res))
    sr = osr.SpatialReference()
    sr.ImportFromEPSG(25800 + zone)
    ds.SetProjection(sr.ExportToWkt())
    for i in range(4):
        ds.GetRasterBand(i + 1).WriteArray(out[i])
    ds.GetRasterBand(4).SetColorInterpretation(gdal.GCI_AlphaBand)
    ds = None
    lyr = QgsRasterLayer(out_tif, "particelle catastali (AdE)")
    return lyr if lyr.isValid() else None


def catasto_legend_layer(crs):
    """Layer vuoto usato solo per la voce di legenda delle particelle (linea gialla)."""
    vl = QgsVectorLayer("LineString", "particelle catastali (legenda)", "memory")
    vl.setCrs(crs)
    vl.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple({"line_color": "255,215,0", "line_width": "0.35"})))
    return vl
