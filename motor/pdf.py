"""
Generador mínimo de PDF con la librería estándar: texto (Helvetica), rectángulos, líneas e imágenes.

Coordenadas en puntos, con el origen ARRIBA a la izquierda (como en pantalla); la conversión al sistema
del PDF la hace la clase. Fuentes estándar Helvetica y Helvetica-Bold en codificación WinAnsi (tildes y eñes).
Imágenes: JPEG tal cual (DCTDecode) y PNG de 8 bits RGB, RGBA o gris sin entrelazado.
"""
import struct
import unicodedata
import zlib

# anchos de Helvetica y Helvetica-Bold (milésimas de em) para los caracteres 32 a 126
_W = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278,
      584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667,
      944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556,
      333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584]
_WB = [278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 333, 333,
       584, 584, 584, 611, 975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667,
       944, 667, 667, 611, 333, 278, 333, 584, 556, 333, 556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611, 611, 611,
       389, 556, 333, 611, 556, 778, 556, 556, 500, 389, 280, 389, 584]
_OTROS = {"·": 278, "–": 556, "—": 1000, "°": 400, "«": 556, "»": 556, "¿": 611, "¡": 333, "•": 350, "²": 333, "º": 365, "ª": 370, "€": 556}
_CAMBIOS = {"₂": "2", "≈": "~", "→": "-", "≤": "<=", "≥": ">=", "＋": "+", "…": "...", " ": " ", "’": "'", "“": '"', "”": '"'}


def _limpio(t):
    t = "".join(_CAMBIOS.get(c, c) for c in str(t))
    return t.encode("cp1252", "replace").decode("cp1252")


def ancho_texto(t, tam, negrita=False):
    tabla, total = (_WB if negrita else _W), 0
    for c in _limpio(t):
        if 32 <= ord(c) <= 126:
            total += tabla[ord(c) - 32]
        elif c in _OTROS:
            total += _OTROS[c]
        else:  # letras con tilde, eñe: ancho de la letra base
            b = unicodedata.normalize("NFD", c)[0]
            total += tabla[ord(b) - 32] if 32 <= ord(b) <= 126 else 556
    return total * tam / 1000.0


def _rgb(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _png(datos):
    """PNG -> (ancho, alto, canales de color, bytes de color, bytes de alfa o None), todo sin comprimir."""
    assert datos[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, w = 8, b"", None
    while pos < len(datos):
        n, tipo = struct.unpack(">I4s", datos[pos:pos + 8])
        cuerpo = datos[pos + 8:pos + 8 + n]
        if tipo == b"IHDR":
            w, h, bits, color, _, _, entrelazado = struct.unpack(">IIBBBBB", cuerpo)
            if bits != 8 or entrelazado or color not in (0, 2, 4, 6):
                raise ValueError("PNG no soportado (debe ser de 8 bits, sin paleta ni entrelazado)")
        elif tipo == b"IDAT":
            idat += cuerpo
        pos += 12 + n
    canales = {0: 1, 2: 3, 4: 2, 6: 4}[color]
    crudo, paso = zlib.decompress(idat), w * canales
    filas, previa = [], bytearray(paso)
    for y in range(h):
        f = crudo[y * (paso + 1)]
        fila = bytearray(crudo[y * (paso + 1) + 1:(y + 1) * (paso + 1)])
        if f == 1:
            for i in range(canales, paso):
                fila[i] = (fila[i] + fila[i - canales]) & 255
        elif f == 2:
            for i in range(paso):
                fila[i] = (fila[i] + previa[i]) & 255
        elif f == 3:
            for i in range(paso):
                fila[i] = (fila[i] + ((fila[i - canales] if i >= canales else 0) + previa[i]) // 2) & 255
        elif f == 4:
            for i in range(paso):
                a, b, c = (fila[i - canales] if i >= canales else 0), previa[i], (previa[i - canales] if i >= canales else 0)
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                fila[i] = (fila[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        filas.append(fila)
        previa = fila
    todo = b"".join(filas)
    if canales in (1, 3):
        return w, h, canales, todo, None
    nc = canales - 1
    color_b = bytearray(w * h * nc)
    for k in range(nc):
        color_b[k::nc] = todo[k::canales]
    return w, h, nc, bytes(color_b), todo[nc::canales]


def _jpeg_tam(d):
    i = 2
    while i < len(d):
        if d[i] != 0xFF:
            i += 1
            continue
        m = d[i + 1]
        if m in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack(">HH", d[i + 5:i + 9])
            return w, h, d[i + 9]
        i += 2 + struct.unpack(">H", d[i + 2:i + 4])[0]
    raise ValueError("JPEG no reconocido")


class Pdf:
    def __init__(self, ancho=960, alto=540, titulo="", autor=""):
        self.w, self.h, self.titulo, self.autor = ancho, alto, titulo, autor
        self.paginas, self.imgs, self._c = [], [], None

    # ---------------------------------------------------------------- páginas y dibujo
    def pagina(self):
        self._c = []
        self.paginas.append(self._c)

    def _color(self, c, trazo=False):
        r, g, b = _rgb(c)
        self._c.append(f"{r:.3f} {g:.3f} {b:.3f} {'RG' if trazo else 'rg'}")

    def rect(self, x, y, w, h, relleno=None, borde=None, radio=0, grosor=1):
        y = self.h - y - h
        if relleno:
            self._color(relleno)
        if borde:
            self._color(borde, True)
            self._c.append(f"{grosor} w")
        if radio:
            r, k = min(radio, w / 2, h / 2), 0.5523
            c = r * k
            self._c.append(f"{x + r:.2f} {y:.2f} m {x + w - r:.2f} {y:.2f} l {x + w - r + c:.2f} {y:.2f} {x + w:.2f} {y + r - c:.2f} {x + w:.2f} {y + r:.2f} c "
                           f"{x + w:.2f} {y + h - r:.2f} l {x + w:.2f} {y + h - r + c:.2f} {x + w - r + c:.2f} {y + h:.2f} {x + w - r:.2f} {y + h:.2f} c "
                           f"{x + r:.2f} {y + h:.2f} l {x + r - c:.2f} {y + h:.2f} {x:.2f} {y + h - r + c:.2f} {x:.2f} {y + h - r:.2f} c "
                           f"{x:.2f} {y + r:.2f} l {x:.2f} {y + r - c:.2f} {x + r - c:.2f} {y:.2f} {x + r:.2f} {y:.2f} c h")
        else:
            self._c.append(f"{x:.2f} {y:.2f} {w:.2f} {h:.2f} re")
        self._c.append("B" if relleno and borde else "f" if relleno else "S")

    def linea(self, x0, y0, x1, y1, color="#000000", grosor=1):
        self._color(color, True)
        self._c.append(f"{grosor} w {x0:.2f} {self.h - y0:.2f} m {x1:.2f} {self.h - y1:.2f} l S")

    def circulo(self, cx, cy, r, relleno):
        self.rect(cx - r, cy - r, 2 * r, 2 * r, relleno, radio=r)

    def texto(self, x, y, t, tam=11, negrita=False, color="#0d2436", alin="izq", ancho=None, interlinea=1.28):
        """Escribe t con la línea base del primer renglón en y + tam. Con `ancho`, parte en renglones.
        Devuelve la altura ocupada."""
        renglones = []
        for parrafo in _limpio(t).split("\n"):
            if not ancho:
                renglones.append(parrafo)
                continue
            actual = ""
            for palabra in parrafo.split(" "):
                prueba = (actual + " " + palabra).strip()
                if ancho_texto(prueba, tam, negrita) <= ancho or not actual:
                    actual = prueba
                else:
                    renglones.append(actual)
                    actual = palabra
            renglones.append(actual)
        self._color(color)
        for i, r in enumerate(renglones):
            aw = ancho_texto(r, tam, negrita)
            base = ancho if ancho else 0
            xx = x + (base - aw) / 2 if alin == "centro" else x + base - aw if alin == "der" else x
            yy = self.h - (y + tam + i * tam * interlinea)
            crudo = r.encode("cp1252", "replace").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)").decode("latin1")
            self._c.append(f"BT /F{2 if negrita else 1} {tam} Tf {xx:.2f} {yy:.2f} Td ({crudo}) Tj ET")
        return len(renglones) * tam * interlinea

    def imagen(self, datos, x, y, w=None, h=None, encajar=False):
        """Dibuja un JPEG o PNG. Con w y h y encajar=True conserva la proporción dentro de la caja, centrada."""
        if datos[:2] == b"\xff\xd8":
            iw, ih, canales = _jpeg_tam(datos)
            obj = dict(w=iw, h=ih, cs="/DeviceRGB" if canales == 3 else "/DeviceGray", filtro="/DCTDecode", datos=datos, alfa=None)
        else:
            iw, ih, canales, color, alfa = _png(datos)
            obj = dict(w=iw, h=ih, cs="/DeviceRGB" if canales == 3 else "/DeviceGray", filtro="/FlateDecode", datos=zlib.compress(color, 9),
                       alfa=zlib.compress(alfa, 9) if alfa else None)
        if w is None and h is None:
            w, h = iw, ih
        elif h is None:
            h = w * ih / iw
        elif w is None:
            w = h * iw / ih
        elif encajar:
            e = min(w / iw, h / ih)
            x, y, w, h = x + (w - iw * e) / 2, y + (h - ih * e) / 2, iw * e, ih * e
        self.imgs.append(obj)
        self._c.append(f"q {w:.2f} 0 0 {h:.2f} {x:.2f} {self.h - y - h:.2f} cm /Im{len(self.imgs)} Do Q")
        return w, h

    def recortar(self, x, y, w, h):
        self._c.append(f"q {x:.2f} {self.h - y - h:.2f} {w:.2f} {h:.2f} re W n")

    def fin_recorte(self):
        self._c.append("Q")

    # ---------------------------------------------------------------- archivo
    def guardar(self):
        objs = []

        def nuevo(cuerpo):
            objs.append(cuerpo if isinstance(cuerpo, bytes) else cuerpo.encode("latin1"))
            return len(objs)

        def flujo(dic, datos):
            return f"<< {dic} /Length {len(datos)} >>\nstream\n".encode("latin1") + datos + b"\nendstream"
        f1 = nuevo("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        f2 = nuevo("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        xobj = []
        for im in self.imgs:
            sm = ""
            if im["alfa"]:
                n = nuevo(flujo(f"/Type /XObject /Subtype /Image /Width {im['w']} /Height {im['h']} /ColorSpace /DeviceGray "
                                f"/BitsPerComponent 8 /Filter /FlateDecode", im["alfa"]))
                sm = f" /SMask {n} 0 R"
            xobj.append(nuevo(flujo(f"/Type /XObject /Subtype /Image /Width {im['w']} /Height {im['h']} /ColorSpace {im['cs']} "
                                    f"/BitsPerComponent 8 /Filter {im['filtro']}{sm}", im["datos"])))
        recursos = f"<< /Font << /F1 {f1} 0 R /F2 {f2} 0 R >> /XObject << " + " ".join(f"/Im{i + 1} {n} 0 R" for i, n in enumerate(xobj)) + " >> >>"
        paginas_n = len(objs) + 1 + 2 * len(self.paginas)  # el objeto Pages va después de todas las páginas
        hijos = []
        for cont in self.paginas:
            c = nuevo(flujo("/Filter /FlateDecode", zlib.compress("\n".join(cont).encode("latin1"), 9)))
            hijos.append(nuevo(f"<< /Type /Page /Parent {paginas_n} 0 R /MediaBox [0 0 {self.w} {self.h}] /Resources {recursos} /Contents {c} 0 R >>"))
        pags = nuevo(f"<< /Type /Pages /Kids [{' '.join(f'{h} 0 R' for h in hijos)}] /Count {len(hijos)} >>")
        assert pags == paginas_n
        cat = nuevo(f"<< /Type /Catalog /Pages {pags} 0 R >>")

        def cadena(s):
            return "(" + _limpio(s).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"
        info = nuevo(f"<< /Title {cadena(self.titulo)} /Author {cadena(self.autor)} /Creator (Evaluador GD) >>".encode("cp1252", "replace"))
        salida, posiciones = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"), []
        for i, o in enumerate(objs, 1):
            posiciones.append(len(salida))
            salida += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
        xref = len(salida)
        salida += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + "".join(f"{p:010d} 00000 n \n" for p in posiciones).encode()
        salida += f"trailer\n<< /Size {len(objs) + 1} /Root {cat} 0 R /Info {info} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
        return bytes(salida)
