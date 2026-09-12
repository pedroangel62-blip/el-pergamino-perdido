$ErrorActionPreference = "Stop"

$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) "el-pergamino-perdido"
$PidFile = Join-Path $TempRoot "processes.json"

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
    Stop-Process -Id $process.Id -Force
    Write-Host "$($entry.Name) detenido (PID $($process.Id))."
}

Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
