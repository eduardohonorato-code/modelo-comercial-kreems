@echo off
REM Doble-clic para actualizar el plan de stock de temporada (proyecciones).
REM Solo lee ventas de Supabase; no toca el ETL diario ni escribe en la base.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0actualizar_plan_stock.ps1"
echo.
pause
