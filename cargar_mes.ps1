<#
    cargar_mes.ps1 - Carga completa de un mes (Kreems).

    Hace TODO el pipeline en orden, solo le pasas el mes:

        .\cargar_mes.ps1 2026-06
        .\cargar_mes.ps1            (pregunta el mes; sugiere el actual)

    Pasos:
      1. organizar         - archiva lo que dejaste en inbox/acuna y inbox/despachos
      2. run_autoventa_api - pedidos (API)
      3. run_etl           - Acuna + despachos (Excel)
      4. run_obuma_api     - Gran Natural ventas + maquinas (toman estado de despachos)

    ANTES de correrlo: arrastra el reporte de Obuma Acuna a inbox\acuna\ y el
    detalle de despachos de Autoventa a inbox\despachos\ (con el nombre que tengan).
#>
param(
    [string]$Periodo
)

$ErrorActionPreference = "Stop"

# --- Ubicarse en la carpeta del proyecto (donde esta este script) ---
Set-Location -Path $PSScriptRoot

# --- Pedir el mes si no se paso como argumento ---
if (-not $Periodo) {
    $sugerido = (Get-Date).ToString("yyyy-MM")
    $Periodo  = Read-Host "Que mes cargar? (AAAA-MM) [$sugerido]"
    if (-not $Periodo) { $Periodo = $sugerido }
}

# --- Validar formato AAAA-MM ---
if ($Periodo -notmatch '^\d{4}-\d{2}$') {
    Write-Host "Periodo invalido: '$Periodo'. Formato esperado AAAA-MM (ej. 2026-06)." -ForegroundColor Red
    exit 1
}

# --- Elegir el python correcto (el de pythoncore tiene las dependencias) ---
$py = "C:\Users\Evelyn Novoa\AppData\Local\Python\pythoncore-3.14-64\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

# --- Helper: corre un paso y aborta si falla ---
function Invoke-Paso {
    param([string]$Titulo, [string[]]$PyArgs)
    Write-Host ""
    Write-Host ("=" * 64) -ForegroundColor Cyan
    Write-Host "  $Titulo" -ForegroundColor Cyan
    Write-Host ("=" * 64) -ForegroundColor Cyan
    & $py $PyArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "FALLO: $Titulo (codigo $LASTEXITCODE). Se detiene la carga." -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

Write-Host "Cargando periodo $Periodo ..." -ForegroundColor Green

Invoke-Paso "1/4  Organizar descargas (inbox -> data/mensual)" @("-m", "etl.organizar", "--periodo", $Periodo)

Invoke-Paso "2/4  Autoventa API (pedidos)" @("-m", "etl.run_autoventa_api", "--periodo", $Periodo)

Invoke-Paso "3/4  ETL Excel (Acuna + despachos)" @("-m", "etl.run_etl", "--periodo", $Periodo)

Invoke-Paso "4/4  Obuma API (Gran Natural ventas + maquinas)" @("-m", "etl.run_obuma_api", "--periodo", $Periodo)

Write-Host ""
Write-Host ("=" * 64) -ForegroundColor Green
Write-Host "  LISTO - periodo $Periodo cargado correctamente." -ForegroundColor Green
Write-Host ("=" * 64) -ForegroundColor Green
