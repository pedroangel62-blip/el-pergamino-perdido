$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) "el-pergamino-perdido"
$ServerLog = Join-Path $TempRoot "server.log"
$ServerErrorLog = Join-Path $TempRoot "server-error.log"
$TunnelLog = Join-Path $TempRoot "cloudflared.log"
$TunnelErrorLog = Join-Path $TempRoot "cloudflared-error.log"
$PidFile = Join-Path $TempRoot "processes.json"

if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "No encuentro Python del entorno virtual en $PythonPath"
}

$Cloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue
if ($null -eq $Cloudflared) {
    throw "No encuentro cloudflared en PATH. Instálalo y vuelve a ejecutar este archivo."
}

New-Item -ItemType Directory -Path $TempRoot -Force | Out-Null
Remove-Item -LiteralPath $ServerLog, $ServerErrorLog, $TunnelLog, $TunnelErrorLog -Force -ErrorAction SilentlyContinue

$serverConnection = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
$serverProcess = $null
if ($null -eq $serverConnection) {
    $serverProcess = Start-Process `
        -FilePath $PythonPath `
        -ArgumentList @("-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8765") `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Minimized `
        -RedirectStandardOutput $ServerLog `
        -RedirectStandardError $ServerErrorLog `
        -PassThru
    Write-Host "Servidor iniciado en segundo plano (PID $($serverProcess.Id))."
} else {
    Write-Host "Ya hay un servidor escuchando en el puerto 8765; se reutiliza."
}

$tunnelProcess = Start-Process `
    -FilePath $Cloudflared.Source `
    -ArgumentList @("tunnel", "--url", "http://127.0.0.1:8765") `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Minimized `
    -RedirectStandardOutput $TunnelLog `
    -RedirectStandardError $TunnelErrorLog `
    -PassThru

$pidData = @{
    server_pid = if ($null -ne $serverProcess) { $serverProcess.Id } else { $null }
    tunnel_pid = $tunnelProcess.Id
}
$pidData | ConvertTo-Json | Set-Content -LiteralPath $PidFile -Encoding UTF8

$publicUrl = $null
for ($attempt = 0; $attempt -lt 60 -and $null -eq $publicUrl; $attempt++) {
    Start-Sleep -Seconds 1
    foreach ($logPath in @($TunnelLog, $TunnelErrorLog)) {
        if (-not (Test-Path -LiteralPath $logPath -PathType Leaf)) {
            continue
        }
        $logText = Get-Content -LiteralPath $logPath -Raw -ErrorAction SilentlyContinue
        $match = [regex]::Match(
            $logText,
            "https://[a-z0-9-]+\.trycloudflare\.com",
            [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
        )
        if ($match.Success) {
            $publicUrl = $match.Value.TrimEnd("/")
            break
        }
    }
}

if ($null -eq $publicUrl) {
    $detalle = ""
    if (Test-Path -LiteralPath $TunnelErrorLog -PathType Leaf) {
        $detalle = Get-Content -LiteralPath $TunnelErrorLog -Raw -ErrorAction SilentlyContinue
    }
    if (-not $detalle -and (Test-Path -LiteralPath $TunnelLog -PathType Leaf)) {
        $detalle = Get-Content -LiteralPath $TunnelLog -Raw -ErrorAction SilentlyContinue
    }
    throw "Cloudflare no devolvió una URL pública en 60 segundos. $detalle"
}

$panelUrl = "$publicUrl/meta/instagram"
Write-Host "URL pública: $panelUrl"
Start-Process $panelUrl
