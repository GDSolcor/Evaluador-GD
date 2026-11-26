"""
Catastro publico del IGAC (corte 31-ago-2026) en el evaluador.

Fuente: datos/igac/catastro/CatastroPubliconAgosto2026/*.shp (MAGNA-SIRGAS / CTM12).
Solo cubre municipios donde el IGAC es gestor catastral.
"""
import json
import os
import re
import sqlite3

import bd
import catastro_acc
import proyeccion as P
from capas_shp import CapaShp, existe as existe_shp

BASE = os.path.dirname(os.path.abspath(__file__))
DIR = os.environ.get("CATASTRO_IGAC", os.path.join(BASE, "..", "datos", "igac", "catastro",
                                                     "CatastroPubliconAgosto2026"))
CORTE = "31-ago-2026"

CAPAS = {
    "R_TERRENO": dict(nombre="Predios rurales", color="#facc15", zoom=15),
    "U_TERRENO": dict(nombre="Predios urbanos", color="#fb923c", zoom=16),
    "R_VEREDA": dict(nombre="Veredas", color="#e879f9", zoom=11),
    "U_MANZANA": dict(nombre="Manzanas urbanas", color="#fdba74", zoom=15),
}

_capas = {}
_municipios = None


def capas():
    if not _capas:
        for k in CAPAS:
            base = os.path.join(DIR, k)
            if existe_shp(base):
                _capas[k] = CapaShp(base, crs="ctm12")
    return _capas


def disponible():
    return bool(capas())


def _nombre_municipio(codigo):
    """Codigo DANE -> nombre, desde la capa de municipios del GIS Solcor."""
    global _municipios
    if _municipios is None:
        _municipios = {}
        try:
            import gis
            if gis.disponible():
                for (aj,) in gis._db().execute("select attrs_json from features where dataset='MUNICIPIO'"):
                    a = json.loads(aj)
                    if len(str(a.get("codigo", ""))) == 5:
                        _municipios[str(a["codigo"])] = f"{a.get('nombre', '')}, {a.get('depto', '')}"
        except (ImportError, sqlite3.Error):
            pass
    return _municipios.get(str(codigo), "")


def catalogo():
    out = []
    for k, meta in CAPAS.items():
        c = capas().get(k)
        if c:
            out.append(dict(id=k, geometrias=c.n, indice=round(c.progreso, 3), **meta))
    return dict(disponible=bool(out), corte=CORTE, capas=out)


def _props(capa, a):
    p = {}
    if capa in ("R_TERRENO", "U_TERRENO"):
        cod = a.get("CODIGO", "")
        p["Número predial"] = cod
        if a.get("CODIGO_ANT"):
            p["Número predial anterior"] = a["CODIGO_ANT"]
        p["Municipio"] = _nombre_municipio(a.get("codigo_mun") or cod[:5]) or a.get("codigo_mun", "")
        if a.get("VEREDA_COD"):
            p["Vereda (código)"] = a["VEREDA_COD"]
        if a.get("MANZANA_CO"):
            p["Manzana (código)"] = a["MANZANA_CO"]
        area = a.get("SHAPE_Area")
        if isinstance(area, (int, float)):
            p["Área catastral"] = f"{area / 10000:,.4f} ha ({area:,.0f} m²)".replace(",", "X").replace(".", ",").replace("X", ".")
    elif capa == "R_VEREDA":
        p["Vereda"] = a.get("NOMBRE", "")
        p["Código"] = a.get("CODIGO", "")
        p["Municipio"] = _nombre_municipio(a.get("codigo_mun", "")) or a.get("codigo_mun", "")
    else:
        p["Código"] = a.get("CODIGO", "")
        p["Municipio"] = _nombre_municipio(a.get("codigo_mun", "")) or a.get("codigo_mun", "")
    p["Fuente"] = f"Catastro IGAC {CORTE}"
    return p


def _a_wgs(partes):
    return [[[round(v, 7) for v in P.a_wgs84(x, y)] for x, y in parte] for parte in partes]


def _features_acc(capa, bbox, limite):
    """Predios de la ACC (municipios de Cundinamarca fuera del catastro IGAC) en la vista."""
    if capa not in ("R_TERRENO", "U_TERRENO") or limite <= 0:
        return [], False
    ps, trunc = catastro_acc.en_bbox(bbox, clase=capa[0], limite=limite)
    feats = []
    for p in ps:
        pr = catastro_acc.props(p, _nombre_municipio)
        feats += [dict(type="Feature", geometry=dict(type="Polygon", coordinates=poly), properties=pr) for poly in p["polys"]]
    return feats, trunc


def features(capa, bbox, limite=3000):
    c = capas().get(capa)
    if not c:
        feats, trunc = _features_acc(capa, bbox, limite)
        return dict(type="FeatureCollection", features=feats, truncado=trunc)
    if c.progreso < 1.0:
        c.construir_en_segundo_plano()
        return dict(type="FeatureCollection", features=[], truncado=False, indexando=round(c.progreso, 3))
    ids = c.ids_en(*P.bbox_a_ctm12(*bbox), limite=limite)
    trunc = len(ids) > limite
    feats = []
    for i, tipo, partes, a in c.leer(ids[:limite]):
        geo = _a_wgs(partes)
        geom = dict(type="Polygon", coordinates=geo) if tipo == "Polygon" else dict(type="MultiLineString", coordinates=geo)
        feats.append(dict(type="Feature", geometry=geom, properties=_props(capa, a)))
    extra, trunc_acc = _features_acc(capa, bbox, limite - len(feats))
    feats += extra
    res = dict(type="FeatureCollection", features=feats, truncado=trunc or trunc_acc)
    if not feats and capa in ("R_TERRENO", "U_TERRENO"):  # vista vacía: ¿la base cubre este municipio?
        res["aviso"] = aviso_cobertura((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2, capa)
    return res


def _area_anillo(r):
    return abs(sum(r[i][0] * r[(i + 1) % len(r)][1] - r[(i + 1) % len(r)][0] * r[i][1] for i in range(len(r)))) / 2


def _area_dentro(partes, polys_ctm, n=40):
    """Área (m²) del predio que cae dentro de los polígonos construibles, por muestreo en una
    cuadrícula de n×n sobre la intersección de sus cajas (CTM12, metros)."""
    import gis
    ext, huecos = partes[0], partes[1:]
    xs, ys = [p[0] for p in ext], [p[1] for p in ext]
    total = 0.0
    for poly in polys_ctm:
        px, py = [p[0] for p in poly], [p[1] for p in poly]
        x0, x1 = max(min(xs), min(px)), min(max(xs), max(px))
        y0, y1 = max(min(ys), min(py)), min(max(ys), max(py))
        if x1 <= x0 or y1 <= y0:
            continue
        dx, dy = (x1 - x0) / n, (y1 - y0) / n
        k = 0
        for i in range(n):
            x = x0 + (i + 0.5) * dx
            for j in range(n):
                y = y0 + (j + 0.5) * dy
                if gis._pir(x, y, poly) and gis._pir(x, y, ext) and not any(gis._pir(x, y, h) for h in huecos):
                    k += 1
        total += k * dx * dy
    return total


def predio_en_punto(lon, lat):
    """Predio (rural o urbano) que contiene el punto, para usarlo como poligono de evaluacion."""
    import gis
    x, y = P.a_ctm12(lon, lat)
    for capa in ("U_TERRENO", "R_TERRENO"):
        c = capas().get(capa)
        if not c or c.progreso < 1.0:
            continue
        for i, tipo, partes, a in c.leer(c.ids_en(x, y, x, y, limite=50)):
            if not partes or not gis._pir(x, y, partes[0]):
                continue
            anillo = _a_wgs(partes[:1])[0]
            area = a.get("SHAPE_Area") if isinstance(a.get("SHAPE_Area"), (int, float)) else None
            return dict(encontrado=True, tipo="rural" if capa == "R_TERRENO" else "urbano",
                        numero_predial=a.get("CODIGO", ""), municipio=_nombre_municipio(a.get("codigo_mun", "")),
                        area_ha=area / 10000 if area else None, coords=anillo, partes=len(partes))
    p = catastro_acc.en_punto(lon, lat)
    if p:
        area = catastro_acc.area_m2(p)
        return dict(encontrado=True, tipo="urbano" if p["clase"] == "Urbano" else "rural", numero_predial=p["clave"],
                    municipio=_nombre_municipio(p["municipio"]), area_ha=area / 10000 if area else None,
                    coords=p["polys"][0][0], partes=sum(len(x) for x in p["polys"]), fuente=p["fuente"])
    return dict(encontrado=False, motivo=aviso_cobertura(lon, lat))


# --------------------------------------------------------------------------- cobertura por municipio
# El catastro público del IGAC solo trae los municipios donde el IGAC es el gestor catastral. Bogotá, Medellín,
# Cali, Cundinamarca (ACC), Antioquia y otros llevan su propio catastro y no están en esta base.
_cobertura = None


def cobertura():
    """{código de municipio: {"R": predios rurales, "U": predios urbanos}} según el índice de números prediales."""
    global _cobertura
    if _cobertura is None:
        cache = os.path.join(DIR, "catastro_cobertura.json")
        if bd.NUBE:
            # copia de catastro_cobertura.json que sube herramientas/migrar_a_nube.py; si falta, se cuenta en Postgres
            datos = bd.leer_archivo("catastro_cobertura.json")
            if datos:
                _cobertura = json.loads(datos)
            else:
                cob = {}
                for mun, capa, n in bd.conectar("ev_cat_codigos").execute(
                        "select substr(codigo, 1, 5), substr(capa, 1, 1), count(*) from predio where capa like '%TERRENO' group by 1, 2"):
                    cob.setdefault(mun, {})[capa] = n
                _cobertura = cob
            return _cobertura
        if os.path.isfile(cache) and os.path.isfile(IDX_CODIGOS) and os.path.getmtime(cache) >= os.path.getmtime(IDX_CODIGOS):
            with open(cache, encoding="utf-8") as f:
                _cobertura = json.load(f)
        elif os.path.isfile(IDX_CODIGOS):
            cob = {}
            db = sqlite3.connect(f"file:{IDX_CODIGOS}?mode=ro", uri=True)
            for mun, capa, n in db.execute("select substr(codigo, 1, 5), substr(capa, 1, 1), count(*) from predio "
                                           "where capa like '%TERRENO' group by 1, 2"):
                cob.setdefault(mun, {})[capa] = n
            db.close()
            _cobertura = cob
            try:
                with open(cache, "w", encoding="utf-8") as f:
                    json.dump(cob, f)
            except OSError:
                pass
        else:
            return {}
    return _cobertura


def aviso_cobertura(lon, lat, capa=None):
    """Texto para el usuario cuando no hay predios en un punto: dice si es porque la base no cubre el municipio."""
    try:
        import gis
        mun = gis.municipio_en(lon, lat)
        cob = cobertura()
        if not mun or not cob:
            return ""
        c = cob.get(mun["codigo"], {})
        lugar = f"{mun['nombre']}, {mun['depto']}"
        if not c and mun["codigo"] in catastro_acc.municipios():
            return ""  # sus predios vienen de la Agencia Catastral de Cundinamarca
        if not c:
            return (f"El catastro público del IGAC no incluye a {lugar}: ese municipio tiene otro gestor catastral. "
                    "Dibuja el polígono a mano o carga un KMZ.")
        if capa in ("R_TERRENO", "U_TERRENO") and not c.get(capa[0]):
            tipo = "rurales" if capa[0] == "R" else "urbanos"
            return f"La base del IGAC no trae predios {tipo} de {lugar}."
        return ""
    except Exception:
        return ""


# --------------------------------------------------------------------------- busqueda por numero predial
IDX_CODIGOS = os.path.join(DIR, "catastro_codigos.sqlite")
_estado_codigos = dict(progreso=1.0 if bd.NUBE or os.path.isfile(IDX_CODIGOS) else 0.0, construyendo=False)


def _campos_dbf(c, nombres):
    return {nm: (off, ln) for nm, t, off, ln in c.campos if nm in nombres}


def construir_indice_codigos():
    """Una pasada por los .dbf: numero predial (30 y 20 digitos) -> registro, y resumen de
    construcciones y direcciones por predio. Se lee solo el tramo de cada campo (rapido)."""
    if _estado_codigos["construyendo"]:
        return
    _estado_codigos["construyendo"] = True
    try:
        tmp = IDX_CODIGOS + ".tmp"
        if os.path.exists(tmp):
            os.remove(tmp)
        db = sqlite3.connect(tmp)
        db.executescript("create table predio(codigo text, ant text, capa text, id integer);"
                         "create table construccion(terreno text, n integer, area real, pisos integer, tipo text);"
                         "create table direccion(terreno text, texto text);")
        tareas = [("R_TERRENO", "predio"), ("U_TERRENO", "predio"), ("R_CONSTRUCCION", "construccion"),
                  ("U_CONSTRUCCION", "construccion"), ("R_NOMENCLATURA_DOMICILIARIA", "direccion")]
        tareas = [t for t in tareas if os.path.isfile(os.path.join(DIR, t[0] + ".dbf"))]
        for k, (capa, tabla) in enumerate(tareas):
            c = CapaShp(os.path.join(DIR, capa), crs="ctm12") if capa not in capas() else capas()[capa]
            f = _campos_dbf(c, ("CODIGO", "CODIGO_ANT", "TERRENO_CO", "SHAPE_Area", "NUMERO_PIS", "TIPO_CONST", "TEXTO"))
            agg = {}
            lote = []
            with open(c.base + ".dbf", "rb", buffering=1 << 20) as fd:
                fd.seek(c.hl)
                bloque_regs = 20000
                i = 0
                while i < c.n:
                    datos = fd.read(c.rl * min(bloque_regs, c.n - i))
                    if not datos:
                        break
                    for j in range(len(datos) // c.rl):
                        r = datos[j * c.rl:(j + 1) * c.rl]
                        if r[:1] == b"*":  # registro borrado
                            continue

                        def v(nm):
                            off, ln = f[nm]
                            return r[off:off + ln].decode("latin1").strip()
                        if tabla == "predio":
                            lote.append((v("CODIGO"), v("CODIGO_ANT") if "CODIGO_ANT" in f else "", capa, i + j))
                        elif tabla == "construccion":
                            t = v("TERRENO_CO")
                            try:
                                a = float(v("SHAPE_Area") or 0)
                            except ValueError:
                                a = 0.0
                            try:
                                p = int(float(v("NUMERO_PIS") or 0))
                            except ValueError:
                                p = 0
                            g = agg.setdefault(t, [0, 0.0, 0, set()])
                            g[0] += 1; g[1] += a; g[2] = max(g[2], p); g[3].add(v("TIPO_CONST"))
                        else:
                            tx = v("TEXTO")
                            if tx:
                                lote.append((v("TERRENO_CO"), tx))
                    i += len(datos) // c.rl
                    _estado_codigos["progreso"] = min(0.99, (k + i / max(1, c.n)) / len(tareas))
                    if len(lote) > 100000:
                        db.executemany(f"insert into {tabla} values ({','.join('?' * len(lote[0]))})", lote)
                        lote.clear()
            if tabla == "construccion":
                lote = [(t, g[0], g[1], g[2], ", ".join(sorted(x for x in g[3] if x))) for t, g in agg.items()]
            if lote:
                db.executemany(f"insert into {tabla} values ({','.join('?' * len(lote[0]))})", lote)
        db.executescript("create index i_cod on predio(codigo); create index i_ant on predio(ant);"
                         "create index i_con on construccion(terreno); create index i_dir on direccion(terreno);")
        db.commit()
        db.close()
        os.replace(tmp, IDX_CODIGOS)
        _estado_codigos["progreso"] = 1.0
    finally:
        _estado_codigos["construyendo"] = False


def estado_indice_codigos():
    if _estado_codigos["progreso"] < 1.0 and not _estado_codigos["construyendo"] and disponible():
        import threading
        threading.Thread(target=construir_indice_codigos, daemon=True).start()
    return round(_estado_codigos["progreso"], 3)


def _limpiar_codigo(cod):
    return "".join(ch for ch in str(cod or "") if ch.isdigit())


def _ficha_acc(p):
    a, area = p["attrs"], catastro_acc.area_m2(p)
    try:
        construida = float(a.get("area_construida") or 0)
    except (TypeError, ValueError):
        construida = 0.0
    vereda = ""
    try:
        v = capas().get("R_VEREDA")
        anillo = p["polys"][0][0]
        cx, cy = sum(q[0] for q in anillo) / len(anillo), sum(q[1] for q in anillo) / len(anillo)
        if v and v.progreso >= 1.0:
            import gis
            x, y = P.a_ctm12(cx, cy)
            for _, _, pv, av in v.leer(v.ids_en(x, y, x, y, limite=20)):
                if gis._pir(x, y, pv[0]):
                    vereda = av.get("NOMBRE", "")
                    break
    except Exception:
        pass
    return dict(encontrado=True, numero_predial=p["clave"], anterior=p["clave"], nupre=a.get("nupre", ""),
                tipo="urbano" if p["clase"] == "Urbano" else "rural", municipio=_nombre_municipio(p["municipio"]),
                codigo_mun=p["municipio"], vereda=vereda, area_m2=area, area_ha=area / 10000 if area else None,
                construcciones=int(a.get("count_building_units") or 0), area_construida_m2=construida, pisos_max=0,
                tipos_construccion="", direcciones=[a["direccion"]] if a.get("direccion") else [],
                destinacion=a.get("destinacion_economica", ""), coords=p["polys"][0][0], huecos=p["polys"][0][1:],
                fuente=p["fuente"])


def ficha_predio(codigo=None, partes_wgs=None):
    """Ficha catastral de un predio por numero predial (30 o 20 digitos): geometria, area,
    construcciones y direccion. Devuelve encontrado=False si no esta en el catastro IGAC."""
    cod = _limpiar_codigo(codigo)
    if not cod:
        return dict(encontrado=False, motivo="sin número predial")
    # en la ACC la clave es el número anterior (20 dígitos); desde uno de 30 se arma con depto, municipio, zona,
    # sector, vereda y terreno
    acc = catastro_acc.por_codigo(cod) or (catastro_acc.por_codigo(cod[:9] + cod[13:21] + "000") if len(cod) == 30 else None)
    if acc:
        return _ficha_acc(acc)
    prog = estado_indice_codigos()
    if prog < 1.0:
        return dict(encontrado=False, indexando=prog, motivo="indexando el catastro por número predial")
    db = bd.conectar("ev_cat_codigos", f"file:{IDX_CODIGOS}?mode=ro", uri=True)
    try:
        fila = db.execute("select codigo, ant, capa, id from predio where codigo=? or ant=? limit 1", (cod, cod)).fetchone()
        if not fila:
            return dict(encontrado=False, motivo="el número predial no está en el catastro público IGAC (el municipio puede tener otro gestor catastral)")
        codigo30, ant, capa, rid = fila
        con = db.execute("select sum(n), sum(area), max(pisos), group_concat(tipo, ', ') from construccion where terreno=?", (codigo30,)).fetchone()
        dirs = [r[0] for r in db.execute("select distinct texto from direccion where terreno=? limit 3", (codigo30,))]
    finally:
        db.close()
    c = capas().get(capa)
    if not c:
        return dict(encontrado=False, motivo=f"capa {capa} no disponible")
    tipo, partes = c.geometria(rid)
    a = c.atributos(rid)
    area = a.get("SHAPE_Area") if isinstance(a.get("SHAPE_Area"), (int, float)) else _area_anillo(partes[0])
    vereda = ""
    try:
        v = capas().get("R_VEREDA")
        if v and v.progreso >= 1.0:
            x, y = partes[0][0]
            import gis
            cx = sum(p[0] for p in partes[0]) / len(partes[0])
            cy = sum(p[1] for p in partes[0]) / len(partes[0])
            for _, _, pv, av in v.leer(v.ids_en(cx, cy, cx, cy, limite=20)):
                if gis._pir(cx, cy, pv[0]):
                    vereda = av.get("NOMBRE", "")
                    break
    except Exception:
        pass
    anillos = _a_wgs(partes)
    return dict(encontrado=True, numero_predial=codigo30, anterior=ant, tipo="rural" if capa.startswith("R_") else "urbano",
                municipio=_nombre_municipio(a.get("codigo_mun") or codigo30[:5]), codigo_mun=a.get("codigo_mun") or codigo30[:5],
                vereda=vereda, area_m2=area, area_ha=area / 10000 if area else None,
                construcciones=int(con[0] or 0), area_construida_m2=float(con[1] or 0), pisos_max=int(con[2] or 0),
                tipos_construccion=", ".join(sorted({t.strip() for t in (con[3] or "").split(",") if t.strip()})),
                direcciones=dirs, coords=anillos[0], huecos=anillos[1:], fuente=f"Catastro IGAC {CORTE}")


# ---------------------------------------------------------------- búsqueda por nombre o dirección del predio
# El catastro público trae el nombre del predio rural («LOTE LA ESPERANZA», «EL MANZANO VDA RIO ARRIBA») y la
# dirección del urbano («CL 10 5 20») en las capas de nomenclatura domiciliaria. Se indexan en una tabla de texto
# completo (FTS5 de SQLite, sin tildes) para buscarlos desde el buscador del encabezado.
IDX_BUSQUEDA = os.path.join(DIR, "catastro_busqueda.sqlite")
_estado_busqueda = dict(progreso=1.0 if bd.NUBE or os.path.isfile(IDX_BUSQUEDA) else 0.0, construyendo=False)
_SIN_NOMBRE = {"", "SIN ASIGNAR", "SIN DIRECCION", "SIN NOMBRE", "NN", "N/A"}


def construir_indice_busqueda():
    if _estado_busqueda["construyendo"]:
        return
    _estado_busqueda["construyendo"] = True
    try:
        tmp = IDX_BUSQUEDA + ".tmp"
        if os.path.exists(tmp):
            os.remove(tmp)
        db = sqlite3.connect(tmp)
        db.execute("create virtual table nombres using fts5(texto, terreno unindexed, mun unindexed, tipo unindexed, "
                   "tokenize='unicode61 remove_diacritics 2')")
        vistos = set()

        def agregar(filas, tipo):
            lote = []
            for terreno, texto in filas:
                texto = " ".join((texto or "").split())
                if texto.upper() in _SIN_NOMBRE or len(terreno) < 20 or (terreno, texto) in vistos:
                    continue
                vistos.add((terreno, texto))
                lote.append((texto, terreno, terreno[:5], tipo))
            db.executemany("insert into nombres values (?,?,?,?)", lote)
        # rurales: ya están en el índice de números prediales
        if estado_indice_codigos() >= 1.0:
            src = sqlite3.connect(f"file:{IDX_CODIGOS}?mode=ro", uri=True)
            agregar(src.execute("select terreno, texto from direccion"), "rural")
            src.close()
        _estado_busqueda["progreso"] = 0.2
        vistos.clear()
        # urbanos: dirección de la nomenclatura domiciliaria urbana
        base = os.path.join(DIR, "U_NOMENCLATURA_DOMICILIARIA")
        if os.path.isfile(base + ".dbf"):
            c = CapaShp(base, crs="ctm12")
            f = _campos_dbf(c, ("TERRENO_CO", "TEXTO"))
            if len(f) == 2:
                with open(c.base + ".dbf", "rb", buffering=1 << 20) as fd:
                    fd.seek(c.hl)
                    i = 0
                    while i < c.n:
                        datos = fd.read(c.rl * min(20000, c.n - i))
                        if not datos:
                            break
                        filas = []
                        for j in range(len(datos) // c.rl):
                            r = datos[j * c.rl:(j + 1) * c.rl]
                            if r[:1] != b"*":
                                (o1, l1), (o2, l2) = f["TERRENO_CO"], f["TEXTO"]
                                filas.append((r[o1:o1 + l1].decode("latin1").strip(), r[o2:o2 + l2].decode("latin1").strip()))
                        agregar(filas, "urbano")
                        vistos.clear()  # en lo urbano no se repiten; así no crece la memoria
                        i += len(datos) // c.rl
                        _estado_busqueda["progreso"] = min(0.99, 0.2 + 0.8 * i / max(1, c.n))
        db.execute("insert into nombres(nombres) values('optimize')")
        db.commit()
        db.close()
        os.replace(tmp, IDX_BUSQUEDA)
        _estado_busqueda["progreso"] = 1.0
    finally:
        _estado_busqueda["construyendo"] = False


def _sin_tildes(s):
    import unicodedata
    return "".join(ch for ch in unicodedata.normalize("NFD", s or "") if unicodedata.category(ch) != "Mn").upper()


_veredas = None


def _lista_veredas():
    """[(nombre sin tildes, nombre, código, código del municipio, registro)] de la capa R_VEREDA (≈18.000), en memoria."""
    global _veredas
    if _veredas is None:
        v = capas().get("R_VEREDA")
        out = []
        if v:
            f = _campos_dbf(v, ("NOMBRE", "CODIGO", "codigo_mun"))
            datos = v.dbf_completo()
            for i in range(v.n):
                r = datos[i * v.rl:(i + 1) * v.rl]
                if r[:1] == b"*":
                    continue
                val = {k: r[o:o + ln].decode(v.enc, "replace").strip() for k, (o, ln) in f.items()}
                nombre = val.get("NOMBRE", "")
                if len(nombre) >= 2 and nombre not in ("_", "SIN NOMBRE"):
                    out.append((_sin_tildes(nombre), nombre, val.get("CODIGO", ""), val.get("codigo_mun", "") or val.get("CODIGO", "")[:5], i))
        _veredas = out
    return _veredas


def vereda(codigo):
    """Geometría (WGS84) de una vereda del catastro por su código."""
    v = capas().get("R_VEREDA")
    reg = next((x for x in _lista_veredas() if x[2] == codigo), None)
    if not v or not reg:
        return dict(encontrado=False, motivo="La vereda no está en el catastro IGAC.")
    _, partes = v.geometria(reg[4])
    anillos = _a_wgs(partes)
    pts = [p for a in anillos for p in a]
    return dict(encontrado=True, nombre=reg[1].title(), codigo=codigo, municipio=_nombre_municipio(reg[3]) or reg[3], anillos=anillos,
                bbox=[min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)])


def buscar_predios(q, limite=15):
    """Predios del catastro IGAC por nombre o dirección («la esperanza paipa», «el cortijo», «cl 10 5 20»).
    Si el texto incluye el nombre de un municipio, se buscan solo los de ese municipio. Un número predial
    (20 o 30 dígitos) va directo a la ficha."""
    if not disponible():
        return dict(resultados=[], aviso="El catastro IGAC no está cargado en este equipo.")
    cod = _limpiar_codigo(q)
    if len(cod) >= 20 and len(cod) == len(re.sub(r"[\s.\-]", "", q or "")):
        f = ficha_predio(cod)
        if f.get("encontrado"):
            return dict(resultados=[dict(texto=f["numero_predial"], codigo=f["numero_predial"], municipio=f["municipio"], tipo=f["tipo"])])
        return dict(resultados=[], aviso=f.get("motivo", ""), indexando=f.get("indexando"))
    if _estado_busqueda["progreso"] < 1.0:
        if not _estado_busqueda["construyendo"]:
            import threading
            threading.Thread(target=construir_indice_busqueda, daemon=True).start()
        return dict(resultados=[], indexando=round(_estado_busqueda["progreso"], 3))
    texto = _sin_tildes(q)
    # ¿nombra un municipio? (el más largo que aparezca completo en el texto)
    _nombre_municipio("")
    muns = {}
    for codigo, nm in (_municipios or {}).items():
        muns.setdefault(_sin_tildes(nm.split(",")[0]), []).append(codigo)
    # «la esperanza paipa»: La Esperanza también es un municipio; se prefiere el nombre de municipio que va al final
    # y que deja texto para buscar, y si con ese filtro no aparece nada se busca en todo el país
    candidatos = []
    for nm in muns:
        if len(nm) >= 4:
            for m in re.finditer(r"(?:^|\s)(" + re.escape(nm) + r")(?=\s|$)", texto):
                candidatos.append((m.end(1), len(nm), nm))
    intentos = []
    for _, _, nm in sorted(candidatos, reverse=True):
        resto = re.sub(r"(^|\s)" + re.escape(nm) + r"(?=\s|$)", " ", texto)
        if re.search(r"[A-Z0-9]{2,}", resto):
            intentos.append((muns[nm], resto))
            break
    intentos.append(([], texto))
    # veredas del catastro (capa R_VEREDA) con el mismo filtro de municipio
    veredas = []
    for filtro_v, resto in intentos:
        pal = re.findall(r"[A-Z0-9]+", resto.replace("VEREDA", " ").replace("VDA", " "))
        if not pal:
            continue
        for nm, nombre, codigo, mun, rid in _lista_veredas():
            if (not filtro_v or mun in filtro_v) and all(re.search(r"(^|\s)" + p, nm) for p in pal):
                veredas.append(dict(texto=f"Vereda {nombre.title()}", codigo=codigo, municipio=_nombre_municipio(mun) or mun, tipo="vereda"))
                if len(veredas) >= 6:
                    break
        if veredas:
            break
    filas, filtro = [], []
    db = bd.conectar("ev_cat_busqueda", f"file:{IDX_BUSQUEDA}?mode=ro", uri=True)
    try:
        for filtro, resto in intentos:
            palabras = [w for w in re.findall(r"[A-Z0-9]+", resto) if w not in ("VDA", "VEREDA", "DE", "LA", "EL", "LOS", "LAS", "DEL")] \
                or re.findall(r"[A-Z0-9]+", resto)
            if not palabras:
                continue
            if bd.NUBE:  # texto completo de Postgres en lugar de FTS5: todas las palabras, por prefijo
                sql = "select texto, terreno, mun, tipo from nombres where to_tsvector('simple', texto) @@ to_tsquery('simple', ?)"
                args = [" & ".join(f"{w.lower()}:*" for w in palabras[:6])]
            else:
                sql = "select texto, terreno, mun, tipo from nombres where nombres match ?"
                args = [" ".join(f'"{w}"*' for w in palabras[:6])]
            if filtro:
                sql += f" and mun in ({','.join('?' * len(filtro))})"
                args += filtro
            sql += (" order by (tipo = 'urbano'), ts_rank(to_tsvector('simple', texto), to_tsquery('simple', ?)) desc limit ?"
                    if bd.NUBE else " order by (tipo = 'urbano'), rank limit ?")
            if bd.NUBE:
                args = args + [args[0]]
            try:
                filas = db.execute(sql, args + [limite * 3]).fetchall()
            except sqlite3.OperationalError:
                filas = []
            if filas:
                break
    finally:
        db.close()
    # predios de la Agencia Catastral de Cundinamarca (fuera de la base del IGAC)
    if catastro_acc.disponible():
        for filtro_a, resto in intentos:
            pal = [w for w in re.findall(r"[A-Z0-9]+", resto) if w not in ("VDA", "VEREDA", "DE", "LA", "EL", "LOS", "LAS", "DEL")]
            if not pal:
                continue
            extra = catastro_acc.buscar(pal[:6], filtro_a, limite)
            if extra:
                filas = list(filas) + [(d, clave, mun, "urbano" if clase == "Urbano" else "rural") for clave, mun, clase, d in extra]
                filtro = filtro or filtro_a
                break
    out, vistos = [], set()
    for texto_p, terreno, mun, tipo in filas:
        if terreno in vistos:
            continue
        vistos.add(terreno)
        out.append(dict(texto=texto_p, codigo=terreno, municipio=_nombre_municipio(mun) or mun, tipo=tipo))
        if len(out) >= limite:
            break
    out = veredas + out[:max(4, limite - len(veredas))]
    res = dict(resultados=out)
    if not out:
        res["aviso"] = "Sin predios ni veredas con ese nombre en el catastro público IGAC" + (" de ese municipio." if filtro else ".") + \
            " Algunos municipios tienen otro gestor catastral y no están en esta base."
    return res


def predios_en(poligonos, limite=200):
    """Predios (rurales y urbanos) que tocan los poligonos construibles (lon/lat)."""
    import gis
    pts = [p for poly in poligonos for p in poly]
    bbox = (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))
    out, pendiente = [], []
    polys_ctm = [[P.a_ctm12(x, y) for x, y in poly] for poly in poligonos]
    for capa in ("R_TERRENO", "U_TERRENO"):
        c = capas().get(capa)
        if not c:
            continue
        if c.progreso < 1.0:
            c.construir_en_segundo_plano()
            pendiente.append(capa)
            continue
        for i, tipo, partes, a in c.leer(c.ids_en(*P.bbox_a_ctm12(*bbox), limite=limite)):
            anillos = [[tuple(v) for v in r] for r in _a_wgs(partes)]
            if not any(gis._poligonos_se_tocan(poly, anillos) for poly in poligonos):
                continue
            area = a.get("SHAPE_Area") if isinstance(a.get("SHAPE_Area"), (int, float)) else None
            dentro = _area_dentro(partes, polys_ctm)
            area_ref = area or _area_anillo(partes[0])
            # un predio vecino que solo comparte lindero "toca" el polígono pero no aporta área
            if dentro < max(50.0, 0.005 * area_ref):
                continue
            out.append(dict(tipo="rural" if capa == "R_TERRENO" else "urbano", numero_predial=a.get("CODIGO", ""),
                            anterior=a.get("CODIGO_ANT", ""), area_ha=area / 10000 if area else None,
                            area_dentro_ha=dentro / 10000, pct_predio=100 * dentro / area_ref if area_ref else None,
                            municipio=_nombre_municipio(a.get("codigo_mun", "")), coords=anillos[0]))
    # predios de la Agencia Catastral de Cundinamarca
    for p in catastro_acc.en_bbox(bbox, limite=limite)[0]:
        for poly in p["polys"]:
            anillos = [[tuple(v) for v in r] for r in poly]
            if not any(gis._poligonos_se_tocan(pg, anillos) for pg in poligonos):
                continue
            partes = [[P.a_ctm12(x, y) for x, y in r] for r in poly]
            area = catastro_acc.area_m2(p) if len(p["polys"]) == 1 else None
            dentro = _area_dentro(partes, polys_ctm)
            area_ref = area or _area_anillo(partes[0])
            if dentro < max(50.0, 0.005 * area_ref):
                continue
            out.append(dict(tipo="urbano" if p["clase"] == "Urbano" else "rural", numero_predial=p["clave"],
                            anterior="", area_ha=area_ref / 10000 if area_ref else None,
                            area_dentro_ha=dentro / 10000, pct_predio=100 * dentro / area_ref if area_ref else None,
                            municipio=_nombre_municipio(p["municipio"]), coords=list(map(list, anillos[0])), fuente=p["fuente_corta"]))
    out.sort(key=lambda p: -(p["area_dentro_ha"] or 0))
    return dict(disponible=disponible(), corte=CORTE, predios=out, indexando=pendiente)
