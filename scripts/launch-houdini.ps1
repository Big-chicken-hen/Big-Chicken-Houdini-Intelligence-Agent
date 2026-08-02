[CmdletBinding()]
param(
    [string]$BridgePython = '',
    [string]$HoudiniExe = '',
    [ValidateSet('hia_v2', 'fxhoudini')][string]$McpBackend = 'hia_v2',
    [AllowEmptyString()][string]$EmbeddingProfile = '',
    [ValidateSet('auto', 'cuda', 'cpu')][string]$EmbeddingDevice = 'auto'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$launcherCore = Join-Path $PSScriptRoot 'launcher\HiaLauncher.Core.psm1'
Import-Module -Force -DisableNameChecking $launcherCore

function Write-LauncherSessionManifest {
    param(
        [Parameter(Mandatory = $true)][string]$ManifestPath,
        [Parameter(Mandatory = $true)]$State
    )

    $safeState = [ordered]@{
        schema_version = 1
        session_id = $State.session_id
        state = $State.state
        selected_houdini = $State.selected_houdini
        hip_path = $State.hip_path
        started_at_utc = $State.started_at_utc
        ended_at_utc = $State.ended_at_utc
        process_exit_code = $State.process_exit_code
        launcher_process_id = $State.launcher_process_id
        houdini_process_id = $State.houdini_process_id
    }
    $json = ConvertTo-HiaRedactedJson -Value $safeState -Depth 4
    [System.IO.File]::WriteAllText(
        $ManifestPath,
        $json + [Environment]::NewLine,
        [System.Text.UTF8Encoding]::new($false)
    )
}

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

function Get-ExactOwnedBridgeProcess {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [Parameter(Mandatory = $true)][int]$LauncherProcessId,
        [Parameter(Mandatory = $true)][string]$BridgeExecutablePath
    )

    if ($ProcessId -le 0 -or $LauncherProcessId -le 0) {
        return $null
    }
    $matches = @(
        Get-CimInstance `
            -ClassName Win32_Process `
            -Filter "ProcessId = $ProcessId" `
            -ErrorAction SilentlyContinue
    )
    if ($matches.Count -ne 1) {
        return $null
    }
    $candidate = $matches[0]
    if (
        -not $candidate.ExecutablePath -or
        [int]$candidate.ProcessId -ne $ProcessId -or
        [int]$candidate.ParentProcessId -ne $LauncherProcessId -or
        -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
            [System.IO.Path]::GetFullPath([string]$candidate.ExecutablePath),
            [System.IO.Path]::GetFullPath($BridgeExecutablePath)
        )
    ) {
        return $null
    }
    return $candidate
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
        if ($null -ne $StartInfo.Environment) {
            $StartInfo.Environment[$entry.Key] = [string]$entry.Value
        } else {
            $StartInfo.EnvironmentVariables[$entry.Key] = [string]$entry.Value
        }
    }
}

function Remove-ChildEnvironment {
    param(
        [Parameter(Mandatory = $true)][System.Diagnostics.ProcessStartInfo]$StartInfo,
        [Parameter(Mandatory = $true)][string[]]$Names
    )

    foreach ($name in $Names) {
        if ($null -ne $StartInfo.Environment) {
            [void]$StartInfo.Environment.Remove($name)
        } else {
            [void]$StartInfo.EnvironmentVariables.Remove($name)
        }
    }
}

function Invoke-HiaKnowledgeBootstrapOnLaunch {
    param(
        [Parameter(Mandatory = $true)][string]$Python,
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string[]]$PythonPathEntries,
        [Parameter(Mandatory = $true)][hashtable]$EmbeddingEnvironment,
        [Parameter(Mandatory = $true)][string[]]$EmbeddingEnvironmentNames
    )

    $environment = @{
        'HIA_PROJECT_ROOT' = $Root
        'PYTHONDONTWRITEBYTECODE' = '1'
        'PYTHONIOENCODING' = 'utf-8'
        'PYTHONNOUSERSITE' = '1'
        'PYTHONUTF8' = '1'
        'PYTHONPATH' = ($PythonPathEntries -join [System.IO.Path]::PathSeparator)
    }
    foreach ($entry in $EmbeddingEnvironment.GetEnumerator()) {
        $environment[[string]$entry.Key] = [string]$entry.Value
    }
    $clearEnvironmentNames = @(
        $EmbeddingEnvironmentNames |
            Where-Object { -not $EmbeddingEnvironment.ContainsKey([string]$_) }
    )

    $invoke = {
        param([string]$Action, [int]$TimeoutSeconds)
        $arguments = @(
            '-B',
            '-m',
            'hia_mcp_runtime.knowledge_index_cli',
            '--project-root',
            $Root,
            '--format',
            'json',
            $Action
        )
        if ($Action -eq 'build') {
            $arguments += @('--batch-size', '32')
        }
        $result = Invoke-HiaProcess `
            -FilePath $Python `
            -Arguments $arguments `
            -TimeoutSeconds $TimeoutSeconds `
            -Environment $environment `
            -RemoveEnvironmentVariables $clearEnvironmentNames `
            -WorkingDirectory $Root
        if ($result.timed_out -eq $true -or $result.exit_code -ne 0) {
            throw "knowledge index $Action did not complete"
        }
        return ([string]$result.stdout).Trim() |
            ConvertFrom-Json -ErrorAction Stop
    }

    $bootstrap = & $invoke 'bootstrap' 180
    if (
        $bootstrap.installation.installed -eq $true -and
        [int]$bootstrap.index.pending_chunks -gt 0
    ) {
        [void](& $invoke 'build' 3600)
    }
}

function Get-EmbeddingLauncherContract {
    param(
        [Parameter(Mandatory = $true)][string]$Python,
        [Parameter(Mandatory = $true)][string]$Root
    )

    $contractPath = Assert-OrdinaryProjectPath `
        -Path (Join-Path $Root 'src\hia_core\embedding_contract.py') `
        -Root $Root
    $probeSource = @'
import json
import dataclasses
import runpy
import sys

module = runpy.run_path(sys.argv[1])
contract = module["launcher_contract"]()
layout = module["runtime_layout"](sys.argv[2])
registry = {
    key: dataclasses.asdict(value)
    for key, value in module["PROFILE_REGISTRY"].items()
}
print(
    json.dumps(
        {"contract": contract, "layout": layout, "registry": registry},
        separators=(",", ":"),
    )
)
'@
    $arguments = @('-B', '-c', $probeSource, $contractPath, $Root)
    $probeInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $probeInfo.FileName = $Python
    $probeInfo.Arguments = (@(
        foreach ($argument in $arguments) {
            ConvertTo-HiaProcessArgument -Value ([string]$argument)
        }
    ) -join ' ')
    $probeInfo.WorkingDirectory = $Root
    $probeInfo.UseShellExecute = $false
    $probeInfo.CreateNoWindow = $true
    $probeInfo.RedirectStandardOutput = $true
    $probeInfo.RedirectStandardError = $true
    if ($null -ne $probeInfo.Environment) {
        $probeInfo.Environment.Clear()
    } else {
        $probeInfo.EnvironmentVariables.Clear()
    }
    Set-ChildEnvironment -StartInfo $probeInfo -Values @{
        'SystemRoot' = [string]$env:SystemRoot
        'WINDIR' = [string]$env:WINDIR
        'PYTHONDONTWRITEBYTECODE' = '1'
        'PYTHONNOUSERSITE' = '1'
        'PYTHONPATH' = ''
        'HIA_PROJECT_ROOT' = $Root
    }

    $probe = [System.Diagnostics.Process]::new()
    $probe.StartInfo = $probeInfo
    try {
        if (-not $probe.Start()) {
            throw 'Embedding contract probe did not start.'
        }
        $stdoutTask = $probe.StandardOutput.ReadToEndAsync()
        $stderrTask = $probe.StandardError.ReadToEndAsync()
        if (-not $probe.WaitForExit(15000)) {
            $probe.Kill()
            [void]$probe.WaitForExit(5000)
            throw 'Embedding contract probe timed out.'
        }
        $stdout = $stdoutTask.Result
        [void]$stderrTask.Result
        if ($probe.ExitCode -ne 0 -or [string]::IsNullOrWhiteSpace($stdout)) {
            throw 'Embedding contract probe failed.'
        }
        if ([System.Text.Encoding]::UTF8.GetByteCount($stdout) -gt 1048576) {
            throw 'Embedding contract probe returned too much data.'
        }
        try {
            $payload = $stdout | ConvertFrom-Json -ErrorAction Stop
        } catch {
            throw 'Embedding contract probe returned invalid JSON.'
        }
    } finally {
        $probe.Dispose()
    }
    if (
        $null -eq $payload -or
        $null -eq $payload.contract -or
        $null -eq $payload.layout -or
        $null -eq $payload.registry -or
        [int]$payload.contract.contract_version -ne 1
    ) {
        throw 'Embedding launcher contract v1 is unavailable.'
    }
    $serializedProfiles = ConvertTo-Json `
        -InputObject $payload.contract.profiles `
        -Depth 8 `
        -Compress
    $serializedRegistry = ConvertTo-Json `
        -InputObject $payload.registry `
        -Depth 8 `
        -Compress
    if (-not [System.StringComparer]::Ordinal.Equals(
        $serializedProfiles,
        $serializedRegistry
    )) {
        throw 'Embedding profile registry disagrees with launcher_contract.'
    }
    return $payload
}

function Test-EmbeddingEnvironmentName {
    param([Parameter(Mandatory = $true)][string]$Name)

    return (
        $Name -match '^[A-Z][A-Z0-9_]+$' -and
        $Name.Length -le 128
    )
}

function Get-EmbeddingModelInstallation {
    param(
        [Parameter(Mandatory = $true)]$Contract,
        [Parameter(Mandatory = $true)]$Layout,
        [Parameter(Mandatory = $true)]$Profile,
        [Parameter(Mandatory = $true)][string]$Root
    )

    $missing = [pscustomobject]@{
        installed = $false
        model_directory = $null
        revision = 'local'
    }
    $relativeDirectory = [string]$Profile.model_directory
    if (
        [string]::IsNullOrWhiteSpace($relativeDirectory) -or
        [System.IO.Path]::IsPathRooted($relativeDirectory)
    ) {
        throw 'Embedding profile model_directory must be project-relative.'
    }
    try {
        $safeModelDirectory = Assert-OrdinaryProjectPath `
            -Path (Join-Path $Root $relativeDirectory) `
            -Root $Root `
            -AllowMissingLeaf
    } catch {
        return $missing
    }
    $layoutPaths = @(
        foreach ($layoutProperty in @($Layout.PSObject.Properties)) {
            $layoutValue = [string]$layoutProperty.Value
            if (-not [string]::IsNullOrWhiteSpace($layoutValue)) {
                [System.IO.Path]::GetFullPath($layoutValue)
            }
        }
    )
    if (-not @($layoutPaths | Where-Object {
        [System.StringComparer]::OrdinalIgnoreCase.Equals(
            $_,
            $safeModelDirectory
        )
    })) {
        throw 'Embedding profile model_directory disagrees with runtime_layout.'
    }
    if (-not (Test-Path -LiteralPath $safeModelDirectory -PathType Container)) {
        return $missing
    }
    try {
        $safeModelDirectory = Assert-OrdinaryProjectPath `
            -Path $safeModelDirectory `
            -Root $Root
    } catch {
        return $missing
    }
    $markerPath = Join-Path $safeModelDirectory '.hia-embedding-model.json'
    $configPath = Join-Path $safeModelDirectory 'config.json'
    foreach ($requiredFile in @($markerPath, $configPath)) {
        try {
            Assert-OrdinaryProjectPath `
                -Path $requiredFile `
                -Root $Root | Out-Null
        } catch {
            return $missing
        }
        $item = Get-Item -LiteralPath $requiredFile -Force -ErrorAction SilentlyContinue
        if (
            $item -isnot [System.IO.FileInfo] -or
            ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            return $missing
        }
    }
    $marker = Get-Item -LiteralPath $markerPath -Force
    if ($marker.Length -gt 65536) {
        return $missing
    }
    $ordinaryWeights = @(
        Get-ChildItem `
            -LiteralPath $safeModelDirectory `
            -Filter '*.safetensors' `
            -File `
            -Force `
            -ErrorAction SilentlyContinue |
        Where-Object {
            ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -eq 0
        }
    )
    if ($ordinaryWeights.Count -lt 1) {
        return $missing
    }
    try {
        $metadata = [System.IO.File]::ReadAllText($markerPath) |
            ConvertFrom-Json -ErrorAction Stop
    } catch {
        return $missing
    }
    $actualProperties = @($metadata.PSObject.Properties.Name)
    $requiredProperties = @(
        'contract_version',
        'profile_id',
        'model_id',
        'revision'
    )
    if (
        $actualProperties.Count -ne $requiredProperties.Count -or
        @($actualProperties | Where-Object { $_ -notin $requiredProperties }).Count -ne 0
    ) {
        return $missing
    }
    foreach ($propertyName in $requiredProperties) {
        if ($null -eq $metadata.PSObject.Properties[$propertyName]) {
            return $missing
        }
    }
    if (
        $metadata.contract_version -isnot [int] -and
        $metadata.contract_version -isnot [long]
    ) {
        return $missing
    }
    try {
        $markerContractVersion = [int]$metadata.contract_version
    } catch {
        return $missing
    }
    if (
        $markerContractVersion -ne [int]$Contract.contract_version -or
        -not [System.StringComparer]::Ordinal.Equals(
            [string]$metadata.profile_id,
            [string]$Profile.profile_id
        ) -or
        -not [System.StringComparer]::Ordinal.Equals(
            [string]$metadata.model_id,
            [string]$Profile.model_id
        )
    ) {
        return $missing
    }
    $revision = ([string]$metadata.revision).Trim()
    if (
        $revision -notmatch '^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$' -or
        $revision.Contains('..') -or
        $revision.Contains('//') -or
        $revision.EndsWith('/')
    ) {
        return $missing
    }
    return [pscustomobject]@{
        installed = $true
        model_directory = $safeModelDirectory
        revision = $revision
    }
}

function New-EmbeddingChildEnvironment {
    param(
        [Parameter(Mandatory = $true)]$Payload,
        [AllowEmptyString()][string]$RequestedProfile = '',
        [ValidateSet('auto', 'cuda', 'cpu')][string]$RequestedDevice = 'auto',
        [Parameter(Mandatory = $true)][string]$Root
    )

    $contract = $Payload.contract
    $profileProperties = @($contract.profiles.PSObject.Properties)
    if ($profileProperties.Count -ne 2) {
        throw 'Embedding launcher contract must expose exactly two profiles.'
    }
    $profileIdSet = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    foreach ($profileProperty in $profileProperties) {
        $profile = $profileProperty.Value
        if (
            -not $profileIdSet.Add([string]$profileProperty.Name) -or
            -not [System.StringComparer]::Ordinal.Equals(
                [string]$profileProperty.Name,
                [string]$profile.profile_id
            )
        ) {
            throw 'Embedding launcher contract contains an invalid profile identity.'
        }
    }
    $selection = if ($RequestedProfile -eq '') {
        [string]$contract.default_profile
    } else {
        $RequestedProfile
    }
    if (-not $profileIdSet.Contains($selection)) {
        throw [System.ArgumentException]::new(
            'EmbeddingProfile must exactly match one of the two contract profiles.'
        )
    }
    $selectedProfile = $contract.profiles.PSObject.Properties[$selection].Value
    $dimension = 0
    try {
        $dimension = [int]$selectedProfile.default_dimension
    } catch {
        throw 'The selected embedding profile default dimension is invalid.'
    }
    if (
        $dimension -lt [int]$selectedProfile.min_mrl_dimension -or
        $dimension -gt [int]$selectedProfile.max_dimension
    ) {
        throw 'The selected embedding profile default dimension is outside its contract range.'
    }

    $contractEnvironmentNameSet = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    foreach ($environmentProperty in @($contract.environment.PSObject.Properties)) {
        $name = [string]$environmentProperty.Value
        if (
            -not (Test-EmbeddingEnvironmentName -Name $name) -or
            -not $contractEnvironmentNameSet.Add($name)
        ) {
            throw 'Embedding launcher contract contains an invalid environment name.'
        }
    }
    $sharedEnvironmentFields = @('profile', 'python', 'dimension', 'device')
    $environmentNames = @{}
    foreach ($field in $sharedEnvironmentFields) {
        $property = $contract.environment.PSObject.Properties[$field]
        if ($null -eq $property) {
            throw "Embedding launcher contract environment field is missing: $field"
        }
        $name = [string]$property.Value
        $environmentNames[$field] = $name
    }
    $values = @{}
    $values[$environmentNames['profile']] = $selection
    $values[$environmentNames['dimension']] = [string]$dimension
    $values[$environmentNames['device']] = $RequestedDevice

    $workerPythonValue = [string]$Payload.layout.worker_python
    $workerPythonRelative = [string]$contract.worker.python
    $normalizedRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd('\')
    if (
        [string]::IsNullOrWhiteSpace($workerPythonValue) -or
        [string]::IsNullOrWhiteSpace($workerPythonRelative) -or
        [System.IO.Path]::IsPathRooted($workerPythonRelative)
    ) {
        throw 'Embedding launcher contract worker_python is invalid.'
    }
    $workerPythonCandidate = [System.IO.Path]::GetFullPath($workerPythonValue)
    $workerPythonFromContract = [System.IO.Path]::GetFullPath(
        (Join-Path $Root $workerPythonRelative)
    )
    if (-not $workerPythonCandidate.StartsWith(
        $normalizedRoot + '\',
        [System.StringComparison]::OrdinalIgnoreCase
    ) -or -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
        $workerPythonCandidate,
        $workerPythonFromContract
    )) {
        throw 'Embedding worker Python disagrees with runtime_layout.'
    }
    $workerPython = $null
    try {
        $workerPython = Assert-OrdinaryProjectPath `
            -Path $workerPythonCandidate `
            -Root $Root `
            -AllowMissingLeaf
    } catch { }
    if ($workerPython -and (Test-Path -LiteralPath $workerPython -PathType Leaf)) {
        try {
            $workerPython = Assert-OrdinaryProjectPath `
                -Path $workerPython `
                -Root $Root
            $workerItem = Get-Item -LiteralPath $workerPython -Force
            if (
                $workerItem -is [System.IO.FileInfo] -and
                ($workerItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -eq 0
            ) {
                $values[$environmentNames['python']] = $workerPython
            }
        } catch { }
    }

    $profileEnvironmentNameSet = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    foreach ($profileProperty in $profileProperties) {
        $profile = $profileProperty.Value
        $modelDirectoryEnvironment = [string]$profile.model_dir_environment
        $modelRevisionEnvironment = [string]$profile.model_revision_environment
        if (
            -not (Test-EmbeddingEnvironmentName -Name $modelDirectoryEnvironment) -or
            -not (Test-EmbeddingEnvironmentName -Name $modelRevisionEnvironment) -or
            -not $profileEnvironmentNameSet.Add($modelDirectoryEnvironment) -or
            -not $profileEnvironmentNameSet.Add($modelRevisionEnvironment)
        ) {
            throw 'Embedding launcher contract profile environment fields are invalid.'
        }
        [void]$contractEnvironmentNameSet.Add($modelDirectoryEnvironment)
        [void]$contractEnvironmentNameSet.Add($modelRevisionEnvironment)
        $installation = Get-EmbeddingModelInstallation `
            -Contract $contract `
            -Layout $Payload.layout `
            -Profile $profile `
            -Root $Root
        if ($installation.installed) {
            $values[$modelDirectoryEnvironment] = [string]$installation.model_directory
            $values[$modelRevisionEnvironment] = [string]$installation.revision
        }
    }
    return [pscustomobject]@{
        requested_profile = $selection
        values = $values
        environment_names = @($contractEnvironmentNameSet | Sort-Object)
    }
}

function New-CryptographicToken {
    $bytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    } finally {
        $generator.Dispose()
    }
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function New-LoopbackBridgeUrl {
    $listener = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Loopback,
        0
    )
    try {
        $listener.Start()
        $endpoint = [System.Net.IPEndPoint]$listener.LocalEndpoint
        $port = [int]$endpoint.Port
        if ($port -lt 1 -or $port -gt 65535) {
            throw 'Windows returned an invalid loopback port.'
        }
    } finally {
        $listener.Stop()
    }
    return "http://127.0.0.1:$port"
}

function Resolve-ProjectCodexExecutable {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $contractRoot = Join-Path $ProjectRoot 'contracts\codex-app-server'
    $toolchainRoot = Join-Path $ProjectRoot '.runtime\toolchains\codex'
    $matches = @()
    foreach ($contractDirectory in @(
        Get-ChildItem -LiteralPath $contractRoot -Directory -ErrorAction SilentlyContinue
    )) {
        $candidate = Join-Path `
            (Join-Path $toolchainRoot $contractDirectory.Name) `
            'codex.exe'
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $matches += $candidate
        }
    }
    $matches = @($matches | Sort-Object -Unique)
    if ($matches.Count -ne 1) {
        throw "Expected exactly one project Codex toolchain matching contracts; found $($matches.Count)."
    }
    return [System.IO.Path]::GetFullPath([string]$matches[0])
}

function Resolve-BridgePythonExecutable {
    param(
        [AllowEmptyString()][string]$RequestedPath,
        [Parameter(Mandatory = $true)][string]$ProjectRoot
    )

    $managedPython = Get-HiaManagedBridgePythonPath -ProjectRoot $ProjectRoot
    if ($RequestedPath) {
        $requested = [System.IO.Path]::GetFullPath($RequestedPath)
        if ([System.StringComparer]::OrdinalIgnoreCase.Equals($requested, $managedPython)) {
            return Resolve-HiaManagedBridgePython -ProjectRoot $ProjectRoot
        }
        return $requested
    }
    return Resolve-HiaManagedBridgePython -ProjectRoot $ProjectRoot
}

$ResolvedRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path.TrimEnd('\')
$rootItem = Get-Item -LiteralPath $ResolvedRoot -Force
if (($rootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "Project root is a reparse point: $ResolvedRoot"
}
$houdiniMetadata = Resolve-HoudiniExecutable -RequestedPath $HoudiniExe
$HoudiniExe = [string]$houdiniMetadata.Path
$houdiniBinDirectory = [System.IO.Path]::GetDirectoryName($HoudiniExe)
$HythonExe = Join-Path $houdiniBinDirectory 'hython.exe'
if (-not (Test-Path -LiteralPath $HythonExe -PathType Leaf)) {
    throw "Selected Houdini installation is missing sibling hython.exe: $HythonExe"
}
$CodexExe = Resolve-ProjectCodexExecutable -ProjectRoot $ResolvedRoot
$CodexHome = Join-Path $ResolvedRoot '.runtime\codex-home'
$BridgePython = Resolve-BridgePythonExecutable `
    -RequestedPath $BridgePython `
    -ProjectRoot $ResolvedRoot
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
$pythonDirectory = [System.IO.Path]::GetDirectoryName($normalizedPython)
$embeddingContractPayload = Get-EmbeddingLauncherContract `
    -Python $normalizedPython `
    -Root $ResolvedRoot
$embeddingSelection = New-EmbeddingChildEnvironment `
    -Payload $embeddingContractPayload `
    -RequestedProfile $EmbeddingProfile `
    -RequestedDevice $EmbeddingDevice `
    -Root $ResolvedRoot
$EmbeddingProfile = [string]$embeddingSelection.requested_profile
$embeddingEnvironment = $embeddingSelection.values
$embeddingEnvironmentNames = [string[]]$embeddingSelection.environment_names

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
$sessionManifest = Assert-OrdinaryProjectPath `
    -Path (Join-Path $sessionRoot 'session.json') `
    -Root $ResolvedRoot `
    -AllowMissingLeaf
$houdiniPreferences = Assert-OrdinaryProjectPath `
    -Path (Join-Path $sessionRoot 'houdini-user-pref') `
    -Root $ResolvedRoot `
    -AllowMissingLeaf
$cacheRoot = Assert-OrdinaryProjectPath `
    -Path (Join-Path $ResolvedRoot '.runtime\cache') `
    -Root $ResolvedRoot `
    -AllowMissingLeaf
$renderOutputRoot = Resolve-HiaRenderOutputDirectory `
    -ProjectRoot $ResolvedRoot `
    -Path ([string]$env:HIA_RENDER_OUTPUT_DIR) `
    -HoudiniExe $HoudiniExe `
    -Create
$screenshotCache = Assert-OrdinaryProjectPath `
    -Path (Join-Path $cacheRoot 'screenshots') `
    -Root $ResolvedRoot `
    -AllowMissingLeaf
$previewCache = Assert-OrdinaryProjectPath `
    -Path (Join-Path $cacheRoot 'previews') `
    -Root $ResolvedRoot `
    -AllowMissingLeaf
$shortTermCache = Assert-OrdinaryProjectPath `
    -Path (Join-Path $cacheRoot 'tmp') `
    -Root $ResolvedRoot `
    -AllowMissingLeaf
$focusStatePath = Assert-OrdinaryProjectPath `
    -Path (Join-Path $ResolvedRoot '.runtime\bridge\focus-mode.json') `
    -Root $ResolvedRoot `
    -AllowMissingLeaf
[System.IO.Directory]::CreateDirectory($sessionTemp) | Out-Null
[System.IO.Directory]::CreateDirectory($houdiniPreferences) | Out-Null
foreach ($cacheDirectory in @($cacheRoot, $screenshotCache, $previewCache, $shortTermCache)) {
    [System.IO.Directory]::CreateDirectory($cacheDirectory) | Out-Null
}
[System.IO.Directory]::CreateDirectory(
    [System.IO.Path]::GetDirectoryName($focusStatePath)
) | Out-Null

$sessionState = [ordered]@{
    session_id = $sessionId
    state = 'starting'
    selected_houdini = $HoudiniExe
    hip_path = $null
    started_at_utc = [DateTime]::UtcNow.ToString('o')
    ended_at_utc = $null
    process_exit_code = $null
    launcher_process_id = [int]$PID
    houdini_process_id = $null
}
Write-LauncherSessionManifest -ManifestPath $sessionManifest -State $sessionState
$houdiniProcess = $null
try {

$bridgePythonPath = Join-Path $ResolvedRoot 'services\bridge'
$projectSourcePath = Join-Path $ResolvedRoot 'src'
$panelPythonPath = Join-Path $ResolvedRoot 'houdini_package\python_libs'
$packageDirectory = Join-Path $ResolvedRoot 'houdini_package\packages'
try {
    [void](Invoke-HiaKnowledgeBootstrapOnLaunch `
        -Python $normalizedPython `
        -Root $ResolvedRoot `
        -PythonPathEntries @($panelPythonPath, $projectSourcePath) `
        -EmbeddingEnvironment $embeddingEnvironment `
        -EmbeddingEnvironmentNames $embeddingEnvironmentNames)
} catch {
    Write-Warning 'Local knowledge refresh did not complete; Houdini will continue with the current index.'
}
$bridgeToken = New-CryptographicToken
do {
    $sceneExecutorToken = New-CryptographicToken
} while ([System.StringComparer]::Ordinal.Equals($bridgeToken, $sceneExecutorToken))
do {
    $houdiniMcpToken = New-CryptographicToken
} while (
    [System.StringComparer]::Ordinal.Equals($houdiniMcpToken, $bridgeToken) -or
    [System.StringComparer]::Ordinal.Equals($houdiniMcpToken, $sceneExecutorToken)
)
$bridgeUrl = New-LoopbackBridgeUrl
$houdiniMcpUrl = New-LoopbackBridgeUrl
$houdiniMcpPort = ([System.Uri]$houdiniMcpUrl).Port

$bridgeBackendPythonPaths = @()
$houdiniBackendPythonPaths = @()
$bridgeBackendEnvironment = @{}
$houdiniBackendEnvironment = @{
    'HIA_MCP_BACKEND' = $McpBackend
}
$backendEnvironmentNames = @(
    'HIA_MCP_BACKEND',
    'HIA_HOUDINI_MCP_PORT',
    'HOUDINI_HOST',
    'HOUDINI_PORT',
    'FXHOUDINIMCP_AUTOSTART',
    'FXHOUDINIMCP_PORT',
    'FXHOUDINIMCP_TOKEN',
    'HIA_MCP_V2_AUTOSTART',
    'HIA_MCP_V2_HOST',
    'HIA_MCP_V2_PORT',
    'HIA_MCP_V2_TOKEN',
    'HIA_MCP_V2_ROUTE',
    'HIA_MCP_V2_RUNTIME_DIR',
    'HIA_MCP_V2_EXECUTOR_PATH',
    'HIA_LAUNCHER_SESSION_ID'
)
if ($McpBackend -eq 'hia_v2') {
    $hiaMcpServicePath = Assert-OrdinaryProjectPath `
        -Path (Join-Path $ResolvedRoot 'services\hia_mcp_v2') `
        -Root $ResolvedRoot
    $hiaMcpRuntimeSource = Assert-OrdinaryProjectPath `
        -Path (Join-Path $ResolvedRoot 'houdini_package\python_libs\hia_mcp_runtime\http_server.py') `
        -Root $ResolvedRoot
    $hiaMcpExecutorSource = Assert-OrdinaryProjectPath `
        -Path (Join-Path $ResolvedRoot 'houdini_package\python_libs\hia_mcp_runtime\executor.py') `
        -Root $ResolvedRoot
    $hiaMcpRuntimeDirectory = Assert-OrdinaryProjectPath `
        -Path (Join-Path $ResolvedRoot '.runtime\hia-mcp-v2') `
        -Root $ResolvedRoot `
        -AllowMissingLeaf
    [System.IO.Directory]::CreateDirectory($hiaMcpRuntimeDirectory) | Out-Null
    $bridgeBackendPythonPaths = @($hiaMcpServicePath)
    $bridgeBackendEnvironment = @{
        'HIA_MCP_V2_HOST' = '127.0.0.1'
        'HIA_MCP_V2_PORT' = [string]$houdiniMcpPort
        'HIA_MCP_V2_TOKEN' = $houdiniMcpToken
        'HIA_MCP_V2_ROUTE' = '/hia-mcp-v2/v1/execute'
        'HIA_MCP_V2_RUNTIME_DIR' = $hiaMcpRuntimeDirectory
        'HIA_MCP_V2_EXECUTOR_PATH' = $hiaMcpExecutorSource
        'HIA_LAUNCHER_SESSION_ID' = $sessionId
    }
    $houdiniBackendEnvironment += $bridgeBackendEnvironment
    $houdiniBackendEnvironment['HIA_MCP_V2_AUTOSTART'] = '1'
    $houdiniBackendEnvironment += $embeddingEnvironment
} else {
    $fxHoudiniRoot = Join-Path $ResolvedRoot '.runtime\fxhoudinimcp\1.3.0'
    $fxMcpPython = Join-Path $fxHoudiniRoot 'venv\Scripts\python.exe'
    $fxMcpSourcePath = Join-Path $fxHoudiniRoot 'source\python'
    $fxHoudiniServerPath = Join-Path $fxHoudiniRoot 'source\houdini\scripts\python'
    Assert-OrdinaryProjectPath -Path $fxMcpPython -Root $ResolvedRoot | Out-Null
    Assert-OrdinaryProjectPath -Path $fxMcpSourcePath -Root $ResolvedRoot | Out-Null
    Assert-OrdinaryProjectPath -Path $fxHoudiniServerPath -Root $ResolvedRoot | Out-Null
    $bridgeBackendPythonPaths = @((Join-Path $ResolvedRoot 'services\houdini_mcp'))
    $houdiniBackendPythonPaths = @($fxHoudiniServerPath)
    $bridgeBackendEnvironment = @{
        'HIA_HOUDINI_MCP_PORT' = [string]$houdiniMcpPort
        'FXHOUDINIMCP_TOKEN' = $houdiniMcpToken
    }
    $houdiniBackendEnvironment += @{
        'FXHOUDINIMCP_AUTOSTART' = '1'
        'FXHOUDINIMCP_PORT' = [string]$houdiniMcpPort
        'FXHOUDINIMCP_TOKEN' = $houdiniMcpToken
    }
}
$bridgeProcessPythonPath = @($bridgePythonPath) + $bridgeBackendPythonPaths + @($projectSourcePath)
$houdiniProcessPythonPath = @($panelPythonPath) + $houdiniBackendPythonPaths + @($projectSourcePath)

$bridgeArguments = @(
    '-B',
    '-m',
    'hia_bridge',
    '--project-root',
    $ResolvedRoot,
    '--codex-exe',
    $CodexExe,
    '--codex-home',
    $CodexHome,
    '--mcp-backend',
    $McpBackend
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
$bridgeEnvironment = @{
    'PATH' = "$pythonDirectory;$houdiniBinDirectory;$($env:PATH)"
    'PYTHONPATH' = $bridgeProcessPythonPath -join ';'
    'PYTHONDONTWRITEBYTECODE' = '1'
    'PYTHONNOUSERSITE' = '1'
    'TEMP' = $sessionTemp
    'TMP' = $sessionTemp
    'CODEX_HOME' = $CodexHome
    'HIA_PROJECT_ROOT' = $ResolvedRoot
    'HIA_CACHE_DIR' = $cacheRoot
    'HIA_FOCUS_STATE_PATH' = $focusStatePath
    'HIA_RENDER_OUTPUT_DIR' = $renderOutputRoot
    'HIA_EXPECTED_PYTHON_EXE' = $normalizedPython
    'HIA_BRIDGE_URL' = $bridgeUrl
    'HIA_BRIDGE_TOKEN' = $bridgeToken
    'HIA_SCENE_EXECUTOR_TOKEN' = $sceneExecutorToken
}
foreach ($entry in $bridgeBackendEnvironment.GetEnumerator()) {
    $bridgeEnvironment[$entry.Key] = $entry.Value
}
Remove-ChildEnvironment `
    -StartInfo $bridgeInfo `
    -Names $embeddingEnvironmentNames
Remove-ChildEnvironment -StartInfo $bridgeInfo -Names $backendEnvironmentNames
Set-ChildEnvironment -StartInfo $bridgeInfo -Values $bridgeEnvironment

$bridgeProcess = [System.Diagnostics.Process]::new()
$bridgeProcess.StartInfo = $bridgeInfo
$bridgeStarted = $false
$bootstrap = $null
$houdiniProcess = $null
$houdiniStarted = $false
$houdiniExited = $false
$houdiniExitCode = 0
$launcherProcessId = [int]$PID
$taskkillExe = Join-Path $env:SystemRoot 'System32\taskkill.exe'
if (-not (Test-Path -LiteralPath $taskkillExe -PathType Leaf)) {
    throw 'Windows taskkill.exe is unavailable for bounded owned-tree cleanup.'
}

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
    if ($null -ne $bootstrap.PSObject.Properties['url']) {
        throw 'Bridge bootstrap must not expose the loopback URL'
    }
    if ($null -ne $bootstrap.PSObject.Properties['token']) {
        throw 'Bridge bootstrap must not expose the session token'
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
    if ($null -ne $bootstrap.scene.PSObject.Properties['executor_token']) {
        throw 'Bridge bootstrap must not expose the scene executor token'
    }
    if ($bootstrap.scene.schema_version -ne '0.2.0') {
        throw 'Bridge returned an unexpected Houdini read Schema version'
    }
    if ($bootstrap.scene.schema_digest -notmatch '^[A-Fa-f0-9]{64}$') {
        throw 'Bridge returned an invalid Houdini read Schema digest'
    }
    $houdiniEnvironment = @{
        'HOUDINI_PACKAGE_DIR' = $packageDirectory
        'HOUDINI_BACKUP_DIR' = $sessionTemp
        'HOUDINI_TEMP_DIR' = $sessionTemp
        'HOUDINI_USER_PREF_DIR' = $houdiniPreferences
        'PYTHONPATH' = $houdiniProcessPythonPath -join ';'
        'PYTHONDONTWRITEBYTECODE' = '1'
        'TEMP' = $sessionTemp
        'TMP' = $sessionTemp
        'HIA_PROJECT_ROOT' = $ResolvedRoot
        'HIA_CACHE_DIR' = $cacheRoot
        'HIA_FOCUS_STATE_PATH' = $focusStatePath
        'HIA_RENDER_OUTPUT_DIR' = $renderOutputRoot
        'HIA_BRIDGE_URL' = $bridgeUrl
        'HIA_BRIDGE_TOKEN' = $bridgeToken
        'HIA_SCENE_PROFILE' = [string]$bootstrap.scene.profile
        'HIA_BRIDGE_LAUNCH_ID' = [string]$bootstrap.scene.launch_id
        'HIA_BRIDGE_GENERATION' = [string]$sceneGeneration
        'HIA_HOUDINI_PROCESS_NONCE' = [string]$bootstrap.scene.process_nonce
        'HIA_SCENE_EXECUTOR_TOKEN' = $sceneExecutorToken
        'HIA_HOUDINI_SCHEMA_VERSION' = [string]$bootstrap.scene.schema_version
        'HIA_HOUDINI_SCHEMA_DIGEST' = [string]$bootstrap.scene.schema_digest
        'HIA_HYTHON_EXE' = $HythonExe
    }
    foreach ($entry in $houdiniBackendEnvironment.GetEnumerator()) {
        $houdiniEnvironment[$entry.Key] = $entry.Value
    }
    $houdiniInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $houdiniInfo.FileName = $HoudiniExe
    $houdiniInfo.WorkingDirectory = $ResolvedRoot
    $houdiniInfo.UseShellExecute = $false
    $houdiniInfo.CreateNoWindow = $false
    Remove-ChildEnvironment `
        -StartInfo $houdiniInfo `
        -Names $embeddingEnvironmentNames
    Remove-ChildEnvironment -StartInfo $houdiniInfo -Names $backendEnvironmentNames
    Set-ChildEnvironment -StartInfo $houdiniInfo -Values $houdiniEnvironment

    $houdiniProcess = [System.Diagnostics.Process]::new()
    $houdiniProcess.StartInfo = $houdiniInfo
    if (-not $houdiniProcess.Start()) {
        throw 'Houdini process did not start'
    }
    $houdiniStarted = $true
    $houdiniExited = $false
    $sessionState['state'] = 'running'
    $sessionState['ended_at_utc'] = $null
    $sessionState['process_exit_code'] = $null
    $sessionState['hip_path'] = $null
    $sessionState['houdini_process_id'] = [int]$houdiniProcess.Id
    Write-LauncherSessionManifest -ManifestPath $sessionManifest -State $sessionState

    $houdiniProcess.WaitForExit()
    $houdiniExited = $houdiniProcess.HasExited
    $houdiniExitCode = $houdiniProcess.ExitCode
    $sessionState['state'] = if ($houdiniExitCode -eq 0) { 'completed' } else { 'abnormal_exit' }
    $sessionState['ended_at_utc'] = [DateTime]::UtcNow.ToString('o')
    $sessionState['process_exit_code'] = [int]$houdiniExitCode
    Write-LauncherSessionManifest -ManifestPath $sessionManifest -State $sessionState
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
            $headers = @{ Authorization = "Bearer $bridgeToken" }
            Invoke-RestMethod `
                -Method Post `
                -Uri "$bridgeUrl/v1/shutdown" `
                -Headers $headers `
                -ContentType 'application/json' `
                -Body '{}' `
                -TimeoutSec 5 | Out-Null
        } catch {
            Write-Warning 'Graceful Bridge shutdown request failed; bounded owned-tree cleanup will continue.'
        }
    }
    if ($bridgeCleanupAllowed -and $bridgeStarted -and -not $bridgeProcess.HasExited -and -not $bridgeProcess.WaitForExit(7000)) {
        try {
            $bridgePid = [int]$bridgeProcess.Id
            $ownedBridge = Get-ExactOwnedBridgeProcess `
                -ProcessId $bridgePid `
                -LauncherProcessId $launcherProcessId `
                -BridgeExecutablePath $normalizedPython
            if ($null -eq $ownedBridge -or $bridgeProcess.HasExited) {
                throw 'Bridge ownership could not be proven for forced cleanup.'
            }
            $taskkillArguments = @('/PID', [string]$bridgePid, '/T', '/F')
            $taskkillOutput = & $taskkillExe @taskkillArguments 2>&1
            if ($LASTEXITCODE -ne 0) {
                throw 'Windows rejected the exact owned Bridge tree cleanup.'
            }
            if (-not $bridgeProcess.WaitForExit(5000)) {
                throw 'The exact owned Bridge tree did not exit after taskkill.'
            }
        } catch {
            Write-Warning 'Forced cleanup was refused or failed; no unverified process was targeted.'
        }
    }
}
} catch {
    if ($sessionState['state'] -ne 'completed') {
        $sessionState['state'] = if ($sessionState['state'] -eq 'running') {
            'abnormal_exit'
        } else {
            'launch_failed'
        }
        $sessionState['ended_at_utc'] = [DateTime]::UtcNow.ToString('o')
        if ($null -ne $houdiniProcess -and $houdiniProcess.HasExited) {
            try { $sessionState['process_exit_code'] = [int]$houdiniProcess.ExitCode } catch { }
        }
        try {
            Write-LauncherSessionManifest -ManifestPath $sessionManifest -State $sessionState
        } catch {
            Write-Warning 'Launcher session failure metadata could not be updated.'
        }
    }
    throw
}

exit $houdiniExitCode
