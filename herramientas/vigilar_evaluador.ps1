# Vigilante del Evaluador: lo ejecuta la tarea programada "Evaluador GD - vigilante" al iniciar sesión y cada 5 minutos.
# Si el servidor (puerto 8765) no responde lo relanza; si el túnel de Cloudflare no existe o caducó, abre uno nuevo.
# La URL vigente queda en datos\logs\url_tunel.txt (y el historial en datos\logs\vigilante.log), y se avisa con una notificación.
$raiz = Split-Path -Parent $PSScriptRoot
$logs = Join-Path $raiz "datos\logs"
New-Item -ItemType Directory -Force $logs | Out-Null
$registro = Join-Path $logs "vigilante.log"
$logTunel = Join-Path $logs "tunel.log"
$archivoUrl = Join-Path $logs "url_tunel.txt"
function Anotar($texto) { Add-Content -Path $registro -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $texto" -Encoding UTF8 }
function Responde($url) {
  try { return (Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 15).StatusCode -eq 200 } catch { return $false }
}
function Avisar($texto) {
  try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    $xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $t = $xml.GetElementsByTagName('text'); $t.Item(0).InnerText = 'Evaluador GD'; $t.Item(1).InnerText = $texto
    $app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($xml))
  } catch {}
}

$servidorOk = Responde 'http://127.0.0.1:8765/'
if (-not $servidorOk) {
  # Un proceso colgado que aún ocupe el puerto impediría relanzarlo.
  Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
  Anotar "servidor sin respuesta: se relanza"
}

# El túnel está sano si cloudflared corre, su registro no dice que caducó y (con internet) su URL responde.
$tunelOk = [bool](Get-Process cloudflared -ErrorAction SilentlyContinue)
$urlActual = $null
if (Test-Path $logTunel) {
  $m = Select-String -Path $logTunel -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' | Select-Object -First 1
  if ($m) { $urlActual = $m.Matches[0].Value }
  if ((Get-Content $logTunel -Tail 20) -match 'Tunnel not found') { $tunelOk = $false }
} else { $tunelOk = $false }
if ($tunelOk -and $servidorOk -and $urlActual -and -not (Responde $urlActual)) {
  # Sin internet no se toca el túnel: abrir otro cambiaría la URL sin necesidad.
  if (Responde 'https://www.cloudflare.com/cdn-cgi/trace') { $tunelOk = $false }
}
if (-not $tunelOk) {
  Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  for ($i = 0; $i -lt 10 -and (Get-Process cloudflared -ErrorAction SilentlyContinue); $i++) { Start-Sleep 1 }
  Anotar "túnel caído o caducado ($urlActual): se abre uno nuevo"
}

if (-not $servidorOk -or -not $tunelOk) {
  & (Join-Path $PSScriptRoot "iniciar_servidor_y_tunel.ps1") -Tunel
}

if (-not $tunelOk) {
  $nueva = $null
  for ($i = 0; $i -lt 30 -and -not $nueva; $i++) {
    Start-Sleep 2
    if (Test-Path $logTunel) {
      $m = Select-String -Path $logTunel -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' | Select-Object -First 1
      if ($m) { $nueva = $m.Matches[0].Value }
    }
  }
  if ($nueva) {
    Set-Content -Path $archivoUrl -Value $nueva -Encoding UTF8
    Anotar "túnel nuevo: $nueva"
    Avisar "Túnel nuevo: $nueva"
  } else { Anotar "no se obtuvo la URL del túnel nuevo" }
} elseif ($urlActual -and -not (Test-Path $archivoUrl)) {
  Set-Content -Path $archivoUrl -Value $urlActual -Encoding UTF8
}
