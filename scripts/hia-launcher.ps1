[CmdletBinding()]
param(
    [AllowEmptyString()][string]$HoudiniExe = '',
    [AllowEmptyString()][string]$BridgePython = '',
    [AllowEmptyString()][string]$McpBackend = '',
    [AllowEmptyString()][string]$RenderOutputDir = '',
    [switch]$CheckOnly,
    [switch]$Json,
    [switch]$RepairSafeProject,
    [ValidateRange(2, 60)][int]$ProbeTimeoutSeconds = 12
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$modulePath = Join-Path $PSScriptRoot 'launcher\HiaLauncher.Core.psm1'
Import-Module -Force $modulePath
$projectRoot = Get-HiaProjectRoot -StartingPath $PSScriptRoot

function Get-SelectedInputs {
    param(
        [AllowEmptyString()][string]$RequestedHoudini,
        [AllowEmptyString()][string]$RequestedBridge,
        [AllowEmptyString()][string]$RequestedBackend,
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

    $bridgeCandidates = @(Get-HiaBridgePythonCandidates `
        -ProjectRoot $projectRoot `
        -ExplicitPath $RequestedBridge `
        -SavedPath ([string]$Settings.bridge_python))
    $selectedBridge = $RequestedBridge
    if ($RequestedBridge) {
        try { $selectedBridge = [System.IO.Path]::GetFullPath($RequestedBridge) } catch { }
    }
    if (-not $selectedBridge -and $Settings.bridge_python -and (Test-Path -LiteralPath $Settings.bridge_python -PathType Leaf)) {
        $selectedBridge = [string]$Settings.bridge_python
    }
    if (-not $selectedBridge -and $bridgeCandidates.Count -eq 1) {
        $selectedBridge = [string]$bridgeCandidates[0].path
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
    return [pscustomobject]@{
        candidates = @($candidates)
        bridge_candidates = @($bridgeCandidates)
        houdini = $selectedHoudini
        bridge = $selectedBridge
        backend = $selectedBackend
        render_output = $selectedRenderOutput
    }
}

function Invoke-PreflightAndReport {
    param(
        [AllowEmptyString()][string]$SelectedHoudini,
        [AllowEmptyString()][string]$SelectedBridge,
        [ValidateSet('hia_v2', 'fxhoudini')][string]$SelectedBackend,
        [AllowEmptyString()][string]$SelectedRenderOutput,
        [Parameter(Mandatory = $true)][object[]]$Candidates
    )

    $result = Invoke-HiaPreflight `
        -ProjectRoot $projectRoot `
        -HoudiniExe $SelectedHoudini `
        -BridgePython $SelectedBridge `
        -RenderOutputDir $SelectedRenderOutput `
        -McpBackend $SelectedBackend `
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
        [AllowEmptyString()][string]$SelectedRenderOutput = '',
        [AllowEmptyString()][string]$RecoverySessionId = '',
        [AllowEmptyString()][string]$RecoveryCheckpoint = '',
        [AllowEmptyString()][string]$RecoveryDecision = ''
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
    if ($RecoveryDecision) {
        $arguments += @(
            '-RecoverySessionId', $RecoverySessionId,
            '-RecoveryDecision', $RecoveryDecision
        )
        if ($RecoveryDecision -eq 'recover') {
            $arguments += @('-RecoveryCheckpoint', $RecoveryCheckpoint)
        }
    }
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

if ($RepairSafeProject) {
    $repairActions = @(Repair-HiaSafeProject -ProjectRoot $projectRoot)
    if ($CheckOnly -or $Json) {
        foreach ($action in $repairActions) {
            if (-not $Json) { Write-Output $action }
        }
    }
}

$settings = Read-HiaLauncherSettings -ProjectRoot $projectRoot
$inputs = Get-SelectedInputs -RequestedHoudini $HoudiniExe -RequestedBridge $BridgePython -RequestedBackend $McpBackend -RequestedRenderOutput $RenderOutputDir -Settings $settings

if ($CheckOnly -or $Json) {
    $result = Invoke-PreflightAndReport `
        -SelectedHoudini $inputs.houdini `
        -SelectedBridge $inputs.bridge `
        -SelectedBackend $inputs.backend `
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
