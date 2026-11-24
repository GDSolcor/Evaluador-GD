@echo off
title Evaluador GD - Solcor
cd /d "%~dp0"
echo Iniciando el Evaluador GD...
echo Se abrira en el navegador. Cierra esta ventana para apagarlo.
echo.

rem 1) Python portatil dentro de la carpeta (sin instalar nada en el PC)
if exist "%~dp0python\python.exe" (
  "%~dp0python\python.exe" servidor.py %*
  goto fin
)
rem 2) Python instalado en el PC
where python >nul 2>nul
if %errorlevel%==0 (
  python servidor.py %*
  goto fin
)
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 servidor.py %*
  goto fin
)
echo No se encontro Python en este PC.
echo.
echo Opciones:
echo   a) Instalar Python 3.10 o superior desde el Portal de empresa / Microsoft Store
echo      (marcar "Add python.exe to PATH") y volver a abrir este archivo.
echo   b) Pedir la carpeta "python" portatil y copiarla junto a este archivo.
echo.
pause
exit /b 1

:fin
if errorlevel 1 pause
