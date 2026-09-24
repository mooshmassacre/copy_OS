@echo off
setlocal EnableExtensions DisableDelayedExpansion
title COPY_OS
rem SPDX-License-Identifier: MIT
rem Copyright (c) 2026 @moosmassacre ^<mooshmassacre@mail.com^>

rem pushd supports local folders, spaces, and UNC network paths.
pushd "%~dp0" >nul 2>&1
if errorlevel 1 goto folder_error

if not exist "copy_OS.py" goto script_missing
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
chcp 65001 >nul 2>&1

rem Probe a working interpreter instead of relying on PATH entries alone.
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
if not errorlevel 1 goto run_py
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
if not errorlevel 1 goto run_python
python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>&1
if not errorlevel 1 goto run_python3

echo Python 3.9 or newer was not found.
echo Install Python from https://www.python.org/downloads/windows/
echo Enable the Python launcher or add Python to PATH during installation.
echo Then double-click this file again.
goto failed

:run_py
py -3 "copy_OS.py"
goto finished

:run_python
python "copy_OS.py"
goto finished

:run_python3
python3 "copy_OS.py"
goto finished

:finished
set "COPY_OS_EXIT=%ERRORLEVEL%"
popd
if "%COPY_OS_EXIT%"=="0" exit /b 0
echo.
echo COPY_OS exited with code %COPY_OS_EXIT%.
pause
exit /b %COPY_OS_EXIT%

:script_missing
echo Could not find copy_OS.py beside this launcher.
echo Extract the complete project ZIP before running this file.
echo Keep start_copy_OS.bat, copy_OS.py and config.json in the same folder.
:failed
popd
pause
exit /b 1

:folder_error
echo Could not open the folder containing this launcher.
echo Check that the drive or network location is available.
pause
exit /b 1
