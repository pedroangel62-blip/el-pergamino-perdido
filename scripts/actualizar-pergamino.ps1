$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) "pergamino-github-update"
$ZipPath = Join-Path $TempRoot "el-pergamino-perdido-main.zip"
$ExtractRoot = Join-Path $TempRoot "extraido"
$DownloadUrl = "https://github.com/pedroangel62-blip/el-pergamino-perdido/archive/refs/heads/main.zip"
$SourceRoot = Join-Path $ExtractRoot "el-pergamino-perdido-main"

$FilesToUpdate = @(
    "backend\instagram.py",
    "backend\main.py",
    "backend\manual\manual_maestro.txt",
    "tests\test_instagram.py",
    "tests\test_app.py",
    "README.md",
    ".env.example",
    "scripts\abrir-pergamino.ps1",
    "scripts\configurar-tailscale.ps1",
    "scripts\Configurar-Tailscale.cmd",
    "scripts\detener-pergamino.ps1",
    "scripts\Abrir-El-Pergamino.cmd",
    "scripts\Detener-El-Pergamino.cmd",
    "scripts\actualizar-pergamino.ps1",
    "scripts\Actualizar-El-Pergamino.cmd"
)

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

Remove-Item -LiteralPath $TempRoot -Recurse -Force
Write-Host "Actualización terminada. Se ha conservado el archivo .env y los datos locales."
