[CmdletBinding()]
param(
    [ValidatePattern('^[0-9A-Za-z][0-9A-Za-z.-]*$')]
    [string]$Version = '0.1.1-preview',
    [switch]$InstallLocalSdk,
    [switch]$PreflightOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\')
$runtimeRoot = Join-Path $projectRoot '.runtime'
$releaseRoot = Join-Path $runtimeRoot 'release'
$workRoot = Join-Path $releaseRoot 'work'
$packageName = "Big-Chicken-Houdini-Intelligence-Agent-v$Version-win-x64"
$archivePath = Join-Path $releaseRoot "$packageName.zip"
$checksumsPath = Join-Path $releaseRoot "SHA256SUMS-v$Version.txt"
$stageRoot = Join-Path $workRoot ([guid]::NewGuid().ToString('N'))
$packageRoot = Join-Path $stageRoot $packageName
$launcherDist = $packageRoot
$launcherBuildScript = Join-Path $projectRoot 'scripts\build-launcher.ps1'
$publicReleaseChecker = Join-Path $projectRoot 'scripts\check-public-release.py'

# Runtime package inputs are deliberately explicit. Do not replace this with a
# repository-wide copy: development history, tests, caches, HIP files, renders,
# credentials, and user assets do not belong in a public release archive.
$releaseFileAllowlist = @(
    '.codex/config.toml',
    '.agents/skills/houdini-visual-research/references/build-brief-and-review.md',
    'AGENTS.md',
    'CHANGELOG.md',
    'LICENSE',
    'README.md',
    'SECURITY.md',
    'THIRD_PARTY_NOTICES.md',
    'pyproject.toml',
    'assets/launcher/launcher-hero.png',
    'docs/ARCHITECTURE.md',
    'docs/DIAGNOSTICS.md',
    'docs/HIA-MCP-V2.md',
    'docs/INSTALLATION.md',
    'docs/PROJECT_TEAM_LIVE_ACCEPTANCE.md',
    'docs/ROADMAP.md',
    'docs/SAFETY.md',
    'houdini_package/packages/houdini_intelligence.json',
    'houdini_package/python3.10libs/uiready.py',
    'houdini_package/python3.11libs/uiready.py',
    'houdini_package/python3.13libs/uiready.py',
    'houdini_package/python_panels/houdini_intelligence.pypanel',
    'houdini_package/python_libs/hia_mcp_runtime/__init__.py',
    'houdini_package/python_libs/hia_mcp_runtime/deterministic_sources.py',
    'houdini_package/python_libs/hia_mcp_runtime/embedding_client.py',
    'houdini_package/python_libs/hia_mcp_runtime/executor.py',
    'houdini_package/python_libs/hia_mcp_runtime/hybrid_knowledge.py',
    'houdini_package/python_libs/hia_mcp_runtime/http_server.py',
    'houdini_package/python_libs/hia_mcp_runtime/knowledge_index.py',
    'houdini_package/python_libs/hia_mcp_runtime/knowledge_index_cli.py',
    'houdini_package/python_libs/hia_panel/__init__.py',
    'houdini_package/python_libs/hia_panel/attachment_store.py',
    'houdini_package/python_libs/hia_panel/bridge_client.py',
    'houdini_package/python_libs/hia_panel/composer.py',
    'houdini_package/python_libs/hia_panel/conversation_view.py',
    'houdini_package/python_libs/hia_panel/houdini_read_adapter.py',
    'houdini_package/python_libs/hia_panel/http_transport.py',
    'houdini_package/python_libs/hia_panel/network_response.py',
    'houdini_package/python_libs/hia_panel/panel.py',
    'houdini_package/python_libs/hia_panel/project_team.py',
    'houdini_package/python_libs/hia_panel/project_team_controller.py',
    'houdini_package/python_libs/hia_panel/project_team_view.py',
    'houdini_package/python_libs/hia_panel/runtime_diagnostics.py',
    'houdini_package/python_libs/hia_panel/task_insights.py',
    'houdini_package/python_libs/hia_panel/turn_state.py',
    'scripts/bootstrap-runtime.ps1',
    'scripts/hia-cache.ps1',
    'scripts/hia-knowledge.ps1',
    'scripts/hia-launcher.ps1',
    'scripts/launch-houdini.ps1',
    'scripts/launcher/HiaLauncher.Core.psm1',
    'scripts/launcher/HiaLauncher.Wpf.ps1',
    'scripts/launcher/HiaLauncher.xaml',
    'scripts/launcher/Install-HiaEmbedding.ps1',
    'scripts/launcher/hia_knowledge_cli.py',
    'scripts/launcher/install_hia_embedding.py',
    'services/bridge/hia_bridge/knowledge_cli.py',
    'src/hia_core/__init__.py',
    'src/hia_core/codex_protocol.py',
    'src/hia_core/embedding_contract.py',
    'src/hia_core/houdini_contract.py',
    'src/hia_core/path_policy.py'
)
$releaseDirectoryAllowlist = @(
    '.agents/skills/',
    'contracts/codex-app-server/0.144.3/',
    'knowledge/community-tutorials/',
    'knowledge/sidefx-official/',
    'schemas/codex-app-server/0.144.3/',
    'schemas/houdini-mcp/0.2.0/',
    'services/bridge/',
    'services/hia_mcp_v2/'
)
$releaseBuildInputFiles = @(
    'scripts/build-launcher.ps1',
    'scripts/build-release.ps1',
    'scripts/check-public-release.py',
    'launcher/HoudiniIntelligenceLauncher/App.xaml',
    'launcher/HoudiniIntelligenceLauncher/App.xaml.cs',
    'launcher/HoudiniIntelligenceLauncher/HoudiniIntelligenceLauncher.csproj',
    'launcher/HoudiniIntelligenceLauncher/ProjectRootLocator.cs'
)
$releaseSourceRequiredFiles = @(
    $releaseFileAllowlist + $releaseBuildInputFiles |
        Sort-Object -Unique
)
$releaseBuildSourcePrefixes = @(
    'launcher/HoudiniIntelligenceLauncher/',
    'scripts/launcher/'
)
$managedVenvDenyPattern = '(^|/)\.venv(/|$)'
$releaseDenyPatterns = @(
    '(^|/)\.runtime(/|$)',
    $managedVenvDenyPattern,
    '(^|/)\.git(/|$)',
    '(^|/)tests?(/|$)',
    '(^|/)assets(/|$)',
    '(^|/)__pycache__(/|$)',
    '(^|/)TEST-REPORT\.md$',
    '(^|/)P[0-9]-',
    '(^|/).*GATE.*\.md$',
    '(^|/)(auth|credentials|secrets?|token)\.json$',
    '(^|/)\.env($|\.)',
    '\.(hip|hiplc|hipnc|abc|fbx|usd|usda|usdc|exr|png|jpe?g|webp|gif|bmp|mp4|mov|log|zip)$',
    'steam-winter-sale'
)
$launcherFiles = @(
    'BigChickenLauncher.exe',
    'D3DCompiler_47_cor3.dll',
    'PenImc_cor3.dll',
    'PresentationNative_cor3.dll',
    'vcruntime140_cor3.dll',
    'wpfgfx_cor3.dll'
)
$dotnetNoticeFiles = @(
    'LICENSE.txt',
    'ThirdPartyNotices.txt'
)
$dotnetNoticeRoot = Join-Path $runtimeRoot 'toolchains\dotnet'

function Assert-UnderDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Parent
    )

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $fullParent = [System.IO.Path]::GetFullPath($Parent).TrimEnd('\')
    $prefix = $fullParent + '\'
    if (-not $fullPath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Generated release path escaped its project-local parent: $fullPath"
    }
    return $fullPath
}

function Test-ReleasePathAllowed {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    $normalized = $RelativePath.Replace('\', '/').TrimStart('/')
    # The user-visible managed environment is never a release input, even if a
    # future edit accidentally adds one of its files to the explicit allowlist.
    if ($normalized -match $managedVenvDenyPattern) { return $false }
    if ($releaseFileAllowlist -contains $normalized) { return $true }
    foreach ($pattern in $releaseDenyPatterns) {
        if ($normalized -match $pattern) { return $false }
    }
    foreach ($prefix in $releaseDirectoryAllowlist) {
        if ($normalized.StartsWith($prefix, [System.StringComparison]::Ordinal)) {
            return $true
        }
    }
    return $false
}

function Test-ReleaseOrdinaryFile {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $false
    }
    $item = Get-Item -LiteralPath $Path -Force
    return (
        $item -is [System.IO.FileInfo] -and
        (([int]$item.Attributes -band
            [int][System.IO.FileAttributes]::ReparsePoint) -eq 0)
    )
}

function Invoke-ReleaseSourcePreflight {
    $releasePython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    foreach ($path in @(
        (Join-Path $projectRoot '.venv'),
        (Join-Path $projectRoot '.venv\Scripts')
    )) {
        if (-not (Test-Path -LiteralPath $path -PathType Container)) {
            throw (
                'Release preflight requires the canonical project .venv. ' +
                'Run scripts\hia-knowledge.ps1 environment-install first.'
            )
        }
        $item = Get-Item -LiteralPath $path -Force
        if (
            $item -isnot [System.IO.DirectoryInfo] -or
            (([int]$item.Attributes -band
                [int][System.IO.FileAttributes]::ReparsePoint) -ne 0)
        ) {
            throw "Release preflight refused a reparse-point .venv path: $path"
        }
    }
    if (-not (Test-ReleaseOrdinaryFile -Path $releasePython)) {
        throw (
            'Release preflight requires .venv\Scripts\python.exe. ' +
            'Run scripts\hia-knowledge.ps1 environment-install first.'
        )
    }

    $trackedFiles = @(& git -C $projectRoot ls-files --)
    if ($LASTEXITCODE -ne 0) {
        throw 'git ls-files failed during release source preflight.'
    }
    $tracked = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    foreach ($relativePath in $trackedFiles) {
        [void]$tracked.Add(
            ([string]$relativePath).Replace('\', '/').TrimStart('/')
        )
    }
    foreach ($relativePath in $releaseSourceRequiredFiles) {
        $normalized = ([string]$relativePath).Replace('\', '/').TrimStart('/')
        if (
            [System.IO.Path]::IsPathRooted($normalized) -or
            @($normalized.Split('/')) -contains '..'
        ) {
            throw "Release source requirement is not project-relative: $normalized"
        }
        $source = [System.IO.Path]::GetFullPath(
            (Join-Path $projectRoot $normalized)
        )
        if (-not (Test-ReleaseOrdinaryFile -Path $source)) {
            throw "Required release source is missing or not ordinary: $normalized"
        }
        if (-not $tracked.Contains($normalized)) {
            throw "Required release source is not tracked by git: $normalized"
        }
    }

    $untrackedFiles = @(
        & git -C $projectRoot ls-files --others --exclude-standard --
    )
    if ($LASTEXITCODE -ne 0) {
        throw 'git ls-files --others failed during release source preflight.'
    }
    $untrackedReleaseInputs = [System.Collections.Generic.List[string]]::new()
    foreach ($relativePath in $untrackedFiles) {
        $normalized = ([string]$relativePath).Replace('\', '/').TrimStart('/')
        $isBuildInput = $false
        foreach ($prefix in $releaseBuildSourcePrefixes) {
            if ($normalized.StartsWith(
                $prefix,
                [System.StringComparison]::Ordinal
            )) {
                $isBuildInput = $true
                break
            }
        }
        if (
            (Test-ReleasePathAllowed -RelativePath $normalized) -or
            $isBuildInput
        ) {
            $untrackedReleaseInputs.Add($normalized)
        }
    }
    if ($untrackedReleaseInputs.Count -gt 0) {
        throw (
            'Untracked release source would be omitted or embedded only in ' +
            'the built launcher: ' +
            (@($untrackedReleaseInputs | Sort-Object) -join ', ')
        )
    }

    $checkerOutput = @(
        & $releasePython -I -B $publicReleaseChecker --source-tree $projectRoot
    )
    if ($LASTEXITCODE -ne 0) {
        throw (
            'Release source preflight rejected the built-in knowledge pack ' +
            "with code $LASTEXITCODE."
        )
    }
    return $releasePython
}

function Copy-ReleaseFile {
    param([Parameter(Mandatory = $true)][string]$RelativePath)

    $normalized = $RelativePath.Replace('\', '/').TrimStart('/')
    if (-not (Test-ReleasePathAllowed -RelativePath $normalized)) {
        throw "Release input is outside the explicit allowlist: $normalized"
    }
    $source = [System.IO.Path]::GetFullPath((Join-Path $projectRoot $normalized))
    $projectPrefix = $projectRoot.TrimEnd('\') + '\'
    if (-not $source.StartsWith($projectPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Release source escaped the project root: $source"
    }
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Required release file is missing: $normalized"
    }
    $sourceItem = Get-Item -LiteralPath $source -Force
    if (([int]$sourceItem.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Release input is a reparse point: $normalized"
    }
    $destination = Join-Path $packageRoot $normalized
    [System.IO.Directory]::CreateDirectory((Split-Path -Parent $destination)) | Out-Null
    [System.IO.File]::Copy($source, $destination, $false)
}

function Remove-GeneratedStage {
    param([Parameter(Mandatory = $true)][string]$Path)

    $safePath = Assert-UnderDirectory -Path $Path -Parent $workRoot
    if (-not (Test-Path -LiteralPath $safePath)) { return }
    $item = Get-Item -LiteralPath $safePath -Force
    if (([int]$item.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to clean a reparse-point release staging directory: $safePath"
    }
    [System.IO.Directory]::Delete($safePath, $true)
}

if (-not (Test-Path -LiteralPath $publicReleaseChecker -PathType Leaf)) {
    throw "Public release checker is missing: $publicReleaseChecker"
}
$releasePython = Invoke-ReleaseSourcePreflight
if ($PreflightOnly) {
    Write-Output (
        '[release-preflight] tracked release sources, build inputs, ' +
        'project .venv, and built-in knowledge passed.'
    )
    exit 0
}

foreach ($directory in @($runtimeRoot, $releaseRoot, $workRoot)) {
    [System.IO.Directory]::CreateDirectory($directory) | Out-Null
}
if (Test-Path -LiteralPath $archivePath) {
    throw "Release archive already exists and was not overwritten: $archivePath"
}
if (Test-Path -LiteralPath $checksumsPath) {
    throw "Release checksum file already exists and was not overwritten: $checksumsPath"
}

# Remove only the six known generated launcher outputs so this invocation cannot
# silently package a stale EXE. No directory is recursively cleared here.
foreach ($launcherFile in $launcherFiles) {
    $generatedPath = Assert-UnderDirectory -Path (Join-Path $launcherDist $launcherFile) -Parent $launcherDist
    if (Test-Path -LiteralPath $generatedPath -PathType Container) {
        throw "Expected launcher output is unexpectedly a directory: $generatedPath"
    }
    if (Test-Path -LiteralPath $generatedPath -PathType Leaf) {
        [System.IO.File]::Delete($generatedPath)
    }
}

$buildArguments = @{ OutputDirectory = $launcherDist }
if ($InstallLocalSdk) { $buildArguments.InstallLocalSdk = $true }
& $launcherBuildScript @buildArguments
if ($LASTEXITCODE -ne 0) {
    throw "Launcher build exited with code $LASTEXITCODE."
}
foreach ($launcherFile in $launcherFiles) {
    $generatedPath = Join-Path $launcherDist $launcherFile
    if (-not (Test-Path -LiteralPath $generatedPath -PathType Leaf)) {
        throw "Fresh launcher output is missing: $generatedPath"
    }
}
foreach ($noticeFile in $dotnetNoticeFiles) {
    $noticePath = Join-Path $dotnetNoticeRoot $noticeFile
    if (-not (Test-Path -LiteralPath $noticePath -PathType Leaf)) {
        throw "The exact .NET SDK notice file is unavailable: $noticePath. Rerun with -InstallLocalSdk."
    }
}

[System.IO.Directory]::CreateDirectory($packageRoot) | Out-Null
try {
    $trackedFiles = @(& git -C $projectRoot ls-files --)
    if ($LASTEXITCODE -ne 0) { throw 'git ls-files failed while assembling the release.' }
    $candidateFiles = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    foreach ($relativePath in $trackedFiles + $releaseFileAllowlist) {
        $normalized = ([string]$relativePath).Replace('\', '/').TrimStart('/')
        if (Test-ReleasePathAllowed -RelativePath $normalized) {
            $candidateFiles.Add($normalized) | Out-Null
        }
    }
    foreach ($relativePath in @($candidateFiles) | Sort-Object) {
        Copy-ReleaseFile -RelativePath $relativePath
    }

    foreach ($noticeFile in $dotnetNoticeFiles) {
        $source = Join-Path $dotnetNoticeRoot $noticeFile
        $destination = Join-Path $packageRoot "licenses\dotnet\$noticeFile"
        [System.IO.Directory]::CreateDirectory((Split-Path -Parent $destination)) | Out-Null
        [System.IO.File]::Copy($source, $destination, $false)
    }

    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archiveStream = [System.IO.File]::Open(
        $archivePath,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::None
    )
    try {
        $archive = [System.IO.Compression.ZipArchive]::new(
            $archiveStream,
            [System.IO.Compression.ZipArchiveMode]::Create,
            $false
        )
        try {
            $stagePrefixLength = $stageRoot.TrimEnd('\').Length + 1
            foreach ($filePath in [System.IO.Directory]::GetFiles(
                $stageRoot,
                '*',
                [System.IO.SearchOption]::AllDirectories
            ) | Sort-Object) {
                $entryName = $filePath.Substring($stagePrefixLength).Replace('\', '/')
                [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                    $archive,
                    $filePath,
                    $entryName,
                    [System.IO.Compression.CompressionLevel]::Optimal
                ) | Out-Null
            }
        } finally {
            $archive.Dispose()
        }
    } finally {
        $archiveStream.Dispose()
    }

    & $releasePython -I -B $publicReleaseChecker `
        $archivePath `
        --forbid-text $projectRoot
    if ($LASTEXITCODE -ne 0) {
        if (Test-Path -LiteralPath $archivePath -PathType Leaf) {
            [System.IO.File]::Delete($archivePath)
        }
        throw "Public release checker rejected the archive with code $LASTEXITCODE."
    }

    $archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    [System.IO.File]::WriteAllText(
        $checksumsPath,
        "$archiveHash *$([System.IO.Path]::GetFileName($archivePath))`r`n",
        [System.Text.Encoding]::ASCII
    )
} finally {
    Remove-GeneratedStage -Path $stageRoot
}

Write-Output "[release] archive: $archivePath"
Write-Output "[release] sha256: $archiveHash"
Write-Output "[release] checksums: $checksumsPath"
Write-Output '[release] Only the project-owned launcher artwork was included; runtime state, user data, tests, HIP files, renders, credentials, and historical Gate reports were not packaged.'
