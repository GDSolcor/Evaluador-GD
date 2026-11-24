# Diagnóstico del código · Evaluador GD v2.1

8 de octubre de 2026. Revisión de `servidor.py`, los 19 módulos de `motor/`, las 12 herramientas y las tres páginas de `web/` (unas 8.700 líneas de Python y 3.100 de interfaz).

## Resultado general

El código está sano: el análisis estático no encontró nombres sin definir, errores de sintaxis ni parámetros mutables por defecto. La seguridad de acceso está bien resuelta (contraseñas con PBKDF2, sesiones con hash, bloqueo por intentos, consultas SQL parametrizadas, adjuntos validados por firma). Lo que sigue son los puntos débiles, ordenados por importancia.

## Corregido en esta revisión

| # | Qué pasaba | Qué se hizo |
|---|---|---|
| 1 | El servidor leía completo cualquier cuerpo de petición, sin tope. Una petición enorme a la página de ingreso (sin sesión) podía agotar la memoria. | Tope de 64 KB en las rutas públicas y 150 MB en las demás; responde 413. |
| 2 | Un `Content-Length` inválido tumbaba el hilo de la petición. | Se valida y responde 413. |
| 3 | No había respaldo de las bases que la herramienta escribe. Un archivo dañado significaba perder usuarios, evaluaciones y tickets. | Respaldo diario automático al iniciar, en `datos\respaldos\AAAAMMDD\`; conserva 14 días. |
| 4 | No existía un juego de pruebas; cada cambio se verificaba a mano. | `python herramientas\pruebas.py`: 11 pruebas (financiero, layout, GD por filas, tildes, informes, usuarios, soporte, privacidad de evaluaciones, GIS). No tocan los datos reales. |
| 5 | El recolector de circuitos se frenaba hasta 7 veces cuando su pestaña quedaba en segundo plano. | Las pausas corren en un Worker; se agregó `__detener()`. |
| 6 | Import sin usar en `evaluador.py`. | Eliminado. |

## Actualización v2.2 (8 de octubre, noche)

De la lista de abajo quedaron resueltos **A, B, C, D, E y K**: cookie `Secure` y dirección pública fija cuando se publica con HTTPS (`EVALUADOR_URL_PUBLICA`), máximo 3 solicitudes de recuperación por hora, borrado de módulos restringido, evaluaciones visibles para todo el equipo y versión 2.2 con `LICENCIA.md`. Siguen abiertos F (rol de solo lectura) y G a M (mantenimiento).

## Pendiente: requiere tu decisión

### Antes de abrir la herramienta al equipo o a internet

Hoy solo escucha en este PC, así que estos puntos no son un riesgo actual. Lo serían al exponerla.

| # | Punto | Riesgo | Propuesta |
|---|---|---|---|
| A | La cookie de sesión no lleva la marca `Secure` y el servidor habla HTTP. | En una red compartida, alguien podría leer la sesión. | Publicar siempre detrás de HTTPS (túnel o proxy) y marcar la cookie como `Secure`. |
| B | Los enlaces de invitación y recuperación se arman con el encabezado `Host` de la petición. | Un atacante podría hacer que el correo lleve un enlace a su dominio. | Fijar la dirección pública en la configuración. |
| C | «Recuperar contraseña» no tiene límite de envíos. | Sirve para saturar de correos a un usuario. | Máximo 3 solicitudes por correo cada hora. |
| D | Cualquier usuario puede borrar módulos del catálogo compartido. | Un borrado por error afecta a todos. | Solo el administrador o quien lo creó. |
| E | Las evaluaciones son privadas de cada usuario. | El equipo no puede abrir la evaluación de un compañero. | Agregar «proyecto compartido» (ver propuesta de acceso). |
| F | No hay roles intermedios: solo usuario y administrador. | No se puede dar acceso de solo consulta. | Rol «lector». |

### Calidad y mantenimiento

| # | Punto | Efecto | Propuesta |
|---|---|---|---|
| G | `web\index.html` tiene más de 3.100 líneas con todo el código de la interfaz. | Cada cambio es más lento y más propenso a romper otra cosa. | Partirlo en archivos por tema (mapa, layout, informes, soporte). |
| H | Siete funciones pasan de 120 líneas (`do_POST`, `reporte_predial`, `ctl.analizar`, `evaluar`…). | Difíciles de probar por partes. | Dividirlas cuando se toque cada una; no es urgente. |
| I | Cada petición abre su propia conexión a la base GIS y vuelve a crear la vista de la red. | Unos milisegundos por petición; se notará con varios usuarios a la vez. | Reutilizar conexiones por hilo de trabajo. |
| J | Las tildes de los informes salen de un diccionario. | Un apellido poco común puede quedar sin tilde, o un nombre extranjero recibir una que no lleva. | Mantener el diccionario con los tickets; opción de desactivar por documento. |
| K | La versión sigue en 2.1 pese a los cambios del barrido. | No se distingue qué copia tiene qué. | Subir a 2.2 y llevar un registro de cambios. |
| L | Cuatro `except Exception: pass` (catastro, PDF, ortografía). | Un fallo ahí queda invisible. | Registrar el error en el log del servidor. |
| M | El layout asigna mesas a inversores por orden, no por cercanía eléctrica. | Es una aproximación válida para prefactibilidad. | Dejarlo indicado en la ficha técnica. |

## Cómo verificar después de cualquier cambio

```bash
python herramientas\pruebas.py
```

Debe terminar con «11 de 11 pruebas pasaron».
