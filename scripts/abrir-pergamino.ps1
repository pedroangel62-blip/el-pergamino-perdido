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
$configuredPublicBase = Get-DotEnvValue "INSTAGRAM_PUBLIC_BASE_URL"
if (-not [string]::IsNullOrWhiteSpace($configuredPublicBase)) {
    try {
        $publicBaseUri = [Uri]$configuredPublicBase
    } catch {
        throw "INSTAGRAM_PUBLIC_BASE_URL no es una URL válida: $configuredPublicBase"
    }
    if (
        $publicBaseUri.Scheme -ne "https" -or
        [string]::IsNullOrWhiteSpace($publicBaseUri.Host) -or
        $publicBaseUri.AbsolutePath -ne "/" -or
        $publicBaseUri.Query -or
        $publicBaseUri.Fragment
    ) {
        throw "INSTAGRAM_PUBLIC_BASE_URL debe ser un origen HTTPS sin ruta: $configuredPublicBase"
    }
    $configuredPublicBase = $publicBaseUri.GetLeftPart([System.UriPartial]::Authority)
    if ($configuredPublicBase.TrimEnd("/").ToLowerInvariant() -ne $registeredPublicBase.TrimEnd("/").ToLowerInvariant()) {
        throw "INSTAGRAM_PUBLIC_BASE_URL e INSTAGRAM_REDIRECT_URI deben usar el mismo dominio público."
    }
    $registeredPublicBase = $configuredPublicBase
}
$panelUrl = "$registeredPublicBase/meta/instagram"
$isQuickTunnel = $redirectUri.Host -like "*.trycloudflare.com"
$isTailscale = $registeredPublicBase -match "\.ts\.net$"

$Cloudflared = $null
$TailscalePath = $null
if ($isTailscale) {
    $tailscaleCommand = Get-Command tailscale -ErrorAction SilentlyContinue
    if ($null -ne $tailscaleCommand) {
        $TailscalePath = $tailscaleCommand.Source
    } else {
        foreach ($candidate in @(
            "C:\Program Files\Tailscale\tailscale.exe",
            "C:\Program Files (x86)\Tailscale\tailscale.exe",
            (Join-Path $env:LOCALAPPDATA "Tailscale\tailscale.exe")
        )) {
            if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                $TailscalePath = $candidate
                break
            }
        }
    }
    if ([string]::IsNullOrWhiteSpace($TailscalePath)) {
        throw "El dominio configurado usa Tailscale, pero no encuentro tailscale.exe. Instala Tailscale para Windows y vuelve a abrir el acceso directo."
    }
} else {
    $Cloudflared = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($null -eq $Cloudflared) {
        throw "No encuentro cloudflared en PATH. Instálalo y vuelve a ejecutar este archivo."
    }
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

$tunnelProcess = $null
$publicPanelReady = $false
$tunnelMode = ""
if ($isTailscale) {
    $funnelOutput = (& $TailscalePath funnel --bg "http://127.0.0.1:8765" 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Tailscale no pudo activar Funnel. Inicia sesión y aprueba Funnel cuando lo solicite. $funnelOutput"
    }
    $tunnelMode = "tailscale"
    for ($attempt = 0; $attempt -lt 60 -and -not $publicPanelReady; $attempt++) {
        $publicPanelReady = Test-PublicPanel $panelUrl
        if (-not $publicPanelReady) {
            Start-Sleep -Seconds 1
        }
    }
    if (-not $publicPanelReady) {
        throw "Tailscale Funnel está configurado, pero $panelUrl no respondió en 60 segundos. Comprueba que Tailscale esté conectado y que el servidor local siga activo."
    }
    Write-Host "Se reutiliza el Funnel estable de Tailscale: $registeredPublicBase"
} else {
    for ($attempt = 0; $attempt -lt 8 -and -not $publicPanelReady; $attempt++) {
        $publicPanelReady = Test-PublicPanel $panelUrl
        if (-not $publicPanelReady) {
            Start-Sleep -Seconds 1
        }
    }

    if ($publicPanelReady) {
        Write-Host "Se reutiliza el túnel público registrado en Meta: $registeredPublicBase"
    } elseif ($isQuickTunnel) {
        throw "La URL rápida registrada en Meta ($registeredPublicBase) no está activa. No genero otra URL: Meta la rechazaría. Mantén abierto el Quick Tunnel actual o configura Tailscale Funnel o un túnel con nombre y dominio estable."
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
        $tunnelMode = "cloudflare"
    }
}

$pidData = @{
    server_pid = if ($null -ne $serverProcess) { $serverProcess.Id } else { $null }
    tunnel_pid = if ($null -ne $tunnelProcess) { $tunnelProcess.Id } else { $null }
    tunnel_mode = $tunnelMode
}
$pidData | ConvertTo-Json | Set-Content -LiteralPath $PidFile -Encoding UTF8
Write-Host "URL pública: $panelUrl"
Start-Process $panelUrl
