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

$windowChrome = [System.Windows.Shell.WindowChrome]::new()
$windowChrome.CaptionHeight = 42
$windowChrome.ResizeBorderThickness = [System.Windows.Thickness]::new(7)
$windowChrome.GlassFrameThickness = [System.Windows.Thickness]::new(0)
$windowChrome.CornerRadius = [System.Windows.CornerRadius]::new(10)
$windowChrome.UseAeroCaptionButtons = $false
[System.Windows.Shell.WindowChrome]::SetWindowChrome($window, $windowChrome)

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

$customTitleBar = Get-RequiredControl -Name 'CustomTitleBar'
$minimizeWindowButton = Get-RequiredControl -Name 'MinimizeWindowButton'
$maximizeWindowButton = Get-RequiredControl -Name 'MaximizeWindowButton'
$maximizeWindowGlyph = Get-RequiredControl -Name 'MaximizeWindowGlyph'
$closeWindowButton = Get-RequiredControl -Name 'CloseWindowButton'
$overallStatusBadge = Get-RequiredControl -Name 'OverallStatusBadge'
$overallStatusDot = Get-RequiredControl -Name 'OverallStatusDot'
$overallStatusText = Get-RequiredControl -Name 'OverallStatusText'
$mcpBackendCombo = Get-RequiredControl -Name 'McpBackendComboBox'
$embeddingProfileCombo = Get-RequiredControl -Name 'EmbeddingProfileComboBox'
$embeddingDeviceCombo = Get-RequiredControl -Name 'EmbeddingDeviceComboBox'
$houdiniCombo = Get-RequiredControl -Name 'HoudiniComboBox'
$browseHoudiniButton = Get-RequiredControl -Name 'BrowseHoudiniButton'
$houdiniPathText = Get-RequiredControl -Name 'HoudiniPathText'
$bridgeCombo = Get-RequiredControl -Name 'BridgePythonComboBox'
$browseBridgeButton = Get-RequiredControl -Name 'BrowseBridgeButton'
$bridgePathText = Get-RequiredControl -Name 'BridgePathText'
$renderOutputTextBox = Get-RequiredControl -Name 'RenderOutputTextBox'
$browseRenderOutputButton = Get-RequiredControl -Name 'BrowseRenderOutputButton'
$knowledgeIndexPanel = Get-RequiredControl -Name 'KnowledgeIndexPanel'
$knowledgeIndexModelText = Get-RequiredControl -Name 'KnowledgeIndexModelText'
$knowledgeIndexCountText = Get-RequiredControl -Name 'KnowledgeIndexCountText'
$knowledgeIndexProgressBar = Get-RequiredControl -Name 'KnowledgeIndexProgressBar'
$knowledgeIndexStatusText = Get-RequiredControl -Name 'KnowledgeIndexStatusText'
$knowledgeIndexActionButton = Get-RequiredControl -Name 'KnowledgeIndexActionButton'
$environmentKnowledgeRuntimeText = Get-RequiredControl -Name 'EnvironmentKnowledgeRuntimeText'
$environmentKnowledgeRuntimePathText = Get-RequiredControl -Name 'EnvironmentKnowledgeRuntimePathText'
$environmentActivationCommandText = Get-RequiredControl -Name 'EnvironmentActivationCommandText'
$copyActivationCommandButton = Get-RequiredControl -Name 'CopyActivationCommandButton'
$knowledgeEnvironmentStatusText = Get-RequiredControl -Name 'KnowledgeEnvironmentStatusText'
$knowledgeEnvironmentPathText = Get-RequiredControl -Name 'KnowledgeEnvironmentPathText'
$repairKnowledgeEnvironmentButton = Get-RequiredControl -Name 'RepairKnowledgeEnvironmentButton'
$knowledgeEnvironmentReasonText = Get-RequiredControl -Name 'KnowledgeEnvironmentReasonText'
$knowledgeEnvironmentProgressPanel = Get-RequiredControl -Name 'KnowledgeEnvironmentProgressPanel'
$knowledgeEnvironmentProgressBar = Get-RequiredControl -Name 'KnowledgeEnvironmentProgressBar'
$knowledgeEnvironmentStageText = Get-RequiredControl -Name 'KnowledgeEnvironmentStageText'
$knowledgeEnvironmentLogExpander = Get-RequiredControl -Name 'KnowledgeEnvironmentLogExpander'
$knowledgeEnvironmentLogPathText = Get-RequiredControl -Name 'KnowledgeEnvironmentLogPathText'
$knowledgeEnvironmentLogTextBox = Get-RequiredControl -Name 'KnowledgeEnvironmentLogTextBox'
$knowledgeSourcesSummaryText = Get-RequiredControl -Name 'KnowledgeSourcesSummaryText'
$importKnowledgeFileButton = Get-RequiredControl -Name 'ImportKnowledgeFileButton'
$importKnowledgeFolderButton = Get-RequiredControl -Name 'ImportKnowledgeFolderButton'
$refreshKnowledgeSourcesButton = Get-RequiredControl -Name 'RefreshKnowledgeSourcesButton'
$knowledgeSourcesList = Get-RequiredControl -Name 'KnowledgeSourcesList'
$deleteKnowledgeSourceButton = Get-RequiredControl -Name 'DeleteKnowledgeSourceButton'
$rescanKnowledgeSourcesButton = Get-RequiredControl -Name 'RescanKnowledgeSourcesButton'
$cacheSummaryText = Get-RequiredControl -Name 'CacheSummaryText'
$refreshCacheButton = Get-RequiredControl -Name 'RefreshCacheButton'
$cacheCategoriesList = Get-RequiredControl -Name 'CacheCategoriesList'
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
$openReportButton = Get-RequiredControl -Name 'OpenReportButton'
$copyReportButton = Get-RequiredControl -Name 'CopyReportButton'
$quickRescanButton = Get-RequiredControl -Name 'QuickRescanButton'
$quickRepairButton = Get-RequiredControl -Name 'QuickRepairButton'
$quickCleanupScreenshotsButton = Get-RequiredControl -Name 'QuickCleanupScreenshotsButton'
$quickOpenReportButton = Get-RequiredControl -Name 'QuickOpenReportButton'
$quickCopyReportButton = Get-RequiredControl -Name 'QuickCopyReportButton'
$launchButton = Get-RequiredControl -Name 'LaunchButton'
$recoveryCard = Get-RequiredControl -Name 'RecoveryCard'
$recoveryCheckpointText = Get-RequiredControl -Name 'RecoveryCheckpointText'
$recoverCheckpointOption = Get-RequiredControl -Name 'RecoverCheckpointOption'
$normalLaunchOption = Get-RequiredControl -Name 'NormalLaunchOption'
$layoutRoot = Get-RequiredControl -Name 'LayoutRoot'
$rightVisualRail = Get-RequiredControl -Name 'RightVisualRail'
$navigationColumn = Get-RequiredControl -Name 'NavigationColumn'
$navigationRail = Get-RequiredControl -Name 'NavigationRail'
$sidebarPanel = Get-RequiredControl -Name 'SidebarPanel'
$mainScrollViewer = Get-RequiredControl -Name 'MainScrollViewer'
$overviewNavButton = Get-RequiredControl -Name 'OverviewNavButton'
$environmentNavButton = Get-RequiredControl -Name 'EnvironmentNavButton'
$preflightNavButton = Get-RequiredControl -Name 'PreflightNavButton'
$reportsSettingsNavButton = Get-RequiredControl -Name 'ReportsSettingsNavButton'
$overviewPage = Get-RequiredControl -Name 'OverviewPage'
$environmentPage = Get-RequiredControl -Name 'EnvironmentPage'
$preflightPage = Get-RequiredControl -Name 'PreflightPage'
$reportsSettingsPage = Get-RequiredControl -Name 'ReportsSettingsPage'
$pageTitleText = Get-RequiredControl -Name 'PageTitleText'
$pageSubtitleText = Get-RequiredControl -Name 'PageSubtitleText'
$overviewOverallHeadline = Get-RequiredControl -Name 'OverviewOverallHeadline'
$overviewOverallDetail = Get-RequiredControl -Name 'OverviewOverallDetail'
$overviewHoudiniDot = Get-RequiredControl -Name 'OverviewHoudiniDot'
$overviewHoudiniValueText = Get-RequiredControl -Name 'OverviewHoudiniValueText'
$overviewHoudiniDetailText = Get-RequiredControl -Name 'OverviewHoudiniDetailText'
$overviewMcpDot = Get-RequiredControl -Name 'OverviewMcpDot'
$overviewMcpValueText = Get-RequiredControl -Name 'OverviewMcpValueText'
$overviewMcpDetailText = Get-RequiredControl -Name 'OverviewMcpDetailText'
$overviewCodexDot = Get-RequiredControl -Name 'OverviewCodexDot'
$overviewCodexValueText = Get-RequiredControl -Name 'OverviewCodexValueText'
$overviewCodexDetailText = Get-RequiredControl -Name 'OverviewCodexDetailText'
$overviewEmbeddingDot = Get-RequiredControl -Name 'OverviewEmbeddingDot'
$overviewEmbeddingValueText = Get-RequiredControl -Name 'OverviewEmbeddingValueText'
$overviewEmbeddingDetailText = Get-RequiredControl -Name 'OverviewEmbeddingDetailText'
$optionalArtworkPanel = Get-RequiredControl -Name 'OptionalArtworkPanel'
$optionalArtworkImage = Get-RequiredControl -Name 'OptionalArtworkImage'

foreach ($captionButton in @(
    $minimizeWindowButton,
    $maximizeWindowButton,
    $closeWindowButton
)) {
    [System.Windows.Shell.WindowChrome]::SetIsHitTestVisibleInChrome(
        $captionButton,
        $true
    )
}

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
$script:currentPage = 'overview'
$script:bootstrapProcess = $null
$script:bootstrapPreferences = $null
$script:embeddingData = $inputs.embedding_data
$script:embeddingProcess = $null
$script:embeddingPreferences = $null
$script:embeddingInstallLogPath = ''
$script:knowledgeIndexProcess = $null
$script:knowledgeIndexProcessAction = ''
$script:knowledgeIndexOutputTask = $null
$script:knowledgeIndexErrorTask = $null
$script:knowledgeIndexLastEvent = $null
$script:knowledgeIndexLastIndex = $null
$script:knowledgeIndexProtocolError = ''
$script:knowledgeIndexCancelRequested = $false
$script:knowledgeIndexWindowClosing = $false
$script:knowledgeEnvironmentStatus = $null
$script:knowledgeEnvironmentProcess = $null
$script:knowledgeEnvironmentOutputTask = $null
$script:knowledgeEnvironmentErrorTask = $null
$script:knowledgeEnvironmentProcessAction = ''
$script:knowledgeEnvironmentLogPath = ''
$script:knowledgeEnvironmentLastFailure = ''
$script:knowledgeSources = @()
$script:cachePreview = $null
$renderOutputTextBox.Text = [string]$inputs.render_output
$renderOutputTextBox.ToolTip = if ($renderOutputTextBox.Text) {
    $renderOutputTextBox.Text
} else {
    '留空时使用项目 .runtime\cache'
}

function Update-HiaWindowStateVisual {
    $isMaximized = (
        $window.WindowState -eq [System.Windows.WindowState]::Maximized
    )
    $label = if ($isMaximized) { '还原窗口' } else { '最大化窗口' }
    $maximizeWindowGlyph.Text = if ($isMaximized) { '❐' } else { '□' }
    $maximizeWindowButton.ToolTip = if ($isMaximized) { '还原' } else { '最大化' }
    [System.Windows.Automation.AutomationProperties]::SetName(
        $maximizeWindowButton,
        $label
    )
}

$script:inlineStatusTimer = [System.Windows.Threading.DispatcherTimer]::new()
$script:inlineStatusTimer.Interval = [TimeSpan]::FromSeconds(2.6)
$script:inlineStatusTimer.Add_Tick({
    $script:inlineStatusTimer.Stop()
    $inlineStatusBorder.Visibility = [System.Windows.Visibility]::Collapsed
})

$script:bootstrapTimer = [System.Windows.Threading.DispatcherTimer]::new()
$script:bootstrapTimer.Interval = [TimeSpan]::FromMilliseconds(250)
$script:embeddingTimer = [System.Windows.Threading.DispatcherTimer]::new()
$script:embeddingTimer.Interval = [TimeSpan]::FromMilliseconds(250)
$script:knowledgeIndexTimer = [System.Windows.Threading.DispatcherTimer]::new()
$script:knowledgeIndexTimer.Interval = [TimeSpan]::FromMilliseconds(100)
$script:knowledgeEnvironmentTimer = [System.Windows.Threading.DispatcherTimer]::new()
$script:knowledgeEnvironmentTimer.Interval = [TimeSpan]::FromMilliseconds(250)

function Get-HiaManagedVenvUiPath {
    param([AllowNull()]$Environment)

    if ($null -ne $Environment -and $null -ne $Environment.venv) {
        $reportedPath = [string]$Environment.venv.path
        if (-not [string]::IsNullOrWhiteSpace($reportedPath)) {
            return $reportedPath
        }
    }
    if ($null -ne $script:embeddingData -and $null -ne $script:embeddingData.layout) {
        $contractPath = [string]$script:embeddingData.layout.venv_root
        if (-not [string]::IsNullOrWhiteSpace($contractPath)) {
            return $contractPath
        }
    }
    return [System.IO.Path]::GetFullPath((Join-Path $projectRoot '.venv'))
}

function Get-HiaManagedVenvActivationUiPath {
    if ($null -ne $script:embeddingData -and $null -ne $script:embeddingData.layout) {
        $contractPath = [string]$script:embeddingData.layout.activation_script
        if (-not [string]::IsNullOrWhiteSpace($contractPath)) {
            return $contractPath
        }
    }
    return [System.IO.Path]::GetFullPath(
        (Join-Path $projectRoot '.venv\Scripts\Activate.ps1')
    )
}

function Update-HiaActivationCommandDisplay {
    $command = '.\.venv\Scripts\Activate.ps1'
    $activationPath = Get-HiaManagedVenvActivationUiPath
    $environmentActivationCommandText.Text = $command
    $environmentActivationCommandText.ToolTip = (
        "$command`n项目内路径：$activationPath"
    )
}

function Test-HiaLatestReportAvailable {
    if ([string]::IsNullOrWhiteSpace($script:lastReportPath)) { return $false }
    try {
        $root = [System.IO.Path]::GetFullPath($projectRoot).TrimEnd('\')
        $runtimeRoot = [System.IO.Path]::GetFullPath(
            (Join-Path $root '.runtime')
        ).TrimEnd('\')
        $launcherRoot = [System.IO.Path]::GetFullPath(
            (Join-Path $runtimeRoot 'launcher')
        ).TrimEnd('\')
        $reportPath = [System.IO.Path]::GetFullPath(
            $script:lastReportPath
        ).TrimEnd('\')
        if (
            -not $reportPath.StartsWith(
                $launcherRoot + '\',
                [System.StringComparison]::OrdinalIgnoreCase
            )
        ) {
            return $false
        }
        foreach ($path in @($root, $runtimeRoot, $launcherRoot, $reportPath)) {
            $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
            if (
                ([int]$item.Attributes -band
                    [int][System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
                -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
                    [System.IO.Path]::GetFullPath($item.FullName).TrimEnd('\'),
                    $path
                )
            ) {
                return $false
            }
        }
        return (Get-Item -LiteralPath $reportPath -Force) -is [System.IO.FileInfo]
    } catch {
        return $false
    }
}

function Update-HiaReportActions {
    $enabled = -not $script:isBusy -and (Test-HiaLatestReportAvailable)
    $openReportButton.IsEnabled = $enabled
    $copyReportButton.IsEnabled = $enabled
    $quickOpenReportButton.IsEnabled = $enabled
    $quickCopyReportButton.IsEnabled = $enabled
}

function Initialize-HiaOptionalArtwork {
    $optionalArtworkImage.Source = $null
    $optionalArtworkPanel.Visibility = [System.Windows.Visibility]::Collapsed
    try {
        $root = [System.IO.Path]::GetFullPath($projectRoot).TrimEnd('\')
        $assetsRoot = Join-Path $root 'assets'
        $artworkRoot = Join-Path $assetsRoot 'launcher'
        $artworkPath = [System.IO.Path]::GetFullPath(
            (Join-Path $artworkRoot 'launcher-hero.png')
        )
        if (-not (Test-Path -LiteralPath $artworkPath -PathType Leaf)) { return }
        if (
            -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
                [System.IO.Path]::GetDirectoryName($artworkPath).TrimEnd('\'),
                [System.IO.Path]::GetFullPath($artworkRoot).TrimEnd('\')
            )
        ) {
            throw 'Launcher artwork escaped its project-local directory.'
        }
        $paths = @($root, $assetsRoot, $artworkRoot, $artworkPath)
        for ($index = 0; $index -lt $paths.Count; $index++) {
            $expectedPath = [System.IO.Path]::GetFullPath($paths[$index]).TrimEnd('\')
            $item = Get-Item -LiteralPath $expectedPath -Force -ErrorAction Stop
            if (
                ([int]$item.Attributes -band
                    [int][System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
                -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
                    [System.IO.Path]::GetFullPath($item.FullName).TrimEnd('\'),
                    $expectedPath
                )
            ) {
                throw 'Launcher artwork path is not an ordinary project-local path.'
            }
            if (
                $index -lt ($paths.Count - 1) -and
                $item -isnot [System.IO.DirectoryInfo]
            ) {
                throw 'Launcher artwork parent is not a directory.'
            }
            if (
                $index -eq ($paths.Count - 1) -and
                $item -isnot [System.IO.FileInfo]
            ) {
                throw 'Launcher artwork is not an ordinary file.'
            }
        }

        $bitmap = [System.Windows.Media.Imaging.BitmapImage]::new()
        $bitmap.BeginInit()
        $bitmap.CacheOption = [System.Windows.Media.Imaging.BitmapCacheOption]::OnLoad
        $bitmap.CreateOptions = [System.Windows.Media.Imaging.BitmapCreateOptions]::IgnoreImageCache
        $bitmap.UriSource = [System.Uri]::new($artworkPath, [System.UriKind]::Absolute)
        $bitmap.EndInit()
        $bitmap.Freeze()
        $optionalArtworkImage.Source = $bitmap
        $optionalArtworkPanel.Visibility = [System.Windows.Visibility]::Visible
    } catch {
        $optionalArtworkImage.Source = $null
        $optionalArtworkPanel.Visibility = [System.Windows.Visibility]::Collapsed
    }
}

function Set-OverallState {
    param([Parameter(Mandatory = $true)][ValidateSet('green', 'yellow', 'red', 'neutral', 'busy')][string]$State)

    switch ($State) {
        'green' {
            $overallStatusText.Text = '可以启动'
            $overallStatusDot.Fill = $brushGreen
            $overallStatusBadge.BorderBrush = $brushGreen
            $overallStatusBadge.Background = $surfaceGreen
            $overviewOverallHeadline.Text = '今天也可以顺利开工'
            $overviewOverallDetail.Text = '关键启动条件已经就绪。进入 Houdini 前仍会按当前选择再确认一次。'
        }
        'yellow' {
            $overallStatusText.Text = '存在警告'
            $overallStatusDot.Fill = $brushYellow
            $overallStatusBadge.BorderBrush = $brushYellow
            $overallStatusBadge.Background = $surfaceYellow
            $overviewOverallHeadline.Text = '有几项提醒，仍可继续'
            $overviewOverallDetail.Text = '黄色项目不会无故拦住启动；展开自检项即可看到对应建议。'
        }
        'red' {
            $overallStatusText.Text = '需要处理'
            $overallStatusDot.Fill = $brushRed
            $overallStatusBadge.BorderBrush = $brushRed
            $overallStatusBadge.Background = $surfaceRed
            $overviewOverallHeadline.Text = '先处理阻断项再出发'
            $overviewOverallDetail.Text = '红色项目确实会影响启动。前往自检或报告页查看原因与安全修复入口。'
        }
        'busy' {
            $overallStatusText.Text = '正在检查'
            $overallStatusDot.Fill = $brushCyan
            $overallStatusBadge.BorderBrush = $brushPurple
            $overallStatusBadge.Background = $surfaceNeutral
            $overviewOverallHeadline.Text = '正在梳理启动环境'
            $overviewOverallDetail.Text = 'Big-Chicken 正在核对 Houdini、Bridge、Codex、MCP 与本地知识环境。'
        }
        default {
            $overallStatusText.Text = '等待检查'
            $overallStatusDot.Fill = $brushNeutral
            $overallStatusBadge.BorderBrush = $brushNeutral
            $overallStatusBadge.Background = $surfaceNeutral
            $overviewOverallHeadline.Text = '等你检查环境'
            $overviewOverallDetail.Text = '选择工作环境后，Big-Chicken 会把启动前的关键状态整理在这里。'
        }
    }
}

function Set-HiaLauncherPage {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('overview', 'environment', 'preflight', 'reports')]
        [string]$Page
    )

    $overviewPage.Visibility = [System.Windows.Visibility]::Collapsed
    $environmentPage.Visibility = [System.Windows.Visibility]::Collapsed
    $preflightPage.Visibility = [System.Windows.Visibility]::Collapsed
    $reportsSettingsPage.Visibility = [System.Windows.Visibility]::Collapsed
    $overviewNavButton.IsChecked = $false
    $environmentNavButton.IsChecked = $false
    $preflightNavButton.IsChecked = $false
    $reportsSettingsNavButton.IsChecked = $false

    switch ($Page) {
        'environment' {
            $environmentPage.Visibility = [System.Windows.Visibility]::Visible
            $environmentNavButton.IsChecked = $true
            $pageTitleText.Text = '环境'
            $pageSubtitleText.Text = '选择 Houdini、Bridge、MCP 与本地知识配置'
        }
        'preflight' {
            $preflightPage.Visibility = [System.Windows.Visibility]::Visible
            $preflightNavButton.IsChecked = $true
            $pageTitleText.Text = '自检'
            $pageSubtitleText.Text = '逐项查看启动条件、结果与修复建议'
        }
        'reports' {
            $reportsSettingsPage.Visibility = [System.Windows.Visibility]::Visible
            $reportsSettingsNavButton.IsChecked = $true
            $pageTitleText.Text = '本地知识'
            $pageSubtitleText.Text = '资料、解析环境、索引与项目缓存'
        }
        default {
            $overviewPage.Visibility = [System.Windows.Visibility]::Visible
            $overviewNavButton.IsChecked = $true
            $pageTitleText.Text = '概览'
            $pageSubtitleText.Text = '启动状态、关键环境与今天的创作入口'
        }
    }

    $script:currentPage = $Page
    $mainScrollViewer.ScrollToTop()
}

function Get-HiaOverviewCheckState {
    param([Parameter(Mandatory = $true)][string]$IdPattern)

    if ($null -eq $script:currentResult) {
        return [pscustomobject]@{
            level = 'neutral'
            label = '等待扫描'
            message = '等待检查结果'
        }
    }

    $matches = @($script:currentResult.checks | Where-Object {
        [string]$_.id -match $IdPattern
    })
    if ($matches.Count -eq 0) {
        return [pscustomobject]@{
            level = 'neutral'
            label = '未检查'
            message = '本次结果未包含该项目'
        }
    }

    $level = 'green'
    if (@($matches | Where-Object level -eq 'red').Count -gt 0) {
        $level = 'red'
    } elseif (@($matches | Where-Object level -eq 'yellow').Count -gt 0) {
        $level = 'yellow'
    }
    $messageCheck = @($matches | Where-Object level -eq $level)[0]
    $label = if ($level -eq 'red') {
        '需要处理'
    } elseif ($level -eq 'yellow') {
        '部分可用'
    } else {
        '已就绪'
    }
    return [pscustomobject]@{
        level = $level
        label = $label
        message = [string]$messageCheck.message
    }
}

function Set-HiaOverviewTile {
    param(
        [Parameter(Mandatory = $true)]$Dot,
        [Parameter(Mandatory = $true)]$ValueText,
        [Parameter(Mandatory = $true)]$DetailText,
        [Parameter(Mandatory = $true)][string]$Level,
        [Parameter(Mandatory = $true)][string]$Value,
        [Parameter(Mandatory = $true)][string]$Detail
    )

    $Dot.Fill = if ($Level -eq 'green') {
        $brushGreen
    } elseif ($Level -eq 'yellow') {
        $brushYellow
    } elseif ($Level -eq 'red') {
        $brushRed
    } else {
        $brushNeutral
    }
    $ValueText.Text = $Value
    $ValueText.ToolTip = $Value
    $DetailText.Text = $Detail
    $DetailText.ToolTip = $Detail
}

function Get-HiaSelectedDisplay {
    param(
        [Parameter(Mandatory = $true)]$Combo,
        [Parameter(Mandatory = $true)][string]$Fallback
    )

    $selected = $Combo.SelectedItem
    if ($null -eq $selected) { return $Fallback }
    foreach ($propertyName in @('display', 'version', 'source', 'id')) {
        $property = $selected.PSObject.Properties[$propertyName]
        if ($null -ne $property -and [string]$property.Value) {
            return [string]$property.Value
        }
    }
    return $Fallback
}

function Update-HiaOverviewSummary {
    param([AllowEmptyString()][string]$FailureMessage = '')

    if ($FailureMessage) {
        Set-HiaOverviewTile `
            -Dot $overviewHoudiniDot `
            -ValueText $overviewHoudiniValueText `
            -DetailText $overviewHoudiniDetailText `
            -Level 'red' `
            -Value '检查未完成' `
            -Detail $FailureMessage
        Set-HiaOverviewTile `
            -Dot $overviewMcpDot `
            -ValueText $overviewMcpValueText `
            -DetailText $overviewMcpDetailText `
            -Level 'red' `
            -Value '检查未完成' `
            -Detail $FailureMessage
        Set-HiaOverviewTile `
            -Dot $overviewCodexDot `
            -ValueText $overviewCodexValueText `
            -DetailText $overviewCodexDetailText `
            -Level 'red' `
            -Value '检查未完成' `
            -Detail $FailureMessage
        Set-HiaOverviewTile `
            -Dot $overviewEmbeddingDot `
            -ValueText $overviewEmbeddingValueText `
            -DetailText $overviewEmbeddingDetailText `
            -Level 'red' `
            -Value '检查未完成' `
            -Detail $FailureMessage
        return
    }

    $houdiniState = Get-HiaOverviewCheckState -IdPattern '^(houdini|hython)\.'
    $houdiniDisplay = Get-HiaSelectedDisplay -Combo $houdiniCombo -Fallback $houdiniState.label
    if ($houdiniDisplay -ne $houdiniState.label -and $houdiniDisplay -notmatch '^Houdini ') {
        $houdiniDisplay = "Houdini $houdiniDisplay"
    }
    Set-HiaOverviewTile `
        -Dot $overviewHoudiniDot `
        -ValueText $overviewHoudiniValueText `
        -DetailText $overviewHoudiniDetailText `
        -Level $houdiniState.level `
        -Value $houdiniDisplay `
        -Detail $houdiniState.message

    $mcpState = Get-HiaOverviewCheckState -IdPattern '^(hia_mcp_v2|fxhoudinimcp|mcp)\.'
    Set-HiaOverviewTile `
        -Dot $overviewMcpDot `
        -ValueText $overviewMcpValueText `
        -DetailText $overviewMcpDetailText `
        -Level $mcpState.level `
        -Value (Get-HiaSelectedDisplay -Combo $mcpBackendCombo -Fallback $mcpState.label) `
        -Detail $mcpState.message

    $codexState = Get-HiaOverviewCheckState -IdPattern '^codex\.'
    Set-HiaOverviewTile `
        -Dot $overviewCodexDot `
        -ValueText $overviewCodexValueText `
        -DetailText $overviewCodexDetailText `
        -Level $codexState.level `
        -Value $codexState.label `
        -Detail $codexState.message

    $embeddingState = Get-HiaOverviewCheckState -IdPattern '^embedding\.'
    $embeddingDisplay = Get-HiaSelectedDisplay `
        -Combo $embeddingProfileCombo `
        -Fallback $embeddingState.label
    Set-HiaOverviewTile `
        -Dot $overviewEmbeddingDot `
        -ValueText $overviewEmbeddingValueText `
        -DetailText $overviewEmbeddingDetailText `
        -Level $embeddingState.level `
        -Value $embeddingDisplay `
        -Detail $embeddingState.message
}

function Update-ResponsiveLayout {
    $compact = $window.ActualWidth -gt 0 -and $window.ActualWidth -lt 820
    if ($script:compactLayout -eq $compact) { return }
    $script:compactLayout = $compact

    if ($compact) {
        $navigationColumn.Width = [System.Windows.GridLength]::new(190)
        $sidebarPanel.Margin = [System.Windows.Thickness]::new(14, 18, 14, 14)
        $rightVisualRail.Width = 160
        $rightVisualRail.Margin = [System.Windows.Thickness]::new(0, 12, 0, 0)
        return
    }

    $navigationColumn.Width = [System.Windows.GridLength]::new(212)
    $sidebarPanel.Margin = [System.Windows.Thickness]::new(18, 22, 18, 18)
    $rightVisualRail.Width = 174
    $rightVisualRail.Margin = [System.Windows.Thickness]::new(0, 16, 0, 0)
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

function Get-ComboEmbeddingProfile {
    $selected = $embeddingProfileCombo.SelectedItem
    if ($null -ne $selected) {
        $idProperty = $selected.PSObject.Properties['id']
        if ($null -ne $idProperty) { return [string]$idProperty.Value }
    }
    if ($null -ne $script:embeddingData) {
        return [string]$script:embeddingData.contract.default_profile
    }
    return ''
}

function Get-ComboEmbeddingDevice {
    $selected = $embeddingDeviceCombo.SelectedItem
    if ($null -eq $selected) { return 'auto' }
    $idProperty = $selected.PSObject.Properties['id']
    if ($null -eq $idProperty) { return 'auto' }
    return Resolve-HiaEmbeddingDevice -Device ([string]$idProperty.Value)
}

function Get-RenderOutputPath {
    return ([string]$renderOutputTextBox.Text).Trim()
}

function Get-HiaKnowledgeIndexValue {
    param(
        [AllowNull()]$Index,
        [Parameter(Mandatory = $true)][string]$Name,
        [AllowNull()]$Default = $null
    )

    if ($null -eq $Index) { return $Default }
    $property = $Index.PSObject.Properties[$Name]
    if ($null -eq $property) { return $Default }
    return $property.Value
}

function Get-HiaKnowledgeIndexSelectedModelLabel {
    if ($null -eq $script:embeddingData) { return '尚未读取模型' }
    try {
        $profile = Get-HiaEmbeddingProfileContract `
            -EmbeddingData $script:embeddingData `
            -Profile (Get-ComboEmbeddingProfile)
        return [string]$profile.label
    } catch {
        return '尚未读取模型'
    }
}

function Update-HiaKnowledgeIndexActionButton {
    if ($null -ne $script:knowledgeIndexProcess) {
        if ($script:knowledgeIndexProcessAction -eq 'build') {
            $knowledgeIndexActionButton.Content = '取消'
            $knowledgeIndexActionButton.IsEnabled = $true
            [System.Windows.Automation.AutomationProperties]::SetName(
                $knowledgeIndexActionButton,
                '取消本次本地知识索引构建'
            )
        } else {
            $knowledgeIndexActionButton.Content = '正在刷新…'
            $knowledgeIndexActionButton.IsEnabled = $false
        }
        return
    }

    $available = [bool](Get-HiaKnowledgeIndexValue `
        -Index $script:knowledgeIndexLastIndex `
        -Name 'available' `
        -Default $false)
    $complete = [bool](Get-HiaKnowledgeIndexValue `
        -Index $script:knowledgeIndexLastIndex `
        -Name 'complete' `
        -Default $false)
    if ($available -and $complete) {
        $knowledgeIndexActionButton.Content = '索引已完成'
        $knowledgeIndexActionButton.IsEnabled = $false
        [System.Windows.Automation.AutomationProperties]::SetName(
            $knowledgeIndexActionButton,
            '本地知识索引已完成'
        )
        return
    }

    $knowledgeIndexActionButton.Content = '构建/继续索引'
    $knowledgeIndexActionButton.IsEnabled = ($available -and -not $script:isBusy)
    [System.Windows.Automation.AutomationProperties]::SetName(
        $knowledgeIndexActionButton,
        '构建或继续本地知识索引'
    )
}

function Set-HiaKnowledgeIndexDisplay {
    param(
        [AllowNull()]$Index = $null,
        [AllowEmptyString()][string]$Message = '',
        [switch]$Reset
    )

    if ($Reset) {
        $script:knowledgeIndexLastIndex = $null
        $script:knowledgeIndexLastEvent = $null
    } elseif ($null -ne $Index) {
        $script:knowledgeIndexLastIndex = $Index
    }
    $current = $script:knowledgeIndexLastIndex

    $model = [string](Get-HiaKnowledgeIndexValue `
        -Index $current `
        -Name 'model_id' `
        -Default '')
    if (-not $model) { $model = Get-HiaKnowledgeIndexSelectedModelLabel }
    $dimension = [long](Get-HiaKnowledgeIndexValue `
        -Index $current `
        -Name 'dim' `
        -Default 0)
    $modelDisplay = if ($dimension -gt 0) { "$model · ${dimension}D" } else { $model }
    $knowledgeIndexModelText.Text = $modelDisplay
    $knowledgeIndexModelText.ToolTip = $modelDisplay

    $total = [long](Get-HiaKnowledgeIndexValue -Index $current -Name 'total_chunks' -Default 0)
    $vector = [long](Get-HiaKnowledgeIndexValue -Index $current -Name 'vector_chunks' -Default 0)
    $pending = [long](Get-HiaKnowledgeIndexValue -Index $current -Name 'pending_chunks' -Default 0)
    $complete = [bool](Get-HiaKnowledgeIndexValue -Index $current -Name 'complete' -Default $false)
    $available = [bool](Get-HiaKnowledgeIndexValue -Index $current -Name 'available' -Default $false)
    $percentage = if ($total -gt 0) {
        [Math]::Min(100, [Math]::Floor(($vector * 100.0) / $total))
    } elseif ($complete -and $available) {
        100
    } else {
        0
    }
    $knowledgeIndexCountText.Text = "$vector / $total · $percentage% · 剩余 $pending"
    $knowledgeIndexProgressBar.Value = [double]$percentage
    $knowledgeIndexProgressBar.Foreground = if ($complete -and $available) {
        $brushGreen
    } else {
        $brushCyan
    }

    if (-not $Message) {
        if ($null -ne $script:knowledgeIndexProcess) {
            $Message = if ($script:knowledgeIndexProcessAction -eq 'build') {
                '正在构建索引；每个已提交批次都会保留。'
            } else {
                '正在读取索引状态…'
            }
        } elseif ($null -eq $current) {
            $Message = '重新扫描后显示索引进度。'
        } elseif (-not $available) {
            $reason = [string](Get-HiaKnowledgeIndexValue `
                -Index $current `
                -Name 'fallback_reason' `
                -Default '')
            $Message = '向量模型暂不可用；FTS5 lexical 检索可继续工作，Houdini 仍可启动。'
            if ($reason) { $Message += " 原因：$reason" }
        } elseif ($complete) {
            $Message = '索引已完成。'
        } else {
            $Message = "索引未完成；剩余 $pending 个，可手动继续。"
        }
    }
    $knowledgeIndexStatusText.Text = $Message
    $knowledgeIndexStatusText.ToolTip = $Message
    $knowledgeIndexStatusText.Foreground = if (
        $null -ne $script:knowledgeIndexLastEvent -and
        [string]$script:knowledgeIndexLastEvent.event -eq 'error'
    ) {
        $brushRed
    } elseif ($null -ne $script:knowledgeIndexProcess) {
        $brushCyan
    } elseif (-not $available -and $null -ne $current) {
        $brushYellow
    } elseif ($complete -and $available) {
        $brushGreen
    } else {
        $brushTextSecondary
    }
    Update-HiaKnowledgeIndexActionButton
}

function Format-HiaByteCount {
    param([Parameter(Mandatory = $true)][long]$Bytes)

    if ($Bytes -lt 1KB) { return "$Bytes B" }
    if ($Bytes -lt 1MB) { return ('{0:N1} KiB' -f ($Bytes / 1KB)) }
    if ($Bytes -lt 1GB) { return ('{0:N1} MiB' -f ($Bytes / 1MB)) }
    return ('{0:N2} GiB' -f ($Bytes / 1GB))
}

function Invoke-HiaLauncherJsonCli {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('hia-cache.ps1', 'hia-knowledge.ps1')]
        [string]$ScriptName,
        [string[]]$Arguments = @(),
        [ValidateRange(2, 600)][int]$TimeoutSeconds = 60
    )

    $scriptsRoot = [System.IO.Path]::GetFullPath(
        (Join-Path $projectRoot 'scripts')
    ).TrimEnd('\')
    $scriptPath = [System.IO.Path]::GetFullPath(
        (Join-Path $scriptsRoot $ScriptName)
    )
    if (
        -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
            [System.IO.Path]::GetDirectoryName($scriptPath).TrimEnd('\'),
            $scriptsRoot
        ) -or
        -not (Test-Path -LiteralPath $scriptPath -PathType Leaf)
    ) {
        throw "缺少项目本地命令：scripts\$ScriptName"
    }
    $powershellExe = Join-Path $env:SystemRoot (
        'System32\WindowsPowerShell\v1.0\powershell.exe'
    )
    $processResult = Invoke-HiaProcess `
        -FilePath $powershellExe `
        -Arguments (@(
            '-NoProfile',
            '-NonInteractive',
            '-ExecutionPolicy', 'Bypass',
            '-File', $scriptPath
        ) + @($Arguments)) `
        -TimeoutSeconds $TimeoutSeconds `
        -WorkingDirectory $projectRoot
    if (
        -not $processResult.started -or
        $processResult.timed_out -or
        $null -eq $processResult.exit_code
    ) {
        throw "项目本地命令未能在 $TimeoutSeconds 秒内完成。"
    }
    $payload = $null
    try {
        $payload = ([string]$processResult.stdout).Trim() |
            ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw '项目本地命令没有返回有效的 JSON 结果。'
    }
    if ([int]$processResult.exit_code -ne 0 -or $payload.ok -eq $false) {
        $detail = ''
        if ($null -ne $payload.error) {
            $detail = [string]$payload.error.message
        }
        if ([string]::IsNullOrWhiteSpace($detail)) {
            $detail = "命令退出码 $($processResult.exit_code)"
        }
        throw (ConvertTo-HiaRedactedText -Text $detail)
    }
    return $payload
}

function Set-HiaCacheDisplay {
    param([Parameter(Mandatory = $true)]$Payload)

    $script:cachePreview = $Payload
    $cacheCategoriesList.Items.Clear()
    foreach ($category in @($Payload.categories)) {
        $reason = @($category.block_reasons) -join '；'
        $sizeLabel = if ([bool]$category.blocked) {
            '已保护'
        } elseif (-not [bool]$category.exists) {
            '未创建'
        } else {
            Format-HiaByteCount -Bytes ([long]$category.bytes)
        }
        $relativePath = if ([string]$category.id -eq 'embedding-runtime') {
            '.runtime/cache/embedding（安装/运行缓存，不含模型下载）'
        } elseif ([string]$category.id -eq 'embedding-downloads') {
            '.runtime/cache/embedding/huggingface'
        } else {
            ".runtime/cache/$([string]$category.id)"
        }
        $tooltip = [string]$category.target_path
        if ($reason) { $tooltip += "`n已保护：$reason" }
        [void]$cacheCategoriesList.Items.Add([pscustomobject]@{
            id = [string]$category.id
            label = [string]$category.label
            relative_path = $relativePath
            size_label = $sizeLabel
            tooltip = $tooltip
            blocked = [bool]$category.blocked
        })
    }
    $total = Format-HiaByteCount -Bytes ([long]$Payload.total_bytes)
    $blockedCount = @($Payload.categories | Where-Object blocked).Count
    $cacheSummaryText.Text = if ($blockedCount -gt 0) {
        "可安全预览 $total；另有 $blockedCount 类因路径链接或安装锁而受保护。"
    } else {
        "已扫描 $($Payload.categories.Count) 类托管缓存，共 $total。"
    }
    Update-HiaCacheActions
}

function Refresh-HiaCacheDisplay {
    param([switch]$Quiet)

    try {
        $payload = Invoke-HiaLauncherJsonCli `
            -ScriptName 'hia-cache.ps1' `
            -Arguments @('-Action', 'list') `
            -TimeoutSeconds 90
        Set-HiaCacheDisplay -Payload $payload
        if (-not $Quiet) {
            Show-InlineStatus -Kind 'success' -Transient -Text '项目缓存分类已刷新。'
        }
    } catch {
        $script:cachePreview = $null
        $cacheCategoriesList.Items.Clear()
        $cacheSummaryText.Text = '缓存状态暂不可用。'
        Update-HiaCacheActions
        if (-not $Quiet) {
            Show-InlineStatus -Kind 'error' -Text (
                "无法读取项目缓存：$($_.Exception.Message)"
            )
        }
    }
}

function Get-HiaKnowledgeEnvironmentAction {
    param(
        [AllowNull()]$Environment,
        [AllowEmptyString()][string]$SelectedProfile = ''
    )

    $modelInstalled = $false
    if ($null -ne $Environment) {
        $modelInstalled = @($Environment.models.items | Where-Object {
            $_.installed -eq $true
        }).Count -gt 0
    }
    if (
        $null -eq $Environment -or
        [string]$Environment.state -eq 'missing'
    ) {
        if ($modelInstalled) {
            return 'environment-install-embedding'
        }
        return 'environment-install'
    }
    if ([string]$Environment.state -ne 'ready') {
        if ($modelInstalled) {
            return 'environment-repair-embedding'
        }
        return 'environment-repair'
    }
    if (
        $modelInstalled -and
        (
            -not [bool]$Environment.torch.installed -or
            -not [bool]$Environment.embedding_worker.installed
        )
    ) {
        return 'environment-repair-embedding'
    }
    return ''
}

function Get-HiaKnowledgeEnvironmentReason {
    param(
        [AllowNull()]$Environment,
        [AllowEmptyString()][string]$SelectedProfile = ''
    )

    if ($null -eq $Environment) {
        return '状态尚未读取；启动器可以检查并准备项目内 Python、uv、共享 venv 与 pypdf。'
    }
    if ([string]$Environment.state -eq 'missing') {
        if (
            (Get-HiaKnowledgeEnvironmentAction `
                -Environment $Environment `
                -SelectedProfile $SelectedProfile) -like
                    'environment-*-embedding'
        ) {
            return '项目本地 venv 尚未安装，但所选模型完整存在；一次修复会准备解析器与 CPU/CUDA 向量运行时并复用模型。'
        }
        return 'HIA Python 环境位于项目根目录 .venv；受管 CPython、uv、模型与缓存位于 .runtime。点击一次即可完成基础环境准备。'
    }
    if ([string]$Environment.state -ne 'ready') {
        $reasons = [System.Collections.Generic.List[string]]::new()
        if (-not [bool]$Environment.venv.portable) {
            [void]$reasons.Add('现有 venv 仍依赖项目外 Python')
        }
        if (-not [bool]$Environment.python.managed_marker.valid) {
            [void]$reasons.Add('缺少有效的 HIA 受管标记')
        }
        if (-not [bool]$Environment.uv.available) {
            [void]$reasons.Add('项目本地 uv 尚未就绪')
        }
        if (-not [bool]$Environment.parser.installed) {
            [void]$reasons.Add('pypdf 尚未安装')
        }
        if ($reasons.Count -eq 0) {
            [void]$reasons.Add('严格的项目本地环境验证未通过')
        }
        $repairScope = if (
            (Get-HiaKnowledgeEnvironmentAction `
                -Environment $Environment `
                -SelectedProfile $SelectedProfile) -like
                    'environment-*-embedding'
        ) {
            '。一次修复会重建 managed venv、pypdf、PyTorch 与 worker，并复用现有模型和项目缓存；旧 venv 会保存在项目 .runtime 内。'
        } else {
            '。点击修复后会先验证新环境，再把旧 venv 保存在项目 .runtime 内。'
        }
        return (
            ($reasons -join '；') +
            $repairScope
        )
    }

    $action = Get-HiaKnowledgeEnvironmentAction `
        -Environment $Environment `
        -SelectedProfile $SelectedProfile
    if ($action -like 'environment-*-embedding') {
        return (
            '所选模型已存在；这次修复会一次准备 managed Python、共享 venv、pypdf、' +
            'PyTorch 与 embedding worker，并按当前 CPU/CUDA 选择复用模型和项目缓存。'
        )
    }
    if ([string]$Environment.embedding_mode -eq 'fts5') {
        return '基础环境已就绪；当前使用 FTS5。未安装向量模型也不会阻断 Houdini。'
    }
    return '项目本地 Python、uv、解析器与知识向量运行时均已通过验证。'
}

function Update-HiaKnowledgeEnvironmentActions {
    $action = Get-HiaKnowledgeEnvironmentAction `
        -Environment $script:knowledgeEnvironmentStatus `
        -SelectedProfile (Get-ComboEmbeddingProfile)
    $running = (
        $null -ne $script:knowledgeEnvironmentProcess -or
        $null -ne $script:embeddingProcess
    )
    $label = switch ($action) {
        'environment-install' {
            if ($script:knowledgeEnvironmentLastFailure) {
                '重试安装本地知识环境'
            } else {
                '安装本地知识环境'
            }
        }
        'environment-repair' {
            if ($script:knowledgeEnvironmentLastFailure) {
                '重试修复本地知识环境'
            } else {
                '修复本地知识环境'
            }
        }
        'environment-install-embedding' {
            if ($script:knowledgeEnvironmentLastFailure) {
                '重试完整修复本地知识环境'
            } else {
                '完整修复本地知识环境'
            }
        }
        'environment-repair-embedding' {
            if ($script:knowledgeEnvironmentLastFailure) {
                '重试完整修复本地知识环境'
            } else {
                '完整修复本地知识环境'
            }
        }
        default { '本地知识环境已就绪' }
    }
    if ($running) {
        $label = '正在准备…'
    }
    $repairKnowledgeEnvironmentButton.Content = $label
    [System.Windows.Automation.AutomationProperties]::SetName(
        $repairKnowledgeEnvironmentButton,
        $label
    )
    $repairKnowledgeEnvironmentButton.IsEnabled = (
        -not $script:isBusy -and
        -not $running -and
        -not [string]::IsNullOrWhiteSpace($action)
    )
    Update-RepairButton
}

function Set-HiaKnowledgeEnvironmentDisplay {
    param([AllowNull()]$Environment)

    $script:knowledgeEnvironmentStatus = $Environment
    $selectedProfile = Get-ComboEmbeddingProfile
    if ($null -eq $Environment) {
        $summary = '本地知识环境暂不可用；可使用“安装 / 修复”准备项目内 Python 与解析器。'
        $path = Get-HiaManagedVenvUiPath -Environment $null
        $knowledgeEnvironmentStatusText.Text = $summary
        $knowledgeEnvironmentPathText.Text = $path
        $knowledgeEnvironmentPathText.ToolTip = $path
        $environmentKnowledgeRuntimeText.Text = '未就绪 · FTS5 仍不阻断 Houdini'
        $environmentKnowledgeRuntimeText.ToolTip = $summary
        $environmentKnowledgeRuntimePathText.Text = $path
        $environmentKnowledgeRuntimePathText.ToolTip = $path
        Update-HiaActivationCommandDisplay
        $knowledgeEnvironmentReasonText.Text = Get-HiaKnowledgeEnvironmentReason `
            -Environment $null `
            -SelectedProfile $selectedProfile
        Update-HiaKnowledgeEnvironmentActions
        return
    }

    $python = $Environment.python
    $uv = $Environment.uv
    $parser = $Environment.parser
    $torch = $Environment.torch
    $venv = $Environment.venv
    $stateLabel = switch ([string]$Environment.state) {
        'ready' { '已就绪' }
        'missing' { '未安装' }
        default { '需要修复' }
    }
    $pythonLabel = if ([bool]$python.available) {
        "Python $([string]$python.version) · $([int]$python.bits) 位"
    } else {
        'Python 缺失'
    }
    $uvLabel = if ([bool]$uv.available) {
        "uv $([string]$uv.version)"
    } else {
        'uv 缺失'
    }
    $parserLabel = if ([bool]$parser.installed) {
        "pypdf $([string]$parser.version)"
    } else {
        'pypdf 未安装'
    }
    $torchLabel = if ([bool]$torch.installed) {
        "PyTorch $([string]$torch.version)"
    } else {
        'PyTorch 未安装（FTS5 可用）'
    }
    $deviceLabel = if ([bool]$torch.cuda_available) {
        "CUDA · $([string]$torch.gpu_name)"
    } elseif ([bool]$torch.installed) {
        'CPU embedding'
    } else {
        'FTS5 lexical'
    }
    $summary = "$stateLabel · $pythonLabel · $uvLabel · $parserLabel · $torchLabel · $deviceLabel"
    $path = Get-HiaManagedVenvUiPath -Environment $Environment
    $knowledgeEnvironmentStatusText.Text = $summary
    $knowledgeEnvironmentStatusText.ToolTip = $summary
    $knowledgeEnvironmentPathText.Text = $path
    $knowledgeEnvironmentPathText.ToolTip = $path
    $environmentKnowledgeRuntimeText.Text = "$stateLabel · $pythonLabel · $deviceLabel"
    $environmentKnowledgeRuntimeText.ToolTip = $summary
    $environmentKnowledgeRuntimePathText.Text = $path
    $environmentKnowledgeRuntimePathText.ToolTip = $path
    Update-HiaActivationCommandDisplay
    $knowledgeEnvironmentReasonText.Text = Get-HiaKnowledgeEnvironmentReason `
        -Environment $Environment `
        -SelectedProfile $selectedProfile
    $knowledgeEnvironmentReasonText.ToolTip = $knowledgeEnvironmentReasonText.Text
    Update-HiaKnowledgeEnvironmentActions
}

function Set-HiaKnowledgeSourcesDisplay {
    param([AllowNull()]$Sources)

    $knowledgeSourcesList.Items.Clear()
    $script:knowledgeSources = @()
    if ($null -eq $Sources) {
        $knowledgeSourcesSummaryText.Text = (
            '资料列表暂不可用；支持 md、txt、html、htm、srt、vtt、pdf。'
        )
        Update-HiaKnowledgeSourceActions
        return
    }
    foreach ($source in @($Sources.items)) {
        $sizeLabel = Format-HiaByteCount -Bytes ([long]$source.size_bytes)
        $statusLabel = if ([string]$source.index_status -eq 'indexed') {
            "已索引 · $([int]$source.chunk_count) 段"
        } else {
            '待索引'
        }
        $origin = [string]$source.source
        if ([string]::IsNullOrWhiteSpace($origin)) { $origin = '原始来源未记录' }
        $details = "{0} · {1} · {2}" -f (
            ([string]$source.format).ToUpperInvariant()
        ), $sizeLabel, $origin
        $tooltip = @(
            [string]$source.name,
            "托管副本：$([string]$source.managed_path)",
            "原始来源：$origin",
            '删除托管副本不会删除原文件。'
        ) -join "`n"
        $view = [pscustomobject]@{
            source_id = [string]$source.source_id
            name = [string]$source.name
            details = $details
            status_label = $statusLabel
            tooltip = $tooltip
        }
        $script:knowledgeSources += $view
        [void]$knowledgeSourcesList.Items.Add($view)
    }
    $knowledgeSourcesSummaryText.Text = (
        "已托管 $([int]$Sources.total) 份资料；支持 md、txt、html、htm、srt、vtt、pdf。删除托管副本不会删除原文件。"
    )
    Update-HiaKnowledgeSourceActions
}

function Refresh-HiaKnowledgeDisplay {
    param([switch]$Quiet)

    try {
        $payload = Invoke-HiaLauncherJsonCli `
            -ScriptName 'hia-knowledge.ps1' `
            -Arguments @('status') `
            -TimeoutSeconds 90
        Set-HiaKnowledgeEnvironmentDisplay `
            -Environment $payload.result.environment
        Set-HiaKnowledgeSourcesDisplay -Sources $payload.result.sources
        if (-not $Quiet) {
            Show-InlineStatus -Kind 'success' -Transient -Text '本地知识状态已刷新。'
        }
    } catch {
        Set-HiaKnowledgeEnvironmentDisplay -Environment $null
        Set-HiaKnowledgeSourcesDisplay -Sources $null
        if (-not $Quiet) {
            Show-InlineStatus -Kind 'error' -Text (
                "无法读取本地知识状态：$($_.Exception.Message)"
            )
        }
    }
}

function Invoke-HiaKnowledgeAction {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet(
            'import-file',
            'import-folder',
            'delete',
            'rescan'
        )]
        [string]$Action,
        [AllowEmptyString()][string]$Path = '',
        [AllowEmptyString()][string]$SourceId = ''
    )

    $arguments = @($Action)
    if ($Path) { $arguments += @('-Path', $Path) }
    if ($SourceId) { $arguments += @('-SourceId', $SourceId) }
    return Invoke-HiaLauncherJsonCli `
        -ScriptName 'hia-knowledge.ps1' `
        -Arguments $arguments `
        -TimeoutSeconds 180
}

function Set-HiaKnowledgeEnvironmentLogPath {
    param([AllowEmptyString()][string]$Path = '')

    $script:knowledgeEnvironmentLogPath = $Path
    if ([string]::IsNullOrWhiteSpace($Path)) {
        $knowledgeEnvironmentLogExpander.Visibility = (
            [System.Windows.Visibility]::Collapsed
        )
        $knowledgeEnvironmentLogPathText.Text = '尚未生成安装日志'
        $knowledgeEnvironmentLogPathText.ToolTip = '尚未生成安装日志'
        $knowledgeEnvironmentLogTextBox.Text = '等待安装日志…'
        return
    }
    $knowledgeEnvironmentLogExpander.Visibility = (
        [System.Windows.Visibility]::Visible
    )
    $knowledgeEnvironmentLogPathText.Text = $Path
    $knowledgeEnvironmentLogPathText.ToolTip = $Path
    $knowledgeEnvironmentLogTextBox.Text = '正在读取安装日志…'
    $validatedLogPath = Resolve-HiaEmbeddingInstallLogForRead `
        -InstallLogPath $Path
    if (-not [string]::IsNullOrWhiteSpace($validatedLogPath)) {
        $script:lastReportPath = $validatedLogPath
        $reportPathTextBox.Text = $validatedLogPath
        $reportPathTextBox.ToolTip = $validatedLogPath
        Update-HiaReportActions
    }
}

function Get-HiaKnowledgeEnvironmentStageFromLog {
    param([AllowEmptyString()][string]$LogText = '')

    if ($LogText -match '(?i)selected embedding model') {
        return '正在校验所选模型；已有完整模型会直接复用'
    }
    if ($LogText -match '(?i)embedding worker') {
        return '正在补全 embedding worker'
    }
    if ($LogText -match '(?i)CUDA PyTorch') {
        return '正在准备并验证 CUDA PyTorch'
    }
    if ($LogText -match '(?i)PDF parser|pypdf') {
        return '正在安装并验证 pypdf'
    }
    if ($LogText -match '(?i)relocatable knowledge environment|legacy venv|shared venv') {
        return '正在迁移并验证共享 venv'
    }
    if ($LogText -match '(?i)managed Python') {
        return '正在准备项目内 managed Python'
    }
    if ($LogText -match '(?i)Astral uv|uv version probe') {
        return '正在准备项目本地 uv'
    }
    return '正在准备项目本地工具链'
}

function Update-HiaKnowledgeEnvironmentLog {
    $validatedLogPath = Resolve-HiaEmbeddingInstallLogForRead `
        -InstallLogPath $script:knowledgeEnvironmentLogPath
    if ([string]::IsNullOrWhiteSpace($validatedLogPath)) {
        return
    }
    $script:lastReportPath = $validatedLogPath
    $reportPathTextBox.Text = $validatedLogPath
    $reportPathTextBox.ToolTip = $validatedLogPath
    Update-HiaReportActions
    try {
        $text = [System.IO.File]::ReadAllText(
            $validatedLogPath,
            [System.Text.Encoding]::UTF8
        )
        $safe = ConvertTo-HiaRedactedText -Text $text
        if ($safe.Length -gt 12000) {
            $safe = '…' + $safe.Substring($safe.Length - 12000)
        }
        $knowledgeEnvironmentLogTextBox.Text = $safe
        $knowledgeEnvironmentLogTextBox.ScrollToEnd()
        $knowledgeEnvironmentStageText.Text = (
            Get-HiaKnowledgeEnvironmentStageFromLog -LogText $safe
        )
    } catch {
        $knowledgeEnvironmentLogTextBox.Text = '安装日志正在写入；稍后会自动刷新。'
    }
}

function Set-HiaKnowledgeEnvironmentProgress {
    param(
        [Parameter(Mandatory = $true)][bool]$Running,
        [AllowEmptyString()][string]$Stage = ''
    )

    $knowledgeEnvironmentProgressPanel.Visibility = if ($Running) {
        [System.Windows.Visibility]::Visible
    } else {
        [System.Windows.Visibility]::Collapsed
    }
    $knowledgeEnvironmentProgressBar.IsIndeterminate = $Running
    if (-not [string]::IsNullOrWhiteSpace($Stage)) {
        $knowledgeEnvironmentStageText.Text = $Stage
    }
    Update-HiaKnowledgeEnvironmentActions
}

function Complete-HiaKnowledgeEnvironmentRepair {
    if (
        $null -eq $script:knowledgeEnvironmentProcess -or
        -not $script:knowledgeEnvironmentProcess.HasExited
    ) {
        return
    }
    $script:knowledgeEnvironmentTimer.Stop()
    $exitCode = [int]$script:knowledgeEnvironmentProcess.ExitCode
    $completedAction = $script:knowledgeEnvironmentProcessAction
    $stdout = ''
    $stderr = ''
    try {
        if (
            $null -ne $script:knowledgeEnvironmentOutputTask -and
            $script:knowledgeEnvironmentOutputTask.Wait(2000)
        ) {
            $stdout = [string]$script:knowledgeEnvironmentOutputTask.Result
        }
        if (
            $null -ne $script:knowledgeEnvironmentErrorTask -and
            $script:knowledgeEnvironmentErrorTask.Wait(2000)
        ) {
            $stderr = [string]$script:knowledgeEnvironmentErrorTask.Result
        }
    } catch { }
    Update-HiaKnowledgeEnvironmentLog
    $script:knowledgeEnvironmentProcess.Dispose()
    $script:knowledgeEnvironmentProcess = $null
    $script:knowledgeEnvironmentOutputTask = $null
    $script:knowledgeEnvironmentErrorTask = $null
    $script:knowledgeEnvironmentProcessAction = ''
    Set-BusyState -Busy $false
    Set-HiaKnowledgeEnvironmentProgress -Running $false

    if ($exitCode -eq 0) {
        Refresh-HiaKnowledgeDisplay -Quiet
        if (
            $null -eq $script:knowledgeEnvironmentStatus -or
            [string]$script:knowledgeEnvironmentStatus.state -ne 'ready'
        ) {
            $failure = (
                '命令已结束，但严格受管环境复检仍未通过。' +
                '请展开日志查看网络、代理、磁盘或旧 venv 迁移阶段。'
            )
            $script:knowledgeEnvironmentLastFailure = $failure
            $knowledgeEnvironmentReasonText.Text = $failure
            $knowledgeEnvironmentLogExpander.IsExpanded = $true
            Update-HiaKnowledgeEnvironmentActions
            Show-InlineStatus -Kind 'error' -Text (
                '本地知识环境命令已结束，但刷新验证未通过；可直接重试，详情见安装日志。'
            )
            return
        }
        $script:knowledgeEnvironmentLastFailure = ''
        Invoke-GuiScan `
            -PreferredHoudini (Get-ComboPath -Combo $houdiniCombo) `
            -PreferredBridge '' `
            -PreferredBackend (Get-ComboBackend) `
            -PreferredEmbedding (Get-ComboEmbeddingProfile) `
            -PreferredEmbeddingDevice (Get-ComboEmbeddingDevice)
        Refresh-HiaKnowledgeDisplay -Quiet
        $nextAction = Get-HiaKnowledgeEnvironmentAction `
            -Environment $script:knowledgeEnvironmentStatus `
            -SelectedProfile (Get-ComboEmbeddingProfile)
        if (
            $completedAction -like 'environment-*-embedding' -and
            $nextAction -like 'environment-*-embedding'
        ) {
            $failure = (
                '完整修复命令已结束，但 PyTorch 或 embedding worker 复检仍未通过。' +
                '模型、知识库和 FTS5 已保留，可展开日志后重试。'
            )
            $script:knowledgeEnvironmentLastFailure = $failure
            $knowledgeEnvironmentReasonText.Text = $failure
            $knowledgeEnvironmentLogExpander.IsExpanded = $true
            Update-HiaKnowledgeEnvironmentActions
            Show-InlineStatus -Kind 'error' -Text $failure
            return
        } else {
            if ($completedAction -like 'environment-*-embedding') {
                $knowledgeEnvironmentStageText.Text = (
                    '项目本地解析器与知识向量运行时已完成验证。'
                )
                Show-InlineStatus -Kind 'success' -Text (
                    '项目本地 Python、uv、pypdf、PyTorch 与 embedding worker 已完成验证。'
                )
            } else {
                $knowledgeEnvironmentStageText.Text = (
                    '项目本地知识环境已完成验证。'
                )
                Show-InlineStatus -Kind 'success' -Text (
                    '项目本地 Python、uv 与文档解析环境已完成验证。'
                )
            }
        }
        Update-HiaKnowledgeEnvironmentActions
        return
    }

    $detail = Get-HiaEmbeddingInstallFailureSummary `
        -InstallLogPath $script:knowledgeEnvironmentLogPath `
        -ExitCode $exitCode
    if ($detail -eq "安装进程退出码 $exitCode") {
        $processDetail = ([string]$stderr).Trim()
        if ([string]::IsNullOrWhiteSpace($processDetail)) {
            $processDetail = ([string]$stdout).Trim()
        }
        if (-not [string]::IsNullOrWhiteSpace($processDetail)) {
            $detail = ConvertTo-HiaRedactedText -Text $processDetail
        }
    }
    if ($detail.Length -gt 420) {
        $detail = $detail.Substring(0, 420) + '…'
    }
    $script:knowledgeEnvironmentLastFailure = $detail
    Refresh-HiaKnowledgeDisplay -Quiet
    $knowledgeEnvironmentReasonText.Text = (
        "这次修复没完成：$detail。可直接重试；FTS5 与已有知识库不会被删除。"
    )
    $knowledgeEnvironmentReasonText.ToolTip = $knowledgeEnvironmentReasonText.Text
    $knowledgeEnvironmentStageText.Text = '修复未完成；已保留项目数据和安装日志。'
    $knowledgeEnvironmentLogExpander.IsExpanded = $true
    Update-HiaKnowledgeEnvironmentActions
    Show-InlineStatus -Kind 'error' -Text (
        "本地知识环境未完成：$detail。可直接重试；日志路径已显示。"
    )
}

$script:knowledgeEnvironmentTimer.Add_Tick({
    Update-HiaKnowledgeEnvironmentLog
    Complete-HiaKnowledgeEnvironmentRepair
})

function Start-HiaKnowledgeEnvironmentRepair {
    if (
        $script:isBusy -or
        $null -ne $script:knowledgeEnvironmentProcess
    ) {
        return
    }
    if ($null -ne $script:knowledgeIndexProcess) {
        Show-InlineStatus -Kind 'warning' -Text '请先等待或取消本次索引，再修复本地知识环境。'
        return
    }
    if ($null -eq $script:knowledgeEnvironmentStatus) {
        Refresh-HiaKnowledgeDisplay -Quiet
    }
    $action = Get-HiaKnowledgeEnvironmentAction `
        -Environment $script:knowledgeEnvironmentStatus `
        -SelectedProfile (Get-ComboEmbeddingProfile)
    if ([string]::IsNullOrWhiteSpace($action)) {
        Show-InlineStatus -Kind 'success' -Transient -Text '本地知识环境已经通过验证。'
        return
    }
    try {
        $activeInstall = Get-HiaEmbeddingInstallLockInfo `
            -ProjectRoot $projectRoot
    } catch {
        Show-InlineStatus -Kind 'error' -Text (
            '无法安全检查项目本地安装锁；请检查 .runtime\launcher 路径。'
        )
        return
    }
    if ($null -ne $activeInstall -and [bool]$activeInstall.active) {
        $ownerLog = Resolve-HiaEmbeddingInstallLogForRead `
            -InstallLogPath ([string]$activeInstall.log_path)
        if (-not [string]::IsNullOrWhiteSpace($ownerLog)) {
            Set-HiaKnowledgeEnvironmentLogPath -Path $ownerLog
            Update-HiaKnowledgeEnvironmentLog
        }
        Show-HiaEmbeddingInstallAlreadyRunning -LockInfo $activeInstall
        $knowledgeEnvironmentReasonText.Text = (
            '已有一次项目本地环境安装正在运行；这里不会再启动第二个。'
        )
        return
    }
    $knowledgeScript = [System.IO.Path]::GetFullPath(
        (Join-Path $projectRoot 'scripts\hia-knowledge.ps1')
    )
    if (-not (Test-Path -LiteralPath $knowledgeScript -PathType Leaf)) {
        Show-InlineStatus -Kind 'error' -Text '缺少 scripts\hia-knowledge.ps1，无法准备本地知识环境。'
        return
    }
    try {
        $environmentLogPath = New-HiaEmbeddingInstallLogPath
        Set-HiaKnowledgeEnvironmentLogPath -Path $environmentLogPath
    } catch {
        Show-InlineStatus -Kind 'error' -Text (
            '无法创建项目本地安装日志，请检查 .runtime\launcher 是否可写。'
        )
        return
    }
    $powershellExe = Join-Path $env:SystemRoot (
        'System32\WindowsPowerShell\v1.0\powershell.exe'
    )
    $cliAction = if ($action -like 'environment-install*') {
        'environment-install'
    } else {
        'environment-repair'
    }
    $selectedProfile = Get-ComboEmbeddingProfile
    $arguments = @(
        '-NoProfile',
        '-NonInteractive',
        '-ExecutionPolicy', 'Bypass',
        '-File', $knowledgeScript,
        $cliAction,
        '-LogPath', $environmentLogPath,
        '-Device', (Get-ComboEmbeddingDevice)
    )
    if (-not [string]::IsNullOrWhiteSpace($selectedProfile)) {
        $arguments += @('-Profile', $selectedProfile)
    }
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $powershellExe
    $startInfo.Arguments = (@($arguments | ForEach-Object {
        ConvertTo-HiaProcessArgument -Value ([string]$_)
    }) -join ' ')
    $startInfo.WorkingDirectory = $projectRoot
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    try {
        $script:knowledgeEnvironmentLastFailure = ''
        $script:knowledgeEnvironmentProcessAction = $action
        $knowledgeEnvironmentLogTextBox.Text = '等待第一条安装日志…'
        $knowledgeEnvironmentLogExpander.IsExpanded = $false
        $script:knowledgeEnvironmentProcess = (
            [System.Diagnostics.Process]::new()
        )
        $script:knowledgeEnvironmentProcess.StartInfo = $startInfo
        if (-not $script:knowledgeEnvironmentProcess.Start()) {
            throw '本地知识环境命令未启动。'
        }
        $script:knowledgeEnvironmentOutputTask = (
            $script:knowledgeEnvironmentProcess.StandardOutput.ReadToEndAsync()
        )
        $script:knowledgeEnvironmentErrorTask = (
            $script:knowledgeEnvironmentProcess.StandardError.ReadToEndAsync()
        )
        Set-BusyState -Busy $true
        $initialStage = if ($action -like 'environment-*-embedding') {
            '正在完整修复 managed venv、解析器与 CPU/CUDA 向量运行时'
        } else {
            '正在准备项目本地 Python、uv 与共享 venv'
        }
        Set-HiaKnowledgeEnvironmentProgress `
            -Running $true `
            -Stage $initialStage
        $overallStatusText.Text = '正在准备本地知识'
        $inlineStage = if ($action -like 'environment-*-embedding') {
            '本地知识环境正在完整修复。HIA Python 环境位于项目根目录 .venv；受管 CPython、uv、模型与缓存位于 .runtime。'
        } else {
            '本地知识环境正在准备中… HIA Python 环境位于项目根目录 .venv；受管 CPython、uv、模型与缓存位于 .runtime。'
        }
        Show-InlineStatus -Kind 'neutral' -Text $inlineStage
        $script:knowledgeEnvironmentTimer.Start()
    } catch {
        if ($null -ne $script:knowledgeEnvironmentProcess) {
            $script:knowledgeEnvironmentProcess.Dispose()
            $script:knowledgeEnvironmentProcess = $null
        }
        $script:knowledgeEnvironmentOutputTask = $null
        $script:knowledgeEnvironmentErrorTask = $null
        $script:knowledgeEnvironmentProcessAction = ''
        Set-BusyState -Busy $false
        Set-HiaKnowledgeEnvironmentProgress -Running $false
        $script:knowledgeEnvironmentLastFailure = [string]$_.Exception.Message
        $knowledgeEnvironmentReasonText.Text = (
            "无法启动修复：$($_.Exception.Message)。可直接重试。"
        )
        $knowledgeEnvironmentLogExpander.IsExpanded = $true
        Update-HiaKnowledgeEnvironmentActions
        Show-InlineStatus -Kind 'error' -Text (
            "无法启动本地知识环境修复：$($_.Exception.Message)"
        )
    }
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

function Update-HiaKnowledgeSourceActions {
    $deleteKnowledgeSourceButton.IsEnabled = (
        -not $script:isBusy -and
        $null -ne $knowledgeSourcesList.SelectedItem
    )
}

function Update-HiaCacheActions {
    $cleanupScreenshotsButton.IsEnabled = (
        -not $script:isBusy -and
        $null -ne $script:cachePreview -and
        $cacheCategoriesList.SelectedItems.Count -gt 0
    )
    $screenshotCategories = @()
    if ($null -ne $script:cachePreview) {
        $screenshotCategories = @($script:cachePreview.categories | Where-Object {
            [string]$_.id -eq 'screenshots' -and
            -not [bool]$_.blocked -and
            [bool]$_.exists -and
            [long]$_.bytes -gt 0
        })
    }
    $quickCleanupScreenshotsButton.IsEnabled = (
        -not $script:isBusy -and
        $screenshotCategories.Count -eq 1
    )
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
    $embeddingProfileCombo.IsEnabled = (-not $Busy -and $null -ne $script:embeddingData)
    $embeddingDeviceCombo.IsEnabled = (-not $Busy -and $null -ne $script:embeddingData)
    $houdiniCombo.IsEnabled = -not $Busy
    $bridgeCombo.IsEnabled = -not $Busy
    $renderOutputTextBox.IsEnabled = -not $Busy
    $browseHoudiniButton.IsEnabled = -not $Busy
    $browseBridgeButton.IsEnabled = -not $Busy
    $browseRenderOutputButton.IsEnabled = -not $Busy
    $recoverCheckpointOption.IsEnabled = -not $Busy
    $normalLaunchOption.IsEnabled = -not $Busy
    $rescanButton.IsEnabled = -not $Busy
    $quickRescanButton.IsEnabled = -not $Busy
    $repairButton.IsEnabled = -not $Busy
    $quickRepairButton.IsEnabled = -not $Busy
    $copyActivationCommandButton.IsEnabled = -not $Busy
    $repairKnowledgeEnvironmentButton.IsEnabled = $false
    $importKnowledgeFileButton.IsEnabled = -not $Busy
    $importKnowledgeFolderButton.IsEnabled = -not $Busy
    $refreshKnowledgeSourcesButton.IsEnabled = -not $Busy
    $knowledgeSourcesList.IsEnabled = -not $Busy
    $rescanKnowledgeSourcesButton.IsEnabled = -not $Busy
    $refreshCacheButton.IsEnabled = -not $Busy
    $cacheCategoriesList.IsEnabled = -not $Busy
    if ($Busy) {
        $openReportButton.IsEnabled = $false
        $copyReportButton.IsEnabled = $false
        $quickOpenReportButton.IsEnabled = $false
        $quickCopyReportButton.IsEnabled = $false
        $launchButton.IsEnabled = $false
        Set-OverallState -State 'busy'
        $window.UpdateLayout()
        [void]$window.Dispatcher.Invoke(
            [System.Action]{ },
            [System.Windows.Threading.DispatcherPriority]::Render
        )
    } else {
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
    Update-HiaKnowledgeSourceActions
    Update-HiaCacheActions
    Update-HiaKnowledgeIndexActionButton
    Update-HiaKnowledgeEnvironmentActions
    Update-HiaReportActions
}

function Test-CurrentRedCheck {
    param([Parameter(Mandatory = $true)][string]$Id)

    if ($null -eq $script:currentResult) { return $false }
    return @($script:currentResult.checks | Where-Object {
        [string]$_.id -eq $Id -and [string]$_.level -eq 'red'
    }).Count -gt 0
}

function Test-CurrentNonGreenCheck {
    param([Parameter(Mandatory = $true)][string]$Id)

    if ($null -eq $script:currentResult) { return $false }
    return @($script:currentResult.checks | Where-Object {
        [string]$_.id -eq $Id -and [string]$_.level -ne 'green'
    }).Count -gt 0
}

function Update-RepairButton {
    $label = '请先重新扫描'
    $actionAvailable = $false
    $knowledgeAction = Get-HiaKnowledgeEnvironmentAction `
        -Environment $script:knowledgeEnvironmentStatus `
        -SelectedProfile (Get-ComboEmbeddingProfile)
    if ($null -eq $script:currentResult) {
        $label = if ($script:preflightFailed) {
            '自检失败，查看报告'
        } else {
            '请先重新扫描'
        }
    } elseif (Test-CurrentRedCheck -Id 'codex.executable') {
        $label = '安装/修复 Codex'
        $actionAvailable = $true
    } elseif (Test-CurrentRedCheck -Id 'codex.login') {
        $label = '复制登录命令'
        $actionAvailable = $true
    } elseif ($knowledgeAction -like 'environment-*') {
        $label = if ($script:knowledgeEnvironmentLastFailure) {
            '重试修复本地知识环境'
        } elseif ($knowledgeAction -like 'environment-*-embedding') {
            '完整修复本地知识环境'
        } else {
            '修复本地知识环境'
        }
        $actionAvailable = $true
    } elseif (
        $null -ne $script:embeddingData -and
        (Test-CurrentNonGreenCheck -Id 'embedding.runtime')
    ) {
        $label = '安装/修复知识向量模型'
        $actionAvailable = $true
    } elseif (@($script:currentResult.checks | Where-Object {
        [string]$_.level -ne 'green' -and
        [string]$_.id -in @(
            'project.runtime_writable',
            'project.portable_codex_config',
            'project.portable_houdini_package'
        )
    }).Count -gt 0) {
        $label = '修复安全项目'
        $actionAvailable = $true
    } elseif ([string]$script:currentResult.overall -eq 'green') {
        $label = '环境无需修复'
    } else {
        $label = '暂无可自动修复项'
    }
    $repairButton.Content = $label
    [System.Windows.Automation.AutomationProperties]::SetName($repairButton, $label)
    $repairButton.IsEnabled = -not $script:isBusy -and $actionAvailable
    $quickRepairButton.Content = $label
    [System.Windows.Automation.AutomationProperties]::SetName(
        $quickRepairButton,
        "快捷操作：$label"
    )
    $quickRepairButton.IsEnabled = $repairButton.IsEnabled
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
    Update-HiaReportActions

    if ($Result.overall -eq 'green') {
        Set-OverallState -State 'green'
    } elseif ($Result.overall -eq 'yellow') {
        Set-OverallState -State 'yellow'
    } else {
        Set-OverallState -State 'red'
    }
    Update-HiaOverviewSummary
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
    Update-HiaOverviewSummary -FailureMessage '自检过程未能完成'
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
    Update-HiaOverviewSummary
    Update-RepairButton
    Update-PathSummaries
    if ($null -eq $script:knowledgeIndexProcess) {
        Set-HiaKnowledgeIndexDisplay `
            -Reset `
            -Message '环境选择已变化，重新扫描后刷新索引状态。'
    }
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
        -PreferredBackend ([string]$preferences.backend) `
        -PreferredEmbedding ([string]$preferences.embedding) `
        -PreferredEmbeddingDevice ([string]$preferences.embedding_device)

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
        embedding = Get-ComboEmbeddingProfile
        embedding_device = Get-ComboEmbeddingDevice
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

function New-HiaEmbeddingInstallLogPath {
    $leafName = 'embedding-install-{0}-{1}.log' -f
        [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss'),
        [Guid]::NewGuid().ToString('N')
    return Resolve-HiaLauncherStoragePath `
        -ProjectRoot $projectRoot `
        -LeafName $leafName `
        -CreateDirectory `
        -AllowMissingLeaf
}

function Resolve-HiaEmbeddingInstallLogForRead {
    param([AllowEmptyString()][string]$InstallLogPath = '')

    if ([string]::IsNullOrWhiteSpace($InstallLogPath)) { return '' }
    try {
        $candidate = [System.IO.Path]::GetFullPath($InstallLogPath)
        $leafName = [System.IO.Path]::GetFileName($candidate)
        if ($leafName -notmatch '^embedding-install-[A-Za-z0-9-]+\.log$') {
            return ''
        }
        $validated = Resolve-HiaLauncherStoragePath `
            -ProjectRoot $projectRoot `
            -LeafName $leafName `
            -AllowMissingLeaf
        if (
            [System.StringComparer]::OrdinalIgnoreCase.Equals(
                $candidate,
                $validated
            ) -and
            (Test-Path -LiteralPath $validated -PathType Leaf)
        ) {
            return $validated
        }
    } catch { }
    return ''
}

function Get-HiaEmbeddingInstallFailureSummary {
    param(
        [AllowEmptyString()][string]$InstallLogPath = '',
        [int]$ExitCode = 1
    )

    $detail = ''
    $validatedLogPath = Resolve-HiaEmbeddingInstallLogForRead `
        -InstallLogPath $InstallLogPath
    if (-not [string]::IsNullOrWhiteSpace($validatedLogPath)) {
        try {
            $errorLines = @(
                [System.IO.File]::ReadAllLines($validatedLogPath) |
                    Where-Object { $_ -match '\sERROR\s' }
            )
            if ($errorLines.Count -gt 0) {
                $detail = [string]$errorLines[-1]
                $detail = ($detail -replace
                    '^\S+\s+ERROR\s+',
                    ''
                ).Trim()
            }
        } catch { }
    }
    if ([string]::IsNullOrWhiteSpace($detail)) {
        $detail = "安装进程退出码 $ExitCode"
    }
    try {
        $safeJson = ConvertTo-HiaRedactedJson `
            -Value ([pscustomobject]@{ message = $detail }) `
            -Depth 2 `
            -Compress
        $detail = [string](($safeJson | ConvertFrom-Json).message)
    } catch {
        $detail = "安装进程退出码 $ExitCode"
    }
    $detail = ([regex]::Replace($detail, '[\r\n]+', ' ')).Trim()
    if ($detail.Length -gt 360) {
        $detail = $detail.Substring(0, 360) + '…'
    }
    return $detail
}

function Test-HiaEmbeddingRuntimeReady {
    param([AllowNull()]$Result)

    if ($null -eq $Result) { return $false }
    $checks = @($Result.checks | Where-Object {
        [string]$_.id -eq 'embedding.runtime'
    })
    return (
        $checks.Count -eq 1 -and
        [string]$checks[0].level -eq 'green'
    )
}

function Get-HiaEmbeddingRuntimeVerificationFailure {
    param([AllowNull()]$Result)

    if ($null -eq $Result) {
        return '安装命令已结束，但自检刷新未完成。'
    }
    $checks = @($Result.checks | Where-Object {
        [string]$_.id -eq 'embedding.runtime'
    })
    if ($checks.Count -ne 1) {
        return '安装命令已结束，但自检没有返回唯一的 embedding.runtime 结果。'
    }
    $detail = [string]$checks[0].message
    if ([string]::IsNullOrWhiteSpace($detail)) {
        $detail = 'embedding.runtime 尚未通过。'
    }
    return ConvertTo-HiaRedactedText -Text $detail
}

function Show-HiaEmbeddingInstallAlreadyRunning {
    param([Parameter(Mandatory = $true)]$LockInfo)

    $ownerLog = Resolve-HiaEmbeddingInstallLogForRead `
        -InstallLogPath ([string]$LockInfo.log_path)
    if (-not [string]::IsNullOrWhiteSpace($ownerLog)) {
        $script:lastReportPath = $ownerLog
        $reportPathTextBox.Text = $ownerLog
        $reportPathTextBox.ToolTip = $ownerLog
        Update-HiaReportActions
        Show-InlineStatus `
            -Kind 'neutral' `
            -Text "已有知识向量安装正在运行。日志：$ownerLog"
        return
    }
    Show-InlineStatus `
        -Kind 'neutral' `
        -Text '已有知识向量安装正在运行；完成后重新扫描即可。'
}

function Complete-HiaEmbeddingInstall {
    if ($null -eq $script:embeddingProcess -or -not $script:embeddingProcess.HasExited) { return }

    $script:embeddingTimer.Stop()
    Update-HiaKnowledgeEnvironmentLog
    $exitCode = $script:embeddingProcess.ExitCode
    $script:embeddingProcess.Dispose()
    $script:embeddingProcess = $null
    $preferences = $script:embeddingPreferences
    $script:embeddingPreferences = $null
    $installLogPath = $script:embeddingInstallLogPath
    $script:embeddingInstallLogPath = ''

    Set-BusyState -Busy $false
    Set-HiaKnowledgeEnvironmentProgress -Running $false
    Invoke-GuiScan `
        -PreferredHoudini ([string]$preferences.houdini) `
        -PreferredBridge ([string]$preferences.bridge) `
        -PreferredBackend ([string]$preferences.backend) `
        -PreferredEmbedding ([string]$preferences.embedding) `
        -PreferredEmbeddingDevice ([string]$preferences.embedding_device)
    Refresh-HiaKnowledgeDisplay -Quiet

    $runtimeReady = (
        -not $script:preflightFailed -and
        (Test-HiaEmbeddingRuntimeReady -Result $script:currentResult)
    )
    if ($exitCode -eq 0 -and $runtimeReady) {
        $script:knowledgeEnvironmentLastFailure = ''
        $knowledgeEnvironmentStageText.Text = '知识向量运行时已完成验证。'
        Update-HiaKnowledgeEnvironmentActions
        Show-InlineStatus -Kind 'success' -Transient -Text '知识向量环境准备好了，自检已刷新。'
        return
    }

    if ($exitCode -ne 0) {
        try {
            $activeInstall = Get-HiaEmbeddingInstallLockInfo `
                -ProjectRoot $projectRoot
            if ($null -ne $activeInstall -and [bool]$activeInstall.active) {
                Show-HiaEmbeddingInstallAlreadyRunning -LockInfo $activeInstall
                return
            }
        } catch { }
    }

    $failureSummary = if ($exitCode -eq 0) {
        Get-HiaEmbeddingRuntimeVerificationFailure `
            -Result $script:currentResult
    } else {
        Get-HiaEmbeddingInstallFailureSummary `
            -InstallLogPath $installLogPath `
            -ExitCode $exitCode
    }
    $validatedInstallLog = Resolve-HiaEmbeddingInstallLogForRead `
        -InstallLogPath $installLogPath
    if (-not [string]::IsNullOrWhiteSpace($validatedInstallLog)) {
        $script:lastReportPath = $validatedInstallLog
        $reportPathTextBox.Text = $validatedInstallLog
        $reportPathTextBox.ToolTip = $validatedInstallLog
        Update-HiaReportActions
    }
    $script:knowledgeEnvironmentLastFailure = $failureSummary
    $knowledgeEnvironmentReasonText.Text = (
        "知识向量运行时未完成：$failureSummary。FTS5 仍可用，可直接重试。"
    )
    $knowledgeEnvironmentReasonText.ToolTip = $knowledgeEnvironmentReasonText.Text
    $knowledgeEnvironmentStageText.Text = '向量运行时未完成；已保留模型、知识库与日志。'
    $knowledgeEnvironmentLogExpander.IsExpanded = $true
    Update-HiaKnowledgeEnvironmentActions
    $failurePrefix = if ($exitCode -eq 0) {
        '安装命令已结束，但刷新验证未通过'
    } else {
        '这次安装没完成'
    }
    $logDisplay = if ([string]::IsNullOrWhiteSpace($validatedInstallLog)) {
        '未生成'
    } else {
        $validatedInstallLog
    }
    Show-InlineStatus `
        -Kind 'error' `
        -Text (
            '{0}：{1}。安装日志：{2}' -f
                $failurePrefix,
                $failureSummary,
                $logDisplay
        )
}

$script:embeddingTimer.Add_Tick({
    Update-HiaKnowledgeEnvironmentLog
    Complete-HiaEmbeddingInstall
})

function Start-HiaEmbeddingInstall {
    if ($null -ne $script:embeddingProcess) { return }

    $installerScript = Join-Path $projectRoot 'scripts\launcher\Install-HiaEmbedding.ps1'
    $selectedProfile = Get-ComboEmbeddingProfile
    $selectedBridge = Get-ComboPath -Combo $bridgeCombo
    if (
        $null -eq $script:embeddingData -or
        -not $selectedProfile -or
        -not (Test-Path -LiteralPath $selectedBridge -PathType Leaf)
    ) {
        Show-InlineStatus -Kind 'error' -Text '请先选择有效 Bridge Python 并重新扫描，再安装知识向量模型。'
        return
    }
    if (-not (Test-Path -LiteralPath $installerScript -PathType Leaf)) {
        Show-InlineStatus -Kind 'error' -Text '缺少项目本地 embedding 安装脚本，无法继续。'
        return
    }
    try {
        $activeInstall = Get-HiaEmbeddingInstallLockInfo `
            -ProjectRoot $projectRoot
    } catch {
        Show-InlineStatus `
            -Kind 'error' `
            -Text '无法安全检查项目本地安装锁；请检查 .runtime\launcher 路径。'
        return
    }
    if ($null -ne $activeInstall -and [bool]$activeInstall.active) {
        $ownerLog = Resolve-HiaEmbeddingInstallLogForRead `
            -InstallLogPath ([string]$activeInstall.log_path)
        if (-not [string]::IsNullOrWhiteSpace($ownerLog)) {
            Set-HiaKnowledgeEnvironmentLogPath -Path $ownerLog
            Update-HiaKnowledgeEnvironmentLog
        }
        Show-HiaEmbeddingInstallAlreadyRunning -LockInfo $activeInstall
        return
    }

    try {
        $selectedDevice = Get-ComboEmbeddingDevice
        Write-HiaEmbeddingPreference `
            -ProjectRoot $projectRoot `
            -EmbeddingData $script:embeddingData `
            -EmbeddingProfile $selectedProfile `
            -EmbeddingDevice $selectedDevice | Out-Null
    } catch {
        Show-InlineStatus -Kind 'error' -Text '无法保存知识向量模型选择；未开始安装。'
        return
    }

    $script:embeddingPreferences = [pscustomobject]@{
        houdini = Get-ComboPath -Combo $houdiniCombo
        bridge = $selectedBridge
        backend = Get-ComboBackend
        embedding = $selectedProfile
        embedding_device = $selectedDevice
    }
    try {
        $script:embeddingInstallLogPath = New-HiaEmbeddingInstallLogPath
        Set-HiaKnowledgeEnvironmentLogPath `
            -Path $script:embeddingInstallLogPath
    } catch {
        $script:embeddingPreferences = $null
        Show-InlineStatus `
            -Kind 'error' `
            -Text '无法创建项目本地安装日志，请检查 .runtime\launcher 是否可写。'
        return
    }
    $powershellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $arguments = @(
        '-NoProfile',
        '-NonInteractive',
        '-ExecutionPolicy', 'Bypass',
        '-File', $installerScript,
        '-ProjectRoot', $projectRoot,
        '-Profile', $selectedProfile,
        '-Device', $selectedDevice,
        '-LogPath', $script:embeddingInstallLogPath
    )
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $powershellExe
    $startInfo.Arguments = (@($arguments | ForEach-Object {
        ConvertTo-HiaProcessArgument -Value ([string]$_)
    }) -join ' ')
    $startInfo.WorkingDirectory = $projectRoot
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true

    try {
        $script:knowledgeEnvironmentLastFailure = ''
        $knowledgeEnvironmentLogTextBox.Text = '等待第一条安装日志…'
        $knowledgeEnvironmentLogExpander.IsExpanded = $false
        $script:embeddingProcess = [System.Diagnostics.Process]::new()
        $script:embeddingProcess.StartInfo = $startInfo
        if (-not $script:embeddingProcess.Start()) {
            throw '无法启动 embedding 安装进程。'
        }
        Set-BusyState -Busy $true
        Set-HiaKnowledgeEnvironmentProgress `
            -Running $true `
            -Stage '正在补全所选模型的 CPU/CUDA 运行时'
        $overallStatusText.Text = '正在准备向量环境'
        $choice = Get-HiaEmbeddingProfileContract `
            -EmbeddingData $script:embeddingData `
            -Profile $selectedProfile
        $size = ([double]$choice.repository_size_gb).ToString(
            '0.##',
            [System.Globalization.CultureInfo]::InvariantCulture
        )
        Show-InlineStatus -Kind 'neutral' -Text ("母鸡啄米中… 正在准备 {0}（官方模型文件约 {1} GB）和 {2} 计算环境；Python 依赖留在项目根 .venv，受管工具、模型与缓存留在 .runtime。" -f $choice.label, $size, $selectedDevice)
        $script:embeddingTimer.Start()
    } catch {
        if ($null -ne $script:embeddingProcess) {
            $script:embeddingProcess.Dispose()
            $script:embeddingProcess = $null
        }
        $script:embeddingPreferences = $null
        $script:embeddingInstallLogPath = ''
        Set-BusyState -Busy $false
        Set-HiaKnowledgeEnvironmentProgress -Running $false
        $script:knowledgeEnvironmentLastFailure = [string]$_.Exception.Message
        $knowledgeEnvironmentReasonText.Text = (
            "无法启动知识向量运行时安装：$($_.Exception.Message)。可直接重试。"
        )
        $knowledgeEnvironmentLogExpander.IsExpanded = $true
        Update-HiaKnowledgeEnvironmentActions
        Show-InlineStatus -Kind 'error' -Text (
            "无法启动知识向量模型项目本地安装：$($_.Exception.Message)"
        )
    }
}

function Update-HiaKnowledgeIndexFromEvent {
    param([Parameter(Mandatory = $true)]$Event)

    $script:knowledgeIndexLastEvent = $Event
    $index = $Event.index
    $message = ''
    switch ([string]$Event.event) {
        'start' {
            $message = if ([string]$Event.action -eq 'build') {
                '正在构建索引；每个已提交批次都会保留。'
            } else {
                '正在读取索引状态…'
            }
        }
        'progress' {
            $batch = [long](Get-HiaKnowledgeIndexValue -Index $Event -Name 'batch_number' -Default 0)
            $indexed = [long](Get-HiaKnowledgeIndexValue -Index $Event -Name 'indexed_this_batch' -Default 0)
            $message = "正在构建索引：第 $batch 批已提交 $indexed 个。"
        }
        'error' {
            $code = [string](Get-HiaKnowledgeIndexValue -Index $Event -Name 'code' -Default 'UNKNOWN')
            $detail = [string](Get-HiaKnowledgeIndexValue -Index $Event -Name 'message' -Default '索引命令失败')
            $message = "索引命令失败（$code）：$detail"
        }
    }
    Set-HiaKnowledgeIndexDisplay -Index $index -Message $message
}

function Stop-HiaKnowledgeIndexProcessTree {
    param(
        [Parameter(Mandatory = $true)]
        [System.Diagnostics.Process]$Process
    )

    if ($Process.HasExited) { return }
    $processId = [int]$Process.Id
    if ($processId -le 0) {
        throw '知识索引 CLI 没有可终止的有效 PID。'
    }
    if ([string]::IsNullOrWhiteSpace([string]$env:SystemRoot)) {
        throw '无法解析 Windows 系统目录，未终止知识索引进程树。'
    }
    $taskkillPath = Join-Path $env:SystemRoot 'System32\taskkill.exe'
    if (-not (Test-Path -LiteralPath $taskkillPath -PathType Leaf)) {
        throw '找不到 Windows taskkill.exe，未终止知识索引进程树。'
    }

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $taskkillPath
    $startInfo.Arguments = "/PID $processId /T /F"
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $terminator = [System.Diagnostics.Process]::new()
    $terminator.StartInfo = $startInfo
    try {
        if (-not $terminator.Start()) {
            throw '无法启动 Windows taskkill.exe。'
        }
        if (-not $terminator.WaitForExit(5000)) {
            throw 'Windows taskkill.exe 未在限定时间内完成。'
        }
        if ($terminator.ExitCode -ne 0) {
            if ($Process.HasExited) { return }
            throw "Windows taskkill.exe 终止进程树失败（exit $($terminator.ExitCode)）。"
        }
    } finally {
        $terminator.Dispose()
    }
}

function Complete-HiaKnowledgeIndexProcess {
    if ($null -eq $script:knowledgeIndexProcess) { return }

    $script:knowledgeIndexTimer.Stop()
    $process = $script:knowledgeIndexProcess
    $exitCode = [int]$process.ExitCode
    $cancelled = $script:knowledgeIndexCancelRequested
    $protocolError = $script:knowledgeIndexProtocolError
    $lastEvent = $script:knowledgeIndexLastEvent
    Stop-HiaKnowledgeIndexProcessTree -Process $process
    $process.Dispose()
    $script:knowledgeIndexProcess = $null
    $script:knowledgeIndexProcessAction = ''
    $script:knowledgeIndexOutputTask = $null
    $script:knowledgeIndexErrorTask = $null
    $script:knowledgeIndexCancelRequested = $false
    $script:knowledgeIndexProtocolError = ''

    if ($script:knowledgeIndexWindowClosing) { return }
    if ($cancelled) {
        $script:knowledgeIndexLastEvent = $null
        Set-HiaKnowledgeIndexDisplay `
            -Index $script:knowledgeIndexLastIndex `
            -Message '本次构建已取消；已提交批次保留，正在刷新实际进度…'
        Start-HiaKnowledgeIndexProcess -Action 'status'
        return
    }
    if ($protocolError) {
        $script:knowledgeIndexLastEvent = [pscustomobject]@{ event = 'error' }
        Set-HiaKnowledgeIndexDisplay `
            -Index $script:knowledgeIndexLastIndex `
            -Message $protocolError
        return
    }
    if (
        $exitCode -ne 0 -or
        $null -eq $lastEvent -or
        [string]$lastEvent.event -ne 'completed'
    ) {
        if ($null -eq $lastEvent -or [string]$lastEvent.event -ne 'error') {
            $script:knowledgeIndexLastEvent = [pscustomobject]@{ event = 'error' }
            Set-HiaKnowledgeIndexDisplay `
                -Index $script:knowledgeIndexLastIndex `
                -Message ("索引命令未正常完成（退出码 {0}）；可稍后继续，FTS5 仍可用。" -f $exitCode)
        } else {
            Update-HiaKnowledgeIndexActionButton
        }
        return
    }
    Set-HiaKnowledgeIndexDisplay -Index $lastEvent.index
}

function Read-HiaKnowledgeIndexOutput {
    while (
        $null -ne $script:knowledgeIndexOutputTask -and
        $script:knowledgeIndexOutputTask.IsCompleted
    ) {
        try {
            $line = $script:knowledgeIndexOutputTask.Result
        } catch {
            $script:knowledgeIndexProtocolError = '无法读取知识索引进程输出。'
            $script:knowledgeIndexOutputTask = $null
            break
        }
        if ($null -eq $line) {
            $script:knowledgeIndexOutputTask = $null
            break
        }
        if (-not [string]::IsNullOrWhiteSpace([string]$line)) {
            try {
                $event = ConvertFrom-HiaKnowledgeIndexJsonLine `
                    -Line ([string]$line) `
                    -EmbeddingData $script:embeddingData
                Update-HiaKnowledgeIndexFromEvent -Event $event
            } catch {
                $script:knowledgeIndexProtocolError = [string]$_.Exception.Message
                try {
                    if ($null -ne $script:knowledgeIndexProcess) {
                        Stop-HiaKnowledgeIndexProcessTree `
                            -Process $script:knowledgeIndexProcess
                    }
                } catch { }
                $script:knowledgeIndexOutputTask = $null
                break
            }
        }
        if ($null -ne $script:knowledgeIndexProcess) {
            $script:knowledgeIndexOutputTask = $script:knowledgeIndexProcess.StandardOutput.ReadLineAsync()
        }
    }
}

function Test-HiaKnowledgeIndexProcessComplete {
    if ($null -eq $script:knowledgeIndexProcess) { return }
    Read-HiaKnowledgeIndexOutput
    if (
        $script:knowledgeIndexProcess.HasExited -and
        $null -eq $script:knowledgeIndexOutputTask -and
        $null -ne $script:knowledgeIndexErrorTask -and
        $script:knowledgeIndexErrorTask.IsCompleted
    ) {
        Complete-HiaKnowledgeIndexProcess
    }
}

$script:knowledgeIndexTimer.Add_Tick({
    try {
        Test-HiaKnowledgeIndexProcessComplete
    } catch {
        $script:knowledgeIndexProtocolError = '知识索引进程状态无法读取。'
        try {
            if ($null -ne $script:knowledgeIndexProcess) {
                Stop-HiaKnowledgeIndexProcessTree `
                    -Process $script:knowledgeIndexProcess
            }
        } catch { }
    }
})

function Start-HiaKnowledgeIndexProcess {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateSet('status', 'build')]
        [string]$Action
    )

    if ($null -ne $script:knowledgeIndexProcess) { return }
    $bridgePython = Get-ComboPath -Combo $bridgeCombo
    if (
        $null -eq $script:embeddingData -or
        [string]::IsNullOrWhiteSpace($bridgePython)
    ) {
        Set-HiaKnowledgeIndexDisplay `
            -Reset `
            -Message '暂时读不到索引状态；FTS5 lexical 检索可继续工作，Houdini 仍可启动。'
        return
    }

    try {
        $plan = New-HiaKnowledgeCliProcessPlan `
            -ProjectRoot $projectRoot `
            -BridgePython $bridgePython `
            -EmbeddingData $script:embeddingData `
            -EmbeddingProfile (Get-ComboEmbeddingProfile) `
            -EmbeddingDevice (Get-ComboEmbeddingDevice) `
            -Action $Action
        $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
        $startInfo.FileName = [string]$plan.file_path
        $startInfo.Arguments = (@($plan.arguments | ForEach-Object {
            ConvertTo-HiaProcessArgument -Value ([string]$_)
        }) -join ' ')
        $startInfo.WorkingDirectory = [string]$plan.working_directory
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        $startInfo.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false)
        $startInfo.StandardErrorEncoding = [System.Text.UTF8Encoding]::new($false)
        # Assign the environment collection directly in each branch.  In
        # Windows PowerShell 5.1, returning StringDictionary from an `if`
        # expression enumerates it into a fixed-size Object[]; Remove() below
        # would then fail before the CLI can start.
        if ($null -ne $startInfo.Environment) {
            $processEnvironment = $startInfo.Environment
        } else {
            $processEnvironment = $startInfo.EnvironmentVariables
        }
        foreach ($name in @($plan.clear_environment_names)) {
            [void]$processEnvironment.Remove([string]$name)
        }
        foreach ($entry in $plan.environment.GetEnumerator()) {
            $processEnvironment[[string]$entry.Key] = [string]$entry.Value
        }

        $process = [System.Diagnostics.Process]::new()
        $process.StartInfo = $startInfo
        if (-not $process.Start()) {
            throw '知识索引进程未启动。'
        }
        $script:knowledgeIndexProcess = $process
        $script:knowledgeIndexProcessAction = $Action
        $script:knowledgeIndexCancelRequested = $false
        $script:knowledgeIndexProtocolError = ''
        $script:knowledgeIndexLastEvent = $null
        $script:knowledgeIndexOutputTask = $process.StandardOutput.ReadLineAsync()
        $script:knowledgeIndexErrorTask = $process.StandardError.ReadToEndAsync()
        $startMessage = if ($Action -eq 'build') {
            '正在构建索引；每个已提交批次都会保留。'
        } else {
            '正在读取索引状态…'
        }
        Set-HiaKnowledgeIndexDisplay -Message $startMessage
        $script:knowledgeIndexTimer.Start()
    } catch {
        $knowledgeIndexFailure = $_
        if ($null -ne $script:knowledgeIndexProcess) {
            try {
                Stop-HiaKnowledgeIndexProcessTree `
                    -Process $script:knowledgeIndexProcess
            } catch { }
            $script:knowledgeIndexProcess.Dispose()
            $script:knowledgeIndexProcess = $null
        }
        $script:knowledgeIndexProcessAction = ''
        $script:knowledgeIndexOutputTask = $null
        $script:knowledgeIndexErrorTask = $null
        $script:knowledgeIndexLastEvent = [pscustomobject]@{ event = 'error' }
        $failureDetail = [string]$knowledgeIndexFailure.Exception.Message
        $failureDetail = ($failureDetail -replace '[\r\n]+', ' ').Trim()
        if ($failureDetail.Length -gt 300) {
            $failureDetail = $failureDetail.Substring(0, 300) + '…'
        }
        $failureMessage = '无法启动知识索引命令'
        if ($failureDetail) {
            $failureMessage += "：$failureDetail"
        }
        $failureMessage += '；FTS5 lexical 检索可继续工作，Houdini 仍可启动。'
        Set-HiaKnowledgeIndexDisplay `
            -Message $failureMessage
    }
}

function Stop-HiaKnowledgeIndexProcess {
    if (
        $null -eq $script:knowledgeIndexProcess -or
        $script:knowledgeIndexProcessAction -ne 'build'
    ) {
        return
    }
    $script:knowledgeIndexCancelRequested = $true
    $knowledgeIndexActionButton.Content = '正在取消…'
    $knowledgeIndexActionButton.IsEnabled = $false
    $knowledgeIndexStatusText.Text = '正在取消本次 CLI；已提交批次不会删除。'
    $knowledgeIndexStatusText.ToolTip = $knowledgeIndexStatusText.Text
    try {
        Stop-HiaKnowledgeIndexProcessTree `
            -Process $script:knowledgeIndexProcess
    } catch {
        $script:knowledgeIndexCancelRequested = $false
        Set-HiaKnowledgeIndexDisplay -Message '无法终止本次索引 CLI 进程树；进程退出后可继续。'
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

function Get-EmbeddingIndex {
    param([AllowEmptyString()][string]$Profile = '')

    if (-not $Profile) { return -1 }
    for ($index = 0; $index -lt $embeddingProfileCombo.Items.Count; $index++) {
        $item = $embeddingProfileCombo.Items[$index]
        $property = $item.PSObject.Properties['id']
        if ($null -ne $property -and [string]$property.Value -eq $Profile) {
            return $index
        }
    }
    return -1
}

function Get-EmbeddingDeviceIndex {
    param([AllowEmptyString()][string]$Device = '')

    $resolved = Resolve-HiaEmbeddingDevice -Device $Device
    for ($index = 0; $index -lt $embeddingDeviceCombo.Items.Count; $index++) {
        $item = $embeddingDeviceCombo.Items[$index]
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
        [AllowEmptyString()][string]$PreferredBackend = '',
        [AllowEmptyString()][string]$PreferredEmbedding = '',
        [AllowEmptyString()][string]$PreferredEmbeddingDevice = ''
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
                        automatic = $false
                        healthy = $false
                        advanced = $true
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
            } else {
                $automaticBridge = @($bridgeCandidates | Where-Object {
                    $property = $_.PSObject.Properties['automatic']
                    $null -ne $property -and [bool]$property.Value
                })
                if ($automaticBridge.Count -eq 1) {
                    $bridgeCombo.SelectedIndex = Get-PathIndex `
                        -Combo $bridgeCombo `
                        -Path ([string]$automaticBridge[0].path)
                } else {
                    $bridgeCombo.SelectedIndex = -1
                }
            }

            $script:embeddingData = $null
            $selectedBridgeForContract = Get-ComboPath -Combo $bridgeCombo
            $contractPythonAvailable = $false
            if (-not [string]::IsNullOrWhiteSpace($selectedBridgeForContract)) {
                try {
                    $contractPythonAvailable = Test-Path `
                        -LiteralPath $selectedBridgeForContract `
                        -PathType Leaf `
                        -ErrorAction Stop
                } catch {
                    $contractPythonAvailable = $false
                }
            }
            if ($contractPythonAvailable) {
                try {
                    $script:embeddingData = Get-HiaEmbeddingContractData `
                        -ProjectRoot $projectRoot `
                        -PythonExe $selectedBridgeForContract `
                        -TimeoutSeconds $ProbeTimeoutSeconds
                } catch {
                    $script:embeddingData = $null
                }
            }
            $embeddingProfileCombo.Items.Clear()
            $embeddingDeviceCombo.Items.Clear()
            if ($null -ne $script:embeddingData) {
                $embeddingChoice = $PreferredEmbedding
                if (-not $embeddingChoice) {
                    $settingKey = [string]$script:embeddingData.contract.settings.profile
                    $settingProperty = $settings.PSObject.Properties[$settingKey]
                    if ($null -ne $settingProperty) {
                        $embeddingChoice = [string]$settingProperty.Value
                    }
                }
                try {
                    $embeddingChoice = Resolve-HiaEmbeddingProfile `
                        -EmbeddingData $script:embeddingData `
                        -Profile $embeddingChoice
                } catch {
                    $embeddingChoice = [string]$script:embeddingData.contract.default_profile
                }
                foreach ($candidate in @(Get-HiaEmbeddingProfileChoices -EmbeddingData $script:embeddingData)) {
                    [void]$embeddingProfileCombo.Items.Add($candidate)
                }
                $embeddingIndex = Get-EmbeddingIndex -Profile $embeddingChoice
                if ($embeddingIndex -lt 0) {
                    throw 'The selected embedding profile is unavailable.'
                }
                $embeddingProfileCombo.SelectedIndex = $embeddingIndex
                $deviceChoice = if ($PreferredEmbeddingDevice) {
                    $PreferredEmbeddingDevice
                } else {
                    [string]$inputs.embedding_device
                }
                foreach ($candidate in @(Get-HiaEmbeddingDeviceChoices)) {
                    [void]$embeddingDeviceCombo.Items.Add($candidate)
                }
                $deviceIndex = Get-EmbeddingDeviceIndex -Device $deviceChoice
                if ($deviceIndex -lt 0) {
                    throw 'The selected embedding device is unavailable.'
                }
                $embeddingDeviceCombo.SelectedIndex = $deviceIndex
            } else {
                $embeddingProfileCombo.SelectedIndex = -1
                $embeddingDeviceCombo.SelectedIndex = -1
            }
        } finally {
            $script:suppressSelectionCheck = $false
        }

        Update-PathSummaries
        $selectedHoudini = Get-ComboPath -Combo $houdiniCombo
        $selectedBridge = Get-ComboPath -Combo $bridgeCombo
        $selectedBackend = Get-ComboBackend
        $selectedEmbedding = Get-ComboEmbeddingProfile
        $selectedEmbeddingDevice = Get-ComboEmbeddingDevice
        $selectedRenderOutput = Get-RenderOutputPath
        $script:currentResult = Invoke-PreflightAndReport `
            -SelectedHoudini $selectedHoudini `
            -SelectedBridge $selectedBridge `
            -SelectedBackend $selectedBackend `
            -SelectedEmbedding $selectedEmbedding `
            -SelectedEmbeddingDevice $selectedEmbeddingDevice `
            -EmbeddingData $script:embeddingData `
            -SelectedRenderOutput $selectedRenderOutput `
            -Candidates $script:currentCandidates
        Show-Result -Result $script:currentResult
        Refresh-HiaKnowledgeDisplay -Quiet
        Refresh-HiaCacheDisplay -Quiet
    } catch {
        Show-PreflightFailure
    } finally {
        $script:suppressSelectionCheck = $false
        Set-BusyState -Busy $false
    }
    Start-HiaKnowledgeIndexProcess -Action 'status'
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
                automatic = $false
                healthy = $false
                advanced = $true
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

$overviewNavButton.Add_Click({ Set-HiaLauncherPage -Page 'overview' })
$environmentNavButton.Add_Click({
    Set-HiaLauncherPage -Page 'environment'
    if ($null -eq $script:knowledgeEnvironmentStatus -and -not $script:isBusy) {
        Refresh-HiaKnowledgeDisplay -Quiet
    }
})
$preflightNavButton.Add_Click({ Set-HiaLauncherPage -Page 'preflight' })
$reportsSettingsNavButton.Add_Click({
    Set-HiaLauncherPage -Page 'reports'
    if ($null -eq $script:knowledgeEnvironmentStatus -and -not $script:isBusy) {
        Refresh-HiaKnowledgeDisplay -Quiet
    }
    if ($null -eq $script:cachePreview -and -not $script:isBusy) {
        Refresh-HiaCacheDisplay -Quiet
    }
})

$cacheCategoriesList.Add_SelectionChanged({ Update-HiaCacheActions })
$knowledgeSourcesList.Add_SelectionChanged({
    Update-HiaKnowledgeSourceActions
})
$refreshCacheButton.Add_Click({
    if ($script:isBusy) { return }
    Set-BusyState -Busy $true
    try {
        Refresh-HiaCacheDisplay
    } finally {
        Set-BusyState -Busy $false
    }
})

$refreshKnowledgeSourcesButton.Add_Click({
    if ($script:isBusy) { return }
    Set-BusyState -Busy $true
    try {
        Refresh-HiaKnowledgeDisplay
    } finally {
        Set-BusyState -Busy $false
    }
})

$repairKnowledgeEnvironmentButton.Add_Click({
    Start-HiaKnowledgeEnvironmentRepair
})

$importKnowledgeFileButton.Add_Click({
    if ($script:isBusy) { return }
    $dialog = [Microsoft.Win32.OpenFileDialog]::new()
    $dialog.Title = '导入本地知识文件'
    $dialog.Filter = (
        '支持的资料 (*.md;*.txt;*.html;*.htm;*.srt;*.vtt;*.pdf)|' +
        '*.md;*.txt;*.html;*.htm;*.srt;*.vtt;*.pdf'
    )
    $dialog.CheckFileExists = $true
    $dialog.Multiselect = $true
    if ($dialog.ShowDialog($window) -ne $true) { return }

    Set-BusyState -Busy $true
    $imported = 0
    $alreadyImported = 0
    try {
        foreach ($selectedPath in @($dialog.FileNames)) {
            $payload = Invoke-HiaKnowledgeAction `
                -Action 'import-file' `
                -Path $selectedPath
            $imported += [int]$payload.result.imported
            $alreadyImported += [int]$payload.result.already_imported
        }
        Refresh-HiaKnowledgeDisplay -Quiet
        Show-InlineStatus -Kind 'success' -Text (
            "资料导入完成：新增 $imported 份，已有 $alreadyImported 份。托管副本位于项目 .runtime。"
        )
    } catch {
        Show-InlineStatus -Kind 'error' -Text (
            "资料导入未完成：$($_.Exception.Message)"
        )
    } finally {
        Set-BusyState -Busy $false
    }
})

$importKnowledgeFolderButton.Add_Click({
    if ($script:isBusy) { return }
    $shell = $null
    try {
        $shell = New-Object -ComObject Shell.Application
        $owner = [System.Windows.Interop.WindowInteropHelper]::new($window).Handle
        $folder = $shell.BrowseForFolder(
            [int]$owner,
            '选择要导入的资料文件夹',
            0x01,
            0
        )
        if ($null -eq $folder) { return }
        $selectedPath = [string]$folder.Self.Path
        Set-BusyState -Busy $true
        try {
            $payload = Invoke-HiaKnowledgeAction `
                -Action 'import-folder' `
                -Path $selectedPath
            Refresh-HiaKnowledgeDisplay -Quiet
            Show-InlineStatus -Kind 'success' -Text (
                '文件夹导入完成：新增 {0} 份，已有 {1} 份，跳过 {2} 个不支持或不安全的条目。' -f
                    [int]$payload.result.imported,
                    [int]$payload.result.already_imported,
                    @($payload.result.skipped).Count
            )
        } finally {
            Set-BusyState -Busy $false
        }
    } catch {
        Show-InlineStatus -Kind 'error' -Text (
            "文件夹导入未完成：$($_.Exception.Message)"
        )
    } finally {
        if ($null -ne $shell) {
            try {
                [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject(
                    $shell
                )
            } catch { }
        }
    }
})

$deleteKnowledgeSourceButton.Add_Click({
    if ($script:isBusy -or $null -eq $knowledgeSourcesList.SelectedItem) {
        return
    }
    $selected = $knowledgeSourcesList.SelectedItem
    $selectedName = [string]$selected.name
    $confirmation = [System.Windows.MessageBox]::Show(
        $window,
        (
            "删除项目托管副本：$selectedName`n`n" +
            '原文件不会被删除；索引会同步刷新。'
        ),
        '确认删除托管知识副本',
        [System.Windows.MessageBoxButton]::YesNo,
        [System.Windows.MessageBoxImage]::Warning,
        [System.Windows.MessageBoxResult]::No
    )
    if ($confirmation -ne [System.Windows.MessageBoxResult]::Yes) {
        Show-InlineStatus -Kind 'neutral' -Transient -Text '已取消；托管副本和原文件都没有变化。'
        return
    }
    Set-BusyState -Busy $true
    try {
        [void](Invoke-HiaKnowledgeAction `
            -Action 'delete' `
            -SourceId ([string]$selected.source_id))
        Refresh-HiaKnowledgeDisplay -Quiet
        Show-InlineStatus -Kind 'success' -Text '托管副本已删除；原文件保持不变。'
    } catch {
        Show-InlineStatus -Kind 'error' -Text (
            "托管副本未删除：$($_.Exception.Message)"
        )
    } finally {
        Set-BusyState -Busy $false
    }
})

$rescanKnowledgeSourcesButton.Add_Click({
    if ($script:isBusy) { return }
    Set-BusyState -Busy $true
    try {
        [void](Invoke-HiaKnowledgeAction -Action 'rescan')
        Refresh-HiaKnowledgeDisplay -Quiet
        Show-InlineStatus -Kind 'success' -Transient -Text '托管资料已重新扫描；FTS5 状态已刷新。'
    } catch {
        Show-InlineStatus -Kind 'error' -Text (
            "资料重新扫描失败：$($_.Exception.Message)"
        )
    } finally {
        Set-BusyState -Busy $false
    }
})

$rescanButton.Add_Click({
    Invoke-GuiScan `
        -PreferredHoudini (Get-ComboPath -Combo $houdiniCombo) `
        -PreferredBridge (Get-ComboPath -Combo $bridgeCombo) `
        -PreferredBackend (Get-ComboBackend) `
        -PreferredEmbedding (Get-ComboEmbeddingProfile) `
        -PreferredEmbeddingDevice (Get-ComboEmbeddingDevice)
})

$knowledgeIndexActionButton.Add_Click({
    if (
        $null -ne $script:knowledgeIndexProcess -and
        $script:knowledgeIndexProcessAction -eq 'build'
    ) {
        Stop-HiaKnowledgeIndexProcess
        return
    }
    if ($null -eq $script:knowledgeIndexProcess) {
        Start-HiaKnowledgeIndexProcess -Action 'build'
    }
})

$mcpBackendCombo.Add_SelectionChanged({ Mark-SelectionNeedsCheck })
$embeddingProfileCombo.Add_SelectionChanged({
    if ($script:suppressSelectionCheck -or $script:isBusy) { return }
    $selectedProfile = Get-ComboEmbeddingProfile
    if ($null -ne $script:embeddingData -and $selectedProfile) {
        try {
            Write-HiaEmbeddingPreference `
                -ProjectRoot $projectRoot `
                -EmbeddingData $script:embeddingData `
                -EmbeddingProfile $selectedProfile `
                -EmbeddingDevice (Get-ComboEmbeddingDevice) | Out-Null
        } catch {
            Show-InlineStatus -Kind 'error' -Text '无法保存知识向量模型选择。'
            return
        }
    }
    Mark-SelectionNeedsCheck
    Update-HiaKnowledgeEnvironmentActions
    Show-InlineStatus -Kind 'warning' -Text '知识向量模型已切换；FTS5 无需重建，向量索引将在使用新模型时按模型重建。请重新扫描。'
})
$embeddingDeviceCombo.Add_SelectionChanged({
    if ($script:suppressSelectionCheck -or $script:isBusy) { return }
    if ($null -ne $script:embeddingData) {
        try {
            Write-HiaEmbeddingPreference `
                -ProjectRoot $projectRoot `
                -EmbeddingData $script:embeddingData `
                -EmbeddingProfile (Get-ComboEmbeddingProfile) `
                -EmbeddingDevice (Get-ComboEmbeddingDevice) | Out-Null
        } catch {
            Show-InlineStatus -Kind 'error' -Text '无法保存知识向量计算设备选择。'
            return
        }
    }
    Mark-SelectionNeedsCheck
    Update-HiaKnowledgeEnvironmentActions
    Show-InlineStatus -Kind 'warning' -Text '知识向量计算设备已切换；重新扫描后生效。'
})
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
    if ($null -eq $script:knowledgeEnvironmentStatus) {
        Refresh-HiaKnowledgeDisplay -Quiet
    }
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
    $knowledgeAction = Get-HiaKnowledgeEnvironmentAction `
        -Environment $script:knowledgeEnvironmentStatus `
        -SelectedProfile (Get-ComboEmbeddingProfile)
    if (-not [string]::IsNullOrWhiteSpace($knowledgeAction)) {
        Set-HiaLauncherPage -Page 'reports'
        Start-HiaKnowledgeEnvironmentRepair
        return
    }
    if (
        $null -ne $script:embeddingData -and
        (Test-CurrentNonGreenCheck -Id 'embedding.runtime')
    ) {
        Start-HiaEmbeddingInstall
        return
    }
    $preferredHoudini = Get-ComboPath -Combo $houdiniCombo
    $preferredBridge = Get-ComboPath -Combo $bridgeCombo
    $preferredBackend = Get-ComboBackend
    $preferredEmbedding = Get-ComboEmbeddingProfile
    $preferredEmbeddingDevice = Get-ComboEmbeddingDevice
    Set-BusyState -Busy $true
    try {
        $actions = @(Repair-HiaSafeProject -ProjectRoot $projectRoot)
    } catch {
        Set-BusyState -Busy $false
        Show-InlineStatus -Kind 'error' -Text '安全项目修复失败。请在控制台使用 -RepairSafeProject -CheckOnly 查看详情。'
        return
    }
    Set-BusyState -Busy $false
    Invoke-GuiScan `
        -PreferredHoudini $preferredHoudini `
        -PreferredBridge $preferredBridge `
        -PreferredBackend $preferredBackend `
        -PreferredEmbedding $preferredEmbedding `
        -PreferredEmbeddingDevice $preferredEmbeddingDevice
    if ($null -ne $script:currentResult) {
        $detail = if ($actions.Count -gt 0) { ' ' + ($actions -join '；') } else { '' }
        Show-InlineStatus -Kind 'success' -Transient -Text ("安全项目修复完成。$detail")
    }
})

function Invoke-HiaCacheCleanup {
    param(
        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string[]]$CategoryIds
    )

    if ($script:isBusy) { return }
    $categoryIds = @($CategoryIds | Where-Object {
        -not [string]::IsNullOrWhiteSpace([string]$_)
    } | Select-Object -Unique)
    if ($categoryIds.Count -eq 0) { return }
    $categoryCsv = $categoryIds -join ','
    Set-BusyState -Busy $true
    try {
        $preview = Invoke-HiaLauncherJsonCli `
            -ScriptName 'hia-cache.ps1' `
            -Arguments @('-Action', 'list', '-Category', $categoryCsv) `
            -TimeoutSeconds 90
    } catch {
        Set-BusyState -Busy $false
        Show-InlineStatus -Kind 'error' -Text (
            "缓存预览未通过安全检查：$($_.Exception.Message)"
        )
        return
    }
    Set-BusyState -Busy $false

    $targetLines = @($preview.categories | ForEach-Object {
        $size = Format-HiaByteCount -Bytes ([long]$_.bytes)
        "• $([string]$_.label)：$size`n  $([string]$_.target_path)"
    }) -join "`n"
    $previewSize = Format-HiaByteCount -Bytes ([long]$preview.total_bytes)
    $confirmationText = @"
即将清理这些项目托管缓存：

$targetLines

预计释放：$previewSize

继续前会再次核对分类、精确路径和本次快照。项目资料、模型、工具链、附件、会话检查点、HIP 与最终输出不会被清理。
"@
    $confirmation = [System.Windows.MessageBox]::Show(
        $window,
        $confirmationText,
        '确认清理项目缓存',
        [System.Windows.MessageBoxButton]::YesNo,
        [System.Windows.MessageBoxImage]::Warning,
        [System.Windows.MessageBoxResult]::No
    )
    if ($confirmation -ne [System.Windows.MessageBoxResult]::Yes) {
        Show-InlineStatus -Kind 'neutral' -Transient -Text '已取消缓存清理；未删除任何文件。'
        return
    }

    Set-BusyState -Busy $true
    try {
        $cleanupResult = Invoke-HiaLauncherJsonCli `
            -ScriptName 'hia-cache.ps1' `
            -Arguments @(
                '-Action', 'clear',
                '-Category', $categoryCsv,
                '-SnapshotHash', [string]$preview.snapshot_hash
            ) `
            -TimeoutSeconds 180
    } catch {
        Show-InlineStatus -Kind 'error' -Text (
            "缓存未清理：$($_.Exception.Message)"
        )
        return
    } finally {
        Set-BusyState -Busy $false
    }

    $freedSize = Format-HiaByteCount -Bytes ([long]$cleanupResult.freed_bytes)
    $deletedFiles = 0
    $deletedDirectories = 0
    foreach ($result in @($cleanupResult.results)) {
        $deletedFiles += [int]$result.deleted_files
        $deletedDirectories += [int]$result.deleted_directories
    }
    Show-InlineStatus `
        -Kind 'success' `
        -Text (
            '缓存清理完成：删除 {0} 个文件和 {1} 个空目录，释放 {2}。' -f `
                $deletedFiles,
                $deletedDirectories,
                $freedSize
        )
    Refresh-HiaCacheDisplay -Quiet
}

$cleanupScreenshotsButton.Add_Click({
    $selected = @($cacheCategoriesList.SelectedItems)
    if ($selected.Count -eq 0) { return }
    Invoke-HiaCacheCleanup -CategoryIds @(
        $selected | ForEach-Object { [string]$_.id }
    )
})

$openReportButton.Add_Click({
    if (-not (Test-HiaLatestReportAvailable)) { return }
    try {
        $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
        $startInfo.FileName = [System.IO.Path]::GetFullPath($script:lastReportPath)
        $startInfo.UseShellExecute = $true
        [void][System.Diagnostics.Process]::Start($startInfo)
        Show-InlineStatus -Kind 'success' -Transient -Text '已打开最新报告。'
    } catch {
        Show-InlineStatus -Kind 'error' -Text '无法打开最新报告；可复制路径后手动查看。'
    }
})

$copyReportButton.Add_Click({
    if (-not (Test-HiaLatestReportAvailable)) { return }
    try {
        [System.Windows.Clipboard]::SetText($script:lastReportPath)
        Show-InlineStatus -Kind 'success' -Transient -Text ("已复制报告路径：$($script:lastReportPath)")
    } catch {
        Show-InlineStatus -Kind 'error' -Text '未能写入剪贴板；可直接选中上方报告路径复制。'
    }
})

$copyActivationCommandButton.Add_Click({
    try {
        [System.Windows.Clipboard]::SetText('.\.venv\Scripts\Activate.ps1')
        Show-InlineStatus -Kind 'success' -Transient -Text '已复制项目 .venv 激活命令。'
    } catch {
        Show-InlineStatus -Kind 'error' -Text '未能复制激活命令；可直接选中环境页中的命令。'
    }
})

$quickRescanButton.Add_Click({
    $rescanButton.RaiseEvent(
        [System.Windows.RoutedEventArgs]::new(
            [System.Windows.Controls.Button]::ClickEvent
        )
    )
})

$quickRepairButton.Add_Click({
    $repairButton.RaiseEvent(
        [System.Windows.RoutedEventArgs]::new(
            [System.Windows.Controls.Button]::ClickEvent
        )
    )
})

$quickCleanupScreenshotsButton.Add_Click({
    Invoke-HiaCacheCleanup -CategoryIds @('screenshots')
})

$quickOpenReportButton.Add_Click({
    $openReportButton.RaiseEvent(
        [System.Windows.RoutedEventArgs]::new(
            [System.Windows.Controls.Button]::ClickEvent
        )
    )
})

$quickCopyReportButton.Add_Click({
    $copyReportButton.RaiseEvent(
        [System.Windows.RoutedEventArgs]::new(
            [System.Windows.Controls.Button]::ClickEvent
        )
    )
})

$launchButton.Add_Click({
    if ($script:selectionNeedsCheck -or $script:isBusy) { return }
    $selectedHoudini = Get-ComboPath -Combo $houdiniCombo
    $selectedBridge = Get-ComboPath -Combo $bridgeCombo
    $selectedBackend = Get-ComboBackend
    $selectedEmbedding = Get-ComboEmbeddingProfile
    $selectedEmbeddingDevice = Get-ComboEmbeddingDevice
    $selectedRenderOutput = Get-RenderOutputPath
    Set-BusyState -Busy $true
    try {
        try {
            $script:currentResult = Invoke-PreflightAndReport `
                -SelectedHoudini $selectedHoudini `
                -SelectedBridge $selectedBridge `
                -SelectedBackend $selectedBackend `
                -SelectedEmbedding $selectedEmbedding `
                -SelectedEmbeddingDevice $selectedEmbeddingDevice `
                -EmbeddingData $script:embeddingData `
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
                -McpBackend $selectedBackend `
                -EmbeddingData $script:embeddingData `
                -EmbeddingProfile $selectedEmbedding `
                -EmbeddingDevice $selectedEmbeddingDevice | Out-Null
            $launchParameters = @{
                SelectedHoudini = $selectedHoudini
                SelectedBridge = $selectedBridge
                SelectedBackend = $selectedBackend
                SelectedEmbedding = $selectedEmbedding
                SelectedEmbeddingDevice = $selectedEmbeddingDevice
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

$minimizeWindowButton.Add_Click({
    [System.Windows.SystemCommands]::MinimizeWindow($window)
})
$maximizeWindowButton.Add_Click({
    if ($window.WindowState -eq [System.Windows.WindowState]::Maximized) {
        [System.Windows.SystemCommands]::RestoreWindow($window)
        return
    }
    [System.Windows.SystemCommands]::MaximizeWindow($window)
})
$closeWindowButton.Add_Click({
    [System.Windows.SystemCommands]::CloseWindow($window)
})
$window.Add_StateChanged({ Update-HiaWindowStateVisual })
Update-HiaWindowStateVisual

Initialize-HiaOptionalArtwork
Set-HiaLauncherPage -Page 'overview'
Update-HiaOverviewSummary
Initialize-RecoveryPrompt

$window.Add_SizeChanged({ Update-ResponsiveLayout })
$window.Add_Closed({
    $script:inlineStatusTimer.Stop()
    $script:bootstrapTimer.Stop()
    $script:embeddingTimer.Stop()
    $script:knowledgeIndexWindowClosing = $true
    $script:knowledgeIndexTimer.Stop()
    $script:knowledgeEnvironmentTimer.Stop()
    if ($null -ne $script:knowledgeIndexProcess) {
        try {
            Stop-HiaKnowledgeIndexProcessTree `
                -Process $script:knowledgeIndexProcess
        } catch { }
        $script:knowledgeIndexProcess.Dispose()
        $script:knowledgeIndexProcess = $null
    }
    if ($null -ne $script:bootstrapProcess) {
        # Disposing this wrapper does not terminate the user-started verified bootstrap.
        $script:bootstrapProcess.Dispose()
        $script:bootstrapProcess = $null
    }
    if ($null -ne $script:embeddingProcess) {
        # The verified project-local install may continue after the launcher window closes.
        $script:embeddingProcess.Dispose()
        $script:embeddingProcess = $null
    }
    if ($null -ne $script:knowledgeEnvironmentProcess) {
        # A user-started project-local environment repair may finish after the window closes.
        $script:knowledgeEnvironmentProcess.Dispose()
        $script:knowledgeEnvironmentProcess = $null
    }
})
$window.Add_ContentRendered({
    Update-ResponsiveLayout
    if ($script:initialScanStarted) { return }
    $script:initialScanStarted = $true
    Invoke-GuiScan `
        -PreferredHoudini $inputs.houdini `
        -PreferredBridge $inputs.bridge `
        -PreferredBackend $inputs.backend `
        -PreferredEmbedding $inputs.embedding `
        -PreferredEmbeddingDevice $inputs.embedding_device
})

Set-HiaKnowledgeIndexDisplay -Reset
Set-HiaKnowledgeEnvironmentDisplay -Environment $null
Set-HiaKnowledgeSourcesDisplay -Sources $null
Update-HiaCacheActions
[void]$window.ShowDialog()
