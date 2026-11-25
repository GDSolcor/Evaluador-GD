"""
Entregables Word del evaluador, con el formato de los documentos de referencia de Solcor:
  - Estudio de Titulos Preliminar   (ref. GD 0017 Monsalve, 3 Juridico/Estudio_de_titulos)
  - Reporte de Caracterizacion Predial (ref. GD 0017 Monsalve, 1 Predial/01_Identificacion)

Ambos son borradores para revision: se arman con los datos del CTL, el catastro IGAC y las capas
GIS del evaluador; los campos que no tienen fuente en la herramienta quedan marcados para consulta manual.
"""
import base64
import datetime as dt
import os
import re

from docx import Docx, NIVELES, AZUL, GRIS_ETQ

BASE = os.path.dirname(os.path.abspath(__file__))
# logo sobre fondo blanco opaco: con fondo transparente, Word en modo oscuro deja «SOLCOR» negro sobre negro (#46).
# Se regenera con herramientas/logo_fondo_blanco.py
LOGO = os.path.join(BASE, "..", "web", "marca", "solcor_documentos.png")
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _logo():
    try:
        return open(LOGO, "rb").read()
    except OSError:
        return None


def _f(iso):
    """'2026-07-21' -> date"""
    if isinstance(iso, dt.date):
        return iso
    try:
        return dt.date.fromisoformat(str(iso)[:10])
    except ValueError:
        return None


def fecha_larga(d):
    d = _f(d)
    return f"{d.day} de {MESES[d.month - 1]} de {d.year}" if d else ""


def fecha_corta(d):
    d = _f(d)
    return d.strftime("%d / %m / %Y") if d else ""


def num(v, dec=0):
    if v is None:
        return "—"
    s = f"{v:,.{dec}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def num_id(s):
    s = str(s or "")
    if "-" in s:
        a, b = s.split("-", 1)
        return num(int(a)) + "-" + b if a.isdigit() else s
    return num(int(s)) if s.isdigit() else s


def titulo_caso(s):
    s = str(s or "").strip()
    if not s:
        return ""
    out = []
    for w in s.lower().split():
        out.append(w if w in ("de", "del", "la", "las", "los", "y", "e", "en") else w.capitalize())
    txt = " ".join(out)
    return txt[:1].upper() + txt[1:]


def _img(b64):
    if not b64:
        return None
    if "," in b64[:80]:
        b64 = b64.split(",", 1)[1]
    try:
        return base64.b64decode(b64)
    except Exception:
        return None


def dms(lon, lat):
    def conv(v, pos, neg):
        h = pos if v >= 0 else neg
        v = abs(v)
        g = int(v)
        m = int((v - g) * 60)
        s = (v - g - m / 60) * 3600
        return f"{g}°{m:02d}'{s:04.1f}\" {h}".replace(".", ",")
    return f"{conv(lat, 'N', 'S')} – {conv(lon, 'E', 'O')}"


def _firmas(d, elaboro, aprobo):
    d.espacio(10)
    filas = [[[("Elaboró", {"b": True, "color": AZUL})], [("Aprobó", {"b": True, "color": AZUL})]],
             [elaboro or "", aprobo or ""]]
    d.tabla(filas, [d.ancho_util_cm / 2] * 2, encabezado=False, bordes="FFFFFF")


# =============================================================================================
# ESTUDIO DE TITULOS
# =============================================================================================
def estudio_titulos(r, georef=None, mapa_png=None, elaboro="", aprobo="", propietario=None):
    """r: resultado de ctl.analizar convertido con ctl.a_json (fechas en ISO)."""
    predio = titulo_caso(r.get("nombre_predio")) or "Sin nombre"
    mun, dep, ver = titulo_caso(r.get("municipio")), titulo_caso(r.get("depto")), titulo_caso(r.get("vereda"))
    ubic = ", ".join(x for x in [f"Vereda {ver}" if ver else "", f"Municipio de {mun}" if mun else "", dep] if x)
    d = Docx("ESTUDIO DE TÍTULOS", f"Matrícula Inmobiliaria {r.get('matricula')} · Predio \"{predio}\"", _logo(),
             pie=f"Estudio de Títulos — Matrícula {r.get('matricula')}")
    d.titulo("ESTUDIO DE TÍTULOS", [f"Matrícula Inmobiliaria {r.get('matricula')} - Predio \"{predio}\"", ubic])
    d.p(f"Este estudio de títulos analiza el folio de matrícula inmobiliaria {r.get('matricula')} del predio \"{predio}\", con el fin de "
        "verificar la disponibilidad y validez jurídica del predio para un proyecto de generación distribuida, con base en el certificado de "
        "tradición y libertad suministrado.", alin="both")

    tit = r.get("titulares") or []
    from ctl import coincidencias_propietario, cuota_txt
    # La verificación del oferente se hace aquí, con los datos del formulario al generar el estudio
    # (en el flujo normal el nombre se completa después de leer el CTL, así que la del CTL puede faltar).
    coincide = coincidencias_propietario(tit, propietario)
    riesgos = [x for x in (r.get("riesgos") or []) if not x["hallazgo"].startswith("La persona que ofrece el predio")]
    if propietario and (propietario.get("nombre") or propietario.get("cedula")) and not coincide:
        riesgos.insert(0, dict(nivel="Alto", hallazgo=f"La persona que ofrece el predio ({propietario.get('nombre') or ''} "
                                                      f"{propietario.get('cedula') or ''}) no figura como titular inscrito.".replace("  ", " "),
                               recomendacion="Verificar su calidad (poseedor, heredero no adjudicado, apoderado) y exigir el título o poder correspondiente."))
    r = dict(r, riesgos=riesgos)
    area_ctl = r.get("area_estructurada") or r.get("area_narrativa")
    area_txt = (f"{num(area_ctl / 10000, 2)} hectáreas" if area_ctl else "Sin área en el folio")
    if not r.get("area_estructurada") and r.get("area_narrativa"):
        area_txt += " (según descripción narrativa de linderos; el campo estructurado de área del folio aparece en blanco)"
    if georef and georef.get("encontrado"):
        area_txt += f". Catastro IGAC: {num(georef['area_m2'], 2)} m² (≈ {num(georef['area_ha'], 2)} ha)"
    d.h1("Ficha rápida del predio")
    # Ficha a dos columnas: los datos cortos van en pares por fila; los largos ocupan la fila completa.
    ficha = [
        ["Matrícula inmobiliaria", r.get("matricula", ""), "Área", area_txt],
        ["Círculo registral", titulo_caso(r.get("circulo_nombre") or r.get("circulo")),
         "Número predial", r.get("codigo_catastral") or "No consta en el folio"],
        ["Predio", predio, "Estado del folio", r.get("estado") or "—"],
        ["Ubicación", ubic, "Total de anotaciones", str(r.get("total_anotaciones") or len(r.get("anotaciones") or []))],
        # nombre e identificación en el mismo renglón, para que no se desalineen cuando hay varios titulares
        ["Propietario(s) actual(es)", [[f"{t['nombre_propio']} — " + (f"{'C.C.' if t['tipo_id'] == 'CC' else t['tipo_id']} {num_id(t['id'])}" if t["id"]
                                        else "sin identificación en el folio") + f" ({cuota_txt(t)})"] for t in tit] or "No determinado"],
        ["Linderos", "Descritos en forma narrativa (ver anexo)" if r.get("linderos") else "No consta de forma estructurada en el folio",
         "Fecha del estudio", fecha_larga(r.get("fecha_estudio"))],
        ["Fuente", f"Certificado de Tradición y Libertad, ORIP {titulo_caso(r.get('circulo_nombre'))}, Pin {r.get('pin') or '—'}, "
                   f"expedido el {_f(r.get('fecha_expedicion')).strftime('%d-%m-%Y') if _f(r.get('fecha_expedicion')) else '—'}, "
                   f"con {len(r.get('anotaciones') or [])} anotaciones"],
    ]
    # cada columna de etiquetas mide lo que pide su frase más larga (negrita 9 pt ≈ 0,17 cm por carácter)
    e1 = 0.45 + 0.17 * max(len(f[0]) for f in ficha)
    e2 = 0.45 + 0.17 * max(len(f[2]) for f in ficha if len(f) > 2)
    resto = d.ancho_util_cm - e1 - e2
    d.tabla(ficha, [e1, resto * 0.56, e2, resto * 0.44], encabezado=False, etiquetas=True)

    altos = [x for x in r.get("riesgos", []) if x["nivel"] == "Alto"]
    d.h1("Resumen — " + ("problemas a resolver" if altos else "sin problemas críticos"))
    d.vinetas(r.get("resumen") or [])

    d.h1("1. Titularidad actual")
    d.p("Propietarios actuales reconstruidos a partir de las anotaciones de transferencia del folio de matrícula:", tam=9)
    por = {a["nro"]: a for a in r.get("anotaciones") or []}
    filas = [["Titular", "Cédula / identificación", "Cuota", "Título de adquisición"]]
    for t in tit:
        titulos = []
        for n in t["anotaciones"]:
            a = por.get(n)
            if a:
                txt = f"{titulo_caso(a.get('doc_tipo'))[:3]}. {a.get('doc_num')}" if a.get("doc_tipo") else a.get("doc", "")
                if a.get("doc_fecha"):
                    txt += f" del {a['doc_fecha']}"
                titulos.append(f"{txt} (anot. {n:03d}, {a['acto'].lower()[:60]})")
        filas.append([t["nombre_propio"] + (f" ({t['marca']})" if t["marca"] else ""),
                      f"{'C.C.' if t['tipo_id'] == 'CC' else t['tipo_id']} {num_id(t['id'])}" if t["id"] else "Sin identificación",
                      cuota_txt(t), [[x] for x in titulos] or "—"])
    if len(filas) == 1:
        filas.append(["No fue posible determinar la titularidad actual", "", "", ""])
    d.tabla(filas, [4.6, 3.4, 2.4, d.ancho_util_cm - 10.4], cebra=True)

    d.h1(f"2. Cadena de tradición ({len(por)} anotaciones)")
    d.p("Anotaciones del folio con el documento correspondiente, el acto realizado y las partes intervinientes. "
        "(X = titular de derecho real de dominio; I = titular de dominio incompleto). Las anotaciones canceladas se muestran en gris.", tam=9)
    filas = [["Anot.", "Fecha", "Documento", "Acto", "De", "A"]]
    colores = {}
    for i, a in enumerate(r.get("anotaciones") or [], start=1):
        de = "; ".join(p["nombre_propio"] + (f" ({p['marca']})" if p["marca"] else "") for p in a["personas"] if p["rol"] == "DE")
        aa = "; ".join(p["nombre_propio"] + (f" ({p['marca']})" if p["marca"] else "") + (f" {num(p['pct'], 2)} %" if p.get("pct") else "")
                       for p in a["personas"] if p["rol"] == "A")
        acto = f"{titulo_caso(a.get('categoria'))} - {a['acto'].lower()}" if a.get("categoria") else a["acto"].lower()
        if a.get("cancelada_por"):
            acto += f" [cancelada por anot. {a['cancelada_por']:03d}]"
        if a.get("cancela"):
            acto += f" [cancela anot. {', '.join(f'{n:03d}' for n in a['cancela'])}]"
        filas.append([f"{a['nro']:03d}", a["fecha"], titulo_caso(a.get("doc"))[:90], acto, de, aa])
        if a.get("cancelada_por"):
            colores[i] = {"color": "8A94A6"}
        elif a.get("clase") in ("medida_cautelar", "gravamen", "falsa_tradicion"):
            colores[i] = {"fill": "FDEBD0"}
    d.tabla(filas, [1.1, 1.7, 3.6, 4.3, 3.4, d.ancho_util_cm - 14.1], cebra=False, tam=8, colores_fila=colores)
    resumen_cadena = []
    if r.get("matriculas_base"):
        resumen_cadena.append(f"El folio {r.get('matricula')} se abrió con base en la(s) matrícula(s) {', '.join(r['matriculas_base'])}.")
    if r.get("complementacion"):
        anios = sorted(set(re.findall(r"\b(1[89]\d\d|20\d\d)\b", r["complementacion"])))
        if anios:
            resumen_cadena.append(f"La sección de complementación de tradición cita títulos antecedentes de {', '.join(anios[:6])} sobre el mismo globo de terreno; "
                                  "esa historia es anterior a la apertura del folio actual.")
    if resumen_cadena:
        d.p([("Resumen de la cadena de tradición: ", {"b": True, "color": AZUL}), " ".join(resumen_cadena)], alin="both", tam=9)

    d.h1("3. Análisis de riesgos jurídicos")
    filas = [["Nivel", "Hallazgo", "Recomendación"]]
    fills = {}
    for i, x in enumerate(r.get("riesgos") or [], start=1):
        filas.append([x["nivel"], x["hallazgo"], x["recomendacion"]])
        fills[(i, 0)] = {"fill": NIVELES.get(x["nivel"], "5A6169"), "color": "FFFFFF", "b": True}
    d.tabla(filas, [2.0, 8.2, d.ancho_util_cm - 10.2], fill_celdas=fills, alin=["center", "both", "both"], tam=8.5)

    if propietario and (propietario.get("nombre") or propietario.get("cedula")):
        d.h2("Verificación de la persona que ofrece el predio")
        ok = bool(coincide)
        res = (f"Figura como titular inscrito: {', '.join(t['nombre_propio'] + ' (' + cuota_txt(t) + ')' for t in coincide)}" if ok
               else "NO figura como titular inscrito en el folio")
        d.tabla([["Nombre", propietario.get("nombre") or "—", "Identificación", num_id(re.sub(r'\D', '', propietario.get("cedula") or "")) or "—"],
                 ["Resultado", [res]]],
                [3, 6, 3, d.ancho_util_cm - 12], encabezado=False, etiquetas=True,
                fill_celdas={(1, 1): {"fill": "E8F5E9" if ok else "FDEDEC", "b": True, "color": "2E7D32" if ok else "C0392B"}})

    if georef:
        d.h2("Georreferenciación catastral")
        if georef.get("encontrado"):
            dif = None
            if area_ctl and georef.get("area_m2"):
                dif = 100 * (georef["area_m2"] - area_ctl) / area_ctl
            d.tabla([["Número predial", georef.get("numero_predial", ""), "Número anterior", georef.get("anterior", "")],
                     ["Municipio", georef.get("municipio", ""), "Vereda", titulo_caso(georef.get("vereda"))],
                     ["Área catastral", f"{num(georef['area_m2'], 2)} m²", "Diferencia con el folio", f"{num(dif, 1)} %" if dif is not None else "—"],
                     ["Construcciones", f"{georef.get('construcciones', 0)} ({num(georef.get('area_construida_m2'), 1)} m²)", "Fuente", georef.get("fuente", "")]],
                    [3.2, 5.6, 3.6, d.ancho_util_cm - 12.4], encabezado=False, etiquetas=True)
            if dif is not None and abs(dif) > 10:
                d.nota(f"El área catastral difiere {num(abs(dif), 1)} % del área del folio: se recomienda un levantamiento topográfico y, si aplica, actualización de cabida y linderos.")
        else:
            d.nota("Georreferenciación: " + (georef.get("motivo") or "el número predial no se encontró en el catastro IGAC."))
        if mapa_png:
            img = _img(mapa_png)
            if img:
                d.imagen(img, 15.5, "Figura 1. Predio del folio sobre imagen satelital (catastro IGAC). Fuente: Evaluador GD.")

    d.h1("4. Documentos pendientes y requeridos")
    d.p("Documentos requeridos para completar el estudio y soportar la operación (arrendamiento, servidumbre u otra). Marque \"Disponible\" o \"Pendiente\" según el estado de cada uno.", tam=9, alin="both")
    filas = [["Documento", "Disponible", "Pendiente"]] + [[x, "☐", "☐"] for x in r.get("documentos") or []]
    d.tabla(filas, [d.ancho_util_cm - 4.4, 2.2, 2.2], alin=["both", "center", "center"], cebra=True)

    if r.get("linderos"):
        d.h2("Anexo — Descripción de cabida y linderos (texto del folio)")
        d.p(r["linderos"], tam=8, alin="both", color="444444")

    d.nota("Este documento es un análisis preliminar generado a partir del certificado de tradición y libertad suministrado. No constituye concepto "
           "jurídico definitivo ni asesoría legal vinculante. Debe ser validado por un abogado habilitado antes de proceder con cualquier trámite notarial o registral.")
    _firmas(d, elaboro, aprobo)
    return d.guardar()


# =============================================================================================
# REPORTE DE CARACTERIZACION PREDIAL
# =============================================================================================
_ENTIDADES_RESTRICTIVAS = [
    # entidad, capa, dataset, filtro de capa ANT
    ("PNN", "RUNAP / Límites Parques Nacionales Naturales", ("PNN_LIMITES", "RUNAP"), None),
    ("ANM", "Títulos mineros", ("ANM_Titulo",), None),
    ("ANT", "Resguardos indígenas formalizados", ("ANT",), "Resguardo"),
    ("ANT", "Zonas de Reserva Campesina constituidas", ("ANT",), "Zona_de_Reserva"),
    ("ANT", "Consejos comunitarios titulados", ("ANT",), "Consejo"),
    ("ANT", "Territorios campesinos agroalimentarios", ("ANT",), "Territorios_Campesinos"),
    ("CAR", "Áreas protegidas regionales (CAR)", ("AREAS_PROTEGIDAS_CAR",), None),
    ("ANLA", "Proyectos con licencia ambiental", ("ANLA_Area", "ANLA_Linea"), None),
    ("ANLA", "Proyectos en evaluación de licencia ambiental (líneas)", ("ANLA_Linea_Eval",), None),
    ("ICANH", "Áreas y parques arqueológicos protegidos", ("ICANH_Protegida",), None),
    ("ICANH", "Zonificación arqueológica municipal (planes de manejo)", ("ICANH_PMA",), None),
    ("ICANH", "Sitios arqueológicos (a menos de 300 m) y hallazgos positivos de PAP", ("ICANH_Sitio", "ICANH_Hallazgo"), None),
    ("ANH", "Tierras / áreas de hidrocarburos", ("ANH_Tierras",), None),
    ("ANH", "Pozos de hidrocarburos (a menos de 200 m)", ("ANH_Pozo",), None),
    ("MinAmbiente", "Reservas forestales Ley 2ª de 1959 (zonificación y límite)", ("MINAMBIENTE_Ley2",), None),
    ("MinAmbiente", "Sustracciones definitivas de reserva Ley 2ª", ("MINAMBIENTE_Ley2_Sustraccion",), None),
]
_MANUALES = [
    ("URT", "Restitución de tierras (procesos, demandas y sentencias)", "Consulta manual en el visor DICAT de la URT"),
    ("IDEAM", "Amenaza por creciente súbita / inundación", "Consulta manual en el geovisor ambiental del IDEAM"),
]


def _hallazgos_de(gisr, datasets, filtro=None):
    out = []
    for h in (gisr or {}).get("hallazgos", []):
        if h.get("dataset") in datasets and (not filtro or filtro.lower() in str(h.get("extra", "")).lower()):
            out.append(h)
    return out


def reporte_predial(datos):
    """datos: dict armado por servidor.py (proyecto, predio, catastro, gis, hidro, lectura, mapas, capacidad...)."""
    hoy = _f(datos.get("fecha")) or dt.date.today()
    proyecto = datos.get("proyecto") or "GD"
    cat = datos.get("catastro") or {}
    gisr = datos.get("gis") or {}
    mun = (gisr.get("municipio") or {})
    municipio = titulo_caso(mun.get("nombre")) or (cat.get("municipio", "").split(",")[0])
    depto = titulo_caso(mun.get("depto")) or (cat.get("municipio", ",").split(",")[-1].strip())
    cod_mun = str(mun.get("codigo") or cat.get("codigo_mun") or "")
    prop = datos.get("propietario") or {}
    ctl = datos.get("ctl") or {}
    lect = datos.get("lectura") or {}
    hidro = datos.get("hidro") or []
    cap = datos.get("capacidad") or {}

    d = Docx("REPORTE DE CARACTERIZACIÓN PREDIAL", f"Proyecto de Generación Distribuida · {proyecto} · Rev. 01", _logo())
    d.titulo("REPORTE DE CARACTERIZACIÓN PREDIAL", ["PROYECTO DE GENERACIÓN DISTRIBUIDA", ", ".join(x for x in [municipio, depto] if x)])
    d.p(f"Solcor Colombia S.A.S. presenta a continuación la caracterización predial del terreno propuesto para el proyecto de Generación Distribuida "
        f"{proyecto}, elaborada a partir del cruce de la información catastral del predio (IGAC) con las capas geográficas del evaluador GD "
        "(Parques Nacionales, RUNAP, ANM, ANT, ANLA, áreas protegidas CAR, arqueología ICANH, tierras y pozos de hidrocarburos ANH, "
        "ecosistemas, hidrografía IGAC 1:100.000 y red eléctrica), con el fin de "
        "establecer su idoneidad legal, ambiental y técnica.", alin="both")

    d.h1("Ficha del informe")
    d.tabla([["Etapa", datos.get("etapa") or "Prefactibilidad"],
             ["Alcance", "Caracterización predial y cruce de capas con determinantes sociales, ambientales, bióticos y abióticos para el proyecto de generación distribuida"],
             ["Fecha de elaboración", fecha_corta(hoy)],
             ["Fecha de consulta de capas", fecha_corta(datos.get("fecha_consulta") or hoy)]],
            [5.0, d.ancho_util_cm - 5.0], encabezado=False, etiquetas=True)

    d.h1("Datos del predio y del titular")
    centro = datos.get("centro") or gisr.get("centro")
    area_total = cat.get("area_m2")
    area_res = datos.get("area_reserva_ha")
    titular = prop.get("nombre") or (", ".join(t["nombre_propio"] for t in ctl.get("titulares", [])) if ctl else "")
    cedula = prop.get("cedula") or ("; ".join(num_id(t["id"]) for t in ctl.get("titulares", []) if t.get("id")) if ctl else "")
    tipo = cat.get("tipo") or ("rural" if ctl.get("vereda") else "")
    d.tabla([
        ["Proyecto", proyecto, "Coordenadas aproximadas", dms(*centro) if centro else "—"],
        ["Propietario del predio", titular or "Por confirmar", "Cédula / NIT", cedula or "Por confirmar"],
        ["Municipio", f"{municipio} (Código {cod_mun[2:]})" if cod_mun else municipio, "Departamento", f"{depto} (Código {cod_mun[:2]})" if cod_mun else depto],
        ["Tipo de predio", "Rural (X)" if tipo == "rural" else ("Urbano (X)" if tipo == "urbano" else "—"),
         "Nombre del predio / Vereda", " – ".join(x for x in [titulo_caso(ctl.get("nombre_predio")) or (cat.get("direcciones") or [""])[0].title(), titulo_caso(cat.get("vereda") or ctl.get("vereda"))] if x) or "—"],
        ["Área a reservar para el proyecto", f"{num(area_res, 2)} ha" + (f" ({proyecto})" if proyecto else "") if area_res else "—",
         "Área total del predio (IGAC)", f"{num(area_total, 2)} m² (≈ {num(area_total / 10000, 2)} ha)" if area_total else "—"],
        ["Número predial", cat.get("numero_predial") or ctl.get("codigo_catastral") or "—", "Matrícula inmobiliaria", ctl.get("matricula") or datos.get("matricula") or "Por confirmar"],
    ], [3.6, 5.0, 3.6, d.ancho_util_cm - 12.2], encabezado=False, etiquetas=True)
    if cap:
        d.tabla([["Capacidad estimada", f"{num(cap.get('kwp'), 0)} kWp / {num(cap.get('kwac'), 0)} kWac", "GDs / módulos", f"{cap.get('n_gd', '—')} GD · {num(cap.get('modulos'))} módulos"]],
                [3.6, 5.0, 3.6, d.ancho_util_cm - 12.2], encabezado=False, etiquetas=True)

    # ---------------- hallazgos principales
    restr = [h for h in gisr.get("hallazgos", []) if h.get("nivel") == "rojo"]
    ambar = [h for h in gisr.get("hallazgos", []) if h.get("nivel") == "ambar"]
    info = [h for h in gisr.get("hallazgos", []) if h.get("nivel") == "info"]
    agua_cerca = [h for h in hidro if h.get("distancia_m", 1e9) <= 100]
    d.h2("Hallazgos principales")
    d.p(f"El predio{(' identificado con el número predial ' + cat['numero_predial']) if cat.get('numero_predial') else ''} fue cruzado con las capas "
        "geográficas del evaluador. Los hallazgos se clasificaron en capas de carácter restrictivo e informativo. En síntesis:", alin="both")
    bul = []
    if not restr:
        bul.append([("El predio avanza libre de capas de carácter restrictivo ", {"b": True}),
                    "(Parques Nacionales Naturales / RUNAP, áreas protegidas CAR, resguardos indígenas, consejos comunitarios titulados y zonas de reserva campesina): no se identificaron traslapos."])
    else:
        bul.append([("Se identifican traslapos con capas restrictivas: ", {"b": True, "color": "C0392B"}),
                    "; ".join(f"{h['capa']} – {h['detalle']}" for h in restr) + ". Se requiere validación antes de continuar."])
    if ambar:
        bul.append([("Capas a revisar: ", {"b": True}), "; ".join(f"{h['capa']} – {h['detalle']}{(' (' + str(h['extra']) + ')') if h.get('extra') else ''}" for h in ambar) + "."])
    if hidro:
        h0 = hidro[0]
        bul.append([("Hidrografía: ", {"b": True}),
                    f"el cuerpo de agua más cercano es {h0.get('nombre') or h0.get('capa', '').lower()} ({h0.get('capa')}) a {num(h0.get('distancia_m'))} m del área de interés"
                    + (f"; {len(agua_cerca)} elemento(s) a menos de 100 m generan ronda hídrica a descontar del área útil." if agua_cerca else ".")])
    else:
        bul.append([("Hidrografía: ", {"b": True}), "no se identifican drenajes ni cuerpos de agua IGAC 1:100.000 en el entorno inmediato del predio."])
    pend = gisr.get("pendiente") or {}
    if pend:
        bul.append([("Topografía: ", {"b": True}), f"pendiente media {num(pend.get('promedio'), 1)} % (P90 {num(pend.get('p90'), 1)} %)"
                    + (" — aceptable para montaje en mesa fija." if (pend.get("p90") or 0) <= 15 else " — superior al 15 %: requiere análisis topográfico detallado.")])
    red = gisr.get("red_electrica") or []
    if red:
        r0 = red[0]
        bul.append([("Conexión: ", {"b": True}), f"{r0.get('tipo')} de {r0.get('operador')} ({r0.get('nombre')}) a {num(r0.get('distancia_m'))} m."])
    pv = (gisr.get("pvout") or {}).get("anual")
    if pv:
        bul.append([("Recurso solar: ", {"b": True}), f"PVOUT {num(pv, 0)} kWh/kWp·año (Solargis)."])
    d.vinetas(bul)

    # ---------------- informacion catastral
    d.h1("Información catastral")
    d.p("La siguiente identificación se realizó con base en el catastro público del IGAC (corte 31-ago-2026) incluido en el evaluador, "
        "a partir de la ubicación del predio.", alin="both", tam=9)
    if cat.get("encontrado"):
        d.tabla([["Número predial actual", "Número predial anterior", "Dirección", "Área del terreno", "Área de construcción"],
                 [cat.get("numero_predial", ""), cat.get("anterior", ""), "; ".join(cat.get("direcciones") or []) or "—",
                  f"{num(cat.get('area_m2'), 2)} m²", f"{num(cat.get('area_construida_m2'), 2)} m²"],
                 [[("Número de construcciones", {"b": True, "color": AZUL})], str(cat.get("construcciones", 0)),
                  [("Fuente", {"b": True, "color": AZUL})], cat.get("fuente", "Catastro IGAC")]],
                [4.0, 3.4, 3.6, 3.2, d.ancho_util_cm - 14.2], alin="center", tam=8.5)
    else:
        d.nota("No se encontró el predio en el catastro público IGAC: " + (cat.get("motivo") or "sin número predial") + ".")
    mapas = datos.get("mapas") or {}
    img = _img(mapas.get("catastral"))
    if img:
        d.imagen(img, 16.5, f"Figura 1. Predio {cat.get('numero_predial', '')} sobre imagen satelital, con el catastro IGAC. Fuente: Evaluador GD (Esri World Imagery, IGAC).")

    # ---------------- lectura satelital
    d.h1("Lectura satelital del terreno")
    d.p("Conforme al análisis de la imagen satelital del predio y de las capas del evaluador se identifican los siguientes hallazgos:", tam=9)

    def sn(v):
        return ("X", "") if v is True else ("", "X") if v is False else ("", "")
    preguntas = [
        ("¿La topografía es aceptable?", lect.get("topografia", (pend.get("p90") or 0) <= 15 if pend else None), ""),
        ("¿Tiene acceso por carretera?", lect.get("acceso"), lect.get("acceso_obs", "")),
        ("¿El predio es bordeado por un cuerpo de agua?", lect.get("agua", bool(agua_cerca) if hidro is not None else None),
         (hidro[0].get("nombre") or hidro[0].get("capa")) if agua_cerca else ""),
        ("¿El predio presenta vegetación arbórea?", lect.get("vegetacion"), lect.get("vegetacion_obs", "")),
        ("¿Se identifica infraestructura existente sobre el predio?", lect.get("infraestructura", (cat.get("construcciones") or 0) > 0 if cat.get("encontrado") else None),
         f"{cat.get('construcciones')} construcción(es) catastral(es)" if cat.get("construcciones") else ""),
    ]
    filas = [["Aspecto", "Sí", "No", "Observación"]]
    for q, v, obs in preguntas:
        s, n = sn(v)
        filas.append([q, s, n, obs or ("Por verificar en visita" if v is None else "")])
    d.tabla(filas, [7.4, 1.3, 1.3, d.ancho_util_cm - 10.0], alin=["left", "center", "center", "left"], cebra=True)
    img = _img(mapas.get("layout"))
    if img:
        d.imagen(img, 16.5, f"Figura 2. {proyecto} – Área a reservar{(': ' + num(area_res, 2) + ' ha') if area_res else ''} y layout preliminar. Fuente: Evaluador GD.")
    if lect.get("observacion"):
        d.p(lect["observacion"], alin="both")

    # ---------------- restricciones
    d.h1("Verificación de restricciones legales y ambientales")
    filas = [["Entidad", "Capa consultada", "Sí", "No", "Observaciones"]]
    fills = {}
    for ent, capa, dss, filtro in _ENTIDADES_RESTRICTIVAS:
        # solo restricción o revisión (p. ej. el mapa de tierras ANH marca como información las áreas no asignadas)
        hs = [h for h in _hallazgos_de(gisr, dss, filtro) if h.get("nivel") != "info"]
        obs = "; ".join(f"{h['detalle']}{(' – ' + str(h['extra'])) if h.get('extra') and not filtro else ''}" for h in hs)[:220] if hs else "Sin traslapo"
        filas.append([ent, capa, "X" if hs else "", "" if hs else "X", obs])
        if hs:
            fills[(len(filas) - 1, 2)] = {"fill": "FDEDEC", "b": True, "color": "C0392B"}
    for ent, capa, obs in _MANUALES[:2]:
        filas.append([ent, capa, "", "", obs])
    d.tabla(filas, [2.0, 5.2, 1.0, 1.0, d.ancho_util_cm - 9.2], alin=["center", "left", "center", "center", "left"], fill_celdas=fills, tam=8.5)

    d.h1("Consultas complementarias")
    filas = [["Entidad / fuente", "Capa consultada", "Sí", "No", "Observaciones"]]
    hs = _hallazgos_de(gisr, ("PRIORIDADES_CONSERVACION",))
    filas.append(["IAvH / MinAmbiente", "Prioridades de conservación", "X" if hs else "", "" if hs else "X", "; ".join(h["detalle"] for h in hs)[:200] or "Sin traslapo"])
    hs = _hallazgos_de(gisr, ("REAA",))
    filas.append(["IDEAM / IAvH", "Ecosistemas (REAA)", "X" if hs else "", "" if hs else "X", "; ".join(h["detalle"] for h in hs)[:200] or "Sin información"])
    hs = _hallazgos_de(gisr, ("ICANH_PAP",))
    filas.append(["ICANH", "Programas de arqueología preventiva previos en el predio", "X" if hs else "", "" if hs else "X",
                  "; ".join(h["detalle"] for h in hs)[:200] or "Sin registros"])
    hs = _hallazgos_de(gisr, ("ANH_Sismica",))
    filas.append(["ANH", "Líneas sísmicas 2D que cruzan el predio", "X" if hs else "", "" if hs else "X",
                  "; ".join(h["detalle"] for h in hs)[:200] or "Sin registros"])
    filas.append(["IGAC", "Drenajes y cuerpos de agua (1:100.000)", "X" if agua_cerca else "", "" if agua_cerca else "X",
                  "; ".join(f"{h.get('nombre') or h.get('capa')} a {num(h.get('distancia_m'))} m" for h in hidro[:4]) or "Sin elementos en el entorno"])
    za = gisr.get("zonas_aptas") or []
    filas.append(["Solcor", "Zonas aptas GD", "X" if za else "", "" if za else "X", "El predio está dentro de una zona apta GD" if za else "Fuera de zonas aptas identificadas"])
    filas.append(["Operadores de red", "Red eléctrica (ENEL, EBSA, ESSA, CENS, EPM, EDEQ)", "X" if red else "", "" if red else "X",
                  "; ".join(f"{x['operador']} {x['tipo']} {x['nombre']} a {num(x['distancia_m'])} m" for x in red[:3]) or "Sin red en 3 km"])
    for ent, capa, obs in _MANUALES[2:]:
        filas.append([ent, capa, "", "", obs])
    d.tabla(filas, [2.6, 4.8, 1.0, 1.0, d.ancho_util_cm - 9.4], alin=["center", "left", "center", "center", "left"], tam=8.5)

    # ---------------- interpretacion
    d.h1("Interpretación de hallazgos")
    d.p(f"La geometría del predio analizada en el presente documento proviene de {datos.get('origen_geometria') or 'el polígono del evaluador GD'} y de la "
        "identificación catastral IGAC. Se analizaron capas geográficas de carácter restrictivo y capas de carácter informativo:", alin="both")
    if restr:
        d.p([("Capas restrictivas: ", {"b": True}), "se observan traslapos con " + "; ".join(f"{h['capa']} ({h['detalle']})" for h in restr) +
             ". Estas áreas deben excluirse o validarse ante la entidad competente antes de continuar con el desarrollo."], alin="both")
    else:
        d.p([("Capas restrictivas: ", {"b": True}), "no se observan zonas con restricciones (PNN/RUNAP, áreas protegidas CAR, reservas forestales de Ley 2ª, "
             "territorios étnicos y campesinos ANT), "
             "por lo que el predio se encuentra libre para el proyecto en cuanto a estas capas. Las consultas marcadas como manuales (URT, IDEAM) "
             "deben completarse en los geovisores de cada entidad."], alin="both")
    txt_info = []
    if ambar:
        txt_info.append("se identifican capas a revisar: " + "; ".join(f"{h['capa']} ({h['detalle']})" for h in ambar))
    if hidro:
        txt_info.append(f"el predio tiene {len(hidro)} elemento(s) hídrico(s) en su entorno; el más cercano a {num(hidro[0].get('distancia_m'))} m, cuya ronda de protección se descuenta del área útil en el layout")
    if info:
        txt_info.append("aplican clasificaciones informativas: " + "; ".join(sorted({h['detalle'] for h in info}))[:300])
    d.p([("Capas informativas: ", {"b": True}), (". ".join(txt_info) + ".") if txt_info else "sin hallazgos informativos relevantes."], alin="both")

    d.h1("Fuentes de información")
    d.vinetas(["IGAC – Catastro público (corte 31-ago-2026) e hidrografía 1:100.000",
               "GIS Solcor – PNN, RUNAP, áreas protegidas CAR, ANT, ANM, ANLA, prioridades de conservación, REAA y red eléctrica",
               "Solargis – PVOUT; modelo de pendientes GIS Solcor (~30 m)",
               "Esri World Imagery – lectura satelital del predio"])
    _firmas(d, datos.get("elaboro"), datos.get("aprobo"))
    return d.guardar()


# =============================================================================================
# FICHA TECNICA DEL PROYECTO EVALUADO
# =============================================================================================
def ficha_tecnica(datos):
    """Resumen de una hoja del dimensionamiento y sus resultados.
    datos: proyecto, resultado (params, mesa, gds, resumen), financiero (lista por GD), gis, red, mapa, elaboro, aprobo."""
    hoy = _f(datos.get("fecha")) or dt.date.today()
    proyecto = datos.get("proyecto") or "GD"
    res = datos.get("resultado") or {}
    s, p, mesa, gds = res.get("resumen") or {}, res.get("params") or {}, res.get("mesa") or {}, res.get("gds") or []
    fin = datos.get("financiero") or []
    gisr = datos.get("gis") or {}
    mun = gisr.get("municipio") or {}
    lugar = ", ".join(x for x in [titulo_caso(mun.get("nombre")), titulo_caso(mun.get("depto"))] if x)
    centro = datos.get("centro") or gisr.get("centro")
    d = Docx("FICHA TÉCNICA DEL PROYECTO", f"Proyecto de Generación Distribuida · {proyecto}", _logo(),
             pie=f"Ficha técnica — {proyecto}")
    d.titulo("FICHA TÉCNICA DEL PROYECTO", [proyecto, lugar] if lugar else [proyecto])
    w = d.ancho_util_cm
    cols = [3.9, w / 2 - 3.9, 3.9, w / 2 - 3.9]

    d.h1("Datos generales")
    d.tabla([
        ["Proyecto", proyecto, "Fecha", fecha_larga(hoy)],
        ["Municipio", titulo_caso(mun.get("nombre")) or "—", "Departamento", titulo_caso(mun.get("depto")) or "—"],
        ["Coordenadas", dms(*centro) if centro else "—", "Número predial", datos.get("codigo_predial") or "—"],
        ["Área construible", f"{num(s.get('area_construible_ha'), 2)} ha", "Área del predio", f"{num(s.get('area_total_ha'), 2)} ha"],
        ["Área de arriendo", f"{num(s.get('area_arriendo_ha'), 2)} ha", "Canon de arriendo", f"$ {num(s.get('arriendo_anio'))} COP/año"],
    ], cols, encabezado=False, etiquetas=True)

    d.h1("Dimensionamiento")
    orient = "horizontal" if str(p.get("orientacion", "")).startswith("h") else "vertical"
    inv = sum(g.get("inversores", 0) for g in gds)
    dc, ac = s.get("dc_kwp") or 0, s.get("ac_kw_total") or 0
    d.tabla([
        ["Potencia DC", f"{num(dc, 1)} kWp ({num(dc / 1000, 2)} MWp)", "Potencia AC", f"{num(ac, 0)} kW"],
        ["Relación DC/AC", num(dc / ac, 2) if ac else "—", "Bloques GD", f"{s.get('n_gd', 0)} (máx. {num(p.get('ac_por_gd_kw'), 0)} kW AC c/u)"],
        ["Módulos", f"{num(s.get('modulos'))} × {num(p.get('modulo_wp'), 0)} Wp", "Modelo de módulo", p.get("modulo_nombre") or p.get("nombre") or "—"],
        ["Mesas", f"{num(s.get('mesas'))} de {mesa.get('modulos', '—')} módulos ({num(mesa.get('kwp'), 1)} kWp)",
         "Configuración de mesa", f"{p.get('filas')} filas × {p.get('columnas')} columnas, módulo {orient}"],
        ["Dimensiones de mesa", f"{num(mesa.get('ancho'), 2)} × {num(mesa.get('profundidad'), 2)} m", "Inclinación / azimut", f"{num(p.get('inclinacion'), 1)}° / {num(p.get('azimut', 180), 1)}°"
         + (" (sur)" if abs(float(p.get("azimut", 180)) - 180) < 0.05 else f" ({num(abs(float(p.get('azimut', 180)) - 180), 1)}° al {'este' if float(p.get('azimut', 180)) < 180 else 'oeste'} del sur)")],
        ["Pasillo entre filas", f"{num(p.get('pasillo'), 2)} m", "Retiro al lindero", f"{num(p.get('retiro'), 1)} m"],
        ["Inversores", f"{inv} × {num(p.get('potencia_inversor_kw'), 0)} kW", "Densidad", f"{num(s.get('densidad_kwp_ha'), 0)} kWp/ha"],
        ["Energía año 1", f"{num(s.get('energia_mwh_anio'), 0)} MWh/año", "Rendimiento específico", f"{num(p.get('rendimiento_kwh_kwp'), 0)} kWh/kWp·año"],
    ], cols, encabezado=False, etiquetas=True)
    if s.get("limitado_por"):
        d.nota(f"El layout está limitado por {s['limitado_por']}: se usan {num(s.get('mesas'))} de las {num(s.get('mesas_disponibles'))} mesas que caben en el área.")

    if gds:
        d.h2("Bloques de generación distribuida")
        filas = [["Bloque", "Mesas", "Módulos", "DC (kWp)", "AC (kW)", "DC/AC", "Inversores"]]
        for i, g in enumerate(gds, 1):
            filas.append([f"GD {i}", num(g["mesas"]), num(g["mesas"] * (mesa.get("modulos") or 0)), num(g["dc_kwp"], 1), num(g["ac_kw"], 0),
                          num(g["dc_ac"], 2), str(g.get("inversores", "—"))])
        filas.append(["Total", num(s.get("mesas")), num(s.get("modulos")), num(dc, 1), num(ac, 0), num(dc / ac, 2) if ac else "—", str(inv)])
        d.tabla(filas, [w * x for x in (.16, .13, .15, .15, .14, .12, .15)], cebra=True, alin=["left"] + ["center"] * 6,
                colores_fila={len(filas) - 1: {"fill": GRIS_ETQ, "b": True, "color": AZUL}})
        if len(gds) > 1 and not s.get("filas_compartidas"):
            d.nota("Cada bloque GD empieza en una fila nueva: dos bloques no comparten fila física de mesas.")

    red = datos.get("red") or []
    lineas = [r for r in red if not r.get("es_transformador")][:4]
    d.h1("Conexión a la red")
    if lineas:
        d.tabla([["Operador", "Red / circuito", "Tensión", "Distancia"]] +
                [[r.get("operador") or "—", ("Subestación " if r.get("es_subestacion") else "") + str(r.get("nombre") or "—"),
                  r.get("tension") or "—", f"{num(r.get('distancia_m'))} m"] for r in lineas],
                [w * .16, w * .50, w * .16, w * .18], cebra=True, alin=["left", "left", "center", "center"])
    else:
        d.p("La base no tiene redes de media tensión a menos de 5 km del proyecto, o no cubre al operador de red de la zona. "
            "Se debe confirmar el punto de conexión con el operador.", alin="both")

    d.h1("Restricciones y recurso")
    hall = gisr.get("hallazgos") or []
    rojos, ambar = [h for h in hall if h.get("nivel") == "rojo"], [h for h in hall if h.get("nivel") == "ambar"]
    sem = {"rojo": "Restricción fuerte", "ambar": "Revisar", "verde": "Sin alertas"}.get(gisr.get("semaforo"), "—")
    pv, pend = (gisr.get("pvout") or {}).get("anual"), gisr.get("pendiente") or {}
    d.tabla([
        ["Semáforo GIS", sem, "PVOUT Solargis", f"{num(pv, 0)} kWh/kWp·año" if pv else "—"],
        ["Pendiente media / P90", f"{num(pend.get('promedio'), 1)} % / {num(pend.get('p90'), 1)} %" if pend else "Sin cobertura",
         "Ronda hídrica aplicada", f"{num(p.get('ronda_hidrica_m'), 0)} m"],
    ], cols, encabezado=False, etiquetas=True,
        fill_celdas={(0, 1): {"fill": NIVELES.get(gisr.get("semaforo"), "5A6169"), "color": "FFFFFF", "b": True}})
    if rojos or ambar:
        d.vinetas([[(("Restricción: " if h in rojos else "Revisar: "), {"b": True, "color": "C0392B" if h in rojos else "B7791F"}),
                    f"{h.get('capa')} – {h.get('detalle')}"] for h in (rojos + ambar)[:10]])
    else:
        d.p("El área construible no se cruza con capas restrictivas ni de revisión de la base GIS.", tam=9)

    if fin:
        d.h1("Indicadores financieros")
        pct = lambda v: f"{num(100 * v, 1)} %" if v is not None else "—"  # noqa: E731
        mm = lambda v: f"$ {num(v / 1e6, 0)} M" if v is not None else "—"  # noqa: E731
        filas = [["Bloque", "CAPEX", "COP/kWp", "TIR equity (SAS)", "TIR proyecto", "EBITDA año 1", "DSCR mín."]]
        for i, f in enumerate(fin, 1):
            filas.append([f"GD {i}", mm(f["capex"]["total"]), f"$ {num(f['capex']['cop_kwp'])}", pct(f["tir"].get("equity_sas")),
                          pct(f["tir"].get("proyecto_pretax")), mm(f["anio1"]["ebitda"]), num(f.get("dscr_min"), 2) if f.get("dscr_min") else "—"])
        if len(fin) > 1:
            filas.append(["Total", mm(sum(f["capex"]["total"] for f in fin)), "", "", "", mm(sum(f["anio1"]["ebitda"] for f in fin)), ""])
        d.tabla(filas, [w * x for x in (.12, .15, .15, .16, .14, .15, .13)], cebra=True, alin=["left"] + ["center"] * 6)
        d.nota("Cifras en pesos colombianos (M = millones). Modelo financiero preliminar con los supuestos cargados en el evaluador; no reemplaza el modelo del proyecto.")

    img = _img(datos.get("mapa"))
    if img:
        d.h1("Layout preliminar")
        d.imagen(img, w, "Figura 1. Layout preliminar sobre imagen satelital: mesas por bloque GD, área construible y exclusiones. Fuente: Evaluador GD.")

    d.nota("Ficha generada por el Evaluador GD de Solcor a partir del dimensionamiento preliminar. Los valores deben confirmarse con el diseño de detalle, "
           "el estudio de conexión y la simulación energética del proyecto.")
    _firmas(d, datos.get("elaboro"), datos.get("aprobo"))
    return d.guardar()
