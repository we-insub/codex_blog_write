@echo off
setlocal

if defined MATO_BLOG_RUNTIME_HOME (
  set "MATO_RUNTIME_ROOT=%MATO_BLOG_RUNTIME_HOME%"
) else (
  set "MATO_RUNTIME_ROOT=%USERPROFILE%\.googleblog\mato-blog-codex"
)

if exist "%MATO_RUNTIME_ROOT%\.venv\Scripts\python.exe" (
  "%MATO_RUNTIME_ROOT%\.venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
  if not errorlevel 1 goto run_runtime
)

where py >nul 2>nul
if not errorlevel 1 (
  for %%V in (3.14 3.13 3.12 3.11 3.10) do (
    py -%%V -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
    if not errorlevel 1 (
      set "MATO_PY_VERSION=%%V"
      goto run_py
    )
  )
)

where python >nul 2>nul
if not errorlevel 1 (
  python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
  if not errorlevel 1 goto run_python
)

where python3 >nul 2>nul
if not errorlevel 1 (
  python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
  if not errorlevel 1 goto run_python3
)

echo Python 3.10 or newer was not found. Install Python and try again. 1>&2
exit /b 2

:run_py
py -%MATO_PY_VERSION% "%~dp0bootstrap.py" %*
exit /b %errorlevel%

:run_runtime
"%MATO_RUNTIME_ROOT%\.venv\Scripts\python.exe" "%~dp0bootstrap.py" %*
exit /b %errorlevel%

:run_python
python "%~dp0bootstrap.py" %*
exit /b %errorlevel%

:run_python3
python3 "%~dp0bootstrap.py" %*
exit /b %errorlevel%
