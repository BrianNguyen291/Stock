
@echo off
setlocal

set "APP_ROOT=%HOME%\site\wwwroot"
set "PYTHON_EXE=%APP_ROOT%\runtime\python.exe"
set "DB_PATH=%HOME%\data\stock\test.sqlite3"
set "LOG_DIR=%HOME%\data\stock\logs"

if not exist "%PYTHON_EXE%" (
    echo ERROR: Python runtime not found.
    exit /b 1
)

if /I not "%WEBSITE_SLOT_NAME%"=="test" (
    echo ERROR: This WebJob is restricted to the test slot.
    exit /b 1
)

cd /d "%APP_ROOT%"
if errorlevel 1 exit /b 1

"%PYTHON_EXE%" "%APP_ROOT%\update_data.py" --write-db --db "%DB_PATH%" --lookback-days 40 --attempts 3 --retry-seconds 60 --log-dir "%LOG_DIR%"

exit /b %ERRORLEVEL%
