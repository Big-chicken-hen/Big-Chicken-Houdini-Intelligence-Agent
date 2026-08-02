[CmdletBinding()]
param(
    [AllowEmptyString()][string]$HoudiniExe = '',
    [AllowEmptyString()][string]$BridgePython = '',
    [AllowEmptyString()][string]$McpBackend = '',
    [AllowEmptyString()][string]$EmbeddingProfile = '',
    [ValidateSet('', 'auto', 'cuda', 'cpu')][string]$EmbeddingDevice = '',
    [AllowEmptyString()][string]$RenderOutputDir = '',
    [switch]$CheckOnly,
    [switch]$Json,
    [switch]$RepairSafeProject,
    [switch]$PrintCodexLoginCommand,
    [ValidateRange(2, 60)][int]$ProbeTimeoutSeconds = 12
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$modulePath = Join-Path $PSScriptRoot 'launcher\HiaLauncher.Core.psm1'
Import-Module -Force $modulePath
$projectRoot = Get-HiaProjectRoot -StartingPath $PSScriptRoot

if (
    $PrintCodexLoginCommand -and
    ($CheckOnly -or $RepairSafeProject)
) {
    throw (
        '-PrintCodexLoginCommand cannot be combined with check or repair actions.'
    )
}

function Get-SelectedInputs {
    param(
        [AllowEmptyString()][string]$RequestedHoudini,
        [AllowEmptyString()][string]$RequestedBridge,
        [AllowEmptyString()][string]$RequestedBackend,
        [AllowEmptyString()][string]$RequestedEmbedding,
        [AllowEmptyString()][string]$RequestedEmbeddingDevice,
        [AllowEmptyString()][string]$RequestedRenderOutput,
        [Parameter(Mandatory = $true)]$Settings
    )

    $candidates = @(if ($RequestedHoudini) {
        Get-HiaHoudiniCandidates -ExplicitPath $RequestedHoudini
    } else {
        Get-HiaHoudiniCandidates
    })
    $selectedHoudini = $RequestedHoudini
    if ($RequestedHoudini -and $candidates.Count -eq 1) {
        $selectedHoudini = [string]$candidates[0].path
    }
    if (-not $selectedHoudini -and $Settings.houdini_exe) {
        $remembered = @($candidates | Where-Object {
            [System.StringComparer]::OrdinalIgnoreCase.Equals([string]$_.path, [string]$Settings.houdini_exe)
        })
        if ($remembered.Count -eq 1) { $selectedHoudini = [string]$remembered[0].path }
    }
    if (-not $selectedHoudini -and $candidates.Count -eq 1) {
        $selectedHoudini = [string]$candidates[0].path
    }

    $savedBridgeCandidate = ''
    $savedAdvancedProperty = $Settings.PSObject.Properties['bridge_python_advanced']
    if ($null -ne $savedAdvancedProperty) {
        $savedBridgeCandidate = [string]$savedAdvancedProperty.Value
    }
    $bridgeCandidates = @(Get-HiaBridgePythonCandidates `
        -ProjectRoot $projectRoot `
        -ExplicitPath $RequestedBridge `
        -SavedPath $savedBridgeCandidate)
    $selectedBridge = $RequestedBridge
    if ($RequestedBridge) {
        try { $selectedBridge = [System.IO.Path]::GetFullPath($RequestedBridge) } catch { }
    }
    if (-not $selectedBridge) {
        $automaticBridge = @($bridgeCandidates | Where-Object {
            $_.PSObject.Properties['automatic'] -and [bool]$_.automatic
        })
        if ($automaticBridge.Count -eq 1) {
            $selectedBridge = [string]$automaticBridge[0].path
        }
    }
    $selectedBackend = if ($RequestedBackend) {
        Resolve-HiaMcpBackend -Backend $RequestedBackend
    } else {
        Resolve-HiaMcpBackend -Backend ([string]$Settings.mcp_backend)
    }
    $selectedRenderOutput = if ($RequestedRenderOutput) {
        try { [System.IO.Path]::GetFullPath($RequestedRenderOutput) } catch { $RequestedRenderOutput }
    } else {
        [string]$Settings.render_output_dir
    }
    $embeddingData = $null
    $selectedEmbedding = ''
    $embeddingContractPythonAvailable = $false
    if ($selectedBridge) {
        try {
            $embeddingContractPythonAvailable = Test-Path `
                -LiteralPath $selectedBridge `
                -PathType Leaf `
                -ErrorAction Stop
        } catch {
            $embeddingContractPythonAvailable = $false
        }
    }
    if ($embeddingContractPythonAvailable) {
        try {
            $embeddingData = Get-HiaEmbeddingContractData `
                -ProjectRoot $projectRoot `
                -PythonExe $selectedBridge `
                -TimeoutSeconds $ProbeTimeoutSeconds
        } catch {
            $embeddingData = $null
        }
        if ($null -ne $embeddingData) {
            $savedEmbedding = ''
            $settingKey = [string]$embeddingData.contract.settings.profile
            $savedProperty = $Settings.PSObject.Properties[$settingKey]
            if ($null -ne $savedProperty) { $savedEmbedding = [string]$savedProperty.Value }
            if ($RequestedEmbedding) {
                $selectedEmbedding = Resolve-HiaEmbeddingProfile `
                    -EmbeddingData $embeddingData `
                    -Profile $RequestedEmbedding
            } else {
                try {
                    $selectedEmbedding = Resolve-HiaEmbeddingProfile `
                        -EmbeddingData $embeddingData `
                        -Profile $savedEmbedding
                } catch {
                    $selectedEmbedding = [string]$embeddingData.contract.default_profile
                }
            }
        }
    }
    $savedDevice = ''
    if ($null -ne $embeddingData) {
        $deviceKey = [string]$embeddingData.contract.settings.device
        $deviceProperty = $Settings.PSObject.Properties[$deviceKey]
        if ($null -ne $deviceProperty) {
            $savedDevice = [string]$deviceProperty.Value
        }
    }
    $deviceCandidate = if ($RequestedEmbeddingDevice) {
        $RequestedEmbeddingDevice
    } else {
        $savedDevice
    }
    $selectedEmbeddingDevice = Resolve-HiaEmbeddingDevice -Device $deviceCandidate
    return [pscustomobject]@{
        candidates = @($candidates)
        bridge_candidates = @($bridgeCandidates)
        houdini = $selectedHoudini
        bridge = $selectedBridge
        backend = $selectedBackend
        embedding = $selectedEmbedding
        embedding_device = $selectedEmbeddingDevice
        embedding_data = $embeddingData
        render_output = $selectedRenderOutput
    }
}

function Invoke-PreflightAndReport {
    param(
        [AllowEmptyString()][string]$SelectedHoudini,
        [AllowEmptyString()][string]$SelectedBridge,
        [ValidateSet('hia_v2', 'fxhoudini')][string]$SelectedBackend,
        [AllowEmptyString()][string]$SelectedEmbedding,
        [ValidateSet('auto', 'cuda', 'cpu')][string]$SelectedEmbeddingDevice = 'auto',
        [AllowNull()]$EmbeddingData,
        [AllowEmptyString()][string]$SelectedRenderOutput,
        [Parameter(Mandatory = $true)][object[]]$Candidates
    )

    $result = Invoke-HiaPreflight `
        -ProjectRoot $projectRoot `
        -HoudiniExe $SelectedHoudini `
        -BridgePython $SelectedBridge `
        -RenderOutputDir $SelectedRenderOutput `
        -McpBackend $SelectedBackend `
        -EmbeddingData $EmbeddingData `
        -EmbeddingProfile $SelectedEmbedding `
        -EmbeddingDevice $SelectedEmbeddingDevice `
        -Candidates $Candidates `
        -TimeoutSeconds $ProbeTimeoutSeconds
    try {
        Write-HiaPreflightReport -Result $result -ProjectRoot $projectRoot | Out-Null
    } catch {
        $result.checks += [pscustomobject]@{
            id = 'launcher.report_write'
            name = 'Preflight report write'
            level = 'red'
            message = 'The report directory could not be created or written.'
            advice = 'Restore write permission under the project-local .runtime directory.'
        }
        $result.overall = 'red'
    }
    return $result
}

function Write-ConsoleSummary {
    param([Parameter(Mandatory = $true)]$Result)

    Write-Output "Overall: $($Result.overall)"
    Write-Output "MCP backend: $($Result.mcp_backend)"
    Write-Output "Final output: $($Result.render_output_dir)"
    Write-Output "JSON report: $($Result.report.json_path)"
    Write-Output "Log report:  $($Result.report.log_path)"
    foreach ($check in $Result.checks) {
        Write-Output "[$($check.level.ToUpperInvariant())] $($check.name): $($check.message)"
        if ($check.level -ne 'green') { Write-Output "  Fix: $($check.advice)" }
    }
}

function Start-ExistingHoudiniLauncher {
    param(
        [Parameter(Mandatory = $true)][string]$SelectedHoudini,
        [Parameter(Mandatory = $true)][string]$SelectedBridge,
        [ValidateSet('hia_v2', 'fxhoudini')][string]$SelectedBackend,
        [AllowEmptyString()][string]$SelectedEmbedding = '',
        [ValidateSet('auto', 'cuda', 'cpu')][string]$SelectedEmbeddingDevice = 'auto',
        [AllowEmptyString()][string]$SelectedRenderOutput = ''
    )

    $resolvedRenderOutput = Resolve-HiaRenderOutputDirectory `
        -ProjectRoot $projectRoot `
        -Path $SelectedRenderOutput `
        -HoudiniExe $SelectedHoudini `
        -Create
    $powershellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $launchScript = Join-Path $projectRoot 'scripts\launch-houdini.ps1'
    $arguments = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', $launchScript,
        '-HoudiniExe', $SelectedHoudini,
        '-BridgePython', $SelectedBridge,
        '-McpBackend', $SelectedBackend
    )
    if ($SelectedEmbedding) {
        $arguments += @('-EmbeddingProfile', $SelectedEmbedding)
    }
    $arguments += @('-EmbeddingDevice', $SelectedEmbeddingDevice)
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $powershellExe
    $startInfo.Arguments = (@($arguments | ForEach-Object { ConvertTo-HiaProcessArgument -Value ([string]$_) }) -join ' ')
    $startInfo.WorkingDirectory = $projectRoot
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $false
    if ($null -ne $startInfo.Environment) {
        $startInfo.Environment['HIA_RENDER_OUTPUT_DIR'] = $resolvedRenderOutput
    } else {
        $startInfo.EnvironmentVariables['HIA_RENDER_OUTPUT_DIR'] = $resolvedRenderOutput
    }
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    if (-not $process.Start()) { throw 'The existing Houdini launcher process did not start.' }
    $process.Dispose()
}

if ($PrintCodexLoginCommand) {
    $loginCommand = Get-HiaCodexLoginCommand -ProjectRoot $projectRoot
    if ($Json) {
        Write-Output (ConvertTo-HiaRedactedJson -Value ([ordered]@{
            schema = 'hia-launcher-cli/1'
            action = 'codex-login-command'
            ok = $true
            command = $loginCommand
        }) -Depth 4)
    } else {
        Write-Output $loginCommand
    }
    exit 0
}

if ($RepairSafeProject) {
    $repairActions = @(Repair-HiaSafeProject -ProjectRoot $projectRoot)
    if ($CheckOnly -or $Json) {
        foreach ($action in $repairActions) {
            if (-not $Json) { Write-Output $action }
        }
    }
}

$settings = Read-HiaLauncherSettings -ProjectRoot $projectRoot
$inputs = Get-SelectedInputs `
    -RequestedHoudini $HoudiniExe `
    -RequestedBridge $BridgePython `
    -RequestedBackend $McpBackend `
    -RequestedEmbedding $EmbeddingProfile `
    -RequestedEmbeddingDevice $EmbeddingDevice `
    -RequestedRenderOutput $RenderOutputDir `
    -Settings $settings

if ($CheckOnly -or $Json) {
    $result = Invoke-PreflightAndReport `
        -SelectedHoudini $inputs.houdini `
        -SelectedBridge $inputs.bridge `
        -SelectedBackend $inputs.backend `
        -SelectedEmbedding $inputs.embedding `
        -SelectedEmbeddingDevice $inputs.embedding_device `
        -EmbeddingData $inputs.embedding_data `
        -SelectedRenderOutput $inputs.render_output `
        -Candidates $inputs.candidates
    if ($Json) {
        Write-Output (ConvertTo-HiaRedactedJson -Value $result -Depth 12)
    } else {
        Write-ConsoleSummary -Result $result
    }
    if ($result.overall -eq 'red') { exit 2 }
    exit 0
}

$wpfUiPath = Join-Path $PSScriptRoot 'launcher\HiaLauncher.Wpf.ps1'
. $wpfUiPath
