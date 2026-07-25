[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,

    [AllowEmptyString()]
    [string]$Profile = '',

    [Parameter(Mandatory = $true)]
    [string]$BootstrapPython,

    [ValidateSet('auto', 'cuda', 'cpu')]
    [string]$Device = 'auto',

    [AllowEmptyString()]
    [string]$Revision = '',

    [AllowEmptyString()]
    [string]$LogPath = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:HiaUvVersion = '0.11.29'
$script:HiaUvInstallerUri = 'https://astral.sh/uv/0.11.29/install.ps1'
$script:HiaPyPiIndex = 'https://pypi.org/simple'
$script:HiaEmbeddingProjectRoot = ''
$script:HiaEmbeddingInstallLogPath = ''
$script:HiaEmbeddingInstallLock = $null

function Test-HiaEmbeddingOrdinaryFile {
    param([Parameter(Mandatory = $true)][string]$Path)

    try {
        $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
        return (
            $item -is [System.IO.FileInfo] -and
            -not ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint)
        )
    } catch {
        return $false
    }
}

function ConvertTo-HiaEmbeddingSafeLogText {
    param([AllowEmptyString()][string]$Text = '')

    $safe = [string]$Text
    $indexEnvironmentName = (
        '(?:' +
        'uv_(?:config_file|default_index|extra_index_url|find_links|index|' +
        'index_strategy|index_url|insecure_host|no_config|no_index|offline|' +
        'torch_backend)|' +
        'pip_(?:config_file|extra_index_url|find_links|index_url|no_index|' +
        'trusted_host))'
    )
    $sensitiveName = (
        '(?:token|cookie|api[_-]?key|authorization|password|secret|' +
        'auth(?:orization)?[_-]?code|' +
        $indexEnvironmentName + ')'
    )
    $safe = [regex]::Replace(
        $safe,
        '(?i)(Authorization\s*:\s*Basic)\s+[A-Za-z0-9+/=]+',
        '$1 [REDACTED]'
    )
    $safe = [regex]::Replace(
        $safe,
        '(?i)Bearer\s+[A-Za-z0-9._~+\-/=]+',
        'Bearer [REDACTED]'
    )
    $safe = [regex]::Replace(
        $safe,
        '(?i)(https?://)[^@/\s]+@',
        '$1[REDACTED]@'
    )
    $safe = [regex]::Replace(
        $safe,
        '(?i)\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{8,}',
        '[REDACTED]'
    )
    $safe = [regex]::Replace(
        $safe,
        '(?i)(' + $sensitiveName + '\s*[:=]\s*)[^\s,;&]+',
        '$1[REDACTED]'
    )
    $safe = [regex]::Replace(
        $safe,
        '(?i)(' + $indexEnvironmentName +
            '\s*[:=]\s*)(?:(?!\\[rn]|[\r\n"]).)*',
        '$1[REDACTED]'
    )
    return ([regex]::Replace($safe, '[\r\n]+', ' | ')).Trim()
}

function Resolve-HiaEmbeddingLauncherDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [switch]$CreateDirectory
    )

    $root = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
    $runtimeRoot = Join-Path $root '.runtime'
    $launcherRoot = Join-Path $runtimeRoot 'launcher'
    foreach ($directoryPath in @($root, $runtimeRoot, $launcherRoot)) {
        $item = Get-Item `
            -LiteralPath $directoryPath `
            -Force `
            -ErrorAction SilentlyContinue
        if ($null -eq $item) {
            if (
                -not $CreateDirectory -or
                [System.StringComparer]::OrdinalIgnoreCase.Equals(
                    $directoryPath,
                    $root
                )
            ) {
                throw 'Embedding launcher storage directory is unavailable.'
            }
            [System.IO.Directory]::CreateDirectory($directoryPath) | Out-Null
            $item = Get-Item `
                -LiteralPath $directoryPath `
                -Force `
                -ErrorAction Stop
        }
        if (
            $item -isnot [System.IO.DirectoryInfo] -or
            ([int]$item.Attributes -band
                [int][System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
            -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
                [System.IO.Path]::GetFullPath($item.FullName).TrimEnd('\'),
                [System.IO.Path]::GetFullPath($directoryPath).TrimEnd('\')
            )
        ) {
            throw 'Embedding launcher storage contains a reparse point or non-directory object.'
        }
    }
    return $launcherRoot
}

function Resolve-HiaEmbeddingInstallLogPath {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [AllowEmptyString()][string]$RequestedPath = '',
        [switch]$ValidateOnly
    )

    $logRoot = Resolve-HiaEmbeddingLauncherDirectory `
        -ProjectRoot $ProjectRoot `
        -CreateDirectory
    $candidate = if ([string]::IsNullOrWhiteSpace($RequestedPath)) {
        Join-Path $logRoot (
            'embedding-install-{0}-{1}.log' -f
                [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss'),
                [Guid]::NewGuid().ToString('N')
        )
    } else {
        [System.IO.Path]::GetFullPath($RequestedPath)
    }
    if (
        -not [string]::Equals(
            [System.IO.Path]::GetDirectoryName($candidate),
            $logRoot,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -or
        [System.IO.Path]::GetFileName($candidate) -notmatch
            '^embedding-install-[A-Za-z0-9-]+\.log$'
    ) {
        throw 'Embedding install log path must be a project-local launcher log.'
    }
    $existing = Get-Item `
        -LiteralPath $candidate `
        -Force `
        -ErrorAction SilentlyContinue
    if (
        $null -ne $existing -and
        -not (Test-HiaEmbeddingOrdinaryFile -Path $candidate)
    ) {
        throw 'Embedding install log must be an ordinary file.'
    }
    if ($ValidateOnly) {
        if ($null -eq $existing) {
            throw 'Embedding install log is unavailable.'
        }
        return $candidate
    }
    $stream = [System.IO.File]::Open(
        $candidate,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::Write,
        [System.IO.FileShare]::Read
    )
    $stream.Dispose()
    if (-not (Test-HiaEmbeddingOrdinaryFile -Path $candidate)) {
        throw 'Embedding install log creation did not produce an ordinary file.'
    }
    return $candidate
}

function Enter-HiaEmbeddingInstallLock {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$InstallLogPath
    )

    $launcherRoot = Resolve-HiaEmbeddingLauncherDirectory `
        -ProjectRoot $ProjectRoot `
        -CreateDirectory
    $lockPath = Join-Path $launcherRoot 'embedding-install.lock'
    $existing = Get-Item `
        -LiteralPath $lockPath `
        -Force `
        -ErrorAction SilentlyContinue
    if (
        $null -ne $existing -and
        -not (Test-HiaEmbeddingOrdinaryFile -Path $lockPath)
    ) {
        throw 'Embedding install lock must be an ordinary file.'
    }

    $stream = $null
    try {
        $stream = [System.IO.FileStream]::new(
            $lockPath,
            [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::Read
        )
    } catch [System.IO.IOException] {
        $ownerLog = ''
        $metadataStream = $null
        $metadataReader = $null
        try {
            $metadataStream = [System.IO.FileStream]::new(
                $lockPath,
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::Read,
                [System.IO.FileShare]::ReadWrite
            )
            $metadataReader = [System.IO.StreamReader]::new(
                $metadataStream,
                [System.Text.UTF8Encoding]::new($false),
                $true
            )
            $owner = $metadataReader.ReadToEnd() |
                ConvertFrom-Json
            $ownerCandidate = [string]$owner.log_path
            if (-not [string]::IsNullOrWhiteSpace($ownerCandidate)) {
                $ownerLog = Resolve-HiaEmbeddingInstallLogPath `
                    -ProjectRoot $ProjectRoot `
                    -RequestedPath $ownerCandidate `
                    -ValidateOnly
            }
        } catch {
        } finally {
            if ($null -ne $metadataReader) { $metadataReader.Dispose() }
            if ($null -ne $metadataStream) { $metadataStream.Dispose() }
        }
        if ([string]::IsNullOrWhiteSpace($ownerLog)) {
            throw 'Another embedding installation is already running.'
        }
        throw "Another embedding installation is already running. Log: $ownerLog"
    }

    try {
        if (-not (Test-HiaEmbeddingOrdinaryFile -Path $lockPath)) {
            throw 'Embedding install lock creation did not produce an ordinary file.'
        }
        $payload = [ordered]@{
            pid = [int]$PID
            started_at_utc = [DateTime]::UtcNow.ToString('o')
            log_path = $InstallLogPath
        } | ConvertTo-Json -Compress
        $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes(
            $payload + [Environment]::NewLine
        )
        $stream.SetLength(0)
        $stream.Position = 0
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
        return $stream
    } catch {
        $stream.Dispose()
        throw
    }
}

function Write-HiaEmbeddingInstallLog {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('INFO', 'ERROR')]
        [string]$Level,

        [AllowEmptyString()]
        [string]$Message = ''
    )

    if ([string]::IsNullOrWhiteSpace($script:HiaEmbeddingInstallLogPath)) {
        return
    }
    $validatedLogPath = Resolve-HiaEmbeddingInstallLogPath `
        -ProjectRoot $script:HiaEmbeddingProjectRoot `
        -RequestedPath $script:HiaEmbeddingInstallLogPath `
        -ValidateOnly
    $safe = ConvertTo-HiaEmbeddingSafeLogText -Text $Message
    $line = '{0} {1} {2}{3}' -f
        [DateTime]::UtcNow.ToString('o'),
        $Level,
        $safe,
        [Environment]::NewLine
    [System.IO.File]::AppendAllText(
        $validatedLogPath,
        $line,
        [System.Text.UTF8Encoding]::new($false)
    )
}

function ConvertTo-HiaEmbeddingProcessArgument {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Value
    )

    if ($Value -notmatch '[\s"]') {
        return $Value
    }
    $escaped = $Value -replace '(\\*)"', '$1$1\"'
    $escaped = $escaped -replace '(\\+)$', '$1$1'
    return '"' + $escaped + '"'
}

function Invoke-HiaEmbeddingChildProcess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [string[]]$Arguments = @(),

        [hashtable]$Environment = @{},

        [string[]]$RemoveEnvironment = @(),

        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory
    )

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $FilePath
    $startInfo.Arguments = (
        @(
            $Arguments | ForEach-Object {
                ConvertTo-HiaEmbeddingProcessArgument -Value ([string]$_)
            }
        ) -join ' '
    )
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.WorkingDirectory = $WorkingDirectory

    foreach ($name in $RemoveEnvironment) {
        if (-not [string]::IsNullOrWhiteSpace($name)) {
            [void]$startInfo.EnvironmentVariables.Remove([string]$name)
        }
    }
    foreach ($entry in $Environment.GetEnumerator()) {
        $startInfo.EnvironmentVariables[[string]$entry.Key] = [string]$entry.Value
    }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    try {
        if (-not $process.Start()) {
            throw 'child process did not start'
        }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        [void]$stdoutTask.Wait()
        [void]$stderrTask.Wait()
        return [pscustomobject]@{
            exit_code = [int]$process.ExitCode
            stdout = [string]$stdoutTask.Result
            stderr = [string]$stderrTask.Result
        }
    } finally {
        $process.Dispose()
    }
}

function ConvertTo-HiaEmbeddingHashtable {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Value
    )

    $result = @{}
    foreach ($property in $Value.PSObject.Properties) {
        $result[[string]$property.Name] = [string]$property.Value
    }
    return $result
}

function Assert-HiaEmbeddingProcessSucceeded {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Result,

        [Parameter(Mandatory = $true)]
        [string]$Operation
    )

    if ($Result.exit_code -eq 0) {
        Write-HiaEmbeddingInstallLog `
            -Level 'INFO' `
            -Message "$Operation completed."
        return
    }
    $detail = ([string]$Result.stderr).Trim()
    if ([string]::IsNullOrWhiteSpace($detail)) {
        $detail = ([string]$Result.stdout).Trim()
    }
    Write-HiaEmbeddingInstallLog `
        -Level 'ERROR' `
        -Message ("{0}: {1}" -f $Operation, $detail)
    if ($detail.Length -gt 1600) {
        $detail = $detail.Substring($detail.Length - 1600)
    }
    if ([string]::IsNullOrWhiteSpace($detail)) {
        throw "$Operation failed with exit code $($Result.exit_code)."
    }
    throw "$Operation failed with exit code $($Result.exit_code): $detail"
}

function Get-HiaProjectLocalUv {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ProjectRoot,

        [Parameter(Mandatory = $true)]
        [string]$ToolchainRoot,

        [Parameter(Mandatory = $true)]
        [string]$CacheRoot,

        [Parameter(Mandatory = $true)]
        [string]$TempRoot,

        [Parameter(Mandatory = $true)]
        [hashtable]$Environment,

        [Parameter(Mandatory = $true)]
        [string[]]$RemoveEnvironment
    )

    $uvInstallRoot = Join-Path $ToolchainRoot (
        'uv\{0}' -f $script:HiaUvVersion
    )
    $uvExe = Join-Path $uvInstallRoot 'uv.exe'
    $uvCacheRoot = Join-Path $CacheRoot 'uv'
    [System.IO.Directory]::CreateDirectory($uvInstallRoot) | Out-Null
    [System.IO.Directory]::CreateDirectory($uvCacheRoot) | Out-Null
    [System.IO.Directory]::CreateDirectory($TempRoot) | Out-Null

    if (-not (Test-HiaEmbeddingOrdinaryFile -Path $uvExe)) {
        $installerUri = $script:HiaUvInstallerUri
        $installerPath = Join-Path $TempRoot (
            'uv-{0}-install-{1}.ps1' -f
                $script:HiaUvVersion,
                [Guid]::NewGuid().ToString('N')
        )
        Write-HiaEmbeddingInstallLog `
            -Level 'INFO' `
            -Message (
                'Downloading official Astral uv {0} bootstrap to project runtime.' -f
                    $script:HiaUvVersion
            )
        try {
            Invoke-WebRequest `
                -UseBasicParsing `
                -Uri $installerUri `
                -OutFile $installerPath
        } catch {
            $downloadFailure = ConvertTo-HiaEmbeddingSafeLogText `
                -Text ([string]$_.Exception.Message)
            throw (
                "Project-local uv bootstrap download failed: $downloadFailure. " +
                'Retry the launcher repair button, ' +
                "or download $installerUri and run it with UV_UNMANAGED_INSTALL=$uvInstallRoot."
            )
        }
        if (-not (Test-HiaEmbeddingOrdinaryFile -Path $installerPath)) {
            throw 'The downloaded project-local uv bootstrap is unavailable.'
        }

        $bootstrapEnvironment = @{}
        foreach ($entry in $Environment.GetEnumerator()) {
            $bootstrapEnvironment[[string]$entry.Key] = [string]$entry.Value
        }
        $bootstrapEnvironment['UV_CACHE_DIR'] = $uvCacheRoot
        $bootstrapEnvironment['UV_NO_MODIFY_PATH'] = '1'
        $bootstrapEnvironment['UV_UNMANAGED_INSTALL'] = $uvInstallRoot
        $powershellExe = Join-Path $env:SystemRoot (
            'System32\WindowsPowerShell\v1.0\powershell.exe'
        )
        $bootstrapResult = Invoke-HiaEmbeddingChildProcess `
            -FilePath $powershellExe `
            -Arguments @(
                '-NoProfile',
                '-NonInteractive',
                '-ExecutionPolicy', 'Bypass',
                '-File', $installerPath
            ) `
            -Environment $bootstrapEnvironment `
            -RemoveEnvironment $RemoveEnvironment `
            -WorkingDirectory $ProjectRoot
        Assert-HiaEmbeddingProcessSucceeded `
            -Result $bootstrapResult `
            -Operation "Project-local Astral uv $($script:HiaUvVersion) bootstrap"
    }

    if (-not (Test-HiaEmbeddingOrdinaryFile -Path $uvExe)) {
        throw 'Project-local uv bootstrap completed without a usable uv.exe.'
    }
    $versionResult = Invoke-HiaEmbeddingChildProcess `
        -FilePath $uvExe `
        -Arguments @('--version') `
        -Environment $Environment `
        -RemoveEnvironment $RemoveEnvironment `
        -WorkingDirectory $ProjectRoot
    Assert-HiaEmbeddingProcessSucceeded `
        -Result $versionResult `
        -Operation 'Project-local Astral uv version probe'
    $versionText = ([string]$versionResult.stdout).Trim()
    if (
        $versionText -notmatch (
            '^uv\s+{0}(?:\s|$)' -f
                [regex]::Escape($script:HiaUvVersion)
        )
    ) {
        throw (
            'Project-local uv version mismatch. Expected {0}; found {1}.' -f
                $script:HiaUvVersion,
                $versionText
        )
    }
    return [pscustomobject]@{
        exe = $uvExe
        cache_root = $uvCacheRoot
        version = $script:HiaUvVersion
    }
}

function Get-HiaEmbeddingTorchProbe {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonExe,

        [Parameter(Mandatory = $true)]
        [hashtable]$Environment,

        [Parameter(Mandatory = $true)]
        [string[]]$RemoveEnvironment,

        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory
    )

    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
        return $null
    }
    $probeCode = @'
import json
try:
    import torch
    available = bool(torch.cuda.is_available())
    payload = {
        "installed": True,
        "torch_version": str(torch.__version__),
        "torch_cuda_build": str(torch.version.cuda or ""),
        "cuda_available": available,
        "device_name": str(torch.cuda.get_device_name(0)) if available else "",
    }
except Exception as exc:
    payload = {
        "installed": False,
        "torch_version": "",
        "torch_cuda_build": "",
        "cuda_available": False,
        "device_name": "",
        "error": type(exc).__name__,
    }
print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
'@
    $result = Invoke-HiaEmbeddingChildProcess `
        -FilePath $PythonExe `
        -Arguments @('-I', '-B', '-c', $probeCode) `
        -Environment $Environment `
        -RemoveEnvironment $RemoveEnvironment `
        -WorkingDirectory $WorkingDirectory
    if ($result.exit_code -ne 0) {
        return $null
    }
    try {
        return ([string]$result.stdout).Trim() | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Test-HiaNvidiaGpuAvailable {
    try {
        $command = Get-Command 'nvidia-smi.exe' -ErrorAction Stop
        $result = Invoke-HiaEmbeddingChildProcess `
            -FilePath $command.Source `
            -Arguments @('--query-gpu=name', '--format=csv,noheader') `
            -WorkingDirectory $resolvedRoot
        return (
            $result.exit_code -eq 0 -and
            -not [string]::IsNullOrWhiteSpace([string]$result.stdout)
        )
    } catch {
        return $false
    }
}

function Resolve-HiaEmbeddingInstallDevice {
    param(
        [ValidateSet('auto', 'cuda', 'cpu')]
        [string]$RequestedDevice,

        [AllowNull()]
        [object]$TorchProbe,

        [bool]$NvidiaAvailable
    )

    $cudaProperty = if ($null -eq $TorchProbe) {
        $null
    } else {
        $TorchProbe.PSObject.Properties['cuda_available']
    }
    $torchCudaAvailable = (
        $null -ne $cudaProperty -and
        [bool]$cudaProperty.Value
    )
    $cudaCapabilityAvailable = ($torchCudaAvailable -or $NvidiaAvailable)
    $resolvedDevice = if ($RequestedDevice -eq 'auto') {
        if ($cudaCapabilityAvailable) { 'cuda' } else { 'cpu' }
    } else {
        $RequestedDevice
    }
    if ($resolvedDevice -eq 'cuda' -and -not $cudaCapabilityAvailable) {
        throw 'CUDA was requested, but neither CUDA-enabled PyTorch nor an NVIDIA GPU was detected. Choose Device=cpu or restore the NVIDIA driver.'
    }
    return $resolvedDevice
}

try {
$directorySeparators = [char[]]@(
    [System.IO.Path]::DirectorySeparatorChar,
    [System.IO.Path]::AltDirectorySeparatorChar
)
$resolvedRoot = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd(
    $directorySeparators
)
if (-not (Test-Path -LiteralPath $resolvedRoot -PathType Container)) {
    throw 'ProjectRoot is unavailable.'
}
$script:HiaEmbeddingProjectRoot = $resolvedRoot
$script:HiaEmbeddingInstallLogPath = Resolve-HiaEmbeddingInstallLogPath `
    -ProjectRoot $resolvedRoot `
    -RequestedPath $LogPath
Write-HiaEmbeddingInstallLog `
    -Level 'INFO' `
    -Message (
        'Embedding install started: profile={0}; requested_device={1}; uv={2}.' -f
            $Profile,
            $Device,
            $script:HiaUvVersion
    )
$script:HiaEmbeddingInstallLock = Enter-HiaEmbeddingInstallLock `
    -ProjectRoot $resolvedRoot `
    -InstallLogPath $script:HiaEmbeddingInstallLogPath
Write-HiaEmbeddingInstallLog `
    -Level 'INFO' `
    -Message 'Project-local embedding install lock acquired.'

$resolvedBootstrapPython = [System.IO.Path]::GetFullPath($BootstrapPython)
if (-not (Test-Path -LiteralPath $resolvedBootstrapPython -PathType Leaf)) {
    throw 'BootstrapPython is unavailable.'
}

$helperPath = Join-Path $resolvedRoot 'scripts\launcher\install_hia_embedding.py'
if (-not (Test-Path -LiteralPath $helperPath -PathType Leaf)) {
    throw 'The embedding installer helper is unavailable.'
}

# This first probe is read-only and isolated.  It imports the standard-library-
# only contract with bytecode disabled, then returns every canonical path and
# child-process environment value needed by the mutating installation steps.
$planArguments = @(
    '-I',
    '-B',
    $helperPath,
    '--action',
    'plan',
    '--project-root',
    $resolvedRoot
)
if (-not [string]::IsNullOrWhiteSpace($Profile)) {
    $planArguments += @('--profile', $Profile.Trim())
}
if (-not [string]::IsNullOrWhiteSpace($Revision)) {
    $planArguments += @('--revision', $Revision.Trim())
}
$probeEnvironment = @{
    'PYTHONDONTWRITEBYTECODE' = '1'
    'PYTHONNOUSERSITE' = '1'
    'PYTHONUTF8' = '1'
    'PYTHONIOENCODING' = 'utf-8'
}
$planResult = Invoke-HiaEmbeddingChildProcess `
    -FilePath $resolvedBootstrapPython `
    -Arguments $planArguments `
    -Environment $probeEnvironment `
    -RemoveEnvironment @('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'CONDA_PREFIX') `
    -WorkingDirectory $resolvedRoot
Assert-HiaEmbeddingProcessSucceeded `
    -Result $planResult `
    -Operation 'Embedding contract probe'

try {
    $plan = ([string]$planResult.stdout).Trim() | ConvertFrom-Json
} catch {
    throw 'Embedding contract probe returned invalid JSON.'
}
if (
    $null -eq $plan.child_environment -or
    $null -eq $plan.layout -or
    [string]::IsNullOrWhiteSpace([string]$plan.profile_id)
) {
    throw 'Embedding contract probe returned an incomplete plan.'
}

$childEnvironment = ConvertTo-HiaEmbeddingHashtable `
    -Value $plan.child_environment
$removeEnvironment = @($plan.remove_environment | ForEach-Object { [string]$_ })
foreach ($directory in @($plan.required_directories)) {
    [System.IO.Directory]::CreateDirectory([string]$directory) | Out-Null
}

$venvRoot = [string]$plan.layout.venv_root
$workerPython = [string]$plan.layout.worker_python
$workerSource = [string]$plan.layout.worker_source

$venvResult = Invoke-HiaEmbeddingChildProcess `
    -FilePath $resolvedBootstrapPython `
    -Arguments @('-I', '-B', '-m', 'venv', $venvRoot) `
    -Environment $childEnvironment `
    -RemoveEnvironment $removeEnvironment `
    -WorkingDirectory $resolvedRoot
Assert-HiaEmbeddingProcessSucceeded `
    -Result $venvResult `
    -Operation 'Embedding virtual environment creation'
if (-not (Test-Path -LiteralPath $workerPython -PathType Leaf)) {
    throw 'The dedicated embedding Python was not created.'
}

$uv = Get-HiaProjectLocalUv `
    -ProjectRoot $resolvedRoot `
    -ToolchainRoot ([string]$plan.layout.toolchain_root) `
    -CacheRoot ([string]$plan.layout.cache_root) `
    -TempRoot ([string]$plan.layout.temp_root) `
    -Environment $childEnvironment `
    -RemoveEnvironment $removeEnvironment

$torchProbe = Get-HiaEmbeddingTorchProbe `
    -PythonExe $workerPython `
    -Environment $childEnvironment `
    -RemoveEnvironment $removeEnvironment `
    -WorkingDirectory $resolvedRoot
$nvidiaAvailable = Test-HiaNvidiaGpuAvailable
$torchCudaAvailable = (
    $null -ne $torchProbe -and
    [bool]$torchProbe.cuda_available
)
$resolvedDevice = Resolve-HiaEmbeddingInstallDevice `
    -RequestedDevice $Device `
    -TorchProbe $torchProbe `
    -NvidiaAvailable $nvidiaAvailable

if (
    $resolvedDevice -eq 'cuda' -and
    -not $torchCudaAvailable
) {
    $cudaTorchResult = Invoke-HiaEmbeddingChildProcess `
        -FilePath ([string]$uv.exe) `
        -Arguments @(
            '--no-config',
            'pip',
            'install',
            '--python', $workerPython,
            '--default-index', $script:HiaPyPiIndex,
            '--torch-backend=cu128',
            '--reinstall-package', 'torch',
            'torch'
        ) `
        -Environment $childEnvironment `
        -RemoveEnvironment $removeEnvironment `
        -WorkingDirectory $resolvedRoot
    Assert-HiaEmbeddingProcessSucceeded `
        -Result $cudaTorchResult `
        -Operation 'Official CUDA PyTorch installation'
}

$workerInstallArguments = @(
    '--no-config',
    'pip',
    'install',
    '--python', $workerPython,
    '--default-index', $script:HiaPyPiIndex,
    '--reinstall-package', [string]$plan.worker_distribution
)
if ($resolvedDevice -eq 'cuda') {
    $workerInstallArguments += '--torch-backend=cu128'
}
$workerInstallArguments += $workerSource
$workerInstallResult = Invoke-HiaEmbeddingChildProcess `
    -FilePath ([string]$uv.exe) `
    -Arguments $workerInstallArguments `
    -Environment $childEnvironment `
    -RemoveEnvironment $removeEnvironment `
    -WorkingDirectory $resolvedRoot
Assert-HiaEmbeddingProcessSucceeded `
    -Result $workerInstallResult `
    -Operation 'Embedding worker installation'

$verifiedTorch = Get-HiaEmbeddingTorchProbe `
    -PythonExe $workerPython `
    -Environment $childEnvironment `
    -RemoveEnvironment $removeEnvironment `
    -WorkingDirectory $resolvedRoot
if ($null -eq $verifiedTorch -or -not [bool]$verifiedTorch.installed) {
    throw 'PyTorch is unavailable in the project-local embedding environment.'
}
if ($resolvedDevice -eq 'cuda' -and -not [bool]$verifiedTorch.cuda_available) {
    throw 'CUDA PyTorch installation completed, but torch.cuda.is_available() is false. The GPU repair was not accepted.'
}

$downloadResult = Invoke-HiaEmbeddingChildProcess `
    -FilePath $workerPython `
    -Arguments @(
        '-I',
        '-B',
        $helperPath,
        '--action',
        'download',
        '--project-root',
        $resolvedRoot,
        '--profile',
        [string]$plan.profile_id,
        '--revision',
        [string]$plan.revision
    ) `
    -Environment $childEnvironment `
    -RemoveEnvironment $removeEnvironment `
    -WorkingDirectory $resolvedRoot
Assert-HiaEmbeddingProcessSucceeded `
    -Result $downloadResult `
    -Operation 'Selected embedding model installation'

$downloadOutput = ([string]$downloadResult.stdout).Trim()
if ([string]::IsNullOrWhiteSpace($downloadOutput)) {
    throw 'Selected embedding model installation returned no result.'
}
try {
    $downloadPayload = $downloadOutput | ConvertFrom-Json
} catch {
    throw 'Selected embedding model installation returned invalid JSON.'
}
$resultPayload = [ordered]@{
    status = [string]$downloadPayload.status
    profile_id = [string]$downloadPayload.profile_id
    model_id = [string]$downloadPayload.model_id
    model_dir = [string]$downloadPayload.model_dir
    requested_device = $Device
    resolved_device = $resolvedDevice
    torch_version = [string]$verifiedTorch.torch_version
    torch_cuda_build = [string]$verifiedTorch.torch_cuda_build
    cuda_available = [bool]$verifiedTorch.cuda_available
    device_name = [string]$verifiedTorch.device_name
    uv_version = [string]$uv.version
    log_path = [string]$script:HiaEmbeddingInstallLogPath
}
Write-HiaEmbeddingInstallLog `
    -Level 'INFO' `
    -Message (
        'Embedding install completed: profile={0}; resolved_device={1}; uv={2}.' -f
            [string]$plan.profile_id,
            $resolvedDevice,
            [string]$uv.version
    )
Write-Output ($resultPayload | ConvertTo-Json -Compress)
} catch {
    $failure = ConvertTo-HiaEmbeddingSafeLogText `
        -Text ([string]$_.Exception.Message)
    try {
        Write-HiaEmbeddingInstallLog -Level 'ERROR' -Message $failure
    } catch { }
    $consoleMessage = "Embedding installation failed: $failure"
    if (-not [string]::IsNullOrWhiteSpace($script:HiaEmbeddingInstallLogPath)) {
        $consoleMessage += (
            " Project-local log: $($script:HiaEmbeddingInstallLogPath)"
        )
    }
    [Console]::Error.WriteLine($consoleMessage)
    exit 1
} finally {
    if ($null -ne $script:HiaEmbeddingInstallLock) {
        $script:HiaEmbeddingInstallLock.Dispose()
        $script:HiaEmbeddingInstallLock = $null
    }
}
