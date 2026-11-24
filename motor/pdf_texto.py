"""
Extraccion de texto de PDF con la libreria estandar (sin pypdf / PyMuPDF).

Cubre lo que traen los certificados de tradicion de la SNR (iText, Helvetica WinAnsi) y la mayoria de
fichas tecnicas de modulos (fuentes Type0/CID con ToUnicode, flujos de objetos comprimidos).
No hace OCR: un PDF escaneado devuelve poco o ningun texto (se informa en `escaneado`).
"""
import re
import zlib

_WS = b" \t\r\n\f\x00"
_DELIM = b"()<>[]{}/%"


class _Ref:
    __slots__ = ("n",)

    def __init__(self, n):
        self.n = n

    def __repr__(self):
        return f"R{self.n}"


class _Op(str):
    pass


def _tokens(b, i=0, fin=None):
    """Tokenizador PDF sencillo: numeros, nombres, cadenas, arreglos, diccionarios, operadores."""
    fin = len(b) if fin is None else fin
    while i < fin:
        c = b[i:i + 1]
        if c in (b" ", b"\t", b"\r", b"\n", b"\f", b"\x00"):
            i += 1
        elif c == b"%":
            while i < fin and b[i:i + 1] not in (b"\r", b"\n"):
                i += 1
        elif c == b"/":
            j = i + 1
            while j < fin and b[j:j + 1] not in _WS and b[j:j + 1] not in _DELIM:
                j += 1
            nombre = re.sub(rb"#([0-9A-Fa-f]{2})", lambda m: bytes([int(m.group(1), 16)]), b[i + 1:j])
            yield ("name", nombre.decode("latin-1")), j
            i = j
        elif c == b"(":
            prof, j, out = 1, i + 1, bytearray()
            while j < fin and prof:
                ch = b[j]
                if ch == 0x5C:  # backslash
                    j += 1
                    e = b[j:j + 1]
                    esc = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f"}
                    if e in esc:
                        out += esc[e]; j += 1
                    elif e in (b"\r", b"\n"):
                        j += 1
                        if e == b"\r" and b[j:j + 1] == b"\n":
                            j += 1
                    elif e.isdigit():
                        k = j
                        while k < j + 3 and b[k:k + 1].isdigit():
                            k += 1
                        out.append(int(b[j:k], 8) & 0xFF); j = k
                    else:
                        out += e; j += 1
                    continue
                if ch == 0x28:
                    prof += 1
                elif ch == 0x29:
                    prof -= 1
                    if not prof:
                        break
                out.append(ch); j += 1
            yield ("str", bytes(out)), j + 1
            i = j + 1
        elif c == b"<" and b[i + 1:i + 2] == b"<":
            yield ("<<", None), i + 2
            i += 2
        elif c == b">" and b[i + 1:i + 2] == b">":
            yield (">>", None), i + 2
            i += 2
        elif c == b"<":
            j = b.find(b">", i)
            h = re.sub(rb"[^0-9A-Fa-f]", b"", b[i + 1:j])
            if len(h) % 2:
                h += b"0"
            yield ("str", bytes.fromhex(h.decode())), j + 1
            i = j + 1
        elif c in (b"[", b"]", b"{", b"}"):
            yield (c.decode(), None), i + 1
            i += 1
        else:
            j = i
            while j < fin and b[j:j + 1] not in _WS and b[j:j + 1] not in _DELIM:
                j += 1
            if j == i:
                i += 1
                continue
            w = b[i:j]
            try:
                v = float(w) if (b"." in w) else int(w)
                yield ("num", v), j
            except ValueError:
                yield ("op", w.decode("latin-1")), j
            i = j


def _parse(toks):
    """Convierte la secuencia de tokens en objetos Python (dict, list, _Ref, str para nombres, bytes, num)."""
    pila = [[]]
    for (t, v), pos in toks:
        if t in ("[", "<<"):
            pila.append([t])
        elif t == "]":
            items = pila.pop()[1:]
            pila[-1].append(items)
        elif t == ">>":
            items = pila.pop()[1:]
            d = {}
            for k in range(0, len(items) - 1, 2):
                if isinstance(items[k], str) and not isinstance(items[k], _Op):
                    d[items[k]] = items[k + 1]
            pila[-1].append(d)
        elif t == "op" and v == "R" and len(pila[-1]) >= 2 and isinstance(pila[-1][-1], int) and isinstance(pila[-1][-2], int):
            pila[-1].pop()
            pila[-1][-1] = _Ref(pila[-1][-1])
        elif t == "name":
            pila[-1].append(v)
        elif t == "str":
            pila[-1].append(v)
        elif t == "num":
            pila[-1].append(v)
        elif t == "op":
            if v in ("true", "false"):
                pila[-1].append(v == "true")
            elif v == "null":
                pila[-1].append(None)
            else:
                pila[-1].append(_Op(v))
        elif t in ("{", "}"):
            pass
    return pila[0]


class Documento:
    def __init__(self, datos):
        self.b = datos
        self.objs = {}  # n -> (dict_o_valor, bytes_stream_crudo o None)
        self._leer_objetos()

    # ---- estructura -------------------------------------------------------------------------
    def _leer_objetos(self):
        b = self.b
        for m in re.finditer(rb"(\d+)\s+(\d+)\s+obj\b", b):
            n = int(m.group(1))
            ini = m.end()
            fin = b.find(b"endobj", ini)
            if fin < 0:
                fin = len(b)
            s = b.find(b"stream", ini, fin)
            stream = None
            if s >= 0 and b[s - 3:s] != b"end":
                cab = b[ini:s]
                k = s + 6
                if b[k:k + 2] == b"\r\n":
                    k += 2
                elif b[k:k + 1] in (b"\n", b"\r"):
                    k += 1
                e = b.rfind(b"endstream", k, fin + 9 if fin + 9 <= len(b) else len(b))
                if e < 0:
                    e = b.find(b"endstream", k)
                stream = b[k:e].rstrip(b"\r\n") if e > 0 else b[k:]
                cuerpo = _parse(_tokens(cab))
            else:
                cuerpo = _parse(_tokens(b, ini, fin))
            val = cuerpo[0] if cuerpo else None
            if stream is not None and isinstance(val, dict):
                ln = val.get("Length")
                if isinstance(ln, int) and 0 < ln <= len(stream) + 2:
                    stream = stream[:ln] if len(stream) >= ln else stream
            self.objs[n] = (val, stream)
        # flujos de objetos (PDF 1.5+)
        for n, (d, st) in list(self.objs.items()):
            if isinstance(d, dict) and d.get("Type") == "ObjStm" and st is not None:
                try:
                    datos = self._decodificar(d, st)
                    cnt, prim = int(d.get("N", 0)), int(self.res(d.get("First", 0)))
                    cab = [v for (t, v), _ in _tokens(datos[:prim]) if t == "num"]
                    for k in range(cnt):
                        on, off = int(cab[2 * k]), int(cab[2 * k + 1])
                        sig = int(cab[2 * k + 2]) if 2 * k + 2 < len(cab) else len(datos) - prim
                        v = _parse(_tokens(datos, prim + off, prim + sig))
                        if on not in self.objs:
                            self.objs[on] = (v[0] if v else None, None)
                except Exception:
                    pass

    def res(self, v):
        seen = 0
        while isinstance(v, _Ref) and seen < 20:
            v = self.objs.get(v.n, (None, None))[0]
            seen += 1
        return v

    def stream_de(self, ref):
        if isinstance(ref, _Ref):
            d, st = self.objs.get(ref.n, (None, None))
            if st is None:
                return b""
            return self._decodificar(d, st)
        return b""

    def _decodificar(self, d, st):
        filtros = self.res(d.get("Filter")) if isinstance(d, dict) else None
        if filtros is None:
            return st
        if not isinstance(filtros, list):
            filtros = [filtros]
        for f in filtros:
            if f in ("FlateDecode", "Fl"):
                try:
                    st = zlib.decompress(st)
                except zlib.error:
                    try:
                        st = zlib.decompressobj().decompress(st)
                    except zlib.error:
                        return b""
            elif f in ("ASCIIHexDecode", "AHx"):
                h = re.sub(rb"[^0-9A-Fa-f]", b"", st.split(b">")[0])
                st = bytes.fromhex((h + b"0" * (len(h) % 2)).decode())
            elif f in ("ASCII85Decode", "A85"):
                import base64
                st = base64.a85decode(re.sub(rb"\s", b"", st.split(b"~>")[0].replace(b"<~", b"")))
            else:  # DCT/JPX/CCITT: imagen, no texto
                return b""
        return st

    def paginas(self):
        raiz = None
        m = re.findall(rb"/Root\s+(\d+)\s+\d+\s+R", self.b)
        if m:
            raiz = self.res(_Ref(int(m[-1])))
        if not isinstance(raiz, dict):
            for d, _ in self.objs.values():
                if isinstance(d, dict) and d.get("Type") == "Catalog":
                    raiz = d
                    break
        out = []

        def rec(nodo, heredado, prof=0):
            nodo = self.res(nodo)
            if not isinstance(nodo, dict) or prof > 30:
                return
            h = dict(heredado)
            if "Resources" in nodo:
                h["Resources"] = nodo["Resources"]
            if nodo.get("Type") == "Pages" or "Kids" in nodo:
                for k in self.res(nodo.get("Kids")) or []:
                    rec(k, h, prof + 1)
            else:
                p = dict(nodo)
                p.setdefault("Resources", h.get("Resources"))
                out.append(p)

        if isinstance(raiz, dict):
            rec(raiz.get("Pages"), {})
        if not out:  # sin arbol legible: todas las /Type /Page en orden de aparicion
            out = [d for n, (d, _) in sorted(self.objs.items()) if isinstance(d, dict) and d.get("Type") == "Page"]
        return out

    # ---- fuentes -----------------------------------------------------------------------------
    def fuente(self, ref, cache):
        clave = ref.n if isinstance(ref, _Ref) else id(ref)
        if clave in cache:
            return cache[clave]
        f = self.res(ref) or {}
        mapa, bytes_x_car = {}, 1
        if f.get("Subtype") == "Type0":
            bytes_x_car = 2
        tu = f.get("ToUnicode")
        if isinstance(tu, _Ref):
            cm = self.stream_de(tu)
            mapa, bytes_x_car = _cmap(cm, bytes_x_car)
        dif = {}
        enc = self.res(f.get("Encoding"))
        if isinstance(enc, dict):
            cod = 0
            for v in self.res(enc.get("Differences")) or []:
                if isinstance(v, (int, float)):
                    cod = int(v)
                elif isinstance(v, str):
                    u = _GLIFOS.get(v)
                    if u is None and re.fullmatch(r"uni[0-9A-Fa-f]{4}", v):
                        u = chr(int(v[3:], 16))
                    if u is not None:
                        dif[cod] = u
                    cod += 1
        cache[clave] = (mapa, bytes_x_car, dif)
        return cache[clave]


def _cmap(cm, bytes_x_car):
    mapa = {}
    anchos = set()

    def hexa(s):
        return int(s, 16)

    def uni(h):
        try:
            return bytes.fromhex(h).decode("utf-16-be", "replace")
        except ValueError:
            return ""

    for bloque in re.findall(rb"beginbfchar(.*?)endbfchar", cm, re.S):
        for a, u in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", bloque):
            mapa[hexa(a)] = uni(u.decode())
            anchos.add(len(a) // 2)
    for bloque in re.findall(rb"beginbfrange(.*?)endbfrange", cm, re.S):
        for a, z, rest in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(<[0-9A-Fa-f]*>|\[[^\]]*\])", bloque):
            ia, iz = hexa(a), hexa(z)
            anchos.add(len(a) // 2)
            if rest.startswith(b"["):
                for k, u in enumerate(re.findall(rb"<([0-9A-Fa-f]*)>", rest)):
                    mapa[ia + k] = uni(u.decode())
            else:
                base = rest[1:-1].decode()
                if not base:
                    continue
                bv = hexa(base)
                for k in range(0, min(iz - ia, 65535) + 1):
                    mapa[ia + k] = uni(format(bv + k, "0%dX" % len(base)))
    if anchos:
        bytes_x_car = max(anchos)
    return mapa, bytes_x_car


_GLIFOS = {"space": " ", "hyphen": "-", "period": ".", "comma": ",", "colon": ":", "semicolon": ";",
           "aacute": "á", "eacute": "é", "iacute": "í", "oacute": "ó", "uacute": "ú", "ntilde": "ñ",
           "Aacute": "Á", "Eacute": "É", "Iacute": "Í", "Oacute": "Ó", "Uacute": "Ú", "Ntilde": "Ñ",
           "udieresis": "ü", "degree": "°", "multiply": "×", "plusminus": "±", "percent": "%",
           "parenleft": "(", "parenright": ")", "slash": "/", "quotesingle": "'", "quotedbl": '"',
           "bullet": "•", "endash": "–", "emdash": "—", "mu": "µ", "periodcentered": "·"}


def _decodificar_texto(s, fuente):
    mapa, n, dif = fuente
    if mapa:
        out = []
        i = 0
        while i < len(s):
            c = int.from_bytes(s[i:i + n], "big")
            out.append(mapa.get(c, "" if n > 1 else (dif.get(c) or bytes([c]).decode("cp1252", "replace"))))
            i += n
        return "".join(out)
    if n > 1:  # CID sin ToUnicode: intento como UTF-16
        return s.decode("utf-16-be", "ignore")
    if dif:
        return "".join(dif.get(c) or bytes([c]).decode("cp1252", "replace") for c in s)
    return s.decode("cp1252", "replace")


def _texto_contenido(doc, datos, recursos, cache, prof=0):
    """Recorre un flujo de contenido y arma lineas segun los cambios de posicion vertical."""
    recursos = doc.res(recursos) or {}
    fuentes = doc.res(recursos.get("Font")) or {}
    xobjs = doc.res(recursos.get("XObject")) or {}
    lineas, actual = [], []
    fuente = ({}, 1, {})
    y = None
    pila = []
    tam = 10

    def nueva_linea():
        if actual:
            lineas.append("".join(actual))
            actual.clear()

    def mover(ny):
        nonlocal y
        if y is not None and ny is not None and abs(ny - y) > tam * 0.5:
            nueva_linea()
        elif actual and not actual[-1].endswith(" "):
            actual.append(" ")
        y = ny

    lx = ly = 0.0
    for obj in _parse(_tokens(datos)):
        if not isinstance(obj, _Op):
            pila.append(obj)
            continue
        op = str(obj)
        try:
            if op == "Tf" and len(pila) >= 2:
                ref = fuentes.get(pila[-2]) if isinstance(fuentes, dict) else None
                fuente = doc.fuente(ref, cache) if ref is not None else ({}, 1, {})
                tam = abs(pila[-1]) or tam
            elif op == "BT":
                lx = ly = 0.0
            elif op in ("Td", "TD") and len(pila) >= 2:
                lx += pila[-2]; ly += pila[-1]
                mover(ly if pila[-1] else y)
            elif op == "Tm" and len(pila) >= 6:
                lx, ly = pila[-2], pila[-1]
                mover(ly)
            elif op == "T*":
                ly -= tam
                mover(ly)
            elif op == "Tj" and pila:
                actual.append(_decodificar_texto(pila[-1], fuente))
            elif op in ("'", '"') and pila:
                ly -= tam
                mover(ly)
                actual.append(_decodificar_texto(pila[-1], fuente))
            elif op == "TJ" and pila and isinstance(pila[-1], list):
                for el in pila[-1]:
                    if isinstance(el, bytes):
                        actual.append(_decodificar_texto(el, fuente))
                    elif isinstance(el, (int, float)) and el < -180 and actual and not actual[-1].endswith(" "):
                        actual.append(" ")
            elif op == "ET":
                pass
            elif op == "Do" and pila and prof < 5:
                ref = xobjs.get(pila[-1]) if isinstance(xobjs, dict) else None
                d = doc.res(ref)
                if isinstance(d, dict) and d.get("Subtype") == "Form":
                    nueva_linea()
                    lineas.extend(_texto_contenido(doc, doc.stream_de(ref), d.get("Resources") or recursos, cache, prof + 1))
        except Exception:
            pass
        pila = []
    nueva_linea()
    return lineas


def extraer(datos):
    """Texto de un PDF (bytes) -> dict(paginas=[str], texto=str, escaneado=bool)."""
    doc = Documento(datos)
    cache = {}
    paginas = []
    for p in doc.paginas():
        cont = p.get("Contents")
        cont = doc.res(cont) if not isinstance(cont, _Ref) else cont
        refs = cont if isinstance(cont, list) else [cont]
        flujo = b"\n".join(doc.stream_de(r) for r in refs if isinstance(r, _Ref))
        lineas = _texto_contenido(doc, flujo, p.get("Resources"), cache)
        paginas.append("\n".join(re.sub(r"[ \t]+", " ", l).strip() for l in lineas if l.strip()))
    texto = "\n".join(paginas)
    return dict(paginas=paginas, texto=texto,
                escaneado=len(re.sub(r"\s", "", texto)) < 40 * max(1, len(paginas)))


if __name__ == "__main__":
    import sys
    r = extraer(open(sys.argv[1], "rb").read())
    sys.stdout.reconfigure(encoding="utf-8")
    print(r["texto"])
    print("\n[paginas:", len(r["paginas"]), "escaneado:", r["escaneado"], "]")
