"""
Puntos semilla para descubrir circuitos de EBSA en el visor GD-Connect por zona:
centroide de cada vereda (catastro IGAC R_VEREDA) y de la cabecera (U_MANZANA) de los municipios pedidos.

    python herramientas/semillas_ebsa.py 15407 15224 ...   -> datos/red_or/ebsa/semillas.json
    python herramientas/semillas_ebsa.py 15814 ... --salida semillas_grupo2.json
"""
import json
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "motor"))
import catastro  # noqa: E402
import gis  # noqa: E402
import proyeccion as P  # noqa: E402


def centroide(anillo):
    xs, ys = [p[0] for p in anillo], [p[1] for p in anillo]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def punto_interior(anillo):
    """Centroide si cae dentro; si no (vereda en forma de C), el vértice medio más cercano al centroide."""
    cx, cy = centroide(anillo)
    if gis._pir(cx, cy, anillo):
        return cx, cy
    return min(anillo, key=lambda p: (p[0] - cx) ** 2 + (p[1] - cy) ** 2)


def main(codigos, archivo="semillas.json"):
    salida = []
    for cod in codigos:
        geo = gis.geometria_lugar(cod)
        if not geo:
            print("sin geometría", cod)
            continue
        anillo = geo["coordinates"][0]
        xs, ys = [p[0] for p in anillo], [p[1] for p in anillo]
        bb = P.bbox_a_ctm12(min(xs), min(ys), max(xs), max(ys))
        nombre = catastro._nombre_municipio(cod)
        for capa, tipo in (("R_VEREDA", "vereda"), ("U_MANZANA", "cabecera")):
            c = catastro.capas().get(capa)
            pts = []
            for i, t, partes, a in c.leer(c.ids_en(*bb, limite=20000)):
                if str(a.get("codigo_mun", "")) != cod:
                    continue
                x, y = punto_interior(partes[0])
                lon, lat = P.a_wgs84(x, y)
                pts.append((lon, lat, a.get("NOMBRE") or a.get("CODIGO", "")))
            if tipo == "cabecera" and pts:  # un punto por cabecera: el centro de las manzanas
                lon = sum(p[0] for p in pts) / len(pts)
                lat = sum(p[1] for p in pts) / len(pts)
                pts = [(lon, lat, "Cabecera")]
            for lon, lat, n in pts:
                salida.append(dict(mun=cod, municipio=nombre, tipo=tipo, nombre=n, lon=round(lon, 6), lat=round(lat, 6)))
        # malla de respaldo: 1,5 km si el municipio no está en el catastro IGAC (otro gestor), 2 km en los demás
        if True:
            paso = (2000 if any(s["mun"] == cod for s in salida) else 1500) / 111320
            y = min(ys)
            while y <= max(ys):
                x = min(xs)
                while x <= max(xs):
                    if gis._pir(x, y, anillo):
                        salida.append(dict(mun=cod, municipio=nombre, tipo="malla", nombre="", lon=round(x, 6), lat=round(y, 6)))
                    x += paso
                y += paso
        print(f"{cod} {nombre}: {sum(1 for s in salida if s['mun'] == cod)} semillas")
    d = os.path.join(RAIZ, "datos", "red_or", "ebsa")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, archivo), "w", encoding="utf-8") as f:
        json.dump(salida, f, ensure_ascii=False)
    print("total", len(salida))


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--salida" in args:  # --salida semillas_grupo2.json
        k = args.index("--salida")
        main(args[:k] + args[k + 2:], args[k + 1])
    else:
        main(args)
