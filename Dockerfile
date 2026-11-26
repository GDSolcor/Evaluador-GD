# Evaluador GD en la nube: el código del repositorio y, en Postgres (Supabase), los datos que carga
# herramientas/migrar_a_nube.py. La imagen no lleva datos: sin EVALUADOR_DB_URL no arranca con capas.
#
#   docker build -t evaluador-gd .
#   docker run -p 8765:8765 -e EVALUADOR_DB_URL=... -e GD_PORTAL_SECRETO=... evaluador-gd
#
# La Ruta (Cloudflare Worker) la publica en /evaluador con el ingreso único (variable EVALUADOR_ORIGEN).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    EVALUADOR_DATOS_USUARIO=/app/datos_usuario \
    TZ=America/Bogota

RUN pip install --no-cache-dir "psycopg[binary]==3.3.*" "psycopg-pool==3.3.*" \
 && useradd --create-home --uid 10001 evaluador

WORKDIR /app
COPY --chown=evaluador:evaluador . /app
RUN mkdir -p /app/datos_usuario && chown -R evaluador:evaluador /app/datos_usuario /app/datos

USER evaluador
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8765/login', timeout=4)"
CMD ["python", "servidor.py", "--red", "--puerto", "8765", "--sin-navegador"]
