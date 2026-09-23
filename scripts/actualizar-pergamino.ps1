$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) "pergamino-github-update"
$ZipPath = Join-Path $TempRoot "el-pergamino-perdido-main.zip"
$ExtractRoot = Join-Path $TempRoot "extraido"
$DownloadUrl = "https://github.com/pedroangel62-blip/el-pergamino-perdido/archive/refs/heads/main.zip"
$SourceRoot = Join-Path $ExtractRoot "el-pergamino-perdido-main"

$FilesToUpdate = @(
    "backend\busqueda_imagenes.py",
    "backend\instagram.py",
    "backend\main.py",
    "backend\produccion.py",
    "backend\templates\index.html",
    "backend\templates\produccion.html",
    "backend\manual\manual_maestro.txt",
    "tests\test_busqueda_imagenes.py",
    "tests\test_instagram.py",
    "tests\test_app.py",
    "tests\test_produccion.py",
    "README.md",
    ".env.example",
    "scripts\abrir-pergamino.ps1",
    "scripts\configurar-tailscale.ps1",
    "scripts\Configurar-Tailscale.cmd",
    "scripts\detener-pergamino.ps1",
    "scripts\Abrir-El-Pergamino.cmd",
    "scripts\Detener-El-Pergamino.cmd",
    "scripts\actualizar-pergamino.ps1",
    "scripts\Actualizar-El-Pergamino.cmd",
    "scripts\Abrir-El-Pergamino.vbs",
    "scripts\Actualizar-El-Pergamino.vbs",
    "scripts\Detener-El-Pergamino.vbs"
)

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

if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) {
    throw "No encuentro la carpeta del proyecto: $ProjectRoot"
}

if (Test-Path -LiteralPath $TempRoot) {
    Remove-Item -LiteralPath $TempRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $ExtractRoot -Force | Out-Null

Write-Host "Descargando la versión actual de GitHub..."
Invoke-WebRequest -Uri $DownloadUrl -OutFile $ZipPath -UseBasicParsing
Expand-Archive -LiteralPath $ZipPath -DestinationPath $ExtractRoot -Force

if (-not (Test-Path -LiteralPath $SourceRoot -PathType Container)) {
    throw "La descarga no contiene la carpeta esperada del proyecto."
}

foreach ($relativePath in $FilesToUpdate) {
    $sourcePath = Join-Path $SourceRoot $relativePath
    $targetPath = Join-Path $ProjectRoot $relativePath
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
        throw "Falta en GitHub el archivo esperado: $relativePath"
    }
    $targetDirectory = Split-Path -Parent $targetPath
    New-Item -ItemType Directory -Path $targetDirectory -Force | Out-Null
    Copy-Item -LiteralPath $sourcePath -Destination $targetPath -Force
    Write-Host "Actualizado: $relativePath"
}

$servidorNoDetenido = $false
$serverConnections = @(Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue)
foreach ($connection in $serverConnections) {
    $process = Get-Process -Id $connection.OwningProcess -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        continue
    }
    if (@("python", "pythonw") -notcontains $process.ProcessName.ToLowerInvariant()) {
        Write-Warning "No detengo el proceso del puerto 8765 porque no es Python: $($process.ProcessName)"
        continue
    }
    if (-not (Stop-PergaminoProcess $process)) {
        $servidorNoDetenido = $true
        Write-Warning "No se pudo detener el servidor antiguo (PID $($process.Id)). La actualización de archivos continúa; el lanzador abrirá la versión actual en otro puerto."
        continue
    }
    Write-Host "Servidor detenido para cargar la versión nueva (PID $($process.Id))."
}

Remove-Item -LiteralPath $TempRoot -Recurse -Force
if ($servidorNoDetenido) {
    Write-Warning "El servidor antiguo permanece activo. Ejecuta Abrir-El-Pergamino.vbs: la versión actual se iniciará en un puerto alternativo y se conectará al mismo dominio público."
}
Write-Host "Actualización terminada. Se ha conservado el archivo .env y los datos locales."
