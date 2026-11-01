@echo off
rem Template: run the serial logger on a fixed COM port.
rem Copy this file and edit the settings below. Extra options can be added on
rem the command line, e.g.  run_serial.bat --filter "ERROR" --no-keywords
setlocal

rem ---- Settings -------------------------------------------------------
set COM_PORT=COM5
set BAUD=115200
set LOG_DIR=logs
rem Line ending sent when you type: lf, cr, crlf or none
set EOL=cr
rem ---------------------------------------------------------------------

cd /d "%~dp0"

python main.py --port %COM_PORT% --baud %BAUD% --log-dir "%LOG_DIR%" --eol %EOL% %*
set EXITCODE=%ERRORLEVEL%

if not "%EXITCODE%"=="0" if not "%EXITCODE%"=="130" (
    echo.
    echo Logger exited with error %EXITCODE%.
    pause
)
endlocal & exit /b %EXITCODE%
