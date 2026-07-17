@echo off
title Governed Validation System Launcher
echo ======================================================================
echo   Governed Validation System - One-Click Launcher
echo ======================================================================
echo.

:: Verify python is installed in PATH
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python was not found in your system PATH.
    echo Please install Python 3 and ensure "Add Python to PATH" is checked during setup.
    echo.
    pause
    exit /b 1
)

:: Run the python launch bootstrap script
python "%~dp0launch.py"

:: Pause if the execution crashed or exited with an error
if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] Launcher exited with code %ERRORLEVEL%.
    pause
)
