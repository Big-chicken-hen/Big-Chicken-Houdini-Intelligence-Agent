Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase

if ([System.Threading.Thread]::CurrentThread.GetApartmentState() -ne [System.Threading.ApartmentState]::STA) {
    [void][System.Windows.MessageBox]::Show(
        '启动器界面需要 STA 模式。请使用 Windows PowerShell 5.1 直接运行 scripts\hia-launcher.ps1。',
        'Big-Chicken Houdini Intelligence Agent 无法继续',
        [System.Windows.MessageBoxButton]::OK,
        [System.Windows.MessageBoxImage]::Error
    )
    exit 1
}

$xamlPath = Join-Path $PSScriptRoot 'HiaLauncher.xaml'
try {
    $xamlDocument = [xml][System.IO.File]::ReadAllText($xamlPath)
    $xamlReader = [System.Xml.XmlNodeReader]::new($xamlDocument)
    try {
        $window = [System.Windows.Markup.XamlReader]::Load($xamlReader)
    } finally {
        $xamlReader.Close()
    }
} catch {
    [void][System.Windows.MessageBox]::Show(
        '无法加载项目本地 WPF 界面资源。请检查 scripts\launcher\HiaLauncher.xaml。',
        'Big-Chicken Houdini Intelligence Agent 无法继续',
        [System.Windows.MessageBoxButton]::OK,
        [System.Windows.MessageBoxImage]::Error
    )
    exit 1
}

$workArea = [System.Windows.SystemParameters]::WorkArea
$availableWidth = [Math]::Max(320, [Math]::Floor($workArea.Width - 32))
$availableHeight = [Math]::Max(240, [Math]::Floor($workArea.Height - 32))
$window.MinWidth = [Math]::Min($window.MinWidth, $availableWidth)
$window.MinHeight = [Math]::Min($window.MinHeight, $availableHeight)
$window.Width = [Math]::Min($window.Width, $availableWidth)
$window.Height = [Math]::Min($window.Height, $availableHeight)

function Get-RequiredControl {
    param([Parameter(Mandatory = $true)][string]$Name)

    $control = $window.FindName($Name)
    if ($null -eq $control) { throw "Required WPF control is missing: $Name" }
    return $control
}

$overallStatusBadge = Get-RequiredControl -Name 'OverallStatusBadge'
$overallStatusDot = Get-RequiredControl -Name 'OverallStatusDot'
$overallStatusText = Get-RequiredControl -Name 'OverallStatusText'
$mcpBackendCombo = Get-RequiredControl -Name 'McpBackendComboBox'
$houdiniCombo = Get-RequiredControl -Name 'HoudiniComboBox'
$browseHoudiniButton = Get-RequiredControl -Name 'BrowseHoudiniButton'
$houdiniPathText = Get-RequiredControl -Name 'HoudiniPathText'
$bridgeCombo = Get-RequiredControl -Name 'BridgePythonComboBox'
$browseBridgeButton = Get-RequiredControl -Name 'BrowseBridgeButton'
$bridgePathText = Get-RequiredControl -Name 'BridgePathText'
$renderOutputTextBox = Get-RequiredControl -Name 'RenderOutputTextBox'
$browseRenderOutputButton = Get-RequiredControl -Name 'BrowseRenderOutputButton'
$passCountText = Get-RequiredControl -Name 'PassCountText'
$warningCountText = Get-RequiredControl -Name 'WarningCountText'
$blockedCountText = Get-RequiredControl -Name 'BlockedCountText'
$checksList = Get-RequiredControl -Name 'ChecksListBox'
$emptyStateBorder = Get-RequiredControl -Name 'EmptyStateBorder'
$emptyStateText = Get-RequiredControl -Name 'EmptyStateText'
$busyPanel = Get-RequiredControl -Name 'BusyPanel'
$inlineStatusBorder = Get-RequiredControl -Name 'InlineStatusBorder'
$inlineStatusText = Get-RequiredControl -Name 'InlineStatusText'
$reportPathTextBox = Get-RequiredControl -Name 'ReportPathTextBox'
$rescanButton = Get-RequiredControl -Name 'RescanButton'
$repairButton = Get-RequiredControl -Name 'RepairButton'
$cleanupScreenshotsButton = Get-RequiredControl -Name 'CleanupScreenshotsButton'
$copyReportButton = Get-RequiredControl -Name 'CopyReportButton'
$launchButton = Get-RequiredControl -Name 'LaunchButton'
$recoveryCard = Get-RequiredControl -Name 'RecoveryCard'
$recoveryCheckpointText = Get-RequiredControl -Name 'RecoveryCheckpointText'
$recoverCheckpointOption = Get-RequiredControl -Name 'RecoverCheckpointOption'
$normalLaunchOption = Get-RequiredControl -Name 'NormalLaunchOption'
$layoutRoot = Get-RequiredControl -Name 'LayoutRoot'
$rightVisualRail = Get-RequiredControl -Name 'RightVisualRail'

$brushGreen = $window.FindResource('StatusGreenBrush')
$brushYellow = $window.FindResource('StatusYellowBrush')
$brushRed = $window.FindResource('StatusRedBrush')
$brushNeutral = $window.FindResource('StatusNeutralBrush')
$brushCyan = $window.FindResource('AccentCyanBrush')
$brushPurple = $window.FindResource('AccentPurpleBrush')
$brushTextSecondary = $window.FindResource('TextSecondaryBrush')
$surfaceGreen = $window.FindResource('GreenSurfaceBrush')
$surfaceYellow = $window.FindResource('YellowSurfaceBrush')
$surfaceRed = $window.FindResource('RedSurfaceBrush')
$surfaceNeutral = $window.FindResource('NeutralSurfaceBrush')

$script:currentCandidates = @()
$script:currentResult = $null
$script:lastReportPath = ''
$script:selectionNeedsCheck = $true
$script:preflightFailed = $false
$script:suppressSelectionCheck = $false
$script:isBusy = $false
$script:initialScanStarted = $false
$script:pendingRecovery = $null
$script:compactLayout = $null
$script:bootstrapProcess = $null
$script:bootstrapPreferences = $null
$renderOutputTextBox.Text = [string]$inputs.render_output
$renderOutputTextBox.ToolTip = if ($renderOutputTextBox.Text) {
    $renderOutputTextBox.Text
} else {
    '留空时使用项目 .runtime\cache'
}

$script:inlineStatusTimer = [System.Windows.Threading.DispatcherTimer]::new()
$script:inlineStatusTimer.Interval = [TimeSpan]::FromSeconds(2.6)
$script:inlineStatusTimer.Add_Tick({
    $script:inlineStatusTimer.Stop()
    $inlineStatusBorder.Visibility = [System.Windows.Visibility]::Collapsed
})

$script:bootstrapTimer = [System.Windows.Threading.DispatcherTimer]::new()
$script:bootstrapTimer.Interval = [TimeSpan]::FromMilliseconds(250)

function Set-OverallState {
    param([Parameter(Mandatory = $true)][ValidateSet('green', 'yellow', 'red', 'neutral', 'busy')][string]$State)

    switch ($State) {
        'green' {
            $overallStatusText.Text = '可以启动'
            $overallStatusDot.Fill = $brushGreen
            $overallStatusBadge.BorderBrush = $brushGreen
            $overallStatusBadge.Background = $surfaceGreen
        }
        'yellow' {
            $overallStatusText.Text = '存在警告'
            $overallStatusDot.Fill = $brushYellow
            $overallStatusBadge.BorderBrush = $brushYellow
            $overallStatusBadge.Background = $surfaceYellow
        }
        'red' {
            $overallStatusText.Text = '需要处理'
            $overallStatusDot.Fill = $brushRed
            $overallStatusBadge.BorderBrush = $brushRed
            $overallStatusBadge.Background = $surfaceRed
        }
        'busy' {
            $overallStatusText.Text = '正在检查'
            $overallStatusDot.Fill = $brushCyan
            $overallStatusBadge.BorderBrush = $brushPurple
            $overallStatusBadge.Background = $surfaceNeutral
        }
        default {
            $overallStatusText.Text = '等待检查'
            $overallStatusDot.Fill = $brushNeutral
            $overallStatusBadge.BorderBrush = $brushNeutral
            $overallStatusBadge.Background = $surfaceNeutral
        }
    }
}

function Update-ResponsiveLayout {
    $compact = $window.ActualWidth -gt 0 -and $window.ActualWidth -lt 760
    if ($script:compactLayout -eq $compact) { return }
    $script:compactLayout = $compact

    if ($compact) {
        $rightVisualRail.Width = 168
        $rightVisualRail.Margin = [System.Windows.Thickness]::new(0, 12, 16, 10)
        return
    }

    $rightVisualRail.Width = 210
    $rightVisualRail.Margin = [System.Windows.Thickness]::new(0, 16, 28, 12)
}

function Initialize-RecoveryPrompt {
    $script:pendingRecovery = $null
    $recoveryCard.Visibility = [System.Windows.Visibility]::Collapsed
    try {
        $candidates = @(Get-HiaRecoverableLauncherSession -ProjectRoot $projectRoot)
        if ($candidates.Count -eq 0) { return }
        $script:pendingRecovery = $candidates[0]
        $recoveryCheckpointText.Text = [string]$script:pendingRecovery.checkpoint_path
        $recoveryCheckpointText.ToolTip = [string]$script:pendingRecovery.checkpoint_path
        $recoverCheckpointOption.IsChecked = $true
        $normalLaunchOption.IsChecked = $false
        $recoveryCard.Visibility = [System.Windows.Visibility]::Visible
    } catch {
        # Recovery discovery is fail-closed; a malformed old session cannot block a normal launch.
        $script:pendingRecovery = $null
        $recoveryCard.Visibility = [System.Windows.Visibility]::Collapsed
    }
}

function Hide-InlineStatus {
    $script:inlineStatusTimer.Stop()
    $inlineStatusBorder.Visibility = [System.Windows.Visibility]::Collapsed
}

function Show-InlineStatus {
    param(
        [Parameter(Mandatory = $true)][string]$Text,
        [ValidateSet('success', 'warning', 'error', 'neutral')][string]$Kind = 'neutral',
        [switch]$Transient
    )

    $script:inlineStatusTimer.Stop()
    $inlineStatusText.Text = $Text
    switch ($Kind) {
        'success' {
            $inlineStatusBorder.Background = $surfaceGreen
            $inlineStatusBorder.BorderBrush = $brushGreen
        }
        'warning' {
            $inlineStatusBorder.Background = $surfaceYellow
            $inlineStatusBorder.BorderBrush = $brushYellow
        }
        'error' {
            $inlineStatusBorder.Background = $surfaceRed
            $inlineStatusBorder.BorderBrush = $brushRed
        }
        default {
            $inlineStatusBorder.Background = $surfaceNeutral
            $inlineStatusBorder.BorderBrush = $brushNeutral
        }
    }
    $inlineStatusBorder.Visibility = [System.Windows.Visibility]::Visible
    if ($Transient) { $script:inlineStatusTimer.Start() }
}

function Get-ComboPath {
    param([Parameter(Mandatory = $true)]$Combo)

    $selected = $Combo.SelectedItem
    if ($null -eq $selected) { return '' }
    $pathProperty = $selected.PSObject.Properties['path']
    if ($null -eq $pathProperty) { return '' }
    return [string]$pathProperty.Value
}

function Get-ComboBackend {
    $selected = $mcpBackendCombo.SelectedItem
    if ($null -eq $selected) { return 'hia_v2' }
    $idProperty = $selected.PSObject.Properties['id']
    if ($null -eq $idProperty) { return 'hia_v2' }
    return Resolve-HiaMcpBackend -Backend ([string]$idProperty.Value)
}

function Get-RenderOutputPath {
    return ([string]$renderOutputTextBox.Text).Trim()
}

function Format-HiaByteCount {
    param([Parameter(Mandatory = $true)][long]$Bytes)

    if ($Bytes -lt 1KB) { return "$Bytes B" }
    if ($Bytes -lt 1MB) { return ('{0:N1} KiB' -f ($Bytes / 1KB)) }
    if ($Bytes -lt 1GB) { return ('{0:N1} MiB' -f ($Bytes / 1MB)) }
    return ('{0:N2} GiB' -f ($Bytes / 1GB))
}

function Update-PathSummaries {
    $houdiniPath = Get-ComboPath -Combo $houdiniCombo
    if ($houdiniPath) {
        $houdiniPathText.Text = $houdiniPath
        $houdiniPathText.ToolTip = $houdiniPath
    } else {
        $houdiniPathText.Text = '尚未选择 houdini.exe'
        $houdiniPathText.ToolTip = '尚未选择 houdini.exe'
    }

    $bridgePath = Get-ComboPath -Combo $bridgeCombo
    if ($bridgePath) {
        $bridgePathText.Text = $bridgePath
        $bridgePathText.ToolTip = $bridgePath
    } else {
        $bridgePathText.Text = '尚未选择 Bridge python.exe'
        $bridgePathText.ToolTip = '尚未选择 Bridge python.exe'
    }

    $renderOutputPath = Get-RenderOutputPath
    $renderOutputTextBox.ToolTip = if ($renderOutputPath) {
        $renderOutputPath
    } else {
        '留空时使用项目 .runtime\cache'
    }
}

function Set-BusyState {
    param([Parameter(Mandatory = $true)][bool]$Busy)

    $script:isBusy = $Busy
    $busyPanel.Visibility = if ($Busy) {
        [System.Windows.Visibility]::Visible
    } else {
        [System.Windows.Visibility]::Collapsed
    }
    $window.Cursor = if ($Busy) { [System.Windows.Input.Cursors]::Wait } else { $null }

    $mcpBackendCombo.IsEnabled = -not $Busy
    $houdiniCombo.IsEnabled = -not $Busy
    $bridgeCombo.IsEnabled = -not $Busy
    $renderOutputTextBox.IsEnabled = -not $Busy
    $browseHoudiniButton.IsEnabled = -not $Busy
    $browseBridgeButton.IsEnabled = -not $Busy
    $browseRenderOutputButton.IsEnabled = -not $Busy
    $recoverCheckpointOption.IsEnabled = -not $Busy
    $normalLaunchOption.IsEnabled = -not $Busy
    $rescanButton.IsEnabled = -not $Busy
    $repairButton.IsEnabled = -not $Busy
    $cleanupScreenshotsButton.IsEnabled = -not $Busy
    if ($Busy) {
        $copyReportButton.IsEnabled = $false
        $launchButton.IsEnabled = $false
        Set-OverallState -State 'busy'
        $window.UpdateLayout()
        [void]$window.Dispatcher.Invoke(
            [System.Action]{ },
            [System.Windows.Threading.DispatcherPriority]::Render
        )
    } else {
        $copyReportButton.IsEnabled = -not [string]::IsNullOrWhiteSpace($script:lastReportPath)
        $launchButton.IsEnabled = (
            $null -ne $script:currentResult -and
            -not $script:selectionNeedsCheck -and
            $script:currentResult.overall -ne 'red'
        )
        if ($script:preflightFailed) {
            Set-OverallState -State 'red'
        } elseif ($null -ne $script:currentResult) {
            Set-OverallState -State ([string]$script:currentResult.overall)
        } else {
            Set-OverallState -State 'neutral'
        }
    }
}

function Test-CurrentRedCheck {
    param([Parameter(Mandatory = $true)][string]$Id)

    if ($null -eq $script:currentResult) { return $false }
    return @($script:currentResult.checks | Where-Object {
        [string]$_.id -eq $Id -and [string]$_.level -eq 'red'
    }).Count -gt 0
}

function Update-RepairButton {
    $label = '修复安全项目'
    if (Test-CurrentRedCheck -Id 'codex.executable') {
        $label = '安装/修复 Codex'
    } elseif (Test-CurrentRedCheck -Id 'codex.login') {
        $label = '复制登录命令'
    }
    $repairButton.Content = $label
    [System.Windows.Automation.AutomationProperties]::SetName($repairButton, $label)
}

function New-CheckView {
    param([Parameter(Mandatory = $true)]$Check)

    $level = ([string]$Check.level).ToLowerInvariant()
    $statusText = '阻断'
    $statusBrush = $brushRed
    $adviceBrush = $brushRed
    if ($level -eq 'green') {
        $statusText = '通过'
        $statusBrush = $brushGreen
        $adviceBrush = $brushTextSecondary
    } elseif ($level -eq 'yellow') {
        $statusText = '警告'
        $statusBrush = $brushYellow
        $adviceBrush = $brushYellow
    }
    $advice = [string]$Check.advice
    if ([string]::IsNullOrWhiteSpace($advice)) { $advice = '无需处理。' }
    return [pscustomobject]@{
        StatusText = $statusText
        StatusBrush = $statusBrush
        BorderBrush = $statusBrush
        CheckName = [string]$Check.name
        ResultText = [string]$Check.message
        AdviceText = "建议：$advice"
        AdviceBrush = $adviceBrush
    }
}

function Show-Result {
    param([Parameter(Mandatory = $true)]$Result)

    $script:currentResult = $Result
    $script:selectionNeedsCheck = $false
    $script:preflightFailed = $false
    $checks = @($Result.checks)
    $views = [System.Collections.Generic.List[object]]::new()
    foreach ($check in $checks) { $views.Add((New-CheckView -Check $check)) }
    $checksList.ItemsSource = $views.ToArray()
    $emptyStateBorder.Visibility = if ($views.Count -eq 0) {
        [System.Windows.Visibility]::Visible
    } else {
        [System.Windows.Visibility]::Collapsed
    }

    $passCountText.Text = [string](@($checks | Where-Object level -eq 'green').Count)
    $warningCountText.Text = [string](@($checks | Where-Object level -eq 'yellow').Count)
    $blockedCountText.Text = [string](@($checks | Where-Object level -eq 'red').Count)

    $reportPath = ''
    if ($null -ne $Result.report) {
        $reportProperty = $Result.report.PSObject.Properties['json_path']
        if ($null -ne $reportProperty) { $reportPath = [string]$reportProperty.Value }
    }
    if ($reportPath) {
        $script:lastReportPath = $reportPath
        $reportPathTextBox.Text = $reportPath
        $reportPathTextBox.ToolTip = $reportPath
    }

    if ($Result.overall -eq 'green') {
        Set-OverallState -State 'green'
    } elseif ($Result.overall -eq 'yellow') {
        Set-OverallState -State 'yellow'
    } else {
        Set-OverallState -State 'red'
    }
    Update-RepairButton
}

function Show-PreflightFailure {
    $script:currentResult = $null
    $script:selectionNeedsCheck = $true
    $script:preflightFailed = $true
    $failedCheck = [pscustomobject]@{
        level = 'red'
        name = '启动器自检'
        message = '自检过程未能完成。'
        advice = '请在控制台运行 scripts\hia-launcher.ps1 -CheckOnly 查看可调试结果。'
    }
    $checksList.ItemsSource = @((New-CheckView -Check $failedCheck))
    $emptyStateBorder.Visibility = [System.Windows.Visibility]::Collapsed
    $passCountText.Text = '0'
    $warningCountText.Text = '0'
    $blockedCountText.Text = '1'
    Set-OverallState -State 'red'
    Update-RepairButton
    Show-InlineStatus -Kind 'error' -Text '自检失败；未启动 Houdini。请使用控制台检查模式定位问题。'
}

function Mark-SelectionNeedsCheck {
    if ($script:suppressSelectionCheck -or $script:isBusy) { return }
    $script:currentResult = $null
    $script:selectionNeedsCheck = $true
    $script:preflightFailed = $false
    $checksList.ItemsSource = $null
    $emptyStateText.Text = '环境选择已变化，重新扫描后显示新的自检结果。'
    $emptyStateBorder.Visibility = [System.Windows.Visibility]::Visible
    $passCountText.Text = '0'
    $warningCountText.Text = '0'
    $blockedCountText.Text = '0'
    Set-OverallState -State 'neutral'
    Update-RepairButton
    Update-PathSummaries
    Set-BusyState -Busy $false
    Show-InlineStatus -Kind 'warning' -Text '环境选择已变化，请点击“重新扫描”完成检查。'
}

function Complete-HiaCodexBootstrap {
    if ($null -eq $script:bootstrapProcess -or -not $script:bootstrapProcess.HasExited) { return }

    $script:bootstrapTimer.Stop()
    $exitCode = $script:bootstrapProcess.ExitCode
    $script:bootstrapProcess.Dispose()
    $script:bootstrapProcess = $null

    $preferences = $script:bootstrapPreferences
    $script:bootstrapPreferences = $null
    $repairFailed = $false
    if ($exitCode -eq 0) {
        try {
            [void]@(Repair-HiaSafeProject -ProjectRoot $projectRoot)
        } catch {
            $repairFailed = $true
        }
    }

    Set-BusyState -Busy $false
    Invoke-GuiScan `
        -PreferredHoudini ([string]$preferences.houdini) `
        -PreferredBridge ([string]$preferences.bridge) `
        -PreferredBackend ([string]$preferences.backend)

    if ($exitCode -eq 0 -and -not $repairFailed) {
        Show-InlineStatus -Kind 'success' -Transient -Text 'Codex 已安装到项目 .runtime；自检已自动刷新。'
        return
    }
    Show-InlineStatus -Kind 'error' -Text ("Codex 项目本地安装失败（退出码 {0}）。请在 PowerShell 中运行 scripts\bootstrap-runtime.ps1 查看详情。" -f $exitCode)
}

$script:bootstrapTimer.Add_Tick({ Complete-HiaCodexBootstrap })

function Start-HiaCodexBootstrap {
    if ($null -ne $script:bootstrapProcess) { return }

    $bootstrapScript = Join-Path $projectRoot 'scripts\bootstrap-runtime.ps1'
    if (-not (Test-Path -LiteralPath $bootstrapScript -PathType Leaf)) {
        Show-InlineStatus -Kind 'error' -Text '缺少 scripts\bootstrap-runtime.ps1，无法执行项目本地 Codex 安装。'
        return
    }

    $script:bootstrapPreferences = [pscustomobject]@{
        houdini = Get-ComboPath -Combo $houdiniCombo
        bridge = Get-ComboPath -Combo $bridgeCombo
        backend = Get-ComboBackend
    }
    $powershellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $powershellExe
    $startInfo.Arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$bootstrapScript`""
    $startInfo.WorkingDirectory = $projectRoot
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true

    try {
        $script:bootstrapProcess = [System.Diagnostics.Process]::new()
        $script:bootstrapProcess.StartInfo = $startInfo
        if (-not $script:bootstrapProcess.Start()) { throw '无法启动 PowerShell bootstrap 进程。' }
        Set-BusyState -Busy $true
        $overallStatusText.Text = '正在安装 Codex'
        Show-InlineStatus -Kind 'neutral' -Text '正在下载并校验官方 Codex 0.144.3；仅写入项目 .runtime。'
        $script:bootstrapTimer.Start()
    } catch {
        if ($null -ne $script:bootstrapProcess) {
            $script:bootstrapProcess.Dispose()
            $script:bootstrapProcess = $null
        }
        $script:bootstrapPreferences = $null
        Set-BusyState -Busy $false
        Show-InlineStatus -Kind 'error' -Text ("无法启动 Codex 项目本地安装：{0}" -f $_.Exception.Message)
    }
}

function Get-PathIndex {
    param(
        [Parameter(Mandatory = $true)]$Combo,
        [AllowEmptyString()][string]$Path = ''
    )

    if (-not $Path) { return -1 }
    for ($index = 0; $index -lt $Combo.Items.Count; $index++) {
        $item = $Combo.Items[$index]
        $property = $item.PSObject.Properties['path']
        if ($null -ne $property -and [System.StringComparer]::OrdinalIgnoreCase.Equals([string]$property.Value, $Path)) {
            return $index
        }
    }
    return -1
}

function Get-BackendIndex {
    param([AllowEmptyString()][string]$Backend = '')

    $resolved = Resolve-HiaMcpBackend -Backend $Backend
    for ($index = 0; $index -lt $mcpBackendCombo.Items.Count; $index++) {
        $item = $mcpBackendCombo.Items[$index]
        $property = $item.PSObject.Properties['id']
        if ($null -ne $property -and [string]$property.Value -eq $resolved) {
            return $index
        }
    }
    return -1
}

function Invoke-GuiScan {
    param(
        [AllowEmptyString()][string]$PreferredHoudini = '',
        [AllowEmptyString()][string]$PreferredBridge = '',
        [AllowEmptyString()][string]$PreferredBackend = ''
    )

    Set-BusyState -Busy $true
    Hide-InlineStatus
    try {
        $script:suppressSelectionCheck = $true
        try {
            $backendChoice = if ($PreferredBackend) {
                Resolve-HiaMcpBackend -Backend $PreferredBackend
            } else {
                Resolve-HiaMcpBackend -Backend ([string]$settings.mcp_backend)
            }
            $mcpBackendCombo.Items.Clear()
            foreach ($candidate in @(Get-HiaMcpBackendChoices)) {
                [void]$mcpBackendCombo.Items.Add($candidate)
            }
            $backendIndex = Get-BackendIndex -Backend $backendChoice
            if ($backendIndex -lt 0) { throw 'The selected MCP backend is unavailable.' }
            $mcpBackendCombo.SelectedIndex = $backendIndex

            $houdiniChoice = $PreferredHoudini
            if (-not $houdiniChoice) { $houdiniChoice = [string]$settings.houdini_exe }
            $candidateMap = @{}
            foreach ($candidate in @(Get-HiaHoudiniCandidates)) {
                $candidateMap[[string]$candidate.path] = $candidate
            }
            if ($houdiniChoice) {
                foreach ($candidate in @(Get-HiaHoudiniCandidates -ExplicitPath $houdiniChoice)) {
                    $candidateMap[[string]$candidate.path] = $candidate
                }
            }
            $script:currentCandidates = @(
                $candidateMap.Values |
                    Sort-Object -Property @{ Expression = { $_.version }; Descending = $true }, path
            )
            $houdiniCombo.Items.Clear()
            foreach ($candidate in $script:currentCandidates) { [void]$houdiniCombo.Items.Add($candidate) }
            $houdiniIndex = Get-PathIndex -Combo $houdiniCombo -Path $houdiniChoice
            if ($houdiniIndex -ge 0) {
                $houdiniCombo.SelectedIndex = $houdiniIndex
            } elseif ($houdiniCombo.Items.Count -eq 1) {
                $houdiniCombo.SelectedIndex = 0
            } else {
                $houdiniCombo.SelectedIndex = -1
            }

            $bridgeChoice = $PreferredBridge
            if (-not $bridgeChoice) { $bridgeChoice = [string]$settings.bridge_python }
            $bridgeCandidates = @(
                Get-HiaBridgePythonCandidates `
                    -ProjectRoot $projectRoot `
                    -ExplicitPath $bridgeChoice `
                    -SavedPath ([string]$settings.bridge_python)
            )
            if ($bridgeChoice) {
                $knownBridge = @($bridgeCandidates | Where-Object {
                    [System.StringComparer]::OrdinalIgnoreCase.Equals([string]$_.path, $bridgeChoice)
                })
                if ($knownBridge.Count -eq 0) {
                    try { $bridgeChoice = [System.IO.Path]::GetFullPath($bridgeChoice) } catch { }
                    $bridgeCandidates += [pscustomobject]@{
                        path = $bridgeChoice
                        source = 'requested'
                        display = "缺失 — $bridgeChoice"
                    }
                }
            }
            $bridgeCombo.Items.Clear()
            foreach ($candidate in @($bridgeCandidates | Sort-Object -Property path -Unique)) {
                [void]$bridgeCombo.Items.Add($candidate)
            }
            $bridgeIndex = Get-PathIndex -Combo $bridgeCombo -Path $bridgeChoice
            if ($bridgeIndex -ge 0) {
                $bridgeCombo.SelectedIndex = $bridgeIndex
            } elseif ($bridgeCombo.Items.Count -eq 1) {
                $bridgeCombo.SelectedIndex = 0
            } else {
                $bridgeCombo.SelectedIndex = -1
            }
        } finally {
            $script:suppressSelectionCheck = $false
        }

        Update-PathSummaries
        $selectedHoudini = Get-ComboPath -Combo $houdiniCombo
        $selectedBridge = Get-ComboPath -Combo $bridgeCombo
        $selectedBackend = Get-ComboBackend
        $selectedRenderOutput = Get-RenderOutputPath
        $script:currentResult = Invoke-PreflightAndReport `
            -SelectedHoudini $selectedHoudini `
            -SelectedBridge $selectedBridge `
            -SelectedBackend $selectedBackend `
            -SelectedRenderOutput $selectedRenderOutput `
            -Candidates $script:currentCandidates
        Show-Result -Result $script:currentResult
    } catch {
        Show-PreflightFailure
    } finally {
        $script:suppressSelectionCheck = $false
        Set-BusyState -Busy $false
    }
}

function Add-OrSelectHoudiniCandidate {
    param([Parameter(Mandatory = $true)][string]$Path)

    $candidate = @(Get-HiaHoudiniCandidates -ExplicitPath $Path)
    if ($candidate.Count -ne 1) {
        Show-InlineStatus -Kind 'error' -Text '无法读取所选 houdini.exe，请确认文件仍然存在。'
        return
    }
    $index = Get-PathIndex -Combo $houdiniCombo -Path ([string]$candidate[0].path)
    $script:suppressSelectionCheck = $true
    try {
        if ($index -lt 0) {
            [void]$houdiniCombo.Items.Add($candidate[0])
            $script:currentCandidates = @($script:currentCandidates) + @($candidate[0])
            $index = $houdiniCombo.Items.Count - 1
        }
        $houdiniCombo.SelectedIndex = $index
    } finally {
        $script:suppressSelectionCheck = $false
    }
    Mark-SelectionNeedsCheck
}

function Add-OrSelectBridgeCandidate {
    param([Parameter(Mandatory = $true)][string]$Path)

    try { $fullPath = [System.IO.Path]::GetFullPath($Path) } catch { $fullPath = $Path }
    $index = Get-PathIndex -Combo $bridgeCombo -Path $fullPath
    $script:suppressSelectionCheck = $true
    try {
        if ($index -lt 0) {
            $candidate = [pscustomobject]@{
                path = $fullPath
                source = 'explicit'
                display = "$fullPath  [explicit]"
            }
            [void]$bridgeCombo.Items.Add($candidate)
            $index = $bridgeCombo.Items.Count - 1
        }
        $bridgeCombo.SelectedIndex = $index
    } finally {
        $script:suppressSelectionCheck = $false
    }
    Mark-SelectionNeedsCheck
}

$rescanButton.Add_Click({
    Invoke-GuiScan `
        -PreferredHoudini (Get-ComboPath -Combo $houdiniCombo) `
        -PreferredBridge (Get-ComboPath -Combo $bridgeCombo) `
        -PreferredBackend (Get-ComboBackend)
})

$mcpBackendCombo.Add_SelectionChanged({ Mark-SelectionNeedsCheck })
$houdiniCombo.Add_SelectionChanged({ Mark-SelectionNeedsCheck })
$bridgeCombo.Add_SelectionChanged({ Mark-SelectionNeedsCheck })
$renderOutputTextBox.Add_TextChanged({ Mark-SelectionNeedsCheck })

$browseHoudiniButton.Add_Click({
    $dialog = [Microsoft.Win32.OpenFileDialog]::new()
    $dialog.Title = '选择 houdini.exe'
    $dialog.Filter = 'Houdini 可执行文件 (houdini.exe)|houdini.exe'
    $dialog.CheckFileExists = $true
    if ($dialog.ShowDialog($window) -eq $true) {
        Add-OrSelectHoudiniCandidate -Path $dialog.FileName
    }
})

$browseBridgeButton.Add_Click({
    $dialog = [Microsoft.Win32.OpenFileDialog]::new()
    $dialog.Title = '选择 Bridge python.exe'
    $dialog.Filter = 'Python 可执行文件 (python.exe)|python.exe'
    $dialog.CheckFileExists = $true
    if ($dialog.ShowDialog($window) -eq $true) {
        Add-OrSelectBridgeCandidate -Path $dialog.FileName
    }
})

$browseRenderOutputButton.Add_Click({
    if ($script:isBusy) { return }
    $shell = $null
    try {
        $shell = New-Object -ComObject Shell.Application
        $owner = [System.Windows.Interop.WindowInteropHelper]::new($window).Handle
        $folder = $shell.BrowseForFolder(
            [int]$owner,
            '选择最终渲染输出目录（可在对话框中新建文件夹）',
            0x41,
            0
        )
        if ($null -eq $folder) { return }
        $selectedPath = [string]$folder.Self.Path
        $resolvedPath = Resolve-HiaRenderOutputDirectory `
            -ProjectRoot $projectRoot `
            -Path $selectedPath `
            -HoudiniExe (Get-ComboPath -Combo $houdiniCombo) `
            -Create
        $script:suppressSelectionCheck = $true
        try {
            $renderOutputTextBox.Text = $resolvedPath
            $renderOutputTextBox.ToolTip = $resolvedPath
        } finally {
            $script:suppressSelectionCheck = $false
        }
        Mark-SelectionNeedsCheck
    } catch {
        Show-InlineStatus -Kind 'error' -Text ([string]$_.Exception.Message)
    } finally {
        if ($null -ne $shell) {
            try { [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell) } catch { }
        }
    }
})

$repairButton.Add_Click({
    if ($script:isBusy) { return }
    if (Test-CurrentRedCheck -Id 'codex.executable') {
        Start-HiaCodexBootstrap
        return
    }
    if (Test-CurrentRedCheck -Id 'codex.login') {
        try {
            [System.Windows.Clipboard]::SetText((Get-HiaCodexLoginCommand -ProjectRoot $projectRoot))
            Show-InlineStatus -Kind 'success' -Text '项目本地 Codex 登录命令已复制；请在 PowerShell 中运行并完成 device login。命令不含凭据。'
        } catch {
            Show-InlineStatus -Kind 'error' -Text '无法复制登录命令；请按安装文档中的项目本地登录步骤执行。'
        }
        return
    }
    $preferredHoudini = Get-ComboPath -Combo $houdiniCombo
    $preferredBridge = Get-ComboPath -Combo $bridgeCombo
    $preferredBackend = Get-ComboBackend
    Set-BusyState -Busy $true
    try {
        $actions = @(Repair-HiaSafeProject -ProjectRoot $projectRoot)
    } catch {
        Set-BusyState -Busy $false
        Show-InlineStatus -Kind 'error' -Text '安全项目修复失败。请在控制台使用 -RepairSafeProject -CheckOnly 查看详情。'
        return
    }
    Set-BusyState -Busy $false
    Invoke-GuiScan -PreferredHoudini $preferredHoudini -PreferredBridge $preferredBridge -PreferredBackend $preferredBackend
    if ($null -ne $script:currentResult) {
        $detail = if ($actions.Count -gt 0) { ' ' + ($actions -join '；') } else { '' }
        Show-InlineStatus -Kind 'success' -Transient -Text ("安全项目修复完成。$detail")
    }
})

$cleanupScreenshotsButton.Add_Click({
    if ($script:isBusy) { return }

    try {
        $preview = Invoke-HiaScreenshotCacheCleanup -ProjectRoot $projectRoot
    } catch {
        Show-InlineStatus -Kind 'error' -Text ("截图缓存未清理：{0}" -f $_.Exception.Message)
        return
    }

    $previewSize = Format-HiaByteCount -Bytes ([long]$preview.matched_bytes)
    $confirmationText = @"
唯一允许目标：
$($preview.target_path)

匹配 PNG 文件：$($preview.matched_count) 个
总大小：$previewSize
当前跳过：$($preview.skipped_count) 个

确认只删除该目录第一层、且仍与本次预览一致的 Big-Chicken PNG 截图吗？
子目录、其他缓存、附件和最终渲染输出不会被清理。
"@
    $confirmation = [System.Windows.MessageBox]::Show(
        $window,
        $confirmationText,
        '确认清理截图缓存',
        [System.Windows.MessageBoxButton]::YesNo,
        [System.Windows.MessageBoxImage]::Warning,
        [System.Windows.MessageBoxResult]::No
    )
    if ($confirmation -ne [System.Windows.MessageBoxResult]::Yes) {
        Show-InlineStatus -Kind 'neutral' -Transient -Text '已取消截图缓存清理；未删除任何文件。'
        return
    }

    Set-BusyState -Busy $true
    try {
        $cleanupResult = Invoke-HiaScreenshotCacheCleanup `
            -ProjectRoot $projectRoot `
            -Plan $preview `
            -Delete
    } catch {
        Show-InlineStatus -Kind 'error' -Text ("截图缓存未清理：{0}" -f $_.Exception.Message)
        return
    } finally {
        Set-BusyState -Busy $false
    }

    $freedSize = Format-HiaByteCount -Bytes ([long]$cleanupResult.deleted_bytes)
    $resultKind = if ([int]$cleanupResult.failed_count -gt 0) { 'warning' } else { 'success' }
    Show-InlineStatus `
        -Kind $resultKind `
        -Text (
            '截图缓存清理完成：已删除 {0} 个，释放 {1}；跳过 {2} 个（失败 {3} 个）。' -f `
                $cleanupResult.deleted_count,
                $freedSize,
                $cleanupResult.skipped_count,
                $cleanupResult.failed_count
        )
})

$copyReportButton.Add_Click({
    if (-not $script:lastReportPath) { return }
    try {
        [System.Windows.Clipboard]::SetText($script:lastReportPath)
        Show-InlineStatus -Kind 'success' -Transient -Text ("已复制报告路径：$($script:lastReportPath)")
    } catch {
        Show-InlineStatus -Kind 'error' -Text '未能写入剪贴板；可直接选中上方报告路径复制。'
    }
})

$launchButton.Add_Click({
    if ($script:selectionNeedsCheck -or $script:isBusy) { return }
    $selectedHoudini = Get-ComboPath -Combo $houdiniCombo
    $selectedBridge = Get-ComboPath -Combo $bridgeCombo
    $selectedBackend = Get-ComboBackend
    $selectedRenderOutput = Get-RenderOutputPath
    Set-BusyState -Busy $true
    try {
        try {
            $script:currentResult = Invoke-PreflightAndReport `
                -SelectedHoudini $selectedHoudini `
                -SelectedBridge $selectedBridge `
                -SelectedBackend $selectedBackend `
                -SelectedRenderOutput $selectedRenderOutput `
                -Candidates $script:currentCandidates
            Show-Result -Result $script:currentResult
        } catch {
            Show-PreflightFailure
            return
        }
        if ($script:currentResult.overall -eq 'red') { return }
        try {
            Write-HiaLauncherSettings `
                -ProjectRoot $projectRoot `
                -HoudiniExe $selectedHoudini `
                -BridgePython $selectedBridge `
                -RenderOutputDir $selectedRenderOutput `
                -McpBackend $selectedBackend | Out-Null
            $launchParameters = @{
                SelectedHoudini = $selectedHoudini
                SelectedBridge = $selectedBridge
                SelectedBackend = $selectedBackend
                SelectedRenderOutput = $selectedRenderOutput
            }
            if ($null -ne $script:pendingRecovery) {
                $recoveryDecision = if ($recoverCheckpointOption.IsChecked) { 'recover' } else { 'normal' }
                $launchParameters['RecoverySessionId'] = [string]$script:pendingRecovery.session_id
                $launchParameters['RecoveryDecision'] = $recoveryDecision
                if ($recoveryDecision -eq 'recover') {
                    $launchParameters['RecoveryCheckpoint'] = [string]$script:pendingRecovery.checkpoint_path
                }
            }
            Start-ExistingHoudiniLauncher @launchParameters
            if ($null -ne $script:pendingRecovery) {
                $script:pendingRecovery = $null
                $recoveryCard.Visibility = [System.Windows.Visibility]::Collapsed
            }
            Show-InlineStatus `
                -Kind 'success' `
                -Transient `
                -Text '已交给 scripts\launch-houdini.ps1 启动；Houdini 生命周期仍由该脚本管理。'
        } catch {
            Show-InlineStatus -Kind 'error' -Text '未能启动现有 launch-houdini.ps1。请在控制台运行该脚本查看详情。'
        }
    } finally {
        Set-BusyState -Busy $false
    }
})

Initialize-RecoveryPrompt

$window.Add_SizeChanged({ Update-ResponsiveLayout })
$window.Add_Closed({
    $script:inlineStatusTimer.Stop()
    $script:bootstrapTimer.Stop()
    if ($null -ne $script:bootstrapProcess) {
        # Disposing this wrapper does not terminate the user-started verified bootstrap.
        $script:bootstrapProcess.Dispose()
        $script:bootstrapProcess = $null
    }
})
$window.Add_ContentRendered({
    Update-ResponsiveLayout
    if ($script:initialScanStarted) { return }
    $script:initialScanStarted = $true
    Invoke-GuiScan -PreferredHoudini $inputs.houdini -PreferredBridge $inputs.bridge -PreferredBackend $inputs.backend
})

[void]$window.ShowDialog()
