@echo off
rem Runtime smoke test for nlf.exe under cmd.exe.
rem
rem Usage: call scripts\ci-smoke.cmd <binary> <version>
rem
rem Optional: NLF_SMOKE_UNICODE_DIR names a directory holding exactly one .md
rem file, prepared outside this script (a cmd script cannot portably hold
rem non-ASCII text). Uses find, not findstr, for plain-text matching: findstr
rem treats "\" in paths as an escape. Checks exit codes, stdout/stderr separation, cmd.exe
rem quoting, --explain and --confirm. Temporary directories are left in
rem place and printed at the end; nothing is deleted.
setlocal EnableExtensions DisableDelayedExpansion
set "BIN=%~f1"
set "VER=%~2"
if "%VER%"=="" (echo ci-smoke: version is empty& exit /b 1)
if not exist "%BIN%" (echo ci-smoke: binary not found: %1& exit /b 1)
set "FX=%TEMP%\nlf-cmd-smoke-%RANDOM%%RANDOM%"
set "WK=%FX%-work"
mkdir "%FX%\vendor" "%FX%\my docs" "%WK%" || exit /b 1
> "%FX%\main.go" echo // TODO: finish
> "%FX%\util.go" echo package main
> "%FX%\vendor\lib.go" echo // TODO: vendored
> "%FX%\my docs\notes.md" echo notes
set FAILS=0
rem The Windows find, not a Unix find that Git for Windows may put on PATH.
set "FIND=%SystemRoot%\System32\find.exe"

rem --- basic flags
"%BIN%" --version > "%WK%\out" 2> "%WK%\err"
call :code "--version" 0 %ERRORLEVEL%
"%FIND%" "nlf %VER%" "%WK%\out" > nul || call :fail "--version" "stdout is not nlf %VER%"

rem --- searches
"%BIN%" --dir "%FX%" "go files excluding vendor" > "%WK%\out" 2> "%WK%\err"
call :code "go files excluding vendor" 0 %ERRORLEVEL%
call :lines "go files excluding vendor" 2
"%FIND%" "%FX%\main.go" "%WK%\out" > nul || call :fail "go files excluding vendor" "main.go missing"
"%FIND%" "vendor\lib.go" "%WK%\out" > nul && call :fail "go files excluding vendor" "vendor was not excluded"
call :empty "go files excluding vendor: stderr" "%WK%\err"

"%BIN%" --dir "%FX%\my docs" "md files" > "%WK%\out" 2> "%WK%\err"
call :code "--dir with a space" 0 %ERRORLEVEL%
"%FIND%" "%FX%\my docs\notes.md" "%WK%\out" > nul || call :fail "--dir with a space" "notes.md missing"

if defined NLF_SMOKE_UNICODE_DIR (
  "%BIN%" --dir "%NLF_SMOKE_UNICODE_DIR%" "md files" > "%WK%\out" 2> "%WK%\err"
  call :code "Unicode --dir" 0 %%ERRORLEVEL%%
  call :lines "Unicode --dir" 1
) else (
  call :fail "Unicode --dir" "NLF_SMOKE_UNICODE_DIR is not set"
)

rem cmd.exe quoting: \" inside a quoted argument is a literal quote for nlf.
"%BIN%" --dir "%FX%" "files containing \"TODO\"" > "%WK%\out" 2> "%WK%\err"
call :code "quoted content TODO" 0 %ERRORLEVEL%
call :lines "quoted content TODO" 2
"%FIND%" "%FX%\vendor\lib.go" "%WK%\out" > nul || call :fail "quoted content TODO" "vendor\lib.go missing"

pushd "%FX%"
"%BIN%" "go files" > "%WK%\out" 2> "%WK%\err"
call :code "relative output" 0 %ERRORLEVEL%
"%FIND%" "vendor\lib.go" "%WK%\out" > nul || call :fail "relative output" "vendor\lib.go missing"
popd

rem --- exit codes: stdout stays empty, the message goes to stderr
"%BIN%" --dir "%FX%" "pem files" > "%WK%\out" 2> "%WK%\err"
call :code "pem files (no match)" 1 %ERRORLEVEL%
call :empty "pem files (no match): stdout" "%WK%\out"
call :nonempty "pem files (no match): stderr" "%WK%\err"

"%BIN%" --dir "%FX%" "files without TODO" > "%WK%\out" 2> "%WK%\err"
call :code "files without TODO (invalid)" 2 %ERRORLEVEL%
call :empty "files without TODO (invalid): stdout" "%WK%\out"
call :nonempty "files without TODO (invalid): stderr" "%WK%\err"

"%BIN%" --dir "%FX%\does-not-exist" "go files" > "%WK%\out" 2> "%WK%\err"
call :code "--dir missing" 2 %ERRORLEVEL%
call :empty "--dir missing: stdout" "%WK%\out"

"%BIN%" --bogus "go files" > "%WK%\out" 2> "%WK%\err"
call :code "--bogus (invalid flag)" 2 %ERRORLEVEL%
call :empty "--bogus (invalid flag): stdout" "%WK%\out"

rem --- --explain and --confirm
"%BIN%" --explain --dir "%FX%" "go files" > "%WK%\out" 2> "%WK%\err"
call :code "--explain" 0 %ERRORLEVEL%
"%FIND%" "No approximate command on Windows" "%WK%\out" > nul || call :fail "--explain" "Windows note missing"
"%FIND%" "-exec" "%WK%\out" > nul && call :fail "--explain" "a Unix find command is shown"
"%FIND%" "-iname" "%WK%\out" > nul && call :fail "--explain" "a Unix find command is shown"
call :empty "--explain: stderr" "%WK%\err"

"%BIN%" --dir "%FX%" "go files" > "%WK%\direct" 2> nul
echo n| "%BIN%" --confirm --dir "%FX%" "go files" > "%WK%\out" 2> "%WK%\err"
call :code "--confirm answered n" 0 %ERRORLEVEL%
call :empty "--confirm answered n: stdout" "%WK%\out"
"%FIND%" "Cancelled." "%WK%\err" > nul || call :fail "--confirm answered n" "stderr lacks Cancelled."

echo y| "%BIN%" --confirm --dir "%FX%" "go files" > "%WK%\out" 2> "%WK%\err"
call :code "--confirm answered y" 0 %ERRORLEVEL%
"%SystemRoot%\System32\fc.exe" /b "%WK%\out" "%WK%\direct" > nul || call :fail "--confirm answered y" "stdout differs from the direct run"
"%FIND%" "READ-ONLY" "%WK%\err" > nul || call :fail "--confirm answered y" "plan lacks READ-ONLY"

"%BIN%" --confirm --dir "%FX%" "go files" < nul > "%WK%\out" 2> "%WK%\err"
call :code "--confirm with EOF" 0 %ERRORLEVEL%
call :empty "--confirm with EOF: stdout" "%WK%\out"

echo fixture: %FX%
echo work:    %WK%
if not "%FAILS%"=="0" (
  echo %FAILS% check^(s^) failed for %BIN%
  exit /b 1
)
echo all checks passed for %BIN%
exit /b 0

rem --- helpers ------------------------------------------------------------

:code
if "%~3"=="%~2" (echo PASS  %~1) else (call :fail %1 "exit %~3, want %~2")
exit /b 0

:lines
set "N="
for /f %%C in ('type "%WK%\out" ^| "%FIND%" /c /v ""') do set "N=%%C"
if not "%N%"=="%~2" call :fail %1 "%N% line(s) on stdout, want %~2"
exit /b 0

:empty
if %~z2 NEQ 0 call :fail %1 "not empty"
exit /b 0

:nonempty
if %~z2 EQU 0 call :fail %1 "empty"
exit /b 0

:fail
echo FAIL  %~1: %~2
set /a FAILS+=1
exit /b 0
