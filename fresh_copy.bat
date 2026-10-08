@echo off
rem Copies the program (not the data file) from this folder to a fresh local folder.
rem Usage: fresh_copy.bat [destination]   (default: %LOCALAPPDATA%\NewsletterTracker\app)
setlocal
set "SRC=%~dp0"
set "DEST=%~1"
if "%DEST%"=="" set "DEST=%LOCALAPPDATA%\NewsletterTracker\app"
robocopy "%SRC%." "%DEST%" /E /NFL /NDL /NJH /NJS /NP ^
  /XD .git __pycache__ tests backups .pytest_cache ^
  /XF .git *.db *.db-journal *.lock *.csv *.xlsx *.pdf config*.json msal_cache* .env SCOPE.md .gitignore pytest.ini requirements-dev.txt
if errorlevel 8 (echo Copy failed. & exit /b 1)
echo.
echo Program copied to %DEST%
echo Start it with run.bat in that folder. The first time, choose the shared data file.
exit /b 0
