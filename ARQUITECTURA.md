# Evaluador de predios GD — Arquitectura

Última actualización: 15-nov-2026 (estado del código al 9-nov-2026, versión 2.2).
Autor: Manuel Antonio Bayona Cely (ver `LICENCIA.md`).

## 1. Cómo funciona en una frase
Un **servidor en Python** (solo librería estándar, sin `pip`) lee los datos de `datos/` y responde a una
**página web** (`web/index.html`) que se abre en el navegador del PC o del celular. Todo el cálculo y
todas las bases quedan en el servidor; internet se usa para las imágenes satelitales, la librería del
mapa (Leaflet), la TRM del día y, si se activa, el túnel HTTPS para entrar desde fuera.

## 2. Flujo de información

```
  KMZ/KML del predio,  ┌──────────────── NAVEGADOR: web/index.html (PC o celular) ─────────────────┐
  CTL, foto con GPS ──►│ Paso 1 Predio · Paso 2 Diseño · Paso 3 Negocio · Paso 4 Entregables      │
                       │ Leads · Soporte · Buscador · Capas · Dibujo/medición · Guía (/manual)    │
                       └────┬──────────────┬───────────────┬────────────────┬────────────────┬────┘
                  /api/cargar   /api/gis/* /api/red/*   /api/catastro/*  /api/informe/*   /api/leads/*
                  /api/evaluar  /api/hidro/*            /api/ctl/leer    /api/financiero  /api/soporte/*
                       ┌────▼──────────────▼───────────────▼────────────────▼────────────────▼────┐
                       │          servidor.py  (http://127.0.0.1:8765, sesión obligatoria)        │
                       └─┬─────────┬─────────┬─────────┬──────────┬──────────┬─────────┬─────────┬┘
                   evaluador  financiero    gis      red     catastro   informes   leads    auth
                   layout_    supuestos   hidro-  red_compl.  catastro_  entregables evalua-  soporte
                   engine     _solcor     grafia  red_dupl.   acc        oferta(_pptx) ciones  fuentes
                       │                    │        │          │        docx·xlsx·pdf  │       correo
                       ▼                    ▼        ▼          ▼              │        ▼
                 datos/gis/GIS_SOLCOR   capas_complementarias  datos/igac   datos/plantillas  usuarios.db
                 autoridades_ambientales  red_or/…   catastro_*.sqlite      (Excel, PPTX)    leads.db …
                                                                               │
                                          Pipeline SharePoint (OneDrive) ◄─────┘ carpeta GD NNNN + entregables
```

### Qué pasa cuando cargas un KMZ
1. **`/api/cargar`** → `evaluador.cargar()` lee el KMZ/KML (con o sin espacios de nombres), clasifica cada
   capa (construible, exclusión, mesa existente, área de arriendo, ignorar) y devuelve los parámetros.
2. En paralelo, el navegador pide:
   - **`/api/evaluar`** → `evaluador.evaluar()` arma la grilla de mesas, calcula capacidad y reparte en GD
     (filas completas por GD, topes por `max_gd`, paneles deseados y kVA del transformador del CT);
     luego `financiero.evaluar_gds()` corre el modelo Solcor por GD.
   - **`/api/gis/analizar`** → `gis.analizar()` cruza el área con las capas ambientales, sociales,
     arqueológicas, mineras/hidrocarburos y de autoridades ambientales; entrega municipio, semáforo,
     red cercana, PVOUT y pendiente.
   - **`/api/catastro/predios`** → predios IGAC o de otros gestores (ACC, Antioquia, AMCO, Armenia).
3. Cada cambio de parámetro vuelve a llamar solo a `/api/evaluar`.
4. Al mover el mapa se pide **solo lo que cae en la vista**: `/api/gis/features`, `/api/red/features`,
   `/api/catastro/features`, `/api/hidro/features`.

### Interfaz (definitiva desde el 13-oct-2026)
- Panel en **4 pasos**: Predio (KMZ, CTL, catastro) → Diseño (módulo, mesa, inversores, CT) → Negocio
  (arriendo, CAPEX/TRM, PPA, financiero) → Entregables (estado de cada uno y «Generar todos»).
  Resultados en pestañas; botón Evaluar fijo.
- **Celular:** cajón ☰, barra en dos renglones, «Estoy aquí», coordenada desde la foto (EXIF), barra de
  medición/dibujo fija al pie.
- **Dibujo del proyecto:** punto de conexión («ajustar a la red»), CT/cuarto eléctrico girable, inversores
  (≤ 4 m²), vía de acceso y vía interna («Trazar vía interna»), línea MT, edificaciones, árboles,
  servidumbres, drenajes, medidas guardadas con imán en vértices; los que excluyen pasan al layout.
- **Buscador** del encabezado: municipios/departamentos, veredas y predios (nombre o dirección, índice FTS5).
- Guía interactiva (`/?guia=1`) y manual (`/manual`).

### Acceso y usuarios
- Toda ruta exige sesión salvo `/login` y `/api/auth/*`; sin sesión la API responde 401.
- **Roles y permisos:** administrador y usuario; permisos sueltos `fuentes_ver` y `fuentes_actualizar`;
  **alcance** `todos` (por defecto) o `propios`, que filtra leads, evaluaciones y pipeline. Las evaluaciones
  se comparten con todo el equipo; editar solo el autor o el admin.
- **Invitar/recuperar:** enlaces de un solo uso (7 días / 60 min). La dirección del enlace sale de
  `EVALUADOR_URL_PUBLICA` o, si no está, de la URL vigente del túnel (`datos/logs/url_tunel.txt`).
- **Seguridad:** PBKDF2-SHA256 (240.000 iteraciones), cookie HttpOnly/SameSite=Lax (Secure con URL
  pública) de 12 h, 5 fallos en 15 min bloquean, tope de tamaño del cuerpo HTTP, límite de recuperaciones.
- **Actividad:** historial de ingresos y cambios; `sesiones.ultima_actividad` → «en línea» (5 min) y
  métricas en `/api/admin/actividad`.
- **Correo:** SMTP de Gmail (`datos/config_correo.json`, clave en la variable `EVALUADOR_SMTP_CLAVE`).

## 3. Módulos

### Núcleo de cálculo
| Archivo | Responsabilidad |
|---|---|
| `servidor.py` | Servidor HTTP y rutas `/api/*`; filtros por alcance; respaldo diario a `datos/respaldos` |
| `motor/evaluador.py` | KMZ, roles de capas, grilla de mesas, orientación V/H, capacidad, reparto en GD (`_reparto_por_filas`, `_reparto_recortando`), topes de CT (`trafo_kva_gd`), arriendo (`base_arriendo` construible/útil) |
| `motor/layout_engine.py` | Motor geométrico de la skill `layout-preliminar` |
| `motor/financiero.py` + `supuestos_solcor.json` | Réplica del modelo Solcor (31 años, deuda, Ley 1715, 4 TIR); vectores PPA/IPC/IBR, TRM, CAPEX por GD ↔ COP/kWp |
| `motor/modulos.py` | Catálogo de módulos y lectura de fichas técnicas PDF |

### Datos geográficos
| Archivo | Responsabilidad |
|---|---|
| `motor/gis.py` | GIS Solcor + bases unidas (`cc` capas complementarias, `aa` autoridades ambientales, red complementaria); `analizar()` con semáforo (hallazgos de CAR/Corpoboyacá agrupados por dataset), PVOUT y mapa de calor, pendiente, buscador de lugares, `_operador()` |
| `motor/red.py` | Red por OR → tipo → circuito → tramo; `cercano()`; colores por OR |
| `motor/red_complementaria.py` | Arma `datos/red_or/red_complementaria.db` con cualquier `datos/red_or/<or>/filas.json` |
| `motor/red_duplicados.py` | Oculta capas de red repetidas por otra más completa o reciente |
| `motor/catastro.py` | Catastro IGAC: predios, veredas, manzanas, `ficha_predio()`, `buscar_predios()` (FTS5) |
| `motor/catastro_acc.py` | Predios de otros gestores (lista `FUENTES`: ACC, Antioquia, AMCO, Armenia) |
| `motor/hidrografia.py` | Hidrografía IGAC 1:100k y rondas hídricas |
| `motor/capas_shp.py`, `motor/proyeccion.py` | Shapefiles con R-tree; CTM12 ↔ WGS84 (y MAGNA origen Bogotá) |

### Documentos y entregables
| Archivo | Responsabilidad |
|---|---|
| `motor/pdf_texto.py`, `motor/ctl.py`, `motor/ortografia.py` | Lectura de CTL de la SNR (titulares, cuotas, riesgos); restaura tildes de textos del CTL/catastro |
| `motor/informes.py` + `motor/docx.py` | Word: Estudio de Títulos, Reporte de Caracterización Predial, Ficha técnica |
| `motor/entregables.py` + `motor/xlsx.py` | Cotizador ajustado (plantilla Monsalve, solo cantidades), modelo financiero (plantilla Nemocón), resumen financiero, plano DXF CTM12, KMZ, hoja «Datos para el CRM»; logo con `xlsx.con_logo()` |
| `motor/oferta_pptx.py` | Oferta al propietario en PowerPoint sobre la plantilla real (`datos/plantillas/propuesta_arriendo.pptx`) |
| `motor/oferta.py` + `motor/pdf.py` | La misma oferta en PDF propio (solo con `formato=pdf` en el API) |

### Gestión
| Archivo | Responsabilidad |
|---|---|
| `motor/auth.py` | Usuarios, roles, permisos, alcance, sesiones, enlaces, historial, en línea |
| `motor/evaluaciones.py` | Evaluaciones guardadas y compartidas (`datos/evaluaciones.db`) |
| `motor/leads.py` | Leads → evaluación → «Crear proyecto» (carpeta `01 Pipeline/E1/GD NNNN Nombre` con subcarpetas 0–9; ID leído de las carpetas existentes) → entregables a su subcarpeta; correo de proyecto con logo, captura del layout y enlaces SharePoint |
| `motor/soporte.py` | Tickets como conversación (`datos/soporte.db`, adjuntos en `soporte_adjuntos/`) |
| `motor/fuentes.py` | Registro de las 25 bases (modo auto/Chrome/manual, comando, fecha), actualización en segundo plano desde `/admin` |
| `motor/correo.py` | SMTP; `EVALUADOR_SIN_CORREO` bloquea todo envío en pruebas (`.eml` en `%TEMP%`) |

### Páginas
`web/index.html` (aplicación), `web/login.html`, `web/admin.html` (usuarios, permisos, historial,
actividad, bases de datos), `web/manual.html`, `web/marca/` (logos; `solcor_documentos.png` con fondo
blanco para Word/Excel).

### Herramientas (`herramientas/`)
| Grupo | Archivos |
|---|---|
| Red de los OR | `descargar_red_essa.py`, `descargar_red_arcgis.py` (CENS, EPM), `descargar_red_edeq.py`, `descargar_red_eep.py`, `descargar_red_confirma.py` (Afinia), `importar_red_shp.py` (Air-e), `importar_chec.py`, `importar_ebsa.py` (EBSA/ENERCA/EMSA), `recolector_gdc.js` (se pega en la consola del visor GD-Connect tras el captcha del usuario), `semillas_ebsa.py`, `semillas_ebsa_52.py`, `mvt.py` |
| Catastro | `indexar_catastro.py`, `descargar_predios_acc.py`, `descargar_predios_antioquia.py`, `descargar_predios_amco.py`, `descargar_predios_armenia.py` |
| Capas ambientales | `descargar_capas_arcgis.py` (ICANH, ANH, MinAmbiente, ANLA), `descargar_autoridades_ambientales.py` (Corpoboyacá, CAR), `corregir_autoridades_47.py`, `extraer_hidrografia_100k.py`, `descargar_item_arcgis.py` |
| Operación | `iniciar_servidor_y_tunel.ps1 [-Tunel]`, `vigilar_evaluador.ps1`, `abrir_url_tunel.ps1`, `barrido_soporte.py`, `pruebas.py` (18 pruebas), `preparar_paquete.py`, `logo_fondo_blanco.py` |

## 4. Fuentes de datos

### Bases principales
| Dato | Origen | Ubicación |
|---|---|---|
| **GIS Solcor** (13 capas, 736.672 geometrías + PVOUT + pendiente; red ENEL y líneas de Santander) | Respaldo SharePoint `GIS_SOLCOR_Respaldo`, 23-sep-2026 | `datos/gis/GIS_SOLCOR/` |
| **Catastro IGAC** (corte 31-ago-2026) | IGAC, ítem ArcGIS `4eea60aae26f4426ba10e2f434fdbe30` | `datos/igac/catastro/CatastroPubliconAgosto2026/` |
| **Catastro ACC** (76 municipios de Cundinamarca) | API pública `backtransacts.acc.gov.co` | `datos/catastro_acc.sqlite` |
| **Catastro Antioquia** (113 municipios, rurales) | ArcGIS `geoags1.catastroantioquia-mas.com` | `datos/catastro_antioquia.sqlite` |
| **Catastro AMCO** (Pereira, Dosquebradas, La Virginia) | WFS `geo.ideamco.gov.co` | `datos/catastro_amco.sqlite` |
| **Catastro Armenia** | FeatureServer de Catastro Bogotá | `datos/catastro_armenia.sqlite` |
| **Hidrografía 1:100k** | IGAC (servicio + GDB exportada a GeoPackage) | `datos/igac/hidrografia_100k/`, `hidrografia_gdb/` |
| **Capas complementarias** (ICANH, ANH, Reserva Forestal Ley 2ª, ANLA) | Geoportales ArcGIS públicos | `datos/capas_complementarias.db` (≈ 0,5 GB) |
| **Autoridades ambientales** (CAR 813 capas, 3,9 M geometrías; Corpoboyacá 82 capas del visor) | ArcGIS Hub CAR, GeoServer Corpoboyacá | `datos/autoridades_ambientales.db` (≈ 10 GB) |
| **Plantillas** | Cotizador Monsalve, modelo financiero Nemocón, propuesta de arriendo Pantanillo 1 v3 | `datos/plantillas/` |
| **Mapas base** | Esri World Imagery / Street / Topo | en línea |

### Red eléctrica complementaria (`datos/red_or/red_complementaria.db`, 324.239 elementos)
El respaldo GIS Solcor no se modifica: `gis._db()` une esta base con una vista temporal. Se reconstruye
con `python motor/red_complementaria.py`.

| OR | Fuente y método | Contenido |
|---|---|---|
| ESSA | Geoportal CREG 174 (pg_tileserv) | 363 circuitos estimados (árbol de mínima distancia), 38.728 trafos, 74 subestaciones |
| EBSA | Visor GD-Connect `gdc.ebsa.com.co` (política + captcha del usuario, recolector en Chrome) | 159 circuitos |
| ENERCA | Visor SPARD GD-Connect | 92 circuitos |
| EMSA | Visor SPARD GD-Connect | 56 circuitos |
| CENS | ArcGIS público | 196 circuitos, 45 subestaciones, 25.668 trafos |
| EPM | ArcGIS público | 1.250 circuitos, 149 líneas de transmisión, 164 subestaciones, 160.494 trafos |
| EDEQ | Mapa de disponibilidad público | 74 circuitos estimados, 14 subestaciones, 8.710 trafos |
| CHEC | Visor CREG 174 (token tras reCAPTCHA; recolector en Chrome) | 529 circuitos, 77 líneas AT, 83 subestaciones, 22.005 trafos |
| EEP | API pública del portal de solicitudes | 52 circuitos, 6.288 trafos (Pereira y Cartago) |
| Afinia | Visores Confirma sin captcha | 634 tramos de circuito |
| Air-e | Shapefiles entregados por el usuario (ticket #28) | 579 tramos de circuito |
| Celsia | Excel público | 57.774 trafos (sin circuito) |

Reglas: en los visores con captcha el usuario entra y resuelve; el recolector trabaja dentro de su
sesión. **Nunca** sacar el token ni llamar el API desde Python saltando el captcha.

### Archivos derivados (se regeneran solos)
- `datos/gis/_conteos.json`; índices `*.rtree.sqlite`, `catastro_codigos.sqlite` y `catastro_busqueda.sqlite` del catastro.
- `datos/igac/hidrografia_100k/_progreso.json`; caché de descargas en `datos/autoridades_ambientales/` (≈ 7 GB).
- `datos/red_or/red_complementaria.db` (desde los `filas.json`).

## 5. ¿Qué usa cada ZIP?
**Ninguno de los ZIP lo usa la herramienta.** Son el empaque original de la descarga.

| ZIP | Tamaño | Carpeta que lo reemplaza | Cómo recuperarlo |
|---|---|---|---|
| `datos/gis/GIS_SOLCOR_Database.zip` | 2,0 GB | `datos/gis/GIS_SOLCOR/` | 106 partes en SharePoint (`GIS_SOLCOR_Respaldo`) |
| `datos/igac/catastro/CatastroPubliconAgosto2026.zip` | 2,5 GB | `.../CatastroPubliconAgosto2026/` | `python herramientas/descargar_item_arcgis.py 4eea60aae26f4426ba10e2f434fdbe30 datos/igac/catastro` |
| `datos/igac/hidrografia_gdb/Hidrografia.zip` | 325 MB | `Hidrografia.gdb` y `Hidrografia_completa.gpkg` | `python herramientas/descargar_item_arcgis.py 8df6da3458fc4d2e8afa31230a02a688 datos/igac/hidrografia_gdb` |

Otros redundantes: `Hidrografia.gpkg` (5 MB, exportación incompleta) y `Hidrografia.gdb` (459 MB, mismo
contenido que el GeoPackage).

## 6. Limitaciones conocidas
- **Catastro:** sin fuente oficial pública para Manizales, municipios de Masora (Montenegro, Quimbaya,
  Filandia…) y otros gestores no listados. No trae R1/R2.
- **Red:** sin fuente para Energuaviare, CEDENAR, Electrohuila; Celsia solo trafos. Varios circuitos son
  trazados estimados (ESSA, EDEQ). Los visores GD-Connect y CHEC dependen del captcha del usuario y se
  caen a veces.
- **Grilla:** estimación de terreno (±3 % contra el layout de detalle de Mavalle).
- MAGNA-SIRGAS se trata como WGS84 (< 1 m).
- **Rondas hídricas:** 30 m por defecto sobre hidrografía 1:100k (±50 m); la definitiva la fija el POMCA.
- **Modelo financiero:** replica el Excel tal cual, incluidas sus inconsistencias. CAPEX EPC no incluye RTB.
- **CTL:** sin OCR para escaneados; el estudio es borrador para abogado.
- **Reporte predial:** IDEAM (creciente súbita) sigue sin fuente; la lectura satelital la confirma quien elabora.
- **CRM:** se entrega la hoja «Datos para el CRM»; no se escribe en el libro del CRM (pendiente, con respaldo y el usuario presente).
- **Disponibilidad:** el servidor corre en el portátil del autor; si se reinicia, el vigilante lo relanza,
  pero las descargas largas no se reanudan solas.

## 7. Operación, versiones y despliegue

- **v1** (congelada 25-sep-2026): `Downloads/Personal/Evaluador GD v1`, puerto 8764; sus datos grandes son
  junctions a esta carpeta (no borrar esta sin copiarlos).
- **v2.2** (esta carpeta): puerto 8765.

### Servidor y acceso remoto (actual)
- `herramientas/iniciar_servidor_y_tunel.ps1 [-Tunel]` arranca el servidor desacoplado y, opcionalmente,
  un túnel temporal de Cloudflare (`herramientas/bin/cloudflared.exe`). La URL cambia en cada túnel.
- Tarea programada **«Evaluador GD - vigilante»** (al iniciar sesión y cada 5 min) →
  `vigilar_evaluador.ps1`: relanza el servidor y el túnel si no responden; URL vigente en
  `datos/logs/url_tunel.txt`, historial en `datos/logs/vigilante.log`.
- Para reiniciar solo el servidor: matar el proceso del puerto 8765 y relanzar el script.

### Pruebas
- `python herramientas/pruebas.py` (18 pruebas) después de cada cambio.
- Entorno de pruebas: `python servidor.py --usuarios <base en temporal> --puerto 8766`: base de usuarios
  aparte, pipeline en `%TEMP%\evaluador_pipeline_pruebas`, sin correos reales. Nunca usar
  `datos/usuarios.db` ni el SharePoint real para probar.

### Variables de entorno
`EVALUADOR_SMTP_CLAVE`, `EVALUADOR_URL_PUBLICA`, `EVALUADOR_SIN_CORREO`, `EVALUADOR_FUENTES`.

### Próximo paso de despliegue
Servidor en la nube (recomendado: VM en Azure + Cloudflare Tunnel/Access) con dirección fija; al tenerla,
definir `EVALUADOR_URL_PUBLICA` y reenviar las invitaciones pendientes. La distribución por SharePoint
(`preparar_paquete.py`) está postergada.
