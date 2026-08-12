@echo off
REM Doble-clic para actualizar el plan de stock de temporada (ene-2026 a abr-2027).
REM Lee lo que haya cargado en Supabase y mueve solo el corte real/proyectado.
REM Correr DESPUES de cargar el mes que cerro (Cargar mes.bat).
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo   ACTUALIZAR PLAN DE STOCK - temporada 2026/2027
echo ============================================================
echo.
echo Cierra el Excel del plan de stock si lo tienes abierto.
pause

echo.
echo [1/2] Recalculando demanda y stock desde Supabase...
python "reportes\plan_stock_temporada.py"
if errorlevel 1 (
    echo.
    echo *** Fallo la generacion. Revisa el mensaje de arriba. ***
    pause
    exit /b 1
)

echo.
echo [2/2] Recalculando formulas con Excel...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$p='G:\Mi unidad\Reportes Financieros\Entregables Valorizacion\Entregables Due Diligence\Proyecciones\plan_stock_temporada_2026_2027.xlsx';" ^
  "if(-not (Test-Path $p)){$p=Join-Path $PWD 'reportes\plan_stock_temporada_2026_2027.xlsx'};" ^
  "$xl=New-Object -ComObject Excel.Application; $xl.DisplayAlerts=$false;" ^
  "try{$wb=$xl.Workbooks.Open($p); $xl.CalculateFullRebuild(); $wb.Save(); $wb.Close($true); Write-Host '    formulas recalculadas OK'}" ^
  "catch{Write-Host '    *** No se pudo recalcular:' $_.Exception.Message}" ^
  "finally{$xl.Quit(); [System.Runtime.Interopservices.Marshal]::ReleaseComObject($xl)|Out-Null}"

echo.
echo ============================================================
echo   LISTO. Abre el archivo y revisa la hoja Parametros:
echo   ahi dice hasta que mes son datos REALES.
echo ============================================================
echo.
pause
