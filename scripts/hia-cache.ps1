[CmdletBinding()]
param(
    [AllowEmptyString()][string]$Action = 'list',
    [AllowEmptyCollection()][string[]]$Category = @(),
    [AllowEmptyString()][string]$SnapshotHash = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$script:HiaCacheProtocol = 'hia-cache-json/1'
$script:HiaCacheSeparators = [char[]]@(
    [System.IO.Path]::DirectorySeparatorChar,
    [System.IO.Path]::AltDirectorySeparatorChar
)
$script:HiaCacheComparer = [System.StringComparer]::OrdinalIgnoreCase

function Get-HiaCacheNormalizedPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $pathRoot = [System.IO.Path]::GetPathRoot($fullPath)
    if ([string]::IsNullOrWhiteSpace($pathRoot)) {
        throw 'A cache path is not absolute.'
    }
    if ($fullPath.Length -gt $pathRoot.Length) {
        return $fullPath.TrimEnd($script:HiaCacheSeparators)
    }
    return $fullPath
}

function Test-HiaCacheReparsePoint {
    param([Parameter(Mandatory = $true)]$Item)

    return (
        ([int]$Item.Attributes -band
            [int][System.IO.FileAttributes]::ReparsePoint) -ne 0
    )
}

function Assert-HiaCacheDirectoryChain {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [switch]$AllowMissing
    )

    $candidate = Get-HiaCacheNormalizedPath -Path $Path
    $pathRoot = [System.IO.Path]::GetPathRoot($candidate)
    if (
        $candidate.Length -lt $pathRoot.Length -or
        -not $script:HiaCacheComparer.Equals(
            $candidate.Substring(0, $pathRoot.Length),
            $pathRoot
        )
    ) {
        throw 'A cache path has an invalid filesystem root.'
    }

    $current = Get-HiaCacheNormalizedPath -Path $pathRoot
    $rootItem = Get-Item -LiteralPath $current -Force -ErrorAction Stop
    if (
        $rootItem -isnot [System.IO.DirectoryInfo] -or
        (Test-HiaCacheReparsePoint -Item $rootItem) -or
        -not $script:HiaCacheComparer.Equals(
            (Get-HiaCacheNormalizedPath -Path $rootItem.FullName),
            $current
        )
    ) {
        throw "Cache path chain contains a reparse point or invalid directory: $current"
    }

    $remaining = $candidate.Substring($pathRoot.Length).Trim(
        $script:HiaCacheSeparators
    )
    if (-not [string]::IsNullOrWhiteSpace($remaining)) {
        foreach ($part in @($remaining -split '[\\/]')) {
            if (
                [string]::IsNullOrWhiteSpace($part) -or
                $part -eq '.' -or
                $part -eq '..' -or
                [System.IO.Path]::GetFileName($part) -ne $part
            ) {
                throw 'Cache path chain contains an invalid segment.'
            }
            $expectedParent = $current
            $current = Get-HiaCacheNormalizedPath -Path (
                Join-Path $expectedParent $part
            )
            if (
                -not $script:HiaCacheComparer.Equals(
                    (Get-HiaCacheNormalizedPath -Path (
                        [System.IO.Path]::GetDirectoryName($current)
                    )),
                    $expectedParent
                )
            ) {
                throw 'Cache path chain escaped its expected parent.'
            }

            $item = Get-Item `
                -LiteralPath $current `
                -Force `
                -ErrorAction SilentlyContinue
            if ($null -eq $item) {
                if ($AllowMissing) {
                    return [pscustomobject]@{
                        path = $candidate
                        exists = $false
                        missing_at = $current
                    }
                }
                throw "Required cache directory is unavailable: $current"
            }
            if (
                $item -isnot [System.IO.DirectoryInfo] -or
                (Test-HiaCacheReparsePoint -Item $item) -or
                -not $script:HiaCacheComparer.Equals(
                    (Get-HiaCacheNormalizedPath -Path $item.FullName),
                    $current
                )
            ) {
                throw "Cache path chain contains a reparse point or invalid directory: $current"
            }
        }
    }

    return [pscustomobject]@{
        path = $candidate
        exists = $true
        missing_at = ''
    }
}

function Assert-HiaCacheOrdinaryFile {
    param([Parameter(Mandatory = $true)][string]$Path)

    $candidate = Get-HiaCacheNormalizedPath -Path $Path
    $parent = [System.IO.Path]::GetDirectoryName($candidate)
    [void](Assert-HiaCacheDirectoryChain -Path $parent)
    $item = Get-Item -LiteralPath $candidate -Force -ErrorAction Stop
    if (
        $item -isnot [System.IO.FileInfo] -or
        (Test-HiaCacheReparsePoint -Item $item) -or
        -not $script:HiaCacheComparer.Equals(
            (Get-HiaCacheNormalizedPath -Path $item.FullName),
            $candidate
        ) -or
        -not $script:HiaCacheComparer.Equals(
            (Get-HiaCacheNormalizedPath -Path $item.DirectoryName),
            (Get-HiaCacheNormalizedPath -Path $parent)
        )
    ) {
        throw "Cache command file must be an ordinary file: $candidate"
    }
    return $candidate
}

function Join-HiaCacheFixedPath {
    param(
        [Parameter(Mandatory = $true)][string]$BasePath,
        [Parameter(Mandatory = $true)][string[]]$Segments
    )

    $current = Get-HiaCacheNormalizedPath -Path $BasePath
    foreach ($segment in $Segments) {
        if (
            [string]::IsNullOrWhiteSpace($segment) -or
            $segment -eq '.' -or
            $segment -eq '..' -or
            [System.IO.Path]::IsPathRooted($segment) -or
            [System.IO.Path]::GetFileName($segment) -ne $segment
        ) {
            throw 'A fixed cache category contains an invalid path segment.'
        }
        $parent = $current
        $current = Get-HiaCacheNormalizedPath -Path (
            Join-Path $parent $segment
        )
        if (
            -not $script:HiaCacheComparer.Equals(
                (Get-HiaCacheNormalizedPath -Path (
                    [System.IO.Path]::GetDirectoryName($current)
                )),
                $parent
            )
        ) {
            throw 'A fixed cache category escaped its expected parent.'
        }
    }
    return $current
}

function Get-HiaCacheExecutionContext {
    $scriptPath = Get-HiaCacheNormalizedPath -Path $PSCommandPath
    $scriptsRoot = Get-HiaCacheNormalizedPath -Path (
        [System.IO.Path]::GetDirectoryName($scriptPath)
    )
    $projectRoot = Get-HiaCacheNormalizedPath -Path (
        Join-Path $scriptsRoot '..'
    )
    $filesystemRoot = Get-HiaCacheNormalizedPath -Path (
        [System.IO.Path]::GetPathRoot($projectRoot)
    )
    if ($script:HiaCacheComparer.Equals($projectRoot, $filesystemRoot)) {
        throw 'The cache command cannot operate at a filesystem root.'
    }

    $expectedScriptsRoot = Join-HiaCacheFixedPath `
        -BasePath $projectRoot `
        -Segments @('scripts')
    $expectedScript = Join-HiaCacheFixedPath `
        -BasePath $expectedScriptsRoot `
        -Segments @('hia-cache.ps1')
    if (
        -not $script:HiaCacheComparer.Equals($scriptsRoot, $expectedScriptsRoot) -or
        -not $script:HiaCacheComparer.Equals($scriptPath, $expectedScript)
    ) {
        throw 'The cache command location does not identify a project root.'
    }

    [void](Assert-HiaCacheDirectoryChain -Path $projectRoot)
    [void](Assert-HiaCacheDirectoryChain -Path $scriptsRoot)
    [void](Assert-HiaCacheOrdinaryFile -Path $scriptPath)

    $runtimeRoot = Join-HiaCacheFixedPath `
        -BasePath $projectRoot `
        -Segments @('.runtime')
    $cacheRoot = Join-HiaCacheFixedPath `
        -BasePath $runtimeRoot `
        -Segments @('cache')
    return [pscustomobject]@{
        project_root = $projectRoot
        runtime_root = $runtimeRoot
        cache_root = $cacheRoot
        script_path = $scriptPath
    }
}

function Get-HiaCacheCategoryDefinitions {
    return @(
        [pscustomobject]@{
            id = 'screenshots'
            label = 'Screenshots'
            segments = @('.runtime', 'cache', 'screenshots')
            excluded_child = ''
            embedding = $false
        },
        [pscustomobject]@{
            id = 'previews'
            label = 'Previews'
            segments = @('.runtime', 'cache', 'previews')
            excluded_child = ''
            embedding = $false
        },
        [pscustomobject]@{
            id = 'tmp'
            label = 'Temporary files'
            segments = @('.runtime', 'cache', 'tmp')
            excluded_child = ''
            embedding = $false
        },
        [pscustomobject]@{
            id = 'embedding-runtime'
            label = 'Embedding install/runtime cache'
            segments = @('.runtime', 'cache', 'embedding')
            excluded_child = 'huggingface'
            embedding = $true
        },
        [pscustomobject]@{
            id = 'embedding-downloads'
            label = 'Embedding download cache'
            segments = @(
                '.runtime',
                'cache',
                'embedding',
                'huggingface'
            )
            excluded_child = ''
            embedding = $true
        },
        [pscustomobject]@{
            id = 'dotnet'
            label = '.NET build cache'
            segments = @('.runtime', 'cache', 'dotnet')
            excluded_child = ''
            embedding = $false
        }
    )
}

function Resolve-HiaCacheCategoryIds {
    param(
        [AllowEmptyCollection()][string[]]$Requested,
        [switch]$RequireSelection
    )

    $requestedIds = [System.Collections.Generic.List[string]]::new()
    foreach ($rawValue in @($Requested)) {
        foreach ($value in @(([string]$rawValue) -split ',')) {
            $trimmed = $value.Trim().ToLowerInvariant()
            if (-not [string]::IsNullOrWhiteSpace($trimmed)) {
                $requestedIds.Add($trimmed)
            }
        }
    }
    if ($requestedIds.Count -eq 0) {
        if ($RequireSelection) {
            throw 'Clear requires at least one explicit cache category.'
        }
        return @(
            Get-HiaCacheCategoryDefinitions |
                ForEach-Object { [string]$_.id }
        )
    }

    $known = @{}
    foreach ($definition in @(Get-HiaCacheCategoryDefinitions)) {
        $known[[string]$definition.id] = $true
    }
    foreach ($requestedId in $requestedIds) {
        if (-not $known.ContainsKey($requestedId)) {
            throw "Unsupported cache category: $requestedId"
        }
    }

    $selected = [System.Collections.Generic.List[string]]::new()
    foreach ($definition in @(Get-HiaCacheCategoryDefinitions)) {
        $id = [string]$definition.id
        foreach ($requestedId in $requestedIds) {
            if (
                $script:HiaCacheComparer.Equals($id, $requestedId) -and
                -not $selected.Contains($id)
            ) {
                $selected.Add($id)
            }
        }
    }
    return @($selected.ToArray())
}

function Get-HiaEmbeddingInstallLockState {
    param([Parameter(Mandatory = $true)]$Context)

    $launcherRoot = Join-HiaCacheFixedPath `
        -BasePath $Context.project_root `
        -Segments @('.runtime', 'launcher')
    try {
        $launcherState = Assert-HiaCacheDirectoryChain `
            -Path $launcherRoot `
            -AllowMissing
        if (-not $launcherState.exists) {
            return [pscustomobject]@{
                active = $false
                blocked = $false
                reason = ''
            }
        }

        $lockPath = Join-HiaCacheFixedPath `
            -BasePath $launcherRoot `
            -Segments @('embedding-install.lock')
        $lockItem = Get-Item `
            -LiteralPath $lockPath `
            -Force `
            -ErrorAction SilentlyContinue
        if ($null -eq $lockItem) {
            return [pscustomobject]@{
                active = $false
                blocked = $false
                reason = ''
            }
        }
        [void](Assert-HiaCacheOrdinaryFile -Path $lockPath)

        $stream = $null
        try {
            $stream = [System.IO.FileStream]::new(
                $lockPath,
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::ReadWrite,
                [System.IO.FileShare]::Read
            )
            return [pscustomobject]@{
                active = $false
                blocked = $false
                reason = ''
            }
        } catch [System.IO.IOException] {
            return [pscustomobject]@{
                active = $true
                blocked = $false
                reason = 'Embedding installer lock is active.'
            }
        } finally {
            if ($null -ne $stream) {
                $stream.Dispose()
            }
        }
    } catch {
        return [pscustomobject]@{
            active = $false
            blocked = $true
            reason = [string]$_.Exception.Message
        }
    }
}

function New-HiaCacheEntryRecord {
    param(
        [Parameter(Mandatory = $true)][string]$Relative,
        [Parameter(Mandatory = $true)][string]$Kind,
        [long]$Bytes,
        [long]$CreationTicks,
        [long]$LastWriteTicks,
        [int]$Attributes,
        [int]$Depth
    )

    return [pscustomobject]@{
        relative = $Relative
        kind = $Kind
        bytes = $Bytes
        creation_utc_ticks = $CreationTicks
        last_write_utc_ticks = $LastWriteTicks
        attributes = $Attributes
        depth = $Depth
    }
}

function Get-HiaCacheTreeSnapshot {
    param(
        [Parameter(Mandatory = $true)][string]$TargetPath,
        [AllowEmptyString()][string]$ExcludedChild = ''
    )

    $chain = Assert-HiaCacheDirectoryChain `
        -Path $TargetPath `
        -AllowMissing
    if (-not $chain.exists) {
        return [pscustomobject]@{
            exists = $false
            blocked = $false
            block_reasons = @()
            bytes = [long]0
            file_count = 0
            directory_count = 0
            root_creation_utc_ticks = [long]0
            root_attributes = 0
            entries = @()
        }
    }

    $target = Get-Item -LiteralPath $TargetPath -Force -ErrorAction Stop
    if (
        $target -isnot [System.IO.DirectoryInfo] -or
        (Test-HiaCacheReparsePoint -Item $target)
    ) {
        throw "Cache category root is not an ordinary directory: $TargetPath"
    }

    $entries = [System.Collections.ArrayList]::new()
    $blockReasons = [System.Collections.ArrayList]::new()
    $stack = [System.Collections.Generic.Stack[object]]::new()
    $stack.Push([pscustomobject]@{
        directory = $target
        relative = ''
        depth = 0
    })
    [long]$bytes = 0
    [int]$fileCount = 0
    [int]$directoryCount = 0

    while ($stack.Count -gt 0) {
        $node = $stack.Pop()
        $directory = $node.directory
        $directoryPath = Get-HiaCacheNormalizedPath -Path $directory.FullName
        try {
            $children = @($directory.GetFileSystemInfos())
        } catch {
            [void]$blockReasons.Add(
                "Unable to enumerate cache directory: $($node.relative)"
            )
            continue
        }

        foreach ($child in $children) {
            if (
                [string]::IsNullOrWhiteSpace([string]$node.relative) -and
                -not [string]::IsNullOrWhiteSpace($ExcludedChild) -and
                $script:HiaCacheComparer.Equals(
                    [string]$child.Name,
                    $ExcludedChild
                )
            ) {
                continue
            }

            $childPath = Get-HiaCacheNormalizedPath -Path $child.FullName
            $expectedChild = Get-HiaCacheNormalizedPath -Path (
                Join-Path $directoryPath $child.Name
            )
            $actualParent = Get-HiaCacheNormalizedPath -Path (
                [System.IO.Path]::GetDirectoryName($childPath)
            )
            $relative = if (
                [string]::IsNullOrWhiteSpace([string]$node.relative)
            ) {
                [string]$child.Name
            } else {
                ([string]$node.relative) + '/' + [string]$child.Name
            }
            $depth = [int]$node.depth + 1

            if (
                -not $script:HiaCacheComparer.Equals(
                    $childPath,
                    $expectedChild
                ) -or
                -not $script:HiaCacheComparer.Equals(
                    $actualParent,
                    $directoryPath
                )
            ) {
                [void]$blockReasons.Add(
                    "Cache entry escaped its expected parent: $relative"
                )
                continue
            }

            if (Test-HiaCacheReparsePoint -Item $child) {
                [void]$entries.Add((New-HiaCacheEntryRecord `
                    -Relative $relative `
                    -Kind 'reparse' `
                    -Bytes 0 `
                    -CreationTicks $child.CreationTimeUtc.Ticks `
                    -LastWriteTicks $child.LastWriteTimeUtc.Ticks `
                    -Attributes ([int]$child.Attributes) `
                    -Depth $depth))
                [void]$blockReasons.Add(
                    "Cache category contains a reparse point: $relative"
                )
                continue
            }

            if ($child -is [System.IO.FileInfo]) {
                [long]$length = $child.Length
                $bytes += $length
                $fileCount += 1
                [void]$entries.Add((New-HiaCacheEntryRecord `
                    -Relative $relative `
                    -Kind 'file' `
                    -Bytes $length `
                    -CreationTicks $child.CreationTimeUtc.Ticks `
                    -LastWriteTicks $child.LastWriteTimeUtc.Ticks `
                    -Attributes ([int]$child.Attributes) `
                    -Depth $depth))
                if (
                    [System.IO.Path]::GetExtension(
                        [string]$child.Name
                    ).ToLowerInvariant() -in @('.hip', '.hiplc', '.hipnc')
                ) {
                    [void]$blockReasons.Add(
                        "Cache category contains a Houdini scene file: $relative"
                    )
                }
                continue
            }

            if ($child -is [System.IO.DirectoryInfo]) {
                $directoryCount += 1
                [void]$entries.Add((New-HiaCacheEntryRecord `
                    -Relative $relative `
                    -Kind 'directory' `
                    -Bytes 0 `
                    -CreationTicks $child.CreationTimeUtc.Ticks `
                    -LastWriteTicks $child.LastWriteTimeUtc.Ticks `
                    -Attributes ([int]$child.Attributes) `
                    -Depth $depth))
                $stack.Push([pscustomobject]@{
                    directory = $child
                    relative = $relative
                    depth = $depth
                })
                continue
            }

            [void]$entries.Add((New-HiaCacheEntryRecord `
                -Relative $relative `
                -Kind 'unknown' `
                -Bytes 0 `
                -CreationTicks $child.CreationTimeUtc.Ticks `
                -LastWriteTicks $child.LastWriteTimeUtc.Ticks `
                -Attributes ([int]$child.Attributes) `
                -Depth $depth))
            [void]$blockReasons.Add(
                "Cache category contains an unsupported entry: $relative"
            )
        }
    }

    $orderedEntries = @(
        $entries |
            Sort-Object `
                @{ Expression = { ([string]$_.relative).ToLowerInvariant() } },
                @{ Expression = { [string]$_.relative } }
    )
    return [pscustomobject]@{
        exists = $true
        blocked = ($blockReasons.Count -gt 0)
        block_reasons = @($blockReasons.ToArray())
        bytes = $bytes
        file_count = $fileCount
        directory_count = $directoryCount
        root_creation_utc_ticks = $target.CreationTimeUtc.Ticks
        root_attributes = [int]$target.Attributes
        entries = $orderedEntries
    }
}

function Get-HiaCacheCategorySnapshots {
    param(
        [Parameter(Mandatory = $true)]$Context,
        [Parameter(Mandatory = $true)][string[]]$CategoryIds
    )

    $embeddingLock = Get-HiaEmbeddingInstallLockState -Context $Context
    $snapshots = [System.Collections.ArrayList]::new()
    foreach ($definition in @(Get-HiaCacheCategoryDefinitions)) {
        if ($definition.id -notin $CategoryIds) {
            continue
        }
        $targetPath = Join-HiaCacheFixedPath `
            -BasePath $Context.project_root `
            -Segments @($definition.segments)
        $excludedPath = ''
        if (-not [string]::IsNullOrWhiteSpace($definition.excluded_child)) {
            $excludedPath = Join-HiaCacheFixedPath `
                -BasePath $targetPath `
                -Segments @([string]$definition.excluded_child)
        }

        try {
            $tree = Get-HiaCacheTreeSnapshot `
                -TargetPath $targetPath `
                -ExcludedChild ([string]$definition.excluded_child)
            $reasons = [System.Collections.ArrayList]::new()
            foreach ($reason in @($tree.block_reasons)) {
                [void]$reasons.Add([string]$reason)
            }
            if ([bool]$definition.embedding) {
                if ($embeddingLock.active) {
                    [void]$reasons.Add([string]$embeddingLock.reason)
                }
                if ($embeddingLock.blocked) {
                    [void]$reasons.Add(
                        "Embedding installer lock could not be validated: $($embeddingLock.reason)"
                    )
                }
            }
            [void]$snapshots.Add([pscustomobject]@{
                id = [string]$definition.id
                label = [string]$definition.label
                target_path = $targetPath
                excluded_path = $excludedPath
                exists = [bool]$tree.exists
                blocked = ($reasons.Count -gt 0)
                block_reasons = @($reasons.ToArray())
                bytes = [long]$tree.bytes
                file_count = [int]$tree.file_count
                directory_count = [int]$tree.directory_count
                root_creation_utc_ticks = [long]$tree.root_creation_utc_ticks
                root_attributes = [int]$tree.root_attributes
                entries = @($tree.entries)
            })
        } catch {
            [void]$snapshots.Add([pscustomobject]@{
                id = [string]$definition.id
                label = [string]$definition.label
                target_path = $targetPath
                excluded_path = $excludedPath
                exists = (Test-Path -LiteralPath $targetPath)
                blocked = $true
                block_reasons = @([string]$_.Exception.Message)
                bytes = [long]0
                file_count = 0
                directory_count = 0
                root_creation_utc_ticks = [long]0
                root_attributes = 0
                entries = @()
            })
        }
    }
    return @($snapshots.ToArray())
}

function Add-HiaCacheHashField {
    param(
        [Parameter(Mandatory = $true)]
        [System.Text.StringBuilder]$Builder,
        [AllowNull()]$Value
    )

    $text = if ($null -eq $Value) { '' } else { [string]$Value }
    [void]$Builder.Append(
        $text.Length.ToString(
            [System.Globalization.CultureInfo]::InvariantCulture
        )
    )
    [void]$Builder.Append(':')
    [void]$Builder.Append($text)
    [void]$Builder.Append(';')
}

function Get-HiaCacheSnapshotHash {
    param([Parameter(Mandatory = $true)][object[]]$Snapshots)

    $builder = [System.Text.StringBuilder]::new()
    Add-HiaCacheHashField -Builder $builder -Value 'hia-cache-snapshot-v1'
    foreach ($snapshot in @($Snapshots)) {
        foreach ($value in @(
            $snapshot.id,
            $snapshot.target_path,
            [int][bool]$snapshot.exists,
            [int][bool]$snapshot.blocked,
            [long]$snapshot.bytes,
            [int]$snapshot.file_count,
            [int]$snapshot.directory_count,
            [long]$snapshot.root_creation_utc_ticks,
            [int]$snapshot.root_attributes
        )) {
            Add-HiaCacheHashField -Builder $builder -Value $value
        }
        foreach ($reason in @($snapshot.block_reasons | Sort-Object)) {
            Add-HiaCacheHashField -Builder $builder -Value $reason
        }
        foreach ($entry in @($snapshot.entries)) {
            foreach ($value in @(
                $entry.relative,
                $entry.kind,
                [long]$entry.bytes,
                [long]$entry.creation_utc_ticks,
                [long]$entry.last_write_utc_ticks,
                [int]$entry.attributes,
                [int]$entry.depth
            )) {
                Add-HiaCacheHashField -Builder $builder -Value $value
            }
        }
    }

    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes(
            $builder.ToString()
        )
        $digest = $algorithm.ComputeHash($bytes)
        return (
            [System.BitConverter]::ToString($digest)
        ).Replace('-', '').ToLowerInvariant()
    } finally {
        $algorithm.Dispose()
    }
}

function ConvertTo-HiaCachePublicCategory {
    param([Parameter(Mandatory = $true)]$Snapshot)

    return [ordered]@{
        id = [string]$Snapshot.id
        label = [string]$Snapshot.label
        target_path = [string]$Snapshot.target_path
        excluded_path = [string]$Snapshot.excluded_path
        exists = [bool]$Snapshot.exists
        blocked = [bool]$Snapshot.blocked
        block_reasons = @($Snapshot.block_reasons)
        bytes = [long]$Snapshot.bytes
        file_count = [int]$Snapshot.file_count
        directory_count = [int]$Snapshot.directory_count
        category_root_preserved = $true
    }
}

function New-HiaCacheReadPayload {
    param(
        [Parameter(Mandatory = $true)]$Context,
        [Parameter(Mandatory = $true)][string]$ActionName,
        [Parameter(Mandatory = $true)][string[]]$CategoryIds,
        [Parameter(Mandatory = $true)][object[]]$Snapshots
    )

    [long]$totalBytes = 0
    $blocked = [System.Collections.Generic.List[string]]::new()
    $publicCategories = @(
        foreach ($snapshot in @($Snapshots)) {
            $totalBytes += [long]$snapshot.bytes
            if ([bool]$snapshot.blocked) {
                $blocked.Add([string]$snapshot.id)
            }
            ConvertTo-HiaCachePublicCategory -Snapshot $snapshot
        }
    )
    return [ordered]@{
        protocol = $script:HiaCacheProtocol
        ok = $true
        action = $ActionName
        project_root = [string]$Context.project_root
        cache_root = [string]$Context.cache_root
        selected_categories = @($CategoryIds)
        categories = @($publicCategories)
        blocked_categories = @($blocked.ToArray())
        total_bytes = $totalBytes
        snapshot_hash = Get-HiaCacheSnapshotHash -Snapshots $Snapshots
        safety = [ordered]@{
            cache_root_files_preserved = $true
            excluded_cache_directories = @('renders', 'research')
            excluded_runtime_directories = @(
                'knowledge',
                'models',
                'toolchains',
                'attachments',
                'launcher-sessions',
                'diagnostics'
            )
        }
    }
}

function Get-HiaCacheEntryPath {
    param(
        [Parameter(Mandatory = $true)][string]$TargetPath,
        [Parameter(Mandatory = $true)][string]$RelativePath
    )

    $current = Get-HiaCacheNormalizedPath -Path $TargetPath
    foreach ($segment in @($RelativePath -split '/')) {
        $current = Join-HiaCacheFixedPath `
            -BasePath $current `
            -Segments @($segment)
    }
    return $current
}

function Assert-HiaCacheDeletionPlan {
    param([Parameter(Mandatory = $true)][object[]]$Snapshots)

    foreach ($snapshot in @($Snapshots)) {
        if (-not [bool]$snapshot.exists) {
            continue
        }
        [void](Assert-HiaCacheDirectoryChain -Path $snapshot.target_path)
        foreach ($entry in @($snapshot.entries)) {
            if ([string]$entry.kind -notin @('file', 'directory')) {
                throw "Blocked cache entry reached deletion plan: $($entry.relative)"
            }
            $path = Get-HiaCacheEntryPath `
                -TargetPath $snapshot.target_path `
                -RelativePath $entry.relative
            $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
            if (
                (Test-HiaCacheReparsePoint -Item $item) -or
                -not $script:HiaCacheComparer.Equals(
                    (Get-HiaCacheNormalizedPath -Path $item.FullName),
                    $path
                )
            ) {
                throw "Cache deletion plan contains an invalid entry: $($entry.relative)"
            }
            if ([string]$entry.kind -eq 'file') {
                if (
                    $item -isnot [System.IO.FileInfo] -or
                    [long]$item.Length -ne [long]$entry.bytes -or
                    $item.CreationTimeUtc.Ticks -ne
                        [long]$entry.creation_utc_ticks -or
                    $item.LastWriteTimeUtc.Ticks -ne
                        [long]$entry.last_write_utc_ticks -or
                    [int]$item.Attributes -ne [int]$entry.attributes
                ) {
                    throw "Cache file changed after preview: $($entry.relative)"
                }
            } elseif (
                $item -isnot [System.IO.DirectoryInfo] -or
                $item.CreationTimeUtc.Ticks -ne
                    [long]$entry.creation_utc_ticks -or
                $item.LastWriteTimeUtc.Ticks -ne
                    [long]$entry.last_write_utc_ticks -or
                [int]$item.Attributes -ne [int]$entry.attributes
            ) {
                throw "Cache directory changed after preview: $($entry.relative)"
            }
        }
    }
}

function Invoke-HiaCacheDeletion {
    param([Parameter(Mandatory = $true)]$Snapshot)

    [long]$deletedBytes = 0
    [int]$deletedFiles = 0
    [int]$deletedDirectories = 0
    $failures = [System.Collections.ArrayList]::new()

    $files = @(
        $Snapshot.entries |
            Where-Object { [string]$_.kind -eq 'file' }
    )
    foreach ($entry in $files) {
        $path = Get-HiaCacheEntryPath `
            -TargetPath $Snapshot.target_path `
            -RelativePath $entry.relative
        try {
            $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
            if (
                $item -isnot [System.IO.FileInfo] -or
                (Test-HiaCacheReparsePoint -Item $item) -or
                -not $script:HiaCacheComparer.Equals(
                    (Get-HiaCacheNormalizedPath -Path $item.FullName),
                    $path
                ) -or
                [long]$item.Length -ne [long]$entry.bytes -or
                $item.CreationTimeUtc.Ticks -ne
                    [long]$entry.creation_utc_ticks -or
                $item.LastWriteTimeUtc.Ticks -ne
                    [long]$entry.last_write_utc_ticks -or
                [int]$item.Attributes -ne [int]$entry.attributes
            ) {
                throw 'Entry is no longer an ordinary cache file.'
            }
            [System.IO.File]::Delete($path)
            $deletedBytes += [long]$entry.bytes
            $deletedFiles += 1
        } catch {
            [void]$failures.Add([ordered]@{
                relative_path = [string]$entry.relative
                message = [string]$_.Exception.Message
            })
        }
    }

    $directories = @(
        $Snapshot.entries |
            Where-Object { [string]$_.kind -eq 'directory' } |
            Sort-Object `
                @{ Expression = { [int]$_.depth }; Descending = $true },
                @{ Expression = { ([string]$_.relative).Length }; Descending = $true },
                @{ Expression = { [string]$_.relative }; Descending = $true }
    )
    foreach ($entry in $directories) {
        $path = Get-HiaCacheEntryPath `
            -TargetPath $Snapshot.target_path `
            -RelativePath $entry.relative
        try {
            $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
            if (
                $item -isnot [System.IO.DirectoryInfo] -or
                (Test-HiaCacheReparsePoint -Item $item) -or
                -not $script:HiaCacheComparer.Equals(
                    (Get-HiaCacheNormalizedPath -Path $item.FullName),
                    $path
                )
            ) {
                throw 'Entry is no longer an ordinary cache directory.'
            }
            [System.IO.Directory]::Delete($path, $false)
            $deletedDirectories += 1
        } catch {
            [void]$failures.Add([ordered]@{
                relative_path = [string]$entry.relative
                message = [string]$_.Exception.Message
            })
        }
    }

    return [ordered]@{
        id = [string]$Snapshot.id
        target_path = [string]$Snapshot.target_path
        estimated_bytes = [long]$Snapshot.bytes
        deleted_bytes = $deletedBytes
        deleted_files = $deletedFiles
        deleted_directories = $deletedDirectories
        failed_count = $failures.Count
        failures = @($failures.ToArray())
        category_root_preserved = $true
    }
}

function Write-HiaCacheJson {
    param([Parameter(Mandatory = $true)]$Payload)

    Write-Output (
        $Payload |
            ConvertTo-Json -Depth 12 -Compress
    )
}

$context = $null
try {
    $normalizedAction = $Action.Trim().ToLowerInvariant()
    if ($normalizedAction -notin @('list', 'clear')) {
        throw "Unsupported cache action: $Action"
    }
    $context = Get-HiaCacheExecutionContext
    $categoryIds = @(
        Resolve-HiaCacheCategoryIds `
            -Requested $Category `
            -RequireSelection:($normalizedAction -eq 'clear')
    )

    if ($normalizedAction -eq 'list') {
        $snapshots = @(
            Get-HiaCacheCategorySnapshots `
                -Context $context `
                -CategoryIds $categoryIds
        )
        Write-HiaCacheJson -Payload (
            New-HiaCacheReadPayload `
                -Context $context `
                -ActionName 'list' `
                -CategoryIds $categoryIds `
                -Snapshots $snapshots
        )
        return
    }

    if ($SnapshotHash -notmatch '\A[0-9A-Fa-f]{64}\z') {
        throw 'Clear requires a valid snapshot hash from list.'
    }

    $firstSnapshots = @(
        Get-HiaCacheCategorySnapshots `
            -Context $context `
            -CategoryIds $categoryIds
    )
    $firstPayload = New-HiaCacheReadPayload `
        -Context $context `
        -ActionName 'clear' `
        -CategoryIds $categoryIds `
        -Snapshots $firstSnapshots
    if (@($firstPayload.blocked_categories).Count -gt 0) {
        $firstPayload.ok = $false
        $firstPayload.error = [ordered]@{
            code = 'CACHE_CATEGORY_BLOCKED'
            message = 'At least one selected cache category is blocked; nothing was deleted.'
        }
        Write-HiaCacheJson -Payload $firstPayload
        exit 3
    }
    if (
        -not $script:HiaCacheComparer.Equals(
            [string]$firstPayload.snapshot_hash,
            $SnapshotHash
        )
    ) {
        $firstPayload.ok = $false
        $firstPayload.error = [ordered]@{
            code = 'CACHE_SNAPSHOT_STALE'
            message = 'The selected cache contents changed after preview; nothing was deleted.'
        }
        Write-HiaCacheJson -Payload $firstPayload
        exit 4
    }

    $verifiedSnapshots = @(
        Get-HiaCacheCategorySnapshots `
            -Context $context `
            -CategoryIds $categoryIds
    )
    $verifiedPayload = New-HiaCacheReadPayload `
        -Context $context `
        -ActionName 'clear' `
        -CategoryIds $categoryIds `
        -Snapshots $verifiedSnapshots
    if (@($verifiedPayload.blocked_categories).Count -gt 0) {
        $verifiedPayload.ok = $false
        $verifiedPayload.error = [ordered]@{
            code = 'CACHE_CATEGORY_BLOCKED'
            message = 'A selected cache category became blocked; nothing was deleted.'
        }
        Write-HiaCacheJson -Payload $verifiedPayload
        exit 3
    }
    if (
        -not $script:HiaCacheComparer.Equals(
            [string]$verifiedPayload.snapshot_hash,
            $SnapshotHash
        )
    ) {
        $verifiedPayload.ok = $false
        $verifiedPayload.error = [ordered]@{
            code = 'CACHE_SNAPSHOT_STALE'
            message = 'The selected cache contents changed during verification; nothing was deleted.'
        }
        Write-HiaCacheJson -Payload $verifiedPayload
        exit 4
    }

    Assert-HiaCacheDeletionPlan -Snapshots $verifiedSnapshots
    $finalLock = Get-HiaEmbeddingInstallLockState -Context $context
    if (
        @($verifiedSnapshots | Where-Object {
            $_.id -in @('embedding-runtime', 'embedding-downloads')
        }).Count -gt 0 -and
        ($finalLock.active -or $finalLock.blocked)
    ) {
        $verifiedPayload.ok = $false
        $verifiedPayload.error = [ordered]@{
            code = 'CACHE_CATEGORY_BLOCKED'
            message = 'Embedding installation started during verification; nothing was deleted.'
        }
        Write-HiaCacheJson -Payload $verifiedPayload
        exit 3
    }

    $results = @(
        foreach ($snapshot in $verifiedSnapshots) {
            Invoke-HiaCacheDeletion -Snapshot $snapshot
        }
    )
    [long]$freedBytes = 0
    [int]$failedCount = 0
    foreach ($result in $results) {
        $freedBytes += [long]$result.deleted_bytes
        $failedCount += [int]$result.failed_count
    }
    Write-HiaCacheJson -Payload ([ordered]@{
        protocol = $script:HiaCacheProtocol
        ok = ($failedCount -eq 0)
        action = 'clear'
        project_root = [string]$context.project_root
        cache_root = [string]$context.cache_root
        selected_categories = @($categoryIds)
        preview_snapshot_hash = $SnapshotHash.ToLowerInvariant()
        results = @($results)
        freed_bytes = $freedBytes
        failed_count = $failedCount
        safety = $verifiedPayload.safety
    })
    if ($failedCount -gt 0) {
        exit 5
    }
} catch {
    $payload = [ordered]@{
        protocol = $script:HiaCacheProtocol
        ok = $false
        action = $Action
        error = [ordered]@{
            code = 'CACHE_COMMAND_ERROR'
            message = [string]$_.Exception.Message
        }
    }
    if ($null -ne $context) {
        $payload.project_root = [string]$context.project_root
        $payload.cache_root = [string]$context.cache_root
    }
    Write-HiaCacheJson -Payload $payload
    exit 2
}
