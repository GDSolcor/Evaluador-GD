# Plan de integración · Herramientas de Desarrollo GD en un solo sitio

25 de noviembre de 2026, actualizado el 26 de noviembre de 2026. Integrar el **Evaluador GD** y la **Ruta de
desarrollo GD** en un solo sitio, con un único inicio de sesión desde el que cada persona entra a la herramienta que
tenga habilitada.

> **Cambio respecto a la primera versión.** La Ruta ya no es la aplicación Python local: se reescribió en TypeScript y
> está publicada en Cloudflare Workers (`https://ruta-desarrollo-gd.solcor.workers.dev`). Por eso el plan ya no
> construye un portal nuevo: **el Worker de la Ruta hace de portal** y el Evaluador se conecta detrás de él.

## 1. Punto de partida

| | Evaluador GD | Ruta de desarrollo GD |
|---|---|---|
| Repositorio | `GDSolcor/Evaluador-GD` (cuenta personal `GDSolcor`) | `sebastiansolcor/ruta-desarrollo-gd` (privado; `GDSolcor` es colaborador) |
| Tecnología | Python sin librerías externas, SQLite, HTML/JS | TypeScript (Hono) en Cloudflare Workers; frontend estático HTML/JS |
| Datos | ≈ 40 GB en disco: IGAC 14 GB, autoridades ambientales 16 GB, GIS Solcor 6 GB, red OR 1,4 GB, catastros | Postgres en Supabase |
| Dónde corre | PC de `C:\Evaluador GD`; acceso remoto por túnel temporal de Cloudflare (la URL cambia) | Cloudflare (cuenta `solcor`), dirección fija `*.workers.dev` |
| Usuarios | `usuarios.db` propia; invitación y recuperación por correo; roles admin/usuario; bitácora | Tabla `usuarios` en Postgres; PBKDF2; cookie `sid`; roles admin/editor/lector |
| SharePoint | **Escribe** en `01 Pipeline` por la carpeta sincronizada de OneDrive | **Lee y escribe** por Microsoft Graph con la app de Entra ID «Ruta GD» (`Sites.Selected`) |
| Correo | Outlook clásico del PC (cuenta corporativa); Graph listo para cuando TI registre la app | No envía correo |
| Rutas web | Página y `/api/*` en la raíz | Página en la raíz; el Worker atiende solo `/api/*` |

**Por qué el Evaluador no se pasa a Workers.** Workers no tiene disco, limita memoria y tiempo de CPU, y sus bases
administradas no admiten 40 GB de SQLite. El Evaluador además genera Word, Excel, PowerPoint y PDF con Python.
Llevarlo a Workers sería reescribirlo; no se justifica. Se queda en un servidor propio (hoy el PC, después una VM)
y se publica detrás del Worker.

## 2. Arquitectura propuesta

```
                ┌──────────── Cloudflare (cuenta solcor) ─────────────┐
 Navegador ───► │ gd.solcor.com.co  (hoy ruta-desarrollo-gd.workers.dev) │
                │                                                     │
                │  /             Ruta: frontend estático              │
                │  /api/*        Ruta: Worker + Supabase + Graph      │
                │  /evaluador/*  Worker: valida la sesión de la Ruta, │
                │                comprueba el permiso «evaluador»,    │
                │                firma la identidad y reenvía ──────┐ │
                └───────────────────────────────────────────────────┼─┘
                                                                    │ túnel con nombre fijo
                                                                    │ (solo el Worker puede entrar)
                                                                    ▼
                                   Evaluador GD · 127.0.0.1:8765 en «modo portal»
                                   (PC hoy → VM Windows en Azure después)
```

- **Un solo login: el de la Ruta.** El Worker ya resuelve el usuario desde la cookie `sid` (`auth.usuarioDe`). Para
  `/evaluador/*` hace lo mismo y, si no hay sesión, envía al login de la Ruta.
- **Permiso por herramienta.** Se agrega a `usuarios` de la Ruta la columna `evaluador` (`no | usuario | admin`).
  Quien no lo tiene no ve el acceso al Evaluador y recibe 403 en `/evaluador/*`.
- **Identidad firmada.** Antes de reenviar, el Worker:
  1. borra cualquier cabecera `X-GD-*` que traiga el navegador;
  2. agrega `X-GD-Usuario` (correo), `X-GD-Nombre`, `X-GD-Rol` y `X-GD-Fecha`;
  3. firma esos valores con HMAC-SHA256 y el secreto `GD_PORTAL_SECRETO` (`X-GD-Firma`).

  El Evaluador valida la firma y que la fecha tenga menos de 60 s, crea el usuario si no existe (para conservar
  autoría y bitácora) y omite su propio login.
- **Túnel cerrado.** El túnel con nombre fijo no queda abierto a internet. Hay dos maneras de lograrlo:
  - **Opción A (más simple):** el nombre del túnel (por ejemplo `evaluador-origen.solcor.com.co`) se protege con
    Cloudflare Access usando un *service token*, y solo el Worker tiene sus credenciales (`CF-Access-Client-Id/Secret`).
  - **Opción B:** Workers VPC, que conecta el Worker con el túnel privado sin nombre público.

  En los dos casos la firma HMAC es una segunda barrera.
- **Sin Python en Cloudflare.** El Worker solo reenvía: la petición y la respuesta pasan en *streaming*, sin
  procesarse, así que casi no consume CPU.
- **El Evaluador sigue funcionando solo.** Sin `GD_PORTAL_SECRETO` arranca como hoy, con su propio login, en
  `http://127.0.0.1:8765`. Sirve para desarrollo y pruebas, y como respaldo si el Worker falla.

## 3. Cambios por pieza

### 3.1 Evaluador (`GDSolcor/Evaluador-GD`)

| Paso | Cambio | Cómo se prueba |
|---|---|---|
| E1 | **URL relativas.** Las ≈100 direcciones absolutas (`/api/...`, `/admin`, recursos) pasan a relativas o a un prefijo configurable (`EVALUADOR_PREFIJO=/evaluador`). Es obligatorio: `/api/*` en la raíz es de la Ruta. | Las 19 pruebas pasan con y sin prefijo |
| E2 | **Modo portal** (`GD_PORTAL_SECRETO`): valida la firma, crea o actualiza el usuario y ignora su cookie `evaluador_sesion`. Los roles salen de `X-GD-Rol`. | Pruebas nuevas: firma válida, vencida, alterada y ausente |
| E3 | **Enlaces de los correos** (invitación, recuperación, avisos) con `EVALUADOR_URL_PUBLICA=https://gd.solcor.com.co/evaluador`. En modo portal, la invitación lleva al login de la Ruta. | Correo de prueba desde el panel |
| E4 | **Administración.** En modo portal, el alta de usuarios pasa a la Ruta; el panel del Evaluador conserva la configuración propia (correo, permisos de bases y leads). | Revisión manual del panel |
| E5 | **Arranque.** `iniciar_servidor_y_tunel.ps1` levanta el túnel con nombre fijo (`cloudflared tunnel run`, credenciales fuera del repositorio) en lugar del temporal; la tarea «vigilante» se mantiene. | Reiniciar el PC y entrar desde el celular |

### 3.2 Ruta (`sebastiansolcor/ruta-desarrollo-gd`), por PR al repositorio de la Ruta

| Paso | Cambio |
|---|---|
| R1 | `wrangler.jsonc`: `run_worker_first: ["/api/*", "/evaluador", "/evaluador/*"]` y el secreto `GD_PORTAL_SECRETO`. Para la opción A: `EVALUADOR_ORIGEN`, `CF_ACCESS_CLIENT_ID` y `CF_ACCESS_CLIENT_SECRET` como secretos. |
| R2 | `src/worker.ts` (o una ruta nueva `src/routes/evaluador.ts`): sesión → permiso → limpiar y firmar cabeceras → `fetch` al origen, preservando método, cuerpo, `Content-Type`, descargas y `Set-Cookie` con la ruta `/evaluador`. |
| R3 | Esquema: columna `evaluador` en `usuarios`; administración de usuarios con ese permiso. |
| R4 | Frontend: en la barra lateral, un acceso «Evaluador GD» visible solo para quien tiene el permiso, y desde el Evaluador un enlace de vuelta a la Ruta. |
| R5 | Pruebas en `tests/api/`: sin sesión → login; sin permiso → 403; cabeceras falsificadas se descartan; firma verificable con el mismo secreto. El origen se simula como ya hacen con `fakeGraph`. |

### 3.3 Cloudflare y Microsoft 365

- **Túnel con nombre fijo** en la cuenta `solcor` (`cloudflared tunnel create evaluador-gd`) apuntando a
  `http://127.0.0.1:8765`. Termina con las URL que cambian y con los enlaces de invitación rotos.
- **Dominio propio (recomendado):** `gd.solcor.com.co` como dominio del Worker. Exige que la zona `solcor.com.co`
  (o un subdominio delegado) esté en Cloudflare; si no, se sigue en `*.workers.dev` sin cambiar nada más.
- **Entra ID:** Solcor ya registró la app «Ruta GD» para SharePoint. Para el Evaluador se pide un registro
  igual con `Mail.Send` limitado al buzón `tramites@solcor.com.co` y, después, `Sites.Selected` para escribir
  `01 Pipeline` por Graph. El código de envío por Graph ya está listo en `motor/correo.py`.

## 4. Fases

| Fase | Contenido | Quién | Duración |
|---|---|---|---|
| 0 · Accesos | Permiso de escritura para `GDSolcor` en el repositorio de la Ruta (o mover los dos repositorios a una organización de Solcor); acceso a la cuenta Cloudflare `solcor`; solicitud a TI de la app de Entra ID del Evaluador; integrar el PR GDSolcor/Evaluador-GD#1 | Desarrollo GD, Sebastián, TI | 1 semana |
| 1 · Evaluador listo para el portal | E1 a E4, con el Evaluador aún en su URL actual | Evaluador | 1 a 2 semanas |
| 2 · Túnel fijo | E5 y túnel con nombre; Access con *service token* (opción A) | Evaluador + administrador de Cloudflare | 2 a 3 días |
| 3 · Worker como portal | R1 a R5 en una rama de la Ruta; Cloudflare publica la vista previa de la rama; prueba completa; integrar a `main` | Ruta | 1 a 2 semanas |
| 4 · Corte de usuarios | Pasar los usuarios del Evaluador a la Ruta cruzando por correo (los que no existan se invitan desde la Ruta), activar `GD_PORTAL_SECRETO` en el Evaluador y retirar el túnel temporal | Las dos | 1 día |
| 5 · Evaluador a la nube | VM Windows en Azure (B2ms + 256 GB, ≈ USD 80–110/mes), copia de datos, servicio de Windows, mismo túnel con nombre (solo cambia el equipo donde corre `cloudflared`); correo por Graph porque en la VM no hay Outlook | Evaluador + TI | 1 a 2 semanas |
| 6 · SharePoint por Graph y endurecimiento | El Evaluador escribe `01 Pipeline` por Graph y se retira OneDrive de la VM; opcional: login con la cuenta Microsoft 365 en la Ruta (Entra ID) | Las dos | 2 a 3 semanas |

**Criterio de cierre de la etapa 4:** un usuario entra una vez en la Ruta, abre el Evaluador sin volver a
autenticarse, y un usuario sin el permiso no puede abrirlo ni con la URL directa ni con la del túnel.

## 5. Riesgos y cómo se controlan

| Riesgo | Control |
|---|---|
| Peticiones largas del Evaluador (evaluaciones, generación de entregables): Cloudflare corta a los ≈100 s sin respuesta del origen | Medir en la fase 1; lo que tarde más se pasa a tarea en segundo plano con consulta de avance |
| Subidas grandes (KMZ, adjuntos) | El límite de cuerpo de Cloudflare (100 MB en planes Free/Pro) cubre los KMZ actuales; el Worker reenvía en *streaming* |
| `/api/*` del Evaluador choca con la Ruta | Paso E1 antes de cualquier otra cosa |
| Alguien llega al Evaluador saltándose el Worker | Túnel cerrado con Access o Workers VPC, más la firma HMAC con vencimiento |
| El Evaluador depende de que el PC esté encendido | Fase 5; mientras tanto, la tarea «vigilante» y aviso en el Worker cuando el origen no responde (502 con mensaje claro) |
| Repositorios en cuentas personales (`GDSolcor`, `sebastiansolcor`) | Fase 0: organización de Solcor en GitHub con los dos repositorios |
| Secretos (`GD_PORTAL_SECRETO`, *service token*, Graph) | Secretos de Wrangler en Cloudflare; variables de entorno o DPAPI en el servidor; nunca en archivos del repositorio |
| Correo en la nube | Outlook solo sirve en un PC con sesión iniciada; en la VM se usa Graph (solicitud a TI en la fase 0) |

## 6. Decisiones pendientes

1. Dominio: `gd.solcor.com.co` (requiere DNS en Cloudflare) o seguir en `ruta-desarrollo-gd.solcor.workers.dev`.
2. Túnel cerrado con Access y *service token* (opción A, recomendada) o con Workers VPC (opción B).
3. Crear una organización de Solcor en GitHub y mover allí los dos repositorios, y quién mantiene cada herramienta.
4. Quién administra la cuenta Cloudflare `solcor` y crea el túnel.
5. Si más adelante el login pasa a ser solo con Microsoft 365, o se mantienen usuarios externos con contraseña.
