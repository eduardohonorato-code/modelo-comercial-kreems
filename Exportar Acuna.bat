@echo off
REM Doble-clic para exportar el historico de Acuna (carga unica para el SaaS).
REM Deja los archivos en exports\acuna_AAAAMMDD\ y un .zip. Solo lee la base.
cd /d "%~dp0"
set "PY=C:\Users\Evelyn Novoa\AppData\Local\Python\pythoncore-3.14-64\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -m etl.exportar_acuna
echo.
pause
