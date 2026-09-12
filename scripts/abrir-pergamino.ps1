$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) "el-pergamino-perdido"
$ServerLog = Join-Path $TempRoot "server.log"
$ServerErrorLog = Join-Path $TempRoot "server-error.log"
$TunnelLog = Join-Path $TempRoot "cloudflared.log"
$TunnelErrorLog = Join-Path $TempRoot "cloudflared-error.log"
$PidFile = Join-Path $TempRoot "processes.json"
$EnvFile = Join-Path $ProjectRoot ".env"

function Get-DotEnvValue {
    param([string]$Name)

    $pattern = "^\s*" + [regex]::Escape($Name) + "\s*=\s*(.*)$"
    if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
        return $null
    }
    foreach ($line in Get-Content -LiteralPath $EnvFile) {
        if ($line -notmatch $pattern) {
            continue
        }
        $value = $Matches[1].Trim()
        if ($value.Length -ge 2) {
            $first = $value.Substring(0, 1)
            $last = $value.Substring($value.Length - 1, 1)
            if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        return $value
    }
    return $null
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

if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "No encuentro Python del entorno virtual en $PythonPath"
}

$Cloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue
if ($null -eq $Cloudflared) {
    throw "No encuentro cloudflared en PATH. Instálalo y vuelve a ejecutar este archivo."
}

$registeredRedirect = Get-DotEnvValue "INSTAGRAM_REDIRECT_URI"
if ([string]::IsNullOrWhiteSpace($registeredRedirect)) {
    throw "Falta INSTAGRAM_REDIRECT_URI en $EnvFile. Meta necesita una URL de retorno registrada."
}
try {
    $redirectUri = [Uri]$registeredRedirect
} catch {
    throw "INSTAGRAM_REDIRECT_URI no es una URL válida: $registeredRedirect"
}
if ($redirectUri.Scheme -ne "https" -or [string]::IsNullOrWhiteSpace($redirectUri.Host)) {
    throw "INSTAGRAM_REDIRECT_URI debe ser una URL HTTPS completa: $registeredRedirect"
}
$registeredPublicBase = $redirectUri.GetLeftPart([System.UriPartial]::Authority)
$panelUrl = "$registeredPublicBase/meta/instagram"
$isQuickTunnel = $redirectUri.Host -like "*.trycloudflare.com"

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

$tunnelProcess = $null
$publicPanelReady = $false
for ($attempt = 0; $attempt -lt 8 -and -not $publicPanelReady; $attempt++) {
    $publicPanelReady = Test-PublicPanel $panelUrl
    if (-not $publicPanelReady) {
        Start-Sleep -Seconds 1
    }
}

if ($publicPanelReady) {
    Write-Host "Se reutiliza el túnel público registrado en Meta: $registeredPublicBase"
} elseif ($isQuickTunnel) {
    throw "La URL rápida registrada en Meta ($registeredPublicBase) no está activa. No genero otra URL: Meta la rechazaría. Mantén abierto el Quick Tunnel actual o configura un túnel con nombre y dominio estable."
} else {
    $tunnelName = Get-DotEnvValue "CLOUDFLARED_TUNNEL_NAME"
    if ([string]::IsNullOrWhiteSpace($tunnelName)) {
        throw "El dominio registrado no responde y falta CLOUDFLARED_TUNNEL_NAME en $EnvFile para iniciar el túnel con nombre."
    }

    $tunnelProcess = Start-Process `
        -FilePath $Cloudflared.Source `
        -ArgumentList @("tunnel", "run", $tunnelName) `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Minimized `
        -RedirectStandardOutput $TunnelLog `
        -RedirectStandardError $TunnelErrorLog `
        -PassThru

    for ($attempt = 0; $attempt -lt 60 -and -not $publicPanelReady; $attempt++) {
        Start-Sleep -Seconds 1
        $publicPanelReady = Test-PublicPanel $panelUrl
    }

    if (-not $publicPanelReady) {
        $detalle = ""
        if (Test-Path -LiteralPath $TunnelErrorLog -PathType Leaf) {
            $detalle = Get-Content -LiteralPath $TunnelErrorLog -Raw -ErrorAction SilentlyContinue
        }
        if (-not $detalle -and (Test-Path -LiteralPath $TunnelLog -PathType Leaf)) {
            $detalle = Get-Content -LiteralPath $TunnelLog -Raw -ErrorAction SilentlyContinue
        }
        Stop-Process -Id $tunnelProcess.Id -Force -ErrorAction SilentlyContinue
        throw "El túnel con nombre no respondió en 60 segundos. $detalle"
    }
}

$pidData = @{
    server_pid = if ($null -ne $serverProcess) { $serverProcess.Id } else { $null }
    tunnel_pid = if ($null -ne $tunnelProcess) { $tunnelProcess.Id } else { $null }
}
$pidData | ConvertTo-Json | Set-Content -LiteralPath $PidFile -Encoding UTF8
Write-Host "URL pública: $panelUrl"
Start-Process $panelUrl
