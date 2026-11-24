"""
Generador minimo de documentos Word (.docx) con la libreria estandar.

Estilo de los entregables Solcor (Reporte de Caracterizacion Predial / Estudio de Titulos):
carta, encabezado con logo, titulos azul 1B2A4A, tablas con encabezado azul y etiquetas E7EEF5.
"""
import io
import struct
import zipfile
from xml.sax.saxutils import escape

from ortografia import tildar

AZUL = "1B2A4A"
GRIS_ETQ = "E7EEF5"
GRIS_FILA = "F4F6F8"
VERDE = "2E7D32"
NIVELES = {"Alto": "C0392B", "Medio": "B7791F", "Bajo": "2E7D32", "Informativo": "5A6169",
           "rojo": "C0392B", "ambar": "B7791F", "verde": "2E7D32", "info": "5A6169"}
EMU_CM = 360000
TW_CM = 567  # twips por cm


def _img_tam(datos):
    if datos[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", datos[16:24])
        return w, h, "png"
    if datos[:2] == b"\xff\xd8":
        i = 2
        while i < len(datos):
            if datos[i] != 0xFF:
                i += 1
                continue
            m = datos[i + 1]
            if m in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack(">HH", datos[i + 5:i + 9])
                return w, h, "jpeg"
            i += 2 + struct.unpack(">H", datos[i + 2:i + 4])[0]
    raise ValueError("imagen no soportada (PNG o JPEG)")


def _run(texto, b=False, i=False, color=None, tam=None, fuente=None):
    rpr = ""
    if fuente:
        rpr += f'<w:rFonts w:ascii="{fuente}" w:hAnsi="{fuente}" w:cs="{fuente}"/>'
    if b:
        rpr += "<w:b/><w:bCs/>"
    if i:
        rpr += "<w:i/><w:iCs/>"
    if color:
        rpr += f'<w:color w:val="{color}"/>'
    if tam:
        rpr += f'<w:sz w:val="{int(tam * 2)}"/><w:szCs w:val="{int(tam * 2)}"/>'
    partes = tildar(str(texto)).split("\n")  # los folios y el catastro llegan sin tildes
    out = []
    for k, t in enumerate(partes):
        if k:
            out.append("<w:br/>")
        out.append(f'<w:t xml:space="preserve">{escape(t)}</w:t>')
    return f"<w:r><w:rPr>{rpr}</w:rPr>{''.join(out)}</w:r>"


def _runs(contenido, **fmt):
    """contenido: str | [(texto, {fmt}) | str, ...]"""
    if isinstance(contenido, (list, tuple)):
        out = []
        for c in contenido:
            if isinstance(c, (list, tuple)):
                f = dict(fmt)
                f.update(c[1] if len(c) > 1 else {})
                out.append(_run(c[0], **f))
            else:
                out.append(_run(c, **fmt))
        return "".join(out)
    return _run(contenido, **fmt)


def _ppr(alin=None, antes=0, despues=80, sangria=None, sombra=None, borde_inf=None, mantener=False, estilo=None, numid=None):
    p = ""
    if estilo:
        p += f'<w:pStyle w:val="{estilo}"/>'
    if mantener:
        p += "<w:keepNext/>"
    if numid:
        p += f'<w:numPr><w:ilvl w:val="0"/><w:numId w:val="{numid}"/></w:numPr>'
    if borde_inf:
        p += f'<w:pBdr><w:bottom w:val="single" w:sz="8" w:space="1" w:color="{borde_inf}"/></w:pBdr>'
    if sombra:
        p += f'<w:shd w:val="clear" w:color="auto" w:fill="{sombra}"/>'
    p += f'<w:spacing w:before="{antes}" w:after="{despues}"/>'
    if sangria:
        p += f'<w:ind w:left="{sangria}"/>'
    if alin:
        p += f'<w:jc w:val="{alin}"/>'
    return f"<w:pPr>{p}</w:pPr>"


class Docx:
    def __init__(self, titulo_encabezado="", subtitulo_encabezado="", logo=None, pie="Solcor Colombia S.A.S. – Desarrollo GD",
                 margen_cm=(2.4, 1.3, 1.9, 1.3), fuente="Calibri", tam=9.5, autor="Evaluador GD"):
        self.cuerpo = []
        self.medios = []  # (nombre, bytes)
        self.rels = []  # (rid, tipo, destino)
        self.titulo_enc, self.subtitulo_enc, self.logo, self.pie = titulo_encabezado, subtitulo_encabezado, logo, pie
        self.margen = margen_cm
        self.fuente, self.tam, self.autor = fuente, tam, autor
        self.ancho_util_cm = 21.59 - margen_cm[1] - margen_cm[3]
        self._n_img = 0

    # ---------------------------------------------------------------- bloques
    def p(self, contenido, alin=None, antes=0, despues=80, b=False, i=False, color=None, tam=None, sangria=None, sombra=None):
        self.cuerpo.append(f"<w:p>{_ppr(alin, antes, despues, sangria, sombra)}{_runs(contenido, b=b, i=i, color=color, tam=tam)}</w:p>")

    def titulo(self, texto, sub=None):
        self.cuerpo.append(f"<w:p>{_ppr('center', 120, 40)}{_run(texto, b=True, color=AZUL, tam=17)}</w:p>")
        if sub:
            for s in ([sub] if isinstance(sub, str) else sub):
                self.cuerpo.append(f"<w:p>{_ppr('center', 0, 20)}{_run(s, b=True, color='5A6169', tam=11)}</w:p>")
        self.cuerpo.append(f"<w:p>{_ppr(None, 0, 120, borde_inf=AZUL)}</w:p>")

    def h1(self, texto):
        self.cuerpo.append(f"<w:p>{_ppr(None, 200, 80, sombra=AZUL, mantener=True)}{_run(' ' + texto.upper(), b=True, color='FFFFFF', tam=10.5)}</w:p>")

    def h2(self, texto):
        self.cuerpo.append(f"<w:p>{_ppr(None, 160, 60, borde_inf='B8C4D6', mantener=True)}{_run(texto, b=True, color=AZUL, tam=10.5)}</w:p>")

    def vinetas(self, items, numerada=False):
        for it in items:
            self.cuerpo.append(f"<w:p>{_ppr('both', 0, 60, numid=2 if numerada else 1)}{_runs(it)}</w:p>")

    def nota(self, texto):
        self.cuerpo.append(f"<w:p>{_ppr('both', 60, 60)}{_run(texto, i=True, color='5A6169', tam=8)}</w:p>")

    def salto(self):
        self.cuerpo.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')

    def espacio(self, pts=4):
        self.cuerpo.append(f'<w:p>{_ppr(None, 0, int(pts * 20))}</w:p>')

    def _celda(self, contenido, ancho_tw, fill=None, b=False, color=None, alin=None, tam=None, span=1, valign="center"):
        tcpr = f'<w:tcW w:w="{ancho_tw}" w:type="dxa"/>'
        if span > 1:
            tcpr += f'<w:gridSpan w:val="{span}"/>'
        if fill:
            tcpr += f'<w:shd w:val="clear" w:color="auto" w:fill="{fill}"/>'
        tcpr += f'<w:vAlign w:val="{valign}"/>'
        parrafos = contenido if isinstance(contenido, list) and contenido and isinstance(contenido[0], list) else [contenido]
        ps = "".join(f"<w:p>{_ppr(alin, 20, 20)}{_runs(c if c is not None else '', b=b, color=color, tam=tam)}</w:p>" for c in parrafos)
        return f"<w:tc><w:tcPr>{tcpr}</w:tcPr>{ps}</w:tc>"

    def tabla(self, filas, anchos_cm=None, encabezado=True, etiquetas=False, cebra=False, tam=None, alin=None,
              colores_fila=None, fill_celdas=None, bordes="B8C4D6"):
        """filas: listas de celdas (str o runs). etiquetas=True: columnas pares son etiquetas (ficha).
        fill_celdas: dict {(fila, col): 'RRGGBB'} ; colores_fila: dict {fila: {'fill':..,'color':..,'b':..}}"""
        ncol = max(len(f) for f in filas)
        anchos_cm = anchos_cm or [self.ancho_util_cm / ncol] * ncol
        tw = [int(a * TW_CM) for a in anchos_cm]
        grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in tw)
        b = f'w:val="single" w:sz="4" w:space="0" w:color="{bordes}"'
        tblpr = (f'<w:tblPr><w:tblW w:w="{sum(tw)}" w:type="dxa"/><w:tblLayout w:type="fixed"/>'
                 f'<w:tblBorders><w:top {b}/><w:left {b}/><w:bottom {b}/><w:right {b}/><w:insideH {b}/><w:insideV {b}/></w:tblBorders>'
                 f'<w:tblCellMar><w:left w:w="80" w:type="dxa"/><w:right w:w="80" w:type="dxa"/></w:tblCellMar></w:tblPr>')
        filas_xml = []
        for r, fila in enumerate(filas):
            celdas = []
            col = 0
            for c, cont in enumerate(fila):
                span = 1
                if c == len(fila) - 1 and len(fila) < ncol:
                    span = ncol - len(fila) + 1
                ancho = sum(tw[col:col + span])
                fill, color, bold = None, None, False
                if encabezado and r == 0:
                    fill, color, bold = AZUL, "FFFFFF", True
                elif etiquetas and col % 2 == 0:
                    fill, bold, color = GRIS_ETQ, True, AZUL
                elif cebra and r % 2 == 0:
                    fill = GRIS_FILA
                if colores_fila and r in colores_fila:
                    cf = colores_fila[r]
                    fill, color, bold = cf.get("fill", fill), cf.get("color", color), cf.get("b", bold)
                if fill_celdas and (r, c) in fill_celdas:
                    v = fill_celdas[(r, c)]
                    if isinstance(v, dict):
                        fill, color, bold = v.get("fill", fill), v.get("color", color), v.get("b", bold)
                    else:
                        fill = v
                a = alin[c] if isinstance(alin, (list, tuple)) else alin
                celdas.append(self._celda(cont, ancho, fill, bold, color, a, tam or (self.tam - 0.5), span))
                col += span
            cab = '<w:trPr><w:tblHeader/><w:cantSplit/></w:trPr>' if encabezado and r == 0 else '<w:trPr><w:cantSplit/></w:trPr>'
            filas_xml.append(f"<w:tr>{cab}{''.join(celdas)}</w:tr>")
        self.cuerpo.append(f"<w:tbl>{tblpr}<w:tblGrid>{grid}</w:tblGrid>{''.join(filas_xml)}</w:tbl>")
        self.espacio(3)

    def _dibujo(self, datos, ancho_cm, alto_cm=None):
        w, h, ext = _img_tam(datos)
        self._n_img += 1
        nombre = f"image{self._n_img}.{ext}"
        rid = f"rIdImg{self._n_img}"
        self.medios.append((nombre, datos))
        self.rels.append((rid, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image", f"media/{nombre}"))
        cx = int(ancho_cm * EMU_CM)
        cy = int((alto_cm * EMU_CM) if alto_cm else cx * h / w)
        n = self._n_img
        return (f'<w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{cx}" cy="{cy}"/>'
                f'<wp:docPr id="{n}" name="Imagen {n}"/><wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>'
                f'<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic>'
                f'<pic:nvPicPr><pic:cNvPr id="{n}" name="{nombre}"/><pic:cNvPicPr/></pic:nvPicPr>'
                f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
                f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
                f'</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing>'), rid

    def imagen(self, datos, ancho_cm=None, pie=None):
        ancho_cm = min(ancho_cm or self.ancho_util_cm, self.ancho_util_cm)
        dib, _ = self._dibujo(datos, ancho_cm)
        self.cuerpo.append(f"<w:p>{_ppr('center', 80, 40, mantener=bool(pie))}<w:r>{dib}</w:r></w:p>")
        if pie:
            self.cuerpo.append(f"<w:p>{_ppr('center', 0, 120)}{_run(pie, i=True, color='5A6169', tam=8)}</w:p>")

    # ---------------------------------------------------------------- empaquetado
    def _encabezado(self):
        logo_xml = ""
        rels = ""
        if self.logo:
            w, h, ext = _img_tam(self.logo)
            cx = int(4.2 * EMU_CM)
            cy = int(cx * h / w)
            logo_xml = (f'<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{cx}" cy="{cy}"/>'
                        f'<wp:docPr id="900" name="Logo"/><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
                        f'<pic:pic><pic:nvPicPr><pic:cNvPr id="900" name="logo.{ext}"/><pic:cNvPicPr/></pic:nvPicPr>'
                        f'<pic:blipFill><a:blip r:embed="rIdLogo"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
                        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
                        f'</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>')
            rels = f'<Relationship Id="rIdLogo" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/logo.{ext}"/>'
        ancho = int(self.ancho_util_cm * TW_CM)
        c1, c2 = int(ancho * 0.3), ancho - int(ancho * 0.3)
        sin = '<w:tblBorders><w:top w:val="nil"/><w:left w:val="nil"/><w:bottom w:val="single" w:sz="8" w:color="1B2A4A"/><w:right w:val="nil"/><w:insideH w:val="nil"/><w:insideV w:val="nil"/></w:tblBorders>'
        texto = (f'<w:p>{_ppr("right", 0, 0)}{_run(self.titulo_enc, b=True, color=AZUL, tam=10)}</w:p>'
                 f'<w:p>{_ppr("right", 0, 40)}{_run(self.subtitulo_enc, color="5A6169", tam=7.5)}</w:p>')
        xml = (f'<w:hdr {_NS}><w:tbl><w:tblPr><w:tblW w:w="{ancho}" w:type="dxa"/><w:tblLayout w:type="fixed"/>{sin}</w:tblPr>'
               f'<w:tblGrid><w:gridCol w:w="{c1}"/><w:gridCol w:w="{c2}"/></w:tblGrid><w:tr>'
               f'<w:tc><w:tcPr><w:tcW w:w="{c1}" w:type="dxa"/><w:vAlign w:val="center"/></w:tcPr><w:p>{_ppr(None, 0, 40)}{logo_xml}</w:p></w:tc>'
               f'<w:tc><w:tcPr><w:tcW w:w="{c2}" w:type="dxa"/><w:vAlign w:val="center"/></w:tcPr>{texto}</w:tc>'
               f'</w:tr></w:tbl><w:p>{_ppr(None, 0, 0)}</w:p></w:hdr>')
        return xml, rels

    def _pie(self):
        fld = lambda instr: (f'<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> {instr} </w:instrText></w:r>'
                             f'<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>')
        return (f'<w:ftr {_NS}><w:p><w:pPr><w:pBdr><w:top w:val="single" w:sz="4" w:space="4" w:color="B8C4D6"/></w:pBdr>'
                f'<w:tabs><w:tab w:val="right" w:pos="{int(self.ancho_util_cm * TW_CM)}"/></w:tabs><w:spacing w:before="0" w:after="0"/></w:pPr>'
                f'{_run(self.pie, color="5A6169", tam=7.5)}<w:r><w:rPr><w:sz w:val="15"/></w:rPr><w:tab/></w:r>'
                f'{_run("Página ", color="5A6169", tam=7.5)}{fld("PAGE")}{_run(" de ", color="5A6169", tam=7.5)}{fld("NUMPAGES")}</w:p></w:ftr>')

    def guardar(self):
        t, d, b, iz = [int(x * TW_CM) for x in self.margen]
        sect = (f'<w:sectPr><w:headerReference w:type="default" r:id="rIdHdr"/><w:footerReference w:type="default" r:id="rIdFtr"/>'
                f'<w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="{t}" w:right="{d}" w:bottom="{b}" w:left="{iz}" w:header="500" w:footer="500" w:gutter="0"/></w:sectPr>')
        doc = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {_NS}><w:body>{"".join(self.cuerpo)}{sect}</w:body></w:document>'
        hdr, hdr_rels = self._encabezado()
        rels = "".join(f'<Relationship Id="{i}" Type="{t_}" Target="{d_}"/>' for i, t_, d_ in self.rels)
        rels += ('<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                 '<Relationship Id="rIdNum" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
                 '<Relationship Id="rIdSet" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>'
                 '<Relationship Id="rIdHdr" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>'
                 '<Relationship Id="rIdFtr" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>')
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml",
                       '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                       '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                       '<Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/>'
                       '<Default Extension="jpeg" ContentType="image/jpeg"/>'
                       '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                       '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
                       '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
                       '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
                       '<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>'
                       '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>'
                       '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
                       '</Types>')
            z.writestr("_rels/.rels",
                       '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                       '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                       '</Relationships>')
            z.writestr("docProps/core.xml",
                       '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                       'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>' + escape(self.titulo_enc) + '</dc:title><dc:creator>' + escape(self.autor) +
                       '</dc:creator></cp:coreProperties>')
            z.writestr("word/document.xml", doc)
            z.writestr("word/_rels/document.xml.rels",
                       f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>')
            z.writestr("word/header1.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + hdr)
            z.writestr("word/_rels/header1.xml.rels",
                       f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{hdr_rels}</Relationships>')
            z.writestr("word/footer1.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>' + self._pie())
            z.writestr("word/styles.xml", _estilos(self.fuente, self.tam))
            z.writestr("word/numbering.xml", _NUMERACION)
            z.writestr("word/settings.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                       '<w:updateFields w:val="true"/><w:defaultTabStop w:val="708"/><w:compat><w:compatSetting w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word" w:val="15"/></w:compat></w:settings>')
            if self.logo:
                z.writestr(f"word/media/logo.{_img_tam(self.logo)[2]}", self.logo)
            for nombre, datos in self.medios:
                z.writestr(f"word/media/{nombre}", datos)
        return buf.getvalue()


_NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
       'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
       'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
       'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"')


def _estilos(fuente, tam):
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="{fuente}" w:hAnsi="{fuente}" w:cs="{fuente}" w:eastAsia="{fuente}"/>'
            f'<w:color w:val="222222"/><w:sz w:val="{int(tam * 2)}"/><w:szCs w:val="{int(tam * 2)}"/><w:lang w:val="es-CO"/></w:rPr></w:rPrDefault>'
            '<w:pPrDefault><w:pPr><w:spacing w:after="80" w:line="252" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
            '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
            '<w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val="Normal Table"/><w:tblPr><w:tblInd w:w="0" w:type="dxa"/>'
            '<w:tblCellMar><w:top w:w="0" w:type="dxa"/><w:left w:w="108" w:type="dxa"/><w:bottom w:w="0" w:type="dxa"/><w:right w:w="108" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style>'
            '</w:styles>')


_NUMERACION = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
               '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="■"/>'
               '<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="360" w:hanging="260"/></w:pPr><w:rPr><w:color w:val="3F8C3C"/><w:sz w:val="14"/></w:rPr></w:lvl></w:abstractNum>'
               '<w:abstractNum w:abstractNumId="1"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/>'
               '<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="360" w:hanging="300"/></w:pPr><w:rPr><w:b/><w:color w:val="1B2A4A"/></w:rPr></w:lvl></w:abstractNum>'
               '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num><w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num></w:numbering>')
