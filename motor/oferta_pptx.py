"""
Oferta económica para el propietario en PowerPoint, sobre la plantilla de las propuestas de arriendo de Solcor.

La plantilla (datos/plantillas/propuesta_arriendo.pptx) es la propuesta enviada de GD Pantanillo 1. No se redibuja
nada: se conserva la presentación tal cual —tipografía, colores, logotipo, iconos y gráfico— y solo se cambian los
textos con cifras, los valores del gráfico de ingreso anual y las dos imágenes del predio. Así el resultado se puede
seguir editando en PowerPoint como cualquier otra propuesta.

Cada forma se ubica por su número (id) dentro de la lámina; si se reemplaza la plantilla por otra versión hay que
revisar la tabla de formas de más abajo.
"""
import base64
import datetime as dt
import io
import math
import os
import re
import struct
import zipfile
import zlib
from xml.sax.saxutils import escape

import oferta

BASE = os.path.dirname(os.path.abspath(__file__))
PLANTILLA = os.path.join(BASE, "..", "datos", "plantillas", "propuesta_arriendo.pptx")
MESES = oferta.MESES


def _n(v, dec=0):
    return f"{v:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _gris():
    """PNG gris de 4×3 px para cuando la evaluación no trae imagen del predio."""
    fila = b"\x00" + bytes([226, 232, 229]) * 4

    def bloque(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return b"\x89PNG\r\n\x1a\n" + bloque(b"IHDR", struct.pack(">IIBBBBB", 4, 3, 8, 2, 0, 0, 0)) + bloque(b"IDAT", zlib.compress(fila * 3)) + bloque(b"IEND", b"")


def _imagen(b64):
    if not b64:
        return None
    try:
        return base64.b64decode(b64.split(",", 1)[1] if "," in b64[:80] else b64)
    except Exception:
        return None


class _Lamina:
    """Edición de los textos de una lámina por número de forma, sin tocar su formato."""

    def __init__(self, xml):
        self.x = xml
        self.faltan = []

    def _forma(self, fid):
        for m in re.finditer(r"<p:sp>.*?</p:sp>", self.x, re.S):
            if re.search(r'<p:cNvPr id="%d" ' % fid, m.group(0)):
                return m
        self.faltan.append(fid)
        return None

    def parrafos(self, fid, textos):
        """textos: uno por párrafo. str = texto del párrafo (toma el formato de su primer tramo);
        lista = texto de cada tramo, None deja el tramo como está; None = borra el párrafo; ... = no lo toca."""
        m = self._forma(fid)
        if not m:
            return
        forma = m.group(0)
        ps = list(re.finditer(r"<a:p>.*?</a:p>", forma, re.S))
        nueva, pos = [], 0
        for i, p in enumerate(ps):
            nueva.append(forma[pos:p.start()])
            pos = p.end()
            t = textos[i] if i < len(textos) else ...
            if t is ...:
                nueva.append(p.group(0))
            elif t is None:
                continue
            else:
                k = [0]

                def cambia(mt, t=t, k=k):
                    j = k[0]
                    k[0] += 1
                    if isinstance(t, str):
                        return f"<a:t>{escape(t) if j == 0 else ''}</a:t>"
                    nuevo = t[j] if j < len(t) else ""
                    return mt.group(0) if nuevo is None else f"<a:t>{escape(nuevo)}</a:t>"
                nueva.append(re.sub(r"<a:t>[^<]*</a:t>", cambia, p.group(0)))
        nueva.append(forma[pos:])
        self.x = self.x[:m.start()] + "".join(nueva) + self.x[m.end():]

    def texto(self, fid, t):
        self.parrafos(fid, [t])

    def _bloque(self, fid):
        m = re.search(r'<p:(sp|pic)>(?:(?!</p:\1>).)*?<p:cNvPr id="%d" .*?</p:\1>' % fid, self.x, re.S)
        if not m:
            self.faltan.append(fid)
        return m

    def quitar(self, fid):
        m = self._bloque(fid)
        if m:
            self.x = self.x[:m.start()] + self.x[m.end():]

    def mover(self, fid, dy_pt):
        m = self._bloque(fid)
        if m:
            b = re.sub(r'(<a:off x="\d+" y=")(\d+)(")', lambda o: f"{o.group(1)}{int(o.group(2)) + round(dy_pt * 12700)}{o.group(3)}", m.group(0), count=1)
            self.x = self.x[:m.start()] + b + self.x[m.end():]

    def copiar(self, fid, nuevo_id, texto, y_pt, alto_pt, tam=None, ancho_pt=None):
        """Agrega una copia de un cuadro de texto (mismo formato) con otro texto y otra posición vertical."""
        m = self._bloque(fid)
        if not m:
            return
        b = re.sub(r'<p:cNvPr id="\d+" name="[^"]*"', f'<p:cNvPr id="{nuevo_id}" name="Evaluador {nuevo_id}"', m.group(0), count=1)
        b = re.sub(r'(<a:off x="\d+" y=")\d+(")', rf"\g<1>{round(y_pt * 12700)}\2", b, count=1)
        b = re.sub(r'(<a:ext cx=")(\d+)(" cy=")\d+(")',
                   lambda o: f"{o.group(1)}{round(ancho_pt * 12700) if ancho_pt else o.group(2)}{o.group(3)}{round(alto_pt * 12700)}{o.group(4)}", b, count=1)
        if tam:
            b = re.sub(r'sz="\d+"', f'sz="{round(tam * 100)}"', b)
            b = re.sub(r'<a:lnSpc>.*?</a:lnSpc>', "", b)
        b = re.sub(r"<a:t>[^<]*</a:t>", f"<a:t>{escape(texto)}</a:t>", b, count=1)
        self.x = self.x.replace("</p:spTree>", b + "</p:spTree>", 1)

    def sin_recorte(self, fid):
        """Quita el recorte de la imagen original para que la nueva se vea completa."""
        m = re.search(r'<p:pic>(?:(?!</p:pic>).)*?<p:cNvPr id="%d" .*?</p:pic>' % fid, self.x, re.S)
        if m:
            self.x = self.x[:m.start()] + re.sub(r"<a:srcRect[^>]*/>", "", m.group(0)) + self.x[m.end():]


def _grafico(xml, anual):
    """Valores del gráfico de barras: un año por barra, con los años 1, 10, 20 y el último resaltados."""
    n = len(anual)
    cat = f'<c:ptCount val="{n}"/>' + "".join(f'<c:pt idx="{i}"><c:v>{i + 1}</c:v></c:pt>' for i in range(n))
    val = f'<c:ptCount val="{n}"/>' + "".join(f'<c:pt idx="{i}"><c:v>{v!r}</c:v></c:pt>' for i, v in enumerate(anual))
    partes = re.findall(r'<c:ptCount val="\d+"/>(?:<c:pt idx="\d+"><c:v>[^<]*</c:v></c:pt>)+', xml)
    if len(partes) < 3:
        raise ValueError("La plantilla de la propuesta no tiene el gráfico esperado.")
    xml = xml.replace(partes[1], cat, 1).replace(partes[2], val, 1)
    xml = re.sub(r"(\$A\$2:\$A\$)\d+", rf"\g<1>{n + 1}", xml)
    xml = re.sub(r"(\$C\$2:\$C\$)\d+", rf"\g<1>{n + 1}", xml)
    xml = xml.replace('<c:dPt><c:idx val="29"/>', f'<c:dPt><c:idx val="{n - 1}"/>')
    # el gráfico de la plantilla está enlazado a la calculadora en SharePoint: se deja con sus valores propios
    return re.sub(r"<c:externalData[^>]*>.*?</c:externalData>|<c:externalData[^>]*/>", "", xml, flags=re.S)


def generar(d):
    """Mismos datos que oferta.generar (PDF), más mapa_portada y mapa_planta (imágenes en base64)."""
    if not os.path.isfile(PLANTILLA):
        raise ValueError("Falta la plantilla de la propuesta en datos/plantillas/propuesta_arriendo.pptx.")
    c = oferta.calcular(d)
    hoy = d.get("fecha") if isinstance(d.get("fecha"), dt.date) else dt.date.today()
    proyecto = str(d.get("proyecto") or "GD").strip()
    kwp, kwac, mwh, n_gd = float(d.get("kwp") or 0), float(d.get("kwac") or 0), float(d.get("mwh") or 0), max(1, int(d.get("n_gd") or 1))
    area_m2, area_predio = float(d.get("area_m2") or 0), float(d.get("area_predio_m2") or 0)
    sigla = "".join(ch for ch in proyecto.upper().replace("GD", "") if ch.isalnum())[:4] or "GD"
    codigo = d.get("codigo") or f"PROP-GD-{sigla}-001"
    cop = lambda v: "$" + _n(round(v))  # noqa: E731
    mill = lambda v: f"${_n(v / 1e6)} millones"  # noqa: E731
    grande = kwp >= 2000
    z = zipfile.ZipFile(PLANTILLA)
    L = {n: _Lamina(z.read(f"ppt/slides/slide{n}.xml").decode("utf-8")) for n in (1, 2, 3, 4)}

    # ---- 1. portada
    s = L[1]
    s.texto(6, _n(kwp / 1000, 2) if grande else _n(kwp)); s.texto(7, "MWp" if grande else "kWp")
    s.texto(9, _n(kwac / 1000, 2) if grande else _n(kwac)); s.texto(10, "MWac" if grande else "kWac")
    s.texto(12, _n(mwh))
    s.texto(15, f"Departamento · {d.get('depto') or 'por confirmar'}")
    s.texto(16, f"Municipio · {d.get('municipio') or 'por confirmar'}")
    s.texto(5, f"Vereda · {d['vereda']}" if d.get("vereda") else "")
    s.texto(17, f"Propietario · {d['propietario']}" if d.get("propietario") else "")
    s.texto(19, f"Documento confidencial  ·  {codigo}  ·  {MESES[hoy.month - 1].capitalize()} de {hoy.year}  ·  Bogotá, Colombia")
    s.sin_recorte(22)

    # ---- 2. oferta económica
    s = L[2]
    s.texto(7, f"{cop(c['canon'])}  mensual" + ("**" if c.get("canon_ha") else ""))
    if c.get("canon_ha"):  # valor de referencia por hectárea (#50), en letra pequeña bajo la tarjeta
        s.copiar(8, 9001, f"** {cop(c['canon_ha'])} por hectárea al mes ({_n(c['ha'], 2)} ha)", 144, 12, tam=8, ancho_pt=240)
    s.parrafos(10, [[cop(c["anual"][0]) + " ", None, None, None]])
    s.texto(13, cop(c["total"]))
    s.parrafos(14, [[None, None, None, None, f" {c['anios']} años de operación"]])
    s.parrafos(48, [["Plazo (", f"{c['m_des']}) meses"]])
    s.parrafos(52, [["Prima de exclusividad ", "·", cop(c["prima"]) + " "] if c["prima"] else ["Exclusividad sobre el área", "", ""]])
    s.texto(62, f"Plazo ({c['m_con']}) meses")
    s.parrafos(66, [[None, None, None, cop(c["pago_construccion"]), ""]])
    s.texto(76, f"Plazo ({c['anios']}) años")
    s.parrafos(80, [[None, None, None, _n(round(c["anual"][0])), None]])
    s.texto(90, f"Plazo ({c['m_desm']}) meses")
    s.texto(94, f"Canon vigente año {c['anios']}")
    ult = c["anios"] - 1
    s.texto(99, mill(c["anual"][ult]))
    s.texto(101, mill(c["anual"][min(19, ult)]))
    s.parrafos(103, [["$", f"{_n(c['anual'][min(9, ult)] / 1e6)} millones"]])
    s.texto(105, mill(c["anual"][0]))
    s.texto(119, f"Año {c['anios']}")
    s.texto(125, f"*Proyección con IPC constante de {_n(c['ipc'] * 100, 1)}% anual, únicamente como estimación. "
                 "El ajuste real se hará con el IPC certificado cada año por el DANE.   ")

    # ---- 3. características del proyecto
    s = L[3]
    s.texto(10, f"Área Requerida {_n(area_m2)} m2")
    s.texto(49, f"{_n(d.get('modulos') or 0)} unidades")
    s.parrafos(55, [[None, None, None, f" {_n(float(d.get('inclinacion') or 10))}°"]])
    s.texto(61, f"{n_gd} x 1 MW · 13,2 kV" if n_gd > 1 else "1 MW · 13,2 kV")
    s.texto(67, f"{25 * n_gd} m2")
    s.parrafos(73, [[f"{_n(d['long_mt_m'])} metros " if d.get("long_mt_m") else "Por definir ", None, None]])
    s.texto(79, f"{_n(d['perimetro_m'])} metros" if d.get("perimetro_m") else "Perimetral")
    s.texto(87, f"{c['m_des']} meses"); s.texto(93, f"{c['m_con']} meses"); s.texto(99, f"{c['anios']} años")
    s.parrafos(163, [[None, None, None, None, None, None, f"{_n(c['co2'])} Toneladas"]])
    # beneficio ambiental: sin el bloque de hogares (#50); quedan árboles, vehículos y el recuadro de energía limpia
    for fid in (173, 175, 177, 207):
        s.quitar(fid)
    for fid in (221, 223, 225, 209):
        s.mover(fid, -46)  # vehículos a mitad de camino entre árboles y el recuadro
    s.texto(215, _n(c["arboles"]))
    s.texto(223, f"+{_n(math.floor(c['vehiculos']))}")
    s.parrafos(231, [[f"Equivalencias calculadas sobre los {_n(mwh)} ", None, None, None, None, None,
                      f". Captura por árbol {oferta.KG_ARBOL} kg CO₂/año y emisión de un vehículo particular {_n(oferta.T_VEHICULO, 1)} ",
                      None, None]])
    s.sin_recorte(8)

    # ---- 4. pasos a seguir
    s = L[4]
    ocupa = f"Se cerca y se ocupa {_n(area_m2 / 10000, 2)} ha"
    if area_predio > area_m2 * 1.02:
        ocupa += f", el {_n(100 * area_m2 / area_predio)} % del predio,"
    ocupa += f" durante los {c['anios']} años del contrato."
    s.parrafos(44, [ocupa, f"Habrá tránsito de maquinaria y personal durante los {c['m_con']} meses de obra.", ..., ...,
                    "Si el proyecto no resulta viable en desarrollo, el contrato termina"
                    + (", usted conserva la prima" if c["prima"] else "") + " y el predio queda libre."])
    if not c["prima"]:
        s.parrafos(95, [None, ...])
    faltan = [f"lámina {n}: formas {l.faltan}" for n, l in L.items() if l.faltan]
    if faltan:
        raise ValueError("La plantilla de la propuesta cambió y no coincide con el evaluador (" + "; ".join(faltan) + ").")

    # ---- imágenes del predio: se agregan como archivos nuevos y las láminas pasan a apuntar a ellos
    portada, planta = _imagen(d.get("mapa_portada")) or _imagen(d.get("mapa")), _imagen(d.get("mapa_planta")) or _imagen(d.get("mapa"))
    medios = {}
    for clave, datos in (("portada", portada), ("planta", planta)):
        datos = datos or _gris()
        medios[clave] = (f"evaluador_{clave}." + ("jpeg" if datos[:2] == b"\xff\xd8" else "png"), datos)
    rels = {1: z.read("ppt/slides/_rels/slide1.xml.rels").decode("utf-8"), 3: z.read("ppt/slides/_rels/slide3.xml.rels").decode("utf-8")}
    viejas = set()
    for n, rid, clave in ((1, "rId4", "portada"), (3, "rId3", "planta")):
        m = re.search(r'<Relationship [^>]*Id="%s"[^>]*/>' % rid, rels[n])
        viejas.add(re.search(r'Target="\.\./media/([^"]+)"', m.group(0)).group(1))
        rels[n] = rels[n].replace(m.group(0), re.sub(r'Target="[^"]+"', f'Target="../media/{medios[clave][0]}"', m.group(0)))

    salida = io.BytesIO()
    with zipfile.ZipFile(salida, "w", zipfile.ZIP_DEFLATED) as out:
        for item in z.infolist():
            nombre, datos = item.filename, z.read(item.filename)
            m = re.match(r"ppt/slides/slide(\d)\.xml$", nombre)
            if m:
                datos = L[int(m.group(1))].x.encode("utf-8")
            elif nombre == "ppt/slides/_rels/slide1.xml.rels":
                datos = rels[1].encode("utf-8")
            elif nombre == "ppt/slides/_rels/slide3.xml.rels":
                datos = rels[3].encode("utf-8")
            elif nombre == "ppt/charts/chart1.xml":
                datos = _grafico(datos.decode("utf-8"), c["anual"]).encode("utf-8")
            elif nombre == "ppt/charts/_rels/chart1.xml.rels":
                datos = re.sub(rb"<Relationship [^>]*oleObject[^>]*/>", b"", datos)
            elif nombre.startswith("ppt/media/") and nombre.split("/")[-1] in viejas:
                continue  # fotos del predio de la plantilla: no viajan en la propuesta nueva
            elif nombre.startswith("ppt/changesInfos/") or nombre == "ppt/revisionInfo.xml":
                continue  # historial de cambios de la plantilla
            elif nombre == "[Content_Types].xml":
                datos = re.sub(rb'<Override PartName="/ppt/(changesInfos/[^"]+|revisionInfo\.xml)"[^>]*/>', b"", datos)
            elif nombre == "ppt/_rels/presentation.xml.rels":
                datos = re.sub(rb'<Relationship [^>]*(changesInfos|revisionInfo)[^>]*/>', b"", datos)
            elif nombre == "docProps/core.xml":
                datos = re.sub(rb"<dc:title>[^<]*</dc:title>", f"<dc:title>Propuesta de arriendo · {escape(proyecto)}</dc:title>".encode("utf-8"), datos)
            out.writestr(item, datos)
        for archivo, datos in medios.values():
            out.writestr("ppt/media/" + archivo, datos)
    return salida.getvalue()
