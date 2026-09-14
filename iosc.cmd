@echo off
setlocal enabledelayedexpansion

set "ROOT=%~dp0"
set "VENV=%ROOT%.venv"
set "IOSC=%VENV%\Scripts\iosc.exe"

if not exist "%IOSC%" (
    call :bootstrap || exit /b 1
)

"%IOSC%" %*
exit /b %ERRORLEVEL%

:bootstrap
set "LAUNCHER="
py -3 --version >nul 2>&1 && set "LAUNCHER=py -3"
if not defined LAUNCHER (
    python --version >nul 2>&1 && set "LAUNCHER=python"
)
if not defined LAUNCHER (
    echo error Python 3.11 or newer was not found on PATH.
    echo Install it from https://www.python.org/downloads/ and run this again.
    exit /b 1
)

echo Setting up %VENV%
%LAUNCHER% -m venv "%VENV%"
if errorlevel 1 (
    echo error could not create the virtual environment at %VENV%
    exit /b 1
)

echo Installing iosc
"%VENV%\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -e "%ROOT%."
if errorlevel 1 (
    echo error could not install iosc into %VENV%
    exit /b 1
)

if not exist "%IOSC%" (
    echo error install finished but %IOSC% is missing
    exit /b 1
)
echo Ready.
exit /b 0
