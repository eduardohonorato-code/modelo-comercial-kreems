<#
    actualizar_plan_stock.ps1 - Refresca el plan de stock de temporada.

        .\actualizar_plan_stock.ps1

    Pasos:
      1. reportes\plan_stock_temporada.py  - recalcula demanda y stock desde Supabase
      2. recalculo de formulas con Excel   - para que el archivo se abra con valores

    Solo LEE ventas de Supabase (no escribe nada en la base) y genera el Excel
    en la carpeta de Drive. Es independiente del ETL diario.

    El corte entre meses REALES y PROYECTADOS se detecta solo: no hay que
    configurar nada, basta correrlo despues de que cierre el mes.
#>

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$destino = "G:\Mi unidad\Reportes Financieros\Entregables Valorizacion\Entregables Due Diligence\Proyecciones\plan_stock_temporada_2026_2027.xlsx"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  ACTUALIZAR PLAN DE STOCK - temporada 2026/2027" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# --- Aviso si el Excel esta abierto ---
if (Test-Path $destino) {
    try {
        $fs = [System.IO.File]::Open($destino, 'Open', 'ReadWrite', 'None')
        $fs.Close()
    } catch {
        Write-Host "  [!] El Excel del plan esta ABIERTO." -ForegroundColor Yellow
        Write-Host "      Cierralo y vuelve a correr, o se guardara una copia en reportes\." -ForegroundColor Yellow
        Write-Host ""
    }
}

# --- Elegir el python correcto (el de pythoncore tiene las dependencias) ---
$py = "C:\Users\Evelyn Novoa\AppData\Local\Python\pythoncore-3.14-64\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

Write-Host "[1/2] Recalculando demanda y stock desde Supabase..." -ForegroundColor Green
# Se captura la salida para saber QUE archivo se escribio (si Drive estaba
# bloqueado, el bueno es la copia local), sin dejar de mostrarla en pantalla.
& $py "reportes\plan_stock_temporada.py" 2>&1 |
    Tee-Object -Variable lineas |
    ForEach-Object { if ("$_" -notlike "##ARCHIVO##*") { Write-Host "$_" } }
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "*** Fallo la generacion. Revisa el mensaje de arriba. ***" -ForegroundColor Red
    exit 1
}

# --- Recalcular formulas con Excel ---
Write-Host ""
Write-Host "[2/2] Recalculando formulas con Excel..." -ForegroundColor Green
$marca = $lineas | Where-Object { "$_" -like "##ARCHIVO##*" } | Select-Object -Last 1
if ($marca) {
    $archivo = ("$marca" -replace '^##ARCHIVO##', '').Trim()
} else {
    $archivo = $destino
}
if (-not (Test-Path $archivo)) {
    $archivo = Join-Path $PSScriptRoot "reportes\plan_stock_temporada_2026_2027.xlsx"
}
$xl = New-Object -ComObject Excel.Application
$xl.DisplayAlerts = $false
try {
    $wb = $xl.Workbooks.Open($archivo)
    $xl.CalculateFullRebuild()
    $wb.Save()
    $wb.Close($true)
    Write-Host "      formulas recalculadas OK" -ForegroundColor Green
} catch {
    Write-Host "      *** No se pudo recalcular: $($_.Exception.Message)" -ForegroundColor Red
} finally {
    $xl.Quit()
    [System.Runtime.Interopservices.Marshal]::ReleaseComObject($xl) | Out-Null
}

Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  LISTO" -ForegroundColor Cyan
Write-Host "  Archivo: $archivo"
Write-Host "  Revisa la hoja Parametros: ahi dice hasta que mes son datos REALES."
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
