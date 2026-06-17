@echo off
REM Doble-clic para cargar un mes. Pregunta el mes y corre cargar_mes.ps1.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0cargar_mes.ps1"
echo.
pause
