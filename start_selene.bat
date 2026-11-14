@echo off
setlocal
title Selene

echo Iniciando Selene...
echo.

tasklist /FI "IMAGENAME eq ollama.exe" 2>NUL | find /I "ollama.exe" >NUL
if errorlevel 1 (
    echo Iniciando Ollama...
    start "" "%LOCALAPPDATA%\Programs\Ollama\ollama app.exe"
    timeout /t 6 /nobreak >nul
) else (
    echo Ollama ja esta rodando.
)

cd /d "%~dp0"
echo Iniciando o servidor da Selene (ou confirmando que ja esta rodando)...
".venv\Scripts\jarvis.exe" start
timeout /t 8 /nobreak >nul

start "" "http://127.0.0.1:8000"

echo.
echo Pronto! Selene esta no ar.
timeout /t 3 >nul
