$ErrorActionPreference = "Stop"

$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) "el-pergamino-perdido"
$PidFile = Join-Path $TempRoot "processes.json"

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
            Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $command) -Verb RunAs -Wait -ErrorAction Stop | Out-Null
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

if (-not (Test-Path -LiteralPath $PidFile -PathType Leaf)) {
    Write-Host "No hay procesos del lanzador registrados."
    exit 0
}

$pidData = Get-Content -LiteralPath $PidFile -Raw | ConvertFrom-Json
foreach ($entry in @(
    @{ Name = "servidor"; Id = $pidData.server_pid; Allowed = @("python", "pythonw") },
    @{ Name = "Cloudflare"; Id = $pidData.tunnel_pid; Allowed = @("cloudflared") }
)) {
    if ($null -eq $entry.Id) {
        continue
    }
    $process = Get-Process -Id ([int]$entry.Id) -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        continue
    }
    if ($entry.Allowed -notcontains $process.ProcessName.ToLowerInvariant()) {
        Write-Warning "No detengo el PID $($entry.Id): ya pertenece a otro proceso."
        continue
    }
    if (Stop-PergaminoProcess $process) {
        Write-Host "$($entry.Name) detenido (PID $($process.Id))."
    } else {
        Write-Warning "No se pudo detener $($entry.Name) (PID $($process.Id))."
    }
}

Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
