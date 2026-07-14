[CmdletBinding()]
param(
    [string]$BridgePython = 'D:\Python_3.10\python.exe'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ExpectedRoot = 'E:\houdini-intelligence-agent'
$HoudiniExe = 'C:\Program Files\Side Effects Software\Houdini 21.0.440\bin\houdini.exe'
$CodexExe = 'E:\houdini-intelligence-agent\.runtime\toolchains\codex\0.144.3\codex.exe'
$CodexHome = 'E:\houdini-intelligence-agent\.runtime\codex-home'

function Assert-OrdinaryProjectPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root,
        [switch]$AllowMissingLeaf
    )

    $normalizedRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd('\')
    $normalizedPath = [System.IO.Path]::GetFullPath($Path)
    if (-not $normalizedPath.StartsWith(
        $normalizedRoot + '\',
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Path is outside the project root: $normalizedPath"
    }
    if ($normalizedPath.StartsWith('\\') -or $normalizedPath.Substring(2).Contains(':')) {
        throw "UNC, device, and ADS paths are forbidden: $normalizedPath"
    }

    $rootItem = Get-Item -LiteralPath $normalizedRoot -Force
    if (($rootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Project root is a reparse point: $normalizedRoot"
    }
    $current = $normalizedRoot
    $relative = $normalizedPath.Substring($normalizedRoot.Length).TrimStart('\')
    foreach ($part in $relative.Split('\')) {
        if (-not $part) { continue }
        $current = Join-Path $current $part
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Path traverses a reparse point: $current"
            }
        } elseif (-not $AllowMissingLeaf) {
            throw "Required path does not exist: $current"
        }
    }
    return $normalizedPath
}

function Set-ChildEnvironment {
    param(
        [Parameter(Mandatory = $true)][System.Diagnostics.ProcessStartInfo]$StartInfo,
        [Parameter(Mandatory = $true)][hashtable]$Values
    )

    foreach ($entry in $Values.GetEnumerator()) {
        $StartInfo.EnvironmentVariables[$entry.Key] = [string]$entry.Value
    }
}

$ResolvedRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path.TrimEnd('\')
if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($ResolvedRoot, $ExpectedRoot)) {
    throw "Launcher must run from $ExpectedRoot; resolved root was $ResolvedRoot"
}

$rootItem = Get-Item -LiteralPath $ResolvedRoot -Force
if (($rootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "Project root is a reparse point: $ResolvedRoot"
}
if (-not (Test-Path -LiteralPath $HoudiniExe -PathType Leaf)) {
    throw "Houdini 21.0.440 executable was not found: $HoudiniExe"
}
if (-not (Test-Path -LiteralPath $BridgePython -PathType Leaf)) {
    throw "Bridge Python executable was not found: $BridgePython"
}
$normalizedPython = [System.IO.Path]::GetFullPath($BridgePython)
if ($normalizedPython -match '(?i)\\(AppData|WindowsApps)\\') {
    throw "Bridge Python may not come from AppData or WindowsApps: $normalizedPython"
}

Assert-OrdinaryProjectPath -Path $CodexExe -Root $ResolvedRoot | Out-Null
Assert-OrdinaryProjectPath -Path $CodexHome -Root $ResolvedRoot | Out-Null

$sessionId = [Guid]::NewGuid().ToString('N')
$sessionRoot = Assert-OrdinaryProjectPath `
    -Path (Join-Path $ResolvedRoot ".runtime\launcher-sessions\$sessionId") `
    -Root $ResolvedRoot `
    -AllowMissingLeaf
$sessionTemp = Assert-OrdinaryProjectPath `
    -Path (Join-Path $sessionRoot 'tmp') `
    -Root $ResolvedRoot `
    -AllowMissingLeaf
$houdiniPreferences = Assert-OrdinaryProjectPath `
    -Path (Join-Path $sessionRoot 'houdini-user-pref') `
    -Root $ResolvedRoot `
    -AllowMissingLeaf
[System.IO.Directory]::CreateDirectory($sessionTemp) | Out-Null
[System.IO.Directory]::CreateDirectory($houdiniPreferences) | Out-Null

$bridgePythonPath = Join-Path $ResolvedRoot 'services\bridge'
$projectSourcePath = Join-Path $ResolvedRoot 'src'
$panelPythonPath = Join-Path $ResolvedRoot 'houdini_package\python_libs'
$packageDirectory = Join-Path $ResolvedRoot 'houdini_package\packages'

$bridgeArguments = @(
    '-B',
    '-m',
    'hia_bridge',
    '--project-root',
    $ResolvedRoot,
    '--codex-exe',
    $CodexExe,
    '--codex-home',
    $CodexHome
)
foreach ($argument in $bridgeArguments) {
    if ($argument -match '[\s"`\r\n]') {
        throw "Unsafe Bridge argument for the fixed launcher: $argument"
    }
}

$bridgeInfo = [System.Diagnostics.ProcessStartInfo]::new()
$bridgeInfo.FileName = $normalizedPython
$bridgeInfo.Arguments = $bridgeArguments -join ' '
$bridgeInfo.WorkingDirectory = $ResolvedRoot
$bridgeInfo.UseShellExecute = $false
$bridgeInfo.CreateNoWindow = $true
$bridgeInfo.RedirectStandardOutput = $true
$bridgeInfo.RedirectStandardError = $false
Set-ChildEnvironment -StartInfo $bridgeInfo -Values @{
    'PYTHONPATH' = "$bridgePythonPath;$projectSourcePath"
    'PYTHONDONTWRITEBYTECODE' = '1'
    'PYTHONNOUSERSITE' = '1'
    'TEMP' = $sessionTemp
    'TMP' = $sessionTemp
    'CODEX_HOME' = $CodexHome
    'HIA_PROJECT_ROOT' = $ResolvedRoot
}

$bridgeProcess = [System.Diagnostics.Process]::new()
$bridgeProcess.StartInfo = $bridgeInfo
$bridgeStarted = $false
$bootstrap = $null
$houdiniProcess = $null
$houdiniExitCode = 0
$ownedCodexPid = $null

try {
    if (-not $bridgeProcess.Start()) {
        throw 'Bridge process did not start'
    }
    $bridgeStarted = $true
    $bootstrapTask = $bridgeProcess.StandardOutput.ReadLineAsync()
    if (-not $bootstrapTask.Wait(60000)) {
        throw 'Bridge did not publish bootstrap data within 60 seconds'
    }
    $bootstrapLine = $bootstrapTask.Result
    if (-not $bootstrapLine) {
        throw 'Bridge exited without bootstrap data'
    }
    $bootstrap = $bootstrapLine | ConvertFrom-Json
    if (-not $bootstrap.ok) {
        throw 'Bridge bootstrap reported failure'
    }
    if ($bootstrap.url -notmatch '^http://127\.0\.0\.1:[0-9]+$') {
        throw "Bridge returned a non-loopback URL: $($bootstrap.url)"
    }
    if (-not $bootstrap.token -or $bootstrap.token.Length -lt 32) {
        throw 'Bridge returned an invalid session token'
    }
    $ownedCodexPid = [int]$bootstrap.codex_pid

    $houdiniInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $houdiniInfo.FileName = $HoudiniExe
    $houdiniInfo.WorkingDirectory = $ResolvedRoot
    $houdiniInfo.UseShellExecute = $false
    $houdiniInfo.CreateNoWindow = $false
    Set-ChildEnvironment -StartInfo $houdiniInfo -Values @{
        'HOUDINI_PACKAGE_DIR' = $packageDirectory
        'HOUDINI_TEMP_DIR' = $sessionTemp
        'HOUDINI_USER_PREF_DIR' = $houdiniPreferences
        'PYTHONPATH' = "$panelPythonPath;$projectSourcePath"
        'PYTHONDONTWRITEBYTECODE' = '1'
        'TEMP' = $sessionTemp
        'TMP' = $sessionTemp
        'HIA_PROJECT_ROOT' = $ResolvedRoot
        'HIA_BRIDGE_URL' = [string]$bootstrap.url
        'HIA_BRIDGE_TOKEN' = [string]$bootstrap.token
    }
    $houdiniProcess = [System.Diagnostics.Process]::new()
    $houdiniProcess.StartInfo = $houdiniInfo
    if (-not $houdiniProcess.Start()) {
        throw 'Houdini process did not start'
    }
    $houdiniProcess.WaitForExit()
    $houdiniExitCode = $houdiniProcess.ExitCode
} finally {
    if ($bridgeStarted -and $null -eq $ownedCodexPid -and -not $bridgeProcess.HasExited) {
        $childProcesses = Get-CimInstance `
            -ClassName Win32_Process `
            -Filter "ParentProcessId = $($bridgeProcess.Id)" `
            -ErrorAction SilentlyContinue
        $ownedCodex = $childProcesses | Where-Object {
            $_.ExecutablePath -and [System.StringComparer]::OrdinalIgnoreCase.Equals(
                $_.ExecutablePath,
                $CodexExe
            )
        } | Select-Object -First 1
        if ($null -ne $ownedCodex) {
            $ownedCodexPid = [int]$ownedCodex.ProcessId
        }
    }
    if ($bridgeStarted -and $null -ne $bootstrap -and -not $bridgeProcess.HasExited) {
        try {
            $headers = @{ Authorization = "Bearer $($bootstrap.token)" }
            Invoke-RestMethod `
                -Method Post `
                -Uri "$($bootstrap.url)/v1/shutdown" `
                -Headers $headers `
                -ContentType 'application/json' `
                -Body '{}' `
                -TimeoutSec 5 | Out-Null
        } catch {
            Write-Warning "Graceful Bridge shutdown failed: $($_.Exception.Message)"
        }
    }
    if ($bridgeStarted -and -not $bridgeProcess.HasExited -and -not $bridgeProcess.WaitForExit(7000)) {
        $bridgeProcess.Kill()
        $bridgeProcess.WaitForExit()
    }
    if ($null -ne $ownedCodexPid) {
        $codexProcess = Get-Process -Id $ownedCodexPid -ErrorAction SilentlyContinue
        if ($null -ne $codexProcess) {
            Write-Warning 'Codex app-server survived Bridge shutdown; terminating the exact child process.'
            Stop-Process -Id $codexProcess.Id -Force
        }
    }
}

exit $houdiniExitCode
