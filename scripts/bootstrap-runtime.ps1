[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\')
$runtimeRoot = Join-Path $projectRoot '.runtime'
$version = '0.144.3'
$archiveUri = 'https://github.com/openai/codex/releases/download/rust-v0.144.3/codex-x86_64-pc-windows-msvc.exe.zip'
$archiveSha256 = '5490114D8684B30F91E6E6F7B1238B2544FA3B957E42C9836AA959E8F563C01F'
$signerThumbprint = '6D5ECE62E405BFD318FEB942FCBB37E5FD6C4AB3'
$downloadRoot = Join-Path $runtimeRoot "downloads\codex\$version"
$archivePath = Join-Path $downloadRoot 'codex-x86_64-pc-windows-msvc.exe.zip'
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

function Assert-CodexExecutable {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ExpectedSha256
    )

    $resolvedPath = Assert-HiaProjectLocalPath -Path $Path
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
    $safeDirectory = Assert-HiaProjectLocalPath -Path $directory
    [System.IO.Directory]::CreateDirectory($safeDirectory) | Out-Null
}

if (Test-InstalledCodex) {
    Write-Output "[bootstrap] Codex $version is already verified: $(Join-Path $installRoot 'codex.exe')"
} else {
    if (
        (Test-Path -LiteralPath $installRoot) -and
        @(Get-ChildItem -LiteralPath $installRoot -Force -ErrorAction Stop).Count -ne 0
    ) {
        throw "The project-local Codex directory is incomplete or invalid; it was not modified: $installRoot"
    }

    if (Test-Path -LiteralPath $archivePath -PathType Leaf) {
        $existingArchiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash
        if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($existingArchiveHash, $archiveSha256)) {
            throw "The existing project-local Codex archive has an unexpected SHA-256 and was not overwritten: $archivePath"
        }
    } else {
        $partialPath = Assert-HiaProjectLocalPath -Path ($archivePath + '.partial')
        if (Test-Path -LiteralPath $partialPath) {
            throw "A previous partial Codex download exists; inspect or remove this exact file before retrying: $partialPath"
        }
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Write-Output "[bootstrap] Downloading official Codex $version..."
        Invoke-WebRequest -UseBasicParsing -Uri $archiveUri -OutFile $partialPath
        $partialHash = (Get-FileHash -LiteralPath $partialPath -Algorithm SHA256).Hash
        if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($partialHash, $archiveSha256)) {
            throw "The downloaded Codex archive failed the pinned SHA-256 check: $partialPath"
        }
        [System.IO.File]::Move($partialPath, $archivePath)
    }

    [System.IO.Directory]::CreateDirectory($temporaryRoot) | Out-Null
    try {
        Expand-Archive -LiteralPath $archivePath -DestinationPath $temporaryRoot
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
        [System.IO.Directory]::CreateDirectory((Split-Path -Parent $installRoot)) | Out-Null
        [System.IO.Directory]::Move($preparedRoot, $installRoot)
    } finally {
        $safeTemporaryRoot = Assert-HiaProjectLocalPath -Path $temporaryRoot
        if (Test-Path -LiteralPath $safeTemporaryRoot) {
            $temporaryItem = Get-Item -LiteralPath $safeTemporaryRoot -Force
            if (([int]$temporaryItem.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Refusing to clean a reparse-point bootstrap directory: $safeTemporaryRoot"
            }
            [System.IO.Directory]::Delete($safeTemporaryRoot, $true)
        }
    }
    Write-Output "[bootstrap] Installed and verified Codex ${version}: $(Join-Path $installRoot 'codex.exe')"
}

$pythonCandidates = @(
    Get-Command -Name 'python.exe' -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue
)
$pythonMessage = 'Install Python 3.10+ yourself, then select python.exe as Bridge Python in the launcher.'
foreach ($pythonCandidate in $pythonCandidates) {
    try {
        $pythonVersion = (
            & $pythonCandidate -c 'import sys; print(sys.version_info.major, sys.version_info.minor, sep=chr(46))'
        ).Trim()
        if ($LASTEXITCODE -eq 0 -and $pythonVersion -match '^(3\.(?:1[0-9]|[2-9][0-9]))$') {
            $pythonMessage = "Bridge Python prerequisite found: $pythonCandidate (Python $pythonVersion)"
            break
        }
    } catch {
        continue
    }
}

Write-Output "[bootstrap] $pythonMessage"
Write-Output '[bootstrap] No global PATH, registry, Houdini installation, or user configuration was changed.'
