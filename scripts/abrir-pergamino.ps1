$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$PythonWindowlessPath = Join-Path $ProjectRoot ".venv\Scripts\pythonw.exe"
if (Test-Path -LiteralPath $PythonWindowlessPath -PathType Leaf) {
    $PythonPath = $PythonWindowlessPath
}
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

function Stop-PergaminoProcess {
    param([System.Diagnostics.Process]$Process)

    try {
        Stop-Process -Id $Process.Id -Force -ErrorAction Stop
        return $true
    } catch {
        $message = $_.Exception.Message
        if ($message -notmatch "(?i)access is denied|acceso denegado|denegad") {
            throw
        }

        Write-Warning "Windows ha rechazado el cierre del proceso $($Process.Id) por permisos. Se solicitará permiso de administrador..."
        try {
            $command = "Stop-Process -Id $($Process.Id) -Force -ErrorAction Stop"
            Start-Process \`
                -FilePath "powershell.exe" \`
                -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $command) \`
                -Verb RunAs \`
                -Wait \`
                -ErrorAction Stop | Out-Null
        } catch {
            Write-Warning "No se pudo solicitar el cierre elevado del proceso $($Process.Id): $($_.Exception.Message)"
            return $false
        }

        Start-Sleep -Milliseconds 500
        $stillRunning = Get-Process -Id $Process.Id -ErrorAction SilentlyContinue
        if ($null -ne $stillRunning) {
            Write-Warning "El proceso $($Process.Id) sigue activo. Se reutilizará si continúa escuchando en el puerto."
            return $false
        }
        return $true
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
$homeUrl = "$registeredPublicBase/"
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

$serverPids = @(
    Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
)
$reuseExistingServer = $false
foreach ($serverPid in $serverPids) {
    $existingServer = Get-Process -Id ([int]$serverPid) -ErrorAction SilentlyContinue
    if ($null -eq $existingServer) {
        continue
    }
    if (@("python", "pythonw") -notcontains $existingServer.ProcessName.ToLowerInvariant()) {
        throw "El puerto 8765 está ocupado por un proceso que no pertenece a Python: $($existingServer.ProcessName)"
    }
    if (Stop-PergaminoProcess $existingServer) {
        Write-Host "Servidor anterior detenido para cargar la versión actual (PID $($existingServer.Id))."
    } else {
        $reuseExistingServer = $true
        $serverPid = [int]$existingServer.Id
        Write-Warning "No se pudo detener el servidor anterior. Se reutilizará el proceso que ya está escuchando."
    }
}
if ($serverPids.Count -gt 0) {
    Start-Sleep -Milliseconds 500
}

$serverConnection = @(Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue)
$serverProcess = $null
$serverPid = $null
if ($serverConnection.Count -eq 0) {
    # start /b desacopla el proceso del console host del lanzador. Esto evita
    # que cerrar la ventana negra envíe CTRL+C al servidor Uvicorn.
    $serverCommandLine = @(
        "/d",
        "/c",
        "start",
        '""',
        "/b",
        "`"$PythonPath`"",
        "-m",
        "uvicorn",
        "backend.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8765",
        ">",
        "`"$ServerLog`"",
        "2>",
        "`"$ServerErrorLog`""
    ) -join " "
    $serverProcess = Start-Process `
        -FilePath "cmd.exe" `
        -ArgumentList $serverCommandLine `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -PassThru

    for ($attempt = 0; $attempt -lt 20 -and $null -eq $serverPid; $attempt++) {
        Start-Sleep -Milliseconds 500
        $serverConnection = @(Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue)
        if ($serverConnection.Count -gt 0) {
            $serverPid = [int]$serverConnection[0].OwningProcess
        }
    }

    if ($null -eq $serverPid) {
        throw "El servidor no empezó a escuchar en el puerto 8765. Revisa $ServerErrorLog"
    }
    Write-Host "Servidor iniciado en segundo plano (PID $serverPid)."
} elseif ($reuseExistingServer) {
    $serverPid = [int]$serverConnection[0].OwningProcess
    Write-Host "Se reutiliza el servidor existente en segundo plano (PID $serverPid)."
} else {
    throw "No se pudo liberar el puerto 8765 para cargar la versión actual."
}

$tunnelProcess = $null
$publicPanelReady = $false
$tunnelMode = ""
if ($isTailscale) {
    Write-Host "Activando o reutilizando Tailscale Funnel..."
    & $TailscalePath funnel --bg "http://127.0.0.1:8765"
    $funnelExitCode = $LASTEXITCODE
    if ($funnelExitCode -ne 0) {
        throw "Tailscale no pudo activar Funnel. Inicia sesión y aprueba Funnel cuando lo solicite."
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
    server_pid = $serverPid
    tunnel_pid = if ($null -ne $tunnelProcess) { $tunnelProcess.Id } else { $null }
    tunnel_mode = $tunnelMode
}
$pidData | ConvertTo-Json | Set-Content -LiteralPath $PidFile -Encoding UTF8
Write-Host "URL pública: $homeUrl"
Start-Process $homeUrl
