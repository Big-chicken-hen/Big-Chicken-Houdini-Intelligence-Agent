Set-StrictMode -Version Latest

$script:HiaFxHoudiniVersion = '1.3.0'
$script:HiaProbeMarker = '__HIA_LAUNCHER_PROBE__'
$script:HiaDefaultMcpBackend = 'hia_v2'

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
    $hythonPassed = (
        -not $HythonTimedOut -and
        $HythonExitCode -eq 0 -and
        $null -ne $payload -and
        [bool]$payload.hou_import
    )
    $checks += New-HiaCheckResult `
        -Id 'houdini.hython_probe' -Name 'Hython / hou probe' `
        -Level $(if ($hythonPassed) { 'green' } else { 'red' }) `
        -Message $(if ($hythonPassed) { "import hou 成功；Houdini build $($payload.build)；Python $($payload.python)" } elseif ($HythonTimedOut) { 'hython 只读探针超时。' } else { 'hython 无法 import hou 或未返回有效探针数据。' }) `
        -Advice $(if ($hythonPassed) { '无需处理。' } else { '修复所选 Houdini 安装，并确认 hython 可执行 import hou。' })

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
        [Parameter(Mandatory = $true)][object[]]$Candidates,
        [int]$TimeoutSeconds = 12,
        [hashtable]$ProbeOverrides = @{}
    )

    if (-not $HoudiniExe) {
        if ($Candidates.Count -eq 0) {
            return @(New-HiaCheckResult -Id 'houdini.selection' -Name 'Houdini selection' -Level 'red' `
                -Message '未发现 Houdini 安装。' -Advice '选择准确的 houdini.exe，或安装 Houdini 后重新扫描。')
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
        [AllowEmptyString()][string]$BridgePython = '',
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
            $bridgePathAllowed = (
                $bridgeFullPath -match '^[A-Za-z]:\\' -and
                -not $bridgeFullPath.Substring(2).Contains(':') -and
                $bridgeFullPath -notmatch '(?i)\\(AppData|WindowsApps)\\'
            )
        } catch { }
    }
    if (-not $BridgePython -or -not (Test-Path -LiteralPath $BridgePython -PathType Leaf)) {
        $checks += New-HiaCheckResult -Id 'bridge.python' -Name 'Bridge Python' -Level 'red' `
            -Message '尚未选择有效的 Bridge Python executable。' `
            -Advice '选择 Python 3.10 或更高版本的 python.exe；不要安装到全局环境作为自动修复。'
    } elseif (-not $bridgePathAllowed) {
        $checks += New-HiaCheckResult -Id 'bridge.python' -Name 'Bridge Python' -Level 'red' `
            -Message 'Bridge Python 必须是普通本地盘绝对路径，且不能来自 AppData 或 WindowsApps。' `
            -Advice '选择项目本地或受控工具链中的 python.exe，使其符合实际生命周期脚本的路径策略。'
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
            -Advice $(if ($bridgeLevel -eq 'green') { '无需处理。' } else { '选择 Python 3.10+，并从项目根按 README 提示验证项目本地 import。' })
    }

    $codexMatches = @(Get-HiaPinnedCodexExecutable -ProjectRoot $ProjectRoot)
    if ($codexMatches.Count -ne 1) {
        $checks += New-HiaCheckResult -Id 'codex.executable' -Name 'Project Codex executable' -Level 'red' `
            -Message "与项目协议版本匹配的 codex.exe 数量为 $($codexMatches.Count)。" `
            -Advice '按项目锁定版本放置 codex.exe 到 .runtime\toolchains\codex\<version>；不要改系统 PATH。'
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
            -Advice $(if ($codexVersionPassed) { '无需处理。' } else { '恢复项目锁定的 Codex 本地 toolchain。' })

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
            -Advice $(if ($loggedIn) { '无需处理。' } else { '使用项目本地 CODEX_HOME 执行 codex login；报告不会读取或输出凭据内容。' })
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
    $checks += @(Invoke-HiaProjectChecks -ProjectRoot $root -BridgePython $BridgePython -McpBackend $McpBackend -TimeoutSeconds $TimeoutSeconds -ProbeOverrides $ProbeOverrides)
    $level = Get-HiaOverallLevel -Checks $checks
    return [pscustomobject]@{
        schema_version = 1
        generated_at_utc = [DateTime]::UtcNow.ToString('o')
        project_root = $root
        overall = $level
        selected_houdini = $HoudiniExe
        bridge_python = $BridgePython
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
    $lines.Add('Houdini Intelligence Agent launcher preflight')
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

function Read-HiaLauncherSettings {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $settingsPath = Join-Path $ProjectRoot '.runtime\launcher\settings.json'
    if (-not (Test-Path -LiteralPath $settingsPath -PathType Leaf)) {
        return [pscustomobject]@{ houdini_exe = ''; bridge_python = ''; mcp_backend = 'hia_v2' }
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
            mcp_backend = $backend
        }
    } catch {
        return [pscustomobject]@{ houdini_exe = ''; bridge_python = ''; mcp_backend = 'hia_v2' }
    }
}

function Write-HiaLauncherSettings {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$HoudiniExe,
        [Parameter(Mandatory = $true)][string]$BridgePython,
        [ValidateSet('hia_v2', 'fxhoudini')][string]$McpBackend = 'hia_v2'
    )

    $directory = Join-Path $ProjectRoot '.runtime\launcher'
    [System.IO.Directory]::CreateDirectory($directory) | Out-Null
    $settingsPath = Join-Path $directory 'settings.json'
    $settings = [ordered]@{
        houdini_exe = [System.IO.Path]::GetFullPath($HoudiniExe)
        bridge_python = [System.IO.Path]::GetFullPath($BridgePython)
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
    'Get-HiaBridgePythonCandidates',
    'Get-HiaHoudiniCandidates',
    'Get-HiaMcpBackendChoices',
    'Get-HiaOverallLevel',
    'Get-HiaPinnedCodexExecutable',
    'Get-HiaProjectRoot',
    'Get-HiaProbePayload',
    'Invoke-HiaPreflight',
    'Invoke-HiaProcess',
    'Read-HiaLauncherSettings',
    'Repair-HiaSafeProject',
    'Resolve-HiaMcpBackend',
    'Test-HiaHoudiniProbeConsistency',
    'Test-HiaLoopbackPorts',
    'Test-HiaRuntimeWritable',
    'Write-HiaLauncherSettings',
    'Write-HiaPreflightReport'
)
