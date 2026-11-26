# Plan de integración · Portal de herramientas de Desarrollo GD

25 de noviembre de 2026. Integrar el **Evaluador GD** y la **Ruta de desarrollo GD** en un solo sitio, con un
único inicio de sesión desde el que cada persona entra a la herramienta que tenga habilitada. Primero en un
servidor local y después en la nube.

## 1. Punto de partida

| | Evaluador GD | Ruta de desarrollo GD |
|---|---|---|
| Repositorio | `GDSolcor/Evaluador-GD` | `jonatan-gd/ruta-desarrollo-gd` (cuenta personal) |
| Tecnología | Python sin librerías externas, SQLite, HTML/JS | Igual: Python sin librerías externas, SQLite, HTML/JS |
| Puerto | 8765 | **8765** (chocan: hoy no pueden correr a la vez) |
| Usuarios | `usuarios.db` propia; invitación y recuperación por correo; roles admin/usuario; bitácora | Tabla `usuarios` propia; roles admin/editor/lector; sin correo |
| Cookie de sesión | `evaluador_sesion` | `sid` |
| SharePoint | **Escribe** en `01 Pipeline` (crea carpetas GD NNNN y entregables) | **Lee** `01 Pipeline` cada 30 min (`sync.py`) |
| Dónde corre | `C:\Evaluador GD` (datos fuera de OneDrive) | Dentro de la carpeta sincronizada `00 Área GD\08 App Desarrollo\ruta-rtb`, **con la base SQLite dentro de OneDrive** |
| Acceso remoto | Túnel temporal de Cloudflare (URL cambia) | Solo local |

Las dos herramientas tienen la misma tecnología y el mismo modelo de sesión, lo que hace la integración
sencilla: no hay que reescribirlas, solo ponerlas detrás de una puerta común.

## 2. Arquitectura propuesta

```
                         ┌──────────────────────────── Portal Desarrollo GD ───────────────────────────┐
  Navegador ──https──►   │  /            inicio: tarjetas de las herramientas habilitadas              │
  (oficina, casa,        │  /login       un solo inicio de sesión (local: correo+contraseña;           │
   celular)              │               nube: cuenta Microsoft 365 de Solcor)                         │
                         │  /admin       usuarios y permisos por herramienta                           │
                         │  /evaluador/* ──proxy──► Evaluador GD   127.0.0.1:8765                      │
                         │  /ruta/*      ──proxy──► Ruta de desarrollo GD 127.0.0.1:8780               │
                         └──────────────────────────────────────────────────────────────────────────────┘
                                         │ identidad firmada (correo + rol) en cada petición
                                         ▼
                         Cada herramienta confía en el portal y deja de pedir su propio login
```

- **Un solo origen y una sola cookie.** El portal recibe todo el tráfico y reenvía por ruta
  (`/evaluador/…`, `/ruta/…`). Las herramientas solo escuchan en `127.0.0.1`: nadie entra sin pasar por el portal.
- **Identidad delegada.** El portal agrega a cada petición las cabeceras `X-GD-Usuario`, `X-GD-Rol` y una firma
  HMAC con un secreto compartido (`GD_PORTAL_SECRETO`). Cada herramienta, en «modo portal», valida la firma,
  crea el usuario en su tabla si no existe (para conservar autoría y bitácora) y omite su propio login.
  Sin el secreto, cada herramienta sigue funcionando sola como hoy (útil para desarrollo y pruebas).
- **Permisos por herramienta.** Tabla `permisos(usuario, herramienta, rol)`:
  Evaluador → `usuario | admin`; Ruta → `lector | editor | admin`. Quien no tiene permiso no ve la tarjeta
  y recibe 403 si intenta la ruta.
- **Usuarios.** El portal hereda el módulo de usuarios del Evaluador (`motor/auth.py` + `motor/correo.py`), que ya
  tiene invitaciones, recuperación de contraseña, bitácora y envío desde `tramites@solcor.com.co`. Los usuarios de
  la Ruta se migran cruzando por correo y conservando su rol.
- **Por qué rutas y no subdominios.** Con rutas basta una URL, un certificado y una cookie, y funciona igual en
  local, con el túnel y en la nube. El costo es cambiar las URL absolutas de los dos frontends por relativas
  (≈100 en el Evaluador y ≈70 en la Ruta; cambio mecánico y verificable con las pruebas).

## 3. Etapa 1 · Servidor local

### Fase 0 · Ordenar antes de integrar (1 semana)

1. **Ruta: consolidar el código.** La copia local está 1 commit atrás de GitHub y tiene 6 archivos modificados sin
   subir. Jonatan sube los cambios y se transfiere el repositorio a la organización `GDSolcor`.
2. **Ruta: sacar la base de datos de OneDrive.** Mover la herramienta a `C:\Ruta GD` (o hacer que `data/` apunte a
   `%LOCALAPPDATA%`, como ya hace el Evaluador). SQLite dentro de una carpeta sincronizada puede generar copias en
   conflicto o corromperse. `sync.py` sigue leyendo `01 Pipeline` desde la carpeta sincronizada.
3. **Puertos fijos:** portal 8700, Evaluador 8765, Ruta 8780 (8766 queda para las pruebas del Evaluador).
4. **Subir a GitHub lo pendiente del Evaluador** (rama `soporte/2026-11-25-tickets-54-47` y el cambio de correo).
5. **Matriz de usuarios y roles:** lista de quién usa cada herramienta y con qué rol.
6. **Solicitud a TI** (en paralelo, porque tarda): registro de la aplicación en Entra ID con `Mail.Send` limitado
   al buzón `tramites@solcor.com.co`. Más adelante se le agrega `Sites.Selected` para SharePoint y el inicio de
   sesión con Microsoft 365.

### Fase 1 · Portal y modo portal en las herramientas (2 a 3 semanas)

| Paso | Entregable | Cómo se prueba |
|---|---|---|
| 1.1 | Repositorio `GDSolcor/Portal-GD`: servidor Python (sin librerías externas), login, inicio con tarjetas, administración de usuarios y permisos | Pruebas automáticas al estilo de `herramientas/pruebas.py` |
| 1.2 | Proxy inverso por ruta (GET/POST/PUT/DELETE, cuerpos grandes para KMZ y adjuntos, descargas, cabeceras) | Subir un KMZ, descargar entregables y CSV a través del portal |
| 1.3 | Evaluador: URL relativas + «modo portal» (variable `GD_PORTAL_SECRETO`) | Sus 19 pruebas pasan, solo y detrás del portal |
| 1.4 | Ruta: URL relativas + «modo portal» | Sus pruebas y un recorrido manual de cada vista |
| 1.5 | Migración de usuarios de las dos bases al portal | Cada usuario entra una vez y ve solo sus herramientas |
| 1.6 | Arranque único: `iniciar_portal.ps1` levanta las tres piezas; la tarea «vigilante» las vigila | Reiniciar el PC y comprobar que todo vuelve solo |
| 1.7 | Túnel apuntando al portal (8700) en lugar del Evaluador | Entrar desde el celular |

Criterio de cierre: un usuario entra una vez, pasa del Evaluador a la Ruta sin volver a autenticarse, y un lector de
la Ruta no puede editar ni entrar al Evaluador si no lo tiene habilitado.

### Fase 2 · Conexiones entre herramientas (1 a 2 semanas, opcional)

- Del lead E1 en el Evaluador, enlace directo al proyecto en la Ruta (mismo número GD) y viceversa.
- La Ruta toma del Evaluador los datos del predio (coordenadas, área, capacidad) en lugar de leerlos de PDF.
- Un solo panel de soporte/tiquets para las dos herramientas.

## 4. Etapa 2 · Nube

### Recomendación

**Máquina virtual Windows en Azure + Cloudflare Tunnel con nombre fijo + Cloudflare Access con Microsoft Entra ID.**

- **Por qué una VM y no un servicio administrado:** el Evaluador usa varios GB de datos geográficos (catastro,
  GIS Solcor, hidrografía) y SQLite; una VM con disco propio los aloja sin cambios. App Service o Render obligarían a
  migrar a otra base de datos y a almacenamiento de objetos antes de tiempo.
- **Por qué Windows al inicio:** permite usar el cliente de OneDrive con una cuenta de servicio para seguir leyendo
  y escribiendo `01 Pipeline` sin cambiar código el primer día. Después se migra a Graph (fase 4) y la VM puede
  pasar a Linux.
- **Acceso:** `gd.solcor.com.co` (o similar) con Cloudflare Access exige la cuenta Microsoft 365 de Solcor y su
  verificación en dos pasos. El portal lee la identidad de Access (`Cf-Access-Jwt-Assertion`, validando la firma)
  y ya no maneja contraseñas: altas y bajas de personal quedan en manos de TI.
- **Costo orientativo:** VM B2ms (2 vCPU, 8 GB) + disco de 256 GB ≈ USD 80–110/mes; Cloudflare Tunnel y Access
  gratis hasta 50 usuarios. A confirmar con TI según los acuerdos de Microsoft de Solcor.

### Fases

| Fase | Contenido | Duración |
|---|---|---|
| 3 · Montaje | VM, Python, copia de datos de solo lectura, servicios de Windows para las tres piezas, túnel con nombre fijo, `EVALUADOR_URL_PUBLICA`, `COOKIE_SECURE=1`, respaldos diarios a SharePoint/Azure con prueba de restauración mensual | 1 a 2 semanas |
| 3b · Corte | Copiar bases (`usuarios`, `evaluaciones`, `leads`, `soporte`, `ruta_rtb`) en una ventana acordada, congelar el PC local, reenviar invitaciones con la nueva URL | 1 día |
| 4 · SharePoint por Graph | El Evaluador crea carpetas y sube entregables por Graph; `sync.py` lee por Graph; se retira OneDrive de la VM | 2 a 3 semanas |
| 5 · Endurecimiento | Inicio de sesión solo con Microsoft 365, monitoreo de disponibilidad, bitácora centralizada, plan de recuperación | 1 semana |

## 5. Riesgos y cómo se controlan

| Riesgo | Control |
|---|---|
| Base de la Ruta dentro de OneDrive (corrupción o copias en conflicto) | Fase 0, paso 2, antes de cualquier otra cosa |
| Ejecución de tiquets con Claude en la Ruta (`claude_runner.py`) depende del PC de Jonatan | En la nube queda desactivada; los tiquets se atienden desde el PC de desarrollo contra GitHub |
| Enlaces de invitación enviados con la URL del túnel temporal | Se reenvían cuando exista la dirección fija |
| Dos herramientas escribiendo en `01 Pipeline` | Solo el Evaluador crea carpetas; la Ruta solo lee. Se mantiene así |
| Dependencia de TI para Entra ID | Solicitud en la Fase 0; la etapa local no la necesita |
| Respaldos en el mismo disco | Copia diaria cifrada fuera de la VM desde la Fase 3 |

## 6. Decisiones pendientes

1. Confirmar rutas (`/evaluador`, `/ruta`) en un solo dominio frente a subdominios.
2. Nombre del dominio en la nube (por ejemplo `gd.solcor.com.co`) y quién administra el DNS.
3. Transferir `ruta-desarrollo-gd` a `GDSolcor` y quién mantiene cada herramienta.
4. Proveedor de nube: Azure (recomendado por Microsoft 365) u otro que defina TI.
5. Si en la nube el login es solo Microsoft 365 o también se admiten usuarios externos (aliados) con contraseña.
