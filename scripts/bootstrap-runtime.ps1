[CmdletBinding()]
param(
    [switch]$Repair
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\')
$runtimeRoot = Join-Path $projectRoot '.runtime'
$supportedVersion = '0.144.3'
$contractRoot = Join-Path $projectRoot 'contracts\codex-app-server'
if (-not (Test-Path -LiteralPath $contractRoot -PathType Container)) {
    throw "The packaged Codex app-server contract directory is missing: $contractRoot"
}
$contractRootItem = Get-Item -LiteralPath $contractRoot -Force
if (([int]$contractRootItem.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "Refusing to resolve the Codex version through a reparse-point contract directory: $contractRoot"
}
$contractVersions = @(Get-ChildItem -LiteralPath $contractRoot -Directory -Force -ErrorAction Stop)
if ($contractVersions.Count -ne 1) {
    throw "Expected exactly one packaged Codex app-server contract version under: $contractRoot"
}
$contractVersion = $contractVersions[0]
if (([int]$contractVersion.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "Refusing to resolve the Codex version through a reparse-point contract: $($contractVersion.FullName)"
}
$version = $contractVersion.Name
if (-not [System.StringComparer]::Ordinal.Equals($version, $supportedVersion)) {
    throw "No pinned Codex runtime metadata is available for packaged contract version: $version"
}
$archiveUri = 'https://github.com/openai/codex/releases/download/rust-v0.144.3/codex-x86_64-pc-windows-msvc.exe.zip'
$archiveSha256 = '5490114D8684B30F91E6E6F7B1238B2544FA3B957E42C9836AA959E8F563C01F'
$signerThumbprint = '6D5ECE62E405BFD318FEB942FCBB37E5FD6C4AB3'
$downloadRoot = Join-Path $runtimeRoot "downloads\codex\$version"
$archivePath = Join-Path $downloadRoot 'codex-x86_64-pc-windows-msvc.exe.zip'
$partialPath = $archivePath + '.partial'
$installRoot = Join-Path $runtimeRoot "toolchains\codex\$version"
$temporaryParent = Join-Path $runtimeRoot 'tmp\codex-bootstrap'
$temporaryRoot = Join-Path $temporaryParent ([guid]::NewGuid().ToString('N'))

$expectedExecutables = [ordered]@{
    'codex.exe' = 'E5DCC9F9B08102C58596AF85345F689A69FD53A87D8D408BDC0FCDAF99FCF6E3'
    'codex-command-runner.exe' = '9806824E11AACFC2FC41C5AEC9413CB64F755CAFD982F49170FA4F659500444A'
    'codex-windows-sandbox-setup.exe' = '7EFA768607D8E3F3FBF8F018C7A3454695FAE718984345125BAB387A863F089F'
}

function Assert-HiaProjectLocalPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $prefix = $runtimeRoot.TrimEnd('\') + '\'
    if (
        -not [System.StringComparer]::OrdinalIgnoreCase.Equals($fullPath.TrimEnd('\'), $runtimeRoot) -and
        -not $fullPath.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "Runtime bootstrap path escaped the project .runtime directory: $fullPath"
    }
    return $fullPath
}

function Assert-HiaNoReparsePathChain {
    param([Parameter(Mandatory = $true)][string]$Path)

    $fullPath = Assert-HiaProjectLocalPath -Path $Path
    $cursor = $fullPath.TrimEnd('\')
    $pathChain = [System.Collections.Generic.List[string]]::new()
    while ($true) {
        $pathChain.Add($cursor)
        if ([System.StringComparer]::OrdinalIgnoreCase.Equals($cursor, $runtimeRoot.TrimEnd('\'))) {
            break
        }
        $parent = [System.IO.Path]::GetDirectoryName($cursor)
        if ([string]::IsNullOrWhiteSpace($parent)) {
            throw "Runtime bootstrap path did not resolve beneath the project .runtime directory: $fullPath"
        }
        $cursor = $parent.TrimEnd('\')
    }
    $pathChain.Reverse()
    foreach ($candidatePath in $pathChain) {
        $item = Get-Item -LiteralPath $candidatePath -Force -ErrorAction SilentlyContinue
        if (
            $null -ne $item -and
            ([int]$item.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw "Refusing to use a reparse-point runtime bootstrap path: $candidatePath"
        }
    }
    return $fullPath
}

function Assert-HiaManagedCodexRepairTarget {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]
        [ValidateSet('InstallDirectory', 'ArchiveFile', 'PartialFile')]
        [string]$Kind
    )

    $expectedPath = switch ($Kind) {
        'InstallDirectory' { $installRoot }
        'ArchiveFile' { $archivePath }
        'PartialFile' { $partialPath }
    }
    $safePath = Assert-HiaNoReparsePathChain -Path $Path
    if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($safePath, $expectedPath)) {
        throw "Refusing to repair an unexpected Codex runtime target: $safePath"
    }
    $item = Get-Item -LiteralPath $safePath -Force -ErrorAction SilentlyContinue
    if ($null -ne $item) {
        $expectsDirectory = $Kind -eq 'InstallDirectory'
        if ($expectsDirectory -ne [bool]$item.PSIsContainer) {
            throw "Refusing to repair a Codex runtime target with an unexpected filesystem type: $safePath"
        }
    }
    return $safePath
}

function Remove-HiaManagedCodexRepairTarget {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)]
        [ValidateSet('InstallDirectory', 'ArchiveFile', 'PartialFile')]
        [string]$Kind
    )

    if (-not $Repair) {
        throw 'Managed Codex runtime targets may only be replaced when -Repair is explicitly supplied.'
    }
    $safePath = Assert-HiaManagedCodexRepairTarget -Path $Path -Kind $Kind
    if (-not (Test-Path -LiteralPath $safePath)) {
        return
    }
    if ($Kind -eq 'InstallDirectory') {
        [System.IO.Directory]::Delete($safePath, $true)
    } else {
        [System.IO.File]::Delete($safePath)
    }
}

function Assert-CodexExecutable {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ExpectedSha256
    )

    $resolvedPath = Assert-HiaNoReparsePathChain -Path $Path
    if (-not (Test-Path -LiteralPath $resolvedPath -PathType Leaf)) {
        throw "Required Codex executable is missing: $resolvedPath"
    }
    $hash = (Get-FileHash -LiteralPath $resolvedPath -Algorithm SHA256).Hash
    if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($hash, $ExpectedSha256)) {
        throw "Codex executable SHA-256 mismatch: $resolvedPath"
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $resolvedPath
    if (
        $signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid -or
        $null -eq $signature.SignerCertificate -or
        -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
            $signature.SignerCertificate.Thumbprint,
            $signerThumbprint
        )
    ) {
        throw "Codex executable does not have the expected valid OpenAI Authenticode signature: $resolvedPath"
    }
}

function Test-InstalledCodex {
    foreach ($entry in $expectedExecutables.GetEnumerator()) {
        $candidate = Join-Path $installRoot $entry.Key
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { return $false }
        Assert-CodexExecutable -Path $candidate -ExpectedSha256 $entry.Value
    }
    return $true
}

foreach ($directory in @($runtimeRoot, $downloadRoot, $temporaryParent)) {
    $safeDirectory = Assert-HiaNoReparsePathChain -Path $directory
    [System.IO.Directory]::CreateDirectory($safeDirectory) | Out-Null
    Assert-HiaNoReparsePathChain -Path $safeDirectory | Out-Null
}

$installedCodexValid = $false
try {
    $installedCodexValid = Test-InstalledCodex
} catch {
    if (-not $Repair) {
        throw
    }
}

if ($installedCodexValid) {
    Write-Output "[bootstrap] Codex $version is already verified: $(Join-Path $installRoot 'codex.exe')"
} else {
    $safeInstallRoot = Assert-HiaManagedCodexRepairTarget -Path $installRoot -Kind InstallDirectory
    if ((Test-Path -LiteralPath $safeInstallRoot) -and -not $Repair) {
        throw "The project-local Codex directory is incomplete or invalid; it was not modified: $installRoot"
    }

    $safeArchivePath = Assert-HiaManagedCodexRepairTarget -Path $archivePath -Kind ArchiveFile
    if (Test-Path -LiteralPath $safeArchivePath -PathType Leaf) {
        $existingArchiveHash = (Get-FileHash -LiteralPath $safeArchivePath -Algorithm SHA256).Hash
        if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($existingArchiveHash, $archiveSha256)) {
            if (-not $Repair) {
                throw "The existing project-local Codex archive has an unexpected SHA-256 and was not overwritten: $archivePath"
            }
            Remove-HiaManagedCodexRepairTarget -Path $safeArchivePath -Kind ArchiveFile
        }
    }
    if (-not (Test-Path -LiteralPath $safeArchivePath -PathType Leaf)) {
        $safePartialPath = Assert-HiaManagedCodexRepairTarget -Path $partialPath -Kind PartialFile
        if (Test-Path -LiteralPath $safePartialPath) {
            if (-not $Repair) {
                throw "A previous partial Codex download exists; inspect or remove this exact file before retrying: $partialPath"
            }
            Remove-HiaManagedCodexRepairTarget -Path $safePartialPath -Kind PartialFile
        }
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Write-Output "[bootstrap] Downloading official Codex $version..."
        Assert-HiaManagedCodexRepairTarget -Path $safePartialPath -Kind PartialFile | Out-Null
        Invoke-WebRequest -UseBasicParsing -Uri $archiveUri -OutFile $safePartialPath
        Assert-HiaManagedCodexRepairTarget -Path $safePartialPath -Kind PartialFile | Out-Null
        $partialHash = (Get-FileHash -LiteralPath $safePartialPath -Algorithm SHA256).Hash
        if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($partialHash, $archiveSha256)) {
            throw "The downloaded Codex archive failed the pinned SHA-256 check: $partialPath"
        }
        Assert-HiaManagedCodexRepairTarget -Path $safeArchivePath -Kind ArchiveFile | Out-Null
        [System.IO.File]::Move($safePartialPath, $safeArchivePath)
        Assert-HiaManagedCodexRepairTarget -Path $safeArchivePath -Kind ArchiveFile | Out-Null
    }

    Assert-HiaNoReparsePathChain -Path $temporaryRoot | Out-Null
    [System.IO.Directory]::CreateDirectory($temporaryRoot) | Out-Null
    Assert-HiaNoReparsePathChain -Path $temporaryRoot | Out-Null
    try {
        Expand-Archive -LiteralPath $safeArchivePath -DestinationPath $temporaryRoot
        foreach ($entry in $expectedExecutables.GetEnumerator()) {
            $archiveName = $entry.Key
            if ($archiveName -eq 'codex.exe') {
                $archiveName = 'codex-x86_64-pc-windows-msvc.exe'
            }
            $source = Join-Path $temporaryRoot $archiveName
            Assert-CodexExecutable -Path $source -ExpectedSha256 $entry.Value
        }

        $preparedRoot = Join-Path $temporaryRoot 'verified'
        [System.IO.Directory]::CreateDirectory($preparedRoot) | Out-Null
        foreach ($entry in $expectedExecutables.GetEnumerator()) {
            $archiveName = $entry.Key
            if ($archiveName -eq 'codex.exe') {
                $archiveName = 'codex-x86_64-pc-windows-msvc.exe'
            }
            $source = Join-Path $temporaryRoot $archiveName
            $destination = Join-Path $preparedRoot $entry.Key
            [System.IO.File]::Copy($source, $destination, $false)
            Assert-CodexExecutable -Path $destination -ExpectedSha256 $entry.Value
        }

        $currentInstallValid = $false
        try {
            $currentInstallValid = Test-InstalledCodex
        } catch {
            if (-not $Repair) {
                throw
            }
        }
        if (-not $currentInstallValid -and (Test-Path -LiteralPath $safeInstallRoot)) {
            Remove-HiaManagedCodexRepairTarget -Path $safeInstallRoot -Kind InstallDirectory
        }
        Assert-HiaManagedCodexRepairTarget -Path $safeInstallRoot -Kind InstallDirectory | Out-Null
        [System.IO.Directory]::CreateDirectory((Split-Path -Parent $installRoot)) | Out-Null
        if (-not $currentInstallValid) {
            Assert-HiaManagedCodexRepairTarget -Path $safeInstallRoot -Kind InstallDirectory | Out-Null
            [System.IO.Directory]::Move($preparedRoot, $installRoot)
            if (-not (Test-InstalledCodex)) {
                throw "The installed Codex runtime failed verification after replacement: $installRoot"
            }
        }
    } finally {
        $safeTemporaryRoot = Assert-HiaNoReparsePathChain -Path $temporaryRoot
        if (Test-Path -LiteralPath $safeTemporaryRoot) {
            $temporaryItem = Get-Item -LiteralPath $safeTemporaryRoot -Force
            if (([int]$temporaryItem.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Refusing to clean a reparse-point bootstrap directory: $safeTemporaryRoot"
            }
            if (-not $temporaryItem.PSIsContainer) {
                throw "Refusing to clean a bootstrap path with an unexpected filesystem type: $safeTemporaryRoot"
            }
            [System.IO.Directory]::Delete($safeTemporaryRoot, $true)
        }
    }
    Write-Output "[bootstrap] Installed and verified Codex ${version}: $(Join-Path $installRoot 'codex.exe')"
}

Write-Output (
    '[bootstrap] Bridge and local knowledge use the project-managed Python. ' +
    'Run scripts\hia-knowledge.ps1 environment-install or environment-repair; ' +
    'a global or PATH Python is not used by the normal setup.'
)
Write-Output '[bootstrap] No global PATH, registry, Houdini installation, or user configuration was changed.'
