"""
Extrae la hidrografia de la Base de datos vectorial basica de Colombia 1:100.000 (IGAC, 2022)
desde su servicio ArcGIS y la guarda como shapefiles (WGS84), sin librerias externas.

    python extraer_hidrografia_100k.py <carpeta_destino> [id_capa ...]

Servicio: https://mapas2.igac.gov.co/server/rest/services/carto/carto100000colombia2019/MapServer
Pide por rangos de OBJECTID (no por offset, que en capas grandes es lento), reintenta ante
errores 503 y parte el rango en dos si el servidor corta la respuesta. Es reanudable:
cada capa terminada queda marcada en _progreso.json y no se vuelve a pedir.
"""
import concurrent.futures as cf
import datetime
import json
import os
import struct
import sys
import time
import urllib.parse
import urllib.request

SERVICIO = "https://mapas2.igac.gov.co/server/rest/services/carto/carto100000colombia2019/MapServer"
UA = {"User-Agent": "EvaluadorGD/1.0 (extraccion datos abiertos IGAC)"}

CAPAS = {
    24: "drenaje_sencillo",            # detalle 1:100k (~427 mil lineas)
    25: "drenaje_sencillo_principal",  # version generalizada para escalas pequenas
    26: "canal_sencillo",
    27: "madrevieja_l",
    35: "drenaje_doble",
    36: "drenaje_doble_principal",
    37: "canal_doble",
    38: "madrevieja_r",
    39: "laguna",
    40: "jaguey_r",
    41: "humedal",
    42: "embalse",
    43: "morichal",
    44: "cienaga",
    45: "manglar",
    46: "pantano",
    47: "otros_cuerpos_agua",
    48: "isla",
    34: "banco_arena",
    23: "raudal_rapido",
    20: "linea_costera",
}
# (OBJECTID maximo, elementos) consultados el 24-sep-2026, por si la estadistica agota el tiempo
# (para 25, 35 y 36 el maximo es una cota holgada: los rangos vacios se descartan rapido)
CONOCIDOS = {24: (1015760, 426947), 25: (1015760, 12432), 35: (60000, 2622), 36: (60000, 253)}
# poligonos de rios dobles son geometrias pesadas: rangos mas cortos para no agotar el tiempo
PASO_CAPA = {35: 400, 36: 400, 24: 2000}
OMITIR = {"SHAPE", "SHAPE_Length", "SHAPE_Area", "GLOBALID", "RULEID", "OVERRIDE", "SYMBOL"}
PRJ_WGS84 = ('GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],'
             'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]')


def _get(url, params, intentos=8):
    q = url + "?" + urllib.parse.urlencode(params)
    for i in range(1, intentos + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(q, headers=UA), timeout=180) as r:
                j = json.load(r)
            if "error" in j:
                raise RuntimeError(j["error"].get("message"))
            return j
        except Exception as e:
            if i == intentos:
                raise
            time.sleep(min(60, 4 * i))
            ultimo = e  # noqa: F841


# --------------------------------------------------------------------------
# Escritor de shapefile (polilinea tipo 3 / poligono tipo 5) + DBF
# --------------------------------------------------------------------------

class Shapefile:
    def __init__(self, base, tipo, campos):
        self.base, self.tipo, self.campos = base, tipo, campos  # campos: [(nombre, 'C'|'N', largo)]
        self.shp = open(base + ".shp.tmp", "wb")
        self.shx = open(base + ".shx.tmp", "wb")
        self.dbf = open(base + ".dbf.tmp", "wb")
        for f in (self.shp, self.shx):
            f.write(b"\0" * 100)
        self._dbf_header(0)
        self.n = 0
        self.bbox = [float("inf"), float("inf"), float("-inf"), float("-inf")]

    def _dbf_header(self, n):
        largo_reg = 1 + sum(c[2] for c in self.campos)
        largo_hdr = 32 + 32 * len(self.campos) + 1
        hoy = datetime.date.today()
        h = struct.pack("<BBBBIHH20x", 3, hoy.year - 1900, hoy.month, hoy.day, n, largo_hdr, largo_reg)
        for nombre, t, largo in self.campos:
            h += struct.pack("<11sc4xBB14x", nombre.encode("ascii")[:10], t.encode(), largo, 0)
        self.dbf.seek(0)
        self.dbf.write(h + b"\r")

    def agregar(self, partes, atributos):
        pts = [p for parte in partes for p in parte]
        if not pts:
            return
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        bb = (min(xs), min(ys), max(xs), max(ys))
        self.bbox = [min(self.bbox[0], bb[0]), min(self.bbox[1], bb[1]),
                     max(self.bbox[2], bb[2]), max(self.bbox[3], bb[3])]
        cont = struct.pack("<i4dii", self.tipo, *bb, len(partes), len(pts))
        idx, acum = [], 0
        for parte in partes:
            idx.append(acum)
            acum += len(parte)
        cont += struct.pack(f"<{len(idx)}i", *idx)
        cont += struct.pack(f"<{2 * len(pts)}d", *[c for p in pts for c in p[:2]])
        self.n += 1
        off = self.shp.tell()
        self.shp.write(struct.pack(">ii", self.n, len(cont) // 2) + cont)
        self.shx.write(struct.pack(">ii", off // 2, len(cont) // 2))
        reg = b" "
        for nombre, t, largo in self.campos:
            v = atributos.get(nombre)
            s = "" if v is None else str(v)
            b = s.encode("utf-8")[:largo]
            while True:  # no cortar un caracter UTF-8 a la mitad
                try:
                    b.decode("utf-8")
                    break
                except UnicodeDecodeError:
                    b = b[:-1]
            reg += b.rjust(largo) if t == "N" else b.ljust(largo)
        self.dbf.write(reg)

    def cerrar(self):
        for f in (self.shp, self.shx):
            largo = f.seek(0, 2)
            bb = self.bbox if self.n else [0, 0, 0, 0]
            f.seek(0)
            f.write(struct.pack(">i20xi", 9994, largo // 2) + struct.pack("<ii4d4d", 1000, self.tipo, *bb, 0, 0, 0, 0))
            f.close()
        self.dbf.seek(0, 2)
        self.dbf.write(b"\x1a")
        self._dbf_header(self.n)
        self.dbf.close()
        for ext in ("shp", "shx", "dbf"):
            os.replace(f"{self.base}.{ext}.tmp", f"{self.base}.{ext}")
        with open(self.base + ".prj", "w") as f:
            f.write(PRJ_WGS84)
        with open(self.base + ".cpg", "w") as f:
            f.write("UTF-8")


# --------------------------------------------------------------------------
# Extraccion por rangos de OBJECTID
# --------------------------------------------------------------------------

def _rango(capa, a, b):
    """Features con a < OBJECTID <= b. Si el servidor corta o se demora, parte el rango en dos."""
    try:
        j = _get(f"{SERVICIO}/{capa}/query", dict(
            where=f"OBJECTID > {a} AND OBJECTID <= {b}", outFields="*", returnGeometry="true",
            outSR=4326, geometryPrecision=7, f="json"), intentos=4 if b - a > 50 else 8)
    except Exception:
        if b - a <= 50:
            raise
        m = (a + b) // 2
        return _rango(capa, a, m) + _rango(capa, m, b)
    feats = j.get("features", [])
    if j.get("exceededTransferLimit") and b - a > 1:
        m = (a + b) // 2
        return _rango(capa, a, m) + _rango(capa, m, b)
    return feats


def _fecha(v):
    if isinstance(v, (int, float)) and v > 1e11:
        return datetime.datetime.utcfromtimestamp(v / 1000).strftime("%Y-%m-%d")
    return v


def extraer_capa(capa, nombre, destino, hilos=4, paso=3000):
    info = _get(f"{SERVICIO}/{capa}", dict(f="json"))
    tipo = {"esriGeometryPolyline": 3, "esriGeometryPolygon": 5}[info["geometryType"]]
    campos_srv = [f for f in info["fields"] if f["name"] not in OMITIR]
    campos = []
    for f in campos_srv:
        nm = f["name"][:10]
        if f["type"] in ("esriFieldTypeOID", "esriFieldTypeInteger", "esriFieldTypeSmallInteger"):
            campos.append((nm, "N", 11))
        elif f["type"] == "esriFieldTypeDate":
            campos.append((nm, "C", 10))
        else:
            campos.append((nm, "C", min(254, max(20, f.get("length") or 50))))
    mapa = {f["name"][:10]: f["name"] for f in campos_srv}
    try:
        stats = _get(f"{SERVICIO}/{capa}/query", dict(where="1=1", f="json", outStatistics=json.dumps([
            {"statisticType": "max", "onStatisticField": "OBJECTID", "outStatisticFieldName": "mx"},
            {"statisticType": "count", "onStatisticField": "OBJECTID", "outStatisticFieldName": "n"}])), intentos=3)
        mx, total = stats["features"][0]["attributes"]["mx"], stats["features"][0]["attributes"]["n"]
    except Exception:
        # en capas grandes la estadistica agota el tiempo del servidor: usar valores ya consultados
        if capa not in CONOCIDOS:
            raise
        mx, total = CONOCIDOS[capa]
        print(f"  {nombre}: el servidor no respondió el conteo; uso OBJECTID max {mx:,} y {total:,} elementos conocidos", flush=True)
    if capa in PASO_CAPA:
        paso = PASO_CAPA[capa]
    rangos = [(a, min(a + paso, mx)) for a in range(0, mx, paso)]
    shp = Shapefile(os.path.join(destino, nombre), tipo, campos)
    t0, hechos = time.time(), 0
    with cf.ThreadPoolExecutor(hilos) as ex:
        for i in range(0, len(rangos), hilos * 4):  # lotes en orden para escribir secuencial
            lote = rangos[i:i + hilos * 4]
            for feats in ex.map(lambda r: _rango(capa, *r), lote):
                for ft in feats:
                    g = ft.get("geometry") or {}
                    partes = g.get("paths") or g.get("rings") or []
                    at = {nm: _fecha(ft["attributes"].get(orig)) for nm, orig in mapa.items()}
                    shp.agregar(partes, at)
                    hechos += 1
            print(f"  {nombre}: {hechos:,}/{total:,} ({time.time() - t0:,.0f} s)", flush=True)
    shp.cerrar()
    return dict(capa=capa, nombre=nombre, esperadas=total, escritas=shp.n)


def main():
    destino = sys.argv[1]
    capas = [int(c) for c in sys.argv[2:]] or list(CAPAS)
    os.makedirs(destino, exist_ok=True)
    prog_p = os.path.join(destino, "_progreso.json")
    prog = json.load(open(prog_p, encoding="utf-8")) if os.path.exists(prog_p) else {}
    for c in capas:
        if str(c) in prog:
            print(f"ya extraida: {CAPAS[c]}", flush=True)
            continue
        print(f"extrayendo capa {c} ({CAPAS[c]})…", flush=True)
        try:
            r = extraer_capa(c, CAPAS[c], destino)
        except Exception as e:
            print(f"ERROR en capa {c} ({CAPAS[c]}): {type(e).__name__}: {e}", flush=True)
            continue
        prog[str(c)] = dict(r, fecha=datetime.datetime.now().isoformat(timespec="seconds"))
        json.dump(prog, open(prog_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        estado = "OK" if r["escritas"] == r["esperadas"] else "DIFERENCIA"
        print(f"{estado}: {CAPAS[c]} {r['escritas']:,} de {r['esperadas']:,}", flush=True)
    print("FIN", flush=True)


if __name__ == "__main__":
    main()
