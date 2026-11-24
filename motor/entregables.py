"""
Entregables de una evaluación que no son Word: cotizador ajustado en cantidades (Excel), modelo financiero
(Excel), plano del layout (DXF georreferenciado) y KMZ.

- Cotizador: se parte de datos/plantillas/cotizador_gd.xlsx (cotizador de GD Monsalve, 1 GD de 1.337 kWp)
  y se cambian solo las cantidades que dependen del dimensionamiento. Precios, IVA y márgenes no se tocan.
- Modelo financiero: el Excel del modelo financiero de Solcor (plantilla en datos/plantillas) con las celdas de
  entrada del GD diligenciadas: municipio, departamento, capacidad, CAPEX, generación, área y arriendo o compra.
  resumen_financiero() conserva la exportación de valores calculados por motor/financiero.py.
- Plano: DXF en MAGNA-SIRGAS Origen Nacional (CTM12), en metros, con una capa por tipo de elemento y por GD.
"""
import io
import math
import os
import re
import zipfile

import proyeccion as P
import xlsx
from xlsx import DEC2, MILES, NEGRITA, PCT, TITULO

BASE = os.path.dirname(os.path.abspath(__file__))
PLANTILLA_COTIZADOR = os.path.join(BASE, "..", "datos", "plantillas", "cotizador_gd.xlsx")
LOGO = os.path.join(BASE, "..", "web", "marca", "solcor_documentos.png")  # fondo blanco (ticket #46)


def _logo():
    try:
        with open(LOGO, "rb") as f:
            return f.read()
    except OSError:
        return None


def _con_logo(libro, ubicaciones):
    logo = _logo()
    return xlsx.con_logo(libro, logo, ubicaciones) if logo else libro


# la plantilla corresponde a un GD de 1.337 kWp y 1.996 módulos
REF_KWP, REF_MODULOS = 1337.0, 1996.0


def kmz(kml_texto):
    logo = _logo()
    if logo and "</Document>" in kml_texto and "<ScreenOverlay" not in kml_texto:
        # logo fijo en la esquina inferior izquierda de Google Earth (#46)
        kml_texto = kml_texto.replace("</Document>", (
            "<ScreenOverlay><name>Solcor Colombia</name><Icon><href>logo_solcor.png</href></Icon>"
            '<overlayXY x="0" y="0" xunits="fraction" yunits="fraction"/>'
            '<screenXY x="0.01" y="0.04" xunits="fraction" yunits="fraction"/>'
            '<size x="0" y="0.07" xunits="fraction" yunits="fraction"/></ScreenOverlay></Document>'), 1)
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as f:
        f.writestr("doc.kml", kml_texto)
        if logo and "logo_solcor.png" in kml_texto:
            f.writestr("logo_solcor.png", logo)
    return z.getvalue()


def _n(v, dec=0):
    return f"{v:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _perimetro_m(poligonos):
    total = 0.0
    for pol in poligonos or []:
        for (x0, y0), (x1, y1) in zip(pol, pol[1:] + pol[:1]):
            total += math.hypot((x1 - x0) * 111320 * math.cos(math.radians((y0 + y1) / 2)), (y1 - y0) * 110574)
    return total


# ---------------------------------------------------------------- cotizador
def cotizador(datos):
    """datos: proyecto, resultado {resumen, params, gds}, poligonos (áreas construibles), long_mt_m.
    Devuelve (xlsx, notas): las notas dicen qué cantidades se ajustaron y con qué criterio."""
    if not os.path.isfile(PLANTILLA_COTIZADOR):
        raise ValueError("Falta la plantilla del cotizador en datos/plantillas/cotizador_gd.xlsx.")
    res = datos.get("resultado") or {}
    s, p, gds = res.get("resumen") or {}, res.get("params") or {}, res.get("gds") or []
    kwp, modulos, n_gd = float(s.get("dc_kwp") or 0), int(s.get("modulos") or 0), max(1, int(s.get("n_gd") or 1))
    if kwp <= 0 or modulos <= 0:
        raise ValueError("Primero evalúa el predio.")
    inversores = sum(int(g.get("inversores") or 0) for g in gds) or n_gd * 3
    area_m2 = float(s.get("area_construible_ha") or 0) * 10000
    perimetro = _perimetro_m(datos.get("poligonos"))
    long_mt = float(datos.get("long_mt_m") or 0)
    f_mod = modulos / REF_MODULOS
    strings = math.ceil(modulos / 26)
    c = {
        # 1. parámetros del proyecto
        "D4": round(area_m2 / 10000, 2), "D6": float(p.get("modulo_wp") or 0), "F5": round(kwp, 1), "F7": modulos, "F9": inversores,
        "D9": round(kwp / float(s.get("ac_kw_total") or kwp), 3),
        # equipos principales
        "G18": modulos, "G19": inversores, "G20": 0, "G21": 0,
        "G22": n_gd, "G23": n_gd, "G24": n_gd, "G25": n_gd, "G26": n_gd, "G27": n_gd, "G28": inversores, "G29": 0.5 * n_gd,
        "G30": 3 * n_gd, "G31": 3 * n_gd, "G32": 2 * n_gd, "G34": n_gd, "G35": n_gd,
        # estructura
        "G37": round(kwp, 1), "G38": modulos,
        # materiales eléctricos: lo de corriente continua va con los módulos; lo demás, por GD
        "G40": round(8778 * f_mod), "G41": round(1000 * f_mod), "G42": strings * 4,
        "G43": 146 * n_gd, "G44": 550 * n_gd, "G45": 563 * n_gd, "G46": 226 * n_gd, "G47": 539 * n_gd, "G48": 40 * n_gd,
        "G49": 194 * n_gd, "G50": 95 * n_gd, "G51": 50 * n_gd, "G52": n_gd, "G53": inversores, "G54": 5 * n_gd, "G55": 200 * n_gd,
        "G56": 175 * n_gd, "G57": 520 * n_gd, "G58": 16 * n_gd, "G59": n_gd,
        # conexión MT
        "G62": n_gd,
        # construcción y montaje
        "G64": round(area_m2), "G65": round(area_m2 / 2), "G66": round(1198 * f_mod), "G67": modulos, "G68": round(kwp, 1),
        "G69": n_gd, "G72": n_gd, "G73": n_gd, "G79": round(kwp, 1),
        # puesta en marcha y estudio de conexión: uno por GD
        "G81": n_gd, "G82": n_gd, "G83": n_gd, "G106": n_gd,
    }
    notas = [f"Cantidades ajustadas a {_n(modulos)} módulos, {_n(kwp, 1)} kWp y {n_gd} GD.",
             "Proporcionales al número de módulos: cable solar, conectores MC4 e hincado de perfiles.",
             "Uno por GD: transformadores, celda, reconectador, tableros, medida, malla de tierra, puesta en marcha y estudio de conexión.",
             "Todos los inversores se cotizan como el modelo de 330 kW de la plantilla; los de 50 y 40 kW quedan en cero."]
    if float(p.get("potencia_inversor_kw") or 330) != 330:
        notas.append(f"OJO: la evaluación usa inversores de {p.get('potencia_inversor_kw'):g} kW; ajusta el precio unitario de la fila 2.")
    if long_mt > 0:
        c["D10"], c["G61"] = round(long_mt), round(long_mt)
        notas.append(f"Línea MT: {_n(long_mt)} m, tomados de la línea trazada en el evaluador.")
    else:
        notas.append("Línea MT: se dejó la longitud de la plantilla (65 m) porque la evaluación no tiene la línea trazada.")
    if perimetro > 0:
        c["G70"] = round(perimetro)
        notas.append(f"Cerramiento: {_n(perimetro)} m, el perímetro de las áreas construibles.")
    notas.append("No se cambiaron precios unitarios, IVA, márgenes, dirección de obra ni estudios; son los de la plantilla (agosto de 2026).")
    proyecto = str(datos.get("proyecto") or "GD").upper()
    with open(PLANTILLA_COTIZADOR, "rb") as f:
        plantilla = f.read()
    z = zipfile.ZipFile(io.BytesIO(plantilla))
    titulo = next((t for t in re.findall(r"<t[^>]*>([^<]*COTIZADOR GD MONSALVE[^<]*)</t>", z.read("xl/sharedStrings.xml").decode("utf-8"))), None)
    textos = {titulo: titulo.replace("COTIZADOR GD MONSALVE", f"COTIZADOR {proyecto}")} if titulo else None
    # logo al inicio de la barra de título (A1:N1, fila 1; el título va centrado)
    return _con_logo(xlsx.editar(plantilla, c, textos), {"xl/worksheets/sheet1.xml": (0, 0, 0.85)}), notas


# ---------------------------------------------------------------- modelo financiero
_SUPUESTOS = [  # (clave, etiqueta, estilo)
    ("capacidad_kwp", "Capacidad DC (kWp)", DEC2), ("capacidad_kw", "Capacidad AC (kW)", MILES),
    ("produccion_especifica", "Producción específica (kWh/kWp·año)", MILES), ("degradacion_anio1", "Degradación año 1", PCT),
    ("degradacion_siguientes", "Degradación años siguientes", PCT), ("ppa_real", "Precio PPA real (COP/kWh)", DEC2),
    ("capex_epc", "CAPEX EPC (COP)", MILES), ("capex_rtb", "CAPEX RTB (COP)", MILES), ("area_ha", "Área de arriendo (ha)", DEC2),
    ("canon_ha_mes", "Canon de arriendo (COP/ha·mes)", MILES), ("trm", "TRM (COP/USD)", MILES),
    ("apalancamiento", "Apalancamiento", PCT), ("ibr", "IBR", PCT), ("spread_ibr", "Spread sobre IBR", PCT), ("plazo_deuda_anios", "Plazo de la deuda (años)", MILES),
    ("om_cop_kwp_anio", "O&M (COP/kWp·año)", MILES), ("seguros_pct", "Seguros (% del CAPEX)", PCT),
    ("impuesto_renta", "Impuesto de renta", PCT), ("anios_operacion", "Años de operación", MILES),
]


PLANTILLA_MODELO = os.path.join(BASE, "..", "datos", "plantillas", "modelo_financiero_gd.xlsx")
_MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]


def modelo_financiero(datos, hoy=None):
    """El modelo financiero de Solcor (datos/plantillas/modelo_financiero_gd.xlsx = modelo de GD Nemocón, versión enviada
    el 2026-10-01) con las celdas de entrada del proyecto diligenciadas; todo lo demás (tarifas, OPEX regulado, deuda,
    impuestos, fórmulas) queda como en la plantilla y Excel recalcula al abrir. El modelo es por GD: datos["gd"] dice
    cuál (0 = el primero). Devuelve (xlsx, notas)."""
    import datetime as dt
    fin = datos.get("financiero") or []
    if not fin:
        raise ValueError("La evaluación no tiene modelo financiero calculado.")
    if not os.path.isfile(PLANTILLA_MODELO):
        raise ValueError("Falta la plantilla del modelo financiero en datos/plantillas/modelo_financiero_gd.xlsx.")
    i = int(datos.get("gd") or 0)
    if not 0 <= i < len(fin):
        raise ValueError("Ese GD no existe en la evaluación.")
    s = fin[i].get("supuestos") or {}
    num = lambda k: float(s.get(k) or 0)  # noqa: E731
    if num("capacidad_kwp") <= 0:
        raise ValueError("Primero evalúa el predio.")
    hoy = hoy or dt.date.today()
    proyecto = str(datos.get("proyecto") or "GD").strip()
    nombre = proyecto + (f" · GD {i + 1}" if len(fin) > 1 else "")
    compra = float(datos.get("compra_cop") or 0)
    c = {
        "Portada!B2": f"MODELO FINANCIERO: GENERADOR DISTRIBUIDO {nombre.upper()}",
        "Portada!B3": f"Municipio: {datos.get('municipio') or 'por definir'}",
        "Portada!D3": f"Departamento: {datos.get('depto') or 'por definir'}",
        "Portada!F3": f"Fecha: {hoy.day:02d} de {_MESES[hoy.month - 1]} del {hoy.year}",
        "Portada!G23": "", "Portada!G24": "",  # notas propias de Nemocón
        "Supuestos!C11": round(num("capacidad_kwp"), 2),
        "Supuestos!C12": round(num("capacidad_kw"), 1),
        "Supuestos!C18": round(num("capex_epc")),
        "Supuestos!C19": round(num("capex_rtb")),
        "Supuestos!C23": round(num("produccion_especifica")),
        "Supuestos!C41": round(num("area_ha"), 2),
        # arriendo o compra: si se compra el predio no se paga canon
        "Supuestos!C42": 0 if compra > 0 else round(num("canon_ha_mes")),
        "Supuestos!C44": round(compra),
        # supuestos de mercado y financiación que el usuario puede ajustar en el evaluador
        "Supuestos!C24": round(num("trm"), 2),
        "Supuestos!C37": round(num("om_cop_kwp_anio")),
        "Supuestos!F52": num("ipc_2026"), "Supuestos!F53": num("ipc_2027"), "Supuestos!F54": num("ipc_2028"),
        "Supuestos!C52": num("ipc_lp"),
        "Supuestos!C60": num("apalancamiento"), "Supuestos!C61": num("spread_ibr"), "Supuestos!C62": num("plazo_deuda_anios"),
    }
    ibr = s.get("ibr") or {}
    for celda, k in (("C69", "2025"), ("C70", "2026"), ("C71", "2027"), ("C72", "2028"), ("C73", "lp")):
        if ibr.get(k) is not None:
            c["Supuestos!" + celda] = float(ibr[k])
    try:
        c["Supuestos!C20"] = (dt.date.fromisoformat(str(s.get("fecha_firma"))[:10]) - dt.date(1899, 12, 30)).days
    except ValueError:
        pass
    # PPA negociado real: fila «Propuesta» de la hoja de tarifas (alimenta la fila 4 y Supuestos!C79:C94)
    ppa = [float(v) for v in (s.get("ppa_real") or []) if v not in (None, "")][:16]
    for k, v in enumerate(ppa):
        c[f"Tarifas 2026 - 2042!{'CDEFGHIJKLMNOPQR'[k]}8"] = v
    if s.get("ppa_mes_base"):
        import financiero
        cod = (fin[i].get("cod") or "")[:10]
        if cod:
            c["Supuestos!D79"] = financiero.factor_inicial(s, dt.date.fromisoformat(cod))
    with open(PLANTILLA_MODELO, "rb") as f:
        libro = xlsx.editar(f.read(), c)
    # Portada: a la derecha del título (H2), dentro del área de impresión A1:H49 (la columna H mide ~2,8 cm)
    libro = _con_logo(libro, {"xl/worksheets/sheet1.xml": (7, 1, 0.7)})
    notas = [f"Modelo financiero Solcor diligenciado para {nombre}: {_n(num('capacidad_kwp'), 1)} kWp / {_n(num('capacidad_kw'))} kW.",
             f"CAPEX EPC {_n(num('capex_epc'))} COP y RTB {_n(num('capex_rtb'))} COP, tomados de la evaluación.",
             f"Producción específica {_n(num('produccion_especifica'))} kWh/kWp·año (la de la evaluación; reemplázala por la P50 de PVSyst cuando exista).",
             (f"Compra del predio: {_n(compra)} COP; canon en cero." if compra > 0 else
              f"Arriendo: {_n(num('canon_ha_mes'))} COP/ha·mes sobre {_n(num('area_ha'), 2)} ha."),
             f"TRM {_n(num('trm'), 2)} COP/USD · IPC {', '.join(_n(100 * num(k), 1) + ' %' for k in ('ipc_2026', 'ipc_2027', 'ipc_2028', 'ipc_lp'))} "
             f"(2026, 2027, 2028, largo plazo) · IBR y deuda de la evaluación.",
             f"PPA negociado real: {', '.join(_n(v, 1) for v in ppa[:4])}… COP/kWh (hoja Tarifas, fila Propuesta)"
             + (f"; mes base {s['ppa_mes_base']}: factor IPC del año 1 = {_n(c.get('Supuestos!D79', 0), 4)} (Supuestos!D79)." if s.get("ppa_mes_base") else "."),
             "OPEX regulado e impuestos quedan como en la plantilla (modelo de Nemocón, octubre de 2026). Excel recalcula al abrir."]
    return libro, notas


def resumen_financiero(datos):
    """Libro de valores con los resultados del modelo del evaluador (el entregable anterior); queda para comparar."""
    fin = datos.get("financiero") or []
    if not fin:
        raise ValueError("La evaluación no tiene modelo financiero calculado.")
    proyecto = datos.get("proyecto") or "GD"
    s = (datos.get("resultado") or {}).get("resumen") or {}
    g = lambda d, *k: (lambda v: v if isinstance(v, (int, float)) else None)(_ruta(d, k))  # noqa: E731
    resumen = [[(f"Modelo financiero preliminar · {proyecto}", TITULO)] + [("", TITULO)] * len(fin) + [("", TITULO)],
               ["Exportado del Evaluador GD: valores calculados, sin fórmulas. No reemplaza el modelo financiero del proyecto."], [],
               [("Indicador", NEGRITA)] + [(f"GD {i}", NEGRITA) for i in range(1, len(fin) + 1)] + [("Total", NEGRITA)]]

    def fila(nombre, fn, estilo, suma=True):
        vals = [fn(f) for f in fin]
        tot = sum(v for v in vals if v is not None) if suma and any(v is not None for v in vals) else None
        resumen.append([nombre] + [(v, estilo) for v in vals] + [(tot, estilo)])
    fila("Capacidad DC (kWp)", lambda f: g(f, "supuestos", "capacidad_kwp"), DEC2)
    fila("Capacidad AC (kW)", lambda f: g(f, "supuestos", "capacidad_kw"), MILES)
    fila("CAPEX total (COP)", lambda f: g(f, "capex", "total"), MILES)
    fila("CAPEX EPC (COP)", lambda f: g(f, "capex", "epc"), MILES)
    fila("CAPEX RTB (COP)", lambda f: g(f, "capex", "rtb"), MILES)
    fila("CAPEX (COP/kWp)", lambda f: g(f, "capex", "cop_kwp"), MILES, False)
    fila("CAPEX (USD/kWp)", lambda f: g(f, "capex", "usd_kwp"), MILES, False)
    fila("Deuda (COP)", lambda f: g(f, "capex", "deuda"), MILES)
    fila("Equity (COP)", lambda f: g(f, "capex", "equity"), MILES)
    fila("TIR proyecto antes de impuestos", lambda f: g(f, "tir", "proyecto_pretax"), PCT, False)
    fila("TIR equity antes de impuestos", lambda f: g(f, "tir", "equity_pretax"), PCT, False)
    fila("TIR equity SAS", lambda f: g(f, "tir", "equity_sas"), PCT, False)
    fila("DSCR mínimo", lambda f: g(f, "dscr_min"), DEC2, False)
    fila("Generación año 1 (MWh)", lambda f: g(f, "anio1", "gen_mwh"), MILES)
    fila("Precio año 1 (COP/kWh)", lambda f: g(f, "anio1", "precio"), DEC2, False)
    fila("Ingresos año 1 (COP)", lambda f: g(f, "anio1", "ingresos"), MILES)
    fila("OPEX año 1 (COP)", lambda f: g(f, "anio1", "opex"), MILES)
    fila("EBITDA año 1 (COP)", lambda f: g(f, "anio1", "ebitda"), MILES)
    fila("Margen EBITDA año 1", lambda f: g(f, "anio1", "margen_ebitda"), PCT, False)
    fila("Arriendo año 1 (COP)", lambda f: g(f, "anio1", "arriendo"), MILES)
    resumen += [[], [("Layout", NEGRITA)], ["Mesas", (s.get("mesas"), MILES)], ["Módulos", (s.get("modulos"), MILES)],
                ["Área construible (ha)", (s.get("area_construible_ha"), DEC2)], ["Área de arriendo (ha)", (s.get("area_arriendo_ha"), DEC2)]]

    sup = [[("Supuestos del modelo", TITULO)] + [("", TITULO)] * len(fin), [],
           [("Supuesto", NEGRITA)] + [(f"GD {i}", NEGRITA) for i in range(1, len(fin) + 1)]]
    vistos = set()
    for clave, etiqueta, estilo in _SUPUESTOS:
        vistos.add(clave)
        if any(clave in (f.get("supuestos") or {}) for f in fin):
            sup.append([etiqueta] + [((f.get("supuestos") or {}).get(clave), estilo) for f in fin])
    sup += [[], [("Otros supuestos", NEGRITA)]]
    for clave in sorted(set(k for f in fin for k in (f.get("supuestos") or {})) - vistos):
        vals = [(f.get("supuestos") or {}).get(clave) for f in fin]
        if all(isinstance(v, (int, float, str, bool, type(None))) for v in vals):
            sup.append([clave.replace("_", " ")] + [(v, DEC2) if isinstance(v, float) else v for v in vals])

    hojas = [("Resumen", resumen, [38] + [18] * (len(fin) + 1)), ("Supuestos", sup, [40] + [20] * len(fin))]
    cab = ["Año de operación", "Año", "Generación (MWh)", "Precio (COP/kWh)", "Ingresos (COP)", "OPEX (COP)", "EBITDA (COP)",
           "Servicio de deuda (COP)", "Flujo de caja equity SAS (COP)"]
    claves = ["anio_op", "anio", "gen_mwh", "precio", "ingresos", "opex", "ebitda", "servicio_deuda", "fcf_equity_sas"]
    estilos = [MILES, None, MILES, DEC2, MILES, MILES, MILES, MILES, MILES]
    for i, f in enumerate(fin, 1):
        filas = [[(f"Flujo anual · GD {i} · {proyecto}", TITULO)] + [("", TITULO)] * (len(cab) - 1), [], [(c, NEGRITA) for c in cab]]
        for a in f.get("anual") or []:
            filas.append([a.get(k) if e is None else (a.get(k), e) for k, e in zip(claves, estilos)])
        hojas.append((f"Flujo GD {i}", filas, [16, 8, 16, 16, 20, 20, 20, 22, 26]))
    return xlsx.crear(hojas, logo=_logo())


def _ruta(d, claves):
    for k in claves:
        d = d.get(k) if isinstance(d, dict) else None
    return d


# ---------------------------------------------------------------- datos para el CRM
def datos_crm(lead, resultado, gisr, red_cercana, fecha):
    """Hoja con los campos de la hoja «Registro» del CRM que el evaluador conoce, con el mismo nombre de columna,
    para copiarlos al registro del proyecto. No escribe en el libro del CRM."""
    s, p = (resultado or {}).get("resumen") or {}, (resultado or {}).get("params") or {}
    mun = (gisr or {}).get("municipio") or {}
    pend = ((gisr or {}).get("pendiente") or {}).get("promedio")
    lineas = [r for r in red_cercana or [] if not r.get("es_transformador") and not r.get("es_subestacion")]
    r0 = lineas[0] if lineas else {}
    sinap = any(h.get("nivel") == "rojo" and h.get("dataset") in ("PNN_LIMITES", "RUNAP", "AREAS_PROTEGIDAS_CAR")
                for h in (gisr or {}).get("hallazgos") or [])
    num = lead.get("proyecto", "").replace("GD ", "GD-")
    contacto = lead.get("contacto") or ""
    campos = [
        ("ID_Proyecto", num), ("Fecha registro", fecha), ("Nombre proyecto", "GD " + (lead.get("carpeta") or "").split(os.sep)[-1].split(" ", 2)[-1]),
        ("Etapa actual", "E1"), ("Municipio", mun.get("nombre") or lead.get("municipio")), ("Departamento", mun.get("depto") or lead.get("depto")),
        ("Vereda / Corregimiento", lead.get("vereda")), ("Operador de Red", r0.get("operador")), ("Propietario", lead.get("propietario")),
        ("Teléfono contacto", "" if "@" in contacto else contacto), ("Correo electrónico", contacto if "@" in contacto else ""),
        ("Canal lead", lead.get("canal")), ("Originador", lead.get("creado_por")), ("No. matrícula", lead.get("matricula")),
        ("No. predial (TEXTO)", lead.get("numero_predial")), ("Área total Ha", (s.get("area_total_ha"), DEC2)),
        ("Área útil Ha", (s.get("area_construible_ha"), DEC2)), ("Área a reservar Ha", (s.get("area_arriendo_ha"), DEC2)),
        ("Pendiente terreno °", (round(math.degrees(math.atan(pend / 100)), 1), DEC2) if pend is not None else None),
        ("Link Google Maps", f"https://www.google.com/maps?q={lead['lat']},{lead['lon']}" if lead.get("lat") is not None else ""),
        ("Canon pactado (COP/Ha/mes)", (p.get("tarifa_arriendo_ha_mes"), MILES)), ("Tecnología", "Solar FV"),
        ("Potencia DC kWp", (s.get("dc_kwp"), DEC2)), ("Potencia AC kW", (s.get("ac_kw_total"), MILES)),
        ("PVOUT kWh/kWp-año", (((gisr or {}).get("pvout") or {}).get("anual"), MILES)),
        ("Nombre circuito", r0.get("nombre")), ("Nivel tensión", r0.get("tension")), ("Distancia línea (mts)", (r0.get("distancia_m"), MILES)),
        ("Área SINAP / Amortiguación", "Sí" if sinap else "No"), ("Carpeta", (lead.get("carpeta") or "").split(os.sep)[-1]),
        ("Ruta etapa", "01 Pipeline/E1"),
    ]
    filas = [[("Datos para la hoja Registro del CRM", TITULO), ("", TITULO)],
             ["Generado por el Evaluador GD. Copia cada valor a la columna del mismo nombre; las columnas calculadas del CRM no se tocan."], [],
             [("Columna del CRM", NEGRITA), ("Valor", NEGRITA)]]
    filas += [[k, v if v not in (None, "") and v != (None, DEC2) and v != (None, MILES) else "por completar"] for k, v in campos]
    return xlsx.crear([("CRM", filas, [34, 60])], logo=_logo())


# ---------------------------------------------------------------- plano DXF
def _dxf_polilinea(capa, pts, cerrada=True, color=None):
    out = ["0", "POLYLINE", "8", capa, "66", "1", "70", "1" if cerrada else "0"]
    if color:
        out += ["62", str(color)]
    for x, y in pts:
        out += ["0", "VERTEX", "8", capa, "10", f"{x:.3f}", "20", f"{y:.3f}", "30", "0.0"]
    return out + ["0", "SEQEND", "8", capa]


def _dxf_texto(capa, x, y, alto, texto):
    return ["0", "TEXT", "8", capa, "10", f"{x:.3f}", "20", f"{y:.3f}", "30", "0.0", "40", f"{alto:.2f}", "1",
            texto.encode("ascii", "replace").decode("ascii")]


def plano_dxf(datos):
    """datos: proyecto, capas [{nombre, rol, tipo, coords}], mesas [{coords, gd}], elementos [{nombre, tipo, coords}].
    Coordenadas de entrada en lon/lat; el plano sale en CTM12 (metros)."""
    mesas = datos.get("mesas") or []
    if not mesas:
        raise ValueError("Primero evalúa el predio.")
    xy = lambda c: [P.a_ctm12(lon, lat) for lon, lat in c]  # noqa: E731
    colores = [5, 3, 6, 1, 30, 82]  # índices de color AutoCAD para GD 1..6
    n_gd = max(int(m.get("gd") or 0) for m in mesas) + 1
    capas = {"CONSTRUIBLE": 3, "EXCLUSION": 1, "PREDIO": 2, "LINEAS": 4, "ELEMENTOS": 7, "TEXTOS": 7}
    capas.update({f"MESAS_GD{g + 1}": colores[g % len(colores)] for g in range(n_gd)})
    ent = []
    for c in datos.get("capas") or []:
        if len(c.get("coords") or []) < 2:
            continue
        rol, pol = c.get("rol"), c.get("tipo") == "Polygon"
        capa = "CONSTRUIBLE" if rol == "construible" else "EXCLUSION" if str(rol).startswith("exclusion") else "PREDIO" if pol else "LINEAS"
        ent += _dxf_polilinea(capa, xy(c["coords"]), pol)
    por_gd = {}
    for m in mesas:
        pts = xy(m["coords"])
        g = int(m.get("gd") or 0)
        por_gd.setdefault(g, []).append(pts)
        ent += _dxf_polilinea(f"MESAS_GD{g + 1}", pts)
    for g, lista in sorted(por_gd.items()):  # rótulo de cada GD en el centro de sus mesas
        cx = sum(p[0] for pts in lista for p in pts) / (4 * len(lista))
        cy = sum(p[1] for pts in lista for p in pts) / (4 * len(lista))
        ent += _dxf_texto("TEXTOS", cx, cy, 4.0, f"GD {g + 1} - {len(lista)} mesas")
    for e in datos.get("elementos") or []:
        if len(e.get("coords") or []) >= 2:
            pts = xy(e["coords"])
            ent += _dxf_polilinea("ELEMENTOS", pts, bool(e.get("cerrado")))
            ent += _dxf_texto("TEXTOS", pts[0][0], pts[0][1], 1.5, str(e.get("nombre") or ""))
    xs = [float(v) for i, v in enumerate(ent) if i and ent[i - 1] == "10"]
    ys = [float(v) for i, v in enumerate(ent) if i and ent[i - 1] == "20"]
    # el DXF R12 no admite imágenes incrustadas: la marca va como texto en el rótulo (#46)
    ent += _dxf_texto("TEXTOS", min(xs), min(ys) - 7, 4.5, "SOLCOR COLOMBIA S.A.S.")
    ent += _dxf_texto("TEXTOS", min(xs), min(ys) - 12, 3.0,f"{datos.get('proyecto') or 'GD'} - Layout preliminar - Evaluador GD - "
                      f"MAGNA-SIRGAS Origen Nacional CTM12 (m)")
    cab = ["0", "SECTION", "2", "HEADER", "9", "$ACADVER", "1", "AC1009", "9", "$INSUNITS", "70", "6",
           "9", "$EXTMIN", "10", f"{min(xs):.3f}", "20", f"{min(ys):.3f}", "9", "$EXTMAX", "10", f"{max(xs):.3f}", "20", f"{max(ys):.3f}",
           "0", "ENDSEC", "0", "SECTION", "2", "TABLES", "0", "TABLE", "2", "LAYER", "70", str(len(capas))]
    for nombre, color in capas.items():
        cab += ["0", "LAYER", "2", nombre, "70", "0", "62", str(color), "6", "CONTINUOUS"]
    cab += ["0", "ENDTAB", "0", "ENDSEC", "0", "SECTION", "2", "ENTITIES"]
    return ("\r\n".join(cab + ent + ["0", "ENDSEC", "0", "EOF"]) + "\r\n").encode("ascii")
