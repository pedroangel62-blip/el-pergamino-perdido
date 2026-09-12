$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) "el-pergamino-perdido"
$ServerLog = Join-Path $TempRoot "server.log"
$ServerErrorLog = Join-Path $TempRoot "server-error.log"
$EnvFile = Join-Path $ProjectRoot ".env"

function Get-TailscalePath {
    $command = Get-Command tailscale -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }
    foreach ($candidate in @(
        "C:\Program Files\Tailscale\tailscale.exe",
        "C:\Program Files (x86)\Tailscale\tailscale.exe",
        (Join-Path $env:LOCALAPPDATA "Tailscale\tailscale.exe")
    )) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }
    return $null
}

function Set-DotEnvValue {
    param(
        [string]$Name,
        [string]$Value
    )

    $lines = @()
    if (Test-Path -LiteralPath $EnvFile -PathType Leaf) {
        $lines = @(Get-Content -LiteralPath $EnvFile)
    }
    $pattern = "^\s*" + [regex]::Escape($Name) + "\s*="
    $found = $false
    $newLines = @()
    foreach ($line in $lines) {
        if ($line -match $pattern) {
            $newLines += "$Name=$Value"
            $found = $true
        } else {
            $newLines += $line
        }
    }
    if (-not $found) {
        $newLines += "$Name=$Value"
    }
    Set-Content -LiteralPath $EnvFile -Value $newLines -Encoding UTF8
}

function Get-FunnelUrl {
    param([string]$TailscalePath)

    $status = (& $TailscalePath funnel status 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "No se pudo leer el estado de Tailscale Funnel. $status"
    }
    $match = [regex]::Match(
        $status,
        "https://[a-z0-9-]+(?:\.[a-z0-9-]+)+\.ts\.net",
        [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
    )
    if (-not $match.Success) {
        throw "Tailscale no mostró todavía una URL Funnel. Comprueba que Tailscale esté conectado y que hayas aprobado Funnel. Estado: $status"
    }
    return $match.Value.TrimEnd("/")
}

function Test-PublicPanel {
    param([string]$Uri)

    try {
        $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 5
        return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500)
    } catch {
        return $false
    }
}

$TailscalePath = Get-TailscalePath
if ([string]::IsNullOrWhiteSpace($TailscalePath)) {
    throw "No encuentro Tailscale. Instálalo desde https://tailscale.com/download/windows y vuelve a ejecutar este archivo."
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "No encuentro Python del entorno virtual en $PythonPath"
}
if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    throw "No encuentro .env en $EnvFile. El archivo contiene la configuración local y debe existir antes de continuar."
}

New-Item -ItemType Directory -Path $TempRoot -Force | Out-Null
$serverConnection = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
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

$funnelOutput = (& $TailscalePath funnel --bg "http://127.0.0.1:8765" 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Tailscale no pudo activar Funnel. Inicia sesión y aprueba la autorización que aparezca. $funnelOutput"
}

$publicBase = Get-FunnelUrl $TailscalePath
$redirectUri = "$publicBase/meta/instagram/callback"

$panelReady = $false
for ($attempt = 0; $attempt -lt 60 -and -not $panelReady; $attempt++) {
    $panelReady = Test-PublicPanel "$publicBase/meta/instagram"
    if (-not $panelReady) {
        Start-Sleep -Seconds 1
    }
}
if (-not $panelReady) {
    throw "La URL Funnel $publicBase existe, pero la aplicación no respondió en 60 segundos."
}

Set-DotEnvValue "INSTAGRAM_PUBLIC_BASE_URL" $publicBase
Set-DotEnvValue "INSTAGRAM_REDIRECT_URI" $redirectUri

Write-Host "Configuración local terminada."
Write-Host "INSTAGRAM_PUBLIC_BASE_URL=$publicBase"
Write-Host "INSTAGRAM_REDIRECT_URI=$redirectUri"
Write-Host "Ahora añade exactamente esta URL en Meta > Instagram > OAuth redirect URIs:"
Write-Host $redirectUri
Write-Host "Después podrás usar scripts\Abrir-El-Pergamino.cmd con un solo clic."
Start-Process "https://developers.facebook.com/apps/2055031832049192/use_cases/customize/"
Start-Process "$publicBase/meta/instagram"
