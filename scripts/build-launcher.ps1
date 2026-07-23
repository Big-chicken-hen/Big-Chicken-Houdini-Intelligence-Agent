[CmdletBinding()]
param(
    [switch]$InstallLocalSdk,
    [AllowEmptyString()][string]$OutputDirectory = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..')).TrimEnd('\')
$runtimeRoot = Join-Path $projectRoot '.runtime'
$toolchainRoot = Join-Path $runtimeRoot 'toolchains\dotnet'
$downloadRoot = Join-Path $runtimeRoot 'downloads'
$cacheRoot = Join-Path $runtimeRoot 'cache\dotnet'
$temporaryRoot = Join-Path $runtimeRoot 'tmp\dotnet-launcher-build'
$buildRoot = Join-Path $runtimeRoot 'build\launcher'
$binRoot = Join-Path $buildRoot 'bin'
$objRoot = Join-Path $buildRoot 'obj'
$distRoot = if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    Join-Path $runtimeRoot 'dist\launcher'
} else {
    [System.IO.Path]::GetFullPath($OutputDirectory)
}
$runtimePrefix = $runtimeRoot.TrimEnd('\') + '\'
if (-not $distRoot.StartsWith(
    $runtimePrefix,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Launcher output must stay under the project runtime directory: $distRoot"
}
$projectFile = Join-Path $projectRoot 'launcher\HoudiniIntelligenceLauncher\HoudiniIntelligenceLauncher.csproj'
$localDotnet = Join-Path $toolchainRoot 'dotnet.exe'

foreach ($directory in @(
    $runtimeRoot,
    $toolchainRoot,
    $downloadRoot,
    $cacheRoot,
    $temporaryRoot,
    $binRoot,
    $objRoot,
    $distRoot
)) {
    [System.IO.Directory]::CreateDirectory($directory) | Out-Null
}

$env:DOTNET_CLI_HOME = Join-Path $cacheRoot 'cli-home'
$env:NUGET_PACKAGES = Join-Path $cacheRoot 'nuget-packages'
$env:NUGET_HTTP_CACHE_PATH = Join-Path $cacheRoot 'nuget-http'
$env:NUGET_PLUGINS_CACHE_PATH = Join-Path $cacheRoot 'nuget-plugins'
$env:DOTNET_SKIP_FIRST_TIME_EXPERIENCE = '1'
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
$env:DOTNET_NOLOGO = '1'
$env:DOTNET_MULTILEVEL_LOOKUP = '0'
$env:TEMP = $temporaryRoot
$env:TMP = $temporaryRoot
foreach ($directory in @(
    $env:DOTNET_CLI_HOME,
    $env:NUGET_PACKAGES,
    $env:NUGET_HTTP_CACHE_PATH,
    $env:NUGET_PLUGINS_CACHE_PATH,
    $env:TEMP
)) {
    [System.IO.Directory]::CreateDirectory($directory) | Out-Null
}

if (-not (Test-Path -LiteralPath $localDotnet -PathType Leaf) -and $InstallLocalSdk) {
    $metadataUri = 'https://dotnetcli.blob.core.windows.net/dotnet/release-metadata/8.0/releases.json'
    $metadataPath = Join-Path $downloadRoot 'dotnet-8-releases.json'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    if (-not (Test-Path -LiteralPath $metadataPath -PathType Leaf)) {
        Invoke-WebRequest -UseBasicParsing -Uri $metadataUri -OutFile $metadataPath
    }

    $metadata = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json
    $latestSdk = [string]$metadata.'latest-sdk'
    $release = @($metadata.releases | Where-Object { $_.sdk.version -eq $latestSdk })[0]
    $sdkFile = @($release.sdk.files | Where-Object {
        $_.rid -eq 'win-x64' -and $_.name -eq 'dotnet-sdk-win-x64.zip'
    })[0]
    if ($null -eq $sdkFile -or $sdkFile.url -notlike 'https://builds.dotnet.microsoft.com/*') {
        throw 'The official .NET 8 metadata did not contain a Microsoft win-x64 SDK archive.'
    }

    $archivePath = Join-Path $downloadRoot "dotnet-sdk-$latestSdk-win-x64.zip"
    $archiveReady = $false
    if (Test-Path -LiteralPath $archivePath -PathType Leaf) {
        $existingHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA512).Hash.ToLowerInvariant()
        $archiveReady = $existingHash -eq ([string]$sdkFile.hash).ToLowerInvariant()
    }
    if (-not $archiveReady) {
        $curl = Get-Command -Name 'curl.exe' -ErrorAction SilentlyContinue
        if ($null -ne $curl) {
            $curlArguments = @(
                '--fail',
                '--location',
                '--retry', '3',
                '--show-error',
                '--output', $archivePath
            )
            if ((Test-Path -LiteralPath $archivePath -PathType Leaf) -and
                (Get-Item -LiteralPath $archivePath).Length -gt 0) {
                $curlArguments += @('--continue-at', '-')
            }
            $curlArguments += [string]$sdkFile.url
            & $curl.Source @curlArguments
            if ($LASTEXITCODE -ne 0) { throw "curl.exe exited with code $LASTEXITCODE." }
        } elseif (-not (Test-Path -LiteralPath $archivePath -PathType Leaf)) {
            Invoke-WebRequest -UseBasicParsing -Uri $sdkFile.url -OutFile $archivePath
        } else {
            throw "The partial SDK archive requires curl.exe to resume safely: $archivePath"
        }
    }
    $archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA512).Hash.ToLowerInvariant()
    if ($archiveHash -ne ([string]$sdkFile.hash).ToLowerInvariant()) {
        throw "The downloaded .NET SDK archive failed the Microsoft SHA-512 check: $archivePath"
    }
    if (@(Get-ChildItem -LiteralPath $toolchainRoot -Force).Count -ne 0) {
        throw "The project-local .NET directory is non-empty but dotnet.exe is missing: $toolchainRoot"
    }
    Expand-Archive -LiteralPath $archivePath -DestinationPath $toolchainRoot
}

$dotnetExe = ''
if (Test-Path -LiteralPath $localDotnet -PathType Leaf) {
    $dotnetExe = $localDotnet
} else {
    $systemDotnet = Get-Command -Name 'dotnet.exe' -ErrorAction SilentlyContinue
    if ($null -ne $systemDotnet) {
        $sdkList = @(& $systemDotnet.Source --list-sdks)
        if (@($sdkList | Where-Object { $_ -match '^8\.' }).Count -gt 0) {
            $dotnetExe = [string]$systemDotnet.Source
        }
    }
}
if (-not $dotnetExe) {
    throw 'A .NET 8 SDK is required. Rerun with -InstallLocalSdk to use the Microsoft project-local installer.'
}

$env:DOTNET_ROOT = Split-Path -Parent $dotnetExe
$sdkVersion = (& $dotnetExe --version).Trim()
if ($LASTEXITCODE -ne 0 -or $sdkVersion -notmatch '^8\.') {
    throw "The selected dotnet does not expose a .NET 8 SDK: $dotnetExe"
}

$pathSeparator = [System.IO.Path]::DirectorySeparatorChar
$publishArguments = @(
    'publish',
    $projectFile,
    '--configuration', 'Release',
    '--runtime', 'win-x64',
    '--self-contained', 'true',
    '--output', $distRoot,
    "-p:BaseOutputPath=$binRoot$pathSeparator",
    "-p:BaseIntermediateOutputPath=$objRoot$pathSeparator",
    "-p:MSBuildProjectExtensionsPath=$objRoot$pathSeparator",
    '-p:PublishSingleFile=true',
    '-p:IncludeNativeLibrariesForSelfExtract=false',
    '-p:PublishTrimmed=false',
    '-p:PublishReadyToRun=false'
)

Write-Output "[launcher] SDK: $sdkVersion"
Write-Output "[launcher] dotnet: $dotnetExe"
Write-Output "[launcher] publish: $distRoot"
& $dotnetExe @publishArguments
if ($LASTEXITCODE -ne 0) { throw "dotnet publish exited with code $LASTEXITCODE." }

$executable = Join-Path $distRoot 'BigChickenLauncher.exe'
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "Published launcher executable is missing: $executable"
}

$smokeProcess = Start-Process `
    -FilePath $executable `
    -ArgumentList '--smoke-test' `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -Wait `
    -PassThru
if ($smokeProcess.ExitCode -ne 0) {
    throw "Launcher smoke test exited with code $($smokeProcess.ExitCode)."
}

$executableInfo = Get-Item -LiteralPath $executable
Write-Output "[launcher] EXE: $($executableInfo.FullName)"
Write-Output "[launcher] bytes: $($executableInfo.Length)"
Write-Output '[launcher] smoke-test: passed (Houdini was not started)'
