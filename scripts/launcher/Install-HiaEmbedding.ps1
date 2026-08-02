[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,

    [AllowEmptyString()]
    [string]$Profile = '',

    [AllowEmptyString()]
    [string]$BootstrapPython = '',

    [ValidateSet('auto', 'cuda', 'cpu')]
    [string]$Device = 'auto',

    [AllowEmptyString()]
    [string]$Revision = '',

    [AllowEmptyString()]
    [string]$LogPath = '',

    [switch]$KnowledgeParserOnly,

    [switch]$Repair
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$script:HiaUvVersion = '0.11.29'
$script:HiaUvInstallerUri = 'https://astral.sh/uv/0.11.29/install.ps1'
$script:HiaUvDownloadTimeoutSeconds = 120
$script:HiaUvDownloadProcessTimeoutSeconds = 135
$script:HiaUvDownloadAttempts = 3
$script:HiaPyPiIndex = 'https://pypi.org/simple'
$script:HiaKnowledgePythonVersion = '3.10.11'
$script:HiaKnowledgeParserRequirement = 'pypdf==6.14.2'
$script:HiaEmbeddingSmokeTimeoutSeconds = 300
$script:HiaManagedVenvMarkerName = '.hia-managed-venv.json'
$script:HiaManagedVenvMarkerSchema = 'hia-managed-python-venv/1'
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
    $proxyEnvironmentName = '(?:(?:http|https|all|no)_proxy)'
    $sensitiveEnvironmentName = (
        '(?:' + $indexEnvironmentName + '|' + $proxyEnvironmentName + ')'
    )
    $sensitiveName = (
        '(?:token|cookie|api[_-]?key|authorization|password|secret|' +
        'auth(?:orization)?[_-]?code|' +
        $sensitiveEnvironmentName + ')'
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
        '(?i)(' + $sensitiveEnvironmentName +
            '\s*[:=]\s*)(?:(?!\\[rn]|[\r\n"]).)*',
        '$1[REDACTED]'
    )
    return ([regex]::Replace($safe, '[\r\n]+', ' | ')).Trim()
}

function Format-HiaEmbeddingModelSelectionLog {
    param(
        [Parameter(Mandatory = $true)][string]$ModelId,
        [Parameter(Mandatory = $true)][string]$Revision,
        [Parameter(Mandatory = $true)][string]$ModelDirectory,
        [Parameter(Mandatory = $true)][string]$RepositorySize
    )

    return (
        (
            'Selected model source: https://huggingface.co/{0} revision {1}; ' +
            'target: {2}; official model files are about {3} GB. ' +
            'Interrupted downloads reuse the project-local Hugging Face cache ' +
            'when the repair action is retried.'
        ) -f $ModelId, $Revision, $ModelDirectory, $RepositorySize
    )
}

function Format-HiaEmbeddingUvDownloadAttemptLog {
    param(
        [Parameter(Mandatory = $true)][int]$Attempt,
        [Parameter(Mandatory = $true)][int]$Attempts,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds
    )

    return (
        'uv bootstrap download attempt {0}/{1}; timeout {2}s.' -f
            $Attempt,
            $Attempts,
            $TimeoutSeconds
    )
}

function Format-HiaEmbeddingUvDownloadRetryLog {
    param(
        [Parameter(Mandatory = $true)][int]$Attempt,
        [Parameter(Mandatory = $true)][string]$Failure,
        [Parameter(Mandatory = $true)][int]$DelaySeconds
    )

    return (
        (
            'uv bootstrap download attempt {0} failed: {1}. ' +
            'Retrying in {2}s.'
        ) -f $Attempt, $Failure, $DelaySeconds
    )
}

function Format-HiaEmbeddingUvDownloadFailure {
    param(
        [Parameter(Mandatory = $true)][int]$Attempts,
        [Parameter(Mandatory = $true)][string]$Failure,
        [Parameter(Mandatory = $true)][string]$InstallerUri,
        [Parameter(Mandatory = $true)][string]$InstallRoot
    )

    return (
        (
            'Project-local uv bootstrap download failed after {0} bounded ' +
            'attempts: {1}. Retry the launcher repair button, or download ' +
            '{2} and run it with UV_UNMANAGED_INSTALL={3}.'
        ) -f $Attempts, $Failure, $InstallerUri, $InstallRoot
    )
}

function Remove-HiaEmbeddingInstallerPartial {
    param([Parameter(Mandatory = $true)][string]$Path)

    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($null -eq $item) { return }
    if (-not (Test-HiaEmbeddingOrdinaryFile -Path $Path)) {
        throw 'The uv bootstrap partial path is not an ordinary file.'
    }
    [System.IO.File]::Delete([System.IO.Path]::GetFullPath($Path))
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
        [ValidateSet('INFO', 'WARNING', 'ERROR')]
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
        [string]$WorkingDirectory,

        [ValidateRange(0, 3600)]
        [int]$TimeoutSeconds = 0
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
    $startInfo.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $startInfo.StandardErrorEncoding = [System.Text.UTF8Encoding]::new($false)
    $startInfo.WorkingDirectory = $WorkingDirectory

    $savedEnvironment = @{}
    $environmentNames = @(
        @($RemoveEnvironment) +
        @($Environment.Keys | ForEach-Object { [string]$_ })
    ) | Sort-Object -Unique
    foreach ($name in $environmentNames) {
        $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable(
            $name,
            [EnvironmentVariableTarget]::Process
        )
    }
    foreach ($name in $RemoveEnvironment) {
        if (-not [string]::IsNullOrWhiteSpace($name)) {
            [Environment]::SetEnvironmentVariable(
                [string]$name,
                $null,
                [EnvironmentVariableTarget]::Process
            )
        }
    }
    foreach ($entry in $Environment.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable(
            [string]$entry.Key,
            [string]$entry.Value,
            [EnvironmentVariableTarget]::Process
        )
    }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    try {
        if (-not $process.Start()) {
            throw 'child process did not start'
        }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $completed = if ($TimeoutSeconds -gt 0) {
            $process.WaitForExit($TimeoutSeconds * 1000)
        } else {
            $process.WaitForExit()
            $true
        }
        if (-not $completed) {
            try {
                $process.Kill()
            } catch { }
            $process.WaitForExit()
            [void]$stdoutTask.Wait()
            [void]$stderrTask.Wait()
            throw (
                'Child process timed out after {0} seconds.' -f
                    $TimeoutSeconds
            )
        }
        [void]$stdoutTask.Wait()
        [void]$stderrTask.Wait()
        return [pscustomobject]@{
            exit_code = [int]$process.ExitCode
            stdout = [string]$stdoutTask.Result
            stderr = [string]$stderrTask.Result
        }
    } finally {
        $process.Dispose()
        foreach ($name in $environmentNames) {
            [Environment]::SetEnvironmentVariable(
                [string]$name,
                $savedEnvironment[$name],
                [EnvironmentVariableTarget]::Process
            )
        }
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
        $powershellExe = Join-Path $env:SystemRoot (
            'System32\WindowsPowerShell\v1.0\powershell.exe'
        )
        $downloadEnvironment = @{}
        foreach ($entry in $Environment.GetEnumerator()) {
            $downloadEnvironment[[string]$entry.Key] = [string]$entry.Value
        }
        $downloadEnvironment['HIA_UV_BOOTSTRAP_URI'] = $installerUri
        $downloadEnvironment['HIA_UV_BOOTSTRAP_PATH'] = $installerPath
        $downloadScript = @"
`$ErrorActionPreference = 'Stop'
`$ProgressPreference = 'SilentlyContinue'
Invoke-WebRequest ``
    -UseBasicParsing ``
    -TimeoutSec $($script:HiaUvDownloadTimeoutSeconds) ``
    -Uri ([string]`$env:HIA_UV_BOOTSTRAP_URI) ``
    -OutFile ([string]`$env:HIA_UV_BOOTSTRAP_PATH)
"@
        $downloadCommand = [Convert]::ToBase64String(
            [System.Text.Encoding]::Unicode.GetBytes($downloadScript)
        )
        $downloadFailures = [System.Collections.Generic.List[string]]::new()
        $downloaded = $false
        for (
            $attempt = 1;
            $attempt -le $script:HiaUvDownloadAttempts;
            $attempt++
        ) {
            Remove-HiaEmbeddingInstallerPartial -Path $installerPath
            Write-HiaEmbeddingInstallLog `
                -Level 'INFO' `
                -Message (Format-HiaEmbeddingUvDownloadAttemptLog `
                    -Attempt $attempt `
                    -Attempts $script:HiaUvDownloadAttempts `
                    -TimeoutSeconds $script:HiaUvDownloadTimeoutSeconds)
            try {
                $uvDownloadResult = Invoke-HiaEmbeddingChildProcess `
                    -FilePath $powershellExe `
                    -Arguments @(
                        '-NoProfile',
                        '-NonInteractive',
                        '-ExecutionPolicy', 'Bypass',
                        '-EncodedCommand', $downloadCommand
                    ) `
                    -Environment $downloadEnvironment `
                    -RemoveEnvironment $RemoveEnvironment `
                    -WorkingDirectory $ProjectRoot `
                    -TimeoutSeconds (
                        $script:HiaUvDownloadProcessTimeoutSeconds
                    )
                if ([int]$uvDownloadResult.exit_code -ne 0) {
                    $detail = ([string]$uvDownloadResult.stderr).Trim()
                    if ([string]::IsNullOrWhiteSpace($detail)) {
                        $detail = ([string]$uvDownloadResult.stdout).Trim()
                    }
                    throw "download process exited with code $($uvDownloadResult.exit_code): $detail"
                }
                if (
                    -not (Test-HiaEmbeddingOrdinaryFile -Path $installerPath) -or
                    (Get-Item -LiteralPath $installerPath -Force).Length -le 0
                ) {
                    throw 'download completed without a usable installer file'
                }
                $downloaded = $true
                break
            } catch {
                $downloadFailure = ConvertTo-HiaEmbeddingSafeLogText `
                    -Text ([string]$_.Exception.Message)
                [void]$downloadFailures.Add($downloadFailure)
                Remove-HiaEmbeddingInstallerPartial -Path $installerPath
                if ($attempt -lt $script:HiaUvDownloadAttempts) {
                    $delay = 2 * $attempt
                    Write-HiaEmbeddingInstallLog `
                        -Level 'WARN' `
                        -Message (Format-HiaEmbeddingUvDownloadRetryLog `
                            -Attempt $attempt `
                            -Failure $downloadFailure `
                            -DelaySeconds $delay)
                    Start-Sleep -Seconds $delay
                }
            }
        }
        if (-not $downloaded) {
            $lastFailure = if ($downloadFailures.Count -gt 0) {
                [string]$downloadFailures[$downloadFailures.Count - 1]
            } else {
                'unknown download failure'
            }
            throw (Format-HiaEmbeddingUvDownloadFailure `
                -Attempts $script:HiaUvDownloadAttempts `
                -Failure $lastFailure `
                -InstallerUri $installerUri `
                -InstallRoot $uvInstallRoot)
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
        -Arguments @('-I', '-X', 'utf8', '-B', '-c', $probeCode) `
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
    $resolvedDevice = if ($RequestedDevice -eq 'auto') {
        # Auto only selects a device that the project venv has already proved
        # runnable. nvidia-smi is hardware evidence for an explicit CUDA repair,
        # not proof that the current Torch/driver pair can execute CUDA.
        if ($torchCudaAvailable) { 'cuda' } else { 'cpu' }
    } else {
        $RequestedDevice
    }
    if (
        $resolvedDevice -eq 'cuda' -and
        -not ($torchCudaAvailable -or $NvidiaAvailable)
    ) {
        throw 'CUDA was requested, but neither CUDA-enabled PyTorch nor an NVIDIA GPU was detected. Choose Device=cpu or restore the NVIDIA driver.'
    }
    return $resolvedDevice
}

function Assert-HiaKnowledgeProjectDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$Path,
        [switch]$Create
    )

    $root = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
    $candidate = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
    $prefix = $root + [System.IO.Path]::DirectorySeparatorChar
    if (
        -not $candidate.StartsWith(
            $prefix,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw 'Knowledge environment directory escaped the project root.'
    }
    $relative = $candidate.Substring($prefix.Length)
    $current = $root
    $rootItem = Get-Item -LiteralPath $root -Force -ErrorAction Stop
    if (
        $rootItem -isnot [System.IO.DirectoryInfo] -or
        ([int]$rootItem.Attributes -band
            [int][System.IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw 'Knowledge environment project root is unsafe.'
    }
    foreach ($part in @($relative -split '[\\/]')) {
        if ([string]::IsNullOrWhiteSpace($part)) { continue }
        $current = Join-Path $current $part
        $item = Get-Item `
            -LiteralPath $current `
            -Force `
            -ErrorAction SilentlyContinue
        if ($null -eq $item) {
            if (-not $Create) {
                continue
            }
            [System.IO.Directory]::CreateDirectory($current) | Out-Null
            $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop
        }
        if (
            $item -isnot [System.IO.DirectoryInfo] -or
            ([int]$item.Attributes -band
                [int][System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
            -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
                [System.IO.Path]::GetFullPath($item.FullName).TrimEnd('\'),
                [System.IO.Path]::GetFullPath($current).TrimEnd('\')
            )
        ) {
            throw 'Knowledge environment contains a reparse point or non-directory object.'
        }
    }
    return $candidate
}

function Get-HiaKnowledgeRemoveEnvironment {
    return @(
        'PYTHONHOME',
        'PYTHONPATH',
        'VIRTUAL_ENV',
        'CONDA_PREFIX',
        'UV_CONFIG_FILE',
        'UV_DEFAULT_INDEX',
        'UV_EXTRA_INDEX_URL',
        'UV_FIND_LINKS',
        'UV_INDEX',
        'UV_INDEX_STRATEGY',
        'UV_INDEX_URL',
        'UV_INSECURE_HOST',
        'UV_NO_CONFIG',
        'UV_NO_INDEX',
        'UV_OFFLINE',
        'UV_PYTHON',
        'UV_PYTHON_DOWNLOADS',
        'UV_PYTHON_INSTALL_DIR',
        'UV_TORCH_BACKEND',
        'PIP_CONFIG_FILE',
        'PIP_EXTRA_INDEX_URL',
        'PIP_FIND_LINKS',
        'PIP_INDEX_URL',
        'PIP_NO_INDEX',
        'PIP_TRUSTED_HOST'
    )
}

function New-HiaKnowledgeChildEnvironment {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$CacheRoot,
        [Parameter(Mandatory = $true)][string]$TempRoot,
        [Parameter(Mandatory = $true)][string]$PythonInstallRoot
    )

    $homeRoot = Join-Path $CacheRoot 'home'
    $uvCacheRoot = Join-Path $CacheRoot 'uv'
    $huggingFaceRoot = Join-Path $CacheRoot 'huggingface'
    $transformersRoot = Join-Path $CacheRoot 'transformers'
    $torchRoot = Join-Path $CacheRoot 'torch'
    foreach ($directory in @(
        $CacheRoot,
        $TempRoot,
        $homeRoot,
        $uvCacheRoot,
        $huggingFaceRoot,
        $transformersRoot,
        $torchRoot,
        $PythonInstallRoot
    )) {
        Assert-HiaKnowledgeProjectDirectory `
            -ProjectRoot $ProjectRoot `
            -Path $directory `
            -Create | Out-Null
    }
    return @{
        'PYTHONNOUSERSITE' = '1'
        'PYTHONDONTWRITEBYTECODE' = '1'
        'PYTHONUTF8' = '1'
        'PYTHONIOENCODING' = 'utf-8'
        'UV_CACHE_DIR' = $uvCacheRoot
        'UV_DEFAULT_INDEX' = $script:HiaPyPiIndex
        'UV_PYTHON_INSTALL_DIR' = $PythonInstallRoot
        'UV_NO_MODIFY_PATH' = '1'
        'HOME' = $homeRoot
        'XDG_CACHE_HOME' = $homeRoot
        'HF_HOME' = $huggingFaceRoot
        'HUGGINGFACE_HUB_CACHE' = $huggingFaceRoot
        'TRANSFORMERS_CACHE' = $transformersRoot
        'TORCH_HOME' = $torchRoot
        'TMP' = $TempRoot
        'TEMP' = $TempRoot
    }
}

function Get-HiaKnowledgeManagedVenvMarker {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$VenvRoot
    )

    $resolvedVenv = Assert-HiaKnowledgeProjectDirectory `
        -ProjectRoot $ProjectRoot `
        -Path $VenvRoot
    if (-not (Test-Path -LiteralPath $resolvedVenv -PathType Container)) {
        return $null
    }
    $markerPath = Join-Path $resolvedVenv $script:HiaManagedVenvMarkerName
    if (-not (Test-HiaEmbeddingOrdinaryFile -Path $markerPath)) {
        return $null
    }
    $markerFile = Get-Item -LiteralPath $markerPath -Force -ErrorAction Stop
    if ([long]$markerFile.Length -le 0 -or [long]$markerFile.Length -gt 65536) {
        return $null
    }
    try {
        $marker = [System.IO.File]::ReadAllText($markerPath) | ConvertFrom-Json
    } catch {
        return $null
    }
    if (
        -not [System.StringComparer]::Ordinal.Equals(
            [string]$marker.schema,
            $script:HiaManagedVenvMarkerSchema
        ) -or
        -not [System.StringComparer]::Ordinal.Equals(
            [string]$marker.role,
            'hia-embedding'
        ) -or
        -not [System.StringComparer]::Ordinal.Equals(
            [string]$marker.python_version,
            $script:HiaKnowledgePythonVersion
        ) -or
        [string]$marker.install_id -notmatch '^[a-f0-9]{32}$' -or
        [string]::IsNullOrWhiteSpace([string]$marker.managed_python) -or
        [System.IO.Path]::IsPathRooted([string]$marker.managed_python) -or
        @(([string]$marker.managed_python) -split '[\\/]').Contains('..')
    ) {
        return $null
    }
    return $marker
}

function Get-HiaKnowledgePythonProbe {
    param(
        [Parameter(Mandatory = $true)][string]$PythonExe,
        [Parameter(Mandatory = $true)][hashtable]$Environment,
        [Parameter(Mandatory = $true)][string[]]$RemoveEnvironment,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory
    )

    if (-not (Test-HiaEmbeddingOrdinaryFile -Path $PythonExe)) {
        return $null
    }
    $probeCode = @'
import importlib.metadata
import json
import os
import platform
import site
import struct
import sys

project_root = os.path.realpath(sys.argv[1])
for relative in (
    os.path.join("services", "bridge"),
    os.path.join("services", "hia_mcp_v2"),
):
    sys.path.insert(0, os.path.join(project_root, relative))

try:
    parser_version = importlib.metadata.version("pypdf")
except importlib.metadata.PackageNotFoundError:
    parser_version = ""
try:
    import hia_bridge
    bridge_import = True
except Exception:
    bridge_import = False
try:
    import hia_mcp_v2
    mcp_import = True
except Exception:
    mcp_import = False
try:
    import hia_embedding_worker
    worker_import = True
except Exception:
    worker_import = False
print(json.dumps({
    "executable": os.path.realpath(sys.executable),
    "version": platform.python_version(),
    "bits": struct.calcsize("P") * 8,
    "prefix": os.path.realpath(sys.prefix),
    "base_prefix": os.path.realpath(getattr(sys, "base_prefix", sys.prefix)),
    "no_user_site": site.ENABLE_USER_SITE is False,
    "pypdf_version": parser_version,
    "bridge_import": bridge_import,
    "mcp_import": mcp_import,
    "worker_import": worker_import,
}, ensure_ascii=False, separators=(",", ":")))
'@
    $result = Invoke-HiaEmbeddingChildProcess `
        -FilePath $PythonExe `
        -Arguments @(
            '-I', '-X', 'utf8', '-B', '-c', $probeCode, $WorkingDirectory
        ) `
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

function Test-HiaKnowledgeManagedVenv {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$VenvRoot,
        [Parameter(Mandatory = $true)][string]$PythonInstallRoot,
        [Parameter(Mandatory = $true)][hashtable]$Environment,
        [Parameter(Mandatory = $true)][string[]]$RemoveEnvironment,
        [switch]$RequireParser,
        [switch]$RequireWorker
    )

    $marker = (
        Get-HiaKnowledgeManagedVenvMarker `
            -ProjectRoot $ProjectRoot `
            -VenvRoot $VenvRoot
    )
    if ($null -eq $marker) {
        return $false
    }
    $pythonExe = Join-Path $VenvRoot 'Scripts\python.exe'
    $probe = Get-HiaKnowledgePythonProbe `
        -PythonExe $pythonExe `
        -Environment $Environment `
        -RemoveEnvironment $RemoveEnvironment `
        -WorkingDirectory $ProjectRoot
    if ($null -eq $probe) {
        return $false
    }
    $expectedVenv = [System.IO.Path]::GetFullPath($VenvRoot).TrimEnd('\')
    $expectedPythonRoot = (
        [System.IO.Path]::GetFullPath($PythonInstallRoot).TrimEnd('\') +
        [System.IO.Path]::DirectorySeparatorChar
    )
    $expectedExecutable = [System.IO.Path]::GetFullPath($pythonExe)
    $markerPython = [System.IO.Path]::GetFullPath(
        (Join-Path $ProjectRoot (
            ([string]$marker.managed_python) -replace '/', '\'
        ))
    )
    if (-not (Test-HiaEmbeddingOrdinaryFile -Path $markerPython)) {
        return $false
    }
    $expectedBasePrefix = [System.IO.Path]::GetFullPath(
        (Split-Path -Parent $markerPython)
    ).TrimEnd('\')
    return (
        [System.StringComparer]::OrdinalIgnoreCase.Equals(
            [System.IO.Path]::GetFullPath([string]$probe.executable),
            $expectedExecutable
        ) -and
        [System.StringComparer]::OrdinalIgnoreCase.Equals(
            [System.IO.Path]::GetFullPath([string]$probe.prefix).TrimEnd('\'),
            $expectedVenv
        ) -and
        [string]$probe.version -eq $script:HiaKnowledgePythonVersion -and
        [int]$probe.bits -eq 64 -and
        [bool]$probe.no_user_site -and
        [bool]$probe.bridge_import -and
        [bool]$probe.mcp_import -and
        [System.IO.Path]::GetFullPath([string]$probe.base_prefix).StartsWith(
            $expectedPythonRoot,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -and
        [System.StringComparer]::OrdinalIgnoreCase.Equals(
            [System.IO.Path]::GetFullPath(
                [string]$probe.base_prefix
            ).TrimEnd('\'),
            $expectedBasePrefix
        ) -and
        (-not $RequireParser -or
            [string]$probe.pypdf_version -eq '6.14.2') -and
        (-not $RequireWorker -or [bool]$probe.worker_import)
    )
}

function Get-HiaKnowledgeManagedPython {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$PythonInstallRoot,
        [Parameter(Mandatory = $true)][hashtable]$Environment,
        [Parameter(Mandatory = $true)][string[]]$RemoveEnvironment
    )

    $root = Assert-HiaKnowledgeProjectDirectory `
        -ProjectRoot $ProjectRoot `
        -Path $PythonInstallRoot `
        -Create
    $resolvedRoot = [System.IO.Path]::GetFullPath($root).TrimEnd('\')
    $versionParts = @($script:HiaKnowledgePythonVersion -split '\.')
    if ($versionParts.Count -lt 2) {
        throw 'Managed Python version is invalid.'
    }
    $expectedAliasName = 'cpython-{0}.{1}-windows-x86_64-none' -f
        $versionParts[0],
        $versionParts[1]
    $expectedTargetName = 'cpython-{0}-windows-x86_64-none' -f
        $script:HiaKnowledgePythonVersion
    $matches = @()
    foreach ($directory in @(
        Get-ChildItem -LiteralPath $root -Directory -Force -ErrorAction Stop
    )) {
        if (
            ([int]$directory.Attributes -band
                [int][System.IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            $linkTypeProperty = $directory.PSObject.Properties['LinkType']
            $targetProperty = $directory.PSObject.Properties['Target']
            $targets = @()
            if ($null -ne $targetProperty) {
                $targets = @($targetProperty.Value)
            }
            $linkPath = [System.IO.Path]::GetFullPath(
                [string]$directory.FullName
            ).TrimEnd('\')
            $expectedLinkPath = [System.IO.Path]::GetFullPath(
                (Join-Path $resolvedRoot $expectedAliasName)
            ).TrimEnd('\')
            $targetPath = ''
            if (
                $targets.Count -eq 1 -and
                -not [string]::IsNullOrWhiteSpace([string]$targets[0]) -and
                [System.IO.Path]::IsPathRooted([string]$targets[0])
            ) {
                $targetPath = [System.IO.Path]::GetFullPath(
                    [string]$targets[0]
                ).TrimEnd('\')
            }
            $targetItem = if (
                -not [string]::IsNullOrWhiteSpace($targetPath)
            ) {
                Get-Item `
                    -LiteralPath $targetPath `
                    -Force `
                    -ErrorAction SilentlyContinue
            } else {
                $null
            }
            $safeUvAlias = (
                $null -ne $linkTypeProperty -and
                [System.StringComparer]::OrdinalIgnoreCase.Equals(
                    [string]$linkTypeProperty.Value,
                    'Junction'
                ) -and
                [System.StringComparer]::OrdinalIgnoreCase.Equals(
                    [string]$directory.Name,
                    $expectedAliasName
                ) -and
                [System.StringComparer]::OrdinalIgnoreCase.Equals(
                    $linkPath,
                    $expectedLinkPath
                ) -and
                -not [string]::IsNullOrWhiteSpace($targetPath) -and
                [System.StringComparer]::OrdinalIgnoreCase.Equals(
                    [System.IO.Path]::GetFullPath(
                        (Split-Path -Parent $targetPath)
                    ).TrimEnd('\'),
                    $resolvedRoot
                ) -and
                [System.StringComparer]::OrdinalIgnoreCase.Equals(
                    [System.IO.Path]::GetFileName($targetPath),
                    $expectedTargetName
                ) -and
                $null -ne $targetItem -and
                $targetItem -is [System.IO.DirectoryInfo] -and
                ([int]$targetItem.Attributes -band
                    [int][System.IO.FileAttributes]::ReparsePoint) -eq 0 -and
                [System.StringComparer]::OrdinalIgnoreCase.Equals(
                    [System.IO.Path]::GetFullPath(
                        [string]$targetItem.FullName
                    ).TrimEnd('\'),
                    $targetPath
                ) -and
                (Test-HiaEmbeddingOrdinaryFile -Path (
                    Join-Path $targetPath 'python.exe'
                ))
            )
            if (-not $safeUvAlias) {
                throw 'Managed Python install root contains an unsafe reparse point.'
            }
            continue
        }
        $candidate = Join-Path $directory.FullName 'python.exe'
        if (-not (Test-HiaEmbeddingOrdinaryFile -Path $candidate)) {
            continue
        }
        $probe = Get-HiaKnowledgePythonProbe `
            -PythonExe $candidate `
            -Environment $Environment `
            -RemoveEnvironment $RemoveEnvironment `
            -WorkingDirectory $ProjectRoot
        if (
            $null -ne $probe -and
            [string]$probe.version -eq $script:HiaKnowledgePythonVersion -and
            [int]$probe.bits -eq 64
        ) {
            $matches += [System.IO.Path]::GetFullPath($candidate)
        }
    }
    $matches = @($matches | Sort-Object -Unique)
    if ($matches.Count -ne 1) {
        throw (
            'Expected exactly one verified project-managed Python {0}; found {1}.' -f
                $script:HiaKnowledgePythonVersion,
                $matches.Count
        )
    }
    return [string]$matches[0]
}

function Assert-HiaKnowledgeManagedVenvTree {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $resolved = Assert-HiaKnowledgeProjectDirectory `
        -ProjectRoot $ProjectRoot `
        -Path $Path
    if (-not (Test-Path -LiteralPath $resolved -PathType Container)) {
        throw 'Managed venv directory is unavailable.'
    }

    # Walk one directory at a time so a package test fixture that disappears
    # between enumeration calls can be retried without skipping validation of
    # the surviving siblings.  The root and every surviving directory still
    # have to be ordinary project-local directories.
    $root = [System.IO.Path]::GetFullPath($resolved).TrimEnd('\')
    $prefix = $root + [System.IO.Path]::DirectorySeparatorChar
    $pending = [System.Collections.Generic.Stack[string]]::new()
    $pending.Push($root)
    while ($pending.Count -gt 0) {
        $current = $pending.Pop()
        $items = $null
        $vanished = $false
        for ($attempt = 1; $attempt -le 3; $attempt++) {
            try {
                if ($current.Length -ge 240) {
                    # Windows PowerShell 5.1 can report DirectoryNotFound for
                    # ordinary Torch package paths near MAX_PATH.  The .NET
                    # extended-length API can enumerate the same tree without
                    # weakening the reparse-point or project-boundary audit.
                    $extendedCurrent = ConvertTo-HiaKnowledgeExtendedPath `
                        -Path $current
                    $currentAttributes = [System.IO.File]::GetAttributes(
                        $extendedCurrent
                    )
                    if (
                        ([int]$currentAttributes -band
                            [int][System.IO.FileAttributes]::Directory) -eq 0 -or
                        ([int]$currentAttributes -band
                            [int][System.IO.FileAttributes]::ReparsePoint) -ne 0
                    ) {
                        throw (
                            'Managed venv tree contains a reparse point or ' +
                            'unknown object.'
                        )
                    }
                    $items = @(
                        foreach ($entry in @(
                            [System.IO.Directory]::EnumerateFileSystemEntries(
                                $extendedCurrent
                            )
                        )) {
                            $attributes = [System.IO.File]::GetAttributes(
                                [string]$entry
                            )
                            $isDirectory = (
                                ([int]$attributes -band
                                    [int][System.IO.FileAttributes]::Directory
                                ) -ne 0
                            )
                            [pscustomobject]@{
                                FullName = ConvertFrom-HiaKnowledgeExtendedPath `
                                    -Path ([string]$entry)
                                Attributes = $attributes
                                HiaIsDirectory = $isDirectory
                                HiaIsFile = -not $isDirectory
                            }
                        }
                    )
                } else {
                    $currentItem = Get-Item `
                        -LiteralPath $current `
                        -Force `
                        -ErrorAction Stop
                    if (
                        $currentItem -isnot [System.IO.DirectoryInfo] -or
                        ([int]$currentItem.Attributes -band
                            [int][System.IO.FileAttributes]::ReparsePoint) -ne 0
                    ) {
                        throw (
                            'Managed venv tree contains a reparse point or ' +
                            'unknown object.'
                        )
                    }
                    $items = @(
                        Get-ChildItem `
                            -LiteralPath $current `
                            -Force `
                            -ErrorAction Stop
                    )
                }
                break
            } catch {
                $exception = $_.Exception
                $missing = $false
                while ($null -ne $exception) {
                    if (
                        $exception -is
                            [System.Management.Automation.ItemNotFoundException] -or
                        $exception -is [System.IO.DirectoryNotFoundException] -or
                        $exception -is [System.IO.FileNotFoundException]
                    ) {
                        $missing = $true
                        break
                    }
                    $exception = $exception.InnerException
                }
                if (-not $missing) {
                    throw
                }
                if (
                    -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
                        $current,
                        $root
                    ) -and
                    -not $(if ($current.Length -ge 240) {
                        [System.IO.Directory]::Exists(
                            (ConvertTo-HiaKnowledgeExtendedPath -Path $current)
                        )
                    } else {
                        Test-Path `
                            -LiteralPath $current `
                            -PathType Container `
                            -ErrorAction SilentlyContinue
                    })
                ) {
                    $vanished = $true
                    break
                }
                if ($attempt -eq 3) {
                    throw
                }
            }
        }
        if ($vanished) {
            continue
        }
        foreach ($item in @($items)) {
            $extendedProperty = $item.PSObject.Properties['HiaIsDirectory']
            $isDirectory = if ($null -ne $extendedProperty) {
                [bool]$extendedProperty.Value
            } else {
                $item -is [System.IO.DirectoryInfo]
            }
            $isFile = if (
                $null -ne $item.PSObject.Properties['HiaIsFile']
            ) {
                [bool]$item.PSObject.Properties['HiaIsFile'].Value
            } else {
                $item -is [System.IO.FileInfo]
            }
            $fullName = [System.IO.Path]::GetFullPath(
                [string]$item.FullName
            )
            if (
                -not $fullName.StartsWith(
                    $prefix,
                    [System.StringComparison]::OrdinalIgnoreCase
                ) -or
                ([int]$item.Attributes -band
                    [int][System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
                (-not $isDirectory -and -not $isFile)
            ) {
                throw (
                    'Managed venv tree contains a reparse point or ' +
                    'unknown object.'
                )
            }
            if ($isDirectory) {
                $pending.Push($fullName)
            }
        }
    }
    return $resolved
}

function ConvertTo-HiaKnowledgeExtendedPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $full = [System.IO.Path]::GetFullPath($Path)
    if ($full.StartsWith('\\?\')) { return $full }
    if ($full.StartsWith('\\')) {
        return '\\?\UNC\' + $full.Substring(2)
    }
    return '\\?\' + $full
}

function ConvertFrom-HiaKnowledgeExtendedPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ($Path.StartsWith('\\?\UNC\')) {
        return '\\' + $Path.Substring(8)
    }
    if ($Path.StartsWith('\\?\')) {
        return $Path.Substring(4)
    }
    return $Path
}

function Get-HiaKnowledgeTransactionPaths {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)]
        [ValidatePattern('^[a-f0-9]{32}$')]
        [string]$InstallId
    )

    $root = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
    $toolchainRoot = Join-Path $root '.runtime\toolchains\hia-embedding'
    # Keep both dependency extraction and WinPS 5.1 recursive cleanup below the
    # legacy Win32 MAX_PATH boundary, including long Torch header names.  The
    # full install id remains in the marker; the lock permits only one active
    # transaction and the marker rejects any stale 64-bit path-key collision.
    $transactionRoot = Join-Path $root (
        '.runtime\v\{0}' -f $InstallId.Substring(0, 16)
    )
    return [pscustomobject]@{
        canonical = Join-Path $root '.venv'
        legacy = Join-Path $toolchainRoot 'venv'
        transaction_root = $transactionRoot
        staging = Join-Path $transactionRoot 's'
        backup = Join-Path $transactionRoot 'b'
        failed = Join-Path $transactionRoot 'f'
    }
}

function Assert-HiaKnowledgeCleanupTarget {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]
        [ValidateSet('staging', 'backup', 'failed', 'legacy')]
        [string]$Kind,
        [Parameter(Mandatory = $true)]
        [ValidatePattern('^[a-f0-9]{32}$')]
        [string]$InstallId
    )

    if (
        [string]::IsNullOrWhiteSpace($Path) -or
        -not [System.IO.Path]::IsPathRooted($Path) -or
        $Path -match (
            '(?i)(?:\$' + 'HOME|~|USERPROFILE|AppData)'
        )
    ) {
        throw 'Managed venv cleanup target is not an absolute project path.'
    }
    $paths = Get-HiaKnowledgeTransactionPaths `
        -ProjectRoot $ProjectRoot `
        -InstallId $InstallId
    $expected = [string]$paths.$Kind
    $resolved = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
    if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals(
        $resolved,
        [System.IO.Path]::GetFullPath($expected).TrimEnd('\')
    )) {
        throw 'Managed venv cleanup target is outside the exact allowlist.'
    }
    if (
        [System.IO.Path]::GetPathRoot($resolved).TrimEnd('\') -eq
        $resolved.TrimEnd('\')
    ) {
        throw 'Managed venv cleanup target cannot be a drive root.'
    }
    Assert-HiaKnowledgeManagedVenvTree `
        -ProjectRoot $ProjectRoot `
        -Path $resolved | Out-Null
    return $resolved
}

function Remove-HiaKnowledgeInstallerOwnedDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]
        [ValidateSet('staging', 'backup', 'failed', 'legacy')]
        [string]$Kind,
        [Parameter(Mandatory = $true)]
        [ValidatePattern('^[a-f0-9]{32}$')]
        [string]$InstallId
    )

    if (-not (Test-Path -LiteralPath $Path -ErrorAction SilentlyContinue)) {
        return $false
    }
    $validated = Assert-HiaKnowledgeCleanupTarget `
        -ProjectRoot $ProjectRoot `
        -Path $Path `
        -Kind $Kind `
        -InstallId $InstallId
    $extended = ConvertTo-HiaKnowledgeExtendedPath -Path $validated
    [System.IO.Directory]::Delete($extended, $true)
    if ([System.IO.Directory]::Exists($extended)) {
        throw 'Managed venv cleanup target still exists after exact deletion.'
    }
    return $true
}

function New-HiaKnowledgeManagedVenv {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$VenvRoot,
        [Parameter(Mandatory = $true)][string]$TransactionRoot,
        [Parameter(Mandatory = $true)][string]$ManagedPython,
        [Parameter(Mandatory = $true)][string]$PythonInstallRoot,
        [Parameter(Mandatory = $true)][string]$UvExe,
        [Parameter(Mandatory = $true)][hashtable]$Environment,
        [Parameter(Mandatory = $true)][string[]]$RemoveEnvironment,
        [bool]$RepairRequested = $false
    )

    $expectedCanonical = Join-Path $ProjectRoot '.venv'
    if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals(
        [System.IO.Path]::GetFullPath($VenvRoot).TrimEnd('\'),
        [System.IO.Path]::GetFullPath($expectedCanonical).TrimEnd('\')
    )) {
        throw 'Knowledge environment canonical path disagrees with project .venv.'
    }
    $expectedTransaction = Join-Path $ProjectRoot (
        '.runtime\toolchains\hia-embedding'
    )
    if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals(
        [System.IO.Path]::GetFullPath($TransactionRoot).TrimEnd('\'),
        [System.IO.Path]::GetFullPath($expectedTransaction).TrimEnd('\')
    )) {
        throw 'Knowledge environment transaction root is not canonical.'
    }
    Assert-HiaKnowledgeProjectDirectory `
        -ProjectRoot $ProjectRoot `
        -Path $TransactionRoot `
        -Create | Out-Null

    if (Test-Path -LiteralPath $VenvRoot -ErrorAction SilentlyContinue) {
        $existingMarker = if (
            Test-Path -LiteralPath $VenvRoot -PathType Container
        ) {
            Get-HiaKnowledgeManagedVenvMarker `
                -ProjectRoot $ProjectRoot `
                -VenvRoot $VenvRoot
        } else {
            $null
        }
        if ($null -eq $existingMarker) {
            throw 'Existing root .venv is unmarked or unsafe; refusing to replace it.'
        }
        Assert-HiaKnowledgeManagedVenvTree `
            -ProjectRoot $ProjectRoot `
            -Path $VenvRoot | Out-Null
        $existingRuntimeValid = Test-HiaKnowledgeManagedVenv `
            -ProjectRoot $ProjectRoot `
            -VenvRoot $VenvRoot `
            -PythonInstallRoot $PythonInstallRoot `
            -Environment $Environment `
            -RemoveEnvironment $RemoveEnvironment
        if (-not $existingRuntimeValid -and -not $RepairRequested) {
            throw 'Existing root .venv is partial; run environment-repair to replace it safely.'
        }
    }

    $installId = [Guid]::NewGuid().ToString('N')
    $paths = Get-HiaKnowledgeTransactionPaths `
        -ProjectRoot $ProjectRoot `
        -InstallId $installId
    Assert-HiaKnowledgeProjectDirectory `
        -ProjectRoot $ProjectRoot `
        -Path ([string]$paths.transaction_root) `
        -Create | Out-Null
    foreach ($path in @($paths.staging, $paths.backup, $paths.failed)) {
        Assert-HiaKnowledgeProjectDirectory `
            -ProjectRoot $ProjectRoot `
            -Path ([string]$path) | Out-Null
        if (Test-Path -LiteralPath $path -ErrorAction SilentlyContinue) {
            throw 'Knowledge environment transaction path already exists.'
        }
    }
    $stagingPath = [string]$paths.staging
    try {
        Write-HiaEmbeddingInstallLog `
            -Level 'INFO' `
            -Message (
                'Staged managed venv transaction path: {0}; length={1}.' -f
                    $stagingPath,
                    $stagingPath.Length
            )

        Write-HiaEmbeddingInstallLog `
            -Level 'INFO' `
            -Message 'Creating the staged project-local .venv from managed Python.'
        $venvResult = Invoke-HiaEmbeddingChildProcess `
            -FilePath $UvExe `
            -Arguments @(
                '--no-config',
                'venv',
                '--python', $ManagedPython,
                '--managed-python',
                '--no-python-downloads',
                '--relocatable',
                [string]$paths.staging
            ) `
            -Environment $Environment `
            -RemoveEnvironment $RemoveEnvironment `
            -WorkingDirectory $ProjectRoot
        Assert-HiaEmbeddingProcessSucceeded `
            -Result $venvResult `
            -Operation 'Staged project-local .venv creation'
        Assert-HiaKnowledgeProjectDirectory `
            -ProjectRoot $ProjectRoot `
            -Path ([string]$paths.staging) | Out-Null

        $markerPath = Join-Path (
            [string]$paths.staging
        ) $script:HiaManagedVenvMarkerName
        $relativeManagedPython = (
            [System.IO.Path]::GetFullPath($ManagedPython).Substring(
                [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\').Length + 1
            ) -replace '\\', '/'
        )
        $markerPayload = [ordered]@{
            schema = $script:HiaManagedVenvMarkerSchema
            role = 'hia-embedding'
            install_id = $installId
            python_version = $script:HiaKnowledgePythonVersion
            created_at_utc = [DateTime]::UtcNow.ToString('o')
            managed_python = $relativeManagedPython
        } | ConvertTo-Json -Compress
        $markerStream = [System.IO.File]::Open(
            $markerPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::Read
        )
        try {
            $markerBytes = [System.Text.UTF8Encoding]::new($false).GetBytes(
                $markerPayload + [Environment]::NewLine
            )
            $markerStream.Write($markerBytes, 0, $markerBytes.Length)
            $markerStream.Flush($true)
        } finally {
            $markerStream.Dispose()
        }
        if (-not (
            Test-HiaKnowledgeManagedVenv `
                -ProjectRoot $ProjectRoot `
                -VenvRoot ([string]$paths.staging) `
                -PythonInstallRoot $PythonInstallRoot `
                -Environment $Environment `
                -RemoveEnvironment $RemoveEnvironment
        )) {
            throw 'The staged project-local .venv did not pass base portability validation.'
        }
        return [pscustomobject]@{
            venv_root = [string]$paths.canonical
            legacy_root = [string]$paths.legacy
            transaction_root = [string]$paths.transaction_root
            staging_root = [string]$paths.staging
            staging_python = Join-Path ([string]$paths.staging) 'Scripts\python.exe'
            backup_root = [string]$paths.backup
            failed_root = [string]$paths.failed
            install_id = $installId
        }
    } catch {
        $creationFailure = $_.Exception
        try {
            if (Test-Path -LiteralPath $paths.staging -ErrorAction SilentlyContinue) {
                [void](Remove-HiaKnowledgeInstallerOwnedDirectory `
                    -ProjectRoot $ProjectRoot `
                    -Path ([string]$paths.staging) `
                    -Kind 'staging' `
                    -InstallId $installId)
            }
            if (Test-Path -LiteralPath $paths.transaction_root -PathType Container) {
                $validatedTransactionRoot = Assert-HiaKnowledgeProjectDirectory `
                    -ProjectRoot $ProjectRoot `
                    -Path ([string]$paths.transaction_root)
                $remaining = @(
                    Get-ChildItem `
                        -LiteralPath $validatedTransactionRoot `
                        -Force `
                        -ErrorAction Stop
                )
                if ($remaining.Count -ne 0) {
                    throw 'The exact failed transaction root contains an unexpected surviving item.'
                }
                [System.IO.Directory]::Delete($validatedTransactionRoot, $false)
            }
        } catch {
            throw (
                'Staged project-local .venv preparation failed: {0}. ' +
                'Exact transaction cleanup also failed: {1}' -f
                    $creationFailure.Message,
                    [string]$_.Exception.Message
            )
        }
        throw $creationFailure
    }
}

function Publish-HiaKnowledgeManagedVenv {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][object]$Transaction
    )

    $installId = [string]$Transaction.install_id
    $paths = Get-HiaKnowledgeTransactionPaths `
        -ProjectRoot $ProjectRoot `
        -InstallId $installId
    foreach ($name in @(
        'venv_root',
        'legacy_root',
        'transaction_root',
        'staging_root',
        'backup_root',
        'failed_root'
    )) {
        $expectedName = @{
            venv_root = 'canonical'
            legacy_root = 'legacy'
            transaction_root = 'transaction_root'
            staging_root = 'staging'
            backup_root = 'backup'
            failed_root = 'failed'
        }[$name]
        if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals(
            [System.IO.Path]::GetFullPath([string]$Transaction.$name).TrimEnd('\'),
            [System.IO.Path]::GetFullPath([string]$paths.$expectedName).TrimEnd('\')
        )) {
            throw 'Knowledge environment transaction paths changed before publish.'
        }
    }
    Assert-HiaKnowledgeManagedVenvTree `
        -ProjectRoot $ProjectRoot `
        -Path ([string]$paths.staging) | Out-Null
    $stagingMarker = Get-HiaKnowledgeManagedVenvMarker `
        -ProjectRoot $ProjectRoot `
        -VenvRoot ([string]$paths.staging)
    if (
        $null -eq $stagingMarker -or
        [string]$stagingMarker.install_id -ne $installId
    ) {
        throw 'Staged knowledge environment marker changed before publish.'
    }

    $priorMoved = $false
    if (Test-Path -LiteralPath $paths.canonical -ErrorAction SilentlyContinue) {
        if (
            -not (Test-Path -LiteralPath $paths.canonical -PathType Container) -or
            $null -eq (
                Get-HiaKnowledgeManagedVenvMarker `
                    -ProjectRoot $ProjectRoot `
                    -VenvRoot ([string]$paths.canonical)
            )
        ) {
            throw 'Existing root .venv is unmarked or unsafe; refusing to publish.'
        }
        Assert-HiaKnowledgeManagedVenvTree `
            -ProjectRoot $ProjectRoot `
            -Path ([string]$paths.canonical) | Out-Null
        [System.IO.Directory]::Move(
            [string]$paths.canonical,
            [string]$paths.backup
        )
        $priorMoved = $true
    }
    try {
        [System.IO.Directory]::Move(
            [string]$paths.staging,
            [string]$paths.canonical
        )
    } catch {
        if (
            $priorMoved -and
            -not (Test-Path -LiteralPath $paths.canonical) -and
            (Test-Path -LiteralPath $paths.backup -PathType Container)
        ) {
            [System.IO.Directory]::Move(
                [string]$paths.backup,
                [string]$paths.canonical
            )
            Assert-HiaKnowledgeManagedVenvTree `
                -ProjectRoot $ProjectRoot `
                -Path ([string]$paths.canonical) | Out-Null
        }
        throw
    }
    return [pscustomobject]@{
        prior_moved = $priorMoved
        backup_root = if ($priorMoved) { [string]$paths.backup } else { '' }
    }
}

function Move-HiaKnowledgeStagingToFailed {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][object]$Transaction
    )

    $staging = [string]$Transaction.staging_root
    $failed = [string]$Transaction.failed_root
    if (-not (Test-Path -LiteralPath $staging -PathType Container)) {
        return ''
    }
    if (Test-Path -LiteralPath $failed -ErrorAction SilentlyContinue) {
        throw 'Knowledge environment failure isolation path already exists.'
    }
    Assert-HiaKnowledgeManagedVenvTree `
        -ProjectRoot $ProjectRoot `
        -Path $staging | Out-Null
    $marker = Get-HiaKnowledgeManagedVenvMarker `
        -ProjectRoot $ProjectRoot `
        -VenvRoot $staging
    if (
        $null -eq $marker -or
        [string]$marker.install_id -ne [string]$Transaction.install_id
    ) {
        throw 'Staged knowledge environment marker does not match this installation.'
    }
    [System.IO.Directory]::Move($staging, $failed)
    return $failed
}

function Undo-HiaKnowledgeManagedVenvPublication {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$VenvRoot,
        [AllowEmptyString()][string]$BackupRoot = '',
        [Parameter(Mandatory = $true)]
        [ValidatePattern('^[a-f0-9]{32}$')]
        [string]$InstallId
    )

    $paths = Get-HiaKnowledgeTransactionPaths `
        -ProjectRoot $ProjectRoot `
        -InstallId $InstallId
    if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals(
        [System.IO.Path]::GetFullPath($VenvRoot).TrimEnd('\'),
        [System.IO.Path]::GetFullPath([string]$paths.canonical).TrimEnd('\')
    )) {
        throw 'Knowledge environment rollback canonical path is not project .venv.'
    }
    $resolvedBackup = ''
    if (-not [string]::IsNullOrWhiteSpace($BackupRoot)) {
        $resolvedBackup = [System.IO.Path]::GetFullPath($BackupRoot).TrimEnd('\')
        if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals(
            $resolvedBackup,
            [System.IO.Path]::GetFullPath([string]$paths.backup).TrimEnd('\')
        )) {
            throw 'Knowledge environment rollback backup is not canonical.'
        }
        Assert-HiaKnowledgeManagedVenvTree `
            -ProjectRoot $ProjectRoot `
            -Path $resolvedBackup | Out-Null
    }
    if (Test-Path -LiteralPath $paths.failed -ErrorAction SilentlyContinue) {
        throw 'Knowledge environment rollback isolation path already exists.'
    }

    $currentMoved = $false
    if (Test-Path -LiteralPath $paths.canonical -PathType Container) {
        $currentMarker = Get-HiaKnowledgeManagedVenvMarker `
            -ProjectRoot $ProjectRoot `
            -VenvRoot ([string]$paths.canonical)
        if (
            $null -eq $currentMarker -or
            [string]$currentMarker.install_id -ne $InstallId
        ) {
            throw 'Knowledge environment rollback marker does not match this installation.'
        }
        Assert-HiaKnowledgeManagedVenvTree `
            -ProjectRoot $ProjectRoot `
            -Path ([string]$paths.canonical) | Out-Null
        [System.IO.Directory]::Move(
            [string]$paths.canonical,
            [string]$paths.failed
        )
        $currentMoved = $true
    } elseif (Test-Path -LiteralPath $paths.canonical) {
        throw 'Knowledge environment rollback found a non-directory root .venv.'
    }
    $restoredVenv = ''
    if (-not [string]::IsNullOrWhiteSpace($resolvedBackup)) {
        [System.IO.Directory]::Move(
            $resolvedBackup,
            [string]$paths.canonical
        )
        Assert-HiaKnowledgeManagedVenvTree `
            -ProjectRoot $ProjectRoot `
            -Path ([string]$paths.canonical) | Out-Null
        $restoredVenv = [string]$paths.canonical
        $resolvedBackup = ''
    }
    Write-HiaEmbeddingInstallLog `
        -Level 'WARNING' `
        -Message (
            'The failed replacement was isolated and the prior root .venv ' +
            'was atomically restored from its transaction backup.'
        )
    return [pscustomobject]@{
        restored_venv = $restoredVenv
        preserved_backup = ''
        isolated_failed_venv = if (
            $currentMoved
        ) { [string]$paths.failed } else { '' }
    }
}

function Complete-HiaKnowledgeManagedVenvTransaction {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][object]$Transaction,
        [switch]$IncludeVerifiedLegacyCandidate
    )

    $installId = [string]$Transaction.install_id
    $legacyCleanupCandidate = ''
    if (
        $IncludeVerifiedLegacyCandidate -and
        (Test-Path -LiteralPath $Transaction.legacy_root -PathType Container)
    ) {
        $legacyMarker = Get-HiaKnowledgeManagedVenvMarker `
            -ProjectRoot $ProjectRoot `
            -VenvRoot ([string]$Transaction.legacy_root)
        $legacyConfig = Join-Path (
            [string]$Transaction.legacy_root
        ) 'pyvenv.cfg'
        $legacyPython = Join-Path (
            [string]$Transaction.legacy_root
        ) 'Scripts\python.exe'
        $legacyShapeValid = (
            Test-HiaEmbeddingOrdinaryFile -Path $legacyConfig
        ) -and (
            Test-HiaEmbeddingOrdinaryFile -Path $legacyPython
        )
        if ($legacyShapeValid) {
            $configItem = Get-Item `
                -LiteralPath $legacyConfig `
                -Force `
                -ErrorAction Stop
            if (
                [long]$configItem.Length -le 0 -or
                [long]$configItem.Length -gt 65536
            ) {
                $legacyShapeValid = $false
            } else {
                $configText = [System.IO.File]::ReadAllText($legacyConfig)
                $legacyShapeValid = (
                    $configText -match '(?im)^\s*home\s*=' -and
                    $configText -match (
                        '(?im)^\s*version(?:_info)?\s*=\s*' +
                        [regex]::Escape($script:HiaKnowledgePythonVersion)
                    )
                )
            }
        }
        if ($null -ne $legacyMarker -or $legacyShapeValid) {
            $legacyCleanupCandidate = Assert-HiaKnowledgeCleanupTarget `
                -ProjectRoot $ProjectRoot `
                -Path ([string]$Transaction.legacy_root) `
                -Kind 'legacy' `
                -InstallId $installId
            Write-HiaEmbeddingInstallLog `
                -Level 'INFO' `
                -Message (
                    'A verified legacy deep venv is eligible for explicit ' +
                    'project-local cleanup.'
                )
        } else {
            Write-HiaEmbeddingInstallLog `
                -Level 'WARNING' `
                -Message 'The unknown legacy deep directory was preserved.'
        }
    }
    $cleanupEntries = @(
        @{ path = [string]$Transaction.staging_root; kind = 'staging' },
        @{ path = [string]$Transaction.failed_root; kind = 'failed' },
        @{ path = [string]$Transaction.backup_root; kind = 'backup' }
    )
    foreach ($entry in $cleanupEntries) {
        if (Test-Path -LiteralPath $entry.path -ErrorAction SilentlyContinue) {
            [void](Assert-HiaKnowledgeCleanupTarget `
                -ProjectRoot $ProjectRoot `
                -Path $entry.path `
                -Kind ([string]$entry.kind) `
                -InstallId $installId)
        }
    }
    foreach ($entry in @($cleanupEntries | Where-Object {
        [string]$_.kind -ne 'backup'
    })) {
        if (Test-Path -LiteralPath $entry.path -ErrorAction SilentlyContinue) {
            [void](Remove-HiaKnowledgeInstallerOwnedDirectory `
                -ProjectRoot $ProjectRoot `
                -Path $entry.path `
                -Kind ([string]$entry.kind) `
                -InstallId $installId)
        }
    }
    $backupCleanupWarning = ''
    $backupEntry = @($cleanupEntries | Where-Object {
        [string]$_.kind -eq 'backup'
    })[0]
    if (Test-Path -LiteralPath $backupEntry.path -ErrorAction SilentlyContinue) {
        try {
            [void](Remove-HiaKnowledgeInstallerOwnedDirectory `
                -ProjectRoot $ProjectRoot `
                -Path $backupEntry.path `
                -Kind 'backup' `
                -InstallId $installId)
        } catch {
            $cleanupDetail = ConvertTo-HiaEmbeddingSafeLogText `
                -Text ([string]$_.Exception.Message)
            $backupCleanupWarning = (
                (
                    'The verified root .venv is active, but its transaction ' +
                    'backup could not be removed safely and was left for ' +
                    'diagnosis. Cleanup detail: {0}'
                ) -f $cleanupDetail
            )
            Write-HiaEmbeddingInstallLog `
                -Level 'WARNING' `
                -Message $backupCleanupWarning
        }
    }
    $transactionCleanupWarning = ''
    $paths = Get-HiaKnowledgeTransactionPaths `
        -ProjectRoot $ProjectRoot `
        -InstallId $installId
    $transactionRoot = [string]$paths.transaction_root
    if (Test-Path -LiteralPath $transactionRoot -PathType Container) {
        $validatedTransactionRoot = Assert-HiaKnowledgeProjectDirectory `
            -ProjectRoot $ProjectRoot `
            -Path $transactionRoot
        $remaining = @(
            Get-ChildItem `
                -LiteralPath $validatedTransactionRoot `
                -Force `
                -ErrorAction Stop
        )
        if ($remaining.Count -eq 0) {
            [System.IO.Directory]::Delete(
                $validatedTransactionRoot,
                $false
            )
        } else {
            $transactionCleanupWarning = (
                'The exact managed venv transaction root contains an ' +
                'unexpected surviving item and was preserved for diagnosis.'
            )
            Write-HiaEmbeddingInstallLog `
                -Level 'WARNING' `
                -Message $transactionCleanupWarning
        }
    }
    return [pscustomobject]@{
        legacy_cleanup_candidate = $legacyCleanupCandidate
        backup_cleanup_warning = $backupCleanupWarning
        transaction_cleanup_warning = $transactionCleanupWarning
    }
}

function Invoke-HiaKnowledgeParserEnvironment {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [bool]$RepairRequested,
        [switch]$DeferPublish
    )

    $transactionRoot = Join-Path $ProjectRoot (
        '.runtime\toolchains\hia-embedding'
    )
    $venvRoot = Join-Path $ProjectRoot '.venv'
    $legacyRoot = Join-Path $transactionRoot 'venv'
    $pythonInstallRoot = Join-Path $ProjectRoot '.runtime\toolchains\python'
    $cacheRoot = Join-Path $ProjectRoot '.runtime\cache\embedding'
    $tempRoot = Join-Path $cacheRoot 'tmp'
    foreach ($directory in @(
        $transactionRoot,
        $pythonInstallRoot,
        $cacheRoot,
        $tempRoot
    )) {
        Assert-HiaKnowledgeProjectDirectory `
            -ProjectRoot $ProjectRoot `
            -Path $directory `
            -Create | Out-Null
    }
    if (Test-Path -LiteralPath $venvRoot -ErrorAction SilentlyContinue) {
        $canonicalMarker = if (
            Test-Path -LiteralPath $venvRoot -PathType Container
        ) {
            Get-HiaKnowledgeManagedVenvMarker `
                -ProjectRoot $ProjectRoot `
                -VenvRoot $venvRoot
        } else {
            $null
        }
        if ($null -eq $canonicalMarker) {
            throw 'Existing root .venv is unmarked or unsafe; refusing to repair or replace it.'
        }
        Assert-HiaKnowledgeManagedVenvTree `
            -ProjectRoot $ProjectRoot `
            -Path $venvRoot | Out-Null
    }

    $removeEnvironment = @(Get-HiaKnowledgeRemoveEnvironment)
    $childEnvironment = New-HiaKnowledgeChildEnvironment `
        -ProjectRoot $ProjectRoot `
        -CacheRoot $cacheRoot `
        -TempRoot $tempRoot `
        -PythonInstallRoot $pythonInstallRoot
    $uv = Get-HiaProjectLocalUv `
        -ProjectRoot $ProjectRoot `
        -ToolchainRoot $transactionRoot `
        -CacheRoot $cacheRoot `
        -TempRoot $tempRoot `
        -Environment $childEnvironment `
        -RemoveEnvironment $removeEnvironment
    $uvExecutable = [string]$uv.exe

    Write-HiaEmbeddingInstallLog `
        -Level 'INFO' `
        -Message (
            'Preparing project-local managed Python {0}.' -f
                $script:HiaKnowledgePythonVersion
        )
    $pythonInstallResult = Invoke-HiaEmbeddingChildProcess `
        -FilePath $uvExecutable `
        -Arguments @(
            '--no-config',
            'python',
            'install',
            $script:HiaKnowledgePythonVersion,
            '--install-dir', $pythonInstallRoot,
            '--no-bin',
            '--no-registry'
        ) `
        -Environment $childEnvironment `
        -RemoveEnvironment $removeEnvironment `
        -WorkingDirectory $ProjectRoot
    Assert-HiaEmbeddingProcessSucceeded `
        -Result $pythonInstallResult `
        -Operation (
            'Project-local managed Python {0} installation' -f
                $script:HiaKnowledgePythonVersion
        )
    $managedPython = Get-HiaKnowledgeManagedPython `
        -ProjectRoot $ProjectRoot `
        -PythonInstallRoot $pythonInstallRoot `
        -Environment $childEnvironment `
        -RemoveEnvironment $removeEnvironment

    if (Test-Path -LiteralPath $venvRoot -PathType Container) {
        $canonicalBaseValid = Test-HiaKnowledgeManagedVenv `
            -ProjectRoot $ProjectRoot `
            -VenvRoot $venvRoot `
            -PythonInstallRoot $pythonInstallRoot `
            -Environment $childEnvironment `
            -RemoveEnvironment $removeEnvironment
        if (-not $canonicalBaseValid) {
            if (-not $RepairRequested) {
                throw 'Existing root .venv is partial or no longer uses managed Python; run environment-repair to replace it safely.'
            }
            Write-HiaEmbeddingInstallLog `
                -Level 'WARNING' `
                -Message (
                    'The marker-owned root .venv is incomplete; explicit ' +
                    'repair will replace it through the staged transaction.'
                )
        } else {
            $canonicalParserValid = Test-HiaKnowledgeManagedVenv `
                -ProjectRoot $ProjectRoot `
                -VenvRoot $venvRoot `
                -PythonInstallRoot $pythonInstallRoot `
                -Environment $childEnvironment `
                -RemoveEnvironment $removeEnvironment `
                -RequireParser
            if (
                $canonicalParserValid -and
                -not $RepairRequested -and
                -not $DeferPublish
            ) {
                $verifiedExisting = Get-HiaKnowledgePythonProbe `
                    -PythonExe (Join-Path $venvRoot 'Scripts\python.exe') `
                    -Environment $childEnvironment `
                    -RemoveEnvironment $removeEnvironment `
                    -WorkingDirectory $ProjectRoot
                return [ordered]@{
                    status = 'already_installed'
                    mode = 'knowledge-parser-only'
                    transaction_pending = $false
                    venv_root = $venvRoot
                    worker_python = Join-Path $venvRoot 'Scripts\python.exe'
                    python_version = [string]$verifiedExisting.version
                    python_bits = [int]$verifiedExisting.bits
                    base_prefix = [string]$verifiedExisting.base_prefix
                    portable = $true
                    pypdf_version = [string]$verifiedExisting.pypdf_version
                    uv_version = [string]$uv.version
                    backup_root = ''
                    install_id = ''
                    installed_torch = $false
                    installed_model = $false
                    uv_cache_cleanup_candidate = Join-Path $cacheRoot 'uv'
                    log_path = [string]$script:HiaEmbeddingInstallLogPath
                }
            }
        }
    }

    $transaction = $null
    $published = $false
    try {
        $transaction = New-HiaKnowledgeManagedVenv `
            -ProjectRoot $ProjectRoot `
            -VenvRoot $venvRoot `
            -TransactionRoot $transactionRoot `
            -ManagedPython $managedPython `
            -PythonInstallRoot $pythonInstallRoot `
            -UvExe $uvExecutable `
            -Environment $childEnvironment `
            -RemoveEnvironment $removeEnvironment `
            -RepairRequested $RepairRequested
        $stagingPython = [string]$transaction.staging_python
        $parserArguments = @(
            '--no-config',
            'pip',
            'install',
            '--python', $stagingPython,
            '--default-index', $script:HiaPyPiIndex,
            '--reinstall-package', 'pypdf',
            $script:HiaKnowledgeParserRequirement
        )
        Write-HiaEmbeddingInstallLog `
            -Level 'INFO' `
            -Message 'Installing pypdf in the staged project-local .venv.'
        $parserInstallResult = Invoke-HiaEmbeddingChildProcess `
            -FilePath $uvExecutable `
            -Arguments $parserArguments `
            -Environment $childEnvironment `
            -RemoveEnvironment $removeEnvironment `
            -WorkingDirectory $ProjectRoot
        Assert-HiaEmbeddingProcessSucceeded `
            -Result $parserInstallResult `
            -Operation 'Staged project-local PDF parser installation'
        if (-not (
            Test-HiaKnowledgeManagedVenv `
                -ProjectRoot $ProjectRoot `
                -VenvRoot ([string]$transaction.staging_root) `
                -PythonInstallRoot $pythonInstallRoot `
                -Environment $childEnvironment `
                -RemoveEnvironment $removeEnvironment `
                -RequireParser
        )) {
            throw 'The staged project-local PDF parser environment did not pass validation.'
        }
        if ($DeferPublish) {
            return [pscustomobject]@{
                status = 'staged'
                transaction_pending = $true
                transaction = $transaction
                venv_root = $venvRoot
                legacy_root = $legacyRoot
                worker_python = $stagingPython
                managed_python = $managedPython
                python_install_root = $pythonInstallRoot
                uv = $uv
                child_environment = $childEnvironment
                remove_environment = $removeEnvironment
            }
        }

        $publish = Publish-HiaKnowledgeManagedVenv `
            -ProjectRoot $ProjectRoot `
            -Transaction $transaction
        $published = $true
        $transaction | Add-Member `
            -NotePropertyName published_backup_root `
            -NotePropertyValue ([string]$publish.backup_root) `
            -Force
        if (-not (
            Test-HiaKnowledgeManagedVenv `
                -ProjectRoot $ProjectRoot `
                -VenvRoot $venvRoot `
                -PythonInstallRoot $pythonInstallRoot `
                -Environment $childEnvironment `
                -RemoveEnvironment $removeEnvironment `
                -RequireParser
        )) {
            throw 'The published project-local PDF parser environment did not pass validation.'
        }
        $verified = Get-HiaKnowledgePythonProbe `
            -PythonExe (Join-Path $venvRoot 'Scripts\python.exe') `
            -Environment $childEnvironment `
            -RemoveEnvironment $removeEnvironment `
            -WorkingDirectory $ProjectRoot
        $completion = Complete-HiaKnowledgeManagedVenvTransaction `
            -ProjectRoot $ProjectRoot `
            -Transaction $transaction `
            -IncludeVerifiedLegacyCandidate
        return [ordered]@{
            status = if ($RepairRequested) { 'repaired' } else { 'installed' }
            mode = 'knowledge-parser-only'
            transaction_pending = $false
            venv_root = $venvRoot
            worker_python = Join-Path $venvRoot 'Scripts\python.exe'
            python_version = [string]$verified.version
            python_bits = [int]$verified.bits
            base_prefix = [string]$verified.base_prefix
            portable = $true
            pypdf_version = [string]$verified.pypdf_version
            uv_version = [string]$uv.version
            backup_root = ''
            install_id = [string]$transaction.install_id
            installed_torch = $false
            installed_model = $false
            legacy_cleanup_candidate = (
                [string]$completion.legacy_cleanup_candidate
            )
            backup_cleanup_warning = (
                [string]$completion.backup_cleanup_warning
            )
            uv_cache_cleanup_candidate = Join-Path $cacheRoot 'uv'
            log_path = [string]$script:HiaEmbeddingInstallLogPath
        }
    } catch {
        $failure = $_.Exception
        if ($null -ne $transaction) {
            try {
                if ($published) {
                    [void](Undo-HiaKnowledgeManagedVenvPublication `
                        -ProjectRoot $ProjectRoot `
                        -VenvRoot $venvRoot `
                        -BackupRoot ([string]$transaction.published_backup_root) `
                        -InstallId ([string]$transaction.install_id))
                } else {
                    [void](Move-HiaKnowledgeStagingToFailed `
                        -ProjectRoot $ProjectRoot `
                        -Transaction $transaction)
                }
            } catch {
                throw (
                    "Knowledge parser setup failed: $($failure.Message). " +
                    'Transaction recovery also failed: ' +
                    [string]$_.Exception.Message
                )
            }
        }
        throw $failure
    }
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

if ($KnowledgeParserOnly) {
    Write-HiaEmbeddingInstallLog `
        -Level 'INFO' `
        -Message (
            'Knowledge parser environment action started: repair={0}; Python={1}; parser={2}.' -f
                [bool]$Repair,
                $script:HiaKnowledgePythonVersion,
                $script:HiaKnowledgeParserRequirement
        )
    $parserResult = Invoke-HiaKnowledgeParserEnvironment `
        -ProjectRoot $resolvedRoot `
        -RepairRequested ([bool]$Repair)
    Write-HiaEmbeddingInstallLog `
        -Level 'INFO' `
        -Message 'Knowledge parser environment action completed without installing PyTorch or a model.'
    Write-Output ($parserResult | ConvertTo-Json -Compress)
    return
}

$managedEnvironment = Invoke-HiaKnowledgeParserEnvironment `
    -ProjectRoot $resolvedRoot `
    -RepairRequested ([bool]$Repair) `
    -DeferPublish
$transaction = $managedEnvironment.transaction
$published = $false
$runtimeBackupRoot = ''
try {
$managedPython = [System.IO.Path]::GetFullPath(
    [string]$managedEnvironment.managed_python
)
if (-not (Test-HiaEmbeddingOrdinaryFile -Path $managedPython)) {
    throw 'The verified project-managed Python is unavailable after environment preparation.'
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
    '-X', 'utf8',
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
    -FilePath $managedPython `
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
    [string]::IsNullOrWhiteSpace([string]$plan.profile_id) -or
    [double]$plan.repository_size_gb -le 0
) {
    throw 'Embedding contract probe returned an incomplete plan.'
}
$repositorySize = ([double]$plan.repository_size_gb).ToString(
    '0.##',
    [System.Globalization.CultureInfo]::InvariantCulture
)
Write-HiaEmbeddingInstallLog `
    -Level 'INFO' `
    -Message (Format-HiaEmbeddingModelSelectionLog `
        -ModelId ([string]$plan.model_id) `
        -Revision ([string]$plan.revision) `
        -ModelDirectory ([string]$plan.model_dir) `
        -RepositorySize $repositorySize)

$childEnvironment = ConvertTo-HiaEmbeddingHashtable `
    -Value $plan.child_environment
$removeEnvironment = @($plan.remove_environment | ForEach-Object { [string]$_ })
foreach ($directory in @($plan.required_directories)) {
    Assert-HiaKnowledgeProjectDirectory `
        -ProjectRoot $resolvedRoot `
        -Path ([string]$directory) `
        -Create | Out-Null
}

$venvRoot = [string]$plan.layout.venv_root
$canonicalWorkerPython = [string]$plan.layout.worker_python
$workerPython = [string]$transaction.staging_python
$workerSource = [string]$plan.layout.worker_source
$expectedVenvRoot = Join-Path $resolvedRoot '.venv'
$expectedTransactionRoot = Join-Path $resolvedRoot (
    '.runtime\toolchains\hia-embedding'
)
if (
    -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
        [System.IO.Path]::GetFullPath($venvRoot).TrimEnd('\'),
        [System.IO.Path]::GetFullPath($expectedVenvRoot).TrimEnd('\')
    ) -or
    -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
        [System.IO.Path]::GetFullPath([string]$plan.layout.toolchain_root).TrimEnd('\'),
        [System.IO.Path]::GetFullPath($expectedTransactionRoot).TrimEnd('\')
    ) -or
    -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
        [System.IO.Path]::GetFullPath($canonicalWorkerPython),
        [System.IO.Path]::GetFullPath(
            (Join-Path $expectedVenvRoot 'Scripts\python.exe')
        )
    ) -or
    -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
        [System.IO.Path]::GetFullPath([string]$transaction.venv_root).TrimEnd('\'),
        [System.IO.Path]::GetFullPath($venvRoot).TrimEnd('\')
    )
) {
    throw 'Embedding contract and managed .venv transaction paths disagree.'
}
if (
    $null -ne $plan.layout.PSObject.Properties['legacy_venv_root'] -and
    -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
        [System.IO.Path]::GetFullPath(
            [string]$plan.layout.legacy_venv_root
        ).TrimEnd('\'),
        [System.IO.Path]::GetFullPath(
            [string]$transaction.legacy_root
        ).TrimEnd('\')
    )
) {
    throw 'Embedding contract legacy venv path disagrees with migration policy.'
}
if (-not (Test-HiaEmbeddingOrdinaryFile -Path $workerPython)) {
    throw 'The staged project-managed embedding Python is unavailable.'
}

$stagingEnvironment = @{}
foreach ($entry in $childEnvironment.GetEnumerator()) {
    $value = [string]$entry.Value
    if (
        [System.IO.Path]::IsPathRooted($value) -and
        [System.StringComparer]::OrdinalIgnoreCase.Equals(
            [System.IO.Path]::GetFullPath($value),
            [System.IO.Path]::GetFullPath($canonicalWorkerPython)
        )
    ) {
        $value = $workerPython
    }
    $stagingEnvironment[[string]$entry.Key] = $value
}
$uv = $managedEnvironment.uv

$priorTorchPython = $canonicalWorkerPython
if (
    -not (Test-HiaEmbeddingOrdinaryFile -Path $priorTorchPython) -and
    (Test-Path -LiteralPath $transaction.legacy_root -PathType Container)
) {
    try {
        Assert-HiaKnowledgeManagedVenvTree `
            -ProjectRoot $resolvedRoot `
            -Path ([string]$transaction.legacy_root) | Out-Null
        $legacyTorchPython = Join-Path (
            [string]$transaction.legacy_root
        ) 'Scripts\python.exe'
        if (Test-HiaEmbeddingOrdinaryFile -Path $legacyTorchPython) {
            $priorTorchPython = $legacyTorchPython
        }
    } catch {
        Write-HiaEmbeddingInstallLog `
            -Level 'WARNING' `
            -Message 'The legacy deep venv was not safe enough for a read-only Torch probe.'
    }
}
$priorTorchProbe = Get-HiaEmbeddingTorchProbe `
    -PythonExe $priorTorchPython `
    -Environment $childEnvironment `
    -RemoveEnvironment $removeEnvironment `
    -WorkingDirectory $resolvedRoot
$nvidiaAvailable = Test-HiaNvidiaGpuAvailable
$priorTorchCudaAvailable = (
    $null -ne $priorTorchProbe -and
    [bool]$priorTorchProbe.cuda_available
)
$resolvedDevice = Resolve-HiaEmbeddingInstallDevice `
    -RequestedDevice $Device `
    -TorchProbe $priorTorchProbe `
    -NvidiaAvailable $nvidiaAvailable

if (
    $resolvedDevice -eq 'cuda' -and
    -not $priorTorchCudaAvailable
) {
    Write-HiaEmbeddingInstallLog `
        -Level 'INFO' `
        -Message 'Preparing official CUDA PyTorch in the staged project-local .venv.'
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
        -Environment $stagingEnvironment `
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
Write-HiaEmbeddingInstallLog `
    -Level 'INFO' `
    -Message 'Installing and validating the embedding worker in the staged project-local .venv.'
$workerInstallResult = Invoke-HiaEmbeddingChildProcess `
    -FilePath ([string]$uv.exe) `
    -Arguments $workerInstallArguments `
    -Environment $stagingEnvironment `
    -RemoveEnvironment $removeEnvironment `
    -WorkingDirectory $resolvedRoot
Assert-HiaEmbeddingProcessSucceeded `
    -Result $workerInstallResult `
    -Operation 'Embedding worker installation'

$verifiedTorch = Get-HiaEmbeddingTorchProbe `
    -PythonExe $workerPython `
    -Environment $stagingEnvironment `
    -RemoveEnvironment $removeEnvironment `
    -WorkingDirectory $resolvedRoot
if ($null -eq $verifiedTorch -or -not [bool]$verifiedTorch.installed) {
    throw 'PyTorch is unavailable in the project-local embedding environment.'
}
if ($resolvedDevice -eq 'cuda' -and -not [bool]$verifiedTorch.cuda_available) {
    throw 'CUDA PyTorch installation completed, but torch.cuda.is_available() is false. The GPU repair was not accepted.'
}
if (-not (
    Test-HiaKnowledgeManagedVenv `
        -ProjectRoot $resolvedRoot `
        -VenvRoot ([string]$transaction.staging_root) `
        -PythonInstallRoot ([string]$managedEnvironment.python_install_root) `
        -Environment $stagingEnvironment `
        -RemoveEnvironment $removeEnvironment `
        -RequireParser `
        -RequireWorker
)) {
    throw 'The staged Bridge, MCP, parser, and embedding worker imports did not pass validation.'
}

Write-HiaEmbeddingInstallLog `
    -Level 'INFO' `
    -Message 'Validating or installing the selected embedding model payload.'
$downloadResult = Invoke-HiaEmbeddingChildProcess `
    -FilePath $workerPython `
    -Arguments @(
        '-I',
        '-X', 'utf8',
        '-B',
        $helperPath,
        '--action',
        'download',
        '--project-root',
        $resolvedRoot,
        '--profile',
        [string]$plan.profile_id,
        '--revision',
        [string]$plan.revision,
        '--staging-install-id',
        [string]$transaction.install_id
    ) `
    -Environment $stagingEnvironment `
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
if (
    [string]$downloadPayload.profile_id -ne [string]$plan.profile_id -or
    [string]$downloadPayload.model_id -ne [string]$plan.model_id -or
    -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
        [System.IO.Path]::GetFullPath([string]$downloadPayload.model_dir),
        [System.IO.Path]::GetFullPath([string]$plan.model_dir)
    )
) {
    throw 'Selected embedding model validation disagrees with the install plan.'
}

Write-HiaEmbeddingInstallLog `
    -Level 'INFO' `
    -Message 'Loading the selected model once for a bounded offline encode smoke.'
$smokeResult = Invoke-HiaEmbeddingChildProcess `
    -FilePath $workerPython `
    -Arguments @(
        '-I',
        '-X', 'utf8',
        '-B',
        $helperPath,
        '--action',
        'smoke',
        '--project-root',
        $resolvedRoot,
        '--profile',
        [string]$plan.profile_id,
        '--revision',
        [string]$plan.revision,
        '--staging-install-id',
        [string]$transaction.install_id,
        '--device',
        $resolvedDevice
    ) `
    -Environment $stagingEnvironment `
    -RemoveEnvironment $removeEnvironment `
    -WorkingDirectory $resolvedRoot `
    -TimeoutSeconds $script:HiaEmbeddingSmokeTimeoutSeconds
Assert-HiaEmbeddingProcessSucceeded `
    -Result $smokeResult `
    -Operation 'Staged embedding model encode smoke'
try {
    $smokePayload = ([string]$smokeResult.stdout).Trim() |
        ConvertFrom-Json
} catch {
    throw 'Staged embedding model encode smoke returned invalid JSON.'
}
if (
    [string]$smokePayload.status -ne 'ready' -or
    [string]$smokePayload.profile_id -ne [string]$plan.profile_id -or
    [string]$smokePayload.model_id -ne [string]$plan.model_id -or
    [string]$smokePayload.revision -ne [string]$plan.revision -or
    [string]$smokePayload.device -ne $resolvedDevice -or
    [int]$smokePayload.dimension -ne [int]$plan.dimension -or
    [double]::IsNaN([double]$smokePayload.norm) -or
    [double]::IsInfinity([double]$smokePayload.norm) -or
    [Math]::Abs([double]$smokePayload.norm - 1.0) -gt 0.0001
) {
    throw 'Staged embedding model encode smoke did not satisfy its contract.'
}
$smokeNorm = ([double]$smokePayload.norm).ToString(
    '0.000000',
    [System.Globalization.CultureInfo]::InvariantCulture
)
Write-HiaEmbeddingInstallLog `
    -Level 'INFO' `
    -Message (
        (
            'Embedding smoke encode result: profile={0}; device={1}; ' +
            'dimension={2}; vector_count=1; norm={3}.'
        ) -f
            [string]$smokePayload.profile_id,
            [string]$smokePayload.device,
            [int]$smokePayload.dimension,
            $smokeNorm
    )

$publish = Publish-HiaKnowledgeManagedVenv `
    -ProjectRoot $resolvedRoot `
    -Transaction $transaction
$published = $true
$runtimeBackupRoot = [string]$publish.backup_root
$transaction | Add-Member `
    -NotePropertyName published_backup_root `
    -NotePropertyValue $runtimeBackupRoot `
    -Force
if (-not (
    Test-HiaKnowledgeManagedVenv `
        -ProjectRoot $resolvedRoot `
        -VenvRoot $venvRoot `
        -PythonInstallRoot ([string]$managedEnvironment.python_install_root) `
        -Environment $childEnvironment `
        -RemoveEnvironment $removeEnvironment `
        -RequireParser `
        -RequireWorker
)) {
    throw 'The published root .venv failed its Bridge, MCP, parser, or worker validation.'
}
$verifiedPublishedTorch = Get-HiaEmbeddingTorchProbe `
    -PythonExe $canonicalWorkerPython `
    -Environment $childEnvironment `
    -RemoveEnvironment $removeEnvironment `
    -WorkingDirectory $resolvedRoot
if (
    $null -eq $verifiedPublishedTorch -or
    -not [bool]$verifiedPublishedTorch.installed -or
    ($resolvedDevice -eq 'cuda' -and
        -not [bool]$verifiedPublishedTorch.cuda_available)
) {
    throw 'The published root .venv failed its PyTorch/CUDA validation.'
}
$publishedModelResult = Invoke-HiaEmbeddingChildProcess `
    -FilePath $canonicalWorkerPython `
    -Arguments @(
        '-I',
        '-X', 'utf8',
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
    -Result $publishedModelResult `
    -Operation 'Published embedding model verification'
try {
    $publishedModelPayload = (
        ([string]$publishedModelResult.stdout).Trim() |
            ConvertFrom-Json
    )
} catch {
    throw 'Published embedding model verification returned invalid JSON.'
}
if (
    [string]$publishedModelPayload.profile_id -ne [string]$plan.profile_id -or
    [string]$publishedModelPayload.model_id -ne [string]$plan.model_id
) {
    throw 'Published embedding model verification disagrees with the install plan.'
}
$completion = Complete-HiaKnowledgeManagedVenvTransaction `
    -ProjectRoot $resolvedRoot `
    -Transaction $transaction `
    -IncludeVerifiedLegacyCandidate

$resultPayload = [ordered]@{
    status = [string]$publishedModelPayload.status
    profile_id = [string]$publishedModelPayload.profile_id
    model_id = [string]$publishedModelPayload.model_id
    model_dir = [string]$publishedModelPayload.model_dir
    requested_device = $Device
    resolved_device = $resolvedDevice
    torch_version = [string]$verifiedPublishedTorch.torch_version
    torch_cuda_build = [string]$verifiedPublishedTorch.torch_cuda_build
    cuda_available = [bool]$verifiedPublishedTorch.cuda_available
    device_name = [string]$verifiedPublishedTorch.device_name
    uv_version = [string]$uv.version
    venv_root = $venvRoot
    legacy_cleanup_candidate = [string]$completion.legacy_cleanup_candidate
    backup_cleanup_warning = [string]$completion.backup_cleanup_warning
    uv_cache_cleanup_candidate = [string]$uv.cache_root
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
    $runtimeFailure = $_.Exception
    if ($null -ne $transaction) {
        try {
            if ($published) {
                [void](Undo-HiaKnowledgeManagedVenvPublication `
                    -ProjectRoot $resolvedRoot `
                    -VenvRoot ([string]$transaction.venv_root) `
                    -BackupRoot $runtimeBackupRoot `
                    -InstallId ([string]$transaction.install_id))
            } else {
                [void](Move-HiaKnowledgeStagingToFailed `
                    -ProjectRoot $resolvedRoot `
                    -Transaction $transaction)
            }
        } catch {
            throw (
                "Embedding runtime installation failed: $($runtimeFailure.Message). " +
                'Transaction recovery also failed: ' +
                [string]$_.Exception.Message
            )
        }
    }
    throw $runtimeFailure
}
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
