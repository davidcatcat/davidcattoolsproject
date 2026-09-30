# Runtime smoke test for a built nlf.exe on Windows.
#
# Usage: pwsh -File scripts/ci-smoke.ps1 <binary> <version>
#        powershell -File scripts/ci-smoke.ps1 <binary> <version>
#
# Runs with PowerShell 7 (pwsh) and Windows PowerShell 5.1. Builds fixture
# trees in a fresh temporary directory and checks exit codes, exact stdout
# and stderr, --explain, --confirm, -0, drive-letter, UNC, ~\, Unicode and
# space paths, a junction root, and that nothing in the fixtures changed.
#
# String comparisons use -ceq/-cne/-ccontains: PowerShell's -eq ignores case.
#
# Most checks start nlf through System.Diagnostics.Process, which gives the
# exact stdout and stderr bytes and lets the test write stdin. A few checks
# call nlf the way a PowerShell user does (& $bin ...), to cover PowerShell's
# own argument passing. Temporary directories are left in place and printed
# at the end; nothing is deleted.
param(
	[Parameter(Mandatory = $true)][string]$Binary,
	[Parameter(Mandatory = $true)][string]$Version
)
Set-StrictMode -Version 3
$ErrorActionPreference = 'Stop'
# nlf writes UTF-8; decode captured native output as UTF-8, not the OEM code page.
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$utf8 = New-Object System.Text.UTF8Encoding($false)
# Process.StandardInput uses Console.InputEncoding. On .NET Framework
# (Windows PowerShell 5.1) it writes that encoding's preamble as soon as the
# process starts, so a UTF-8 console would put a BOM before the --confirm
# answer ("\ufeffy" is not "y"). A preamble-free encoding keeps stdin exact.
[Console]::InputEncoding = $utf8

if (-not $Version) { throw 'ci-smoke: version is empty' }
if (-not (Test-Path -LiteralPath $Binary -PathType Leaf)) { throw "ci-smoke: binary not found: $Binary" }
$bin = (Resolve-Path -LiteralPath $Binary).ProviderPath
$psv = $PSVersionTable.PSVersion
Write-Output "PowerShell $psv, binary $bin"

# --- fixture -----------------------------------------------------------------

$base = Join-Path ([System.IO.Path]::GetTempPath()) ('nlf-smoke-' + [guid]::NewGuid().ToString('N'))
$fx = Join-Path $base 'fx'
$hx = Join-Path $base 'hostile'
$home_ = Join-Path $base 'home'
$uni = Join-Path $fx '中文 目錄'
foreach ($d in @($fx, (Join-Path $fx 'vendor'), (Join-Path $fx 'docs'), $uni, $hx, (Join-Path $home_ 'proj'))) {
	New-Item -ItemType Directory -Path $d -Force | Out-Null
}
function Write-Fixture([string]$path, [string]$text) {
	[System.IO.File]::WriteAllText($path, $text, $utf8)
}
Write-Fixture (Join-Path $fx 'main.go') "package main`n`n// TODO: finish`nfunc main() {}`n"
Write-Fixture (Join-Path $fx 'util.go') "package main`n`nfunc util() {}`n"
Write-Fixture (Join-Path $fx 'vendor\lib.go') "package lib`n`n// TODO: vendored`n"
Write-Fixture (Join-Path $fx 'docs\readme.md') "# readme`n"
Write-Fixture (Join-Path $uni '說明.md') "說明`n"
Write-Fixture (Join-Path $fx 'big.log') ('x' * 3000)
Write-Fixture (Join-Path $fx 'old.go') "package old`n"
(Get-Item -LiteralPath (Join-Path $fx 'old.go')).LastWriteTime = Get-Date '2020-01-01T12:00:00'
Write-Fixture (Join-Path $home_ 'proj\home.md') "home`n"

# Names that are legal on Windows but awkward: spaces, a leading "-", quotes
# and CJK. Tabs, newlines, \ / : * ? " < > | and trailing spaces or dots
# cannot be Windows file names.
$hostileNames = @('a b.txt', '-rf.txt', '--help.txt', '-n', ' lead.txt', "it's.txt", '中文 檔名.txt')
foreach ($n in $hostileNames) { Write-Fixture (Join-Path $hx $n) 'x' }

$junction = Join-Path $base 'docs-junction'
New-Item -ItemType Junction -Path $junction -Target (Join-Path $fx 'docs') | Out-Null

function Get-Snapshot([string]$dir) {
	Get-ChildItem -LiteralPath $dir -Recurse -File -Force | ForEach-Object {
		'{0}|{1}|{2}' -f $_.FullName, (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash, $_.LastWriteTimeUtc.Ticks
	} | Sort-Object
}
$before = (Get-Snapshot $fx) -join "`n"
$hxBefore = (Get-Snapshot $hx) -join "`n"

# --- helpers -----------------------------------------------------------------

$script:nfail = 0
$script:failures = @()
function Pass([string]$name) { Write-Output "PASS  $name" }
function Fail([string]$name, [string]$why) {
	Write-Output "FAIL  ${name}: $why"
	$script:nfail++
	$script:failures += "  - ${name}: $why"
}

# ConvertTo-WinArg quotes one argument for CreateProcess the way
# CommandLineToArgvW (and Go's os.Args) parse it back.
function ConvertTo-WinArg([string]$a) {
	if ($a -ne '' -and $a -notmatch '[\s"]') { return $a }
	$sb = New-Object System.Text.StringBuilder
	[void]$sb.Append('"')
	$bs = 0
	foreach ($ch in $a.ToCharArray()) {
		if ($ch -eq '\') { $bs++; continue }
		if ($ch -eq '"') {
			[void]$sb.Append('\' * (2 * $bs + 1)); [void]$sb.Append('"'); $bs = 0; continue
		}
		if ($bs -gt 0) { [void]$sb.Append('\' * $bs); $bs = 0 }
		[void]$sb.Append($ch)
	}
	[void]$sb.Append('\' * (2 * $bs)); [void]$sb.Append('"')
	return $sb.ToString()
}

# Invoke-Nlf runs nlf with exact arguments and returns Code, Out, Err
# (UTF-8 strings) and OutBytes.
function Invoke-Nlf {
	param([string[]]$NlfArgs, [string]$Stdin = '', [string]$Dir = $fx, [hashtable]$Env = @{})
	$psi = New-Object System.Diagnostics.ProcessStartInfo
	$psi.FileName = $bin
	$psi.Arguments = (@($NlfArgs) | ForEach-Object { ConvertTo-WinArg $_ }) -join ' '
	$psi.WorkingDirectory = $Dir
	$psi.UseShellExecute = $false
	$psi.RedirectStandardInput = $true
	$psi.RedirectStandardOutput = $true
	$psi.RedirectStandardError = $true
	foreach ($k in $Env.Keys) { $psi.EnvironmentVariables[$k] = $Env[$k] }
	$p = [System.Diagnostics.Process]::Start($psi)
	$outMs = New-Object System.IO.MemoryStream
	$errMs = New-Object System.IO.MemoryStream
	$t1 = $p.StandardOutput.BaseStream.CopyToAsync($outMs)
	$t2 = $p.StandardError.BaseStream.CopyToAsync($errMs)
	try {
		if ($Stdin -ne '') {
			$in = $utf8.GetBytes($Stdin)
			$p.StandardInput.BaseStream.Write($in, 0, $in.Length)
		}
		$p.StandardInput.Close()
	} catch [System.IO.IOException] {
		# nlf exited without reading stdin; its exit code decides the check.
	}
	$p.WaitForExit()
	$t1.Wait(); $t2.Wait()
	$ob = $outMs.ToArray()
	return [pscustomobject]@{ Code = $p.ExitCode; OutBytes = $ob; Out = $utf8.GetString($ob); Err = $utf8.GetString($errMs.ToArray()) }
}

function Get-Lines([string]$s) {
	return @($s -split "`n" | Where-Object { $_ -ne '' } | Sort-Object)
}

function Test-Found([string]$name, [string[]]$want, [string[]]$NlfArgs, [string]$Dir = $fx) {
	$r = Invoke-Nlf -NlfArgs $NlfArgs -Dir $Dir
	$got = (Get-Lines $r.Out) -join '|'
	$exp = (@($want) | Sort-Object) -join '|'
	if ($r.Code -ne 0) { Fail $name "exit $($r.Code), want 0 (stderr: $($r.Err))" }
	elseif ($r.Err -ne '') { Fail $name "stderr not empty: $($r.Err)" }
	elseif ($got -cne $exp) { Fail $name "got [$got] want [$exp]" }
	else { Pass $name }
}

function Test-ExitEmpty([string]$name, [int]$want, [string[]]$NlfArgs, [string]$Dir = $fx) {
	$r = Invoke-Nlf -NlfArgs $NlfArgs -Dir $Dir
	if ($r.Code -ne $want) { Fail $name "exit $($r.Code), want $want (stderr: $($r.Err))" }
	elseif ($r.Out -ne '') { Fail $name "stdout not empty: $($r.Out)" }
	elseif ($r.Err -eq '') { Fail $name 'stderr is empty; want a message' }
	else { Pass $name }
}

function Join-Fx([string]$rel) { return Join-Path $fx $rel }

# --- basic flags ---------------------------------------------------------------

$r = Invoke-Nlf -NlfArgs @('--version')
if ($r.Code -eq 0 -and $r.Out -ceq "nlf $Version`n" -and $r.Err -eq '') { Pass '--version' }
else { Fail '--version' "exit $($r.Code), stdout [$($r.Out)]" }

$r = Invoke-Nlf -NlfArgs @('--help')
if ($r.Code -eq 0 -and $r.Out.Contains('Usage:') -and $r.Err -eq '') { Pass '--help' }
else { Fail '--help' "exit $($r.Code), stdout lacks Usage:" }

# --- searches with a drive-letter --dir -----------------------------------------

Test-Found 'go files excluding vendor' @((Join-Fx 'main.go'), (Join-Fx 'util.go'), (Join-Fx 'old.go')) @('--dir', $fx, 'go files excluding vendor')
Test-Found '找 go 檔' @((Join-Fx 'main.go'), (Join-Fx 'util.go'), (Join-Fx 'old.go'), (Join-Fx 'vendor\lib.go')) @('--dir', $fx, '找 go 檔')
Test-Found 'files containing "TODO"' @((Join-Fx 'main.go'), (Join-Fx 'vendor\lib.go')) @('--dir', $fx, 'files containing "TODO"')
Test-Found 'larger than 2KB' @((Join-Fx 'big.log')) @('--dir', $fx, 'larger than 2KB')
Test-Found 'go files modified in the last 7 days' @((Join-Fx 'main.go'), (Join-Fx 'util.go'), (Join-Fx 'vendor\lib.go')) @('--dir', $fx, 'go files modified in the last 7 days')
Test-Found 'Unicode --dir with a space: md files' @((Join-Path $uni '說明.md')) @('--dir', $uni, 'md files')
Test-Found '--dir with a trailing backslash' @((Join-Fx 'docs\readme.md')) @('--dir', ((Join-Fx 'docs') + '\'), 'md files')
# WalkDir joins child paths with filepath.Join, which also turns / into \.
Test-Found '--dir with forward slashes' @((Join-Fx 'docs\readme.md')) @('--dir', ((Join-Fx 'docs') -replace '\\', '/'), 'md files')
Test-Found 'relative output from the working directory' @('main.go', 'util.go', 'old.go', 'vendor\lib.go') @('go files')
Test-Found '--max-depth 1 go files' @((Join-Fx 'main.go'), (Join-Fx 'util.go'), (Join-Fx 'old.go')) @('--max-depth', '1', '--dir', $fx, 'go files')
Test-Found '--min-depth=2 go files' @((Join-Fx 'vendor\lib.go')) @('--min-depth=2', '--dir', $fx, 'go files')
Test-Found '--min-depth 2 md files excluding vendor' @((Join-Fx 'docs\readme.md'), (Join-Path $uni '說明.md')) @('--min-depth', '2', '--dir', $fx, 'md files excluding vendor')
Test-Found 'exclude a Windows path under the root' @((Join-Fx 'main.go'), (Join-Fx 'util.go'), (Join-Fx 'old.go')) @('--dir', $fx, ('go files excluding "' + (Join-Fx 'vendor') + '"'))
Test-Found 'junction as the search root' @((Join-Path $junction 'readme.md')) @('--dir', $junction, 'md files')
# nlf expands ~\ itself (cmd.exe and PowerShell pass it unchanged); USERPROFILE
# points at the fixture so the check never reads the real profile.
$r = Invoke-Nlf -NlfArgs @('--dir', '~\proj', 'md files') -Env @{ USERPROFILE = $home_ }
if ($r.Code -eq 0 -and $r.Out -ceq ((Join-Path $home_ 'proj\home.md') + "`n")) { Pass '~\proj with USERPROFILE set' }
else { Fail '~\proj with USERPROFILE set' "exit $($r.Code), stdout [$($r.Out)] (stderr: $($r.Err))" }

# UNC: the same fixture through the administrative share \\localhost\C$\...
$drive = $fx.Substring(0, 1)
$unc = '\\localhost\' + $drive + '$' + $fx.Substring(2)
if (Test-Path -LiteralPath $unc) {
	Test-Found 'UNC --dir' @(($unc + '\main.go'), ($unc + '\util.go'), ($unc + '\old.go')) @('--dir', $unc, 'go files excluding vendor')
} else {
	Fail 'UNC --dir' "administrative share $unc is not reachable"
}

# Drive root: list at most one level of the drive. Any file found is printed
# with the drive prefix and backslashes; nothing found is exit 1.
$root = $fx.Substring(0, 3)
$r = Invoke-Nlf -NlfArgs @('--max-depth', '1', '--dir', $root, 'files larger than 0 bytes')
$bad = @(Get-Lines $r.Out | Where-Object { -not $_.StartsWith($root) -or $_.Contains('/') })
if (($r.Code -eq 0 -or $r.Code -eq 1) -and $bad.Count -eq 0) { Pass "drive root $root (exit $($r.Code))" }
else { Fail "drive root $root" "exit $($r.Code), unexpected lines [$($bad -join '|')] (stderr: $($r.Err))" }

# --- exit codes ------------------------------------------------------------------

Test-ExitEmpty 'pem files (no match)' 1 @('--dir', $fx, 'pem files')
Test-ExitEmpty 'files without TODO (invalid)' 2 @('--dir', $fx, 'files without TODO')
Test-ExitEmpty '--bogus go files (invalid flag)' 2 @('--bogus', 'go files')
Test-ExitEmpty '--dir missing (invalid)' 2 @('--dir', (Join-Fx 'does-not-exist'), 'go files')
Test-ExitEmpty 'log files !containing "TODO" (invalid)' 2 @('--dir', $fx, 'log files !containing "TODO"')
Test-ExitEmpty '--max-depth -1 (invalid)' 2 @('--max-depth', '-1', '--dir', $fx, 'go files')
Test-ExitEmpty '--min-depth 2 --max-depth 1 (invalid)' 2 @('--min-depth', '2', '--max-depth', '1', '--dir', $fx, 'go files')
Test-ExitEmpty '--help --max-depth 1 (conflict)' 2 @('--help', '--max-depth', '1')
Test-ExitEmpty 'Windows path in the query (use --dir)' 2 @(('go files in ' + $fx))

# --- --explain and --confirm -------------------------------------------------------

$r = Invoke-Nlf -NlfArgs @('--explain', '--dir', $fx, 'go files')
if ($r.Code -ne 0) { Fail '--explain' "exit $($r.Code) (stderr: $($r.Err))" }
elseif ($r.Err -ne '') { Fail '--explain' "stderr not empty: $($r.Err)" }
elseif (-not $r.Out.Contains('No approximate command on Windows')) { Fail '--explain' "stdout lacks the Windows note: $($r.Out)" }
elseif ($r.Out.Contains('find ') -or $r.Out.Contains('-exec') -or $r.Out.Contains('-iname')) { Fail '--explain' "stdout shows a Unix command: $($r.Out)" }
elseif (-not $r.Out.Contains($fx)) { Fail '--explain' 'stdout lacks the search root' }
else { Pass '--explain (Windows: conditions only)' }

$r = Invoke-Nlf -NlfArgs @('--explain', '--dir', $fx, '找 go 檔')
if ($r.Code -eq 0 -and $r.Out.Contains('Windows 上不顯示近似指令') -and $r.Out.Contains('.go')) { Pass '--explain in Chinese' }
else { Fail '--explain in Chinese' "exit $($r.Code), stdout [$($r.Out)]" }

$direct = Invoke-Nlf -NlfArgs @('--dir', $fx, 'go files')

$r = Invoke-Nlf -NlfArgs @('--confirm', '--dir', $fx, 'go files') -Stdin "n`r`n"
if ($r.Code -eq 0 -and $r.Out -eq '' -and $r.Err.Contains('Cancelled.')) { Pass '--confirm answered n' }
else { Fail '--confirm answered n' "exit $($r.Code), stdout [$($r.Out)]" }

$r = Invoke-Nlf -NlfArgs @('--confirm', '--dir', $fx, 'go files') -Stdin "y`r`n"
if ($r.Code -ne 0) { Fail '--confirm answered y' "exit $($r.Code) (stderr: $($r.Err))" }
elseif ($r.Out -eq '' -or $r.Out -cne $direct.Out) { Fail '--confirm answered y' "stdout differs from the direct run (stderr: $($r.Err))" }
elseif (-not $r.Err.Contains('READ-ONLY') -or -not $r.Err.Contains('No approximate command on Windows')) { Fail '--confirm answered y' "plan lacks READ-ONLY or the Windows note: $($r.Err)" }
elseif ($r.Err.Contains('find ')) { Fail '--confirm answered y' 'plan shows a Unix command' }
else { Pass '--confirm answered y' }

$r = Invoke-Nlf -NlfArgs @('--confirm', '--dir', $fx, 'go files')
if ($r.Code -eq 0 -and $r.Out -eq '' -and $r.Err.Contains('Cancelled.')) { Pass '--confirm with EOF' }
else { Fail '--confirm with EOF' "exit $($r.Code), stdout [$($r.Out)]" }

# --- -0 and hostile names ------------------------------------------------------------

# Default order: names sorted byte-wise; a name starting with "-" gets ".\".
$want0 = @(' lead.txt', '.\--help.txt', '.\-n', '.\-rf.txt', 'a b.txt', "it's.txt", '中文 檔名.txt')
$wantBytes = $utf8.GetBytes((($want0 | ForEach-Object { $_ + [char]0 }) -join ''))
$r = Invoke-Nlf -NlfArgs @('-0', 'files') -Dir $hx
if ($r.Code -ne 0) { Fail '-0 files (hostile names)' "exit $($r.Code) (stderr: $($r.Err))" }
elseif ([System.Convert]::ToBase64String($r.OutBytes) -cne [System.Convert]::ToBase64String($wantBytes)) {
	Fail '-0 files (hostile names)' ("got [" + ($r.Out -replace [char]0, '|') + "]")
} else { Pass '-0 files (hostile names)' }

$r0 = $r
$r = Invoke-Nlf -NlfArgs @('--null', 'files') -Dir $hx
if ($r.Code -eq 0 -and [System.Convert]::ToBase64String($r.OutBytes) -ceq [System.Convert]::ToBase64String($r0.OutBytes)) { Pass '--null is identical to -0' }
else { Fail '--null is identical to -0' "exit $($r.Code) or bytes differ" }

$r = Invoke-Nlf -NlfArgs @('files') -Dir $hx
if ($r.Code -eq 0 -and ($r.Out -split "`n") -ccontains '.\-rf.txt' -and -not (($r.Out -split "`n") -ccontains '-rf.txt')) { Pass 'newline output prints .\-rf.txt' }
else { Fail 'newline output prints .\-rf.txt' "exit $($r.Code), stdout [$($r.Out)]" }

$r = Invoke-Nlf -NlfArgs @('-0', '--confirm', 'files') -Dir $hx -Stdin "y`n"
if ($r.Code -eq 0 -and [System.Convert]::ToBase64String($r.OutBytes) -ceq [System.Convert]::ToBase64String($r0.OutBytes)) { Pass '-0 --confirm answered y' }
else { Fail '-0 --confirm answered y' "exit $($r.Code) or stdout differs from the direct -0 run (stderr: $($r.Err))" }

# --- PowerShell's own argument passing ----------------------------------------------

# Windows PowerShell 5.1 turns redirected native stderr into error records,
# which would stop the script; exit codes are checked explicitly instead.
$ErrorActionPreference = 'Continue'

# Content with embedded double quotes: PowerShell 7.3+ passes them as is;
# Windows PowerShell 5.1 drops them unless they are written as \".
if ($psv.Major -ge 7) { $q = 'files containing "TODO"' } else { $q = 'files containing \"TODO\"' }
$out = @(& $bin --dir $fx $q)
$code = $LASTEXITCODE
$got = (@($out) | Sort-Object) -join '|'
$exp = (@((Join-Fx 'main.go'), (Join-Fx 'vendor\lib.go')) | Sort-Object) -join '|'
if ($code -eq 0 -and $got -ceq $exp) { Pass "PowerShell call: $q" }
else { Fail "PowerShell call: $q" "exit $code, got [$got] want [$exp]" }

$out = @(& $bin --dir $uni '找 md 檔')
$code = $LASTEXITCODE
if ($code -eq 0 -and $out.Count -eq 1 -and $out[0] -ceq (Join-Path $uni '說明.md')) { Pass 'PowerShell call: Unicode --dir with a space' }
else { Fail 'PowerShell call: Unicode --dir with a space' "exit $code, got [$($out -join '|')]" }

$out = @(& $bin --dir $fx 'pem files' 2>$null)
$code = $LASTEXITCODE
if ($code -eq 1 -and $out.Count -eq 0) { Pass 'PowerShell call: $LASTEXITCODE is 1 for no match' }
else { Fail 'PowerShell call: $LASTEXITCODE is 1 for no match' "exit $code, stdout [$($out -join '|')]" }

$ErrorActionPreference = 'Stop'

# --- read-only ---------------------------------------------------------------------

if (((Get-Snapshot $fx) -join "`n") -ceq $before) { Pass 'fixture unchanged (hash and mtime)' }
else { Fail 'fixture unchanged (hash and mtime)' 'snapshot differs' }
if (((Get-Snapshot $hx) -join "`n") -ceq $hxBefore) { Pass 'hostile-name fixture unchanged' }
else { Fail 'hostile-name fixture unchanged' 'snapshot differs' }

# --- summary -------------------------------------------------------------------------

Write-Output "fixtures: $base"
if ($script:nfail -ne 0) {
	Write-Output ''
	Write-Output "$($script:nfail) check(s) failed for ${bin}:"
	$script:failures | ForEach-Object { Write-Output $_ }
	exit 1
}
Write-Output "all checks passed for $bin"
exit 0
