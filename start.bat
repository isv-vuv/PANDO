@echo off
setlocal
title PANDO

echo ======================================================================
echo  PANDO - Automated Startup Script
echo ======================================================================
echo.

set "SCRIPT_DIR=%~dp0"
set "REQ=%SCRIPT_DIR%requirements.txt"

:SEARCH_QGIS
set "QGIS_PYTHON="

:: Prefer an explicitly configured launcher.
if defined PANDO_QGIS_PYTHON if exist "%PANDO_QGIS_PYTHON%" (
    call :TRY_QGIS "%PANDO_QGIS_PYTHON%"
    if defined QGIS_PYTHON goto :FOUND
)

:: Search per-user and system-wide OSGeo4W installations.
for %%D in ("%LOCALAPPDATA%\Programs\OSGeo4W" "%SystemDrive%\OSGeo4W" "%SystemDrive%\OSGeo4W64") do (
    if exist "%%~fD\bin\python-qgis.bat" (
        call :TRY_QGIS "%%~fD\bin\python-qgis.bat"
        if defined QGIS_PYTHON goto :FOUND
    )
)

:: Search a standalone QGIS installation installed by winget.
for /d %%D in ("%ProgramFiles%\QGIS*") do (
    if exist "%%~fD\bin\python-qgis.bat" (
        call :TRY_QGIS "%%~fD\bin\python-qgis.bat"
        if defined QGIS_PYTHON goto :FOUND
    )
)

:: Also accept a QGIS launcher already available through PATH.
for /f "delims=" %%I in ('where python-qgis.bat 2^>nul') do (
    call :TRY_QGIS "%%~fI"
    if defined QGIS_PYTHON goto :FOUND
)

goto :NOT_FOUND

:NOT_FOUND
echo [WARNING] A QGIS 4 Python environment was not found.
echo.
echo QGIS 4 can be installed as a standalone application via winget.
echo No separate OSGeo4W installation is required.
echo.
where winget >nul 2>&1
if errorlevel 1 (
    echo [ERROR] winget is not available. Install QGIS 4 manually from qgis.org.
    pause
    exit /b 1
)

set /p INSTALL_CHOICE="Would you like to install QGIS 4 automatically via winget now? (Y/N): "
if /i "%INSTALL_CHOICE%"=="Y" goto :DO_INSTALL
if /i "%INSTALL_CHOICE%"=="YES" goto :DO_INSTALL
goto :NO_INSTALL

:DO_INSTALL
echo.
echo Starting standalone QGIS 4 installation via winget...
winget install --exact --id OSGeo.QGIS --accept-package-agreements --accept-source-agreements
if errorlevel 1 (
    echo.
    echo [ERROR] Installation failed or was canceled.
    pause
    exit /b 1
)

echo.
echo Installation completed successfully. Rescanning for QGIS 4...
echo.
goto :SEARCH_QGIS

:NO_INSTALL
echo.
echo Please install QGIS 4 manually or set PANDO_QGIS_PYTHON to its Python launcher.
pause
exit /b 1

:FOUND
echo [OK] QGIS 4 Python environment found:
echo      "%QGIS_PYTHON%"
echo.

echo [1/2] Verifying Python dependencies...
call "%QGIS_PYTHON%" "%SCRIPT_DIR%core\app\app_core\check_deps.py" "%REQ%"
if errorlevel 1 (
    echo.
    echo [ERROR] Error occurred during installation of Python requirements.
    pause
    exit /b 1
)

echo.
echo [2/2] Starting PANDO Application (MainQt.py)...
call "%QGIS_PYTHON%" "%SCRIPT_DIR%MainQt.py"
set "APP_EXIT_CODE=%ERRORLEVEL%"

if not "%APP_EXIT_CODE%"=="0" echo [ERROR] PANDO exited with code %APP_EXIT_CODE%.
pause
exit /b %APP_EXIT_CODE%

:TRY_QGIS
:: Keep environment changes from rejected launchers out of subsequent checks.
setlocal
call "%~1" -c "from qgis.PyQt import QtCore, QtGui, QtWidgets; from qgis.core import Qgis; raise SystemExit(0 if Qgis.QGIS_VERSION_INT >= 40000 else 1)" >nul 2>&1
if errorlevel 1 (
    echo [WARNING] Skipping launcher without a working QGIS 4 environment: "%~1"
    endlocal
    exit /b 0
)
endlocal & set "QGIS_PYTHON=%~1"
exit /b 0
