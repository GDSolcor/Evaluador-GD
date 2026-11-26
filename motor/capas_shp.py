"""
Lectura de shapefiles grandes por vista del mapa, sin librerias externas.

La primera vez se construye un indice espacial R-tree (SQLite) con el bbox de cada registro,
leido de la cabecera del registro en el .shp. Despues cada consulta solo abre los registros
que caen en la vista: la geometria se lee del .shp por su desplazamiento (.shx) y los
atributos del .dbf por numero de registro (registros de largo fijo).
"""
import os
import sqlite3
import struct
import threading

import bd

DATOS = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "datos"))


def clave_nube(base):
    """Nombre de la capa en ev_shp (ruta relativa a datos/, p. ej. igac/hidrografia_100k/laguna)."""
    return os.path.relpath(os.path.normpath(base), DATOS).replace("\\", "/")


def existe(base):
    if bd.NUBE:
        try:
            return bd.conectar("ev_shp").execute("select 1 from capas where capa=?", (clave_nube(base),)).fetchone() is not None
        except sqlite3.Error:
            return False
    return os.path.isfile(base + ".shp")


class CapaShp:
    """En la nube (EVALUADOR_DB_URL) los registros del .shp y del .dbf, tal cual en bytes, y su recuadro están en
    ev_shp.registros (herramientas/migrar_a_nube.py): el índice es GiST y no hay que construir nada."""

    def __init__(self, base, crs="wgs84"):
        self.base = base
        self.crs = crs
        self.idx_path = base + ".rtree.sqlite"
        self._local = threading.local()
        self._construyendo = False
        if bd.NUBE:
            self.clave = clave_nube(base)
            self.progreso = 1.0
        else:
            self.progreso = 1.0 if os.path.isfile(self.idx_path) and self._indice_valido() else 0.0
        self._leer_dbf_header()

    # ---------------- DBF ----------------
    def _leer_dbf_header(self):
        if bd.NUBE:
            n, cab, cpg = bd.conectar("ev_shp").execute(
                "select n, dbf_cabecera, cpg from capas where capa=?", (self.clave,)).fetchone()
            cab = bytes(cab)
            h, d = cab[:32], cab[32:]
            self.n, self.hl, self.rl = struct.unpack("<IHH", h[4:12])
            self.enc = cpg or "latin1"
        else:
            with open(self.base + ".dbf", "rb") as f:
                h = f.read(32)
                self.n, self.hl, self.rl = struct.unpack("<IHH", h[4:12])
                d = f.read(self.hl - 32)
            cpg = self.base + ".cpg"
            self.enc = open(cpg).read().strip() if os.path.isfile(cpg) else "latin1"
        if self.enc.upper().replace("-", "") in ("UTF8", "65001"):
            self.enc = "utf-8"
        self.campos, off = [], 1
        for i in range((self.hl - 33) // 32):
            c = d[32 * i:32 * i + 32]
            nm = c[:11].split(b"\0")[0].decode("latin1")
            self.campos.append((nm, chr(c[11]), off, c[16]))
            off += c[16]

    def atributos(self, i, f=None):
        if bd.NUBE:
            fila = bd.conectar("ev_shp").execute("select dbf from registros where capa=? and i=?", (self.clave, i)).fetchone()
            return self._atributos_de(bytes(fila[0]) if fila else b"")
        cerrar = f is None
        f = f or open(self.base + ".dbf", "rb")
        try:
            f.seek(self.hl + i * self.rl)
            r = f.read(self.rl)
        finally:
            if cerrar:
                f.close()
        return self._atributos_de(r)

    def _atributos_de(self, r):
        out = {}
        for nm, t, off, ln in self.campos:
            v = r[off:off + ln].decode(self.enc, "replace").strip()
            if t in ("N", "F") and v:
                try:
                    v = float(v) if ("." in v or "e" in v.lower()) else int(v)
                except ValueError:
                    pass
            out[nm] = v
        return out

    # ---------------- indice ----------------
    def _db(self):
        if not hasattr(self._local, "c"):
            self._local.c = sqlite3.connect(self.idx_path, check_same_thread=False)
        return self._local.c

    def _indice_valido(self):
        try:
            c = sqlite3.connect(self.idx_path)
            ok = c.execute("select valor from meta where clave='completo'").fetchone()
            c.close()
            return bool(ok)
        except sqlite3.Error:
            return False

    def construir_indice(self):
        if self._construyendo or self.progreso >= 1.0:
            return
        self._construyendo = True
        try:
            tmp = self.idx_path + ".tmp"
            if os.path.exists(tmp):
                os.remove(tmp)
            c = sqlite3.connect(tmp)
            c.execute("create virtual table idx using rtree(id, x0, x1, y0, y1)")
            c.execute("create table meta(clave text primary key, valor text)")
            with open(self.base + ".shx", "rb") as fx:
                fx.seek(100)
                shx = fx.read()
            total = len(shx) // 8
            lote = []
            with open(self.base + ".shp", "rb", buffering=1 << 16) as fs:
                for i in range(total):
                    off = struct.unpack_from(">i", shx, 8 * i)[0] * 2
                    fs.seek(off + 8)
                    cab = fs.read(36)
                    if len(cab) < 36 or struct.unpack_from("<i", cab)[0] == 0:
                        continue  # geometria nula
                    x0, y0, x1, y1 = struct.unpack_from("<4d", cab, 4)
                    lote.append((i + 1, x0, x1, y0, y1))
                    if len(lote) >= 50000:
                        c.executemany("insert into idx values (?,?,?,?,?)", lote)
                        lote.clear()
                        self.progreso = min(0.99, i / total)
            if lote:
                c.executemany("insert into idx values (?,?,?,?,?)", lote)
            c.execute("insert into meta values ('completo', '1')")
            c.commit()
            c.close()
            os.replace(tmp, self.idx_path)
            self.progreso = 1.0
        finally:
            self._construyendo = False

    def construir_en_segundo_plano(self):
        if self.progreso < 1.0 and not self._construyendo:
            threading.Thread(target=self.construir_indice, daemon=True).start()

    # ---------------- consultas ----------------
    def dbf_completo(self):
        """Todos los registros del .dbf seguidos (sin la cabecera), como los lee catastro._lista_veredas."""
        if bd.NUBE:
            return b"".join(bytes(r[0]) for r in bd.conectar("ev_shp").execute(
                "select dbf from registros where capa=? order by i", (self.clave,)))
        with open(self.base + ".dbf", "rb") as fd:
            fd.seek(self.hl)
            return fd.read(self.rl * self.n)

    def ids_en(self, x0, y0, x1, y1, limite=3000):
        if bd.NUBE:
            rows = bd.conectar("ev_shp").execute(
                "select i from registros where capa=? and x1>=? and x0<=? and y1>=? and y0<=? limit ?",
                (self.clave, x0, x1, y0, y1, limite + 1)).fetchall()
            return [r[0] for r in rows]
        rows = self._db().execute(
            "select id from idx where x1>=? and x0<=? and y1>=? and y0<=? limit ?", (x0, x1, y0, y1, limite + 1)
        ).fetchall()
        return [r[0] - 1 for r in rows]

    def geometria(self, i, fs=None, fx=None):
        """Partes (anillos o trazos) del registro i en el CRS nativo."""
        if bd.NUBE:
            fila = bd.conectar("ev_shp").execute("select shp from registros where capa=? and i=?", (self.clave, i)).fetchone()
            return self._geometria_de(bytes(fila[0]))
        cerrar = fs is None
        fs = fs or open(self.base + ".shp", "rb")
        fx = fx or open(self.base + ".shx", "rb")
        try:
            fx.seek(100 + 8 * i)
            off, largo = struct.unpack(">ii", fx.read(8))
            fs.seek(off * 2 + 8)
            c = fs.read(largo * 2)
        finally:
            if cerrar:
                fs.close()
                fx.close()
        return self._geometria_de(c)

    @staticmethod
    def _geometria_de(c):
        tipo = struct.unpack_from("<i", c)[0]
        if tipo in (1, 11, 21):
            return "Point", [[struct.unpack_from("<2d", c, 4)]]
        nparts, npts = struct.unpack_from("<ii", c, 36)
        idx = list(struct.unpack_from(f"<{nparts}i", c, 44)) + [npts]
        xy = struct.unpack_from(f"<{2 * npts}d", c, 44 + 4 * nparts)
        partes = [[(xy[2 * k], xy[2 * k + 1]) for k in range(idx[p], idx[p + 1])] for p in range(nparts)]
        return ("Polygon" if tipo in (5, 15, 25) else "LineString"), partes

    def leer(self, ids):
        if bd.NUBE:
            ids = list(ids)
            if not ids:
                return
            filas = {}
            for k in range(0, len(ids), 1000):
                parte = ids[k:k + 1000]
                for i, shp, dbf in bd.conectar("ev_shp").execute(
                        f"select i, shp, dbf from registros where capa=? and i in ({','.join('?' * len(parte))})",
                        [self.clave] + parte):
                    filas[i] = (bytes(shp), bytes(dbf))
            for i in ids:
                if i in filas:
                    tipo, partes = self._geometria_de(filas[i][0])
                    yield i, tipo, partes, self._atributos_de(filas[i][1])
            return
        with open(self.base + ".shp", "rb") as fs, open(self.base + ".shx", "rb") as fx, \
                open(self.base + ".dbf", "rb") as fd:
            for i in ids:
                tipo, partes = self.geometria(i, fs, fx)
                yield i, tipo, partes, self.atributos(i, fd)
