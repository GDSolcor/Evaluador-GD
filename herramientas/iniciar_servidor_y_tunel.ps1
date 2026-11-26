# Arranca el servidor del Evaluador (puerto 8765) y, con -Tunel, el túnel temporal de Cloudflare.
# Se lanza desacoplado de la ventana o sesión que lo invoca, para que no se cierre con ella.
# Las variables EVALUADOR_* se toman del entorno del usuario (la clave del correo no se escribe en ningún archivo).
param([switch]$Tunel)
$raiz = Split-Path -Parent $PSScriptRoot
$logs = Join-Path $raiz "datos\logs"
New-Item -ItemType Directory -Force $logs | Out-Null
foreach ($n in 'EVALUADOR_GRAPH_SECRETO', 'EVALUADOR_SMTP_CLAVE', 'EVALUADOR_SMTP_USUARIO', 'EVALUADOR_URL_PUBLICA', 'EVALUADOR_SHAREPOINT_URL', 'GD_PORTAL_SECRETO') {
  $v = [Environment]::GetEnvironmentVariable($n, 'User'); if ($v) { Set-Item "env:$n" $v }
}
if (-not (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue)) {
  Start-Process -FilePath python -ArgumentList '-u', 'servidor.py', '--sin-navegador' -WorkingDirectory $raiz -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logs "servidor_out.log") -RedirectStandardError (Join-Path $logs "servidor_err.log")
}
if ($Tunel -and -not (Get-Process cloudflared -ErrorAction SilentlyContinue)) {
  $log = Join-Path $logs "tunel.log"; Remove-Item $log -ErrorAction SilentlyContinue
  Start-Process -FilePath (Join-Path $raiz "herramientas\bin\cloudflared.exe") -ArgumentList 'tunnel', '--no-autoupdate', '--url', 'http://127.0.0.1:8765', '--logfile', "`"$log`"" -WindowStyle Hidden
}
