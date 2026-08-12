[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet(
        'status',
        'environment-status',
        'environment-install',
        'environment-repair',
        'import-file',
        'import-folder',
        'thread-import',
        'thread-remove',
        'list',
        'delete',
        'rescan',
        'index-status',
        'index-build',
        'assets'
    )]
    [string]$Action = 'status',

    [Parameter(Position = 1)]
    [ValidateSet(
        'capabilities',
        'import',
        'list',
        'status',
        'resume',
        'delete',
        'repair'
    )]
    [string]$AssetAction = 'list',

    [AllowEmptyCollection()]
    [string[]]$Path = @(),

    [AllowEmptyString()]
    [string]$AssetId = '',

    [AllowEmptyString()]
    [string]$AsrModelPath = '',

    [AllowEmptyString()]
    [string]$SourceId = '',

    [AllowEmptyString()]
    [string]$ThreadId = '',

    [AllowEmptyString()]
    [string]$SnapshotFile = '',

    [AllowEmptyString()]
    [string]$BootstrapPython = '',

    [AllowEmptyString()]
    [string]$LogPath = '',

    [AllowEmptyString()]
    [string]$Profile = '',

    [ValidateSet('auto', 'cuda', 'cpu')]
    [string]$Device = 'auto',

    [ValidateRange(1, 64)]
    [int]$BatchSize = 32,

    [ValidateRange(0, 2147483647)]
    [int]$Offset = 0,

    [ValidateRange(1, 500)]
    [int]$Limit = 100,

    [switch]$Json
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$script:HiaKnowledgeSchema = 'hia-knowledge-powershell-cli/1'
$script:HiaKnowledgeProjectRoot = ''

function ConvertTo-HiaKnowledgeProcessArgument {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Value
    )

    if ($Value.Length -eq 0) { return '""' }
    if ($Value -notmatch '[\s"]') {
        return $Value
    }
    $escaped = $Value -replace '(\\*)"', '$1$1\"'
    $escaped = $escaped -replace '(\\+)$', '$1$1'
    return '"' + $escaped + '"'
}

function Test-HiaKnowledgeOrdinaryFile {
    param([Parameter(Mandatory = $true)][string]$LiteralPath)

    try {
        $item = Get-Item -LiteralPath $LiteralPath -Force -ErrorAction Stop
        return (
            $item -is [System.IO.FileInfo] -and
            ([int]$item.Attributes -band
                [int][System.IO.FileAttributes]::ReparsePoint) -eq 0
        )
    } catch {
        return $false
    }
}

function Assert-HiaKnowledgeProjectDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$LiteralPath,
        [switch]$Create
    )

    $root = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
    $target = [System.IO.Path]::GetFullPath($LiteralPath).TrimEnd('\')
    $prefix = $root + [System.IO.Path]::DirectorySeparatorChar
    if (
        -not $target.StartsWith(
            $prefix,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw 'Project-local knowledge directory escaped the project root.'
    }
    $rootItem = Get-Item -LiteralPath $root -Force -ErrorAction Stop
    if (
        $rootItem -isnot [System.IO.DirectoryInfo] -or
        ([int]$rootItem.Attributes -band
            [int][System.IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw 'Project root is not an ordinary directory.'
    }
    $relative = $target.Substring($prefix.Length)
    $current = $root
    foreach ($part in @($relative -split '[\\/]')) {
        if ([string]::IsNullOrWhiteSpace($part)) { continue }
        $current = Join-Path $current $part
        $item = Get-Item `
            -LiteralPath $current `
            -Force `
            -ErrorAction SilentlyContinue
        if ($null -eq $item) {
            if (-not $Create) { continue }
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
            throw 'Project-local knowledge directory contains a reparse point or non-directory object.'
        }
    }
    return $target
}

function Get-HiaKnowledgeProjectRoot {
    $root = [System.IO.Path]::GetFullPath(
        (Split-Path -Parent $PSScriptRoot)
    ).TrimEnd('\')
    $required = @(
        (Join-Path $root 'scripts\launcher\hia_knowledge_cli.py'),
        (
            Join-Path $root (
                'houdini_package\python_libs\hia_mcp_runtime\' +
                'knowledge_index.py'
            )
        ),
        (Join-Path $root 'src\hia_core\embedding_contract.py')
    )
    $item = Get-Item -LiteralPath $root -Force -ErrorAction Stop
    if (
        $item -isnot [System.IO.DirectoryInfo] -or
        ([int]$item.Attributes -band
            [int][System.IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw 'The project root derived from hia-knowledge.ps1 is unsafe.'
    }
    foreach ($requiredPath in $required) {
        if (-not (Test-HiaKnowledgeOrdinaryFile -LiteralPath $requiredPath)) {
            throw 'The project root derived from hia-knowledge.ps1 is incomplete.'
        }
    }
    return $root
}

function Get-HiaKnowledgePython {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [AllowEmptyString()][string]$ExplicitBootstrap = ''
    )

    if (-not [string]::IsNullOrWhiteSpace($ExplicitBootstrap)) {
        $explicit = [System.IO.Path]::GetFullPath($ExplicitBootstrap)
        if (-not (Test-HiaKnowledgeOrdinaryFile -LiteralPath $explicit)) {
            throw 'The explicit BootstrapPython is not an ordinary file.'
        }
        return $explicit
    }
    $candidate = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    if (Test-HiaKnowledgeOrdinaryFile -LiteralPath $candidate) {
        return [System.IO.Path]::GetFullPath($candidate)
    }
    throw (
        'The canonical project-local knowledge Python is unavailable. ' +
        'Run environment-install, ' +
        'or pass -BootstrapPython explicitly as an advanced bootstrap override.'
    )
}

function New-HiaKnowledgeChildEnvironment {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [switch]$ReadOnly
    )

    $cacheRoot = Join-Path $ProjectRoot '.runtime\cache\knowledge-cli'
    $tempRoot = Join-Path $cacheRoot 'tmp'
    $homeRoot = Join-Path $cacheRoot 'home'
    $environment = @{
        'PYTHONNOUSERSITE' = '1'
        'PYTHONDONTWRITEBYTECODE' = '1'
        'PYTHONUTF8' = '1'
        'PYTHONIOENCODING' = 'utf-8'
    }
    if ($ReadOnly) {
        foreach ($directory in @($cacheRoot, $tempRoot, $homeRoot)) {
            Assert-HiaKnowledgeProjectDirectory `
                -ProjectRoot $ProjectRoot `
                -LiteralPath $directory | Out-Null
        }
        return $environment
    }
    foreach ($directory in @($cacheRoot, $tempRoot, $homeRoot)) {
        Assert-HiaKnowledgeProjectDirectory `
            -ProjectRoot $ProjectRoot `
            -LiteralPath $directory `
            -Create | Out-Null
    }
    $environment['HOME'] = $homeRoot
    $environment['XDG_CACHE_HOME'] = $homeRoot
    $environment['TMP'] = $tempRoot
    $environment['TEMP'] = $tempRoot
    return $environment
}

function Invoke-HiaKnowledgeProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [hashtable]$Environment = @{},
        [string[]]$RemoveEnvironment = @(),
        [Parameter(Mandatory = $true)][string]$WorkingDirectory
    )

    if (-not (Test-HiaKnowledgeOrdinaryFile -LiteralPath $FilePath)) {
        throw 'The requested knowledge CLI executable is unavailable or unsafe.'
    }
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $FilePath
    $startInfo.Arguments = (
        @(
            $Arguments | ForEach-Object {
                ConvertTo-HiaKnowledgeProcessArgument -Value ([string]$_)
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
        [Environment]::SetEnvironmentVariable(
            [string]$name,
            $null,
            [EnvironmentVariableTarget]::Process
        )
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
            throw 'Knowledge CLI child process did not start.'
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
        foreach ($name in $environmentNames) {
            [Environment]::SetEnvironmentVariable(
                [string]$name,
                $savedEnvironment[$name],
                [EnvironmentVariableTarget]::Process
            )
        }
    }
}

function Invoke-HiaKnowledgeStreamingProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [hashtable]$Environment = @{},
        [string[]]$RemoveEnvironment = @(),
        [Parameter(Mandatory = $true)][string]$WorkingDirectory
    )

    if (-not (Test-HiaKnowledgeOrdinaryFile -LiteralPath $FilePath)) {
        throw 'The requested knowledge CLI executable is unavailable or unsafe.'
    }
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $FilePath
    $startInfo.Arguments = (
        @(
            $Arguments | ForEach-Object {
                ConvertTo-HiaKnowledgeProcessArgument -Value ([string]$_)
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
        [Environment]::SetEnvironmentVariable(
            [string]$name,
            $null,
            [EnvironmentVariableTarget]::Process
        )
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
            throw 'Knowledge CLI child process did not start.'
        }
        $stderrTask = $process.StandardError.ReadToEndAsync()
        while (-not $process.StandardOutput.EndOfStream) {
            $line = $process.StandardOutput.ReadLine()
            if ($null -ne $line) {
                [Console]::Out.WriteLine([string]$line)
                [Console]::Out.Flush()
            }
        }
        $process.WaitForExit()
        [void]$stderrTask.Wait()
        return [pscustomobject]@{
            exit_code = [int]$process.ExitCode
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

function Write-HiaKnowledgeProcessResult {
    param([Parameter(Mandatory = $true)]$Result)

    if (-not [string]::IsNullOrEmpty([string]$Result.stdout)) {
        [Console]::Out.Write([string]$Result.stdout)
    }
    if (-not [string]::IsNullOrEmpty([string]$Result.stderr)) {
        [Console]::Error.Write([string]$Result.stderr)
    }
}

function Write-HiaKnowledgeError {
    param(
        [Parameter(Mandatory = $true)][string]$ActionName,
        [Parameter(Mandatory = $true)][string]$Message
    )

    $safe = [regex]::Replace(
        ([regex]::Replace([string]$Message, '[\r\n]+', ' ')).Trim(),
        '(?i)\b(token|secret|password|authorization)\s*[:=]\s*\S+',
        '$1=[REDACTED]'
    )
    $payload = [ordered]@{
        schema = $script:HiaKnowledgeSchema
        ok = $false
        action = $ActionName
        project_root = $script:HiaKnowledgeProjectRoot
        error = [ordered]@{
            code = 'KNOWLEDGE_CLI_FAILED'
            message = $safe
        }
    } | ConvertTo-Json -Compress
    [Console]::Out.WriteLine($payload)
}

function Write-HiaAssetCliError {
    param(
        [Parameter(Mandatory = $true)][string]$ActionName,
        [Parameter(Mandatory = $true)][string]$Message
    )

    $safe = [regex]::Replace(
        ([regex]::Replace([string]$Message, '[\r\n]+', ' ')).Trim(),
        '(?i)\b(token|secret|password|authorization)\s*[:=]\s*\S+',
        '$1=[REDACTED]'
    )
    $payload = [ordered]@{
        protocol = 'hia-knowledge-index-jsonl/1'
        event = 'error'
        action = $ActionName
        code = 'KNOWLEDGE_CLI_FAILED'
        message = $safe
        error = [ordered]@{
            code = 'KNOWLEDGE_CLI_FAILED'
            message = $safe
            recoverable = $true
        }
    } | ConvertTo-Json -Compress
    [Console]::Out.WriteLine($payload)
}

function Write-HiaKnowledgeMissingEnvironment {
    param(
        [Parameter(Mandatory = $true)][string]$ActionName,
        [Parameter(Mandatory = $true)][string]$ProjectRoot
    )

    $venvRoot = Join-Path $ProjectRoot '.venv'
    $pythonPath = Join-Path $venvRoot 'Scripts\python.exe'
    $uvPath = Join-Path $ProjectRoot (
        '.runtime\toolchains\hia-embedding\uv\0.11.29\uv.exe'
    )
    $indexPath = Join-Path $ProjectRoot (
        '.runtime\knowledge\knowledge.sqlite3'
    )
    $sourcesPath = Join-Path $ProjectRoot '.runtime\knowledge\sources'
    try {
        $drive = [System.IO.DriveInfo]::new(
            [System.IO.Path]::GetPathRoot($ProjectRoot)
        )
        $freeBytes = [long]$drive.AvailableFreeSpace
        $totalBytes = [long]$drive.TotalSize
    } catch {
        $freeBytes = [long]-1
        $totalBytes = [long]-1
    }
    $environment = [ordered]@{
        state = 'missing'
        project_root = $ProjectRoot
        venv = [ordered]@{
            path = $venvRoot
            exists = $false
            resolved_from_project = $true
            portable = $false
            repair_required = $true
        }
        python = [ordered]@{
            available = $false
            path = $pythonPath
            expected_path = $pythonPath
            version = ''
            bits = 0
            prefix = ''
            base_prefix = ''
            portable = $false
            resolution = (
                'project-relative contract; re-resolved after project moves'
            )
        }
        uv = [ordered]@{
            available = (
                Test-HiaKnowledgeOrdinaryFile -LiteralPath $uvPath
            )
            path = if (
                Test-HiaKnowledgeOrdinaryFile -LiteralPath $uvPath
            ) { $uvPath } else { '' }
            version = if (
                Test-HiaKnowledgeOrdinaryFile -LiteralPath $uvPath
            ) { '0.11.29' } else { '' }
            source = 'project'
        }
        parser = [ordered]@{
            name = 'pypdf'
            required_version = '6.14.2'
            installed = $false
            version = ''
            repair_action = 'environment-install'
        }
        torch = [ordered]@{
            installed = $false
            version = ''
            cuda_build = ''
            cuda_available = $false
            gpu_name = ''
        }
        embedding_worker = [ordered]@{
            installed = $false
            version = ''
        }
        models = [ordered]@{
            items = @()
            installed_profiles = @()
        }
        requested_profile = ''
        active_profile = ''
        profile_source = 'lexical'
        embedding_mode = 'fts5'
        embedding_runtime_environment = [ordered]@{}
        fallback_non_blocking = $true
        houdini_launch_blocked = $false
        repair_actions = [ordered]@{
            parser_only = 'environment-install'
            embedding = (
                'Install embeddings explicitly only after the local environment is ready.'
            )
        }
        storage = [ordered]@{
            free_bytes = $freeBytes
            total_bytes = $totalBytes
            repair_space_hint_bytes = [long](2GB)
            repair_space_hint_satisfied = if ($freeBytes -lt 0) {
                $null
            } else {
                $freeBytes -ge [long](2GB)
            }
        }
        proxy = [ordered]@{
            http_configured = -not [string]::IsNullOrWhiteSpace(
                [Environment]::GetEnvironmentVariable('HTTP_PROXY')
            )
            https_configured = -not [string]::IsNullOrWhiteSpace(
                [Environment]::GetEnvironmentVariable('HTTPS_PROXY')
            )
            no_proxy_configured = -not [string]::IsNullOrWhiteSpace(
                [Environment]::GetEnvironmentVariable('NO_PROXY')
            )
            values_redacted = $true
        }
    }
    $result = if ($ActionName -eq 'environment-status') {
        $environment
    } else {
        [ordered]@{
            environment = $environment
            index = [ordered]@{
                available = (
                    Test-HiaKnowledgeOrdinaryFile -LiteralPath $indexPath
                )
                path = $indexPath
                fts5_available = (
                    Test-HiaKnowledgeOrdinaryFile -LiteralPath $indexPath
                )
            }
            sources = [ordered]@{
                items = @()
                total = $null
                managed_root = $sourcesPath
                deletion_notice = (
                    'Deleting a managed copy never deletes the original file.'
                )
            }
            source_contract = [ordered]@{
                supported_formats = @(
                    '.aac', '.avi', '.flac', '.htm', '.html', '.m4a',
                    '.m4v', '.md', '.mkv', '.mov', '.mp3', '.mp4', '.ogg',
                    '.pdf', '.srt', '.txt', '.vtt', '.wav', '.webm'
                )
                folder_supported_formats = @(
                    '.htm', '.html', '.md', '.pdf', '.srt', '.txt', '.vtt'
                )
                maximum_document_bytes = 4194304
                managed_copy = $true
                media_copy_policy = 'transcript_sidecar_only'
                deletion_notice = (
                    'Deleting a managed copy never deletes the original file.'
                )
            }
            fallback = [ordered]@{
                mode = 'fts5'
                fts5_available = (
                    Test-HiaKnowledgeOrdinaryFile -LiteralPath $indexPath
                )
                houdini_launch_blocked = $false
            }
            warning = ''
        }
    }
    $payload = [ordered]@{
        schema = 'hia-knowledge-launcher-cli/1'
        ok = $true
        action = $ActionName
        project_root = $ProjectRoot
        result = $result
    } | ConvertTo-Json -Compress -Depth 10
    [Console]::Out.WriteLine($payload)
}

function Assert-HiaKnowledgeManagedEnvironment {
    param(
        [Parameter(Mandatory = $true)]$Payload,
        [Parameter(Mandatory = $true)][string]$CanonicalPython
    )

    $result = $Payload.result
    $pythonPath = if ($null -ne $result.python.path) {
        [string]$result.python.path
    } else {
        [string]$result.python.executable
    }
    $resolvedPython = try {
        [System.IO.Path]::GetFullPath($pythonPath)
    } catch {
        ''
    }
    $expectedPython = [System.IO.Path]::GetFullPath($CanonicalPython)
    $valid = (
        [string]$Payload.schema -eq 'hia-knowledge-launcher-cli/1' -and
        $Payload.ok -eq $true -and
        [string]$result.state -eq 'ready' -and
        $result.venv.portable -eq $true -and
        $result.python.portable -eq $true -and
        $result.python.base_prefix_is_project_local -eq $true -and
        $result.python.managed_marker.valid -eq $true -and
        [System.StringComparer]::OrdinalIgnoreCase.Equals(
            $resolvedPython,
            $expectedPython
        )
    )
    if (-not $valid) {
        throw (
            'This action requires a ready, managed, portable project-local ' +
            'knowledge environment. Run environment-repair first.'
        )
    }
    return $result
}

function Get-HiaKnowledgeEnvironmentInstallPlan {
    param(
        [AllowNull()]$Environment,
        [Parameter(Mandatory = $true)]
        [ValidateSet('environment-install', 'environment-repair')]
        [string]$ActionName,
        [AllowEmptyString()][string]$RequestedProfile = '',
        [ValidateSet('auto', 'cuda', 'cpu')]
        [string]$RequestedDevice = 'auto'
    )

    # Keep this launcher boundary aligned with PROFILE_REGISTRY in
    # src/hia_core/embedding_contract.py.  An omitted Revision is normalized
    # to "main" by install_hia_embedding.py, so clean installs must not try to
    # discover it from an installed-model record that cannot exist yet.
    $profileCatalog = @{
        'qwen3-embedding-0.6b' = 'main'
        'qwen3-embedding-8b' = 'main'
    }
    $installedProfiles = @()
    if ($null -ne $Environment -and $null -ne $Environment.models) {
        $installedProfiles = @(
            $Environment.models.items |
                Where-Object { $_.installed -eq $true } |
                ForEach-Object { [string]$_.profile_id } |
                Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
        )
    }
    $requestedProfileName = $RequestedProfile.Trim()
    $selectedModel = $null
    $selectedProfile = ''
    $selectedRevision = ''
    if (-not [string]::IsNullOrWhiteSpace($requestedProfileName)) {
        if (-not $profileCatalog.ContainsKey($requestedProfileName)) {
            throw (
                "Unsupported embedding profile '$requestedProfileName'. " +
                'Choose a profile declared by the embedding installer contract.'
            )
        }
        $selectedProfile = $requestedProfileName
        $selectedRevision = [string]$profileCatalog[$requestedProfileName]
        if ($requestedProfileName -in $installedProfiles) {
            $selectedModel = @(
                $Environment.models.items |
                    Where-Object {
                        $_.installed -eq $true -and
                        [string]$_.profile_id -eq $requestedProfileName
                    } |
                    Select-Object -First 1
            )[0]
            if (
                $null -ne $selectedModel -and
                -not [string]::IsNullOrWhiteSpace(
                    [string]$selectedModel.revision
                )
            ) {
                $selectedRevision = [string]$selectedModel.revision
            }
        }
    }
    $selectedDevice = $RequestedDevice
    if (
        $selectedDevice -eq 'auto' -and
        $null -ne $Environment -and
        [string]$Environment.requested_device -in @('cuda', 'cpu')
    ) {
        $selectedDevice = [string]$Environment.requested_device
    } elseif (
        $selectedDevice -eq 'auto' -and
        $null -ne $Environment -and
        $Environment.torch.cuda_available -eq $true
    ) {
        $selectedDevice = 'cuda'
    }
    return [pscustomobject]@{
        action = $ActionName
        repair = $ActionName -eq 'environment-repair'
        parser_only = [string]::IsNullOrWhiteSpace($selectedProfile)
        profile = $selectedProfile
        revision = $selectedRevision
        device = $selectedDevice
    }
}

try {
    $projectRoot = Get-HiaKnowledgeProjectRoot
    $script:HiaKnowledgeProjectRoot = $projectRoot
    $helperPath = Join-Path $projectRoot 'scripts\launcher\hia_knowledge_cli.py'
    $assetRepairPath = Join-Path $projectRoot 'scripts\repair_hia_assets.py'
    $indexCliPath = Join-Path $projectRoot (
        'houdini_package\python_libs\hia_mcp_runtime\knowledge_index_cli.py'
    )
    $removeEnvironment = @(
        'PYTHONHOME',
        'PYTHONPATH',
        'VIRTUAL_ENV',
        'CONDA_PREFIX'
    )
    $assetCapabilitiesAction = (
        $Action -eq 'assets' -and
        $AssetAction -eq 'capabilities'
    )
    $childEnvironment = New-HiaKnowledgeChildEnvironment `
        -ProjectRoot $projectRoot `
        -ReadOnly:(
            $Action -in @('status', 'environment-status', 'list') -or
            $assetCapabilitiesAction
        )

    if ($Action -in @('environment-install', 'environment-repair')) {
        $installerPath = Join-Path $projectRoot (
            'scripts\launcher\Install-HiaEmbedding.ps1'
        )
        if (-not (Test-HiaKnowledgeOrdinaryFile -LiteralPath $installerPath)) {
            throw 'The project-local environment installer is unavailable.'
        }
        $enginePath = [string](Get-Process -Id $PID -ErrorAction Stop).Path
        if (-not (Test-HiaKnowledgeOrdinaryFile -LiteralPath $enginePath)) {
            throw 'The current PowerShell executable is unavailable.'
        }
        $environment = $null
        try {
            $statusPython = Get-HiaKnowledgePython `
                -ProjectRoot $projectRoot `
                -ExplicitBootstrap $BootstrapPython
            if (
                Test-HiaKnowledgeOrdinaryFile -LiteralPath $statusPython
            ) {
                $statusResult = Invoke-HiaKnowledgeProcess `
                    -FilePath $statusPython `
                    -Arguments @(
                        '-I',
                        '-X', 'utf8',
                        '-B',
                        $helperPath,
                        '--project-root',
                        $projectRoot,
                        'environment-status'
                    ) `
                    -Environment $childEnvironment `
                    -RemoveEnvironment $removeEnvironment `
                    -WorkingDirectory $projectRoot
                if ([int]$statusResult.exit_code -eq 0) {
                    $statusPayload = (
                        [string]$statusResult.stdout
                    ).Trim() | ConvertFrom-Json -ErrorAction Stop
                    if (
                        [string]$statusPayload.schema -eq
                            'hia-knowledge-launcher-cli/1' -and
                        $statusPayload.ok -eq $true
                    ) {
                        $environment = $statusPayload.result
                    }
                }
            }
        } catch {
            $environment = $null
        }
        $environmentPlan = Get-HiaKnowledgeEnvironmentInstallPlan `
            -Environment $environment `
            -ActionName $Action `
            -RequestedProfile $Profile `
            -RequestedDevice $Device
        $installArguments = @(
            '-NoProfile',
            '-NonInteractive',
            '-ExecutionPolicy', 'Bypass',
            '-File', $installerPath,
            '-ProjectRoot', $projectRoot
        )
        if ([bool]$environmentPlan.parser_only) {
            $installArguments += '-KnowledgeParserOnly'
        } else {
            $installArguments += @(
                '-Profile', [string]$environmentPlan.profile,
                '-Device', [string]$environmentPlan.device
            )
            if (
                -not [string]::IsNullOrWhiteSpace(
                    [string]$environmentPlan.revision
                )
            ) {
                $installArguments += @(
                    '-Revision',
                    [string]$environmentPlan.revision
                )
            }
        }
        if ($Action -eq 'environment-repair') {
            $installArguments += '-Repair'
        }
        if (-not [string]::IsNullOrWhiteSpace($BootstrapPython)) {
            $installArguments += @('-BootstrapPython', $BootstrapPython)
        }
        if (-not [string]::IsNullOrWhiteSpace($LogPath)) {
            $installArguments += @('-LogPath', $LogPath)
        }
        $installResult = Invoke-HiaKnowledgeProcess `
            -FilePath $enginePath `
            -Arguments $installArguments `
            -Environment $childEnvironment `
            -RemoveEnvironment $removeEnvironment `
            -WorkingDirectory $projectRoot
        Write-HiaKnowledgeProcessResult -Result $installResult
        exit ([int]$installResult.exit_code)
    }

    $canonicalPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    $canonicalAvailable = Test-HiaKnowledgeOrdinaryFile `
        -LiteralPath $canonicalPython
    if (
        -not $canonicalAvailable -and
        [string]::IsNullOrWhiteSpace($BootstrapPython) -and
        $Action -in @('status', 'environment-status')
    ) {
        Write-HiaKnowledgeMissingEnvironment `
            -ActionName $Action `
            -ProjectRoot $projectRoot
        exit 0
    }
    if (
        -not $canonicalAvailable -and
        $Action -notin @('status', 'environment-status') -and
        -not $assetCapabilitiesAction
    ) {
        throw (
            'This action requires the canonical project-local knowledge ' +
            'environment. Run environment-install first.'
        )
    }
    $python = if (
        $Action -in @('status', 'environment-status') -or
        $assetCapabilitiesAction
    ) {
        Get-HiaKnowledgePython `
            -ProjectRoot $projectRoot `
            -ExplicitBootstrap $BootstrapPython
    } else {
        [System.IO.Path]::GetFullPath($canonicalPython)
    }
    $helperArguments = @(
        '-I',
        '-X', 'utf8',
        '-B',
        $helperPath,
        '--project-root',
        $projectRoot
    )
    $verifiedEnvironment = $null
    if (
        $Action -notin @('status', 'environment-status') -and
        -not $assetCapabilitiesAction
    ) {
        $environmentProbe = Invoke-HiaKnowledgeProcess `
            -FilePath $python `
            -Arguments ($helperArguments + 'environment-status') `
            -Environment $childEnvironment `
            -RemoveEnvironment $removeEnvironment `
            -WorkingDirectory $projectRoot
        if ($environmentProbe.exit_code -ne 0) {
            throw 'The project-local knowledge environment probe failed.'
        }
        try {
            $environmentPayload = (
                [string]$environmentProbe.stdout
            ).Trim() | ConvertFrom-Json
        } catch {
            throw 'The project-local knowledge environment probe returned invalid JSON.'
        }
        $verifiedEnvironment = Assert-HiaKnowledgeManagedEnvironment `
            -Payload $environmentPayload `
            -CanonicalPython $canonicalPython
    }
    if (
        $Action -notin @('status', 'environment-status') -and
        -not $assetCapabilitiesAction
    ) {
        $runtimeEnvironment = $verifiedEnvironment.embedding_runtime_environment
        foreach ($property in @($runtimeEnvironment.PSObject.Properties)) {
            $name = [string]$property.Name
            $value = [string]$property.Value
            if (
                $name -notmatch '^HIA_[A-Z0-9_]{2,96}$' -or
                [string]::IsNullOrWhiteSpace($value)
            ) {
                throw 'The knowledge environment probe returned an unsafe runtime setting.'
            }
            $childEnvironment[$name] = $value
        }
    }
    if ($Action -in @(
        'status',
        'environment-status',
        'import-file',
        'import-folder',
        'thread-import',
        'thread-remove',
        'list',
        'delete',
        'rescan'
    )) {
        $helperArguments += $Action
        if ($Action -in @('import-file', 'import-folder')) {
            if ($Path.Count -eq 0) {
                throw "$Action requires at least one -Path."
            }
            foreach ($selectedPath in $Path) {
                if ([string]::IsNullOrWhiteSpace($selectedPath)) {
                    throw "$Action received an empty -Path."
                }
                $helperArguments += @('--path', $selectedPath)
            }
        } elseif ($Action -eq 'delete') {
            if ([string]::IsNullOrWhiteSpace($SourceId)) {
                throw 'delete requires -SourceId.'
            }
            $helperArguments += @('--source-id', $SourceId)
        } elseif ($Action -eq 'thread-import') {
            if ([string]::IsNullOrWhiteSpace($ThreadId)) {
                throw 'thread-import requires -ThreadId.'
            }
            if ([string]::IsNullOrWhiteSpace($SnapshotFile)) {
                throw 'thread-import requires -SnapshotFile.'
            }
            $helperArguments += @(
                '--thread-id', $ThreadId,
                '--snapshot-file', $SnapshotFile
            )
        } elseif ($Action -eq 'thread-remove') {
            if ([string]::IsNullOrWhiteSpace($ThreadId)) {
                throw 'thread-remove requires -ThreadId.'
            }
            $helperArguments += @('--thread-id', $ThreadId)
        }
        $helperResult = Invoke-HiaKnowledgeProcess `
            -FilePath $python `
            -Arguments $helperArguments `
            -Environment $childEnvironment `
            -RemoveEnvironment $removeEnvironment `
            -WorkingDirectory $projectRoot
        Write-HiaKnowledgeProcessResult -Result $helperResult
        exit ([int]$helperResult.exit_code)
    }

    if (-not (Test-HiaKnowledgeOrdinaryFile -LiteralPath $indexCliPath)) {
        throw 'The stable HIA knowledge index CLI is unavailable.'
    }
    if ($Action -eq 'assets' -and $AssetAction -eq 'repair') {
        if (-not (
            Test-HiaKnowledgeOrdinaryFile -LiteralPath $assetRepairPath
        )) {
            throw 'The project-local asset repair helper is unavailable.'
        }
        $repairArguments = @(
            '-I',
            '-X', 'utf8',
            '-B',
            $assetRepairPath,
            '--project-root',
            $projectRoot
        )
        if (-not [string]::IsNullOrWhiteSpace($AsrModelPath)) {
            $repairArguments += @(
                '--asr-model-path',
                $AsrModelPath
            )
        }
        if ($Json) {
            $repairResult = Invoke-HiaKnowledgeProcess `
                -FilePath $python `
                -Arguments $repairArguments `
                -Environment $childEnvironment `
                -RemoveEnvironment $removeEnvironment `
                -WorkingDirectory $projectRoot
            $terminal = $null
            foreach ($line in @(
                [string]$repairResult.stdout -split '\r?\n'
            )) {
                if ([string]::IsNullOrWhiteSpace($line)) { continue }
                try {
                    $event = $line | ConvertFrom-Json -ErrorAction Stop
                } catch {
                    throw 'Asset repair emitted invalid JSONL.'
                }
                if (
                    [string]$event.action -eq 'assets.repair' -and
                    [string]$event.event -in @('completed', 'error')
                ) {
                    $terminal = $event
                }
            }
            if ($null -eq $terminal) {
                throw 'Asset repair returned no terminal JSON event.'
            }
            [Console]::Out.WriteLine(
                ($terminal | ConvertTo-Json -Compress -Depth 12)
            )
            if (-not [string]::IsNullOrEmpty([string]$repairResult.stderr)) {
                [Console]::Error.Write([string]$repairResult.stderr)
            }
            exit ([int]$repairResult.exit_code)
        }
        $repairResult = Invoke-HiaKnowledgeStreamingProcess `
            -FilePath $python `
            -Arguments $repairArguments `
            -Environment $childEnvironment `
            -RemoveEnvironment $removeEnvironment `
            -WorkingDirectory $projectRoot
        if (-not [string]::IsNullOrEmpty([string]$repairResult.stderr)) {
            [Console]::Error.Write([string]$repairResult.stderr)
        }
        exit ([int]$repairResult.exit_code)
    }
    if ($Action -eq 'assets') {
        $assetArguments = @(
            '-I',
            '-X', 'utf8',
            '-B',
            $indexCliPath,
            '--project-root',
            $projectRoot
        )
        if ($Json) {
            $assetArguments += @('--format', 'json')
        }
        $assetArguments += @('assets', $AssetAction)
        switch ($AssetAction) {
            'import' {
                if ($Path.Count -ne 1 -or [string]::IsNullOrWhiteSpace($Path[0])) {
                    throw 'assets import requires exactly one -Path.'
                }
                $assetArguments += @('--path', [string]$Path[0])
            }
            'list' {
                $assetArguments += @(
                    '--offset', [string]$Offset,
                    '--limit', [string]$Limit
                )
            }
            { $_ -in @('status', 'resume', 'delete') } {
                if ([string]::IsNullOrWhiteSpace($AssetId)) {
                    throw "assets $AssetAction requires -AssetId."
                }
                $assetArguments += @('--asset-id', $AssetId)
            }
        }
        $assetResult = Invoke-HiaKnowledgeStreamingProcess `
            -FilePath $python `
            -Arguments $assetArguments `
            -Environment $childEnvironment `
            -RemoveEnvironment $removeEnvironment `
            -WorkingDirectory $projectRoot
        if (-not [string]::IsNullOrEmpty([string]$assetResult.stderr)) {
            [Console]::Error.Write([string]$assetResult.stderr)
        }
        exit ([int]$assetResult.exit_code)
    }
    if ($Action -eq 'index-build') {
        $rescanResult = Invoke-HiaKnowledgeProcess `
            -FilePath $python `
            -Arguments @(
                '-I',
                '-X', 'utf8',
                '-B',
                $helperPath,
                '--project-root',
                $projectRoot,
                'rescan'
            ) `
            -Environment $childEnvironment `
            -RemoveEnvironment $removeEnvironment `
            -WorkingDirectory $projectRoot
        if ($rescanResult.exit_code -ne 0) {
            Write-HiaKnowledgeProcessResult -Result $rescanResult
            exit ([int]$rescanResult.exit_code)
        }
    }
    $indexArguments = @(
        '-I',
        '-X', 'utf8',
        '-B',
        $indexCliPath,
        '--project-root',
        $projectRoot
    )
    if ($Json) {
        $indexArguments += @('--format', 'json')
    }
    if ($Action -eq 'index-build') {
        $indexArguments += @('build', '--batch-size', [string]$BatchSize)
    } else {
        $indexArguments += 'status'
    }
    $indexResult = Invoke-HiaKnowledgeProcess `
        -FilePath $python `
        -Arguments $indexArguments `
        -Environment $childEnvironment `
        -RemoveEnvironment $removeEnvironment `
        -WorkingDirectory $projectRoot
    Write-HiaKnowledgeProcessResult -Result $indexResult
    exit ([int]$indexResult.exit_code)
} catch {
    if ($Action -eq 'assets') {
        Write-HiaAssetCliError `
            -ActionName "assets.$AssetAction" `
            -Message ([string]$_.Exception.Message)
    } else {
        Write-HiaKnowledgeError `
            -ActionName $Action `
            -Message ([string]$_.Exception.Message)
    }
    exit 1
}
