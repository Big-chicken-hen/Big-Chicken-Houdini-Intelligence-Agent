Set-StrictMode -Version Latest

$script:HiaFxHoudiniVersion = '1.3.0'
$script:HiaCodexVersion = '0.144.3'
$script:HiaProbeMarker = '__HIA_LAUNCHER_PROBE__'
$script:HiaDefaultMcpBackend = 'hia_v2'

function Get-HiaCodexLoginCommand {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $root = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
    $codexHome = (Join-Path $root '.runtime\codex-home').Replace("'", "''")
    $codexExe = (Join-Path $root ".runtime\toolchains\codex\$script:HiaCodexVersion\codex.exe").Replace("'", "''")
    return "`$env:CODEX_HOME = '$codexHome'; & '$codexExe' login --device-auth"
}

function Get-HiaMcpBackendChoices {
    return @(
        [pscustomobject]@{
            id = 'hia_v2'
            display = 'HIA MCP V2（推荐）'
        },
        [pscustomobject]@{
            id = 'fxhoudini'
            display = 'FXHoudiniMCP 1.3.0（兼容回退）'
        }
    )
}

function Resolve-HiaMcpBackend {
    param([AllowEmptyString()][string]$Backend = '')

    if (-not $Backend) { return $script:HiaDefaultMcpBackend }
    if ($Backend -notin @('hia_v2', 'fxhoudini')) {
        throw "Unsupported MCP backend: $Backend"
    }
    return $Backend
}

function New-HiaCheckResult {
    param(
        [Parameter(Mandatory = $true)][string]$Id,
        [Parameter(Mandatory = $true)][string]$Name,
        [ValidateSet('green', 'yellow', 'red')][string]$Level,
        [Parameter(Mandatory = $true)][string]$Message,
        [Parameter(Mandatory = $true)][string]$Advice
    )

    return [pscustomobject]@{
        id = $Id
        name = $Name
        level = $Level
        message = $Message
        advice = $Advice
    }
}

function Get-HiaOverallLevel {
    param([Parameter(Mandatory = $true)][object[]]$Checks)

    if (@($Checks | Where-Object { $_.level -eq 'red' }).Count -gt 0) {
        return 'red'
    }
    if (@($Checks | Where-Object { $_.level -eq 'yellow' }).Count -gt 0) {
        return 'yellow'
    }
    return 'green'
}

function Get-HiaProjectRoot {
    param([Parameter(Mandatory = $true)][string]$StartingPath)

    $candidate = [System.IO.Path]::GetFullPath($StartingPath)
    while ($candidate) {
        if (
            (Test-Path -LiteralPath (Join-Path $candidate 'scripts\launch-houdini.ps1') -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $candidate 'pyproject.toml') -PathType Leaf)
        ) {
            return $candidate.TrimEnd('\')
        }
        $parent = [System.IO.Directory]::GetParent($candidate)
        if ($null -eq $parent) { break }
        $candidate = $parent.FullName
    }
    throw "Unable to derive the project root from launcher path: $StartingPath"
}

function Get-HiaVersionText {
    param(
        [AllowEmptyString()][string]$Text,
        [AllowEmptyString()][string]$Fallback
    )

    foreach ($value in @($Text, $Fallback)) {
        if ($value -and $value -match '(?<!\d)(\d+\.\d+(?:\.\d+){0,2})(?!\d)') {
            return [string]$Matches[1]
        }
        if ($value -and $value -match '(?<!\d)(\d+)[, ]+(\d+)[, ]+(\d+)(?!\d)') {
            return "$($Matches[1]).$($Matches[2]).$($Matches[3])"
        }
    }
    return 'unknown'
}

function Add-HiaCandidatePath {
    param(
        [Parameter(Mandatory = $true)][hashtable]$Candidates,
        [AllowEmptyString()][string]$Path,
        [Parameter(Mandatory = $true)][string]$Source
    )

    if ([string]::IsNullOrWhiteSpace($Path)) { return }
    try {
        $fullPath = [System.IO.Path]::GetFullPath($Path.Trim('"'))
    } catch {
        return
    }
    if ($Candidates.ContainsKey($fullPath)) {
        $Candidates[$fullPath].Add($Source)
        return
    }
    $sources = [System.Collections.Generic.List[string]]::new()
    $sources.Add($Source)
    $Candidates[$fullPath] = $sources
}

function Get-HiaHoudiniCandidates {
    [CmdletBinding()]
    param(
        [AllowEmptyString()][string]$ExplicitPath = '',
        [string[]]$CommonInstallRoots,
        [switch]$SkipEnvironment,
        [switch]$SkipPath,
        [switch]$SkipRegistry
    )

    $candidatePaths = @{}
    if ($ExplicitPath) {
        Add-HiaCandidatePath -Candidates $candidatePaths -Path $ExplicitPath -Source 'explicit'
    } else {
        if (-not $SkipEnvironment -and $env:HFS) {
            Add-HiaCandidatePath `
                -Candidates $candidatePaths `
                -Path (Join-Path $env:HFS 'bin\houdini.exe') `
                -Source 'HFS'
        }

        if (-not $SkipPath) {
            foreach ($command in @(Get-Command -Name 'houdini.exe' -All -ErrorAction SilentlyContinue)) {
                if ($command.Source) {
                    Add-HiaCandidatePath -Candidates $candidatePaths -Path ([string]$command.Source) -Source 'PATH'
                }
            }
        }

        if (-not $SkipRegistry) {
            $appPathKeys = @(
                'Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\houdini.exe',
                'Registry::HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\houdini.exe',
                'Registry::HKEY_CURRENT_USER\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\houdini.exe'
            )
            foreach ($key in $appPathKeys) {
                $registryKey = Get-Item -LiteralPath $key -ErrorAction SilentlyContinue
                if ($null -ne $registryKey) {
                    Add-HiaCandidatePath `
                        -Candidates $candidatePaths `
                        -Path ([string]$registryKey.GetValue('')) `
                        -Source 'registry App Paths'
                }
            }

            $uninstallRoots = @(
                'Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
                'Registry::HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall',
                'Registry::HKEY_CURRENT_USER\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
            )
            foreach ($root in $uninstallRoots) {
                foreach ($key in @(Get-ChildItem -LiteralPath $root -ErrorAction SilentlyContinue)) {
                    $properties = Get-ItemProperty -LiteralPath $key.PSPath -ErrorAction SilentlyContinue
                    if ($null -eq $properties) { continue }
                    $displayName = ''
                    $installLocation = ''
                    $displayProperty = $properties.PSObject.Properties['DisplayName']
                    $installProperty = $properties.PSObject.Properties['InstallLocation']
                    if ($null -ne $displayProperty) { $displayName = [string]$displayProperty.Value }
                    if ($null -ne $installProperty) { $installLocation = [string]$installProperty.Value }
                    if ($displayName -match '(?i)\bHoudini\b' -and $installLocation) {
                        Add-HiaCandidatePath `
                            -Candidates $candidatePaths `
                            -Path (Join-Path $installLocation 'bin\houdini.exe') `
                            -Source 'registry Uninstall'
                    }
                }
            }
        }

        if (-not $PSBoundParameters.ContainsKey('CommonInstallRoots')) {
            $rootList = [System.Collections.Generic.List[string]]::new()
            foreach ($programFiles in @(
                [Environment]::GetFolderPath('ProgramFiles'),
                [Environment]::GetEnvironmentVariable('ProgramW6432')
            )) {
                if ($programFiles) {
                    $rootList.Add((Join-Path $programFiles 'Side Effects Software'))
                    $rootList.Add((Join-Path $programFiles 'SideFX'))
                }
            }
            $CommonInstallRoots = @($rootList | Sort-Object -Unique)
        }

        foreach ($root in @($CommonInstallRoots)) {
            if (-not $root) { continue }
            Add-HiaCandidatePath `
                -Candidates $candidatePaths `
                -Path (Join-Path $root 'bin\houdini.exe') `
                -Source 'SideFX install root'
            foreach ($directory in @(Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue)) {
                Add-HiaCandidatePath `
                    -Candidates $candidatePaths `
                    -Path (Join-Path $directory.FullName 'bin\houdini.exe') `
                    -Source 'SideFX common directory'
            }
        }
    }

    $results = @()
    foreach ($entry in $candidatePaths.GetEnumerator()) {
        $path = [string]$entry.Key
        $exists = Test-Path -LiteralPath $path -PathType Leaf
        if (-not $exists -and -not $ExplicitPath) { continue }
        $fileVersion = ''
        if ($exists) {
            try {
                $item = Get-Item -LiteralPath $path -Force
                $fileVersion = [string]$item.VersionInfo.ProductVersion
                if (-not $fileVersion) { $fileVersion = [string]$item.VersionInfo.FileVersion }
            } catch {
                $fileVersion = ''
            }
        }
        $installName = Split-Path -Leaf (Split-Path -Parent (Split-Path -Parent $path))
        $version = Get-HiaVersionText -Text $fileVersion -Fallback $installName
        $display = "Houdini $version — $path"
        if (-not $exists) { $display = "缺失 — $path" }
        $results += [pscustomobject]@{
            path = $path
            version = $version
            display = $display
            exists = [bool]$exists
            sources = @($entry.Value | Sort-Object -Unique)
        }
    }
    return @($results | Sort-Object -Property @{ Expression = { $_.version }; Descending = $true }, path)
}

function Get-HiaBridgePythonCandidates {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [AllowEmptyString()][string]$ExplicitPath = '',
        [AllowEmptyString()][string]$SavedPath = ''
    )

    $paths = @{}
    foreach ($record in @(
        [pscustomobject]@{ path = $ExplicitPath; source = 'explicit' },
        [pscustomobject]@{ path = $SavedPath; source = 'settings' },
        [pscustomobject]@{ path = $env:HIA_BRIDGE_PYTHON; source = 'HIA_BRIDGE_PYTHON' },
        [pscustomobject]@{ path = (Join-Path $ProjectRoot '.runtime\python\python.exe'); source = 'project runtime' }
    )) {
        if (-not $record.path) { continue }
        try { $full = [System.IO.Path]::GetFullPath([string]$record.path) } catch { continue }
        if (Test-Path -LiteralPath $full -PathType Leaf) { $paths[$full] = $record.source }
    }
    foreach ($directory in @(Get-ChildItem -LiteralPath (Join-Path $ProjectRoot '.runtime\toolchains\python') -Directory -ErrorAction SilentlyContinue)) {
        $path = Join-Path $directory.FullName 'python.exe'
        if (Test-Path -LiteralPath $path -PathType Leaf) { $paths[$path] = 'project toolchain' }
    }
    foreach ($command in @(Get-Command -Name 'python.exe' -All -ErrorAction SilentlyContinue)) {
        if ($command.Source -and (Test-Path -LiteralPath $command.Source -PathType Leaf)) {
            $paths[[System.IO.Path]::GetFullPath([string]$command.Source)] = 'PATH'
        }
    }
    return @($paths.GetEnumerator() | ForEach-Object {
        [pscustomobject]@{
            path = [string]$_.Key
            source = [string]$_.Value
            display = "$($_.Key)  [$($_.Value)]"
        }
    } | Sort-Object -Property path)
}

function ConvertTo-HiaProcessArgument {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)

    if ($Value -notmatch '[\s"]') { return $Value }
    $escaped = $Value -replace '(\\*)"', '$1$1\"'
    $escaped = $escaped -replace '(\\+)$', '$1$1'
    return '"' + $escaped + '"'
}

function Invoke-HiaProcess {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [int]$TimeoutSeconds = 12,
        [hashtable]$Environment = @{},
        [AllowEmptyString()][string]$WorkingDirectory = ''
    )

    $result = [ordered]@{
        started = $false
        timed_out = $false
        exit_code = $null
        stdout = ''
        stderr = ''
        error = ''
    }
    try {
        $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
        $startInfo.FileName = $FilePath
        $startInfo.Arguments = (@($Arguments | ForEach-Object { ConvertTo-HiaProcessArgument -Value ([string]$_) }) -join ' ')
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true
        if ($WorkingDirectory) { $startInfo.WorkingDirectory = $WorkingDirectory }
        foreach ($entry in $Environment.GetEnumerator()) {
            if ($null -ne $startInfo.Environment) {
                $startInfo.Environment[[string]$entry.Key] = [string]$entry.Value
            } else {
                $startInfo.EnvironmentVariables[[string]$entry.Key] = [string]$entry.Value
            }
        }
        $process = [System.Diagnostics.Process]::new()
        $process.StartInfo = $startInfo
        if (-not $process.Start()) { throw 'process start returned false' }
        $result.started = $true
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit([Math]::Max(1, $TimeoutSeconds) * 1000)) {
            $result.timed_out = $true
            try { $process.Kill() } catch { }
            [void]$process.WaitForExit(2000)
        }
        if ($process.HasExited) { $result.exit_code = [int]$process.ExitCode }
        if ($stdoutTask.Wait(2000)) { $result.stdout = [string]$stdoutTask.Result }
        if ($stderrTask.Wait(2000)) { $result.stderr = [string]$stderrTask.Result }
        $process.Dispose()
    } catch {
        $result.error = 'process could not be executed'
    }
    return [pscustomobject]$result
}

function Get-HiaProbePayload {
    param([AllowEmptyString()][string]$Output)

    foreach ($line in @($Output -split "`r?`n")) {
        $index = $line.IndexOf($script:HiaProbeMarker, [System.StringComparison]::Ordinal)
        if ($index -lt 0) { continue }
        $json = $line.Substring($index + $script:HiaProbeMarker.Length)
        try { return $json | ConvertFrom-Json } catch { return $null }
    }
    return $null
}

function Test-HiaHoudiniProbeConsistency {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$HoudiniExe,
        [AllowEmptyString()][string]$HoudiniOutput,
        [int]$HoudiniExitCode = 0,
        [AllowEmptyString()][string]$HythonOutput,
        [int]$HythonExitCode = 0,
        [switch]$HoudiniTimedOut,
        [switch]$HythonTimedOut
    )

    $checks = @()
    $hythonExe = Join-Path ([System.IO.Path]::GetDirectoryName($HoudiniExe)) 'hython.exe'
    $houdiniExists = Test-Path -LiteralPath $HoudiniExe -PathType Leaf
    $hythonExists = Test-Path -LiteralPath $hythonExe -PathType Leaf
    $checks += New-HiaCheckResult `
        -Id 'houdini.executable' -Name 'Houdini executable' `
        -Level $(if ($houdiniExists) { 'green' } else { 'red' }) `
        -Message $(if ($houdiniExists) { "已找到 $HoudiniExe" } else { "不存在：$HoudiniExe" }) `
        -Advice $(if ($houdiniExists) { '无需处理。' } else { '重新扫描或选择准确的 houdini.exe。' })
    $checks += New-HiaCheckResult `
        -Id 'houdini.hython' -Name 'Hython executable' `
        -Level $(if ($hythonExists) { 'green' } else { 'red' }) `
        -Message $(if ($hythonExists) { "已找到 $hythonExe" } else { "同一安装缺少：$hythonExe" }) `
        -Advice $(if ($hythonExists) { '无需处理。' } else { '修复或重新安装该 Houdini 版本。' })

    if (-not $houdiniExists -or -not $hythonExists) { return $checks }

    $houdiniCombined = "$HoudiniOutput"
    $houdiniBuild = Get-HiaVersionText -Text $houdiniCombined -Fallback ''
    $houdiniProbePassed = (-not $HoudiniTimedOut -and $HoudiniExitCode -eq 0 -and $houdiniBuild -ne 'unknown')
    $checks += New-HiaCheckResult `
        -Id 'houdini.build_probe' -Name 'Houdini build probe' `
        -Level $(if ($houdiniProbePassed) { 'green' } else { 'red' }) `
        -Message $(if ($houdiniProbePassed) { "Houdini build $houdiniBuild" } elseif ($HoudiniTimedOut) { 'Houdini 版本探针超时。' } else { '无法读取 Houdini build。' }) `
        -Advice $(if ($houdiniProbePassed) { '无需处理。' } else { '在命令行运行 houdini.exe -version，确认该安装可正常启动版本探针。' })

    $payload = Get-HiaProbePayload -Output $HythonOutput
    $licenseUnavailable = $HythonOutput -match '(?i)No licenses could be found to run this application'
    $hythonPassed = (
        -not $HythonTimedOut -and
        $HythonExitCode -eq 0 -and
        $null -ne $payload -and
        [bool]$payload.hou_import
    )
    $checks += New-HiaCheckResult `
        -Id 'houdini.hython_probe' -Name 'Hython / hou probe' `
        -Level $(if ($hythonPassed) { 'green' } else { 'red' }) `
        -Message $(if ($hythonPassed) { "import hou 成功；Houdini build $($payload.build)；Python $($payload.python)" } elseif ($licenseUnavailable) { 'hython 无法取得 Houdini 许可证。' } elseif ($HythonTimedOut) { 'hython 只读探针超时。' } else { 'hython 无法 import hou 或未返回有效探针数据。' }) `
        -Advice $(if ($hythonPassed) { '无需处理。' } elseif ($licenseUnavailable) { '检查 Houdini License Administrator 与许可证服务器；关闭可能占用许可证 seat 的实例后重新扫描。' } else { '修复所选 Houdini 安装，并确认 hython 可执行 import hou。' })

    if ($null -ne $payload) {
        $expectedHython = [System.IO.Path]::GetFullPath($hythonExe)
        $reportedPython = ''
        try { $reportedPython = [System.IO.Path]::GetFullPath([string]$payload.executable) } catch { }
        $pythonMatches = $reportedPython -and [System.StringComparer]::OrdinalIgnoreCase.Equals($expectedHython, $reportedPython)
        $checks += New-HiaCheckResult `
            -Id 'houdini.python_match' -Name 'Houdini Python identity' `
            -Level $(if ($pythonMatches) { 'green' } else { 'red' }) `
            -Message $(if ($pythonMatches) { "内置 Python $($payload.python) 来自所选 hython.exe。" } else { 'hython 探针报告了不同的 Python executable。' }) `
            -Advice $(if ($pythonMatches) { '无需处理。' } else { '检查 Houdini 安装完整性，避免用其他 Python 替代 hython.exe。' })

        $hythonBuild = Get-HiaVersionText -Text ([string]$payload.build) -Fallback ''
        $buildMatches = ($houdiniProbePassed -and $hythonBuild -ne 'unknown' -and $houdiniBuild -eq $hythonBuild)
        $checks += New-HiaCheckResult `
            -Id 'houdini.build_match' -Name 'Houdini / hython build match' `
            -Level $(if ($buildMatches) { 'green' } else { 'red' }) `
            -Message $(if ($buildMatches) { "两项探针均为 build $houdiniBuild。" } else { "版本不匹配：Houdini=$houdiniBuild，hython=$hythonBuild。" }) `
            -Advice $(if ($buildMatches) { '无需处理。' } else { '选择同一安装 bin 目录中的 houdini.exe 与 hython.exe。' })
    }
    return $checks
}

function Invoke-HiaHoudiniChecks {
    param(
        [AllowEmptyString()][string]$HoudiniExe,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Candidates,
        [int]$TimeoutSeconds = 12,
        [hashtable]$ProbeOverrides = @{}
    )

    if (-not $HoudiniExe) {
        if ($Candidates.Count -eq 0) {
            return @(New-HiaCheckResult -Id 'houdini.selection' -Name 'Houdini selection' -Level 'red' `
                -Message '未发现 Houdini 安装。' -Advice '安装或选择 Houdini 后重新扫描。当前 Preview 已验证 Houdini 21.0.440 / Python 3.11：https://www.sidefx.com/download/ 。Big-Chicken Houdini Intelligence Agent 不会自动安装 Houdini、提权或修改系统配置。')
        }
        if ($Candidates.Count -gt 1) {
            return @(New-HiaCheckResult -Id 'houdini.selection' -Name 'Houdini selection' -Level 'red' `
                -Message "发现 $($Candidates.Count) 个 Houdini 版本，尚未选择。" -Advice '从下拉列表明确选择一个版本；启动器不会静默猜测。')
        }
        $HoudiniExe = [string]$Candidates[0].path
    }

    $houdiniExists = Test-Path -LiteralPath $HoudiniExe -PathType Leaf
    $hythonExe = Join-Path ([System.IO.Path]::GetDirectoryName($HoudiniExe)) 'hython.exe'
    $hythonExists = Test-Path -LiteralPath $hythonExe -PathType Leaf
    if (-not $houdiniExists -or -not $hythonExists) {
        return @(Test-HiaHoudiniProbeConsistency -HoudiniExe $HoudiniExe -HoudiniOutput '' -HythonOutput '')
    }

    if ($ProbeOverrides.ContainsKey('houdini')) {
        $houdiniProbe = $ProbeOverrides.houdini
    } else {
        $houdiniProbe = Invoke-HiaProcess -FilePath $HoudiniExe -Arguments @('-version') -TimeoutSeconds $TimeoutSeconds
    }
    if ($ProbeOverrides.ContainsKey('hython')) {
        $hythonProbe = $ProbeOverrides.hython
    } else {
        $probeCode = "import json,sys,hou;print('$script:HiaProbeMarker'+json.dumps({'build':hou.applicationVersionString(),'python':str(sys.version_info[0])+'.'+str(sys.version_info[1]),'executable':sys.executable,'hou_import':True},sort_keys=True))"
        $hythonProbe = Invoke-HiaProcess -FilePath $hythonExe -Arguments @('-B', '-c', $probeCode) -TimeoutSeconds $TimeoutSeconds
    }
    return @(Test-HiaHoudiniProbeConsistency `
        -HoudiniExe $HoudiniExe `
        -HoudiniOutput ("$($houdiniProbe.stdout)`n$($houdiniProbe.stderr)") `
        -HoudiniExitCode $(if ($null -eq $houdiniProbe.exit_code) { -1 } else { [int]$houdiniProbe.exit_code }) `
        -HythonOutput ("$($hythonProbe.stdout)`n$($hythonProbe.stderr)") `
        -HythonExitCode $(if ($null -eq $hythonProbe.exit_code) { -1 } else { [int]$hythonProbe.exit_code }) `
        -HoudiniTimedOut:([bool]$houdiniProbe.timed_out) `
        -HythonTimedOut:([bool]$hythonProbe.timed_out))
}

function Get-HiaPinnedCodexExecutable {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $contractRoot = Join-Path $ProjectRoot 'contracts\codex-app-server'
    $runtimeRoot = Join-Path $ProjectRoot '.runtime\toolchains\codex'
    $matches = @()
    foreach ($contract in @(Get-ChildItem -LiteralPath $contractRoot -Directory -ErrorAction SilentlyContinue)) {
        $candidate = Join-Path (Join-Path $runtimeRoot $contract.Name) 'codex.exe'
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $matches += [pscustomobject]@{ version = $contract.Name; path = $candidate }
        }
    }
    return @($matches | Sort-Object -Property version -Descending)
}

function Test-HiaRuntimeWritable {
    param([Parameter(Mandatory = $true)][string]$RuntimePath)

    try {
        if (-not (Test-Path -LiteralPath $RuntimePath -PathType Container)) { return $false }
        $probePath = Join-Path $RuntimePath ('.hia-write-probe-' + [Guid]::NewGuid().ToString('N') + '.tmp')
        $stream = [System.IO.FileStream]::new(
            $probePath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None,
            4096,
            [System.IO.FileOptions]::DeleteOnClose
        )
        try { $stream.WriteByte(1) } finally { $stream.Dispose() }
        return $true
    } catch {
        return $false
    }
}

function Test-HiaPathWithinDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Directory
    )

    if ([string]::IsNullOrWhiteSpace($Directory)) { return $false }
    try {
        $normalizedPath = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
        $normalizedDirectory = [System.IO.Path]::GetFullPath($Directory).TrimEnd('\')
    } catch {
        return $false
    }
    return (
        [System.StringComparer]::OrdinalIgnoreCase.Equals($normalizedPath, $normalizedDirectory) -or
        $normalizedPath.StartsWith(
            $normalizedDirectory + '\',
            [System.StringComparison]::OrdinalIgnoreCase
        )
    )
}

function Resolve-HiaRenderOutputDirectory {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [AllowEmptyString()][string]$Path = '',
        [AllowEmptyString()][string]$HoudiniExe = '',
        [switch]$Create
    )

    $root = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
    $requested = if ([string]::IsNullOrWhiteSpace($Path)) {
        Join-Path $root '.runtime\cache'
    } else {
        $Path.Trim()
    }
    if ($requested.StartsWith('\') -or $requested -notmatch '^[A-Za-z]:[\\/]') {
        throw '最终输出目录必须是普通本地盘的绝对路径；不接受相对、UNC 或设备路径。'
    }

    try {
        $resolved = [System.IO.Path]::GetFullPath($requested).TrimEnd('\')
    } catch {
        throw '最终输出目录不是有效的 Windows 路径。'
    }
    if (
        $resolved -notmatch '^[A-Za-z]:\\' -or
        $resolved.Substring(2).Contains(':') -or
        $resolved -match '^[A-Za-z]:$'
    ) {
        throw '最终输出目录必须是普通本地盘目录；不接受盘符根、ADS 或设备路径。'
    }
    try {
        $drive = [System.IO.DriveInfo]::new([System.IO.Path]::GetPathRoot($resolved))
        if ($drive.DriveType -notin @(
            [System.IO.DriveType]::Fixed,
            [System.IO.DriveType]::Removable
        )) {
            throw 'not local'
        }
    } catch {
        throw '最终输出目录必须位于可用的本地固定盘或可移动盘。'
    }

    $forbiddenRoots = [System.Collections.Generic.List[string]]::new()
    if ($env:SystemRoot) { $forbiddenRoots.Add([string]$env:SystemRoot) }
    if ($env:HFS) { $forbiddenRoots.Add([string]$env:HFS) }
    if ($HoudiniExe) {
        try {
            $houdiniBin = [System.IO.Path]::GetDirectoryName([System.IO.Path]::GetFullPath($HoudiniExe))
            if ($houdiniBin) {
                $houdiniRoot = [System.IO.Directory]::GetParent($houdiniBin)
                if ($null -ne $houdiniRoot) { $forbiddenRoots.Add($houdiniRoot.FullName) }
            }
        } catch { }
    }
    foreach ($forbiddenRoot in $forbiddenRoots) {
        if (Test-HiaPathWithinDirectory -Path $resolved -Directory $forbiddenRoot) {
            throw '最终输出目录不能位于 Windows 或 Houdini 安装目录中。'
        }
    }
    if ($resolved -match '(?i)\\Side Effects Software\\Houdini[^\\]*(?:\\|$)') {
        throw '最终输出目录不能位于 Houdini 安装目录中。'
    }

    if ((Test-Path -LiteralPath $resolved) -and -not (Test-Path -LiteralPath $resolved -PathType Container)) {
        throw '最终输出目录指向了文件，而不是文件夹。'
    }
    if ($Create) {
        try {
            [System.IO.Directory]::CreateDirectory($resolved) | Out-Null
        } catch {
            throw '最终输出目录无法创建。'
        }
    }

    $probeDirectory = $resolved
    while (-not (Test-Path -LiteralPath $probeDirectory -PathType Container)) {
        $parent = [System.IO.Directory]::GetParent($probeDirectory)
        if ($null -eq $parent) { break }
        $probeDirectory = $parent.FullName
    }
    if (
        -not (Test-Path -LiteralPath $probeDirectory -PathType Container) -or
        -not (Test-HiaRuntimeWritable -RuntimePath $probeDirectory)
    ) {
        throw '最终输出目录不存在且无法创建，或目录不可写。'
    }
    return $resolved
}

function Invoke-HiaScreenshotCacheCleanup {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [AllowNull()][object]$Plan = $null,
        [switch]$Delete
    )

    if (
        [string]::IsNullOrWhiteSpace($ProjectRoot) -or
        $ProjectRoot.Trim() -notmatch '^[A-Za-z]:[\\/]'
    ) {
        throw '截图缓存清理要求启动器提供普通本地盘上的绝对项目根目录。'
    }
    try {
        $fullSuppliedRoot = [System.IO.Path]::GetFullPath($ProjectRoot.Trim())
        $driveRoot = [System.IO.Path]::GetPathRoot($fullSuppliedRoot)
        if (
            [System.StringComparer]::OrdinalIgnoreCase.Equals(
                $fullSuppliedRoot.TrimEnd('\'),
                $driveRoot.TrimEnd('\')
            )
        ) {
            throw 'drive root is not a project directory'
        }
        $suppliedRoot = $fullSuppliedRoot.TrimEnd('\')
        $root = Get-HiaProjectRoot -StartingPath $suppliedRoot
        if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($suppliedRoot, $root)) {
            throw 'project root mismatch'
        }
        $runtimePath = [System.IO.Path]::GetFullPath((Join-Path $root '.runtime')).TrimEnd('\')
        $cachePath = [System.IO.Path]::GetFullPath((Join-Path $root '.runtime\cache')).TrimEnd('\')
        $expected = [System.IO.Path]::GetFullPath(
            (Join-Path $root '.runtime\cache\screenshots')
        ).TrimEnd('\')
    } catch {
        throw '项目根或截图缓存目标路径无法安全解析；已拒绝清理。'
    }

    $target = $expected
    if ($Delete) {
        if ($null -eq $Plan) { throw '缺少已由用户确认的截图缓存清理预览；已拒绝清理。' }
        $targetProperty = $Plan.PSObject.Properties['target_path']
        if ($null -eq $targetProperty -or [string]::IsNullOrWhiteSpace([string]$targetProperty.Value)) {
            throw '截图缓存清理预览没有有效目标；已拒绝清理。'
        }
        try {
            $target = [System.IO.Path]::GetFullPath([string]$targetProperty.Value).TrimEnd('\')
        } catch {
            throw '截图缓存清理预览目标无法规范化；已拒绝清理。'
        }
    }
    if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($target, $expected)) {
        throw '截图缓存目标与项目内唯一允许目录不精确相等；已拒绝清理。'
    }

    $pathChain = @($root, $runtimePath, $cachePath, $expected)
    $assertSafePathChain = {
        param([bool]$RequireAll)

        foreach ($pathToCheck in $pathChain) {
            try {
                $pathItem = Get-Item -LiteralPath $pathToCheck -Force -ErrorAction Stop
            } catch {
                if (-not $RequireAll -and -not (Test-Path -LiteralPath $pathToCheck)) {
                    return $false
                }
                throw '截图缓存路径链缺失或无法读取；已拒绝清理。'
            }
            if (-not $pathItem.PSIsContainer) {
                throw '截图缓存路径链包含非目录对象；已拒绝清理。'
            }
            if (
                ([int]$pathItem.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0
            ) {
                throw '项目根或截图缓存路径链包含 reparse point、junction 或 symlink；已拒绝清理。'
            }
        }
        return $true
    }

    if (-not (& $assertSafePathChain ([bool]$Delete))) {
        return [pscustomobject]@{
            target_path = $expected
            directory_exists = $false
            matched_count = 0
            matched_bytes = [long]0
            deleted_count = 0
            deleted_bytes = [long]0
            skipped_count = 0
            failed_count = 0
            candidates = @()
        }
    }

    if ($Delete) {
        $candidatesProperty = $Plan.PSObject.Properties['candidates']
        $skippedProperty = $Plan.PSObject.Properties['skipped_count']
        if ($null -eq $candidatesProperty -or $null -eq $skippedProperty) {
            throw '截图缓存清理预览内容不完整；已拒绝清理。'
        }
        $matches = @($candidatesProperty.Value)
        $skippedCount = [Math]::Max(0, [int]$skippedProperty.Value)
    } else {
        $matches = [System.Collections.Generic.List[object]]::new()
        $skippedCount = 0
    }

    if (-not $Delete) {
        $directory = [System.IO.DirectoryInfo]::new($expected)
        foreach ($entry in @($directory.GetFileSystemInfos())) {
            try {
                if (
                    ([int]$entry.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0
                ) {
                    $skippedCount++
                    continue
                }
                if ($entry -isnot [System.IO.FileInfo]) {
                    $skippedCount++
                    continue
                }
                if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($entry.Extension, '.png')) {
                    $skippedCount++
                    continue
                }
                $parentPath = [System.IO.Path]::GetFullPath($entry.DirectoryName).TrimEnd('\')
                if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($parentPath, $expected)) {
                    $skippedCount++
                    continue
                }
                $matches.Add([pscustomobject]@{
                    path = $entry.FullName
                    bytes = [long]$entry.Length
                    last_write_utc_ticks = [long]$entry.LastWriteTimeUtc.Ticks
                })
            } catch {
                $skippedCount++
            }
        }
    }

    $matchedBytes = [long]0
    foreach ($match in $matches) { $matchedBytes += [long]$match.bytes }
    if (-not $Delete) {
        return [pscustomobject]@{
            target_path = $expected
            directory_exists = $true
            matched_count = $matches.Count
            matched_bytes = $matchedBytes
            deleted_count = 0
            deleted_bytes = [long]0
            skipped_count = $skippedCount
            failed_count = 0
            candidates = $matches.ToArray()
        }
    }

    $deletedCount = 0
    $deletedBytes = [long]0
    $failedCount = 0
    foreach ($match in $matches) {
        [void](& $assertSafePathChain $true)
        try {
            $pathProperty = $match.PSObject.Properties['path']
            $bytesProperty = $match.PSObject.Properties['bytes']
            $timeProperty = $match.PSObject.Properties['last_write_utc_ticks']
            if ($null -eq $pathProperty -or $null -eq $bytesProperty -or $null -eq $timeProperty) {
                throw '清理预览中的文件记录不完整。'
            }
            $candidatePath = [System.IO.Path]::GetFullPath([string]$pathProperty.Value)
            $candidateParent = [System.IO.Path]::GetFullPath(
                [System.IO.Path]::GetDirectoryName($candidatePath)
            ).TrimEnd('\')
            if (
                -not [System.StringComparer]::OrdinalIgnoreCase.Equals($candidateParent, $expected) -or
                -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
                    [System.IO.Path]::GetExtension($candidatePath),
                    '.png'
                )
            ) {
                throw '清理预览中的文件路径不再满足精确目录或扩展名限制。'
            }
            $file = Get-Item -LiteralPath $candidatePath -Force -ErrorAction Stop
            $fileParent = [System.IO.Path]::GetFullPath($file.DirectoryName).TrimEnd('\')
            if (
                $file -isnot [System.IO.FileInfo] -or
                ([int]$file.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
                -not [System.StringComparer]::OrdinalIgnoreCase.Equals($file.Extension, '.png') -or
                -not [System.StringComparer]::OrdinalIgnoreCase.Equals($fileParent, $expected)
            ) {
                throw '文件在删除前不再满足截图缓存安全条件。'
            }
            if (
                [long]$file.Length -ne [long]$bytesProperty.Value -or
                [long]$file.LastWriteTimeUtc.Ticks -ne [long]$timeProperty.Value
            ) {
                throw '文件在用户确认后发生变化；已跳过。'
            }
            $fileBytes = [long]$file.Length
            [System.IO.File]::Delete($file.FullName)
            if ([System.IO.File]::Exists($file.FullName)) {
                throw '文件删除后仍然存在。'
            }
            $deletedCount++
            $deletedBytes += $fileBytes
        } catch {
            $failedCount++
            $skippedCount++
        }
    }

    return [pscustomobject]@{
        target_path = $expected
        directory_exists = $true
        matched_count = $matches.Count
        matched_bytes = $matchedBytes
        deleted_count = $deletedCount
        deleted_bytes = $deletedBytes
        skipped_count = $skippedCount
        failed_count = $failedCount
        candidates = @()
    }
}

function Test-HiaLoopbackPorts {
    try {
        $first = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
        $second = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
        try {
            $first.Start()
            $second.Start()
            $firstAddress = ([System.Net.IPEndPoint]$first.LocalEndpoint).Address
            $secondAddress = ([System.Net.IPEndPoint]$second.LocalEndpoint).Address
            return ($firstAddress.Equals([System.Net.IPAddress]::Loopback) -and $secondAddress.Equals([System.Net.IPAddress]::Loopback))
        } finally {
            try { $first.Stop() } catch { }
            try { $second.Stop() } catch { }
        }
    } catch {
        return $false
    }
}

function Invoke-HiaProjectChecks {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [AllowEmptyString()][string]$HoudiniExe = '',
        [AllowEmptyString()][string]$BridgePython = '',
        [AllowEmptyString()][string]$RenderOutputDir = '',
        [ValidateSet('hia_v2', 'fxhoudini')][string]$McpBackend = 'hia_v2',
        [int]$TimeoutSeconds = 12,
        [hashtable]$ProbeOverrides = @{}
    )

    $checks = @()
    $requiredPaths = [ordered]@{
        'project.launch_script' = @('Launcher lifecycle script', 'scripts\launch-houdini.ps1')
        'project.bridge_source' = @('Bridge source', 'services\bridge\hia_bridge\__main__.py')
        'project.core_source' = @('Project Python source', 'src\hia_core\__init__.py')
        'project.houdini_package' = @('Houdini package', 'houdini_package\packages\houdini_intelligence.json')
        'project.panel' = @('Houdini Python Panel', 'houdini_package\python_panels\houdini_intelligence.pypanel')
        'project.codex_config' = @('Codex project config', '.codex\config.toml')
        'project.pyproject' = @('Project declaration', 'pyproject.toml')
    }
    foreach ($entry in $requiredPaths.GetEnumerator()) {
        $path = Join-Path $ProjectRoot $entry.Value[1]
        $exists = Test-Path -LiteralPath $path -PathType Leaf
        $checks += New-HiaCheckResult -Id $entry.Key -Name $entry.Value[0] `
            -Level $(if ($exists) { 'green' } else { 'red' }) `
            -Message $(if ($exists) { "存在：$($entry.Value[1])" } else { "缺少：$($entry.Value[1])" }) `
            -Advice $(if ($exists) { '无需处理。' } else { '从完整项目副本恢复该项目本地文件。' })
    }

    $runtimePath = Join-Path $ProjectRoot '.runtime'
    $runtimeWritable = if ($ProbeOverrides.ContainsKey('runtime_writable')) {
        [bool]$ProbeOverrides.runtime_writable
    } else {
        Test-HiaRuntimeWritable -RuntimePath $runtimePath
    }
    $checks += New-HiaCheckResult -Id 'project.runtime_writable' -Name '.runtime writable' `
        -Level $(if ($runtimeWritable) { 'green' } else { 'red' }) `
        -Message $(if ($runtimeWritable) { '.runtime 可写，探针文件已自动清除。' } else { '.runtime 不存在或不可写。' }) `
            -Advice $(if ($runtimeWritable) { '无需处理。' } else { '点击“修复安全项目”创建目录，或修复项目目录权限。' })

    try {
        $resolvedRenderOutput = Resolve-HiaRenderOutputDirectory `
            -ProjectRoot $ProjectRoot `
            -Path $RenderOutputDir `
            -HoudiniExe $HoudiniExe
        $renderOutputExists = Test-Path -LiteralPath $resolvedRenderOutput -PathType Container
        $renderOutputLevel = if ($renderOutputExists) { 'green' } else { 'yellow' }
        $renderOutputMessage = if ([string]::IsNullOrWhiteSpace($RenderOutputDir)) {
            "未指定最终输出目录；将使用项目本地 $resolvedRenderOutput。"
        } elseif ($renderOutputExists) {
            "最终输出目录存在且可写：$resolvedRenderOutput"
        } else {
            "最终输出目录尚不存在，但启动时可以创建：$resolvedRenderOutput"
        }
        $checks += New-HiaCheckResult -Id 'project.render_output' -Name 'Final render output directory' `
            -Level $renderOutputLevel `
            -Message $renderOutputMessage `
            -Advice $(if ($renderOutputExists) { '无需处理。' } else { '启动 Houdini 时会创建一次；也可使用“选择…”先创建并选择目录。' })
    } catch {
        $checks += New-HiaCheckResult -Id 'project.render_output' -Name 'Final render output directory' `
            -Level 'red' `
            -Message ([string]$_.Exception.Message) `
            -Advice '选择可创建、可写的普通本地绝对目录；不要选择 Windows 或 Houdini 安装目录。'
    }

    $loopbackAvailable = if ($ProbeOverrides.ContainsKey('loopback')) {
        [bool]$ProbeOverrides.loopback
    } else {
        Test-HiaLoopbackPorts
    }
    $checks += New-HiaCheckResult -Id 'project.loopback_ports' -Name 'Loopback port allocation' `
        -Level $(if ($loopbackAvailable) { 'green' } else { 'red' }) `
        -Message $(if ($loopbackAvailable) { '可在 127.0.0.1 临时分配两个端口；探针已关闭。' } else { '无法在 127.0.0.1 分配本地端口。' }) `
        -Advice $(if ($loopbackAvailable) { '无需处理。' } else { '检查本机防火墙、Winsock 和端口策略；启动器不会监听外网。' })

    $bridgePathAllowed = $false
    if ($BridgePython) {
        try {
            $bridgeFullPath = [System.IO.Path]::GetFullPath($BridgePython)
            $pythonOrgUserInstall = (
                $bridgeFullPath -match '(?i)\\Users\\[^\\]+\\AppData\\Local\\Programs\\Python\\[^\\]+\\python\.exe$'
            )
            $bridgePathAllowed = (
                $bridgeFullPath -match '^[A-Za-z]:\\' -and
                -not $bridgeFullPath.Substring(2).Contains(':') -and
                $bridgeFullPath -notmatch '(?i)\\WindowsApps\\' -and
                (
                    $bridgeFullPath -notmatch '(?i)\\AppData\\' -or
                    $pythonOrgUserInstall
                )
            )
        } catch { }
    }
    if (-not $BridgePython) {
        $checks += New-HiaCheckResult -Id 'bridge.python' -Name 'Bridge Python' -Level 'red' `
            -Message '尚未选择有效的 Bridge Python executable。' `
            -Advice '安装并选择 CPython 3.10+ 的 python.exe；当前测试基线为 3.10：https://www.python.org/downloads/windows/ 。Big-Chicken Houdini Intelligence Agent 不会自动安装 Python、提权或修改 PATH/注册表。'
    } elseif (-not $bridgePathAllowed) {
        $checks += New-HiaCheckResult -Id 'bridge.python' -Name 'Bridge Python' -Level 'red' `
            -Message 'Bridge Python 必须是普通本地盘绝对路径；WindowsApps、普通 AppData 路径、UNC、相对路径和 ADS 均不接受。' `
            -Advice '选择项目本地工具链，或 python.org 默认安装在 AppData\Local\Programs\Python 下的 python.exe。'
    } elseif (-not (Test-Path -LiteralPath $BridgePython -PathType Leaf)) {
        $checks += New-HiaCheckResult -Id 'bridge.python' -Name 'Bridge Python' -Level 'red' `
            -Message '尚未选择有效的 Bridge Python executable。' `
            -Advice '安装并选择 CPython 3.10+ 的 python.exe；当前测试基线为 3.10：https://www.python.org/downloads/windows/ 。Big-Chicken Houdini Intelligence Agent 不会自动安装 Python、提权或修改 PATH/注册表。'
    } else {
        if ($ProbeOverrides.ContainsKey('bridge')) {
            $bridgeProbe = $ProbeOverrides.bridge
        } else {
            $bridgeImports = "import hia_bridge;import hia_core"
            $bridgeImportNames = "['hia_bridge','hia_core']"
            $bridgePythonPaths = @(
                (Join-Path $ProjectRoot 'services\bridge'),
                (Join-Path $ProjectRoot 'src')
            )
            if ($McpBackend -eq 'hia_v2') {
                $bridgeImports += ';import hia_mcp_v2'
                $bridgeImportNames = "['hia_bridge','hia_core','hia_mcp_v2']"
                $bridgePythonPaths += (Join-Path $ProjectRoot 'services\hia_mcp_v2')
            }
            $bridgeCode = "import json,sys;$bridgeImports;print('$script:HiaProbeMarker'+json.dumps({'python':str(sys.version_info[0])+'.'+str(sys.version_info[1]),'executable':sys.executable,'imports':$bridgeImportNames},sort_keys=True))"
            $bridgePythonPath = $bridgePythonPaths -join ';'
            $bridgeProbe = Invoke-HiaProcess -FilePath $BridgePython -Arguments @('-B', '-c', $bridgeCode) `
                -TimeoutSeconds $TimeoutSeconds -WorkingDirectory $ProjectRoot -Environment @{
                    'PYTHONPATH' = $bridgePythonPath
                    'PYTHONDONTWRITEBYTECODE' = '1'
                    'PYTHONNOUSERSITE' = '1'
                    'HIA_PROJECT_ROOT' = $ProjectRoot
                }
        }
        $bridgePayload = Get-HiaProbePayload -Output ("$($bridgeProbe.stdout)`n$($bridgeProbe.stderr)")
        $bridgePassed = (-not [bool]$bridgeProbe.timed_out -and $bridgeProbe.exit_code -eq 0 -and $null -ne $bridgePayload)
        $versionPassed = $false
        $identityPassed = $false
        if ($bridgePassed) {
            try {
                $version = [version]([string]$bridgePayload.python + '.0')
                $versionPassed = $version -ge [version]'3.10.0'
            } catch { }
            try {
                $identityPassed = [System.StringComparer]::OrdinalIgnoreCase.Equals(
                    [System.IO.Path]::GetFullPath($BridgePython),
                    [System.IO.Path]::GetFullPath([string]$bridgePayload.executable)
                )
            } catch { }
        }
        $bridgeLevel = if ($bridgePassed -and $versionPassed -and $identityPassed) { 'green' } else { 'red' }
        $checks += New-HiaCheckResult -Id 'bridge.python' -Name 'Bridge Python' -Level $bridgeLevel `
            -Message $(if ($bridgeLevel -eq 'green') { "Python $($bridgePayload.python)；Bridge 与所选 MCP backend import 成功。" } elseif ([bool]$bridgeProbe.timed_out) { 'Bridge Python 探针超时。' } else { 'Bridge Python 版本、executable 身份或所选 MCP backend import 不符合项目要求。' }) `
            -Advice $(if ($bridgeLevel -eq 'green') { '无需处理。' } else { '选择 CPython 3.10+（测试基线 3.10）并按 README 验证项目 import：https://www.python.org/downloads/windows/ 。Big-Chicken Houdini Intelligence Agent 不会自动安装或修改系统环境。' })
    }

    $codexMatches = @(Get-HiaPinnedCodexExecutable -ProjectRoot $ProjectRoot)
    if ($codexMatches.Count -ne 1) {
        $checks += New-HiaCheckResult -Id 'codex.executable' -Name 'Project Codex executable' -Level 'red' `
            -Message "与项目协议版本匹配的 codex.exe 数量为 $($codexMatches.Count)。" `
            -Advice "点击「安装/修复 Codex」下载并校验官方固定版本 $script:HiaCodexVersion；仅写入项目 .runtime，不修改系统 PATH。"
    } else {
        $codex = $codexMatches[0]
        if ($ProbeOverrides.ContainsKey('codex_version')) {
            $codexVersionProbe = $ProbeOverrides.codex_version
        } else {
            $codexVersionProbe = Invoke-HiaProcess -FilePath $codex.path -Arguments @('--version') -TimeoutSeconds $TimeoutSeconds -WorkingDirectory $ProjectRoot
        }
        $reportedCodexVersion = Get-HiaVersionText -Text ("$($codexVersionProbe.stdout)`n$($codexVersionProbe.stderr)") -Fallback ''
        $codexVersionPassed = (-not [bool]$codexVersionProbe.timed_out -and $codexVersionProbe.exit_code -eq 0 -and $reportedCodexVersion -eq $codex.version)
        $checks += New-HiaCheckResult -Id 'codex.executable' -Name 'Project Codex executable' `
            -Level $(if ($codexVersionPassed) { 'green' } else { 'red' }) `
            -Message $(if ($codexVersionPassed) { "codex $reportedCodexVersion 与项目协议锁定版本匹配。" } else { "codex 版本探针失败或与锁定版本 $($codex.version) 不匹配。" }) `
            -Advice $(if ($codexVersionPassed) { '无需处理。' } else { "点击「安装/修复 Codex」恢复官方固定版本 $script:HiaCodexVersion；仅写入项目 .runtime。" })

        if ($ProbeOverrides.ContainsKey('codex_login')) {
            $loginProbe = $ProbeOverrides.codex_login
        } else {
            $loginProbe = Invoke-HiaProcess -FilePath $codex.path -Arguments @('login', 'status') -TimeoutSeconds $TimeoutSeconds `
                -WorkingDirectory $ProjectRoot -Environment @{ 'CODEX_HOME' = (Join-Path $ProjectRoot '.runtime\codex-home') }
        }
        $loggedIn = (-not [bool]$loginProbe.timed_out -and $loginProbe.exit_code -eq 0)
        $checks += New-HiaCheckResult -Id 'codex.login' -Name 'Codex login status' `
            -Level $(if ($loggedIn) { 'green' } else { 'red' }) `
            -Message $(if ($loggedIn) { '项目本地 CODEX_HOME 已登录；未读取凭据内容。' } else { '项目本地 CODEX_HOME 尚未登录或状态探针失败。' }) `
            -Advice $(if ($loggedIn) { '无需处理。' } else { '点击「复制登录命令」，在 PowerShell 中运行项目本地 device login。命令不含凭据，报告也不会读取凭据内容。' })
    }

    if ($McpBackend -eq 'hia_v2') {
        $hiaService = Join-Path $ProjectRoot 'services\hia_mcp_v2\hia_mcp_v2\__main__.py'
        $hiaRuntime = Join-Path $ProjectRoot 'houdini_package\python_libs\hia_mcp_runtime\http_server.py'
        $hiaUiReady310 = Join-Path $ProjectRoot 'houdini_package\python3.10libs\uiready.py'
        $hiaUiReady311 = Join-Path $ProjectRoot 'houdini_package\python3.11libs\uiready.py'
        $hiaFilesPresent = (
            (Test-Path -LiteralPath $hiaService -PathType Leaf) -and
            (Test-Path -LiteralPath $hiaRuntime -PathType Leaf) -and
            (Test-Path -LiteralPath $hiaUiReady310 -PathType Leaf) -and
            (Test-Path -LiteralPath $hiaUiReady311 -PathType Leaf)
        )
        $checks += New-HiaCheckResult -Id 'hia_mcp_v2.runtime' -Name 'HIA MCP V2' `
            -Level $(if ($hiaFilesPresent) { 'green' } else { 'red' }) `
            -Message $(if ($hiaFilesPresent) { 'stdio package、Houdini runtime 与 UI-ready 启动钩子存在。' } else { 'HIA MCP V2 的 stdio package、Houdini runtime 或 UI-ready 启动钩子不完整。' }) `
            -Advice $(if ($hiaFilesPresent) { '无需处理。' } else { '从完整项目副本恢复 services\hia_mcp_v2 与 houdini_package 中的 HIA MCP V2 文件。' })
    } else {
        $fxRoot = Join-Path $ProjectRoot ".runtime\fxhoudinimcp\$script:HiaFxHoudiniVersion"
        $fxPython = Join-Path $fxRoot 'venv\Scripts\python.exe'
        $fxSource = Join-Path $fxRoot 'source\python\fxhoudinimcp\_version.py'
        $fxHoudiniSource = Join-Path $fxRoot 'source\houdini\scripts\python'
        $fxFilesPresent = (
            (Test-Path -LiteralPath $fxPython -PathType Leaf) -and
            (Test-Path -LiteralPath $fxSource -PathType Leaf) -and
            (Test-Path -LiteralPath $fxHoudiniSource -PathType Container)
        )
        if (-not $fxFilesPresent) {
            $checks += New-HiaCheckResult -Id 'fxhoudinimcp.runtime' -Name 'FXHoudini MCP 1.3.0' -Level 'red' `
                -Message '项目本地 FXHoudini MCP 1.3.0 source 或 venv 不完整。' `
                -Advice '按项目文档给出的锁定命令恢复 .runtime\fxhoudinimcp\1.3.0；启动器不会自动下载。'
        } else {
            if ($ProbeOverrides.ContainsKey('fxhoudinimcp')) {
                $fxProbe = $ProbeOverrides.fxhoudinimcp
            } else {
                $fxCode = "import json,sys;from fxhoudinimcp._version import __version__;import fxhoudinimcp.server;print('$script:HiaProbeMarker'+json.dumps({'version':__version__,'python':str(sys.version_info[0])+'.'+str(sys.version_info[1]),'executable':sys.executable},sort_keys=True))"
                $fxProbe = Invoke-HiaProcess -FilePath $fxPython -Arguments @('-B', '-c', $fxCode) -TimeoutSeconds $TimeoutSeconds `
                    -WorkingDirectory $ProjectRoot -Environment @{
                        'PYTHONDONTWRITEBYTECODE' = '1'
                        'PYTHONNOUSERSITE' = '1'
                    }
            }
            $fxPayload = Get-HiaProbePayload -Output ("$($fxProbe.stdout)`n$($fxProbe.stderr)")
            $fxPassed = (
                -not [bool]$fxProbe.timed_out -and
                $fxProbe.exit_code -eq 0 -and
                $null -ne $fxPayload -and
                [string]$fxPayload.version -eq $script:HiaFxHoudiniVersion
            )
            $checks += New-HiaCheckResult -Id 'fxhoudinimcp.runtime' -Name 'FXHoudini MCP 1.3.0' `
                -Level $(if ($fxPassed) { 'green' } else { 'red' }) `
                -Message $(if ($fxPassed) { "source、venv 和必要 import 通过；版本 $($fxPayload.version)。" } else { 'FXHoudini MCP import 探针失败或版本不是 1.3.0。' }) `
                -Advice $(if ($fxPassed) { '无需处理。' } else { '按项目锁定配置重建项目本地 venv；不要安装到全局 Python。' })
        }
    }

    $configPath = Join-Path $ProjectRoot '.codex\config.toml'
    if (Test-Path -LiteralPath $configPath -PathType Leaf) {
        $config = [System.IO.File]::ReadAllText($configPath)
        $portableCommand = $config -match "(?m)^command\s*=\s*'\.runtime\\fxhoudinimcp\\1\.3\.0\\venv\\Scripts\\python\.exe'\s*$"
        $portableCwd = $config -match "(?m)^cwd\s*=\s*'\.'\s*$"
        $portable = $portableCommand -and $portableCwd
        $checks += New-HiaCheckResult -Id 'project.portable_codex_config' -Name 'Portable Codex config' `
            -Level $(if ($portable) { 'green' } else { 'yellow' }) `
            -Message $(if ($portable) { '.codex/config.toml 使用项目相对路径。' } else { '.codex/config.toml 含非便携 command/cwd。' }) `
            -Advice $(if ($portable) { '无需处理。' } else { '点击“修复安全项目”仅规范项目本地锁定路径。' })

        $projectMcpOptional = $config -match '(?m)^required\s*=\s*false\s*$'
        $projectMcpRequired = $config -match '(?m)^required\s*=\s*true\s*$'
        $checks += New-HiaCheckResult -Id 'project.codex_config_required' -Name 'Ordinary project MCP requirement' `
            -Level $(if ($projectMcpOptional) { 'green' } else { 'red' }) `
            -Message $(if ($projectMcpOptional) { '普通项目配置为 required=false，不会因离线 Houdini MCP 阻断任务恢复。' } elseif ($projectMcpRequired) { '普通项目配置为 required=true，会在相对 MCP command 不可执行时阻断任务恢复。' } else { '普通项目配置缺少明确的 required=false。' }) `
            -Advice $(if ($projectMcpOptional) { '无需处理；Bridge 仍会为受控 Houdini 生命周期注入 required=true。' } else { '将跟踪配置设为 enabled=true、required=false；不要移除 Bridge 的进程级 required=true 覆盖。' })
    }

    $packageConfigPath = Join-Path $ProjectRoot 'houdini_package\packages\houdini_intelligence.json'
    if (Test-Path -LiteralPath $packageConfigPath -PathType Leaf) {
        $portablePackage = $false
        try {
            $packageConfig = [System.IO.File]::ReadAllText($packageConfigPath) | ConvertFrom-Json
            $portablePackage = ([string]$packageConfig.path -eq '$HIA_PROJECT_ROOT/houdini_package')
            foreach ($entry in @($packageConfig.env)) {
                if ($null -ne $entry.PSObject.Properties['HIA_PROJECT_ROOT']) {
                    $portablePackage = $false
                }
                if ($null -ne $entry.PSObject.Properties['PYTHONPATH']) {
                    $pythonPathValue = [string]$entry.PYTHONPATH.value
                    if (-not $pythonPathValue.StartsWith('$HIA_PROJECT_ROOT/', [System.StringComparison]::Ordinal)) {
                        $portablePackage = $false
                    }
                }
            }
        } catch {
            $portablePackage = $false
        }
        $checks += New-HiaCheckResult -Id 'project.portable_houdini_package' -Name 'Portable Houdini package' `
            -Level $(if ($portablePackage) { 'green' } else { 'yellow' }) `
            -Message $(if ($portablePackage) { 'Houdini package 使用 HIA_PROJECT_ROOT 派生路径。' } else { 'Houdini package 无法解析或仍含安装位置绝对路径。' }) `
            -Advice $(if ($portablePackage) { '无需处理。' } else { '点击“修复安全项目”仅规范项目本地 package 路径。' })
    }
    return $checks
}

function Invoke-HiaPreflight {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [AllowEmptyString()][string]$HoudiniExe = '',
        [AllowEmptyString()][string]$BridgePython = '',
        [AllowEmptyString()][string]$RenderOutputDir = '',
        [ValidateSet('hia_v2', 'fxhoudini')][string]$McpBackend = 'hia_v2',
        [object[]]$Candidates = @(),
        [int]$TimeoutSeconds = 12,
        [hashtable]$ProbeOverrides = @{}
    )

    $root = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
    if ($Candidates.Count -eq 0) {
        $Candidates = @(Get-HiaHoudiniCandidates -ExplicitPath $HoudiniExe)
    }
    $checks = @()
    $checks += @(Invoke-HiaHoudiniChecks -HoudiniExe $HoudiniExe -Candidates $Candidates -TimeoutSeconds $TimeoutSeconds -ProbeOverrides $ProbeOverrides)
    $checks += @(Invoke-HiaProjectChecks -ProjectRoot $root -HoudiniExe $HoudiniExe -BridgePython $BridgePython -RenderOutputDir $RenderOutputDir -McpBackend $McpBackend -TimeoutSeconds $TimeoutSeconds -ProbeOverrides $ProbeOverrides)
    $level = Get-HiaOverallLevel -Checks $checks
    return [pscustomobject]@{
        schema_version = 1
        generated_at_utc = [DateTime]::UtcNow.ToString('o')
        project_root = $root
        overall = $level
        selected_houdini = $HoudiniExe
        bridge_python = $BridgePython
        render_output_dir = $RenderOutputDir
        mcp_backend = $McpBackend
        candidates = @($Candidates)
        checks = @($checks)
        report = [pscustomobject]@{ json_path = ''; log_path = '' }
    }
}

function ConvertTo-HiaRedactedJson {
    param(
        [Parameter(Mandatory = $true)]$Value,
        [int]$Depth = 12,
        [switch]$Compress
    )

    $json = if ($Compress) {
        $Value | ConvertTo-Json -Depth $Depth -Compress
    } else {
        $Value | ConvertTo-Json -Depth $Depth
    }
    $sensitiveName = '(?:token|cookie|api[_-]?key|authorization|password|secret|auth(?:orization)?[_-]?code)'
    $json = [regex]::Replace(
        $json,
        '(?i)("[^"\r\n]*' + $sensitiveName + '[^"\r\n]*"\s*:\s*)"(?:\\.|[^"\\])*"',
        '$1"[REDACTED]"'
    )
    $json = [regex]::Replace($json, '(?i)Bearer\s+[A-Za-z0-9._~+\-/=]+', 'Bearer [REDACTED]')
    $json = [regex]::Replace($json, '(?i)\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{8,}', '[REDACTED]')
    $json = [regex]::Replace(
        $json,
        '(?i)(' + $sensitiveName + '\s*[:=]\s*)[^\s",;}]+',
        '$1[REDACTED]'
    )
    return $json
}

function Write-HiaPreflightReport {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$Result,
        [Parameter(Mandatory = $true)][string]$ProjectRoot
    )

    $reportDirectory = Join-Path $ProjectRoot '.runtime\launcher'
    [System.IO.Directory]::CreateDirectory($reportDirectory) | Out-Null
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fff') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
    $jsonPath = Join-Path $reportDirectory "preflight-$stamp.json"
    $logPath = Join-Path $reportDirectory "preflight-$stamp.log"
    $Result.report.json_path = $jsonPath
    $Result.report.log_path = $logPath

    $utf8 = [System.Text.UTF8Encoding]::new($false)
    $json = ConvertTo-HiaRedactedJson -Value $Result -Depth 12
    [System.IO.File]::WriteAllText($jsonPath, $json + [Environment]::NewLine, $utf8)

    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.Add('Big-Chicken Houdini Intelligence Agent launcher preflight')
    $lines.Add("Generated (UTC): $($Result.generated_at_utc)")
    $lines.Add("Overall: $($Result.overall)")
    $lines.Add("Project root: $($Result.project_root)")
    $lines.Add("Selected Houdini: $($Result.selected_houdini)")
    $lines.Add("Bridge Python: $($Result.bridge_python)")
    $lines.Add('')
    foreach ($check in $Result.checks) {
        $lines.Add("[$($check.level.ToUpperInvariant())] $($check.name): $($check.message)")
        $lines.Add("  Fix: $($check.advice)")
    }
    $logText = $lines -join [Environment]::NewLine
    $logText = [regex]::Replace($logText, '(?i)Bearer\s+\S+', 'Bearer [REDACTED]')
    $logText = [regex]::Replace($logText, '(?i)\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{8,}', '[REDACTED]')
    [System.IO.File]::WriteAllText($logPath, $logText + [Environment]::NewLine, $utf8)
    return $Result.report
}

function Get-HiaLatestLauncherCheckpoint {
    param(
        [Parameter(Mandatory = $true)][string]$CheckpointDirectory,
        [AllowEmptyString()][string]$ThreadId = '',
        [AllowEmptyString()][string]$GoalBinding = ''
    )

    if (-not (Test-Path -LiteralPath $CheckpointDirectory -PathType Container)) { return $null }
    try {
        $directory = Get-Item -LiteralPath $CheckpointDirectory -Force -ErrorAction Stop
        if (
            ([int]$directory.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            return $null
        }
        if ($ThreadId) {
            if ($ThreadId -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$') { return $null }
            if ($GoalBinding -notmatch '^[0-9a-f]{64}$') { return $null }
            $markerPath = Join-Path $directory.FullName '.hia-stage-checkpoint.json'
            if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) { return $null }
            $markerFile = Get-Item -LiteralPath $markerPath -Force -ErrorAction Stop
            if (
                ([int]$markerFile.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
                [long]$markerFile.Length -le 0 -or
                [long]$markerFile.Length -gt 65536
            ) {
                return $null
            }
            $marker = [System.IO.File]::ReadAllText($markerFile.FullName) | ConvertFrom-Json
            $checkpointName = [string]$marker.checkpoint_file
            if (
                [int]$marker.version -ne 1 -or
                -not [System.StringComparer]::Ordinal.Equals([string]$marker.thread_id, $ThreadId) -or
                -not [System.StringComparer]::Ordinal.Equals([string]$marker.goal_binding, $GoalBinding) -or
                -not $checkpointName -or
                $checkpointName -ne [System.IO.Path]::GetFileName($checkpointName) -or
                $checkpointName -notmatch '(?i)\.hip(?:lc|nc)?(?:_bak\d*)?$'
            ) {
                return $null
            }
            $checkpoint = Get-Item `
                -LiteralPath (Join-Path $directory.FullName $checkpointName) `
                -Force `
                -ErrorAction Stop
            if (
                $checkpoint -isnot [System.IO.FileInfo] -or
                ([int]$checkpoint.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
                [long]$checkpoint.Length -le 0
            ) {
                return $null
            }
            return [pscustomobject]@{
                path = $checkpoint.FullName
                last_write_utc_ticks = [long]$checkpoint.LastWriteTimeUtc.Ticks
                thread_id = $ThreadId
                goal_binding = $GoalBinding
            }
        }
        $candidates = [System.Collections.Generic.List[object]]::new()
        foreach ($file in @($directory.GetFiles())) {
            if (
                ([int]$file.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
                $file.Name -notmatch '(?i)\.hip(?:lc|nc)?(?:_bak\d*)?$'
            ) {
                continue
            }
            $candidates.Add([pscustomobject]@{
                path = $file.FullName
                last_write_utc_ticks = [long]$file.LastWriteTimeUtc.Ticks
            })
        }
        return @($candidates | Sort-Object -Property last_write_utc_ticks -Descending | Select-Object -First 1)
    } catch {
        return $null
    }
}

function Get-HiaCrashRecoveryDecision {
    param(
        [Parameter(Mandatory = $true)][int]$ExitCode,
        [Parameter(Mandatory = $true)][bool]$FocusVerified,
        [Parameter(Mandatory = $true)][bool]$ThreadIdle,
        [ValidateRange(0, 1000)][int]$ConsecutiveCrashCount = 0,
        [ValidateRange(0, 1000)][int]$AutomaticRestartCount = 0,
        [ValidateRange(1, 1000)][int]$MaxConsecutiveCrashes = 3,
        [ValidateRange(1, 1000)][int]$MaxAutomaticRestarts = 6
    )

    if ($ExitCode -eq 0) {
        return [pscustomobject]@{ recover = $false; reason = 'normal_exit' }
    }
    if (-not $FocusVerified) {
        return [pscustomobject]@{ recover = $false; reason = 'focus_not_verified' }
    }
    if (-not $ThreadIdle) {
        return [pscustomobject]@{ recover = $false; reason = 'thread_not_idle' }
    }
    if (
        $ConsecutiveCrashCount -gt $MaxConsecutiveCrashes -or
        $AutomaticRestartCount -ge $MaxAutomaticRestarts
    ) {
        return [pscustomobject]@{ recover = $false; reason = 'bounded_limit' }
    }
    return [pscustomobject]@{ recover = $true; reason = 'recover' }
}

function Get-HiaLatestLauncherCrashHip {
    param(
        [Parameter(Mandatory = $true)][string]$TempDirectory,
        [Parameter(Mandatory = $true)][int]$HoudiniProcessId,
        [Parameter(Mandatory = $true)][long]$StartedAtUtcTicks,
        [Parameter(Mandatory = $true)][long]$EndedAtUtcTicks
    )

    if (
        $HoudiniProcessId -le 0 -or
        $StartedAtUtcTicks -le 0 -or
        $EndedAtUtcTicks -lt $StartedAtUtcTicks -or
        -not (Test-Path -LiteralPath $TempDirectory -PathType Container)
    ) {
        return $null
    }
    try {
        $directory = Get-Item -LiteralPath $TempDirectory -Force -ErrorAction Stop
        if (
            $directory.Name -ne 'tmp' -or
            $directory.Parent.Name -notmatch '^[0-9a-fA-F]{32}$' -or
            $directory.Parent.Parent.Name -ne 'launcher-sessions' -or
            ([int]$directory.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            return $null
        }
        $earliest = $StartedAtUtcTicks - [TimeSpan]::FromSeconds(5).Ticks
        $latest = $EndedAtUtcTicks + [TimeSpan]::FromSeconds(60).Ticks
        $namePattern = '(?i)^crash\..+_' + [regex]::Escape([string]$HoudiniProcessId) + '\.hip(?:lc|nc)?$'
        $candidates = [System.Collections.Generic.List[object]]::new()
        foreach ($file in @($directory.GetFiles())) {
            $ticks = [long]$file.LastWriteTimeUtc.Ticks
            if (
                ([int]$file.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
                [long]$file.Length -le 0 -or
                $file.Name -notmatch $namePattern -or
                $ticks -lt $earliest -or
                $ticks -gt $latest
            ) {
                continue
            }
            $candidates.Add([pscustomobject]@{
                path = $file.FullName
                last_write_utc_ticks = $ticks
                houdini_process_id = $HoudiniProcessId
            })
        }
        return @(
            $candidates |
                Sort-Object -Property last_write_utc_ticks -Descending |
                Select-Object -First 1
        )
    } catch {
        return $null
    }
}

function Copy-HiaLauncherRecoveryHip {
    param(
        [Parameter(Mandatory = $true)][string]$SessionRoot,
        [Parameter(Mandatory = $true)][string]$SourcePath,
        [Parameter(Mandatory = $true)][ValidateRange(1, 99)][int]$Attempt
    )

    $session = Get-Item -LiteralPath $SessionRoot -Force -ErrorAction Stop
    if (
        $session -isnot [System.IO.DirectoryInfo] -or
        $session.Name -notmatch '^[0-9a-fA-F]{32}$' -or
        $session.Parent.Name -ne 'launcher-sessions' -or
        ([int]$session.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw 'Recovery requires an ordinary launcher session directory.'
    }
    $source = Get-Item -LiteralPath $SourcePath -Force -ErrorAction Stop
    $checkpoints = Join-Path $session.FullName 'checkpoints'
    $temp = Join-Path $session.FullName 'tmp'
    $sourceParent = $source.Directory.FullName.TrimEnd('\')
    if (
        $source -isnot [System.IO.FileInfo] -or
        ([int]$source.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
        [long]$source.Length -le 0 -or
        -not (
            [System.StringComparer]::OrdinalIgnoreCase.Equals($sourceParent, $checkpoints.TrimEnd('\')) -or
            [System.StringComparer]::OrdinalIgnoreCase.Equals($sourceParent, $temp.TrimEnd('\'))
        )
    ) {
        throw 'Recovery source must be one ordinary top-level HIP in this launcher session.'
    }
    $suffixMatch = [regex]::Match(
        $source.Name,
        '(\.hip(?:lc|nc)?(?:_bak\d*)?)$',
        [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
    )
    if (-not $suffixMatch.Success) {
        throw 'Recovery source is not a supported Houdini HIP file.'
    }
    $recoveryDirectory = Join-Path $session.FullName 'recovery'
    [System.IO.Directory]::CreateDirectory($recoveryDirectory) | Out-Null
    $recoveryItem = Get-Item -LiteralPath $recoveryDirectory -Force -ErrorAction Stop
    if (
        ([int]$recoveryItem.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw 'Recovery destination is a reparse point.'
    }
    $destination = Join-Path $recoveryDirectory (
        'recovery-{0}-{1}{2}' -f `
            $Attempt,
            [Guid]::NewGuid().ToString('N').Substring(0, 16),
            [string]$suffixMatch.Groups[1].Value
    )
    [System.IO.File]::Copy($source.FullName, $destination, $false)
    return [pscustomobject]@{
        path = $destination
        source_path = $source.FullName
    }
}

function Get-HiaRecoverableLauncherSession {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $suppliedRoot = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
    $root = Get-HiaProjectRoot -StartingPath $suppliedRoot
    if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($suppliedRoot, $root)) {
        throw 'Recovery discovery requires the exact launcher project root.'
    }
    $sessionsRoot = Join-Path $root '.runtime\launcher-sessions'
    if (-not (Test-Path -LiteralPath $sessionsRoot -PathType Container)) { return $null }

    try {
        foreach ($pathToCheck in @($root, (Join-Path $root '.runtime'), $sessionsRoot)) {
            $item = Get-Item -LiteralPath $pathToCheck -Force -ErrorAction Stop
            if (([int]$item.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                return $null
            }
        }
        $sessionsDirectory = Get-Item -LiteralPath $sessionsRoot -Force -ErrorAction Stop
        $recoverable = [System.Collections.Generic.List[object]]::new()
        foreach ($sessionDirectory in @($sessionsDirectory.GetDirectories())) {
            if (
                $sessionDirectory.Name -notmatch '^[0-9a-fA-F]{32}$' -or
                ([int]$sessionDirectory.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0
            ) {
                continue
            }
            $manifestPath = Join-Path $sessionDirectory.FullName 'session.json'
            if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { continue }
            $manifestFile = Get-Item -LiteralPath $manifestPath -Force -ErrorAction SilentlyContinue
            if (
                $null -eq $manifestFile -or
                ([int]$manifestFile.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
                [long]$manifestFile.Length -gt 65536
            ) {
                continue
            }
            try {
                $manifest = [System.IO.File]::ReadAllText($manifestPath) | ConvertFrom-Json
            } catch {
                continue
            }
            $idProperty = $manifest.PSObject.Properties['session_id']
            if (
                $null -eq $idProperty -or
                -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
                    [string]$idProperty.Value,
                    $sessionDirectory.Name
                )
            ) {
                continue
            }
            $decisionProperty = $manifest.PSObject.Properties['recovery_decision']
            if ($null -ne $decisionProperty -and -not [string]::IsNullOrWhiteSpace([string]$decisionProperty.Value)) {
                continue
            }
            $stateProperty = $manifest.PSObject.Properties['state']
            $state = if ($null -eq $stateProperty) { '' } else { [string]$stateProperty.Value }
            $exitProperty = $manifest.PSObject.Properties['process_exit_code']
            $exitKnown = ($null -ne $exitProperty -and $null -ne $exitProperty.Value)
            try { $exitCode = if ($exitKnown) { [int]$exitProperty.Value } else { $null } } catch { continue }
            if ($state -eq 'completed' -or ($exitKnown -and $exitCode -eq 0)) { continue }
            if ($state -notin @('starting', 'running', 'abnormal_exit', 'launch_failed') -and -not ($exitKnown -and $exitCode -ne 0)) {
                continue
            }
            if ($state -in @('starting', 'running')) {
                $recordedProcessIsActive = $false
                foreach ($processField in @('launcher_process_id', 'houdini_process_id')) {
                    $processProperty = $manifest.PSObject.Properties[$processField]
                    if ($null -eq $processProperty -or $null -eq $processProperty.Value) { continue }
                    try {
                        $recordedProcess = Get-Process -Id ([int]$processProperty.Value) -ErrorAction Stop
                        if (-not $recordedProcess.HasExited) {
                            $recordedProcessIsActive = $true
                            break
                        }
                    } catch { }
                }
                if ($recordedProcessIsActive) { continue }
            }
            $checkpoint = Get-HiaLatestLauncherCheckpoint `
                -CheckpointDirectory (Join-Path $sessionDirectory.FullName 'checkpoints')
            if ($null -eq $checkpoint) { continue }

            $recoverable.Add([pscustomobject]@{
                session_id = $sessionDirectory.Name
                checkpoint_path = [string]$checkpoint.path
                checkpoint_last_write_utc_ticks = [long]$checkpoint.last_write_utc_ticks
            })
        }
        return @(
            $recoverable |
                Sort-Object -Property checkpoint_last_write_utc_ticks -Descending |
                Select-Object -First 1
        )
    } catch {
        return $null
    }
}

function Set-HiaLauncherRecoveryDecision {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{32}$')][string]$SessionId,
        [Parameter(Mandatory = $true)][ValidateSet('recover', 'normal')][string]$Decision
    )

    $suppliedRoot = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
    $root = Get-HiaProjectRoot -StartingPath $suppliedRoot
    if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals($suppliedRoot, $root)) {
        throw 'Recovery decision requires the exact launcher project root.'
    }
    $runtimeRoot = Join-Path $root '.runtime'
    $sessionsRoot = Join-Path $runtimeRoot 'launcher-sessions'
    $sessionRoot = Join-Path $sessionsRoot $SessionId
    $manifestPath = Join-Path $sessionRoot 'session.json'
    foreach ($pathToCheck in @($root, $runtimeRoot, $sessionsRoot, $sessionRoot, $manifestPath)) {
        $item = Get-Item -LiteralPath $pathToCheck -Force -ErrorAction Stop
        if (([int]$item.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'Recovery decision path contains a reparse point.'
        }
    }
    $manifestFile = Get-Item -LiteralPath $manifestPath -Force -ErrorAction Stop
    if ([long]$manifestFile.Length -gt 65536) { throw 'Recovery session manifest is too large.' }
    $manifest = [System.IO.File]::ReadAllText($manifestPath) | ConvertFrom-Json
    $idProperty = $manifest.PSObject.Properties['session_id']
    if (
        $null -eq $idProperty -or
        -not [System.StringComparer]::OrdinalIgnoreCase.Equals([string]$idProperty.Value, $SessionId)
    ) {
        throw 'Recovery session manifest identity does not match its directory.'
    }

    $allowedFields = @(
        'schema_version',
        'session_id',
        'state',
        'selected_houdini',
        'hip_path',
        'started_at_utc',
        'ended_at_utc',
        'process_exit_code',
        'latest_checkpoint',
        'launcher_process_id',
        'houdini_process_id'
    )
    $updated = [ordered]@{}
    foreach ($field in $allowedFields) {
        $property = $manifest.PSObject.Properties[$field]
        if ($null -ne $property) { $updated[$field] = $property.Value }
    }
    $updated['recovery_decision'] = $Decision
    $json = ConvertTo-HiaRedactedJson -Value $updated -Depth 4
    [System.IO.File]::WriteAllText(
        $manifestPath,
        $json + [Environment]::NewLine,
        [System.Text.UTF8Encoding]::new($false)
    )
    return $manifestPath
}

function Read-HiaLauncherSettings {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $settingsPath = Join-Path $ProjectRoot '.runtime\launcher\settings.json'
    if (-not (Test-Path -LiteralPath $settingsPath -PathType Leaf)) {
        return [pscustomobject]@{ houdini_exe = ''; bridge_python = ''; render_output_dir = ''; mcp_backend = 'hia_v2' }
    }
    try {
        $settings = [System.IO.File]::ReadAllText($settingsPath) | ConvertFrom-Json
        $backendProperty = $settings.PSObject.Properties['mcp_backend']
        $backend = if ($null -eq $backendProperty) {
            'hia_v2'
        } else {
            try {
                Resolve-HiaMcpBackend -Backend ([string]$backendProperty.Value)
            } catch {
                'hia_v2'
            }
        }
        return [pscustomobject]@{
            houdini_exe = [string]$settings.houdini_exe
            bridge_python = [string]$settings.bridge_python
            render_output_dir = if ($null -eq $settings.PSObject.Properties['render_output_dir']) { '' } else { [string]$settings.render_output_dir }
            mcp_backend = $backend
        }
    } catch {
        return [pscustomobject]@{ houdini_exe = ''; bridge_python = ''; render_output_dir = ''; mcp_backend = 'hia_v2' }
    }
}

function Write-HiaLauncherSettings {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$HoudiniExe,
        [Parameter(Mandatory = $true)][string]$BridgePython,
        [AllowEmptyString()][string]$RenderOutputDir = '',
        [ValidateSet('hia_v2', 'fxhoudini')][string]$McpBackend = 'hia_v2'
    )

    $directory = Join-Path $ProjectRoot '.runtime\launcher'
    [System.IO.Directory]::CreateDirectory($directory) | Out-Null
    $settingsPath = Join-Path $directory 'settings.json'
    $storedRenderOutput = if ([string]::IsNullOrWhiteSpace($RenderOutputDir)) {
        ''
    } else {
        Resolve-HiaRenderOutputDirectory `
            -ProjectRoot $ProjectRoot `
            -Path $RenderOutputDir `
            -HoudiniExe $HoudiniExe
    }
    $settings = [ordered]@{
        houdini_exe = [System.IO.Path]::GetFullPath($HoudiniExe)
        bridge_python = [System.IO.Path]::GetFullPath($BridgePython)
        render_output_dir = $storedRenderOutput
        mcp_backend = $McpBackend
    }
    $json = $settings | ConvertTo-Json
    [System.IO.File]::WriteAllText($settingsPath, $json + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
    return $settingsPath
}

function Repair-HiaSafeProject {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $actions = [System.Collections.Generic.List[string]]::new()
    foreach ($relative in @('.runtime', '.runtime\launcher', '.runtime\tmp')) {
        $path = Join-Path $ProjectRoot $relative
        if (-not (Test-Path -LiteralPath $path -PathType Container)) {
            if ($PSCmdlet.ShouldProcess($path, 'Create project-local runtime directory')) {
                [System.IO.Directory]::CreateDirectory($path) | Out-Null
                $actions.Add("已创建 $relative")
            }
        }
    }

    $codexConfigPath = Join-Path $ProjectRoot '.codex\config.toml'
    if (Test-Path -LiteralPath $codexConfigPath -PathType Leaf) {
        $source = [System.IO.File]::ReadAllText($codexConfigPath)
        $lines = @($source -split "`r?`n")
        $insideTarget = $false
        $commandFound = $false
        $cwdFound = $false
        $updatedLines = foreach ($line in $lines) {
            if ($line -match '^\s*\[mcp_servers\.houdini_intelligence\]\s*$') {
                $insideTarget = $true
                $line
                continue
            }
            if ($line -match '^\s*\[') { $insideTarget = $false }
            if ($insideTarget -and $line -match '^\s*command\s*=') {
                $commandFound = $true
                "command = '.runtime\fxhoudinimcp\1.3.0\venv\Scripts\python.exe'"
                continue
            }
            if ($insideTarget -and $line -match '^\s*cwd\s*=') {
                $cwdFound = $true
                "cwd = '.'"
                continue
            }
            $line
        }
        $updated = $updatedLines -join [Environment]::NewLine
        if (-not $commandFound -or -not $cwdFound) { $updated = $source }
        if ($updated -ne $source -and $PSCmdlet.ShouldProcess($codexConfigPath, 'Normalize project-relative MCP paths')) {
            [System.IO.File]::WriteAllText($codexConfigPath, $updated, [System.Text.UTF8Encoding]::new($false))
            $actions.Add('已将 .codex/config.toml 的 command/cwd 规范为项目相对路径')
        }
    }

    $packagePath = Join-Path $ProjectRoot 'houdini_package\packages\houdini_intelligence.json'
    if (Test-Path -LiteralPath $packagePath -PathType Leaf) {
        try {
            $package = [System.IO.File]::ReadAllText($packagePath) | ConvertFrom-Json
            $changed = $false
            if ([string]$package.path -ne '$HIA_PROJECT_ROOT/houdini_package') {
                $package.path = '$HIA_PROJECT_ROOT/houdini_package'
                $changed = $true
            }
            $portableEnvironment = [System.Collections.Generic.List[object]]::new()
            foreach ($entry in @($package.env)) {
                if ($null -ne $entry.PSObject.Properties['HIA_PROJECT_ROOT']) {
                    $changed = $true
                    if (@($entry.PSObject.Properties).Count -eq 1) { continue }
                    $entry.PSObject.Properties.Remove('HIA_PROJECT_ROOT')
                }
                if ($null -ne $entry.PSObject.Properties['PYTHONPATH']) {
                    $value = [string]$entry.PYTHONPATH.value
                    if ($value -match '(?i)/houdini_package/python_libs$') {
                        $entry.PYTHONPATH.value = '$HIA_PROJECT_ROOT/houdini_package/python_libs'
                        $changed = $true
                    } elseif ($value -match '(?i)/src$') {
                        $entry.PYTHONPATH.value = '$HIA_PROJECT_ROOT/src'
                        $changed = $true
                    }
                }
                $portableEnvironment.Add($entry)
            }
            $package.env = @($portableEnvironment)
            if ($changed -and $PSCmdlet.ShouldProcess($packagePath, 'Normalize project-relative Houdini package paths')) {
                $json = $package | ConvertTo-Json -Depth 8
                [System.IO.File]::WriteAllText($packagePath, $json + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
                $actions.Add('已将 Houdini package 规范为 HIA_PROJECT_ROOT 相对路径')
            }
        } catch {
            $actions.Add('警告：Houdini package JSON 无法安全解析，未修改')
        }
    }
    if ($actions.Count -eq 0) { $actions.Add('无需修复；未改动系统或全局环境') }
    return @($actions)
}

Export-ModuleMember -Function @(
    'ConvertTo-HiaProcessArgument',
    'ConvertTo-HiaRedactedJson',
    'Copy-HiaLauncherRecoveryHip',
    'Get-HiaBridgePythonCandidates',
    'Get-HiaCodexLoginCommand',
    'Get-HiaCrashRecoveryDecision',
    'Get-HiaHoudiniCandidates',
    'Get-HiaLatestLauncherCheckpoint',
    'Get-HiaLatestLauncherCrashHip',
    'Get-HiaMcpBackendChoices',
    'Get-HiaOverallLevel',
    'Get-HiaPinnedCodexExecutable',
    'Get-HiaProjectRoot',
    'Get-HiaProbePayload',
    'Get-HiaRecoverableLauncherSession',
    'Invoke-HiaScreenshotCacheCleanup',
    'Invoke-HiaPreflight',
    'Invoke-HiaProcess',
    'Read-HiaLauncherSettings',
    'Repair-HiaSafeProject',
    'Resolve-HiaRenderOutputDirectory',
    'Resolve-HiaMcpBackend',
    'Set-HiaLauncherRecoveryDecision',
    'Test-HiaHoudiniProbeConsistency',
    'Test-HiaLoopbackPorts',
    'Test-HiaRuntimeWritable',
    'Write-HiaLauncherSettings',
    'Write-HiaPreflightReport'
)
