# Envía un correo con Outlook clásico de este PC (lo usa motor/correo.py, método «outlook»).
# Outlook inicia sesión en Microsoft 365 con autenticación moderna, así que no depende del «SMTP autenticado»
# que Solcor tiene apagado. Recibe la ruta de un JSON (ASCII) con: perfil, cuenta, de, para, asunto, texto, html,
# adjuntos [{ruta, nombre, cid}]. Sale con código 1 y el motivo en la salida de error si algo falla.
param([Parameter(Mandatory)][string]$Ruta)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
try {
  $d = Get-Content -Raw -Encoding UTF8 $Ruta | ConvertFrom-Json
  $ol = New-Object -ComObject Outlook.Application
  $ns = $ol.GetNamespace('MAPI')
  $ns.Logon($d.perfil, $null, $false, $false)
  $cuenta = $null
  foreach ($a in $ns.Accounts) { if ($a.SmtpAddress -ieq $d.cuenta) { $cuenta = $a } }
  if (-not $cuenta) {
    $hay = ($ns.Accounts | ForEach-Object { $_.SmtpAddress }) -join ', '
    throw "Outlook de este PC no tiene la cuenta $($d.cuenta) (tiene: $(if ($hay) { $hay } else { 'ninguna' })). Ábrelo e inicia sesión con ella."
  }
  $m = $ol.CreateItem(0)
  $m.To = $d.para
  $m.Subject = $d.asunto
  if ($d.html) { $m.HTMLBody = $d.html } else { $m.Body = $d.texto }
  foreach ($x in $d.adjuntos) {
    $at = $m.Attachments.Add($x.ruta, 1, 0, $x.nombre)
    $at.PropertyAccessor.SetProperty('http://schemas.microsoft.com/mapi/proptag/0x3712001F', $x.cid)  # imagen incrustada (cid:)
  }
  $m.SendUsingAccount = $cuenta
  if ($d.de -and $d.de -ine $d.cuenta) { $m.SentOnBehalfOfName = $d.de }  # p. ej. buzón compartido con permiso «Enviar como»
  $m.Send()
  $ns.SendAndReceive($false)
  exit 0
} catch {
  [Console]::Error.WriteLine($_.Exception.Message)
  exit 1
}
