"""
Descarga la red de Energía de Pereira (EEP) en Pereira y Cartago desde la API pública de su portal de solicitudes
(https://solicitudes.eep.com.co, «Consultar la disponibilidad de la red»; API eepsolicitudescregapi.syspotec.co,
consultas GET sin sesión ni captcha):

  1. Transformadores de Pereira con su capacidad: malla cada ~1 km con InfoActivos/GetByRadio (radio ~700 m, ~1 s).
  2. Circuitos de Pereira: ficha de un transformador que aún no quede junto a un circuito bajado (CregTransformador/GetById,
     trae su circuito y la disponibilidad AGPE del circuito) y, si el circuito es nuevo, el circuito completo
     (InfoActivos/GetInfoCregGeo: tramos MT y subestación).
  3. Circuitos de Cartago: sus códigos siguen el patrón C<subestación>L<línea>; se prueban C1L1…C9L9 (los que no existen
     responden 400 al instante). La consulta «por radio» de la API (GetInfoCregGeoRadio) tarda ~12 s y no se usa.

Una petición a la vez con pausa (--pausa, 1 s por defecto), se detiene tras 3 fallos seguidos y guarda todo en
datos/red_or/eep/cache.json: se puede interrumpir y reanudar. Al final arma datos/red_or/eep/filas.json para
motor/red_complementaria.py.

    python herramientas/descargar_red_eep.py
    python motor/red_complementaria.py
"""
import json
import math
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import descargar_predios_acc as acc  # noqa: E402  (_poligono_municipio, gis, UA)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://eepsolicitudescregapi.syspotec.co/api/"
DIR = os.path.join(BASE, "datos", "red_or", "eep")
CACHE = os.path.join(DIR, "cache.json")
PASO_TRAFO = 0.009    # ~1 km (GetByRadio cubre ~700 m de radio)


class Detenido(Exception):
    pass


def pedir(ruta, est, pausa):
    espera = 20
    while True:
        dt = time.time() - est["ultima"]
        if dt < pausa:
            time.sleep(pausa - dt)
        est["ultima"] = time.time()
        est["pedidas"] += 1
        try:
            req = urllib.request.Request(API + ruta, headers={"User-Agent": acc.UA, "Accept": "application/json",
                                                               "Referer": "https://solicitudes.eep.com.co/"})
            with urllib.request.urlopen(req, timeout=90) as r:
                b = r.read()
            est["fallos"] = 0
            return json.loads(b).get("data")
        except urllib.error.HTTPError as e:
            if e.code == 400:  # la API responde 400 cuando no hay datos o el parámetro no aplica
                est["fallos"] = 0
                return None
            err = e
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError) as e:
            err = e
        est["fallos"] += 1
        if est["fallos"] >= 3:
            raise Detenido(f"3 fallos seguidos ({str(err)[:80]}); se detiene para no forzar el servicio.")
        print(f"    aviso: {str(err)[:80]}; esperando {espera} s", flush=True)
        time.sleep(espera)
        espera *= 2


def malla(codigo, paso):
    anillos, bb = acc._poligono_municipio(codigo)
    pts = []
    y = bb[1] + paso / 2
    while y < bb[3]:
        x = bb[0] + paso / 2
        while x < bb[2]:
            if any(acc.gis._pir(x, y, a) for a in anillos):
                pts.append((round(x, 5), round(y, 5)))
            x += paso
        y += paso
    return pts


def _cargar():
    if os.path.isfile(CACHE):
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    return dict(circuitos={}, hechos=[], trafos={}, trafo_hechos=[], fichas={})


def _guardar(c):
    os.makedirs(DIR, exist_ok=True)
    tmp = CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False)
    os.replace(tmp, CACHE)


def _coords_circuito(feats):
    out = []
    for f in feats:
        g = f["geometry"]
        out += g["coordinates"] if g["type"] == "LineString" else [g["coordinates"]] if g["type"] == "Point" else []
    return out


def _bajar_circuito(c, emp, codigo, municipio, est, pausa):
    dc = pedir(f"InfoActivos/GetInfoCregGeo?Empresa={emp}&TipoSolicitud=2&Option=1&Circuito={urllib.parse.quote(codigo)}", est, pausa)
    feats = [g for g in (dc or {}).get("features", []) if g["properties"].get("type") in ("tramos", "subestacion")]
    if feats:
        c["circuitos"][f"{emp}:{codigo}"] = dict(circuito=codigo, emp=emp, municipio=municipio, feats=feats)
        print(f"  circuito {codigo} ({municipio}): {len(feats)} elementos", flush=True)
    return feats


def fase_circuitos_pereira(c, est, pausa):
    """Recorre los transformadores: el que no quede a menos de 80 m de un circuito ya bajado revela uno nuevo."""
    asignar(c)
    revisados = set(c["hechos"])
    pend = [t for t in c["trafos"].values() if t["emp"] == 1 and t.get("lon") is not None]
    for i, t in enumerate(pend):
        if t.get("circ") or t["code"] in revisados:
            continue
        d = pedir(f"CregTransformador/GetById?Id={urllib.parse.quote(str(t['code']))}&TipoSolicitud=2&Empresa=1", est, pausa)
        revisados.add(t["code"])
        circ = (d or {}).get("circuito")
        if circ:
            k = f"1:{circ}"
            c["fichas"].setdefault(k, d)
            if k not in c["circuitos"]:
                _bajar_circuito(c, 1, circ, "Pereira", est, pausa)
                asignar(c)
            t["circ"] = t.get("circ") or (k if k in c["circuitos"] else None)
        c["hechos"] = sorted(revisados)
        if len(revisados) % 20 == 0:
            _guardar(c)
            print(f"  Pereira: transformador {i + 1}/{len(pend)}, {len(c['circuitos'])} circuitos, {est['pedidas']} peticiones", flush=True)
    _guardar(c)


def fase_circuitos_cartago(c, est, pausa):
    for s in range(1, 10):
        for l in range(1, 10):
            codigo = f"C{s}L{l}"
            if f"2:{codigo}" in c["circuitos"] or f"2:{codigo}" in c["hechos"]:
                continue
            _bajar_circuito(c, 2, codigo, "Cartago", est, pausa)
            c["hechos"].append(f"2:{codigo}")
        _guardar(c)


def fase_trafos(c, est, pausa):
    hechos = set(c["trafo_hechos"])
    pts = malla("66001", PASO_TRAFO)
    print(f"Pereira: {len(pts)} puntos de la malla de transformadores", flush=True)
    for i, (x, y) in enumerate(pts):
        clave = f"{x}:{y}"
        if clave in hechos:
            continue
        d = pedir(f"InfoActivos/GetByRadio?LatitudCentro={y}&LongitudCentro={x}", est, pausa)
        for t in (d or {}).get("transformadores", []) or []:
            r = c["trafos"].setdefault(f"1:{t['code']}", dict(code=t["code"], emp=1))
            r.update(lon=t["longitud"], lat=t["latitud"], kva=t.get("trftype"), pg=t.get("pg"))
        hechos.add(clave)
        c["trafo_hechos"] = sorted(hechos)
        if i % 25 == 0:
            _guardar(c)
            print(f"  transformadores: punto {i + 1}/{len(pts)}, {len(c['trafos'])} transformadores", flush=True)
    _guardar(c)


def _dist_m(x1, y1, x2, y2):
    return math.hypot((x2 - x1) * 111320 * math.cos(math.radians(y1)), (y2 - y1) * 110574)


def asignar(c):
    """Circuito de cada transformador: el del vértice MT más cercano (a menos de 80 m)."""
    celdas = {}
    for k, circ in c["circuitos"].items():
        for x, y in _coords_circuito(circ["feats"]):
            celdas.setdefault((int(x / 0.001), int(y / 0.001)), []).append((x, y, k))
    for t in c["trafos"].values():
        if t.get("lon") is None:
            continue
        cx, cy = int(t["lon"] / 0.001), int(t["lat"] / 0.001)
        mejor = None
        for i in (-1, 0, 1):
            for j in (-1, 0, 1):
                for x, y, k in celdas.get((cx + i, cy + j), []):
                    if not k.startswith(f"{t['emp']}:"):
                        continue
                    d = _dist_m(t["lon"], t["lat"], x, y)
                    if d < 80 and (not mejor or d < mejor[0]):
                        mejor = (d, k)
        t["circ"] = mejor[1] if mejor else None




def armar_filas(c):
    asignar(c)
    fecha = time.strftime("%Y-%m-%d")
    fuente = f"Portal de solicitudes de Energía de Pereira, disponibilidad de la red ({fecha})"
    filas = []

    def fila(capa, nombre, at, gt, geom):
        if gt == "Point":
            xs, ys = [geom[0]], [geom[1]]
        else:
            xs = [p[0] for path in geom for p in path]
            ys = [p[1] for path in geom for p in path]
        filas.append(["RED_ELECTRICA", capa, nombre, json.dumps({k: v for k, v in at.items() if v not in (None, "")}, ensure_ascii=False),
                      gt, json.dumps(geom), min(xs), min(ys), max(xs), max(ys)])

    kva_circ = {}
    for t in c["trafos"].values():
        if t.get("circ") and t.get("kva"):
            try:
                kva_circ[t["circ"]] = kva_circ.get(t["circ"], 0) + float(t["kva"])
            except ValueError:
                pass
    subs = {}
    for k, circ in c["circuitos"].items():
        paths = [[[round(x, 6), round(y, 6)] for x, y in f["geometry"]["coordinates"]]
                 for f in circ["feats"] if f["properties"].get("type") == "tramos" and f["geometry"]["type"] == "LineString"]
        paths = [p for p in paths if len(p) >= 2]
        for f in circ["feats"]:
            if f["properties"].get("type") == "subestacion" and f["geometry"]["type"] == "Point":
                subs.setdefault(f["properties"].get("code"), (f["geometry"]["coordinates"], circ["municipio"]))
        if not paths:
            continue
        fi = c["fichas"].get(k) or {}
        largo = sum(_dist_m(*p[i], *p[i + 1]) for p in paths for i in range(len(p) - 1))
        at = {"Circuito": circ["circuito"], "Operador": "Energía de Pereira", "Municipio": circ["municipio"],
              "Tensión (kV)": fi.get("volt_Nominal_Circ"), "Tramos MT": len(paths), "Longitud MT": f"{largo / 1000:.1f} km",
              "Capacidad del circuito (kVA)": fi.get("cap_Kva_Circ"), "Disponible AGPE en el circuito (kW)": fi.get("cap_Max_Disponible_Circ"),
              "AGPE instalada en el circuito (kW)": fi.get("generacion_Inst"), "Capacidad ocupada (kW)": fi.get("tot_Cap_Ocupada_Circ"),
              "Capacidad de transformadores (kVA)": round(kva_circ[k]) if k in kva_circ else None, "Fuente": fuente}
        fila("EEP_Circuitos_MT", circ["circuito"], at, "LineString", paths)
    for code, (xy, mun) in subs.items():
        fila("EEP_Subestaciones", str(code), {"Subestación": code, "Municipio": mun, "Fuente": fuente}, "Point", [round(xy[0], 6), round(xy[1], 6)])
    for t in c["trafos"].values():
        if t.get("lon") is None:
            continue
        circ = c["circuitos"].get(t.get("circ") or "", {}).get("circuito") or "(sin circuito)"
        at = {"Circuito": circ, "Transformador": t["code"], "Capacidad (kVA)": t.get("kva"), "Municipio": "Pereira" if t["emp"] == 1 else "Cartago",
              "Fuente": fuente}
        fila("EEP_Transformadores", circ, at, "Point", [round(t["lon"], 6), round(t["lat"], 6)])
    with open(os.path.join(DIR, "filas.json"), "w", encoding="utf-8") as f:
        json.dump(filas, f, ensure_ascii=False)
    n = {}
    for fl in filas:
        n[fl[1]] = n.get(fl[1], 0) + 1
    print(", ".join(f"{k}: {v}" for k, v in n.items()), "->", os.path.join(DIR, "filas.json"))


def main():
    pausa = float(sys.argv[sys.argv.index("--pausa") + 1]) if "--pausa" in sys.argv else 1.0
    est = dict(ultima=0.0, pedidas=0, fallos=0)
    c = _cargar()
    try:
        fase_trafos(c, est, pausa)
        fase_circuitos_pereira(c, est, pausa)
        fase_circuitos_cartago(c, est, pausa)
    except Detenido as e:
        _guardar(c)
        print("DETENIDO:", e)
        sys.exit(2)
    finally:
        print(f"{est['pedidas']} peticiones", flush=True)
    _guardar(c)
    armar_filas(c)


if __name__ == "__main__":
    main()
