param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [ValidatePattern('^[a-zA-Z0-9_-]+$')]
    [string]$ProyectoId = "pergamino-20260912-231448-kubrick-grabo-el-alunizaje",
    [switch]$NoAbrir
)

# Solo lectura del proyecto. No detiene procesos ni modifica permisos o archivos.
$ErrorActionPreference = "Stop"
$RutaVideo = Join-Path $ProjectRoot "backend\proyectos\$ProyectoId\video_borrador.mp4"
$Informe = [System.Collections.Generic.List[string]]::new()
$Informe.Add("Diagnostico del video - $(Get-Date -Format o)")
$Informe.Add("Usuario: $([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)")
$Informe.Add("Archivo: $RutaVideo")

try {
    $Archivo = Get-Item -LiteralPath $RutaVideo -Force
    $Informe.Add("Tamano: $($Archivo.Length) bytes; atributos: $($Archivo.Attributes)")
    $Informe.Add("Modificado: $($Archivo.LastWriteTime.ToString('o'))")
} catch {
    $Informe.Add("No se pudo consultar el archivo: $($_.Exception.Message)")
}

foreach ($RutaAcl in @($RutaVideo, (Split-Path -Parent $RutaVideo))) {
    try {
        $Acl = Get-Acl -LiteralPath $RutaAcl
        $Informe.Add("Permisos de: $RutaAcl; propietario: $($Acl.Owner)")
        $Informe.Add(($Acl.Access | Format-Table IdentityReference,FileSystemRights,AccessControlType,IsInherited -AutoSize | Out-String))
    } catch {
        $Informe.Add("No se pudieron consultar los permisos: $($_.Exception.Message)")
    }
}

$Flujo = $null
try {
    $Compartir = [System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete
    $Flujo = [System.IO.File]::Open($RutaVideo, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, $Compartir)
    $Byte = $Flujo.ReadByte()
    $Informe.Add("Lectura de Windows: CORRECTA; primer byte=$Byte")
} catch {
    $Excepcion = $_.Exception
    while ($Excepcion.InnerException) { $Excepcion = $Excepcion.InnerException }
    $Codigo = $Excepcion.HResult -band 0xFFFF
    $Informe.Add("Lectura de Windows: FALLO; codigo=$Codigo; $($Excepcion.Message)")
    if ($Codigo -eq 32) {
        $Informe.Add("Windows informa de una incompatibilidad al compartir el archivo con otro proceso.")
    } elseif ($Codigo -eq 5) {
        $Informe.Add("Windows informa de acceso denegado. Revisar los permisos y propietario indicados arriba.")
    }
} finally {
    if ($null -ne $Flujo) { $Flujo.Dispose() }
}

$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $Python) {
    $PreferenciaAnterior = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $LecturaPython = & $Python -c "import sys; f=open(sys.argv[1],'rb'); print('Lectura Python correcta:', len(f.read(1))); f.close()" $RutaVideo 2>&1 | Out-String
        $CodigoPython = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreferenciaAnterior
    }
    $Informe.Add("Comprobacion con Python del proyecto (codigo ${CodigoPython}): $LecturaPython")
}

try {
    $Version = Invoke-RestMethod -Uri "http://127.0.0.1:8765/api/version" -TimeoutSec 3
    $Informe.Add("Version activa en puerto 8765: $($Version.version)")
} catch {
    $Informe.Add("No se obtuvo la version en el puerto 8765: $($_.Exception.Message)")
}
$Informe.Add("Procesos activos relacionados (esta lista no identifica por si sola quien bloquea el archivo):")
$Informe.Add((Get-Process -Name python,pythonw,ffmpeg,ffprobe,OneDrive -ErrorAction SilentlyContinue |
    Select-Object Id,ProcessName | Format-Table -AutoSize | Out-String))

$CarpetaInforme = Join-Path ([System.IO.Path]::GetTempPath()) "el-pergamino-perdido"
New-Item -ItemType Directory -Path $CarpetaInforme -Force | Out-Null
$RutaInforme = Join-Path $CarpetaInforme "diagnostico-video.txt"
$Informe | Set-Content -LiteralPath $RutaInforme -Encoding UTF8
Write-Host "Diagnostico terminado: $RutaInforme"
if (-not $NoAbrir) {
    Start-Process -FilePath "notepad.exe" -ArgumentList @('"' + $RutaInforme + '"')
}
