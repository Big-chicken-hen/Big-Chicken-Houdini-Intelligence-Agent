[CmdletBinding()]
param(
    [string]$BridgePython = 'D:\Python_3.10\python.exe',
    [string]$HoudiniExe = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ExpectedRoot = 'E:\houdini-intelligence-agent'
$CodexExe = 'E:\houdini-intelligence-agent\.runtime\toolchains\codex\0.144.3\codex.exe'
$CodexHome = 'E:\houdini-intelligence-agent\.runtime\codex-home'

function Get-HoudiniCandidatePaths {
    param([string]$RequestedPath)

    if ($RequestedPath) {
        return @($RequestedPath)
    }

    $candidates = @()
    if ($env:HFS) {
        $candidates += (Join-Path $env:HFS 'bin\houdini.exe')
    }
    foreach ($command in @(Get-Command -Name 'houdini.exe' -All -ErrorAction SilentlyContinue)) {
        if ($command.Source) {
            $candidates += [string]$command.Source
        }
    }

    $appPathKeys = @(
        'Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\houdini.exe',
        'Registry::HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\houdini.exe'
    )
    foreach ($key in $appPathKeys) {
        if (-not (Test-Path -LiteralPath $key)) { continue }
        $registryKey = Get-Item -LiteralPath $key -ErrorAction SilentlyContinue
        if ($null -ne $registryKey) {
            $registeredPath = $registryKey.GetValue('')
            if ($registeredPath) {
                $candidates += [string]$registeredPath
            }
        }
    }

    $uninstallRoots = @(
        'Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
        'Registry::HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'
    )
    foreach ($root in $uninstallRoots) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        foreach ($key in @(Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue)) {
            $properties = Get-ItemProperty -LiteralPath $key.PSPath -ErrorAction SilentlyContinue
            $displayName = $null
            $installLocation = $null
            if ($null -ne $properties) {
                $displayProperty = $properties.PSObject.Properties['DisplayName']
                $installProperty = $properties.PSObject.Properties['InstallLocation']
                if ($null -ne $displayProperty) {
                    $displayName = [string]$displayProperty.Value
                }
                if ($null -ne $installProperty) {
                    $installLocation = [string]$installProperty.Value
                }
            }
            if (
                $displayName -match '(?i)\bHoudini\b' -and
                $installLocation
            ) {
                $candidates += (Join-Path $installLocation 'bin\houdini.exe')
            }
        }
    }

    return @($candidates | Where-Object { $_ } | Sort-Object -Unique)
}

function Test-HoudiniExecutableMetadata {
    param(
        [AllowEmptyString()][string]$ProductName,
        [AllowEmptyString()][string]$FileDescription,
        [AllowEmptyString()][string]$CompanyName
    )

    $identityIsHoudini = (
        $ProductName -match '(?i)\bHoudini\b' -or
        $FileDescription -match '(?i)\bHoudini\b'
    )
    $companyIsSideEffects = (
        $CompanyName -match '(?i)^Side Effects Software(?: Inc\.)?$'
    )
    $identityFieldsAreEmpty = (
        [string]::IsNullOrWhiteSpace($ProductName) -and
        [string]::IsNullOrWhiteSpace($FileDescription)
    )
    return (
        $companyIsSideEffects -and
        ($identityIsHoudini -or $identityFieldsAreEmpty)
    )
}

function Resolve-HoudiniExecutable {
    param([string]$RequestedPath)

    $explicit = [bool]$RequestedPath
    $resolvedCandidates = @()
    foreach ($candidate in @(Get-HoudiniCandidatePaths -RequestedPath $RequestedPath)) {
        try {
            $rawCandidatePath = [string]$candidate
            if (
                $rawCandidatePath -notmatch '^[A-Za-z]:\\' -or
                $rawCandidatePath.Substring(2).Contains(':')
            ) {
                throw "Houdini must use an ordinary absolute drive path: $rawCandidatePath"
            }
            $candidatePath = [System.IO.Path]::GetFullPath($rawCandidatePath)
            if (
                $candidatePath -notmatch '^[A-Za-z]:\\' -or
                $candidatePath.Substring(2).Contains(':')
            ) {
                throw "UNC, device, and ADS Houdini paths are forbidden: $candidatePath"
            }
            if (-not (Test-Path -LiteralPath $candidatePath -PathType Leaf)) {
                throw "Houdini executable does not exist: $candidatePath"
            }
            $candidateItem = Get-Item -LiteralPath $candidatePath -Force
            if (($candidateItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Houdini executable is a reparse point: $candidatePath"
            }
            $candidateParent = $candidateItem.Directory
            while ($null -ne $candidateParent) {
                if (($candidateParent.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                    throw "Houdini candidate path traverses a reparse point: $($candidateParent.FullName)"
                }
                $candidateParent = $candidateParent.Parent
            }
            $resolvedPath = (Resolve-Path -LiteralPath $candidatePath).Path
            if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($candidatePath, $resolvedPath)) {
                throw "Houdini candidate changed during path resolution: $candidatePath"
            }
            $item = Get-Item -LiteralPath $resolvedPath -Force
            if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($item.Name, 'houdini.exe')) {
                throw "Candidate is not houdini.exe: $resolvedPath"
            }
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Houdini executable is a reparse point: $resolvedPath"
            }
            $current = $item.Directory
            while ($null -ne $current) {
                if (($current.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                    throw "Houdini path traverses a reparse point: $($current.FullName)"
                }
                $current = $current.Parent
            }
            $productName = [string]$item.VersionInfo.ProductName
            $description = [string]$item.VersionInfo.FileDescription
            $companyName = [string]$item.VersionInfo.CompanyName
            $build = [string]$item.VersionInfo.ProductVersion
            if (-not $build) {
                $build = [string]$item.VersionInfo.FileVersion
            }
            if (-not (Test-HoudiniExecutableMetadata `
                -ProductName $productName `
                -FileDescription $description `
                -CompanyName $companyName
            )) {
                throw "Candidate metadata does not identify Houdini: $resolvedPath"
            }
            if (-not $build) {
                throw "Houdini candidate has no readable build metadata: $resolvedPath"
            }
            $resolvedCandidates += [pscustomobject]@{
                Path = $resolvedPath
                Build = $build
            }
        } catch {
            if ($explicit) { throw }
        }
    }

    $resolvedCandidates = @($resolvedCandidates | Sort-Object -Property Path -Unique)
    if ($resolvedCandidates.Count -eq 0) {
        throw 'No Houdini executable was discovered. Pass -HoudiniExe with an exact absolute path.'
    }
    if ($resolvedCandidates.Count -ne 1) {
        throw 'Multiple Houdini executables were discovered. Pass -HoudiniExe to select one explicitly.'
    }
    return $resolvedCandidates[0]
}

function Get-ExactOwnedCodexProcess {
    param(
        [Parameter(Mandatory = $true)][int]$ParentProcessId,
        [Parameter(Mandatory = $true)][string]$CodexExecutablePath,
        [int]$ExpectedProcessId = 0
    )

    $matches = @(
        Get-CimInstance `
            -ClassName Win32_Process `
            -Filter "ParentProcessId = $ParentProcessId" `
            -ErrorAction SilentlyContinue |
        Where-Object {
            $_.ExecutablePath -and
            [System.StringComparer]::OrdinalIgnoreCase.Equals(
                [System.IO.Path]::GetFullPath([string]$_.ExecutablePath),
                [System.IO.Path]::GetFullPath($CodexExecutablePath)
            ) -and
            ($ExpectedProcessId -le 0 -or [int]$_.ProcessId -eq $ExpectedProcessId)
        }
    )
    if ($matches.Count -gt 1) {
        throw 'Refusing to select an ambiguous Codex child process.'
    }
    if ($matches.Count -eq 1) {
        return $matches[0]
    }
    return $null
}

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
$houdiniMetadata = Resolve-HoudiniExecutable -RequestedPath $HoudiniExe
$HoudiniExe = [string]$houdiniMetadata.Path
$normalizedPython = [System.IO.Path]::GetFullPath($BridgePython)
if (
    $normalizedPython -notmatch '^[A-Za-z]:\\' -or
    $normalizedPython.Substring(2).Contains(':')
) {
    throw "Bridge Python must use an ordinary absolute drive path: $normalizedPython"
}
if ($normalizedPython -match '(?i)\\(AppData|WindowsApps)\\') {
    throw "Bridge Python may not come from AppData or WindowsApps: $normalizedPython"
}
if (-not (Test-Path -LiteralPath $normalizedPython -PathType Leaf)) {
    throw "Bridge Python executable was not found: $normalizedPython"
}
$resolvedPython = (Resolve-Path -LiteralPath $normalizedPython).Path
if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($normalizedPython, $resolvedPython)) {
    throw "Bridge Python changed during path resolution: $normalizedPython"
}
$pythonItem = Get-Item -LiteralPath $resolvedPython -Force
if (($pythonItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "Bridge Python executable is a reparse point: $resolvedPython"
}
$pythonParent = $pythonItem.Directory
while ($null -ne $pythonParent) {
    if (($pythonParent.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Bridge Python path traverses a reparse point: $($pythonParent.FullName)"
    }
    $pythonParent = $pythonParent.Parent
}
$normalizedPython = $resolvedPython

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
        throw "Unsafe Bridge argument for the launcher: $argument"
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
$houdiniStarted = $false
$houdiniExited = $false
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
    if ($null -eq $bootstrap.scene -or $bootstrap.scene.profile -ne 'p2-v-b2-read-only') {
        throw 'Bridge did not publish the Gate B2 read-only scene profile'
    }
    if ($bootstrap.scene.launch_id -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') {
        throw 'Bridge returned an invalid scene launch ID'
    }
    $sceneGeneration = [int]$bootstrap.scene.generation
    if ($sceneGeneration -lt 0) {
        throw 'Bridge returned an invalid scene generation'
    }
    if ($bootstrap.scene.process_nonce -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{15,127}$') {
        throw 'Bridge returned an invalid Houdini process nonce'
    }
    if (-not $bootstrap.scene.executor_token -or $bootstrap.scene.executor_token.Length -lt 32) {
        throw 'Bridge returned an invalid scene executor token'
    }
    if ($bootstrap.scene.schema_version -ne '0.2.0') {
        throw 'Bridge returned an unexpected Houdini read Schema version'
    }
    if ($bootstrap.scene.schema_digest -notmatch '^[A-Fa-f0-9]{64}$') {
        throw 'Bridge returned an invalid Houdini read Schema digest'
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
        'HIA_SCENE_PROFILE' = [string]$bootstrap.scene.profile
        'HIA_BRIDGE_LAUNCH_ID' = [string]$bootstrap.scene.launch_id
        'HIA_BRIDGE_GENERATION' = [string]$sceneGeneration
        'HIA_HOUDINI_PROCESS_NONCE' = [string]$bootstrap.scene.process_nonce
        'HIA_SCENE_EXECUTOR_TOKEN' = [string]$bootstrap.scene.executor_token
        'HIA_HOUDINI_SCHEMA_VERSION' = [string]$bootstrap.scene.schema_version
        'HIA_HOUDINI_SCHEMA_DIGEST' = [string]$bootstrap.scene.schema_digest
    }
    $houdiniProcess = [System.Diagnostics.Process]::new()
    $houdiniProcess.StartInfo = $houdiniInfo
    if (-not $houdiniProcess.Start()) {
        throw 'Houdini process did not start'
    }
    $houdiniStarted = $true
    $houdiniProcess.WaitForExit()
    $houdiniExited = $houdiniProcess.HasExited
    $houdiniExitCode = $houdiniProcess.ExitCode
} finally {
    $bridgeCleanupAllowed = (-not $houdiniStarted) -or (
        $houdiniExited -and
        $null -ne $houdiniProcess -and
        $houdiniProcess.HasExited
    )
    if (-not $bridgeCleanupAllowed -and $bridgeStarted -and -not $bridgeProcess.HasExited) {
        Write-Warning 'Houdini exit was not confirmed; leaving the shared Bridge running.'
    }
    if ($bridgeCleanupAllowed -and $bridgeStarted -and $null -ne $bootstrap -and -not $bridgeProcess.HasExited) {
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
    if ($bridgeCleanupAllowed -and $bridgeStarted -and -not $bridgeProcess.HasExited -and -not $bridgeProcess.WaitForExit(7000)) {
        $bridgeProcess.Kill()
        $bridgeProcess.WaitForExit()
    }
    if ($bridgeCleanupAllowed -and $bridgeStarted) {
        try {
            $expectedCodexPid = 0
            if ($null -ne $ownedCodexPid) {
                $expectedCodexPid = [int]$ownedCodexPid
            }
            $ownedCodexProcess = Get-ExactOwnedCodexProcess `
                -ParentProcessId $bridgeProcess.Id `
                -CodexExecutablePath $CodexExe `
                -ExpectedProcessId $expectedCodexPid
            if ($null -ne $ownedCodexProcess) {
                Write-Warning 'Codex app-server survived Bridge shutdown; terminating the exact parent/executable child.'
                Stop-Process -Id ([int]$ownedCodexProcess.ProcessId) -Force
            }
        } catch {
            Write-Warning "Exact Codex child cleanup could not be completed safely: $($_.Exception.Message)"
        }
    }
}

exit $houdiniExitCode
