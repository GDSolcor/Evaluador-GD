"""
Servidor local del Evaluador de predios GD.  Solo libreria estandar de Python.

Autor y desarrollador: Manuel Antonio Bayona Cely.
© 2026 Manuel Antonio Bayona Cely. Todos los derechos reservados. Ver LICENCIA.md.

    python servidor.py                 -> http://localhost:8765 (solo este PC)
    python servidor.py --red           -> escucha en la red local (otros PCs de la oficina)
    python servidor.py --puerto 8766   -> otro puerto

Acceso: todo exige sesion (motor/auth.py) salvo /login y /api/auth/*. La primera vez, /login
pide crear el usuario administrador. Ver ARQUITECTURA.md para rutas y flujo.
"""

import json
import os
import sys
import threading
import traceback
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE, "motor"))


def _carpeta_sincronizada(ruta):
    """True si la herramienta corre desde una carpeta de SharePoint/OneDrive sincronizada."""
    r = ruta.lower()
    raices = [os.environ.get(k, "") for k in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer")]
    return any(x and r.startswith(x.lower()) for x in raices) or "sharepoint" in r or "\\onedrive" in r \
        or "solcor colombia s.a.s" in r


# Datos que la herramienta ESCRIBE (usuarios, sesiones, evaluaciones, módulos cargados). Si la carpeta
# está en SharePoint/OneDrive, van a %LOCALAPPDATA%\EvaluadorGD: SQLite no tolera que OneDrive
# sincronice el archivo mientras otro PC lo usa (se generan copias en conflicto o se corrompe).
# Los datos de solo lectura (GIS, catastro, hidrografía) sí se leen desde la carpeta compartida.
if "--datos-usuario" in sys.argv:
    DATOS_USUARIO = sys.argv[sys.argv.index("--datos-usuario") + 1]
elif os.environ.get("EVALUADOR_DATOS_USUARIO"):
    DATOS_USUARIO = os.environ["EVALUADOR_DATOS_USUARIO"]
elif _carpeta_sincronizada(BASE):
    DATOS_USUARIO = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "EvaluadorGD")
else:
    DATOS_USUARIO = os.path.join(BASE, "datos")
os.makedirs(DATOS_USUARIO, exist_ok=True)
os.environ.setdefault("EVALUADOR_USUARIOS", os.path.join(DATOS_USUARIO, "usuarios.db"))
os.environ.setdefault("EVALUADOR_EVALUACIONES", os.path.join(DATOS_USUARIO, "evaluaciones.db"))
os.environ.setdefault("EVALUADOR_MODULOS", os.path.join(DATOS_USUARIO, "modulos.json"))
os.environ.setdefault("EVALUADOR_SOPORTE", os.path.join(DATOS_USUARIO, "soporte.db"))
os.environ.setdefault("EVALUADOR_LEADS", os.path.join(DATOS_USUARIO, "leads.db"))
os.environ.setdefault("EVALUADOR_FUENTES", os.path.join(DATOS_USUARIO, "fuentes.json"))
if "--usuarios" in sys.argv:  # base de usuarios alternativa (pruebas)
    os.environ["EVALUADOR_USUARIOS"] = sys.argv[sys.argv.index("--usuarios") + 1]
    os.environ["EVALUADOR_EVALUACIONES"] = os.environ["EVALUADOR_USUARIOS"] + ".evaluaciones.db"
    os.environ["EVALUADOR_MODULOS"] = os.environ["EVALUADOR_USUARIOS"] + ".modulos.json"
    os.environ["EVALUADOR_SOPORTE"] = os.environ["EVALUADOR_USUARIOS"] + ".soporte.db"
    os.environ["EVALUADOR_LEADS"] = os.environ["EVALUADOR_USUARIOS"] + ".leads.db"
    os.environ["EVALUADOR_FUENTES"] = os.environ["EVALUADOR_USUARIOS"] + ".fuentes.json"
    # las pruebas nunca escriben en el SharePoint real: su pipeline es una carpeta junto a la base de pruebas
    import tempfile
    os.environ["EVALUADOR_PIPELINE"] = os.path.join(tempfile.gettempdir(), "evaluador_pipeline_pruebas")
    # ni envían correos de proyecto: el resumen se arma pero no sale (ver /api/leads/notificar)
    os.environ["EVALUADOR_SIN_CORREO_PROYECTO"] = "1"
    # ni ningún otro correo (invitaciones, recuperación, prueba): quedan como .eml en %TEMP%\evaluador_correos_prueba.
    # El 3-nov una invitación de prueba salió por Gmail hacia una dirección ficticia (ticket #45).
    os.environ["EVALUADOR_SIN_CORREO"] = "1"
    # y la cuenta de envío que se configure en el panel de pruebas no toca la real (datos/config_correo.json)
    os.environ["EVALUADOR_CORREO_CONFIG"] = os.environ["EVALUADOR_USUARIOS"] + ".config_correo.json"
    os.makedirs(os.path.join(os.environ["EVALUADOR_PIPELINE"], "01 Pipeline", "E1"), exist_ok=True)
import auth  # noqa: E402
import catastro  # noqa: E402
import correo  # noqa: E402
import ctl  # noqa: E402
import entregables  # noqa: E402
import evaluaciones  # noqa: E402
import evaluador  # noqa: E402
import financiero  # noqa: E402
import fuentes  # noqa: E402
import gis  # noqa: E402
import hidrografia  # noqa: E402
import informes  # noqa: E402
import leads  # noqa: E402
import modulos  # noqa: E402
import oferta  # noqa: E402
import oferta_pptx  # noqa: E402
import red  # noqa: E402
import soporte  # noqa: E402
from ortografia import tildar  # noqa: E402

VERSION = "2.2"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TIPOS_ARCHIVO = {".docx": DOCX, ".pdf": "application/pdf",
                 ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                 ".dxf": "application/dxf", ".kmz": "application/vnd.google-earth.kmz", ".kml": "application/vnd.google-earth.kml+xml"}

PUERTO = int(sys.argv[sys.argv.index("--puerto") + 1]) if "--puerto" in sys.argv \
    else int(os.environ.get("EVALUADOR_PUERTO", "8765"))
EN_RED = "--red" in sys.argv
_cache_agua = {}


def rondas_cercanas(poligonos, ronda):
    """Cuerpos de agua a menos de la ronda del área construible (con caché: cambiar un parámetro
    del layout no vuelve a consultar la hidrografía)."""
    if ronda <= 0 or not poligonos:
        return []
    clave = (round(ronda, 1), json.dumps(poligonos, sort_keys=True))
    if clave not in _cache_agua:
        if len(_cache_agua) > 20:
            _cache_agua.clear()
        _cache_agua[clave] = hidrografia.cercanos(poligonos, ronda)
    return _cache_agua[clave]
COOKIE = "evaluador_sesion"
URL_PUBLICA = os.environ.get("EVALUADOR_URL_PUBLICA", "").rstrip("/")  # p. ej. https://evaluador.solcor.co
MAX_CUERPO = 150 * 1024 * 1024      # KMZ, CTL, fichas PDF, imágenes de mapa de los informes, adjuntos de soporte
MAX_CUERPO_PUBLICO = 64 * 1024       # ingreso y recuperación: solo correo y contraseña
PUBLICAS = {"/login", "/api/auth/estado", "/api/auth/ingresar", "/api/auth/configurar",
            "/api/auth/recuperar", "/api/auth/restablecer", "/api/auth/token"}


def url_tunel():
    """URL vigente del túnel temporal de Cloudflare (datos/logs/url_tunel.txt), o "" si no hay o en modo de pruebas."""
    if "--usuarios" in sys.argv:
        return ""
    try:
        with open(os.path.join(BASE, "datos", "logs", "url_tunel.txt"), encoding="utf-8-sig") as fh:
            u = fh.readline().strip().rstrip("/")
        return u if u.startswith("https://") else ""
    except OSError:
        return ""


def con_base(enlace, base):
    """El mismo enlace (ruta y token) sobre la dirección vigente: los túneles temporales cambian de dirección."""
    from urllib.parse import urlsplit
    p = urlsplit(enlace or "")
    return f"{base}{p.path}{'?' + p.query if p.query else ''}" if p.path else enlace


class Handler(BaseHTTPRequestHandler):
    # ------------------------------------------------------------ utilidades
    def _send(self, code, body, ctype="application/json; charset=utf-8", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _pagina(self, nombre):
        with open(os.path.join(BASE, "web", nombre), "rb") as f:
            self._send(200, f.read(), "text/html; charset=utf-8")

    def _redir(self, destino):
        self.send_response(302)
        self.send_header("Location", destino)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _token(self):
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return c[COOKIE].value if COOKIE in c else None

    def _ip(self):
        return self.client_address[0]

    def _agente(self):
        return self.headers.get("User-Agent", "")

    def _base_url(self):
        # Con la herramienta publicada, la dirección de los enlaces de correo es fija (EVALUADOR_URL_PUBLICA):
        # armarla con el encabezado Host dejaría que un tercero hiciera llegar enlaces hacia otro dominio.
        if URL_PUBLICA:
            return URL_PUBLICA
        # sin dirección fija: la del túnel vigente (la escribe herramientas/vigilar_evaluador.ps1 al abrir cada túnel),
        # para que una invitación hecha desde este PC (localhost) no lleve una dirección que el invitado no puede abrir
        tunel = url_tunel()
        if tunel:
            return tunel
        return f"http://{self.headers.get('Host', f'localhost:{PUERTO}')}"

    def _segura(self):
        """'; Secure' para la cookie cuando la conexión del usuario es HTTPS (túnel o proxy delante)."""
        https = URL_PUBLICA.startswith("https://") or self.headers.get("X-Forwarded-Proto", "").lower() == "https"
        return "; Secure" if https else ""

    def _json(self, data):
        return json.loads(data.decode("utf-8") or "{}")

    def _usuario(self, ruta):
        """Usuario de la sesion, o None tras responder 401/redirigir."""
        if ruta in PUBLICAS or ruta.startswith("/marca/"):
            return {}
        u = auth.usuario_de_sesion(self._token())
        if u:
            return u
        if ruta.startswith("/api/"):
            self._send(401, {"error": "Sesión vencida o no iniciada.", "login": True})
        else:
            self._redir("/login")
        return None

    # ------------------------------------------------------------ GET
    def do_GET(self):
        url = urlparse(self.path)
        ruta, q = url.path, parse_qs(url.query)
        u = self._usuario(ruta)
        if u is None:
            return
        carpeta = os.path.join(BASE, "ejemplos")
        try:
            if ruta == "/login":
                self._pagina("login.html")
            elif ruta in ("/", "/index.html"):
                self._pagina("index.html")
            elif ruta == "/manual":
                self._pagina("manual.html")
            elif ruta == "/admin":
                # el administrador ve todo; un usuario con permiso de bases de datos ve solo esa sección (#51)
                if u.get("rol") != "admin" and not auth.puede(u, "fuentes_ver"):
                    return self._redir("/")
                self._pagina("admin.html")
            elif ruta == "/api/auth/estado":
                self._send(200, dict(configurado=auth.hay_usuarios(), correo=correo.configurado()))
            elif ruta == "/api/auth/token":
                self._send(200, auth.info_token(q.get("t", [""])[0]) or {"invalido": True})
            elif ruta.startswith("/marca/"):
                f = os.path.join(BASE, "web", "marca", os.path.basename(unquote(ruta[len("/marca/"):])))
                tipos = {".png": "image/png", ".svg": "image/svg+xml", ".ico": "image/x-icon"}
                if os.path.isfile(f) and os.path.splitext(f)[1] in tipos:
                    with open(f, "rb") as fh:
                        self._send(200, fh.read(), tipos[os.path.splitext(f)[1]], headers={"Cache-Control": "max-age=86400"})
                else:
                    self._send(404, {"error": "no encontrado"})
            elif ruta == "/api/auth/yo":
                self._send(200, dict(u, datos=dict(gis="respaldo 23-sep-2026", catastro=catastro.CORTE,
                                                   hidrografia="IGAC 1:100.000"), en_red=EN_RED, version=VERSION))
            elif ruta == "/api/modulos":
                self._send(200, modulos.catalogo())
            elif ruta == "/api/financiero/supuestos":  # valores por defecto del modelo (vectores PPA, IPC, IBR, TRM…)
                self._send(200, financiero.SUPUESTOS_DEFECTO)
            elif ruta == "/api/trm":  # TRM vigente certificada por la Superfinanciera (la que publica el Banco de la República)
                self._send(200, financiero.trm_vigente())
            elif ruta == "/api/pipeline/puntos":  # capa «Pipeline»: leads, proyectos y evaluaciones en el mapa
                if auth.solo_propios(u):
                    self._send(200, leads.puntos_pipeline(evaluaciones.puntos(u["id"]), u))
                else:
                    self._send(200, leads.puntos_pipeline(evaluaciones.puntos()))
            elif ruta == "/api/usuarios":  # para elegir el responsable de un lead: nombre y correo de los usuarios activos
                self._send(200, [dict(nombre=x["nombre"], correo=x["correo"]) for x in auth.listar_usuarios() if x["activo"]])
            elif ruta == "/api/catastro/buscar":
                self._send(200, catastro.buscar_predios(q.get("q", [""])[0][:120]))
            elif ruta == "/api/catastro/vereda":
                self._send(200, catastro.vereda(q.get("codigo", [""])[0]))
            elif ruta == "/api/catastro/ficha":
                self._send(200, catastro.ficha_predio(q.get("codigo", [""])[0]))
            elif ruta == "/api/admin/usuarios":
                if self._solo_admin(u):
                    us, base = auth.listar_usuarios(), self._base_url()
                    for x in us:  # enlaces pendientes con la dirección vigente (el túnel pudo cambiar desde que se crearon)
                        if x.get("enlace_pendiente"):
                            x["enlace_pendiente"]["enlace"] = con_base(x["enlace_pendiente"]["enlace"], base)
                    self._send(200, dict(usuarios=us, correo=correo.configurado(), correo_estado=correo.estado(),
                                         url_base=base, url_fija=bool(URL_PUBLICA)))
            elif ruta == "/api/admin/accesos":
                if self._solo_admin(u):
                    g = lambda k: (q.get(k) or [None])[0]  # noqa: E731
                    self._send(200, auth.accesos(g("correo"), g("desde"), g("hasta"), g("evento")))
            elif ruta == "/api/admin/fuentes":  # bases de datos de la herramienta: enlace, última descarga, actualización
                if auth.puede(u, "fuentes_ver"):
                    self._send(200, dict(fuentes.listar(), puede_actualizar=auth.puede(u, "fuentes_actualizar"),
                                         puede_editar=u.get("rol") == "admin"))
                else:
                    self._send(403, {"error": "No tienes permiso para ver las bases de datos de la herramienta."})
            elif ruta == "/api/admin/actividad":  # métricas por usuario y quién está en línea (#51)
                if self._solo_admin(u):
                    ev = evaluaciones.conteo_por_usuario()
                    creados, resp = leads.conteo_por_usuario()
                    filas = []
                    for x in auth.listar_usuarios():
                        n_ev, ult_ev = ev.get(x["id"], (0, None))
                        c = creados.get(x["id"], {})
                        filas.append(dict(id=x["id"], nombre=x["nombre"], correo=x["correo"], activo=x["activo"],
                                          evaluaciones=n_ev, ultima_evaluacion=ult_ev, leads=c.get("leads", 0),
                                          proyectos=c.get("proyectos", 0), responsable=resp.get(x["correo"].lower(), 0),
                                          ultimo_ingreso=x["ultimo_ingreso"]))
                    self._send(200, dict(usuarios=filas, en_linea=auth.en_linea(), minutos=auth.EN_LINEA_MIN))
            elif ruta == "/api/gis/catalogo":
                self._send(200, gis.catalogo())
            elif ruta == "/api/gis/features":
                self._send(200, gis.features(q["ds"][0], [float(v) for v in q["bbox"][0].split(",")]))
            elif ruta == "/api/gis/pvout":
                self._send(200, gis.pvout_mapa()[1])
            elif ruta == "/api/gis/pvout.png":
                png = gis.pvout_mapa()[0]
                if png:
                    self._send(200, png, "image/png", headers={"Cache-Control": "max-age=86400"})
                else:
                    self._send(404, {"error": "no encontrado"})
            elif ruta == "/api/red/arbol":
                self._send(200, red.arbol())
            elif ruta == "/api/catastro/catalogo":
                self._send(200, catastro.catalogo())
            elif ruta == "/api/catastro/features":
                self._send(200, catastro.features(q["capa"][0], [float(v) for v in q["bbox"][0].split(",")]))
            elif ruta == "/api/evaluaciones":
                lista = evaluaciones.listar(u, int(q["lead"][0]) if q.get("lead") else None)
                if auth.solo_propios(u):
                    suyos = self._leads_suyos(u)
                    lista = [e for e in lista if e["mia"] or e["lead_id"] in suyos]
                self._send(200, lista)
            elif ruta == "/api/evaluaciones/una":
                ev = evaluaciones.obtener(u, int(q["id"][0]))
                if auth.solo_propios(u) and not ev["mia"] and ev["lead_id"] not in self._leads_suyos(u):
                    raise PermissionError("Esta evaluación es de otra persona y tu acceso es solo a tus proyectos.")
                self._send(200, ev)
            elif ruta == "/api/leads":
                lista = leads.listar((q.get("estado") or [None])[0])
                if auth.solo_propios(u):
                    lista = [x for x in lista if leads.es_suyo(u, x)]
                self._send(200, dict(leads=lista, pipeline=leads.estado_pipeline(),
                                     estados=leads.ESTADOS, canales=leads.CANALES, alcance=u.get("alcance", "todos")))
            elif ruta == "/api/soporte":
                g = lambda k: (q.get(k) or [None])[0]  # noqa: E731
                if g("vistos"):
                    soporte.marcar_vistos(u)
                tickets = soporte.listar(u, todos=g("todos") == "1", estado=g("estado"))
                if g("todos") == "1":  # el administrador abrió la bandeja: los comentarios de los usuarios quedan leídos
                    soporte.marcar_vistos(u, todos=True)
                self._send(200, dict(tickets=tickets, resumen=soporte.resumen(u), tipos=soporte.TIPOS, estados=soporte.ESTADOS))
            elif ruta == "/api/soporte/resumen":
                self._send(200, soporte.resumen(u))
            elif ruta == "/api/soporte/adjunto":
                img, mime, nombre = soporte.adjunto(u, int(q["id"][0]))
                if mime.startswith("image/"):
                    self._send(200, img, mime, headers={"Content-Disposition": "inline"})
                else:  # cualquier otro archivo se entrega como descarga, nunca se abre dentro de la página
                    self._archivo(img, nombre, mime)
            elif ruta == "/api/red/cercano":
                self._send(200, red.cercano(float(q["lon"][0]), float(q["lat"][0])))
            elif ruta == "/api/lugares":
                self._send(200, gis.buscar_lugares(q.get("q", [""])[0]))
            elif ruta == "/api/lugares/geom":
                self._send(200, gis.geometria_lugar(q.get("codigo", [""])[0]) or {})
            elif ruta == "/api/hidro/catalogo":
                self._send(200, hidrografia.catalogo())
            elif ruta == "/api/hidro/features":
                self._send(200, hidrografia.features(q["capa"][0], [float(v) for v in q["bbox"][0].split(",")]))
            elif ruta == "/api/catastro/punto":
                self._send(200, catastro.predio_en_punto(float(q["lon"][0]), float(q["lat"][0])))
            elif ruta == "/api/ejemplos":
                nombres = sorted(n for n in os.listdir(carpeta) if n.lower().endswith((".kmz", ".kml"))) \
                    if os.path.isdir(carpeta) else []
                self._send(200, nombres)
            elif ruta.startswith("/ejemplos/"):
                ruta_f = os.path.join(carpeta, os.path.basename(unquote(ruta[len("/ejemplos/"):])))
                if os.path.isfile(ruta_f):
                    with open(ruta_f, "rb") as f:
                        self._send(200, f.read(), "application/octet-stream")
                else:
                    self._send(404, {"error": "no encontrado"})
            else:
                self._send(404, {"error": "no encontrado"})
        except Exception as e:
            traceback.print_exc()
            self._send(400, {"error": f"{type(e).__name__}: {e}"})

    def _leads_suyos(self, u):
        return {x["id"] for x in leads.listar() if leads.es_suyo(u, x)}

    def _lead_permitido(self, u, lid):
        """Con alcance «solo los suyos», un usuario no puede tocar leads de otros (#51)."""
        if lid and auth.solo_propios(u) and not leads.es_suyo(u, leads.obtener(int(lid))):
            raise PermissionError("Este lead es de otra persona y tu acceso es solo a tus leads y proyectos.")

    def _solo_admin(self, u):
        if u.get("rol") == "admin":
            return True
        self._send(403, {"error": "Solo el administrador puede ver esto."})
        return False

    # ------------------------------------------------------------ POST
    def do_POST(self):
        ruta = urlparse(self.path).path
        u = self._usuario(ruta)
        if u is None:
            return
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            n = -1
        # tope del cuerpo: evita que una petición enorme (o sin sesión) agote la memoria del servidor
        if n < 0 or n > (MAX_CUERPO_PUBLICO if ruta in PUBLICAS else MAX_CUERPO):
            self.close_connection = True
            return self._send(413, {"error": "La petición es demasiado grande."})
        data = self.rfile.read(n)
        ip, ag = self._ip(), self._agente()
        try:
            # alcance «solo los suyos» (#51): las acciones sobre un lead, sus informes y sus evaluaciones solo con sus leads
            if auth.solo_propios(u) and (ruta.startswith(("/api/leads/", "/api/informe/")) or ruta == "/api/evaluaciones/guardar"):
                try:
                    req0 = self._json(data)
                except ValueError:
                    req0 = {}
                self._lead_permitido(u, req0.get("id") if ruta.startswith("/api/leads/") else req0.get("lead_id"))
            # ---- acceso
            if ruta == "/api/auth/configurar":
                req = self._json(data)
                auth.crear_primer_admin(req.get("correo"), req.get("nombre", ""), req.get("clave"), ip, ag)
                self._login(req.get("correo"), req.get("clave"))
            elif ruta == "/api/auth/ingresar":
                req = self._json(data)
                self._login(req.get("correo"), req.get("clave"))
            elif ruta == "/api/auth/salir":
                auth.cerrar_sesion(self._token(), ip, ag)
                self._send(200, {"ok": True}, headers={"Set-Cookie": f"{COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax{self._segura()}"})
            elif ruta == "/api/auth/recuperar":
                req = self._json(data)
                # máximo 3 solicitudes por correo cada hora: evita usar la recuperación para llenar un buzón
                frenado = auth.recuperaciones_recientes(req.get("correo")) >= 3
                enlace, usr = (None, None) if frenado else auth.crear_token(req.get("correo"), "recuperacion", self._base_url())
                auth.registrar(req.get("correo"), "recuperacion_solicitada", ip, ag,
                               "límite de solicitudes por hora" if frenado else "" if usr else "correo no registrado")
                if usr:
                    self._enviar_enlace(usr, enlace, "recuperacion")
                # misma respuesta exista o no el correo (no revela quién está registrado)
                self._send(200, {"ok": True, "mensaje": "Si el correo está registrado, te enviamos un enlace para "
                                 "restablecer la contraseña. Si no llega, pídeselo al administrador."})
            elif ruta == "/api/auth/restablecer":
                req = self._json(data)
                c = auth.restablecer(req.get("token"), req.get("clave"), ip, ag)
                self._login(c, req.get("clave"))
            # ---- administración
            elif ruta == "/api/admin/usuarios":
                if self._solo_admin(u):
                    req = self._json(data)
                    auth.crear_usuario(req.get("correo"), req.get("nombre"), req.get("rol", "usuario"), u["correo"], ip, ag)
                    enlace, usr = auth.crear_token(req.get("correo"), "invitacion", self._base_url())
                    enviado = self._enviar_enlace(usr, enlace, "invitacion")
                    self._send(200, {"ok": True, "enviado": enviado, "enlace": None if enviado else enlace})
            elif ruta == "/api/admin/correo_prueba":
                if self._solo_admin(u):
                    try:
                        ok = correo.enviar(u["correo"], "Prueba de correo · Evaluador GD",
                                           f"Hola {u['nombre']},\n\nEl envío de correos del Evaluador GD funciona.\n")
                        auth.registrar(u["correo"], "correo_prueba", ip, ag, "enviado" if ok else "correo no configurado")
                        self._send(200, {"ok": ok, "mensaje": f"Correo de prueba enviado a {u['correo']}." if ok else
                                         "El correo no está configurado todavía (faltan datos en datos/config_correo.json o el secreto en la variable de entorno)."})
                    except Exception as e:
                        auth.registrar(u["correo"], "correo_fallido", ip, ag, f"{type(e).__name__}: {e}"[:200])
                        self._send(200, {"ok": False, "mensaje": f"El servidor de correo rechazó el envío: {type(e).__name__}: {e}"})
            elif ruta == "/api/admin/correo_config":  # cuenta de envío de las notificaciones
                if self._solo_admin(u):
                    req = self._json(data)
                    try:
                        est = correo.guardar(req, u["correo"])
                    except ValueError as e:
                        self._send(400, {"error": str(e)})
                    else:
                        auth.registrar(u["correo"], "correo_configurado", ip, ag,
                                       f"{est['metodo']}: {est['usuario']} como {est['remitente']}"[:200])
                        self._send(200, {"ok": True, "correo": correo.configurado(), "correo_estado": est})
            elif ruta == "/api/admin/usuario":
                if self._solo_admin(u):
                    req = self._json(data)
                    if req.get("accion") == "enlace":
                        c = next(x["correo"] for x in auth.listar_usuarios() if x["id"] == req["id"])
                        enlace, usr = auth.crear_token(c, "recuperacion", self._base_url())
                        enviado = self._enviar_enlace(usr, enlace, "recuperacion")
                        auth.registrar(c, "enlace_generado_admin", ip, ag, f"por {u['correo']}")
                        self._send(200, {"ok": True, "enviado": enviado, "enlace": None if enviado else enlace})
                    else:
                        auth.actualizar_usuario(req["id"], req.get("cambios", {}), u["correo"], ip, ag)
                        self._send(200, {"ok": True})
            elif ruta == "/api/admin/fuentes" and self._json(data).get("accion") == "actualizar":
                # actualizar una base: administrador o usuario con ese permiso (#51)
                if not auth.puede(u, "fuentes_actualizar"):
                    raise PermissionError("No tienes permiso para actualizar las bases de datos; pídeselo al administrador.")
                self._send(200, {"ok": True, "tarea": fuentes.actualizar(self._json(data).get("id"), u["correo"])})
            elif ruta == "/api/admin/fuentes":
                if self._solo_admin(u):
                    req = self._json(data)
                    acc = req.get("accion")
                    if acc == "actualizar":
                        self._send(200, {"ok": True, "tarea": fuentes.actualizar(req.get("id"), u["correo"])})
                    elif acc == "marcar":
                        fuentes.marcar_actualizada(req.get("id"), u["correo"])
                        self._send(200, {"ok": True})
                    elif acc == "eliminar":
                        fuentes.eliminar(req.get("id"))
                        self._send(200, {"ok": True})
                    else:  # guardar: editar enlace y notas, o ingresar una base nueva
                        fuentes.guardar(req, u["correo"])
                        self._send(200, {"ok": True})
            # ---- evaluador
            elif ruta == "/api/cargar":
                res = evaluador.cargar(data)
                res["fin_defecto"] = financiero.SUPUESTOS_DEFECTO
                self._send(200, res)
            elif ruta == "/api/capas_dibujadas":
                res = evaluador.desde_poligonos(self._json(data).get("poligonos", []))
                res["fin_defecto"] = financiero.SUPUESTOS_DEFECTO
                self._send(200, res)
            elif ruta == "/api/evaluar":
                req = self._json(data)
                params = req.get("params") or {}
                ronda = params.get("ronda_hidrica_m")
                ronda = evaluador.PARAMS_DEFECTO["ronda_hidrica_m"] if ronda is None else float(ronda)
                agua = rondas_cercanas([c["coords"] for c in req["capas"]
                                        if c.get("rol") == "construible" and c.get("tipo") == "Polygon"], ronda)
                extra = [dict(id=100000 + k, nombre=f"{h['capa']}: {h['nombre']}", carpeta="Hidrografía IGAC",
                              tipo=e["tipo"], coords=e["coords"], rol="ronda")
                         for k, (h, e) in enumerate((h, e) for h in agua for e in h["elementos"])]
                res = evaluador.evaluar(req["capas"] + extra, params)
                res["hidrografia"] = dict(ronda_m=ronda, cuerpos=agua)
                fin = dict(req.get("fin") or {})
                p = res["params"]
                fin["produccion_especifica"] = p["rendimiento_kwh_kwp"]
                fin["canon_ha_mes"] = p["tarifa_arriendo_ha_mes"]
                res["financiero"] = financiero.evaluar_gds(res["gds"], res["resumen"]["area_arriendo_ha"], fin)
                self._send(200, res)
            elif ruta == "/api/evaluaciones/guardar":
                req = self._json(data)
                eid = evaluaciones.guardar(u, req.get("nombre"), req["datos"], req.get("resumen") or {}, req.get("id"),
                                           req.get("lead_id"))
                if req.get("lead_id"):
                    leads.marcar_evaluado(int(req["lead_id"]))
                self._send(200, {"ok": True, "id": eid})
            # ---- leads y paso a proyecto
            elif ruta == "/api/leads/guardar":
                req = self._json(data)
                lid = leads.guardar(u, req.get("lead") or {}, req.get("id"))
                self._send(200, {"ok": True, "lead": leads.obtener(lid)})
            elif ruta == "/api/leads/estado":
                req = self._json(data)
                leads.cambiar_estado(int(req["id"]), req.get("estado"))
                self._send(200, {"ok": True, "lead": leads.obtener(int(req["id"]))})
            elif ruta == "/api/leads/borrar":
                leads.borrar(u, int(self._json(data)["id"]))
                self._send(200, {"ok": True})
            elif ruta == "/api/leads/crear_proyecto":
                req = self._json(data)
                lead = leads.crear_proyecto(u, int(req["id"]), req.get("nombre"))
                auth.registrar(u.get("correo"), "proyecto_creado", ip, ag, f"{lead['proyecto']} · {lead['carpeta']}")
                self._send(200, {"ok": True, "lead": lead})
            elif ruta == "/api/leads/notificar":  # correo con el resumen del proyecto al administrador y al responsable
                req = self._json(data)
                lead = leads.obtener(int(req["id"]))
                if lead["estado"] != "e1":
                    raise ValueError("El lead aún no es un proyecto en E1.")
                activos = [x for x in auth.listar_usuarios() if x["activo"]]
                # administradores, el responsable del lead y quienes tengan marcado «correo de proyectos» (ticket #43)
                para = sorted({x["correo"] for x in activos if x["rol"] == "admin" or x.get("correo_proyectos")} |
                              ({lead["responsable"]} if lead["responsable"] in {x["correo"] for x in activos} else set()))
                resp = next((x for x in activos if x["correo"] == lead["responsable"]), None)
                vista = dict(lead, responsable=f"{resp['nombre']} ({resp['correo']})" if resp else lead["responsable"])
                imagenes = {}
                with open(os.path.join(BASE, "web", "marca", "solcor_documentos.png"), "rb") as fh:  # fondo blanco (#46)
                    imagenes["logo"] = (fh.read(), "png")
                lay = req.get("layout") or ""
                if lay.startswith("data:image/"):
                    import base64
                    imagenes["layout"] = (base64.b64decode(lay.split(",", 1)[1]), "png" if lay.startswith("data:image/png") else "jpeg")
                asunto, texto, html = leads.resumen_correo(vista, req.get("evaluacion"), req.get("motivo") or "e1", "layout" in imagenes)
                if os.environ.get("EVALUADOR_SIN_CORREO_PROYECTO"):
                    # pruebas: el correo no sale; queda como .eml para revisarlo
                    import tempfile
                    eml = os.path.join(tempfile.gettempdir(), "evaluador_correos_prueba", f"proyecto_{lead['id']}.eml")
                    os.makedirs(os.path.dirname(eml), exist_ok=True)
                    with open(eml, "wb") as fh:
                        fh.write(bytes(correo.armar(", ".join(para), asunto, texto, html, imagenes, "Evaluador GD <pruebas@evaluador.test>")))
                    return self._send(200, {"ok": True, "enviado": False, "para": para, "asunto": asunto, "texto": texto, "eml": eml,
                                            "motivo": "modo de pruebas: el correo no se envía"})
                try:
                    enviado = correo.enviar(", ".join(para), asunto, texto, html, imagenes) if para else False
                except Exception as e:  # el proyecto ya quedó creado: el fallo del correo solo se informa
                    return self._send(200, {"ok": True, "enviado": False, "motivo": f"el servidor de correo respondió: {str(e)[:120]}"})
                auth.registrar(u.get("correo"), "correo_proyecto", ip, ag, f"{lead['proyecto']} → {', '.join(para)}" if enviado else "no enviado")
                self._send(200, {"ok": True, "enviado": enviado, "para": para,
                                 "motivo": "" if enviado else "el correo del evaluador no está configurado"})
            elif ruta == "/api/leads/entregable":  # archivos que arma el navegador (KML del layout)
                req = self._json(data)
                import base64
                tipo = req.get("tipo")
                if tipo not in ("kml", "kmz"):
                    raise ValueError("Este entregable se genera desde su botón de informe.")
                contenido = base64.b64decode(req.get("b64") or "")  # siempre llega el KML; el KMZ se empaqueta aquí
                if tipo == "kmz":
                    contenido = entregables.kmz(contenido.decode("utf-8"))
                rel = leads.guardar_entregable(u, int(req["id"]), tipo, contenido, "." + tipo)
                self._send(200, {"ok": True, "guardado": rel})
            elif ruta == "/api/evaluaciones/borrar":
                evaluaciones.borrar(u, int(self._json(data)["id"]))
                self._send(200, {"ok": True})
            # ---- soporte técnico
            elif ruta == "/api/soporte/crear":
                req = self._json(data)
                contexto = dict(req.get("contexto") or {}, version=VERSION, navegador=ag[:200])
                tid = soporte.crear(u, req.get("tipo"), req.get("titulo"), req.get("descripcion"), contexto, req.get("imagenes"))
                auth.registrar(u.get("correo"), "ticket_soporte", ip, ag, f"#{tid} {req.get('tipo')}")
                self._send(200, {"ok": True, "id": tid})
            elif ruta == "/api/soporte/responder":
                req = self._json(data)
                soporte.responder(int(req["id"]), req.get("estado"), req.get("respuesta"), u)
                self._send(200, {"ok": True})
            elif ruta == "/api/soporte/comentar":
                req = self._json(data)
                soporte.comentar(u, int(req["id"]), req.get("texto"))
                self._send(200, {"ok": True})
            elif ruta == "/api/gis/analizar":
                self._send(200, gis.analizar(self._json(data)["poligonos"]))
            elif ruta == "/api/red/features":
                req = self._json(data)
                self._send(200, red.features(req.get("claves"), req["bbox"]))
            elif ruta == "/api/catastro/predios":
                self._send(200, catastro.predios_en(self._json(data)["poligonos"]))
            # ---- catálogo de módulos (fichas técnicas)
            elif ruta == "/api/modulos/ficha":
                self._send(200, modulos.extraer_ficha(data, unquote(self.headers.get("X-Nombre", ""))))
            elif ruta == "/api/modulos/guardar":
                req = self._json(data)
                pdf = None
                if req.get("pdf_b64"):
                    import base64
                    pdf = base64.b64decode(req["pdf_b64"])
                m = modulos.guardar(req["modulo"], u.get("correo", ""), pdf, req.get("archivo", ""))
                self._send(200, {"ok": True, "modulo": m, "catalogo": modulos.catalogo()})
            elif ruta == "/api/modulos/borrar":
                modulos.borrar(self._json(data)["id"], u)
                self._send(200, {"ok": True, "catalogo": modulos.catalogo()})
            # ---- CTL, estudio de títulos y reporte predial
            elif ruta == "/api/ctl/leer":
                qs = parse_qs(urlparse(self.path).query)
                prop = dict(nombre=(qs.get("nombre") or [""])[0], cedula=(qs.get("cedula") or [""])[0])
                r = ctl.leer_pdf(data, prop if (prop["nombre"] or prop["cedula"]) else None)
                if r.get("ok"):
                    r = ctl.a_json(r)
                    r["georef"] = catastro.ficha_predio(r.get("codigo_catastral") or r.get("codigo_catastral_ant"))
                    if not r["georef"].get("encontrado") and r.get("codigo_catastral_ant"):
                        alt = catastro.ficha_predio(r["codigo_catastral_ant"])
                        if alt.get("encontrado"):
                            r["georef"] = alt
                self._send(200, r)
            elif ruta == "/api/informe/titulos":
                req = self._json(data)
                c = req["ctl"]
                doc = informes.estudio_titulos(c, georef=req.get("georef") or c.get("georef"), mapa_png=req.get("mapa"),
                                               elaboro=req.get("elaboro", ""), aprobo=req.get("aprobo", ""),
                                               propietario=req.get("propietario"))
                nombre = f"{informes.dt.date.today():%Y%m%d} Estudio de Títulos Preliminar {c.get('matricula', '')}.docx"
                self._entregar(u, req, "titulos", doc, nombre)
                auth.registrar(u.get("correo"), "informe_titulos", ip, ag, c.get("matricula", ""))
            elif ruta == "/api/informe/predial":
                req = self._json(data)
                self._entregar(u, req, "predial", *self._reporte_predial(req))
                auth.registrar(u.get("correo"), "informe_predial", ip, ag, req.get("proyecto", ""))
            elif ruta == "/api/informe/ficha":
                req = self._json(data)
                poligonos = [p for p in req.get("poligonos") or [] if len(p) >= 3]
                if not poligonos or not (req.get("resultado") or {}).get("resumen"):
                    raise ValueError("Primero evalúa el predio.")
                gisr = gis.analizar(poligonos)
                centro = gisr.get("centro")
                datos = dict(req, gis=gisr, centro=centro, red=red.cercano(centro[0], centro[1]) if centro else [])
                proyecto = tildar(req.get("proyecto") or "GD")
                self._entregar(u, req, "ficha", informes.ficha_tecnica(datos),
                               f"{informes.dt.date.today():%Y%m%d} Ficha Técnica {proyecto}.docx")
                auth.registrar(u.get("correo"), "informe_ficha", ip, ag, req.get("proyecto", ""))
            elif ruta == "/api/informe/crm":
                req = self._json(data)
                lead = leads.obtener(int(req["lead_id"]))
                poligonos = [p for p in req.get("poligonos") or [] if len(p) >= 3]
                gisr = gis.analizar(poligonos) if poligonos else {}
                centro = gisr.get("centro")
                doc = entregables.datos_crm(lead, req.get("resultado"), gisr, red.cercano(centro[0], centro[1]) if centro else [],
                                            f"{informes.dt.date.today():%d/%m/%Y}")
                self._entregar(u, req, "crm", doc, f"{informes.dt.date.today():%Y%m%d} Datos CRM {lead['proyecto']}.xlsx")
            elif ruta == "/api/informe/oferta":  # oferta económica para el propietario (PDF)
                req = self._json(data)
                s = (req.get("resultado") or {}).get("resumen") or {}
                p = (req.get("resultado") or {}).get("params") or {}
                if not s.get("dc_kwp"):
                    raise ValueError("Primero evalúa el predio.")
                o = req.get("oferta") or {}
                proyecto = tildar(req.get("proyecto") or "GD")
                # el entregable es la presentación con la plantilla de las propuestas de Solcor; el PDF propio queda como alterno
                generar, ext = (oferta.generar, "pdf") if req.get("formato") == "pdf" else (oferta_pptx.generar, "pptx")
                doc = generar(dict(mapa_portada=req.get("mapa_portada"), mapa_planta=req.get("mapa_planta"),
                    proyecto=proyecto, propietario=tildar(req.get("propietario") or ""), depto=req.get("depto"), municipio=req.get("municipio"),
                    vereda=tildar(req.get("vereda") or ""), kwp=s["dc_kwp"], kwac=s.get("ac_kw_total"), mwh=s.get("energia_mwh_anio"),
                    canon_mes=s.get("arriendo_mes"), prima=o.get("prima"), meses_desarrollo=o.get("meses_desarrollo"),
                    meses_construccion=o.get("meses_construccion"), anios_operacion=p.get("plazo_anios"),
                    ipc=float(o["ipc_pct"]) / 100 if o.get("ipc_pct") not in (None, "") else None,
                    area_m2=(s.get("area_arriendo_ha") or 0) * 10000, area_predio_m2=(s.get("area_total_ha") or 0) * 10000,
                    modulos=s.get("modulos"), inclinacion=p.get("inclinacion"), n_gd=s.get("n_gd"), long_mt_m=req.get("long_mt_m"),
                    perimetro_m=entregables._perimetro_m(req.get("poligonos")), mapa=req.get("mapa")))
                self._entregar(u, req, "oferta", doc, f"{informes.dt.date.today():%Y%m%d} Propuesta de Arriendo {proyecto}.{ext}")
                auth.registrar(u.get("correo"), "informe_oferta", ip, ag, req.get("proyecto", ""))
            # ---- entregables en Excel y CAD
            elif ruta in ("/api/informe/cotizador", "/api/informe/financiero", "/api/informe/plano"):
                req = self._json(data)
                proyecto = tildar(req.get("proyecto") or "GD")
                hoy = f"{informes.dt.date.today():%Y%m%d}"
                if ruta.endswith("cotizador"):
                    doc, notas = entregables.cotizador(req)
                    self._entregar(u, req, "cotizador", doc, f"{hoy} Cotizador {proyecto}.xlsx", notas)
                elif ruta.endswith("financiero"):
                    doc, notas = entregables.modelo_financiero(req)
                    gd = f" GD {int(req.get('gd') or 0) + 1}" if len(req.get("financiero") or []) > 1 else ""
                    self._entregar(u, req, "financiero", doc, f"{hoy} Modelo Financiero {proyecto}{gd}.xlsx", notas,
                                   f"Modelo Financiero{gd}" if gd else None)
                else:
                    self._entregar(u, req, "plano", entregables.plano_dxf(req), f"{hoy} Plano Layout {proyecto}.dxf")
                auth.registrar(u.get("correo"), "informe_" + ruta.rsplit("/", 1)[1], ip, ag, req.get("proyecto", ""))
            else:
                self._send(404, {"error": "no encontrado"})
        except (PermissionError, ValueError) as e:
            self._send(400, {"error": str(e)})
        except Exception as e:  # el error se muestra en la interfaz
            traceback.print_exc()
            # archivo:línea del código del evaluador donde ocurrió, para poder ubicarlo desde la captura del usuario
            donde = [f"{os.path.basename(f.filename)}:{f.lineno}" for f in traceback.extract_tb(e.__traceback__)
                     if os.path.dirname(os.path.abspath(__file__)) in os.path.abspath(f.filename)]
            self._send(400, {"error": f"{type(e).__name__}: {e}" + (f" (en {donde[-1]})" if donde else "")})

    def _entregar(self, u, req, tipo, datos, nombre, notas=None, descripcion=None):
        """Entrega un informe: descarga en el navegador y, si la evaluación es de un lead que ya es proyecto,
        copia en su carpeta del pipeline. Con solo_carpeta no se descarga: responde dónde quedó."""
        from urllib.parse import quote
        ext = os.path.splitext(nombre)[1].lower()
        guardado = None
        if req.get("lead_id"):
            lead = leads.obtener(int(req["lead_id"]))
            if lead["estado"] == "e1":
                guardado = leads.guardar_entregable(u, lead["id"], tipo, datos, ext, descripcion)
        if req.get("solo_carpeta"):
            if not guardado:
                raise ValueError("El lead aún no tiene carpeta de proyecto: primero pásalo a E1.")
            return self._send(200, {"ok": True, "guardado": guardado, "notas": notas or []})
        extra = {}
        if guardado:
            extra["X-Guardado-En"] = quote(guardado)
        if notas:
            extra["X-Notas"] = quote(" | ".join(notas))
        self._archivo(datos, nombre, TIPOS_ARCHIVO.get(ext, "application/octet-stream"), extra)

    def _archivo(self, datos, nombre, ctype=DOCX, extra=None):
        from urllib.parse import quote
        self._send(200, datos, ctype, headers=dict(extra or {}, **{
            "Content-Disposition": f"attachment; filename=\"{nombre.encode('ascii', 'ignore').decode()}\"; filename*=UTF-8''{quote(nombre)}"}))

    def _reporte_predial(self, req):
        """Arma los datos del reporte en el servidor (catastro, GIS, hidrografía) y genera el Word."""
        poligonos = [p for p in req.get("poligonos") or [] if len(p) >= 3]
        predio = req.get("predio_coords")
        base = [predio] if predio and len(predio) >= 3 else poligonos
        if not base:
            raise ValueError("Primero define el predio (KMZ, dibujo o predio catastral) y evalúalo.")
        cod = req.get("codigo_predial") or (req.get("ctl") or {}).get("codigo_catastral")
        cat = catastro.ficha_predio(cod) if cod else {}
        if not cat.get("encontrado"):
            pts = [pt for poly in base for pt in poly]
            lon, lat = sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
            pp = catastro.predio_en_punto(lon, lat)
            if pp.get("encontrado"):
                cat = catastro.ficha_predio(pp["numero_predial"])
        gisr = gis.analizar(base)
        hid = hidrografia.cercanos(base, 300)
        for h in hid:
            h.pop("elementos", None)
        datos = dict(req)
        datos.update(catastro=cat or {}, gis=gisr, hidro=hid, centro=gisr.get("centro"),
                     origen_geometria=req.get("origen_geometria") or "el polígono definido en el evaluador GD")
        doc = informes.reporte_predial(datos)
        nombre = f"{informes.dt.date.today():%Y%m%d} Reporte Caracterización Predial {tildar(req.get('proyecto') or 'GD')}.docx"
        return doc, nombre

    def _login(self, correo_u, clave):
        token, seg = auth.ingresar(correo_u, clave, self._ip(), self._agente())
        self._send(200, {"ok": True}, headers={
            "Set-Cookie": f"{COOKIE}={token}; Path=/; Max-Age={seg}; HttpOnly; SameSite=Lax{self._segura()}"})

    def _enviar_enlace(self, usr, enlace, tipo):
        if not usr:
            return False
        asunto, texto, html = correo.mensaje_enlace(usr["nombre"], enlace, tipo)
        try:
            ok = correo.enviar(usr["correo"], asunto, texto, html)
        except Exception as e:  # servidor SMTP caido o credenciales malas: el admin ve el enlace
            auth.registrar(usr["correo"], "correo_fallido", detalle=f"{type(e).__name__}: {e}"[:200])
            ok = False
        auth.registrar(usr["correo"], f"{tipo}_enviada" if ok else f"{tipo}_pendiente_envio",
                       detalle="" if ok else "correo no configurado: enlace visible para el administrador")
        if not ok:
            print(f"[correo no enviado] enlace de {tipo} para {usr['correo']}: {enlace}", flush=True)
        return ok

    def log_message(self, fmt, *args):
        pass


def respaldar_bases(dias=14):
    """Copia diaria de las bases que la herramienta escribe (usuarios, evaluaciones, soporte, módulos) a
    <datos>/respaldos/AAAAMMDD/. Se hace al iniciar, una vez por día, y se conservan los últimos `dias`."""
    import datetime
    import shutil
    import sqlite3
    # junto a la base de usuarios: con --usuarios (pruebas) el respaldo no se mezcla con el de los datos reales
    destino = os.path.join(os.path.dirname(os.path.abspath(os.environ["EVALUADOR_USUARIOS"])), "respaldos",
                           datetime.date.today().strftime("%Y%m%d"))
    if os.path.isdir(destino):
        return
    try:
        os.makedirs(destino)
        for clave in ("EVALUADOR_USUARIOS", "EVALUADOR_EVALUACIONES", "EVALUADOR_SOPORTE", "EVALUADOR_LEADS", "EVALUADOR_MODULOS",
                      "EVALUADOR_FUENTES"):
            origen = os.environ.get(clave, "")
            if not os.path.isfile(origen):
                continue
            copia = os.path.join(destino, os.path.basename(origen))
            if origen.endswith(".db"):  # copia consistente aunque haya escrituras en curso
                with sqlite3.connect(origen) as a, sqlite3.connect(copia) as b:
                    a.backup(b)
            else:
                shutil.copy2(origen, copia)
        raiz = os.path.dirname(destino)
        for viejo in sorted(d for d in os.listdir(raiz) if d.isdigit() and len(d) == 8)[:-dias]:
            shutil.rmtree(os.path.join(raiz, viejo), ignore_errors=True)
        print(f"Respaldo diario de las bases: {destino}")
    except Exception as e:  # el respaldo nunca debe impedir que la herramienta abra
        print(f"Aviso: no se pudo hacer el respaldo diario ({type(e).__name__}: {e})")


def main():
    puerto = PUERTO
    for intento in range(10):  # si el puerto está ocupado (otra copia abierta), probar el siguiente
        try:
            srv = ThreadingHTTPServer(("0.0.0.0" if EN_RED else "127.0.0.1", puerto), Handler)
            break
        except OSError:
            if "--puerto" in sys.argv or intento == 9:
                raise
            puerto += 1
    url = f"http://localhost:{puerto}"
    print(f"Evaluador GD v{VERSION} corriendo en {url}{'  (también en la red local)' if EN_RED else ''}  (Ctrl+C para cerrar)")
    print(f"Datos de usuarios y evaluaciones: {DATOS_USUARIO}")
    respaldar_bases()
    if catastro.disponible():
        catastro.estado_indice_codigos()  # si falta el índice por número predial, se arma en segundo plano
    if not correo.configurado():
        print("Aviso: correo no configurado (datos/config_correo.json + EVALUADOR_GRAPH_SECRETO o EVALUADOR_SMTP_CLAVE); "
              "los enlaces de acceso se verán en el panel de administración.")
    if "--sin-navegador" not in sys.argv:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
