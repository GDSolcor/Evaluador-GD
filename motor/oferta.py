"""
Oferta económica para el propietario del predio (PDF apaisado, 4 páginas), con la estructura de las propuestas
de arriendo enviadas por Solcor (GD Pantanillo, GD Lérida):

  1. Portada: capacidad, energía, ubicación y propietario.
  2. Oferta económica: canon mensual y anual, total del contrato, fases del acuerdo e ingreso año a año.
  3. Características del proyecto, ubicación de la planta y beneficio ambiental.
  4. Beneficios, alcance, pasos a seguir y contacto.

Cálculo del ingreso (igual que la calculadora de oferta de arriendo):
  canon anual año k = canon mensual × 12 × (1 + IPC)^(k-1)
  total = prima de exclusividad + canon mensual × meses de construcción + suma de los años de operación
"""
import base64
import datetime as dt
import math
import os

from pdf import Pdf, ancho_texto

BASE = os.path.dirname(os.path.abspath(__file__))
LOGO = os.path.join(BASE, "..", "web", "marca", "solcor_negativo.png")
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
NAVY, NAVY2, VERDE, LIMA, AMARILLO, GRIS, FONDO, TINTA, SUAVE = "#0d2436", "#16344b", "#23922f", "#7cc01f", "#e5de00", "#5a6776", "#f3f6f4", "#0d2436", "#e6f4e3"
# equivalencias ambientales (las mismas de las propuestas enviadas)
FACTOR_CO2, KWH_HOGAR_MES, KG_ARBOL, T_VEHICULO = 0.16, 160, 22, 2.1
CONTACTO = dict(cargo="Gerente de Desarrollo", nombre="Manuel Antonio Bayona Cely", correo="m.bayona@solcor.com.co",
                empresa="Solcor Colombia S.A.S. · NIT 901.582.696-0")


def _n(v, dec=0):
    return f"{v:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _cop(v):
    return "$" + _n(round(v))


def _millones(v):
    return f"${_n(v / 1e6)} millones"


def calcular(d):
    """Cifras de la oferta a partir de los datos de la evaluación. Se usa también en las pruebas."""
    canon = float(d.get("canon_mes") or 0)
    if canon <= 0:
        raise ValueError("La evaluación no tiene canon de arriendo: revisa la sección de arriendo.")
    ipc = float(d.get("ipc") if d.get("ipc") is not None else 0.035)
    anios = int(d.get("anios_operacion") or 30)
    m_des, m_con, m_desm = int(d.get("meses_desarrollo") or 6), int(d.get("meses_construccion") or 9), int(d.get("meses_desmantelamiento") or 6)
    prima = float(d.get("prima") or 0)
    anual = [canon * 12 * (1 + ipc) ** k for k in range(anios)]
    pago_con = canon * m_con
    mwh = float(d.get("mwh") or 0)
    co2 = mwh * FACTOR_CO2
    ha = float(d.get("area_m2") or 0) / 10000
    return dict(canon=canon, ha=ha, canon_ha=canon / ha if ha > 0 else None, anual=anual, pago_construccion=pago_con, prima=prima, total=prima + pago_con + sum(anual), ipc=ipc, anios=anios,
                m_des=m_des, m_con=m_con, m_desm=m_desm, co2=co2, hogares=mwh * 1000 / (KWH_HOGAR_MES * 12),
                arboles=co2 * 1000 / KG_ARBOL, vehiculos=co2 / T_VEHICULO)


def _img(b64):
    if not b64:
        return None
    try:
        return base64.b64decode(b64.split(",", 1)[1] if "," in b64[:80] else b64)
    except Exception:
        return None


def _encabezado(p, numero, titulo, pagina):
    p.rect(0, 0, p.w, 54, NAVY)
    p.rect(0, 54, p.w, 3, LIMA)
    p.texto(36, 17, numero, 15, True, LIMA)
    p.texto(36 + ancho_texto(numero, 15, True) + 10, 17, "·  " + titulo.upper(), 15, True, "#ffffff")
    p.texto(0, 21, "Confidencial · Solcor Colombia S.A.S.", 9.5, False, "#b8c9d6", "der", p.w - 70)
    p.circulo(p.w - 40, 27, 13, LIMA)
    p.texto(p.w - 53, 20, str(pagina), 12, True, NAVY, "centro", 26)


def _tarjeta(p, x, y, w, h, color="#ffffff", borde="#dde3e6"):
    p.rect(x, y, w, h, color, borde, 10, 0.8)


def generar(d):
    """d: proyecto, propietario, depto, municipio, vereda, kwp, kwac, mwh, canon_mes, prima, meses_*, anios_operacion, ipc,
    area_m2, area_predio_m2, modulos, inclinacion, n_gd, long_mt_m, perimetro_m, mapa (JPEG en base64), codigo, fecha."""
    c = calcular(d)
    hoy = d.get("fecha") if isinstance(d.get("fecha"), dt.date) else dt.date.today()
    proyecto = str(d.get("proyecto") or "GD").strip()
    kwp, kwac, mwh, n_gd = float(d.get("kwp") or 0), float(d.get("kwac") or 0), float(d.get("mwh") or 0), max(1, int(d.get("n_gd") or 1))
    area_m2 = float(d.get("area_m2") or 0)
    sigla = "".join(ch for ch in proyecto.upper().replace("GD", "") if ch.isalnum())[:4] or "GD"
    codigo = d.get("codigo") or f"PROP-GD-{sigla}-001"
    mapa = _img(d.get("mapa"))
    p = Pdf(960, 540, f"Propuesta de arriendo · {proyecto}", "Solcor Colombia S.A.S.")

    # ------------------------------------------------------------ 1. portada
    p.pagina()
    p.rect(0, 0, p.w, p.h, NAVY)
    ancho_txt = 520 if mapa else p.w - 96
    if mapa:
        p.recortar(560, 0, 400, 540)
        try:
            p.imagen(mapa, 480, 0, h=540)
        except ValueError:
            pass
        p.fin_recorte()
        p.rect(556, 0, 6, 540, LIMA)
    if os.path.isfile(LOGO):
        p.imagen(open(LOGO, "rb").read(), 48, 40, h=30)
    p.texto(48, 110, "Propuesta de Arriendo", 36, True, "#ffffff")
    p.texto(48, 160, "Mini granja solar · " + proyecto, 18, False, LIMA, ancho=ancho_txt - 40)
    grande = kwp >= 2000
    kpis = [(_n(kwp / 1000, 2) if grande else _n(kwp), "MWp" if grande else "kWp", "Capacidad instalada"),
            (_n(kwac / 1000, 2) if grande else _n(kwac), "MWac" if grande else "kWac", "Capacidad nominal"),
            (_n(mwh), "MWh/año", "Producción de energía")]
    w_k = (ancho_txt - 48 - 24) / 3
    for i, (valor, unidad, rotulo) in enumerate(kpis):
        x = 48 + i * (w_k + 12)
        p.rect(x, 222, w_k, 96, NAVY2, None, 10)
        p.texto(x + 14, 236, valor, 27, True, "#ffffff")
        p.texto(x + 14 + ancho_texto(valor, 27, True) + 6, 250, unidad, 12, True, LIMA)
        p.texto(x + 14, 286, rotulo, 10.5, False, "#b8c9d6")
    y = 350
    for etiqueta, valor in (("Departamento", d.get("depto")), ("Municipio", d.get("municipio")), ("Vereda", d.get("vereda")),
                            ("Propietario", d.get("propietario"))):
        if valor:
            p.texto(48, y, etiqueta + "  ·  ", 12.5, False, "#b8c9d6")
            p.texto(48 + ancho_texto(etiqueta + "  ·  ", 12.5), y, str(valor), 12.5, True, "#ffffff")
            y += 23
    p.texto(48, 505, f"Documento confidencial  ·  {codigo}  ·  {MESES[hoy.month - 1].capitalize()} de {hoy.year}  ·  Bogotá, Colombia", 9, False, "#8fa5b5")

    # ------------------------------------------------------------ 2. oferta económica
    p.pagina()
    p.rect(0, 0, p.w, p.h, FONDO)
    _encabezado(p, "01", "Oferta económica", 2)
    cifras = [(_cop(c["canon"]), "mensual**" if c["canon_ha"] else "mensual", "Desde · inicio de la fase de construcción"),
              (_cop(c["anual"][0]), "anual", "Ingreso · primer año de operación"),
              (_cop(c["total"]), "", f"Total · {c['anios']} años de operación")]
    for i, (valor, sufijo, nota) in enumerate(cifras):
        x = 36 + i * 300
        _tarjeta(p, x, 74, 288, 84, "#ffffff" if i < 2 else NAVY, None if i == 2 else "#dde3e6")
        col = "#ffffff" if i == 2 else VERDE
        p.texto(x + 16, 86, valor, 25, True, col)
        if sufijo:
            p.texto(x + 16 + ancho_texto(valor, 25, True) + 8, 98, sufijo, 12, True, GRIS)
        p.texto(x + 16, 130, nota, 10, False, "#b8c9d6" if i == 2 else GRIS)
    if c["canon_ha"]:  # valor de referencia por hectárea (#50), en letra pequeña bajo la tarjeta
        p.texto(40, 161, f"** {_cop(c['canon_ha'])} por hectárea al mes ({_n(c['ha'], 2)} ha)", 7.5, False, GRIS)
    p.texto(36, 174, "Fases del acuerdo", 13, True, TINTA)
    fases = [("1", "Desarrollo", f"Plazo ({c['m_des']}) meses", ["El arrendador sigue usando el predio.", "No hay uso ni ocupación."],
              f"Prima de exclusividad · {_cop(c['prima'])}" if c["prima"] else "Exclusividad sobre el área"),
             ("2", "Construcción", f"Plazo ({c['m_con']}) meses", ["Se ocupa e interviene el área arrendada."], f"Pago · {_cop(c['pago_construccion'])}"),
             ("3", "Operación", f"Plazo ({c['anios']}) años", ["La planta solar inicia la venta de energía."], f"Pago anual · {_cop(c['anual'][0])}"),
             ("4", "Desmantelamiento", f"Plazo ({c['m_desm']}) meses", ["Se retiran los equipos y se restituye el predio."], f"Canon vigente del año {c['anios']}")]
    for i, (num, nombre, plazo, lineas, pago) in enumerate(fases):
        x = 36 + i * 224
        _tarjeta(p, x, 196, 214, 120)
        p.circulo(x + 22, 218, 11, VERDE)
        p.texto(x + 11, 212, num, 11, True, "#ffffff", "centro", 22)
        p.texto(x + 40, 208, nombre, 13, True, TINTA)
        p.texto(x + 40, 226, plazo, 9.5, False, GRIS)
        yy = 248
        for ln in lineas:
            yy += p.texto(x + 14, yy, ln, 9.5, False, TINTA, ancho=186)
        p.rect(x + 10, 286, 194, 22, SUAVE, None, 6)
        p.texto(x + 10, 292, pago, 9.5, True, VERDE, "centro", 194)
    # barras: un año de operación por barra
    p.texto(36, 330, "Ingreso anual de arriendo", 13, True, TINTA)
    x0, y_base, alto_max, ancho_total = 36, 492, 118, 888
    paso = ancho_total / c["anios"]
    tope = c["anual"][-1]
    marcados = sorted({0, 9, 19, c["anios"] - 1} & set(range(c["anios"])))
    for k, v in enumerate(c["anual"]):
        h = alto_max * v / tope
        p.rect(x0 + k * paso + 2, y_base - h, paso - 4, h, VERDE if k in marcados else "#9fd0a5", None, 2)
    for k in marcados:
        v = c["anual"][k]
        cx = x0 + k * paso + paso / 2
        h = alto_max * v / tope
        rot = _millones(v)
        aw = ancho_texto(rot, 9.5, True)
        xr = min(max(cx - aw / 2, x0), x0 + ancho_total - aw)
        p.texto(xr, y_base - h - 15, rot, 9.5, True, TINTA)
        p.texto(cx - 30, y_base + 4, f"Año {k + 1}", 9, False, GRIS, "centro", 60)
    p.texto(36, 516, f"Cada barra es un año de operación indexado con el IPC. Proyección con IPC constante de {_n(c['ipc'] * 100, 1)} % anual, únicamente como estimación: "
            "el ajuste real se hará con el IPC certificado cada año por el DANE.", 8, False, GRIS, ancho=888)

    # ------------------------------------------------------------ 3. características del proyecto
    p.pagina()
    p.rect(0, 0, p.w, p.h, FONDO)
    _encabezado(p, "02", "Características del proyecto", 3)
    _tarjeta(p, 36, 74, 300, 300)
    p.texto(52, 88, "Área requerida", 10, False, GRIS)
    p.texto(52, 102, f"{_n(area_m2)} m²", 22, True, VERDE)
    p.texto(52 + ancho_texto(f"{_n(area_m2)} m2", 22, True) + 8, 112, f"({_n(area_m2 / 10000, 2)} ha)", 10.5, False, GRIS)
    trafo = f"{n_gd} × 1 MW · 13,2 kV" if n_gd > 1 else "1 MW · 13,2 kV"
    specs = [("Módulos", f"{_n(d.get('modulos') or 0)} unidades"), ("Estructura", f"Fija sobre suelo · inclinación {_n(float(d.get('inclinacion') or 10))}°"),
             ("Transformador", trafo), ("Cuarto eléctrico", f"{25 * n_gd} m²"),
             ("Línea de conexión", f"{_n(d['long_mt_m'])} metros · 13,2 kV" if d.get("long_mt_m") else "Por definir con el operador de red"),
             ("Cerramiento", f"{_n(d['perimetro_m'])} metros" if d.get("perimetro_m") else "Perimetral del área arrendada")]
    yy = 146
    for etiqueta, valor in specs:
        p.linea(52, yy - 6, 320, yy - 6, "#e3e8e5", 0.6)
        p.texto(52, yy, etiqueta, 9.5, False, GRIS)
        p.texto(52, yy + 13, valor, 11.5, True, TINTA, ancho=268)
        yy += 37
    _tarjeta(p, 348, 74, 576, 300)
    p.texto(364, 86, "Ubicación de la planta", 11, True, TINTA)
    if mapa:
        try:
            p.imagen(mapa, 364, 108, 544, 254, encajar=True)
        except ValueError:
            mapa = None
    if not mapa:
        p.rect(364, 108, 544, 254, "#eef2f0", None, 8)
        p.texto(364, 226, "Imagen del layout no disponible", 11, False, GRIS, "centro", 544)
    # fases del proyecto
    p.texto(36, 388, "Fases del proyecto", 11, True, TINTA)
    for i, (nombre, plazo) in enumerate((("Desarrollo", f"{c['m_des']} meses"), ("Construcción", f"{c['m_con']} meses"), ("Operación", f"{c['anios']} años"))):
        x = 36 + i * 100
        p.rect(x, 408, 92, 44, "#ffffff", "#dde3e6", 8, 0.8)
        p.texto(x, 414, nombre, 9, False, GRIS, "centro", 92)
        p.texto(x, 428, plazo, 12, True, TINTA, "centro", 92)
    # beneficio ambiental
    p.texto(348, 388, "Beneficio ambiental", 11, True, TINTA)
    # sin el bloque de hogares (#50): tres bloques en el mismo ancho
    amb = [(f"{_n(c['co2'])} t", "Emisiones de CO2 evitadas al año"),
           (_n(c["arboles"]), "Árboles equivalentes"), (f"+{_n(math.floor(c['vehiculos']))}", "Vehículos que dejan de circular")]
    for i, (valor, rotulo) in enumerate(amb):
        x = 348 + i * 195
        p.rect(x, 408, 186, 62, SUAVE, None, 8)
        p.texto(x, 416, valor, 17, True, VERDE, "centro", 186)
        p.texto(x + 8, 442, rotulo, 8.5, False, TINTA, "centro", 170)
    p.texto(36, 484, "Energía limpia y renovable  ·  Cero emisiones  ·  Cero ruido", 10.5, True, VERDE)
    p.texto(36, 506, f"Equivalencias calculadas sobre los {_n(mwh)} MWh del primer año. Factor de emisión del SIN {_n(FACTOR_CO2, 2)} tCO2/MWh. "
            f"Captura por árbol {KG_ARBOL} kg CO2/año y emisión de un vehículo particular {_n(T_VEHICULO, 1)} tCO2/año.", 8, False, GRIS, ancho=888)

    # ------------------------------------------------------------ 4. pasos a seguir
    p.pagina()
    p.rect(0, 0, p.w, p.h, FONDO)
    _encabezado(p, "03", "Pasos a seguir", 4)
    area_predio = float(d.get("area_predio_m2") or 0)
    ocupa = f"Se cerca y se ocupa {_n(area_m2 / 10000, 2)} ha"
    if area_predio > area_m2 * 1.02:
        ocupa += f", el {_n(100 * area_m2 / area_predio)} % del predio,"
    ocupa += f" durante los {c['anios']} años del contrato."
    bloques = [("Beneficios", VERDE, ["Ingreso fijo anual que no depende del clima ni del precio de los productos del campo.",
                                      "Diversificación de ingresos y actividad económica en su predio.",
                                      "Cerramiento perimetral y monitoreo permanente sobre el área arrendada.",
                                      "El predio restante sigue disponible para su actividad de siempre."]),
               ("Alcance", NAVY, [ocupa, f"Habrá tránsito de maquinaria y personal durante los {c['m_con']} meses de obra.",
                                  "La línea de interconexión deja una franja de servidumbre sobre el predio.",
                                  "La exclusividad sobre el área es plena durante toda la fase de desarrollo.",
                                  "Si el proyecto no resulta viable en desarrollo, el contrato termina"
                                  + (", usted conserva la prima" if c["prima"] else "") + " y el predio queda libre."])]
    for i, (titulo, color, items) in enumerate(bloques):
        x = 36 + i * 450
        _tarjeta(p, x, 74, 438, 196)
        p.rect(x, 74, 438, 30, color, None, 10)
        p.rect(x, 94, 438, 10, color)
        p.texto(x + 16, 82, titulo, 12.5, True, "#ffffff")
        yy = 116
        for it in items:
            p.circulo(x + 20, yy + 7, 2.6, color)
            yy += p.texto(x + 32, yy, it, 10, False, TINTA, ancho=392) + 5
    pasos = [("A", "Evaluación", "Tiempo estimado (1) semana", ["Análisis de prefactibilidad.", "Presentación de la oferta comercial.", "Atención de observaciones."]),
             ("B", "Revisión contractual", "Tiempo estimado (15) días", ["Revisión del contrato.", "Estudio de títulos preliminar.", "Diagnóstico jurídico predial."]),
             ("C", "Firma del contrato", "Tiempo estimado (15) días",
              (["Pago de la prima de exclusividad."] if c["prima"] else []) + ["Inicio de la fase de desarrollo."])]
    for i, (letra, nombre, tiempo, items) in enumerate(pasos):
        x = 36 + i * 300
        _tarjeta(p, x, 284, 288, 132)
        p.circulo(x + 26, 310, 14, LIMA)
        p.texto(x + 12, 302, letra, 14, True, NAVY, "centro", 28)
        p.texto(x + 50, 296, nombre, 13, True, TINTA)
        p.texto(x + 50, 314, tiempo, 9.5, False, GRIS)
        yy = 340
        for it in items:
            p.circulo(x + 22, yy + 6, 2.2, VERDE)
            yy += p.texto(x + 32, yy, it, 10, False, TINTA, ancho=244) + 4
    p.rect(36, 432, 888, 72, NAVY, None, 10)
    p.texto(56, 446, CONTACTO["cargo"], 9.5, False, "#b8c9d6")
    p.texto(56, 461, f"{CONTACTO['nombre']}  ·  {CONTACTO['correo']}", 13, True, "#ffffff")
    p.texto(56, 482, CONTACTO["empresa"], 10, False, LIMA)
    p.texto(0, 462, "Propuesta sujeta a los resultados de la fase de desarrollo", 9, False, "#b8c9d6", "der", 904)
    p.texto(36, 514, f"{codigo}  ·  {hoy.day} de {MESES[hoy.month - 1]} de {hoy.year}  ·  Generado con el Evaluador GD", 8, False, GRIS)
    return p.guardar()
