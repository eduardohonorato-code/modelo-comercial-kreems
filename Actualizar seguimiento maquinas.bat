@echo off
REM Doble-clic cada lunes despues de la reunion de gerencia.
REM Actualiza en Drive el reporte de seguimiento comercial de maquinas
REM (rechazos sin retomar, reingresos sin cerrar y pedidos sin gestionar),
REM conservando lo que comercial escribio en las columnas amarillas.
REM Solo lee Supabase; no toca el ETL ni escribe en la base.
cd /d "%~dp0"
set PY=C:\Users\Evelyn Novoa\AppData\Local\Python\pythoncore-3.14-64\python.exe
if not exist "%PY%" set PY=python
"%PY%" "reportes\seguimiento_comercial_maquinas.py"
echo.
pause
