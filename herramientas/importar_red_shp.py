"""
Importa la red de media tensión de un operador entregada como shapefile de líneas (por ejemplo los ZIP de
Air-e con LINEAS_MT de Atlántico, Magdalena y La Guajira) y la deja en datos/red_or/<or>/filas.json, que
motor/red_complementaria.py une a la red del evaluador. Un elemento por circuito (todos sus tramos).

    python herramientas/importar_red_shp.py aire "Air-e" datos/red_or/aire/zip1 datos/red_or/aire/zip2 datos/red_or/aire/zip3
    python motor/red_complementaria.py

Cada carpeta debe traer el .shp, .shx, .dbf y .prj. La proyección se toma del .prj: MAGNA-SIRGAS origen
Bogotá, Oeste, Este Central, Este-Este u Origen Nacional (CTM12), o coordenadas geográficas.
Campos del circuito que se reconocen: ID_CIRCUIT / NOMB_CIRCU / NIVEL_TENS (Air-e), o CIRCUITO / NOMBRE / TENSION.
"""
import json
import os
import struct
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "motor"))
import proyeccion as P  # noqa: E402


def origen_de_prj(ruta):
    if not os.path.isfile(ruta):
        raise SystemExit(f"Falta {ruta}: sin él no se sabe la proyección.")
    t = open(ruta, encoding="latin1").read()
    if t.startswith("GEOGCS"):
        return None
    lon0 = float(t.split('"Central_Meridian",')[1].split("]")[0])
    fe = float(t.split('"False_Easting",')[1].split("]")[0])
    for k, (la0, lo0, k0, e0, n0) in P.ORIGENES.items():
        if abs(lo0 - lon0) < 1e-6 and abs(e0 - fe) < 1:
            return k
    raise SystemExit(f"Proyección no reconocida en {ruta} (meridiano central {lon0}).")


def leer_dbf(ruta, enc):
    with open(ruta, "rb") as f:
        n, hl, rl = struct.unpack("<IHH", f.read(32)[4:12])
        campos, off = [], 1
        while True:
            b = f.read(32)
            if b[:1] == b"\r":
                break
            campos.append((b[:11].split(b"\0")[0].decode("latin1"), off, b[16]))
            off += b[16]
        f.seek(hl)
        for _ in range(n):
            r = f.read(rl)
            yield None if r[:1] == b"*" else {nm: r[o:o + ln].decode(enc, "replace").strip() for nm, o, ln in campos}


def leer_lineas(ruta):
    """Polilíneas (tipo 3 o 13) del .shp, en el orden de los registros."""
    with open(ruta, "rb") as f:
        f.seek(100)
        while True:
            cab = f.read(8)
            if len(cab) < 8:
                break
            largo = struct.unpack(">i", cab[4:8])[0] * 2
            d = f.read(largo)
            tipo = struct.unpack("<i", d[:4])[0]
            if tipo not in (3, 13, 23):
                yield []
                continue
            npartes, npts = struct.unpack("<2i", d[36:44])
            partes = list(struct.unpack(f"<{npartes}i", d[44:44 + 4 * npartes])) + [npts]
            xy = struct.unpack(f"<{2 * npts}d", d[44 + 4 * npartes:44 + 4 * npartes + 16 * npts])
            yield [[(xy[2 * i], xy[2 * i + 1]) for i in range(partes[k], partes[k + 1])] for k in range(npartes)]


def campo(a, *nombres):
    for n in nombres:
        if a.get(n) not in (None, ""):
            return a[n]
    return ""


def main():
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    clave, nombre_or, carpetas = sys.argv[1].lower(), sys.argv[2], sys.argv[3:]
    capa = f"{clave.upper()}_Circuitos_MT"
    fuente = f"Red MT {nombre_or} (shapefile entregado al evaluador, {time.strftime('%Y-%m-%d')})"
    circ = {}
    for carpeta in carpetas:
        shp = next((os.path.join(carpeta, x) for x in os.listdir(carpeta) if x.lower().endswith(".shp")), None)
        if not shp:
            raise SystemExit(f"No hay .shp en {carpeta}")
        base = shp[:-4]
        enc = "utf-8" if os.path.isfile(base + ".cpg") and "UTF" in open(base + ".cpg").read().upper() else "latin1"
        origen = origen_de_prj(base + ".prj")
        n = 0
        for a, partes in zip(leer_dbf(base + ".dbf", enc), leer_lineas(shp)):
            if a is None or not partes:
                continue
            paths = []
            for parte in partes:
                pts = [P.a_wgs84(x, y, origen) if origen else (x, y) for x, y in parte]
                pts = [[round(lon, 6), round(lat, 6)] for lon, lat in pts if -80 < lon < -66 and -5 < lat < 14]
                if len(pts) >= 2:
                    paths.append(pts)
            if not paths:
                continue
            idc = campo(a, "ID_CIRCUIT", "CIRCUITO", "COD_CIRCUI")
            nom = campo(a, "NOMB_CIRCU", "NOMBRE", "NOMBRE_CIR") or idc or "(sin circuito)"
            c = circ.setdefault((nom, idc), dict(paths=[], largo=0.0, kv={}, prop={}, area=set()))
            c["paths"] += paths
            try:
                c["largo"] += float(campo(a, "Shape_Leng", "LONGITUD") or 0)
            except ValueError:
                pass
            kv = campo(a, "NIVEL_TENS", "TENSION", "KV")
            if kv:
                c["kv"][kv] = c["kv"].get(kv, 0) + 1
            pr = campo(a, "NOMBRE_PRO")
            if pr:
                c["prop"][pr] = c["prop"].get(pr, 0) + 1
            if campo(a, "TIPO_AREA"):
                c["area"].add(a["TIPO_AREA"])
            n += 1
        print(f"{carpeta}: {n} tramos (proyección {origen or 'geográficas'})")
    filas = []
    for (nom, idc), c in circ.items():
        xs = [p[0] for path in c["paths"] for p in path]
        ys = [p[1] for path in c["paths"] for p in path]
        tot = sum(c["prop"].values())
        at = {"Circuito": nom, "Código del circuito": idc, "Operador": nombre_or,
              "Tensión (kV)": max(c["kv"], key=c["kv"].get) if c["kv"] else None, "Tramos MT": len(c["paths"]),
              "Longitud MT": f"{c['largo'] / 1000:.1f} km" if c["largo"] else None,
              "Tramos de particulares": f"{100 * c['prop'].get('PARTICULAR', 0) / tot:.0f} %" if tot else None,
              "Zona": ", ".join({"R": "rural", "U": "urbana"}.get(x, x) for x in sorted(c["area"])) or None,
              "Fuente": fuente}
        filas.append(["RED_ELECTRICA", capa, nom, json.dumps({k: v for k, v in at.items() if v not in (None, "")}, ensure_ascii=False),
                      "LineString", json.dumps(c["paths"]), min(xs), min(ys), max(xs), max(ys)])
    destino = os.path.join(BASE, "datos", "red_or", clave, "filas.json")
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    with open(destino, "w", encoding="utf-8") as f:
        json.dump(filas, f, ensure_ascii=False)
    print(f"{len(filas)} circuitos -> {destino}")


if __name__ == "__main__":
    main()
