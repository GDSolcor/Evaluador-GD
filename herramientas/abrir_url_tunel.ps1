# Abre en el navegador la URL vigente del túnel del Evaluador y la copia al portapapeles.
# La URL la escribe vigilar_evaluador.ps1 en datos\logs\url_tunel.txt cada vez que abre un túnel nuevo.
$archivo = Join-Path (Split-Path -Parent $PSScriptRoot) "datos\logs\url_tunel.txt"
$aviso = New-Object -ComObject WScript.Shell
if (-not (Test-Path $archivo)) {
  $aviso.Popup("Todavía no hay un túnel abierto. El vigilante lo abrirá en unos minutos.", 0, "Evaluador GD", 48) | Out-Null
  exit
}
$url = (Get-Content $archivo -Encoding UTF8 | Select-Object -First 1).Trim()
Set-Clipboard -Value $url
Start-Process $url
$aviso.Popup("URL del Evaluador copiada al portapapeles:`n`n$url", 8, "Evaluador GD", 64) | Out-Null