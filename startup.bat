@echo off
REM ===================================================================
REM  Dynamic Pricing Engine - one-click startup (Windows)
REM  FR-074, FR-075, FR-076
REM
REM  User-space only: no Docker, no admin rights, no system services.
REM  Each service gets its own visible console window and dies when that
REM  window is closed.
REM
REM  Usage:  startup.bat            start, refusing to run if a port is taken
REM          startup.bat /force     stop whatever holds our ports, then start
REM
REM  Why the windows are visible and stay open on exit: a service that fails
REM  to bind exits in well under a second. Launched minimised and without a
REM  host shell, its console closes with it, so the error is never seen -- and
REM  because a stale listener still answers /health, the launcher reports
REM  "healthy" and carries on against a process running old code. Both halves
REM  of that are fixed below: ports are checked before launching, and every
REM  window is hosted by cmd /k so a crash leaves its message on screen.
REM ===================================================================

setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "FORCE="
if /i "%~1"=="/force" set "FORCE=1"

echo.
echo   Dynamic Pricing Engine
echo   ----------------------------------------------------------------

REM --- Python present? ------------------------------------------------
where python >nul 2>&1
if errorlevel 1 (
    echo   [FAIL] Python not found on PATH.
    echo          Install Python 3.12 and re-run.
    exit /b 1
)

REM --- Virtual environment --------------------------------------------
if not exist ".venv\Scripts\python.exe" (
    echo   [ .. ] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo   [FAIL] Could not create .venv
        exit /b 1
    )
)
set "PY=%CD%\.venv\Scripts\python.exe"

REM --- Dependencies ----------------------------------------------------
"%PY%" -c "import fastapi, numpy, scipy" >nul 2>&1
if errorlevel 1 (
    echo   [ .. ] Installing dependencies ^(first run, this takes a few minutes^)...
    "%PY%" -m pip install --upgrade pip --quiet
    "%PY%" -m pip install -r requirements.txt --quiet
    if errorlevel 1 (
        echo   [FAIL] Dependency installation failed.
        exit /b 1
    )
)

REM --- Environment file -------------------------------------------------
if not exist ".env" (
    echo   [ .. ] Creating .env from .env.example
    copy /y .env.example .env >nul
    echo   [WARN] Review .env before relying on LLM features.
)

set "PYTHONPATH=%CD%"

REM --- Seed the commerce database if empty -------------------------------
if not exist "data\commerce.db" (
    echo   [ .. ] Seeding synthetic retail dataset...
    "%PY%" -m commerce.seed
    if errorlevel 1 (
        echo   [FAIL] Seeding failed.
        exit /b 1
    )
)

REM --- Reclaim our ports -------------------------------------------------
REM A leftover service from a previous run answers /health perfectly well, so
REM without this the launcher adopts it, reports success, and leaves you
REM talking to old code while the newly-started process dies unseen on a bind
REM error. Detected here rather than tolerated.
echo   [ .. ] Checking ports 8001, 8000, 5173...
set "BUSY="
call :portcheck 8001 "Commerce Service"
call :portcheck 8000 "Pricing Platform"
call :portcheck 5173 "Web UI"

if defined BUSY (
    if defined FORCE (
        echo   [ .. ] /force given - stopping the processes holding those ports...
        call :portkill 8001
        call :portkill 8000
        call :portkill 5173
        timeout /t 2 /nobreak >nul
        set "BUSY="
        call :portcheck 8001 "Commerce Service"
        call :portcheck 8000 "Pricing Platform"
        call :portcheck 5173 "Web UI"
        if defined BUSY (
            echo   [FAIL] Could not free the ports. Stop those processes manually.
            exit /b 1
        )
        echo   [ OK ] Ports reclaimed.
    ) else (
        echo.
        echo   [FAIL] Something is already listening on a port this stack needs.
        echo          Almost always a previous run whose windows were left open.
        echo.
        echo          Re-run as:  startup.bat /force
        echo          to stop those processes and start clean.
        exit /b 1
    )
)

REM --- Pre-flight (pre-start: ports should be FREE) ----------------------
echo   [ .. ] Running pre-flight checks...
"%PY%" -m pricing.scripts.preflight
if errorlevel 1 (
    echo.
    echo   [FAIL] Pre-flight reported a blocking failure. Not starting.
    exit /b 1
)

REM --- Start Commerce Service (8001) --------------------------------------
REM cmd /k hosts the console so it survives the service exiting; without it a
REM failed bind closes the window before the error can be read.
echo   [ .. ] Starting Commerce Service on port 8001...
start "Commerce Service :8001" /d "%CD%" cmd /k ""%PY%" -m uvicorn commerce.main:app --host 127.0.0.1 --port 8001"

REM Commerce must be healthy before the platform accepts runs (FR-076).
echo   [ .. ] Waiting for Commerce Service to become healthy...
set /a TRIES=0
:waitcommerce
set /a TRIES+=1
"%PY%" -c "import httpx,sys; sys.exit(0 if httpx.get('http://127.0.0.1:8001/health',timeout=2).json().get('status')=='ok' else 1)" >nul 2>&1
if not errorlevel 1 goto commerceup
if %TRIES% GEQ 20 (
    echo   [FAIL] Commerce Service did not become healthy within ~20s.
    exit /b 1
)
timeout /t 1 /nobreak >nul
goto waitcommerce
:commerceup
echo   [ OK ] Commerce Service healthy.

REM --- Start Pricing Platform (8000) ---------------------------------------
echo   [ .. ] Starting Pricing Platform on port 8000...
start "Pricing Platform :8000" /d "%CD%" cmd /k ""%PY%" -m uvicorn pricing.main:app --host 127.0.0.1 --port 8000"

REM --- Start UI (5173) ------------------------------------------------------
if exist "ui\package.json" (
    where node >nul 2>&1
    if errorlevel 1 (
        echo   [WARN] Node.js not found - skipping UI. Backend APIs still available.
    ) else (
        if not exist "ui\node_modules" (
            echo   [ .. ] Installing UI dependencies ^(first run^)...
            pushd ui
            call npm install --silent
            popd
        )
        echo   [ .. ] Starting UI on port 5173...
        REM /d sets the spawned window's directory, so no pushd/popd race with
        REM the asynchronous start.
        start "Web UI :5173" /d "%CD%\ui" cmd /k "npm run dev"
    )
) else (
    echo   [WARN] ui\package.json not found - skipping UI.
)

echo.
echo   ----------------------------------------------------------------
echo    Commerce Service  http://127.0.0.1:8001/docs
echo    Pricing Platform  http://127.0.0.1:8000/docs
echo    Web UI            http://127.0.0.1:5173
echo   ----------------------------------------------------------------
echo    Three windows should now be open, one per service. If one is
echo    missing, its service failed to start - the window it left behind
echo    holds the reason.
echo.
echo    Close those windows to shut down. Leaving them open is what causes
echo    the next run to find its ports taken.
echo.

endlocal
exit /b 0


REM ===================================================================
REM  Subroutines
REM ===================================================================

REM Report whether a port is already listening. Sets BUSY when it is.
REM   %1 = port   %2 = quoted label
REM tasklist rather than wmic: wmic is deprecated and absent from recent
REM Windows 11 builds, where it fails silently and the name is simply lost.
:portcheck
set "OWNER="
set "OWNERNAME=unknown"
for /f "tokens=5" %%p in ('netstat -ano -p TCP ^| findstr /r /c:":%~1 .*LISTENING"') do set "OWNER=%%p"
if not defined OWNER (
    echo   [ OK ] port %~1 ^(%~2^) free
    exit /b 0
)
for /f "tokens=1 delims=," %%n in ('tasklist /nh /fo csv /fi "PID eq !OWNER!" 2^>nul') do set "OWNERNAME=%%~n"
set "BUSY=1"
echo   [BUSY] port %~1 ^(%~2^) held by PID !OWNER! ^(!OWNERNAME!^)
exit /b 0


REM Stop whatever is listening on a port. Only reached behind /force, so the
REM user has explicitly asked for it -- this never happens on a plain run.
:portkill
for /f "tokens=5" %%p in ('netstat -ano -p TCP ^| findstr /r /c:":%~1 .*LISTENING"') do (
    echo         stopping PID %%p on port %~1
    taskkill /f /t /pid %%p >nul 2>&1
)
exit /b 0
