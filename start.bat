@echo off
setlocal enabledelayedexpansion
title PANDO

echo ======================================================================
echo  PANDO - Automated Startup Script
echo ======================================================================
echo.

:SEARCH_QGIS
set "QGIS_BAT="

:: 1. Check LOCALAPPDATA OSGeo4W
if exist "%LOCALAPPDATA%\Programs\OSGeo4W\bin\python-qgis.bat" (
    set "QGIS_BAT=%LOCALAPPDATA%\Programs\OSGeo4W\bin\python-qgis.bat"
    goto :FOUND
)

:: 2. Check C:\OSGeo4W
if exist "C:\OSGeo4W\bin\python-qgis.bat" (
    set "QGIS_BAT=C:\OSGeo4W\bin\python-qgis.bat"
    goto :FOUND
)

:: 3. Check C:\OSGeo4W64
if exist "C:\OSGeo4W64\bin\python-qgis.bat" (
    set "QGIS_BAT=C:\OSGeo4W64\bin\python-qgis.bat"
    goto :FOUND
)

:: 4. Search in Program Files for QGIS 4 / QGIS
for /d %%D in ("C:\Program Files\QGIS*") do (
    if exist "%%D\bin\python-qgis.bat" (
        set "QGIS_BAT=%%D\bin\python-qgis.bat"
        goto :FOUND
    )
)

:: 5. Search in PATH
for /f "tokens=*" %%i in ('where python-qgis.bat 2^>nul') do (
    set "QGIS_BAT=%%i"
    goto :FOUND
)

:NOT_FOUND
echo [WARNING] QGIS 4 environment (python-qgis.bat) was not found.
echo.
echo QGIS 4 does not appear to be installed or is located in a non-standard directory.
echo (Note: If you currently have QGIS 3 installed, please note that QGIS 4 is required).
echo.
set /p INSTALL_CHOICE="Would you like to install QGIS 4 automatically via winget now? (Y/N): "
if /i "%INSTALL_CHOICE%"=="Y" goto :DO_INSTALL
if /i "%INSTALL_CHOICE%"=="YES" goto :DO_INSTALL
goto :NO_INSTALL

:DO_INSTALL
echo.
echo Starting QGIS 4 installation via winget...
winget install -e --id OSGeo.QGIS
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Installation failed or was canceled.
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo Installation completed successfully. Rescanning for QGIS 4 environment...
echo.
goto :SEARCH_QGIS

:NO_INSTALL
echo.
echo Please install QGIS 4 manually (for example using OSGEO4W) or ensure python-qgis.bat is available in your PATH.
pause
exit /b 1

:FOUND
echo [OK] QGIS 4 Python environment found:
echo      "%QGIS_BAT%"
echo.

set "STAMP=%~dp0.deps_ok"
set "REQ=%~dp0requirements.txt"
set "NEED_DEPS=0"

if not exist "%STAMP%" set "NEED_DEPS=1"
if exist "%REQ%" (
    if not exist "%STAMP%" (
        set "NEED_DEPS=1"
    ) else (
        for /f %%i in ('powershell -NoProfile -Command "(Get-Item '%REQ%').LastWriteTime -gt (Get-Item '%STAMP%' -ErrorAction SilentlyContinue).LastWriteTime"') do (
            if /i "%%i"=="True" set "NEED_DEPS=1"
        )
    )
)

if "%NEED_DEPS%"=="1" (
    echo [1/2] Verifying Python dependencies...
    if exist "%REQ%" (
        call "%QGIS_BAT%" "%~dp0core\app\app_core\check_deps.py" "%REQ%"
        if !ERRORLEVEL! NEQ 0 (
            echo.
            echo [ERROR] Error occurred during installation of Python requirements.
            pause
            exit /b !ERRORLEVEL!
        )
        type nul > "%STAMP%"
    )
) else (
    echo [OK] Python dependencies verified.
)

echo.
echo Starting PANDO Application (MainQt.py)...
call "%QGIS_BAT%" "%~dp0MainQt.py"

pause