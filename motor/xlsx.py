"""
Excel (.xlsx) con la librería estándar: crear libros sencillos y editar celdas de una plantilla.

crear(hojas)            libro nuevo de valores (sin fórmulas), con encabezados y formatos numéricos básicos.
editar(plantilla, ...)  cambia valores de celdas existentes sin reescribir el libro: se conservan fórmulas,
                        formatos, validaciones y comentarios. Excel recalcula al abrir (fullCalcOnLoad).
"""
import io
import re
import zipfile
from xml.sax.saxutils import escape, unescape

# estilos de crear(): índice de formato de celda
NORMAL, NEGRITA, MILES, PCT, TITULO, DEC2, FECHA_TXT = 0, 1, 2, 3, 4, 5, 0
_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<numFmts count="1"><numFmt numFmtId="164" formatCode="0.0%"/></numFmts>'
    '<fonts count="3"><font><sz val="10"/><name val="Calibri"/></font><font><b/><sz val="10"/><name val="Calibri"/></font>'
    '<font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts>'
    '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FF1B2A4A"/><bgColor indexed="64"/></patternFill></fill></fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="6">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    '<xf numFmtId="3" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="0" fontId="2" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
    '<xf numFmtId="4" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '</cellXfs></styleSheet>')


def _col(n):
    s = ""
    while n >= 0:
        s, n = chr(65 + n % 26) + s, n // 26 - 1
    return s


def _celda(ref, v):
    estilo = NORMAL
    if isinstance(v, tuple):
        v, estilo = v
    if v is None or v == "":
        return f'<c r="{ref}" s="{estilo}"/>' if estilo else ""
    if isinstance(v, bool):
        v = "Sí" if v else "No"
    if isinstance(v, (int, float)):
        if v != v or v in (float("inf"), float("-inf")):
            return ""
        return f'<c r="{ref}" s="{estilo}"><v>{v!r}</v></c>'
    return f'<c r="{ref}" s="{estilo}" t="inlineStr"><is><t xml:space="preserve">{escape(str(v))}</t></is></c>'


def crear(hojas, logo=None):
    """hojas: [(nombre, filas, anchos)] · filas: listas de celdas · celda: valor o (valor, ESTILO).
    logo: PNG que se pone arriba a la izquierda de cada hoja (se dejan 3 filas libres para él)."""
    if logo:
        hojas = [(h[0], [[]] * 3 + list(h[1])) + tuple(h[2:]) for h in hojas]
    z_io = io.BytesIO()
    with zipfile.ZipFile(z_io, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                             for i in range(1, len(hojas) + 1)) + "</Types>")
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   + "".join(f'<sheet name="{escape(re.sub(r"[\[\]:*?/\\]", " ", n)[:31])}" sheetId="{i}" r:id="rId{i}"/>' for i, (n, *_) in enumerate(hojas, 1))
                   + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                             for i in range(1, len(hojas) + 1))
                   + f'<Relationship Id="rId{len(hojas) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr("xl/styles.xml", _STYLES)
        for i, hoja in enumerate(hojas, 1):
            filas, anchos = hoja[1], (hoja[2] if len(hoja) > 2 else None)
            cols = "<cols>" + "".join(f'<col min="{k}" max="{k}" width="{w}" customWidth="1"/>' for k, w in enumerate(anchos, 1)) + "</cols>" if anchos else ""
            cuerpo = "".join(f'<row r="{r}">' + "".join(_celda(f"{_col(c)}{r}", v) for c, v in enumerate(fila)) + "</row>"
                             for r, fila in enumerate(filas, 1))
            z.writestr(f"xl/worksheets/sheet{i}.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                       f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">{cols}<sheetData>{cuerpo}</sheetData></worksheet>')
    if logo:
        return con_logo(z_io.getvalue(), logo, {f"xl/worksheets/sheet{i}.xml": (0, 0, 1.3) for i in range(1, len(hojas) + 1)})
    return z_io.getvalue()


EMU_CM = 360000


def _png_tam(png):
    import struct
    return struct.unpack(">II", png[16:24])


def con_logo(libro, png, ubicaciones):
    """Agrega una imagen (el logo) flotando sobre las hojas indicadas, sin tocar celdas ni fórmulas.
    ubicaciones: {ruta de la hoja (xl/worksheets/sheetN.xml): (columna, fila, alto_cm)} con columna y fila desde 0."""
    entrada = zipfile.ZipFile(io.BytesIO(libro))
    nombres = entrada.namelist()
    w, h = _png_tam(png)
    n_dib = 1 + max([int(m.group(1)) for m in (re.match(r"xl/drawings/drawing(\d+)\.xml$", x) for x in nombres) if m] or [0])
    media = "xl/media/logo_solcor.png"
    nuevos, hojas_dib = {}, {}
    for hoja, (col, fila, alto_cm) in ubicaciones.items():
        if hoja not in nombres:
            continue
        cy = int(alto_cm * EMU_CM)
        cx = int(cy * w / h)
        ruta = f"xl/drawings/drawing{n_dib}.xml"
        nuevos[ruta] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<xdr:oneCellAnchor><xdr:from><xdr:col>{col}</xdr:col><xdr:colOff>{int(0.05 * EMU_CM)}</xdr:colOff>'
            f'<xdr:row>{fila}</xdr:row><xdr:rowOff>{int(0.05 * EMU_CM)}</xdr:rowOff></xdr:from><xdr:ext cx="{cx}" cy="{cy}"/>'
            '<xdr:pic><xdr:nvPicPr><xdr:cNvPr id="2" name="Logo Solcor" descr="Solcor Colombia"/>'
            '<xdr:cNvPicPr><a:picLocks noChangeAspect="1"/></xdr:cNvPicPr></xdr:nvPicPr>'
            '<xdr:blipFill><a:blip r:embed="rIdLogo"/><a:stretch><a:fillRect/></a:stretch></xdr:blipFill>'
            f'<xdr:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></xdr:spPr>'
            '</xdr:pic><xdr:clientData/></xdr:oneCellAnchor></xdr:wsDr>').encode("utf-8")
        nuevos[f"xl/drawings/_rels/drawing{n_dib}.xml.rels"] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rIdLogo" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            'Target="../media/logo_solcor.png"/></Relationships>').encode("utf-8")
        hojas_dib[hoja] = n_dib
        n_dib += 1
    if not hojas_dib:
        return libro
    rels_de = {h.replace("worksheets/", "worksheets/_rels/") + ".rels": n for h, n in hojas_dib.items()}

    def rel_dib(n):
        return (f'<Relationship Id="rIdLogoDib" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" '
                f'Target="../drawings/drawing{n}.xml"/>')
    salida_io = io.BytesIO()
    with zipfile.ZipFile(salida_io, "w", zipfile.ZIP_DEFLATED) as salida:
        vistos = set()
        for item in entrada.infolist():
            datos = entrada.read(item.filename)
            nombre = item.filename
            if nombre == "[Content_Types].xml":
                x = datos.decode("utf-8")
                if 'Extension="png"' not in x:
                    x = x.replace("<Default ", '<Default Extension="png" ContentType="image/png"/><Default ', 1)
                x = x.replace("</Types>", "".join(
                    f'<Override PartName="/xl/drawings/drawing{n}.xml" ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/>'
                    for n in hojas_dib.values()) + "</Types>")
                datos = x.encode("utf-8")
            elif nombre in hojas_dib:
                x = datos.decode("utf-8")
                if "xmlns:r=" not in x[:600]:
                    x = re.sub(r"<worksheet\b", '<worksheet xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"', x, 1)
                etiqueta = '<drawing r:id="rIdLogoDib"/>'
                # orden del esquema: <drawing> va antes de legacyDrawing, legacyDrawingHF, picture, oleObjects, controls, tableParts, extLst
                m = re.search(r"<(legacyDrawing|legacyDrawingHF|picture|oleObjects|controls|webPublishItems|tableParts|extLst)\b", x)
                x = x[:m.start()] + etiqueta + x[m.start():] if m else x.replace("</worksheet>", etiqueta + "</worksheet>")
                datos = x.encode("utf-8")
            elif nombre in rels_de:  # la hoja ya tenía relaciones (comentarios, impresora…): se agrega la del dibujo
                datos = datos.replace(b"</Relationships>", rel_dib(rels_de[nombre]).encode("utf-8") + b"</Relationships>")
            salida.writestr(item, datos)
            vistos.add(nombre)
        for rels, n in rels_de.items():
            if rels not in vistos:
                salida.writestr(rels, '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                                f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rel_dib(n)}</Relationships>')
        for ruta, datos in nuevos.items():
            salida.writestr(ruta, datos)
        salida.writestr(media, png)
    return salida_io.getvalue()


def _rutas_hojas(entrada):
    """{nombre de hoja: ruta del XML dentro del libro}."""
    wb = entrada.read("xl/workbook.xml").decode("utf-8")
    rels = entrada.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    destino = {}
    for r in re.findall(r"<Relationship\b[^>]*/>", rels):
        i, t = re.search(r'Id="([^"]+)"', r), re.search(r'Target="([^"]+)"', r)
        if i and t:
            destino[i.group(1)] = t.group(1).lstrip("/") if t.group(1).startswith("/xl/") else "xl/" + t.group(1)
    out = {}
    for s in re.findall(r"<sheet\b[^>]*/>", wb):
        n, i = re.search(r'name="([^"]+)"', s), re.search(r'r:id="([^"]+)"', s)
        if n and i and i.group(1) in destino:
            out[unescape(n.group(1))] = destino[i.group(1)]
    return out


def editar(plantilla, cambios, textos=None, hoja="xl/worksheets/sheet1.xml"):
    """plantilla: bytes del .xlsx · cambios: {"G18": 1996, ...} en celdas que ya existen; la clave puede llevar la hoja
    («Supuestos!C11») y el valor puede ser texto (queda como texto en la celda) · textos: {texto exacto de una celda:
    texto nuevo} (se cambia en la tabla de textos compartidos)."""
    entrada = zipfile.ZipFile(io.BytesIO(plantilla))
    rutas = _rutas_hojas(entrada) if any("!" in k for k in cambios) else {}
    por_hoja = {}
    for k, v in cambios.items():
        nombre, _, ref = k.rpartition("!")
        if nombre and nombre not in rutas:
            raise ValueError(f"La plantilla no tiene la hoja «{nombre}».")
        por_hoja.setdefault(rutas[nombre] if nombre else hoja, {})[ref] = v
    salida_io = io.BytesIO()
    faltan = []
    with zipfile.ZipFile(salida_io, "w", zipfile.ZIP_DEFLATED) as salida:
        for item in entrada.infolist():
            datos = entrada.read(item.filename)
            if item.filename == "xl/calcChain.xml":
                continue  # Excel lo reconstruye; así no queda apuntando a celdas que dejaron de ser fórmula
            if item.filename.startswith("xl/worksheets/") and item.filename.endswith(".xml"):
                x = datos.decode("utf-8")
                for ref, valor in por_hoja.get(item.filename, {}).items():
                    patron = re.compile(r'<c r="%s"((?: [a-zA-Z:]+="[^"]*")*?)(?:/>|>(.*?)</c>)' % re.escape(ref), re.S)
                    m = patron.search(x)
                    if not m:
                        faltan.append(ref)
                        continue
                    if re.search(r'<f\b[^>]*t="shared"[^>]*ref=', m.group(2) or ""):
                        # es la celda madre de una fórmula compartida: borrarla dañaría las celdas que la copian
                        raise ValueError(f"La celda {ref} de la plantilla define una fórmula compartida; no se puede reemplazar.")
                    attrs = re.sub(r' t="[^"]*"', "", m.group(1))  # deja de ser texto compartido si lo era
                    celda = f'<c r="{ref}"{attrs} t="inlineStr"><is><t xml:space="preserve">{escape(valor)}</t></is></c>' \
                        if isinstance(valor, str) else f'<c r="{ref}"{attrs}><v>{float(valor)!r}</v></c>'
                    x = x[:m.start()] + celda + x[m.end():]
                # valores en caché de las fórmulas: se quitan para que nadie vea cifras de la plantilla sin recalcular
                x = re.sub(r"(<f\b[^>]*/>|<f\b[^>]*>.*?</f>)<v>[^<]*</v>", r"\1", x, flags=re.S)
                datos = x.encode("utf-8")
            elif item.filename == "xl/sharedStrings.xml" and textos:
                x = datos.decode("utf-8")
                for viejo, nuevo in textos.items():
                    x = x.replace(f">{escape(viejo)}</t>", f">{escape(nuevo)}</t>")
                datos = x.encode("utf-8")
            elif item.filename == "xl/workbook.xml":
                x = datos.decode("utf-8")
                x = re.sub(r"<calcPr\b[^>]*/>", '<calcPr calcId="191029" fullCalcOnLoad="1"/>', x) if "<calcPr" in x \
                    else x.replace("</workbook>", '<calcPr fullCalcOnLoad="1"/></workbook>')
                datos = x.encode("utf-8")
            elif item.filename == "[Content_Types].xml":
                datos = re.sub(rb'<Override PartName="/xl/calcChain.xml"[^>]*/>', b"", datos)
            elif item.filename == "xl/_rels/workbook.xml.rels":
                datos = re.sub(rb'<Relationship [^>]*calcChain[^>]*/>', b"", datos)
            salida.writestr(item, datos)
    if faltan:
        raise ValueError("La plantilla no tiene las celdas " + ", ".join(faltan))
    return salida_io.getvalue()
