@echo off
setlocal
set "HIA_SOURCE_ROOT=%~dp0"

"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -Sta -ExecutionPolicy Bypass -File "%HIA_SOURCE_ROOT%scripts\hia-launcher.ps1" %*
set "HIA_LAUNCHER_EXIT=%ERRORLEVEL%"

if not "%HIA_LAUNCHER_EXIT%"=="0" (
    echo.
    echo Big-Chicken HIA Launcher exited with code %HIA_LAUNCHER_EXIT%.
    pause
)

exit /b %HIA_LAUNCHER_EXIT%
