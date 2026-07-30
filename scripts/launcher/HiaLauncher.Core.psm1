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

function Get-HiaEmbeddingContractData {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$PythonExe,
        [int]$TimeoutSeconds = 12
    )

    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
        throw 'Embedding contract requires a valid Bridge Python executable.'
    }
    $pythonCode = @"
import dataclasses
import json
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "src"))
from hia_core.embedding_contract import PROFILE_REGISTRY, launcher_contract, runtime_layout

contract = launcher_contract()
profiles = {key: dataclasses.asdict(value) for key, value in PROFILE_REGISTRY.items()}
if contract.get("profiles") != profiles:
    raise RuntimeError("launcher contract and profile registry disagree")
print("$script:HiaProbeMarker" + json.dumps(
    {"contract": contract, "profiles": profiles, "layout": runtime_layout(root)},
    ensure_ascii=False,
    sort_keys=True,
))
"@
    $probe = Invoke-HiaProcess -FilePath $PythonExe -Arguments @('-I', '-B', '-c', $pythonCode, $ProjectRoot) `
        -TimeoutSeconds $TimeoutSeconds -WorkingDirectory $ProjectRoot -Environment @{
            'PYTHONDONTWRITEBYTECODE' = '1'
            'PYTHONNOUSERSITE' = '1'
        }
    $payload = Get-HiaProbePayload -Output ("$($probe.stdout)`n$($probe.stderr)")
    if ([bool]$probe.timed_out -or $probe.exit_code -ne 0 -or $null -eq $payload) {
        throw 'The project embedding contract could not be loaded by Bridge Python.'
    }
    if (
        $null -eq $payload.contract -or
        $null -eq $payload.profiles -or
        $null -eq $payload.layout -or
        @($payload.profiles.PSObject.Properties).Count -eq 0
    ) {
        throw 'The project embedding contract payload is incomplete.'
    }
    return $payload
}

function Get-HiaEmbeddingProfileContract {
    param(
        [Parameter(Mandatory = $true)]$EmbeddingData,
        [Parameter(Mandatory = $true)][string]$Profile
    )

    $property = $EmbeddingData.profiles.PSObject.Properties[$Profile]
    if ($null -eq $property) { throw "Unsupported embedding profile: $Profile" }
    return $property.Value
}

function Resolve-HiaEmbeddingProfile {
    param(
        [Parameter(Mandatory = $true)]$EmbeddingData,
        [AllowEmptyString()][string]$Profile = ''
    )

    $resolved = if ([string]::IsNullOrWhiteSpace($Profile)) {
        [string]$EmbeddingData.contract.default_profile
    } else {
        $Profile.Trim()
    }
    [void](Get-HiaEmbeddingProfileContract -EmbeddingData $EmbeddingData -Profile $resolved)
    return $resolved
}

function Resolve-HiaEmbeddingDevice {
    param([AllowEmptyString()][string]$Device = '')

    $resolved = if ([string]::IsNullOrWhiteSpace($Device)) {
        'auto'
    } else {
        $Device.Trim().ToLowerInvariant()
    }
    if ($resolved -notin @('auto', 'cuda', 'cpu')) {
        throw "Unsupported embedding device: $Device"
    }
    return $resolved
}

function Get-HiaEmbeddingDeviceChoices {
    return @(
        [pscustomobject]@{
            id = 'auto'
            display = '自动（优先 NVIDIA GPU）'
        },
        [pscustomobject]@{
            id = 'cuda'
            display = 'NVIDIA GPU（CUDA）'
        },
        [pscustomobject]@{
            id = 'cpu'
            display = 'CPU'
        }
    )
}

function Get-HiaEmbeddingProfileChoices {
    param([Parameter(Mandatory = $true)]$EmbeddingData)

    $defaultProfile = [string]$EmbeddingData.contract.default_profile
    return @($EmbeddingData.profiles.PSObject.Properties | ForEach-Object {
        $profile = $_.Value
        $size = ([double]$profile.repository_size_gb).ToString(
            '0.##',
            [System.Globalization.CultureInfo]::InvariantCulture
        )
        $tier = if ([string]$profile.profile_id -eq $defaultProfile) {
            '默认 / 轻量'
        } else {
            '高质量 / 资源占用高'
        }
        [pscustomobject]@{
            id = [string]$profile.profile_id
            display = [string]$profile.label
            detail = "$tier · 官方模型文件约 $size GB"
            tooltip = "$($profile.model_id)；安装还需为独立 venv 与项目本地缓存预留空间。"
        }
    })
}

function Test-HiaEmbeddingProjectPath {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$Path,
        [ValidateSet('file', 'directory')][string]$Kind,
        [switch]$AllowMissingLeaf
    )

    try {
        $root = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
        $candidate = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
        if (
            -not [System.StringComparer]::OrdinalIgnoreCase.Equals($candidate, $root) -and
            -not $candidate.StartsWith(
                $root + [System.IO.Path]::DirectorySeparatorChar,
                [System.StringComparison]::OrdinalIgnoreCase
            )
        ) {
            return $false
        }
        if (
            $candidate.StartsWith('\\') -or
            ($candidate.Length -gt 2 -and $candidate.Substring(2).Contains(':'))
        ) {
            return $false
        }
        $rootItem = Get-Item -LiteralPath $root -Force -ErrorAction Stop
        if (
            ([int]$rootItem.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            return $false
        }
        $current = $root
        $missing = $false
        $relative = $candidate.Substring($root.Length).TrimStart('\')
        foreach ($part in @($relative -split '[\\/]')) {
            if (-not $part) { continue }
            $current = Join-Path $current $part
            if (Test-Path -LiteralPath $current -ErrorAction Stop) {
                if ($missing) { return $false }
                $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop
                if (
                    ([int]$item.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0
                ) {
                    return $false
                }
            } else {
                if (-not $AllowMissingLeaf) { return $false }
                $missing = $true
            }
        }
        if ($missing) { return $true }
        $pathType = if ($Kind -eq 'file') { 'Leaf' } else { 'Container' }
        return Test-Path -LiteralPath $candidate -PathType $pathType -ErrorAction Stop
    } catch {
        return $false
    }
}

function Resolve-HiaManagedPythonProjectPath {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$Path,
        [ValidateSet('file', 'directory')][string]$Kind,
        [switch]$AllowMissingLeaf
    )

    $root = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
    $candidate = [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
    $ordinary = if ($AllowMissingLeaf) {
        Test-HiaEmbeddingProjectPath `
            -ProjectRoot $root `
            -Path $candidate `
            -Kind $Kind `
            -AllowMissingLeaf
    } else {
        Test-HiaEmbeddingProjectPath `
            -ProjectRoot $root `
            -Path $candidate `
            -Kind $Kind
    }
    if ($ordinary) {
        return $candidate
    }

    try {
        $installRoot = [System.IO.Path]::GetFullPath(
            (Join-Path $root '.runtime\toolchains\python')
        ).TrimEnd('\')
        if (-not (
            Test-HiaEmbeddingProjectPath `
                -ProjectRoot $root `
                -Path $installRoot `
                -Kind directory
        )) {
            return $null
        }
        if (-not $candidate.StartsWith(
            $installRoot + [System.IO.Path]::DirectorySeparatorChar,
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            return $null
        }
        $relative = $candidate.Substring($installRoot.Length).TrimStart('\')
        $aliasName = @($relative -split '[\\/]')[0]
        if ($aliasName -notmatch '^cpython-(\d+)\.(\d+)-windows-x86_64-none$') {
            return $null
        }
        $major = [string]$matches[1]
        $minor = [string]$matches[2]
        $aliasPath = [System.IO.Path]::GetFullPath(
            (Join-Path $installRoot $aliasName)
        ).TrimEnd('\')
        $aliasItem = Get-Item -LiteralPath $aliasPath -Force -ErrorAction Stop
        $linkTypeProperty = $aliasItem.PSObject.Properties['LinkType']
        $targetProperty = $aliasItem.PSObject.Properties['Target']
        $targets = @()
        if ($null -ne $targetProperty) {
            $targets = @($targetProperty.Value)
        }
        $targetPath = if (
            $targets.Count -eq 1 -and
            [System.IO.Path]::IsPathRooted([string]$targets[0])
        ) {
            [System.IO.Path]::GetFullPath([string]$targets[0]).TrimEnd('\')
        } else {
            ''
        }
        if (
            $aliasItem -isnot [System.IO.DirectoryInfo] -or
            ([int]$aliasItem.Attributes -band
                [int][System.IO.FileAttributes]::ReparsePoint) -eq 0 -or
            $null -eq $linkTypeProperty -or
            -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
                [string]$linkTypeProperty.Value,
                'Junction'
            ) -or
            $targets.Count -ne 1 -or
            [string]::IsNullOrWhiteSpace($targetPath) -or
            -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
                [System.IO.Path]::GetFullPath(
                    (Split-Path -Parent $targetPath)
                ).TrimEnd('\'),
                $installRoot
            ) -or
            [System.IO.Path]::GetFileName($targetPath) -notmatch (
                '^cpython-{0}\.{1}\.\d+-windows-x86_64-none$' -f
                    [regex]::Escape($major),
                    [regex]::Escape($minor)
            ) -or
            -not (
                Test-HiaEmbeddingProjectPath `
                    -ProjectRoot $root `
                    -Path $targetPath `
                    -Kind directory
            ) -or
            -not (
                Test-HiaEmbeddingProjectPath `
                    -ProjectRoot $root `
                    -Path (Join-Path $targetPath 'python.exe') `
                    -Kind file
            )
        ) {
            return $null
        }
        $suffix = $candidate.Substring($aliasPath.Length).TrimStart('\')
        $resolved = if ([string]::IsNullOrWhiteSpace($suffix)) {
            $targetPath
        } else {
            [System.IO.Path]::GetFullPath((Join-Path $targetPath $suffix))
        }
        $safeResolved = if ($AllowMissingLeaf) {
            Test-HiaEmbeddingProjectPath `
                -ProjectRoot $root `
                -Path $resolved `
                -Kind $Kind `
                -AllowMissingLeaf
        } else {
            Test-HiaEmbeddingProjectPath `
                -ProjectRoot $root `
                -Path $resolved `
                -Kind $Kind
        }
        if (-not $safeResolved) {
            return $null
        }
        return $resolved
    } catch {
        return $null
    }
}

function Get-HiaManagedBridgePythonPath {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $root = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
    return [System.IO.Path]::GetFullPath(
        (Join-Path $root '.venv\Scripts\python.exe')
    )
}

function Get-HiaManagedBridgePythonState {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [AllowNull()]$ProbePayloadOverride = $null,
        [ValidateRange(2, 30)][int]$TimeoutSeconds = 8
    )

    $root = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
    $python = Get-HiaManagedBridgePythonPath -ProjectRoot $root
    $venvRoot = [System.IO.Path]::GetFullPath(
        (Join-Path $root '.venv')
    ).TrimEnd('\')
    $configPath = Join-Path $venvRoot 'pyvenv.cfg'
    try {
        if (-not (
            Test-HiaEmbeddingProjectPath `
                -ProjectRoot $root `
                -Path $python `
                -Kind file
        )) {
            throw '项目本地 managed Python 不存在、不是普通文件，或路径经过 reparse point。'
        }
        if (-not (
            Test-HiaEmbeddingProjectPath `
                -ProjectRoot $root `
                -Path $configPath `
                -Kind file
        )) {
            throw '项目本地 venv 缺少安全的 pyvenv.cfg。'
        }

        $config = @{}
        foreach ($line in @([System.IO.File]::ReadAllLines($configPath))) {
            if ($line -notmatch '^\s*([^#=]+?)\s*=\s*(.*?)\s*$') { continue }
            $config[$matches[1].Trim().ToLowerInvariant()] = $matches[2].Trim().Trim('"')
        }
        if (
            -not $config.ContainsKey('include-system-site-packages') -or
            -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
                [string]$config['include-system-site-packages'],
                'false'
            )
        ) {
            throw '项目本地 venv 必须设置 include-system-site-packages=false。'
        }
        $homeValue = if ($config.ContainsKey('home')) { [string]$config['home'] } else { '' }
        if ([string]::IsNullOrWhiteSpace($homeValue) -or -not [System.IO.Path]::IsPathRooted($homeValue)) {
            throw '项目本地 venv 的 Python home 无效。'
        }
        $pythonHomePath = Resolve-HiaManagedPythonProjectPath `
                -ProjectRoot $root `
                -Path $homeValue `
                -Kind directory
        if ([string]::IsNullOrWhiteSpace($pythonHomePath)) {
            throw '项目本地 venv 的 Python home 不在项目内，或路径不安全。'
        }

        $payload = $ProbePayloadOverride
        if ($null -eq $payload) {
            $probeCode = @"
import json
import site
import sys
import sysconfig

print("$script:HiaProbeMarker" + json.dumps({
    "python": ".".join(str(value) for value in sys.version_info[:3]),
    "executable": sys.executable,
    "prefix": sys.prefix,
    "base_prefix": sys.base_prefix,
    "base_exec_prefix": sys.base_exec_prefix,
    "purelib": sysconfig.get_path("purelib"),
    "platlib": sysconfig.get_path("platlib"),
    "site_packages": site.getsitepackages(),
    "sys_path": sys.path,
    "no_user_site": bool(sys.flags.no_user_site),
    "enable_user_site": bool(site.ENABLE_USER_SITE),
}, sort_keys=True))
"@
            $probe = Invoke-HiaProcess `
                -FilePath $python `
                -Arguments @('-I', '-B', '-c', $probeCode) `
                -TimeoutSeconds $TimeoutSeconds `
                -WorkingDirectory $root `
                -Environment @{
                    'HIA_PROJECT_ROOT' = $root
                    'PYTHONDONTWRITEBYTECODE' = '1'
                    'PYTHONNOUSERSITE' = '1'
                }
            if ([bool]$probe.timed_out -or $probe.exit_code -ne 0) {
                throw '项目本地 managed Python 健康探针失败。'
            }
            $payload = Get-HiaProbePayload -Output ("$($probe.stdout)`n$($probe.stderr)")
        }
        if ($null -eq $payload) {
            throw '项目本地 managed Python 未返回健康探针数据。'
        }

        $identityPaths = @(
            [pscustomobject]@{ name = 'executable'; value = [string]$payload.executable; kind = 'file' },
            [pscustomobject]@{ name = 'prefix'; value = [string]$payload.prefix; kind = 'directory' },
            [pscustomobject]@{ name = 'base_prefix'; value = [string]$payload.base_prefix; kind = 'directory' },
            [pscustomobject]@{ name = 'base_exec_prefix'; value = [string]$payload.base_exec_prefix; kind = 'directory' },
            [pscustomobject]@{ name = 'purelib'; value = [string]$payload.purelib; kind = 'directory' },
            [pscustomobject]@{ name = 'platlib'; value = [string]$payload.platlib; kind = 'directory' }
        )
        $resolvedIdentityPaths = @{}
        foreach ($entry in $identityPaths) {
            if ([string]::IsNullOrWhiteSpace([string]$entry.value)) {
                throw "项目本地 managed Python 缺少 $($entry.name) 探针值。"
            }
            $resolvedValue = Resolve-HiaManagedPythonProjectPath `
                    -ProjectRoot $root `
                    -Path ([string]$entry.value) `
                    -Kind ([string]$entry.kind)
            if ([string]::IsNullOrWhiteSpace($resolvedValue)) {
                throw "项目本地 managed Python 的 $($entry.name) 不在项目内，或路径不安全。"
            }
            $resolvedIdentityPaths[[string]$entry.name] = $resolvedValue
        }
        if (
            -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
                $pythonHomePath,
                [string]$resolvedIdentityPaths['base_prefix']
            ) -or
            -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
                $pythonHomePath,
                [string]$resolvedIdentityPaths['base_exec_prefix']
            )
        ) {
            throw '项目本地 managed Python 的基础解释器身份不匹配。'
        }
        if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals(
            [System.IO.Path]::GetFullPath([string]$payload.executable),
            $python
        )) {
            throw '项目本地 managed Python executable 身份不匹配。'
        }
        if (-not [System.StringComparer]::OrdinalIgnoreCase.Equals(
            [System.IO.Path]::GetFullPath([string]$payload.prefix).TrimEnd('\'),
            $venvRoot
        )) {
            throw '项目本地 managed Python 的 venv prefix 不匹配。'
        }
        try {
            if ([version]([string]$payload.python) -lt [version]'3.10') {
                throw '项目本地 managed Python 版本低于 3.10。'
            }
        } catch {
            if ($_.Exception.Message -like '*低于 3.10*') { throw }
            throw '项目本地 managed Python 版本信息无效。'
        }
        if ($payload.no_user_site -ne $true -or $payload.enable_user_site -ne $false) {
            throw '项目本地 managed Python 未隔离 user site-packages。'
        }
        foreach ($sitePath in @($payload.site_packages)) {
            if (
                [string]::IsNullOrWhiteSpace([string]$sitePath) -or
                [string]::IsNullOrWhiteSpace(
                    (Resolve-HiaManagedPythonProjectPath `
                        -ProjectRoot $root `
                        -Path ([string]$sitePath) `
                        -Kind directory)
                )
            ) {
                throw '项目本地 managed Python 探测到项目外 site-packages。'
            }
        }
        foreach ($sysPath in @($payload.sys_path)) {
            if ([string]::IsNullOrWhiteSpace([string]$sysPath)) { continue }
            if (-not [System.IO.Path]::IsPathRooted([string]$sysPath)) {
                throw '项目本地 managed Python 探测到相对 sys.path。'
            }
            $fullSysPath = [System.IO.Path]::GetFullPath([string]$sysPath)
            $kind = if ([System.IO.Path]::GetExtension($fullSysPath) -eq '.zip') {
                'file'
            } else {
                'directory'
            }
            $resolvedSysPath = Resolve-HiaManagedPythonProjectPath `
                    -ProjectRoot $root `
                    -Path $fullSysPath `
                    -Kind $kind `
                    -AllowMissingLeaf
            if ([string]::IsNullOrWhiteSpace($resolvedSysPath)) {
                throw '项目本地 managed Python 探测到项目外或不安全的 sys.path。'
            }
        }
        return [pscustomobject]@{
            path = $python
            relative_path = '.venv/Scripts/python.exe'
            healthy = $true
            reason = '项目本地 managed Python 已验证。'
            python_version = [string]$payload.python
        }
    } catch {
        return [pscustomobject]@{
            path = $python
            relative_path = '.venv/Scripts/python.exe'
            healthy = $false
            reason = [string]$_.Exception.Message
            python_version = ''
        }
    }
}

function Resolve-HiaManagedBridgePython {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [AllowNull()]$ProbePayloadOverride = $null,
        [ValidateRange(2, 30)][int]$TimeoutSeconds = 8
    )

    $state = Get-HiaManagedBridgePythonState `
        -ProjectRoot $ProjectRoot `
        -ProbePayloadOverride $ProbePayloadOverride `
        -TimeoutSeconds $TimeoutSeconds
    if (-not [bool]$state.healthy) {
        throw "项目本地 managed Python 尚未就绪：$($state.reason)"
    }
    return [string]$state.path
}

function Resolve-HiaLauncherStoragePath {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [AllowEmptyString()][string]$LeafName = '',
        [switch]$CreateDirectory,
        [switch]$AllowMissingLeaf
    )

    $root = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
    $runtimeRoot = Join-Path $root '.runtime'
    $launcherRoot = Join-Path $runtimeRoot 'launcher'
    foreach ($directoryPath in @($root, $runtimeRoot, $launcherRoot)) {
        $item = Get-Item `
            -LiteralPath $directoryPath `
            -Force `
            -ErrorAction SilentlyContinue
        if ($null -eq $item) {
            if (
                -not $CreateDirectory -or
                [System.StringComparer]::OrdinalIgnoreCase.Equals(
                    $directoryPath,
                    $root
                )
            ) {
                return $null
            }
            [System.IO.Directory]::CreateDirectory($directoryPath) | Out-Null
            $item = Get-Item `
                -LiteralPath $directoryPath `
                -Force `
                -ErrorAction Stop
        }
        if (
            $item -isnot [System.IO.DirectoryInfo] -or
            ([int]$item.Attributes -band
                [int][System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
            -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
                [System.IO.Path]::GetFullPath($item.FullName).TrimEnd('\'),
                [System.IO.Path]::GetFullPath($directoryPath).TrimEnd('\')
            )
        ) {
            throw 'Launcher storage path contains a reparse point or non-directory object.'
        }
    }

    if ([string]::IsNullOrWhiteSpace($LeafName)) {
        return $launcherRoot
    }
    if (
        $LeafName -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' -or
        [System.IO.Path]::GetFileName($LeafName) -ne $LeafName
    ) {
        throw 'Launcher storage leaf name is invalid.'
    }
    $leafPath = Join-Path $launcherRoot $LeafName
    $leaf = Get-Item `
        -LiteralPath $leafPath `
        -Force `
        -ErrorAction SilentlyContinue
    if ($null -eq $leaf) {
        if ($AllowMissingLeaf) { return $leafPath }
        return $null
    }
    if (
        $leaf -isnot [System.IO.FileInfo] -or
        ([int]$leaf.Attributes -band
            [int][System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
        -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
            [System.IO.Path]::GetFullPath($leaf.FullName),
            [System.IO.Path]::GetFullPath($leafPath)
        )
    ) {
        throw 'Launcher storage file must be an ordinary file.'
    }
    return $leafPath
}

function Get-HiaEmbeddingInstallLockInfo {
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $lockPath = Resolve-HiaLauncherStoragePath `
        -ProjectRoot $ProjectRoot `
        -LeafName 'embedding-install.lock' `
        -AllowMissingLeaf
    if (
        [string]::IsNullOrWhiteSpace($lockPath) -or
        -not (Test-Path -LiteralPath $lockPath -PathType Leaf)
    ) {
        return $null
    }

    $probe = $null
    try {
        $probe = [System.IO.FileStream]::new(
            $lockPath,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::Read
        )
        return $null
    } catch [System.IO.IOException] {
        $payload = $null
        $metadataStream = $null
        $metadataReader = $null
        try {
            $metadataStream = [System.IO.FileStream]::new(
                $lockPath,
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::Read,
                [System.IO.FileShare]::ReadWrite
            )
            $metadataReader = [System.IO.StreamReader]::new(
                $metadataStream,
                [System.Text.UTF8Encoding]::new($false),
                $true
            )
            $payload = $metadataReader.ReadToEnd() |
                ConvertFrom-Json
        } catch {
        } finally {
            if ($null -ne $metadataReader) { $metadataReader.Dispose() }
            if ($null -ne $metadataStream) { $metadataStream.Dispose() }
        }
        $logPath = ''
        if ($null -ne $payload) {
            $property = $payload.PSObject.Properties['log_path']
            if ($null -ne $property) {
                $candidate = [string]$property.Value
                if (-not [string]::IsNullOrWhiteSpace($candidate)) {
                    $candidateFull = [System.IO.Path]::GetFullPath($candidate)
                    $candidateName = [System.IO.Path]::GetFileName($candidateFull)
                    if ($candidateName -match '^embedding-install-[A-Za-z0-9-]+\.log$') {
                        $validated = Resolve-HiaLauncherStoragePath `
                            -ProjectRoot $ProjectRoot `
                            -LeafName $candidateName `
                            -AllowMissingLeaf
                        if (
                            [System.StringComparer]::OrdinalIgnoreCase.Equals(
                                $candidateFull,
                                $validated
                            )
                        ) {
                            $logPath = $validated
                        }
                    }
                }
            }
        }
        return [pscustomobject]@{
            active = $true
            lock_path = $lockPath
            log_path = $logPath
        }
    } finally {
        if ($null -ne $probe) { $probe.Dispose() }
    }
}

function Get-HiaEmbeddingModelDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)]$EmbeddingData,
        [Parameter(Mandatory = $true)]$ProfileContract
    )

    $modelsRoot = [System.IO.Path]::GetFullPath([string]$EmbeddingData.layout.models_root).TrimEnd('\')
    $candidate = [System.IO.Path]::GetFullPath(
        (Join-Path $ProjectRoot ([string]$ProfileContract.model_directory))
    ).TrimEnd('\')
    $prefix = $modelsRoot + [System.IO.Path]::DirectorySeparatorChar
    if (
        -not [System.StringComparer]::OrdinalIgnoreCase.Equals($candidate, $modelsRoot) -and
        -not $candidate.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        throw 'Embedding model directory escaped the contract models root.'
    }
    return $candidate
}

function Test-HiaEmbeddingModelInstall {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)]$EmbeddingData,
        [Parameter(Mandatory = $true)]$ProfileContract
    )

    try {
        $modelDirectory = Get-HiaEmbeddingModelDirectory `
            -ProjectRoot $ProjectRoot `
            -EmbeddingData $EmbeddingData `
            -ProfileContract $ProfileContract
        $manifestPath = Join-Path $modelDirectory '.hia-embedding-model.json'
        $configPath = Join-Path $modelDirectory 'config.json'
        if (
            -not (Test-HiaEmbeddingProjectPath -ProjectRoot $ProjectRoot -Path $modelDirectory -Kind directory) -or
            -not (Test-HiaEmbeddingProjectPath -ProjectRoot $ProjectRoot -Path $manifestPath -Kind file) -or
            -not (Test-HiaEmbeddingProjectPath -ProjectRoot $ProjectRoot -Path $configPath -Kind file)
        ) {
            return $false
        }
        $manifestFile = Get-Item -LiteralPath $manifestPath -Force -ErrorAction Stop
        if ([long]$manifestFile.Length -le 0 -or [long]$manifestFile.Length -gt 65536) {
            return $false
        }
        $manifest = [System.IO.File]::ReadAllText($manifestPath) | ConvertFrom-Json
        if (
            [int]$manifest.contract_version -ne [int]$EmbeddingData.contract.contract_version -or
            -not [System.StringComparer]::Ordinal.Equals(
                [string]$manifest.profile_id,
                [string]$ProfileContract.profile_id
            ) -or
            -not [System.StringComparer]::Ordinal.Equals(
                [string]$manifest.model_id,
                [string]$ProfileContract.model_id
            ) -or
            [string]::IsNullOrWhiteSpace([string]$manifest.revision)
        ) {
            return $false
        }
        $weights = @(
            Get-ChildItem -LiteralPath $modelDirectory -File -Filter '*.safetensors' -ErrorAction Stop |
                Where-Object {
                    Test-HiaEmbeddingProjectPath `
                        -ProjectRoot $ProjectRoot `
                        -Path $_.FullName `
                        -Kind file
                }
        )
        return $weights.Count -gt 0
    } catch {
        return $false
    }
}

function New-HiaKnowledgeIndexProcessPlan {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$BridgePython,
        [Parameter(Mandatory = $true)]$EmbeddingData,
        [AllowEmptyString()][string]$EmbeddingProfile = '',
        [AllowEmptyString()][string]$EmbeddingDevice = '',
        [ValidateSet('status', 'build')][string]$Action
    )

    $root = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
    $python = [System.IO.Path]::GetFullPath($BridgePython)
    if (
        -not (Test-Path -LiteralPath $root -PathType Container) -or
        -not (Test-Path -LiteralPath $python -PathType Leaf)
    ) {
        throw 'Knowledge index requires the project root and Bridge Python.'
    }

    $contract = $EmbeddingData.contract
    $knowledge = $contract.knowledge_index
    if (
        $null -eq $knowledge -or
        [string]$knowledge.python_role -ne 'bridge_python' -or
        $Action -notin @($knowledge.commands)
    ) {
        throw 'Knowledge index launcher contract is invalid.'
    }
    $pythonArguments = @($knowledge.python_args | ForEach-Object { [string]$_ })
    if (
        $pythonArguments.Count -ne 3 -or
        $pythonArguments[0] -ne '-B' -or
        $pythonArguments[1] -ne '-m' -or
        $pythonArguments[2] -ne [string]$knowledge.module
    ) {
        throw 'Knowledge index Python command is invalid.'
    }

    $pythonPathEntries = [System.Collections.Generic.List[string]]::new()
    foreach ($relativePath in @($knowledge.python_path)) {
        $relative = [string]$relativePath
        if (
            [string]::IsNullOrWhiteSpace($relative) -or
            [System.IO.Path]::IsPathRooted($relative)
        ) {
            throw 'Knowledge index PYTHONPATH entry must be project-relative.'
        }
        $candidate = [System.IO.Path]::GetFullPath((Join-Path $root $relative))
        if (-not (
            Test-HiaEmbeddingProjectPath `
                -ProjectRoot $root `
                -Path $candidate `
                -Kind directory
        )) {
            throw 'Knowledge index PYTHONPATH entry is unavailable or unsafe.'
        }
        $pythonPathEntries.Add($candidate)
    }
    if ($pythonPathEntries.Count -eq 0) {
        throw 'Knowledge index PYTHONPATH is empty.'
    }

    $selectedProfile = Resolve-HiaEmbeddingProfile `
        -EmbeddingData $EmbeddingData `
        -Profile $EmbeddingProfile
    $selected = Get-HiaEmbeddingProfileContract `
        -EmbeddingData $EmbeddingData `
        -Profile $selectedProfile
    $dimension = [int]$selected.default_dimension
    if (
        $dimension -lt [int]$selected.min_mrl_dimension -or
        $dimension -gt [int]$selected.max_dimension
    ) {
        throw 'Knowledge index embedding dimension is invalid.'
    }

    $environmentNames = [System.Collections.Generic.HashSet[string]]::new(
        [System.StringComparer]::Ordinal
    )
    foreach ($property in @($contract.environment.PSObject.Properties)) {
        $name = [string]$property.Value
        if ($name -notmatch '^[A-Za-z_][A-Za-z0-9_]{0,127}$') {
            throw 'Knowledge index embedding environment name is invalid.'
        }
        [void]$environmentNames.Add($name)
    }
    foreach ($profileProperty in @($EmbeddingData.profiles.PSObject.Properties)) {
        foreach ($name in @(
            [string]$profileProperty.Value.model_dir_environment,
            [string]$profileProperty.Value.model_revision_environment
        )) {
            if ($name -notmatch '^[A-Za-z_][A-Za-z0-9_]{0,127}$') {
                throw 'Knowledge index profile environment name is invalid.'
            }
            [void]$environmentNames.Add($name)
        }
    }

    $environment = @{
        'HIA_PROJECT_ROOT' = $root
        'PYTHONDONTWRITEBYTECODE' = '1'
        'PYTHONIOENCODING' = 'utf-8'
        'PYTHONNOUSERSITE' = '1'
        'PYTHONUTF8' = '1'
        'PYTHONPATH' = ($pythonPathEntries -join [System.IO.Path]::PathSeparator)
    }
    foreach ($entry in @{
        profile = $selectedProfile
        dimension = [string]$dimension
        device = Resolve-HiaEmbeddingDevice -Device $EmbeddingDevice
    }.GetEnumerator()) {
        $nameProperty = $contract.environment.PSObject.Properties[[string]$entry.Key]
        if ($null -eq $nameProperty) {
            throw "Knowledge index embedding environment field is missing: $($entry.Key)"
        }
        $environment[[string]$nameProperty.Value] = [string]$entry.Value
    }

    $workerPython = [string]$EmbeddingData.layout.worker_python
    if (
        Test-HiaEmbeddingProjectPath `
            -ProjectRoot $root `
            -Path $workerPython `
            -Kind file
    ) {
        $environment[[string]$contract.environment.python] = $workerPython
    }
    foreach ($profileProperty in @($EmbeddingData.profiles.PSObject.Properties)) {
        $profile = $profileProperty.Value
        if (-not (
            Test-HiaEmbeddingModelInstall `
                -ProjectRoot $root `
                -EmbeddingData $EmbeddingData `
                -ProfileContract $profile
        )) {
            continue
        }
        $modelDirectory = Get-HiaEmbeddingModelDirectory `
            -ProjectRoot $root `
            -EmbeddingData $EmbeddingData `
            -ProfileContract $profile
        $manifest = [System.IO.File]::ReadAllText(
            (Join-Path $modelDirectory '.hia-embedding-model.json')
        ) | ConvertFrom-Json
        $environment[[string]$profile.model_dir_environment] = $modelDirectory
        $environment[[string]$profile.model_revision_environment] = [string]$manifest.revision
    }

    $arguments = @($pythonArguments) + @('--project-root', $root, $Action)
    if ($Action -eq 'build') {
        $batchSize = [int]$knowledge.default_batch_size
        if ($batchSize -lt 1 -or $batchSize -gt [int]$knowledge.max_batch_size) {
            throw 'Knowledge index default batch size is invalid.'
        }
        $arguments += @('--batch-size', [string]$batchSize)
    }
    return [pscustomobject]@{
        action = $Action
        file_path = $python
        arguments = @($arguments)
        working_directory = $root
        environment = $environment
        clear_environment_names = @($environmentNames | Sort-Object)
        protocol = [string]$knowledge.protocol
    }
}

function New-HiaKnowledgeCliProcessPlan {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [AllowEmptyString()][string]$BridgePython = '',
        [Parameter(Mandatory = $true)]$EmbeddingData,
        [AllowEmptyString()][string]$EmbeddingProfile = '',
        [AllowEmptyString()][string]$EmbeddingDevice = '',
        [ValidateSet('status', 'build')][string]$Action,
        [AllowNull()]$ManagedProbePayloadOverride = $null
    )

    $managedPython = Resolve-HiaManagedBridgePython `
        -ProjectRoot $ProjectRoot `
        -ProbePayloadOverride $ManagedProbePayloadOverride
    $indexPlan = New-HiaKnowledgeIndexProcessPlan `
        -ProjectRoot $ProjectRoot `
        -BridgePython $managedPython `
        -EmbeddingData $EmbeddingData `
        -EmbeddingProfile $EmbeddingProfile `
        -EmbeddingDevice $EmbeddingDevice `
        -Action $Action
    $root = [string]$indexPlan.working_directory
    $cliPath = Join-Path $root 'scripts\hia-knowledge.ps1'
    if (-not (
        Test-HiaEmbeddingProjectPath `
            -ProjectRoot $root `
            -Path $cliPath `
            -Kind file
    )) {
        throw 'The project-local knowledge CLI is unavailable or unsafe.'
    }
    $powershellExe = Join-Path $env:SystemRoot (
        'System32\WindowsPowerShell\v1.0\powershell.exe'
    )
    if (-not (Test-Path -LiteralPath $powershellExe -PathType Leaf)) {
        throw 'Windows PowerShell is unavailable for the project-local knowledge CLI.'
    }
    [void](Resolve-HiaEmbeddingProfile `
        -EmbeddingData $EmbeddingData `
        -Profile $EmbeddingProfile)
    [void](Resolve-HiaEmbeddingDevice -Device $EmbeddingDevice)
    $cliArguments = @(
        '-NoProfile',
        '-NonInteractive',
        '-ExecutionPolicy', 'Bypass',
        '-File', $cliPath,
        "index-$Action"
    )
    if ($Action -eq 'build') {
        $batchSize = [int]$EmbeddingData.contract.knowledge_index.default_batch_size
        if ($batchSize -lt 1 -or $batchSize -gt 64) {
            throw 'Knowledge index default batch size is invalid.'
        }
        $cliArguments += @('-BatchSize', [string]$batchSize)
    }
    return [pscustomobject]@{
        action = $Action
        file_path = $powershellExe
        arguments = @($cliArguments)
        working_directory = $root
        environment = $indexPlan.environment
        clear_environment_names = @($indexPlan.clear_environment_names)
        protocol = [string]$indexPlan.protocol
    }
}

function New-HiaAssetCliProcessPlan {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)]
        [ValidateSet(
            'capabilities',
            'import',
            'list',
            'status',
            'resume',
            'delete',
            'repair'
        )]
        [string]$Action,
        [AllowEmptyString()][string]$Path = '',
        [AllowEmptyString()][string]$AssetId = '',
        [ValidateRange(0, 2147483647)][int]$Offset = 0,
        [ValidateRange(1, 500)][int]$Limit = 100
    )

    $root = Get-HiaProjectRoot -StartingPath $ProjectRoot
    $cliPath = Join-Path $root 'scripts\hia-knowledge.ps1'
    if (-not (
        Test-HiaEmbeddingProjectPath `
            -ProjectRoot $root `
            -Path $cliPath `
            -Kind file
    )) {
        throw 'The project-local knowledge CLI is unavailable or unsafe.'
    }
    $powershellExe = Join-Path $env:SystemRoot (
        'System32\WindowsPowerShell\v1.0\powershell.exe'
    )
    if (-not (Test-Path -LiteralPath $powershellExe -PathType Leaf)) {
        throw 'Windows PowerShell is unavailable for the project-local asset CLI.'
    }
    $arguments = @(
        '-NoProfile',
        '-NonInteractive',
        '-ExecutionPolicy', 'Bypass',
        '-File', $cliPath,
        'assets', $Action
    )
    switch ($Action) {
        'import' {
            if ([string]::IsNullOrWhiteSpace($Path)) {
                throw 'Asset import requires one file path.'
            }
            $arguments += @('-Path', $Path)
        }
        'list' {
            $arguments += @(
                '-Offset', [string]$Offset,
                '-Limit', [string]$Limit
            )
        }
        { $_ -in @('status', 'resume', 'delete') } {
            if ([string]::IsNullOrWhiteSpace($AssetId)) {
                throw "Asset $Action requires an asset id."
            }
            $arguments += @('-AssetId', $AssetId)
        }
    }
    return [pscustomobject]@{
        action = "assets.$Action"
        file_path = $powershellExe
        arguments = @($arguments)
        working_directory = $root
        environment = @{}
        clear_environment_names = @()
        protocol = if ($Action -eq 'repair') {
            'hia-asset-repair-jsonl/1'
        } else {
            'hia-knowledge-index-jsonl/1'
        }
    }
}

function ConvertTo-HiaAssetState {
    param([Parameter(Mandatory = $true)]$Asset)

    $required = @(
        'id',
        'title',
        'kind',
        'stage',
        'status',
        'processed_units',
        'total_units',
        'fragments',
        'lexical',
        'vector',
        'error',
        'recoverable'
    )
    $completeContract = $true
    foreach ($field in $required) {
        if ($null -eq $Asset.PSObject.Properties[$field]) {
            $completeContract = $false
            break
        }
    }
    if ($completeContract) { return $Asset }

    # Compatibility with the first core asset implementation.  The public
    # contract is normalized here for the WPF shell while the PowerShell CLI
    # continues to forward the core JSONL byte-for-byte.
    foreach ($field in @(
        'asset_id',
        'title',
        'status',
        'fragment_count',
        'next_fragment',
        'documents_indexed',
        'chunks_indexed',
        'vectors_indexed'
    )) {
        if ($null -eq $Asset.PSObject.Properties[$field]) {
            throw "Asset JSONL field is missing: $field"
        }
    }
    $fragments = [long]$Asset.fragment_count
    $processed = [long]$Asset.next_fragment
    $documents = [long]$Asset.documents_indexed
    $chunks = [long]$Asset.chunks_indexed
    $vectors = [long]$Asset.vectors_indexed
    $status = [string]$Asset.status
    $normalizedStatus = switch ($status.ToLowerInvariant()) {
        'processing' { 'running' }
        default { $status }
    }
    $suffix = [string]$Asset.source_suffix
    $kind = $suffix.TrimStart('.')
    if ([string]::IsNullOrWhiteSpace($kind)) {
        $kind = [string]$Asset.extractor
    }
    $stage = [string]$Asset.checkpoint_state
    if ([string]::IsNullOrWhiteSpace($stage)) { $stage = $normalizedStatus }
    return [pscustomobject]@{
        id = [string]$Asset.asset_id
        title = [string]$Asset.title
        kind = $kind
        stage = $stage
        status = $normalizedStatus
        processed_units = $processed
        total_units = [Math]::Max($processed, $fragments)
        fragments = $fragments
        lexical = [pscustomobject]@{
            status = if ($documents -ge $fragments -and $fragments -gt 0) {
                'ready'
            } elseif ($documents -gt 0) {
                'partial'
            } else {
                'pending'
            }
            processed = $documents
            total = $fragments
            complete = ($documents -ge $fragments -and $fragments -gt 0)
        }
        vector = [pscustomobject]@{
            status = if ($vectors -ge $chunks -and $chunks -gt 0) {
                'ready'
            } elseif ($vectors -gt 0) {
                'partial'
            } else {
                'pending'
            }
            processed = $vectors
            total = $chunks
            complete = ($vectors -ge $chunks -and $chunks -gt 0)
        }
        error = ''
        recoverable = ($normalizedStatus -notin @(
            'ready',
            'completed',
            'succeeded'
        ))
    }
}

function Assert-HiaAssetState {
    param([Parameter(Mandatory = $true)]$Asset)

    foreach ($field in @(
        'id',
        'title',
        'kind',
        'stage',
        'status',
        'processed_units',
        'total_units',
        'fragments',
        'lexical',
        'vector',
        'error',
        'recoverable'
    )) {
        if ($null -eq $Asset.PSObject.Properties[$field]) {
            throw "Asset JSONL field is missing: $field"
        }
    }
    foreach ($field in @('id', 'title', 'kind', 'stage', 'status')) {
        if ([string]::IsNullOrWhiteSpace([string]$Asset.$field)) {
            throw "Asset JSONL field is invalid: $field"
        }
    }
    foreach ($field in @(
        'processed_units',
        'total_units',
        'fragments'
    )) {
        if ([long]$Asset.$field -lt 0) {
            throw "Asset JSONL field is invalid: $field"
        }
    }
    if ([long]$Asset.processed_units -gt [long]$Asset.total_units) {
        throw 'Asset JSONL progress exceeds its total units.'
    }
    return $Asset
}

function ConvertFrom-HiaAssetJsonLine {
    param([Parameter(Mandatory = $true)][string]$Line)

    try {
        $payload = $Line | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw 'Asset CLI emitted invalid JSONL.'
    }
    if (
        $null -ne $payload -and
        [string]$payload.event -eq 'started'
    ) {
        $payload.event = 'start'
    }
    $protocol = if ($null -eq $payload) {
        ''
    } else {
        [string]$payload.protocol
    }
    if (
        $null -eq $payload -or
        (
            $protocol -notmatch (
                '^hia-knowledge-(?:index-)?(?:cli|jsonl)/\d+$'
            ) -and
            $protocol -ne 'hia-asset-repair-jsonl/1'
        ) -or
        [string]$payload.event -notin @(
            'start',
            'progress',
            'completed',
            'error'
        ) -or
        [string]$payload.action -notin @(
            'assets.capabilities',
            'assets.import',
            'assets.list',
            'assets.status',
            'assets.resume',
            'assets.delete',
            'assets.repair'
        )
    ) {
        throw 'Asset CLI JSONL protocol is invalid.'
    }
    $assetProperty = $payload.PSObject.Properties['asset']
    if ($null -ne $assetProperty -and $null -ne $assetProperty.Value) {
        $payload.asset = ConvertTo-HiaAssetState -Asset $assetProperty.Value
        [void](Assert-HiaAssetState -Asset $payload.asset)
    }
    $resultProperty = $payload.PSObject.Properties['result']
    if ($null -ne $resultProperty -and $null -ne $resultProperty.Value) {
        $itemsProperty = $resultProperty.Value.PSObject.Properties['items']
        if ($null -ne $itemsProperty) {
            $normalizedItems = @(
                foreach ($asset in @($itemsProperty.Value)) {
                    $normalized = ConvertTo-HiaAssetState -Asset $asset
                    [void](Assert-HiaAssetState -Asset $normalized)
                    $normalized
                }
            )
            $resultProperty.Value.items = @($normalizedItems)
        } elseif (
            [string]$payload.action -in @(
                'assets.import',
                'assets.resume',
                'assets.status'
            )
        ) {
            $normalized = ConvertTo-HiaAssetState -Asset $resultProperty.Value
            [void](Assert-HiaAssetState -Asset $normalized)
            $payload.result = $normalized
            $resultProperty = $payload.PSObject.Properties['result']
        }
        if ($null -ne $resultProperty -and $null -ne $resultProperty.Value) {
            $resultAssetProperty = (
                $resultProperty.Value.PSObject.Properties['asset']
            )
            if (
                $null -ne $resultAssetProperty -and
                $null -ne $resultAssetProperty.Value
            ) {
                $resultProperty.Value.asset = ConvertTo-HiaAssetState `
                    -Asset $resultAssetProperty.Value
                [void](Assert-HiaAssetState `
                    -Asset $resultProperty.Value.asset)
            }
        }
    }
    return $payload
}

function ConvertFrom-HiaKnowledgeIndexJsonLine {
    param(
        [Parameter(Mandatory = $true)][string]$Line,
        [Parameter(Mandatory = $true)]$EmbeddingData
    )

    try {
        $payload = $Line | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw 'Knowledge index emitted invalid JSONL.'
    }
    $knowledge = $EmbeddingData.contract.knowledge_index
    if (
        $null -eq $payload -or
        -not [System.StringComparer]::Ordinal.Equals(
            [string]$payload.protocol,
            [string]$knowledge.protocol
        ) -or
        [string]$payload.event -notin @($knowledge.events)
    ) {
        throw 'Knowledge index JSONL protocol is invalid.'
    }
    $indexProperty = $payload.PSObject.Properties['index']
    if ($null -eq $indexProperty -or $null -eq $indexProperty.Value) {
        throw 'Knowledge index JSONL is missing index state.'
    }
    if (
        [string]$payload.event -eq 'error' -and
        @($indexProperty.Value.PSObject.Properties).Count -eq 0
    ) {
        return $payload
    }
    if (
        $null -eq $indexProperty.Value.PSObject.Properties['profile_id'] -and
        $null -ne $indexProperty.Value.PSObject.Properties['active_profile'] -and
        -not [string]::IsNullOrWhiteSpace(
            [string]$indexProperty.Value.active_profile
        )
    ) {
        $indexProperty.Value |
            Add-Member -NotePropertyName 'profile_id' `
                -NotePropertyValue ([string]$indexProperty.Value.active_profile)
    }
    foreach ($field in @(
        'profile_id',
        'model_id',
        'dim',
        'total_chunks',
        'vector_chunks',
        'pending_chunks',
        'complete',
        'last_batch_count',
        'chunks_indexed_this_call'
    )) {
        if ($null -eq $indexProperty.Value.PSObject.Properties[$field]) {
            throw "Knowledge index JSONL field is missing: $field"
        }
    }
    foreach ($field in @(
        'dim',
        'total_chunks',
        'vector_chunks',
        'pending_chunks',
        'last_batch_count',
        'chunks_indexed_this_call'
    )) {
        if ([long]$indexProperty.Value.$field -lt 0) {
            throw "Knowledge index JSONL field is invalid: $field"
        }
    }
    return $payload
}

function Get-HiaEmbeddingRuntimeState {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)]$EmbeddingData,
        [int]$TimeoutSeconds = 12,
        [AllowNull()]$ProbeOverride = $null
    )

    if ($null -ne $ProbeOverride) { return $ProbeOverride }

    $installedProfiles = [System.Collections.Generic.List[string]]::new()
    foreach ($property in $EmbeddingData.profiles.PSObject.Properties) {
        if (
            Test-HiaEmbeddingModelInstall `
                -ProjectRoot $ProjectRoot `
                -EmbeddingData $EmbeddingData `
                -ProfileContract $property.Value
        ) {
            $installedProfiles.Add([string]$property.Value.profile_id)
        }
    }

    $workerPython = [string]$EmbeddingData.layout.worker_python
    $workerReady = Test-HiaEmbeddingProjectPath `
        -ProjectRoot $ProjectRoot `
        -Path $workerPython `
        -Kind file
    $cachePathsSafe = $true
    foreach ($cachePath in @(
        [string]$EmbeddingData.layout.cache_root,
        [string]$EmbeddingData.layout.huggingface_cache,
        [string]$EmbeddingData.layout.transformers_cache,
        [string]$EmbeddingData.layout.torch_cache,
        [string]$EmbeddingData.layout.temp_root
    )) {
        if (-not (
            Test-HiaEmbeddingProjectPath `
                -ProjectRoot $ProjectRoot `
                -Path $cachePath `
                -Kind directory `
                -AllowMissingLeaf
        )) {
            $cachePathsSafe = $false
            break
        }
    }
    $probePassed = $false
    $probeMessage = if (-not $workerReady) {
        '独立 embedding venv 尚未安装、不完整或路径不安全。'
    } elseif (-not $cachePathsSafe) {
        'embedding 缓存路径不在普通项目目录内，已拒绝运行探针。'
    } else {
        '尚未探针。'
    }
    if ($workerReady -and $cachePathsSafe -and $installedProfiles.Count -gt 0) {
        $workerModule = [string]$EmbeddingData.contract.worker.module
        $probeCode = @"
import importlib
import json
import sys
for name in (sys.argv[1], "sentence_transformers", "transformers", "torch"):
    importlib.import_module(name)
import torch
cuda_available = bool(torch.cuda.is_available())
print("$script:HiaProbeMarker" + json.dumps({
    "imports": "ok",
    "torch_version": str(torch.__version__),
    "torch_cuda_build": str(torch.version.cuda or ""),
    "cuda_available": cuda_available,
    "device_name": str(torch.cuda.get_device_name(0)) if cuda_available else "",
}, sort_keys=True))
"@
        $cacheRoot = [string]$EmbeddingData.layout.cache_root
        $probe = Invoke-HiaProcess -FilePath $workerPython -Arguments @('-I', '-B', '-c', $probeCode, $workerModule) `
            -TimeoutSeconds ([Math]::Max(30, $TimeoutSeconds)) -WorkingDirectory $ProjectRoot -Environment @{
                'PYTHONDONTWRITEBYTECODE' = '1'
                'PYTHONNOUSERSITE' = '1'
                'HF_HOME' = [string]$EmbeddingData.layout.huggingface_cache
                'HUGGINGFACE_HUB_CACHE' = [string]$EmbeddingData.layout.huggingface_cache
                'TRANSFORMERS_CACHE' = [string]$EmbeddingData.layout.transformers_cache
                'TORCH_HOME' = [string]$EmbeddingData.layout.torch_cache
                'XDG_CACHE_HOME' = $cacheRoot
                'HOME' = $cacheRoot
                'USERPROFILE' = $cacheRoot
                'APPDATA' = $cacheRoot
                'LOCALAPPDATA' = $cacheRoot
                'TEMP' = [string]$EmbeddingData.layout.temp_root
                'TMP' = [string]$EmbeddingData.layout.temp_root
            }
        $payload = Get-HiaProbePayload -Output ("$($probe.stdout)`n$($probe.stderr)")
        $probePassed = (
            -not [bool]$probe.timed_out -and
            $probe.exit_code -eq 0 -and
            $null -ne $payload
        )
        $probeMessage = if ($probePassed) {
            if ([bool]$payload.cuda_available) {
                "独立 worker 可用；CUDA 已就绪：$([string]$payload.device_name)。"
            } else {
                '独立 worker 可用，但当前 PyTorch 仅能使用 CPU。'
            }
        } elseif ([bool]$probe.timed_out) {
            '独立 embedding venv import 探针超时。'
        } else {
            '独立 embedding venv 的 worker 或必要 import 不可用。'
        }
    }
    return [pscustomobject]@{
        worker_ready = $workerReady
        installed_profiles = @($installedProfiles)
        probe_passed = $probePassed
        probe_message = $probeMessage
        torch_version = if ($probePassed) { [string]$payload.torch_version } else { '' }
        torch_cuda_build = if ($probePassed) { [string]$payload.torch_cuda_build } else { '' }
        cuda_available = if ($probePassed) { [bool]$payload.cuda_available } else { $false }
        device_name = if ($probePassed) { [string]$payload.device_name } else { '' }
    }
}

function Get-HiaEmbeddingCheckResult {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [AllowNull()]$EmbeddingData,
        [AllowEmptyString()][string]$EmbeddingProfile = '',
        [AllowEmptyString()][string]$EmbeddingDevice = '',
        [int]$TimeoutSeconds = 12,
        [AllowNull()]$ProbeOverride = $null
    )

    if ($null -eq $EmbeddingData) {
        return New-HiaCheckResult -Id 'embedding.runtime' -Name 'Local knowledge embedding' `
            -Level 'red' `
            -Message '无法读取项目 embedding contract；启动器无法安全构造或清除项目定义的子进程环境。' `
            -Advice '先修复 Bridge Python 或从完整项目副本恢复 src\hia_core\embedding_contract.py。模型文件缺失仍只会降级 FTS5，不属于此阻断。'
    }

    try {
        $selected = Resolve-HiaEmbeddingProfile -EmbeddingData $EmbeddingData -Profile $EmbeddingProfile
    } catch {
        $selected = [string]$EmbeddingData.contract.default_profile
    }
    $profile = Get-HiaEmbeddingProfileContract -EmbeddingData $EmbeddingData -Profile $selected
    $selectedDevice = Resolve-HiaEmbeddingDevice -Device $EmbeddingDevice
    $fallbackProfile = [string]$EmbeddingData.contract.fallback_profile
    $size = ([double]$profile.repository_size_gb).ToString(
        '0.##',
        [System.Globalization.CultureInfo]::InvariantCulture
    )
    $spaceNote = "官方模型文件约 $size GB；安装还需为独立 venv 与项目本地缓存预留空间。"
    $state = Get-HiaEmbeddingRuntimeState `
        -ProjectRoot $ProjectRoot `
        -EmbeddingData $EmbeddingData `
        -TimeoutSeconds $TimeoutSeconds `
        -ProbeOverride $ProbeOverride
    $cudaProperty = $state.PSObject.Properties['cuda_available']
    $cudaReady = ($null -ne $cudaProperty -and [bool]$cudaProperty.Value)
    $installed = @($state.installed_profiles)
    $selectedInstalled = $selected -in $installed
    $fallbackInstalled = (
        $selected -ne $fallbackProfile -and
        $fallbackProfile -in $installed
    )

    if (
        $selectedDevice -eq 'cuda' -and
        $selectedInstalled -and
        [bool]$state.worker_ready -and
        [bool]$state.probe_passed -and
        -not $cudaReady
    ) {
        return New-HiaCheckResult -Id 'embedding.runtime' -Name 'Local knowledge embedding' `
            -Level 'yellow' `
            -Message "$($profile.label) 已安装，但已选择 NVIDIA GPU（CUDA），项目本地 PyTorch 的 torch.cuda.is_available()=false；向量检索将降级到 FTS5。$spaceNote" `
            -Advice '点击“安装/修复知识向量模型”准备项目本地 CUDA PyTorch；Houdini 仍可启动。'
    }

    if ($selectedInstalled -and [bool]$state.worker_ready -and [bool]$state.probe_passed) {
        $deviceAdvice = switch ($selectedDevice) {
            'cuda' { '已按选择使用 NVIDIA GPU（CUDA）；无需处理。' }
            'cpu' { '已按选择使用 CPU；无需处理。' }
            default {
                if ($cudaReady) {
                    '自动选择可使用 NVIDIA GPU（CUDA）；无需处理。'
                } else {
                    '自动选择当前使用 CPU。若电脑有 NVIDIA 显卡，可点击“安装/修复知识向量模型”安装官方 CUDA PyTorch；不会修改全局 Python。'
                }
            }
        }
        return New-HiaCheckResult -Id 'embedding.runtime' -Name 'Local knowledge embedding' `
            -Level 'green' `
            -Message "$($profile.label) 已安装，$($state.probe_message) $spaceNote" `
            -Advice "$deviceAdvice 真实模型加载若失败会给出降级原因并保留 FTS5。"
    }

    if (
        -not $selectedInstalled -and
        $fallbackInstalled -and
        [bool]$state.worker_ready -and
        [bool]$state.probe_passed
    ) {
        $fallback = Get-HiaEmbeddingProfileContract -EmbeddingData $EmbeddingData -Profile $fallbackProfile
        return New-HiaCheckResult -Id 'embedding.runtime' -Name 'Local knowledge embedding' `
            -Level 'yellow' `
            -Message "$($profile.label) 未完整安装；可降级到已安装的 $($fallback.label)，再失败则使用 FTS5。$spaceNote" `
            -Advice '点击“安装/修复知识向量模型”准备当前选择；Houdini 仍可启动。'
    }

    if (-not $selectedInstalled) {
        $fallbackFailure = if ($fallbackInstalled) {
            "轻量模型文件已安装，但 $($state.probe_message)"
        } else {
            '没有可用的已安装向量模型。'
        }
        return New-HiaCheckResult -Id 'embedding.runtime' -Name 'Local knowledge embedding' `
            -Level 'yellow' `
            -Message "$($profile.label) 未安装或安装不完整；$fallbackFailure 知识检索将使用 FTS5。$spaceNote" `
            -Advice '点击“安装/修复知识向量模型”准备当前选择；Houdini 仍可启动。'
    }

    $fallbackText = if ($fallbackInstalled) {
        '真实加载失败时将先尝试已安装的轻量模型，再降级 FTS5。'
    } else {
        '真实加载失败时将降级 FTS5。'
    }
    return New-HiaCheckResult -Id 'embedding.runtime' -Name 'Local knowledge embedding' `
        -Level 'yellow' `
        -Message "$($profile.label) 模型文件已安装，但 $($state.probe_message) $fallbackText $spaceNote" `
        -Advice '点击“安装/修复知识向量模型”修复项目本地独立 venv；Houdini 仍可启动。'
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
        [AllowEmptyString()][string]$SavedPath = '',
        [AllowNull()]$ManagedProbePayloadOverride = $null
    )

    $paths = @{}
    if ([string]::IsNullOrWhiteSpace($SavedPath)) {
        try {
            $stored = Read-HiaLauncherSettings -ProjectRoot $ProjectRoot
            $advancedProperty = $stored.PSObject.Properties['bridge_python_advanced']
            if ($null -ne $advancedProperty) {
                $SavedPath = [string]$advancedProperty.Value
            }
        } catch { }
    }
    foreach ($record in @(
        [pscustomobject]@{ path = $ExplicitPath; source = 'explicit' },
        [pscustomobject]@{ path = $SavedPath; source = 'settings' },
        [pscustomobject]@{ path = $env:HIA_BRIDGE_PYTHON; source = 'HIA_BRIDGE_PYTHON' }
    )) {
        if (-not $record.path) { continue }
        try { $full = [System.IO.Path]::GetFullPath([string]$record.path) } catch { continue }
        if (Test-Path -LiteralPath $full -PathType Leaf) {
            $paths[$full] = [pscustomobject]@{
                path = $full
                source = [string]$record.source
                exists = $true
                healthy = $false
                automatic = $false
                advanced = $true
                display = "$full  [高级：$($record.source)]"
            }
        }
    }
    foreach ($command in @(Get-Command -Name 'python.exe' -All -ErrorAction SilentlyContinue)) {
        if ($command.Source -and (Test-Path -LiteralPath $command.Source -PathType Leaf)) {
            $full = [System.IO.Path]::GetFullPath([string]$command.Source)
            if (-not $paths.ContainsKey($full)) {
                $paths[$full] = [pscustomobject]@{
                    path = $full
                    source = 'PATH'
                    exists = $true
                    healthy = $false
                    automatic = $false
                    advanced = $true
                    display = "$full  [高级：PATH]"
                }
            }
        }
    }

    $managedState = Get-HiaManagedBridgePythonState `
        -ProjectRoot $ProjectRoot `
        -ProbePayloadOverride $ManagedProbePayloadOverride
    if (Test-Path -LiteralPath $managedState.path -PathType Leaf) {
        $managedLabel = if ([bool]$managedState.healthy) {
            '项目本地 managed（已验证）'
        } else {
            '项目本地 managed（需要修复）'
        }
        $paths[[string]$managedState.path] = [pscustomobject]@{
            path = [string]$managedState.path
            source = 'project managed'
            exists = $true
            healthy = [bool]$managedState.healthy
            automatic = [bool]$managedState.healthy
            advanced = $false
            display = "$($managedState.path)  [$managedLabel]"
            reason = [string]$managedState.reason
        }
    }
    return @(
        $paths.Values |
            Sort-Object `
                -Property @{ Expression = { [bool]$_.automatic }; Descending = $true }, path
    )
}

function ConvertTo-HiaProcessArgument {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Value)

    if ($Value.Length -eq 0) { return '""' }
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
        [ValidateRange(0, 60)][int]$GraceTimeoutSeconds = 0,
        [hashtable]$Environment = @{},
        [string[]]$RemoveEnvironmentVariables = @(),
        [AllowEmptyString()][string]$WorkingDirectory = ''
    )

    $result = [ordered]@{
        started = $false
        timed_out = $false
        completed_after_grace = $false
        elapsed_ms = 0
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
        foreach ($name in $RemoveEnvironmentVariables) {
            if (-not $name) { continue }
            if ($null -ne $startInfo.Environment) {
                [void]$startInfo.Environment.Remove([string]$name)
            } else {
                [void]$startInfo.EnvironmentVariables.Remove([string]$name)
            }
        }
        $process = [System.Diagnostics.Process]::new()
        $process.StartInfo = $startInfo
        $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
        if (-not $process.Start()) { throw 'process start returned false' }
        $result.started = $true
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $completed = $process.WaitForExit([Math]::Max(1, $TimeoutSeconds) * 1000)
        if (-not $completed -and $GraceTimeoutSeconds -gt 0) {
            $completed = $process.WaitForExit($GraceTimeoutSeconds * 1000)
            if ($completed) { $result.completed_after_grace = $true }
        }
        $stopwatch.Stop()
        $result.elapsed_ms = [long]$stopwatch.ElapsedMilliseconds
        if (-not $completed) {
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
        [switch]$HythonTimedOut,
        [switch]$HythonCompletedAfterGrace,
        [long]$HythonElapsedMilliseconds = 0
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
    $hythonLevel = 'red'
    $hythonMessage = 'hython 无法 import hou 或未返回有效探针数据。'
    $hythonAdvice = '在命令行运行所选 hython.exe 的 import hou 探针；若仍失败，再检查该 Houdini 安装。'
    if ($hythonPassed) {
        if ($HythonCompletedAfterGrace) {
            $elapsedSeconds = [string]::Format(
                [System.Globalization.CultureInfo]::InvariantCulture,
                '{0:0.0}',
                ($HythonElapsedMilliseconds / 1000.0)
            )
            $hythonLevel = 'yellow'
            $hythonMessage = "hython 冷启动较慢（$elapsedSeconds 秒），但 import hou 已验证；Houdini build $($payload.build)；Python $($payload.python)"
            $hythonAdvice = '所选 Houdini 与 import hou 已验证，无需修复；等待知识索引、渲染等重负载结束后重新扫描，可确认冷启动速度。'
        } else {
            $hythonLevel = 'green'
            $hythonMessage = "import hou 成功；Houdini build $($payload.build)；Python $($payload.python)"
            $hythonAdvice = '无需处理。'
        }
    } elseif ($licenseUnavailable) {
        $hythonMessage = 'hython 无法取得 Houdini 许可证。'
        $hythonAdvice = '检查 Houdini License Administrator 与许可证服务器；关闭可能占用许可证 seat 的实例后重新扫描。'
    } elseif ($HythonTimedOut) {
        $elapsedSeconds = [string]::Format(
            [System.Globalization.CultureInfo]::InvariantCulture,
            '{0:0.0}',
            ($HythonElapsedMilliseconds / 1000.0)
        )
        if ($houdiniProbePassed) {
            $hythonMessage = "hython 在 $elapsedSeconds 秒的有界只读探针窗口内仍未完成；Houdini build $houdiniBuild 可读取，但本次尚未确认 import hou。"
        } else {
            $hythonMessage = "hython 在 $elapsedSeconds 秒的有界只读探针窗口内仍未完成，本次尚未确认 import hou。"
        }
        $hythonAdvice = '先等待知识索引、渲染等重负载结束后重新扫描；若连续超时，再在命令行运行所选 hython.exe 的 import hou 探针。'
    } elseif ($HythonExitCode -ne 0) {
        $hythonMessage = "hython 只读探针退出码为 $HythonExitCode，未能确认 import hou。"
    }
    $checks += New-HiaCheckResult `
        -Id 'houdini.hython_probe' -Name 'Hython / hou probe' `
        -Level $hythonLevel `
        -Message $hythonMessage `
        -Advice $hythonAdvice

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
        $probeCode = "import json,sys,hou;print('$script:HiaProbeMarker'+json.dumps({'build':hou.applicationVersionString(),'python':str(sys.version_info[0])+'.'+str(sys.version_info[1]),'executable':sys.executable,'hou_import':True},sort_keys=True),flush=True)"
        $hythonGraceSeconds = [Math]::Max(
            0,
            [Math]::Min($TimeoutSeconds, 30 - $TimeoutSeconds)
        )
        $hythonProbe = Invoke-HiaProcess `
            -FilePath $hythonExe `
            -Arguments @('-B', '-c', $probeCode) `
            -TimeoutSeconds $TimeoutSeconds `
            -GraceTimeoutSeconds $hythonGraceSeconds `
            -RemoveEnvironmentVariables @('PYTHONPATH')
    }
    $hythonCompletedAfterGrace = $false
    $hythonElapsedMilliseconds = 0
    if ($null -ne $hythonProbe.PSObject.Properties['completed_after_grace']) {
        $hythonCompletedAfterGrace = [bool]$hythonProbe.completed_after_grace
    }
    if ($null -ne $hythonProbe.PSObject.Properties['elapsed_ms']) {
        $hythonElapsedMilliseconds = [long]$hythonProbe.elapsed_ms
    }
    return @(Test-HiaHoudiniProbeConsistency `
        -HoudiniExe $HoudiniExe `
        -HoudiniOutput ("$($houdiniProbe.stdout)`n$($houdiniProbe.stderr)") `
        -HoudiniExitCode $(if ($null -eq $houdiniProbe.exit_code) { -1 } else { [int]$houdiniProbe.exit_code }) `
        -HythonOutput ("$($hythonProbe.stdout)`n$($hythonProbe.stderr)") `
        -HythonExitCode $(if ($null -eq $hythonProbe.exit_code) { -1 } else { [int]$hythonProbe.exit_code }) `
        -HoudiniTimedOut:([bool]$houdiniProbe.timed_out) `
        -HythonTimedOut:([bool]$hythonProbe.timed_out) `
        -HythonCompletedAfterGrace:$hythonCompletedAfterGrace `
        -HythonElapsedMilliseconds $hythonElapsedMilliseconds)
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

    $managedCacheRoots = @(
        'screenshots',
        'previews',
        'tmp',
        'embedding',
        'dotnet'
    ) | ForEach-Object {
        [System.IO.Path]::GetFullPath(
            (Join-Path $root ".runtime\cache\$_")
        ).TrimEnd('\')
    }
    foreach ($managedCacheRoot in $managedCacheRoots) {
        if (
            Test-HiaPathWithinDirectory `
                -Path $resolved `
                -Directory $managedCacheRoot
        ) {
            throw (
                '最终输出目录不能等于或位于启动器可清理的托管缓存分类中；' +
                '请选择其他普通本地目录，或留空使用项目 .runtime\cache 根目录。'
            )
        }
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
        [AllowNull()]$EmbeddingData = $null,
        [AllowEmptyString()][string]$EmbeddingProfile = '',
        [AllowEmptyString()][string]$EmbeddingDevice = '',
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
            -Advice '运行 scripts\hia-knowledge.ps1 environment-install。HIA Python 环境位于项目根目录 .venv；受管 CPython、uv、模型与缓存位于 .runtime。不要用全局 pip 或 PATH Python 修复。'
    } elseif (-not $bridgePathAllowed) {
        $checks += New-HiaCheckResult -Id 'bridge.python' -Name 'Bridge Python' -Level 'red' `
            -Message 'Bridge Python 必须是普通本地盘绝对路径；WindowsApps、普通 AppData 路径、UNC、相对路径和 ADS 均不接受。' `
            -Advice '优先运行 scripts\hia-knowledge.ps1 environment-repair 并选择项目受管 Python；外部 Python 仅作为显式高级覆盖。'
    } elseif (-not (Test-Path -LiteralPath $BridgePython -PathType Leaf)) {
        $checks += New-HiaCheckResult -Id 'bridge.python' -Name 'Bridge Python' -Level 'red' `
            -Message '尚未选择有效的 Bridge Python executable。' `
            -Advice '运行 scripts\hia-knowledge.ps1 environment-repair。HIA Python 环境位于项目根目录 .venv；受管 CPython、uv、模型与缓存位于 .runtime。'
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
            -Advice $(if ($bridgeLevel -eq 'green') { '无需处理。' } else { '运行 scripts\hia-knowledge.ps1 environment-repair，并重新检查项目受管 Python、共享 venv 与项目 import。' })
    }

    $embeddingOverride = if ($ProbeOverrides.ContainsKey('embedding')) {
        $ProbeOverrides.embedding
    } else {
        $null
    }
    $checks += Get-HiaEmbeddingCheckResult `
        -ProjectRoot $ProjectRoot `
        -EmbeddingData $EmbeddingData `
        -EmbeddingProfile $EmbeddingProfile `
        -EmbeddingDevice $EmbeddingDevice `
        -TimeoutSeconds $TimeoutSeconds `
        -ProbeOverride $embeddingOverride

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
        [AllowNull()]$EmbeddingData = $null,
        [AllowEmptyString()][string]$EmbeddingProfile = '',
        [AllowEmptyString()][string]$EmbeddingDevice = '',
        [object[]]$Candidates = @(),
        [int]$TimeoutSeconds = 12,
        [hashtable]$ProbeOverrides = @{}
    )

    $root = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
    if ($Candidates.Count -eq 0) {
        $Candidates = @(Get-HiaHoudiniCandidates -ExplicitPath $HoudiniExe)
    }
    $embeddingContractPythonAvailable = $false
    if ($null -eq $EmbeddingData -and -not [string]::IsNullOrWhiteSpace($BridgePython)) {
        try {
            $embeddingContractPythonAvailable = Test-Path `
                -LiteralPath $BridgePython `
                -PathType Leaf `
                -ErrorAction Stop
        } catch {
            $embeddingContractPythonAvailable = $false
        }
    }
    if ($null -eq $EmbeddingData -and $embeddingContractPythonAvailable) {
        try {
            $EmbeddingData = Get-HiaEmbeddingContractData `
                -ProjectRoot $root `
                -PythonExe $BridgePython `
                -TimeoutSeconds $TimeoutSeconds
        } catch {
            $EmbeddingData = $null
        }
    }
    if ($null -ne $EmbeddingData) {
        try {
            $EmbeddingProfile = Resolve-HiaEmbeddingProfile `
                -EmbeddingData $EmbeddingData `
                -Profile $EmbeddingProfile
        } catch {
            $EmbeddingProfile = [string]$EmbeddingData.contract.default_profile
        }
    }
    $EmbeddingDevice = Resolve-HiaEmbeddingDevice -Device $EmbeddingDevice
    $checks = @()
    $checks += @(Invoke-HiaHoudiniChecks -HoudiniExe $HoudiniExe -Candidates $Candidates -TimeoutSeconds $TimeoutSeconds -ProbeOverrides $ProbeOverrides)
    $checks += @(
        Invoke-HiaProjectChecks `
            -ProjectRoot $root `
            -HoudiniExe $HoudiniExe `
            -BridgePython $BridgePython `
            -RenderOutputDir $RenderOutputDir `
            -McpBackend $McpBackend `
            -EmbeddingData $EmbeddingData `
            -EmbeddingProfile $EmbeddingProfile `
            -EmbeddingDevice $EmbeddingDevice `
            -TimeoutSeconds $TimeoutSeconds `
            -ProbeOverrides $ProbeOverrides
    )
    $level = Get-HiaOverallLevel -Checks $checks
    $result = [ordered]@{
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
    if ($null -ne $EmbeddingData) {
        $result[[string]$EmbeddingData.contract.settings.profile] = $EmbeddingProfile
        $result[[string]$EmbeddingData.contract.settings.device] = $EmbeddingDevice
    }
    return [pscustomobject]$result
}

function ConvertTo-HiaRedactedText {
    param([AllowEmptyString()][string]$Text = '')

    $indexEnvironmentName = (
        '(?:' +
        'uv_(?:config_file|default_index|extra_index_url|find_links|index|' +
        'index_strategy|index_url|insecure_host|no_config|no_index|offline|' +
        'torch_backend)|' +
        'pip_(?:config_file|extra_index_url|find_links|index_url|no_index|' +
        'trusted_host))'
    )
    $proxyEnvironmentName = '(?:(?:http|https|all|no)_proxy)'
    $sensitiveEnvironmentName = (
        '(?:' + $indexEnvironmentName + '|' + $proxyEnvironmentName + ')'
    )
    $sensitiveName = (
        '(?:token|cookie|api[_-]?key|authorization|password|secret|' +
        'auth(?:orization)?[_-]?code|' +
        $sensitiveEnvironmentName + ')'
    )
    $safe = [string]$Text
    $safe = [regex]::Replace(
        $safe,
        '(?i)(Authorization\s*:\s*Basic)\s+[A-Za-z0-9+/=]+',
        '$1 [REDACTED]'
    )
    $safe = [regex]::Replace(
        $safe,
        '(?i)Bearer\s+[A-Za-z0-9._~+\-/=]+',
        'Bearer [REDACTED]'
    )
    $safe = [regex]::Replace(
        $safe,
        '(?i)(https?://)[^@/\s]+@',
        '$1[REDACTED]@'
    )
    $safe = [regex]::Replace(
        $safe,
        '(?i)\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{8,}',
        '[REDACTED]'
    )
    $safe = [regex]::Replace(
        $safe,
        '(?i)(' + $sensitiveName + '\s*[:=]\s*)[^\s",;}]+',
        '$1[REDACTED]'
    )
    $safe = [regex]::Replace(
        $safe,
        '(?i)(' + $sensitiveEnvironmentName +
            '\s*[:=]\s*)(?:(?!\\[rn]|[\r\n"]).)*',
        '$1[REDACTED]'
    )
    return $safe
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
    $indexEnvironmentName = (
        '(?:' +
        'uv_(?:config_file|default_index|extra_index_url|find_links|index|' +
        'index_strategy|index_url|insecure_host|no_config|no_index|offline|' +
        'torch_backend)|' +
        'pip_(?:config_file|extra_index_url|find_links|index_url|no_index|' +
        'trusted_host))'
    )
    $proxyEnvironmentName = '(?:(?:http|https|all|no)_proxy)'
    $sensitiveName = (
        '(?:token|cookie|api[_-]?key|authorization|password|secret|' +
        'auth(?:orization)?[_-]?code|' +
        $indexEnvironmentName + '|' + $proxyEnvironmentName + ')'
    )
    $json = [regex]::Replace(
        $json,
        '(?i)("[^"\r\n]*' + $sensitiveName + '[^"\r\n]*"\s*:\s*)"(?:\\.|[^"\\])*"',
        '$1"[REDACTED]"'
    )
    return ConvertTo-HiaRedactedText -Text $json
}

function Write-HiaPreflightReport {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$Result,
        [Parameter(Mandatory = $true)][string]$ProjectRoot
    )

    $reportDirectory = Resolve-HiaLauncherStoragePath `
        -ProjectRoot $ProjectRoot `
        -CreateDirectory
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fff') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 8)
    $jsonPath = Resolve-HiaLauncherStoragePath `
        -ProjectRoot $ProjectRoot `
        -LeafName "preflight-$stamp.json" `
        -AllowMissingLeaf
    $logPath = Resolve-HiaLauncherStoragePath `
        -ProjectRoot $ProjectRoot `
        -LeafName "preflight-$stamp.log" `
        -AllowMissingLeaf
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
    $logText = ConvertTo-HiaRedactedText -Text $logText
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
            $directory -isnot [System.IO.DirectoryInfo] -or
            $directory.Name -ne 'checkpoints' -or
            $directory.Parent.Name -notmatch '^[0-9a-fA-F]{32}$' -or
            $directory.Parent.Parent.Name -ne 'launcher-sessions' -or
            ([int]$directory.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
            ([int]$directory.Parent.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
            ([int]$directory.Parent.Parent.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
            (($ThreadId -and -not $GoalBinding) -or ($GoalBinding -and -not $ThreadId))
        ) {
            return $null
        }
        if (
            ($ThreadId -and $ThreadId -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$') -or
            ($GoalBinding -and $GoalBinding -notmatch '^[0-9a-f]{64}$')
        ) {
            return $null
        }
        $markerPath = Join-Path $directory.FullName '.hia-stage-checkpoint.json'
        if (Test-Path -LiteralPath $markerPath -PathType Leaf) {
            $markerFile = Get-Item -LiteralPath $markerPath -Force -ErrorAction Stop
            if (
                ([int]$markerFile.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
                [long]$markerFile.Length -le 0 -or
                [long]$markerFile.Length -gt 65536
            ) {
                return $null
            }
            $marker = [System.IO.File]::ReadAllText($markerFile.FullName) | ConvertFrom-Json
            $markerVersion = [int]$marker.version
            $markerThread = [string]$marker.thread_id
            $markerGoal = [string]$marker.goal_binding
            $checkpointName = [string]$marker.checkpoint_file
            if (
                $markerThread -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$' -or
                $markerGoal -notmatch '^[0-9a-f]{64}$' -or
                ($ThreadId -and -not [System.StringComparer]::Ordinal.Equals($markerThread, $ThreadId)) -or
                ($GoalBinding -and -not [System.StringComparer]::Ordinal.Equals($markerGoal, $GoalBinding)) -or
                -not $checkpointName -or
                $checkpointName -ne [System.IO.Path]::GetFileName($checkpointName) -or
                $checkpointName -notmatch '(?i)\.hip(?:lc|nc)?(?:_bak\d*)?$'
            ) {
                return $null
            }
            $checkpointPath = $null
            $storageScope = 'runtime_fallback'
            $sourceHipPath = $null
            if ($markerVersion -eq 2) {
                $storageScope = [string]$marker.storage_scope
                if (
                    -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
                        [string]$marker.launcher_session_id,
                        $directory.Parent.Name
                    ) -or
                    $storageScope -notin @('hip', 'runtime_fallback')
                ) {
                    return $null
                }
                if ($storageScope -eq 'runtime_fallback') {
                    if (-not [string]::IsNullOrWhiteSpace([string]$marker.source_hip_path)) {
                        return $null
                    }
                    $checkpointPath = Join-Path $directory.FullName $checkpointName
                } else {
                    $rawSourceHip = [string]$marker.source_hip_path
                    if (
                        [string]::IsNullOrWhiteSpace($rawSourceHip) -or
                        -not [System.IO.Path]::IsPathRooted($rawSourceHip)
                    ) {
                        return $null
                    }
                    $sourceHipPath = [System.IO.Path]::GetFullPath($rawSourceHip)
                    if (
                        [System.IO.Path]::GetPathRoot($sourceHipPath) -notmatch
                            '^[A-Za-z]:\\$'
                    ) {
                        return $null
                    }
                    $sourceHip = Get-Item -LiteralPath $sourceHipPath -Force -ErrorAction Stop
                    $sourceParent = $sourceHip.Directory
                    if (
                        $sourceHip -isnot [System.IO.FileInfo] -or
                        [long]$sourceHip.Length -le 0 -or
                        $null -eq $sourceParent.Parent -or
                        $sourceHip.Name -match '(?i)^untitled(?:\d+)?\.hip(?:lc|nc)?$' -or
                        $sourceHip.Name -notmatch '(?i)\.hip(?:lc|nc)?$' -or
                        ([int]$sourceHip.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
                        ([int]$sourceParent.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
                        -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
                            $sourceHip.FullName,
                            $sourceHipPath
                        ) -or
                        -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
                            $rawSourceHip,
                            $sourceHipPath
                        )
                    ) {
                        return $null
                    }
                    $hiaDirectory = Get-Item `
                        -LiteralPath (Join-Path $sourceParent.FullName '.hia') `
                        -Force `
                        -ErrorAction Stop
                    $externalCheckpointDirectory = Get-Item `
                        -LiteralPath (Join-Path $hiaDirectory.FullName 'checkpoints') `
                        -Force `
                        -ErrorAction Stop
                    if (
                        $hiaDirectory -isnot [System.IO.DirectoryInfo] -or
                        $externalCheckpointDirectory -isnot [System.IO.DirectoryInfo] -or
                        ([int]$hiaDirectory.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
                        ([int]$externalCheckpointDirectory.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
                        -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
                            $hiaDirectory.Parent.FullName,
                            $sourceParent.FullName
                        ) -or
                        -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
                            $externalCheckpointDirectory.Parent.FullName,
                            $hiaDirectory.FullName
                        )
                    ) {
                        return $null
                    }
                    $externalMarkerPath = Join-Path `
                        $externalCheckpointDirectory.FullName `
                        '.hia-stage-checkpoint.json'
                    $externalMarkerFile = Get-Item `
                        -LiteralPath $externalMarkerPath `
                        -Force `
                        -ErrorAction Stop
                    if (
                        $externalMarkerFile -isnot [System.IO.FileInfo] -or
                        ([int]$externalMarkerFile.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
                        [long]$externalMarkerFile.Length -le 0 -or
                        [long]$externalMarkerFile.Length -gt 65536
                    ) {
                        return $null
                    }
                    $externalMarker = (
                        [System.IO.File]::ReadAllText($externalMarkerFile.FullName) |
                            ConvertFrom-Json
                    )
                    if (
                        [int]$externalMarker.version -ne 2 -or
                        -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
                            [string]$externalMarker.launcher_session_id,
                            [string]$marker.launcher_session_id
                        ) -or
                        -not [System.StringComparer]::Ordinal.Equals(
                            [string]$externalMarker.thread_id,
                            $markerThread
                        ) -or
                        -not [System.StringComparer]::Ordinal.Equals(
                            [string]$externalMarker.goal_binding,
                            $markerGoal
                        ) -or
                        -not [System.StringComparer]::Ordinal.Equals(
                            [string]$externalMarker.storage_scope,
                            'hip'
                        ) -or
                        -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
                            [string]$externalMarker.source_hip_path,
                            $sourceHipPath
                        ) -or
                        -not [System.StringComparer]::Ordinal.Equals(
                            [string]$externalMarker.checkpoint_file,
                            $checkpointName
                        )
                    ) {
                        return $null
                    }
                    $checkpointPath = Join-Path `
                        $externalCheckpointDirectory.FullName `
                        $checkpointName
                }
            } elseif ($markerVersion -eq 1 -and $ThreadId) {
                $checkpointPath = Join-Path $directory.FullName $checkpointName
            } elseif ($markerVersion -ne 1) {
                return $null
            }
            if ($checkpointPath) {
                $checkpoint = Get-Item -LiteralPath $checkpointPath -Force -ErrorAction Stop
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
                    thread_id = $markerThread
                    goal_binding = $markerGoal
                    launcher_session_id = $directory.Parent.Name
                    storage_scope = $storageScope
                    source_hip_path = $sourceHipPath
                }
            }
        } elseif ($ThreadId) {
            return $null
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
        [Parameter(Mandatory = $true)][ValidateRange(1, 99)][int]$Attempt,
        [AllowEmptyString()][string]$ThreadId = '',
        [AllowEmptyString()][string]$GoalBinding = ''
    )

    $session = Get-Item -LiteralPath $SessionRoot -Force -ErrorAction Stop
    if (
        $session -isnot [System.IO.DirectoryInfo] -or
        $session.Name -notmatch '^[0-9a-fA-F]{32}$' -or
        $session.Parent.Name -ne 'launcher-sessions' -or
        ([int]$session.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
        ([int]$session.Parent.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw 'Recovery requires an ordinary launcher session directory.'
    }
    $source = Get-Item -LiteralPath $SourcePath -Force -ErrorAction Stop
    $checkpoints = Join-Path $session.FullName 'checkpoints'
    $temp = Join-Path $session.FullName 'tmp'
    $sourceParent = $source.Directory.FullName.TrimEnd('\')
    $sourceIsSessionLocal = (
        [System.StringComparer]::OrdinalIgnoreCase.Equals(
            $sourceParent,
            $checkpoints.TrimEnd('\')
        ) -or
        [System.StringComparer]::OrdinalIgnoreCase.Equals(
            $sourceParent,
            $temp.TrimEnd('\')
        )
    )
    if (-not $sourceIsSessionLocal) {
        if (
            $ThreadId -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$' -or
            $GoalBinding -notmatch '^[0-9a-f]{64}$'
        ) {
            throw 'External recovery requires the exact active Thread and Goal binding.'
        }
        $validatedCheckpoint = Get-HiaLatestLauncherCheckpoint `
            -CheckpointDirectory $checkpoints `
            -ThreadId $ThreadId `
            -GoalBinding $GoalBinding
        if (
            $null -eq $validatedCheckpoint -or
            -not [System.StringComparer]::OrdinalIgnoreCase.Equals(
                [string]$validatedCheckpoint.path,
                $source.FullName
            ) -or
            -not [System.StringComparer]::Ordinal.Equals(
                [string]$validatedCheckpoint.storage_scope,
                'hip'
            )
        ) {
            throw 'External recovery source is not bound to this launcher session checkpoint marker.'
        }
    }
    if (
        $source -isnot [System.IO.FileInfo] -or
        ([int]$source.Attributes -band [int][System.IO.FileAttributes]::ReparsePoint) -ne 0 -or
        [long]$source.Length -le 0 -or
        (-not $sourceIsSessionLocal -and $null -eq $validatedCheckpoint)
    ) {
        throw 'Recovery source must be an ordinary session HIP or a validated HIP-local checkpoint.'
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

    $managedPath = Get-HiaManagedBridgePythonPath -ProjectRoot $ProjectRoot
    $defaults = [ordered]@{
        houdini_exe = ''
        bridge_python = ''
        bridge_python_mode = 'managed'
        bridge_python_managed = $managedPath
        bridge_python_advanced = ''
        render_output_dir = ''
        mcp_backend = 'hia_v2'
    }
    $settingsPath = Resolve-HiaLauncherStoragePath `
        -ProjectRoot $ProjectRoot `
        -LeafName 'settings.json' `
        -AllowMissingLeaf
    if (
        [string]::IsNullOrWhiteSpace($settingsPath) -or
        -not (Test-Path -LiteralPath $settingsPath -PathType Leaf)
    ) {
        return [pscustomobject]$defaults
    }
    try {
        $settings = [System.IO.File]::ReadAllText($settingsPath) | ConvertFrom-Json
        $values = [ordered]@{}
        foreach ($property in $settings.PSObject.Properties) {
            $values[[string]$property.Name] = $property.Value
        }
        $houdiniProperty = $settings.PSObject.Properties['houdini_exe']
        $bridgeProperty = $settings.PSObject.Properties['bridge_python']
        $bridgeModeProperty = $settings.PSObject.Properties['bridge_python_mode']
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
        $storedBridge = if ($null -eq $bridgeProperty) { '' } else { [string]$bridgeProperty.Value }
        $bridgeMode = if ($null -eq $bridgeModeProperty) {
            if ([System.IO.Path]::IsPathRooted($storedBridge)) { 'external' } else { 'managed' }
        } else {
            [string]$bridgeModeProperty.Value
        }
        if ($bridgeMode -notin @('managed', 'external')) {
            $bridgeMode = 'managed'
        }
        $advancedBridge = ''
        if (
            $bridgeMode -eq 'external' -and
            -not [string]::IsNullOrWhiteSpace($storedBridge) -and
            [System.IO.Path]::IsPathRooted($storedBridge)
        ) {
            try { $advancedBridge = [System.IO.Path]::GetFullPath($storedBridge) } catch { }
        }
        $values['houdini_exe'] = if ($null -eq $houdiniProperty) { '' } else { [string]$houdiniProperty.Value }
        $values['bridge_python'] = ''
        $values['bridge_python_mode'] = $bridgeMode
        $values['bridge_python_managed'] = $managedPath
        $values['bridge_python_advanced'] = $advancedBridge
        $values['render_output_dir'] = if ($null -eq $settings.PSObject.Properties['render_output_dir']) { '' } else { [string]$settings.render_output_dir }
        $values['mcp_backend'] = $backend
        return [pscustomobject]$values
    } catch {
        return [pscustomobject]$defaults
    }
}

function Write-HiaLauncherSettings {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)][string]$HoudiniExe,
        [Parameter(Mandatory = $true)][string]$BridgePython,
        [AllowEmptyString()][string]$RenderOutputDir = '',
        [ValidateSet('hia_v2', 'fxhoudini')][string]$McpBackend = 'hia_v2',
        [AllowNull()]$EmbeddingData = $null,
        [AllowEmptyString()][string]$EmbeddingProfile = '',
        [AllowEmptyString()][string]$EmbeddingDevice = ''
    )

    [void](Resolve-HiaLauncherStoragePath `
        -ProjectRoot $ProjectRoot `
        -CreateDirectory)
    $settingsPath = Resolve-HiaLauncherStoragePath `
        -ProjectRoot $ProjectRoot `
        -LeafName 'settings.json' `
        -AllowMissingLeaf
    $storedRenderOutput = if ([string]::IsNullOrWhiteSpace($RenderOutputDir)) {
        ''
    } else {
        Resolve-HiaRenderOutputDirectory `
            -ProjectRoot $ProjectRoot `
            -Path $RenderOutputDir `
            -HoudiniExe $HoudiniExe
    }
    $settings = [ordered]@{}
    if (Test-Path -LiteralPath $settingsPath -PathType Leaf) {
        try {
            $existing = [System.IO.File]::ReadAllText($settingsPath) | ConvertFrom-Json
            foreach ($property in $existing.PSObject.Properties) {
                $settings[[string]$property.Name] = $property.Value
            }
        } catch { }
    }
    $settings['houdini_exe'] = [System.IO.Path]::GetFullPath($HoudiniExe)
    $bridgeFullPath = [System.IO.Path]::GetFullPath($BridgePython)
    $managedBridge = Get-HiaManagedBridgePythonPath -ProjectRoot $ProjectRoot
    if ([System.StringComparer]::OrdinalIgnoreCase.Equals($bridgeFullPath, $managedBridge)) {
        $settings['bridge_python_mode'] = 'managed'
        $settings['bridge_python'] = '.venv/Scripts/python.exe'
    } else {
        [void]$settings.Remove('bridge_python_mode')
        $settings['bridge_python'] = $bridgeFullPath
    }
    $settings['render_output_dir'] = $storedRenderOutput
    $settings['mcp_backend'] = $McpBackend
    if ($null -ne $EmbeddingData) {
        $resolvedEmbedding = Resolve-HiaEmbeddingProfile `
            -EmbeddingData $EmbeddingData `
            -Profile $EmbeddingProfile
        $settings[[string]$EmbeddingData.contract.settings.profile] = $resolvedEmbedding
        $settings[[string]$EmbeddingData.contract.settings.device] = (
            Resolve-HiaEmbeddingDevice -Device $EmbeddingDevice
        )
    }
    $json = $settings | ConvertTo-Json
    $settingsPath = Resolve-HiaLauncherStoragePath `
        -ProjectRoot $ProjectRoot `
        -LeafName 'settings.json' `
        -AllowMissingLeaf
    [System.IO.File]::WriteAllText($settingsPath, $json + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
    return $settingsPath
}

function Write-HiaEmbeddingPreference {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [Parameter(Mandatory = $true)]$EmbeddingData,
        [Parameter(Mandatory = $true)][string]$EmbeddingProfile,
        [AllowEmptyString()][string]$EmbeddingDevice = ''
    )

    $resolved = Resolve-HiaEmbeddingProfile `
        -EmbeddingData $EmbeddingData `
        -Profile $EmbeddingProfile
    [void](Resolve-HiaLauncherStoragePath `
        -ProjectRoot $ProjectRoot `
        -CreateDirectory)
    $settingsPath = Resolve-HiaLauncherStoragePath `
        -ProjectRoot $ProjectRoot `
        -LeafName 'settings.json' `
        -AllowMissingLeaf
    $settings = [ordered]@{}
    if (Test-Path -LiteralPath $settingsPath -PathType Leaf) {
        try {
            $existing = [System.IO.File]::ReadAllText($settingsPath) | ConvertFrom-Json
            foreach ($property in $existing.PSObject.Properties) {
                $settings[[string]$property.Name] = $property.Value
            }
        } catch { }
    }
    $settings[[string]$EmbeddingData.contract.settings.profile] = $resolved
    $settings[[string]$EmbeddingData.contract.settings.device] = (
        Resolve-HiaEmbeddingDevice -Device $EmbeddingDevice
    )
    $json = $settings | ConvertTo-Json
    $settingsPath = Resolve-HiaLauncherStoragePath `
        -ProjectRoot $ProjectRoot `
        -LeafName 'settings.json' `
        -AllowMissingLeaf
    [System.IO.File]::WriteAllText(
        $settingsPath,
        $json + [Environment]::NewLine,
        [System.Text.UTF8Encoding]::new($false)
    )
    return $settingsPath
}

function Repair-HiaSafeProject {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param([Parameter(Mandatory = $true)][string]$ProjectRoot)

    $actions = [System.Collections.Generic.List[string]]::new()
    $runtimePath = Join-Path $ProjectRoot '.runtime'
    $launcherPath = Join-Path $runtimePath 'launcher'
    if (
        -not (Test-Path -LiteralPath $launcherPath -PathType Container) -and
        $PSCmdlet.ShouldProcess(
            $launcherPath,
            'Create project-local launcher runtime directory'
        )
    ) {
        $runtimeMissing = -not (Test-Path -LiteralPath $runtimePath -PathType Container)
        [void](Resolve-HiaLauncherStoragePath `
            -ProjectRoot $ProjectRoot `
            -CreateDirectory)
        if ($runtimeMissing) { $actions.Add('已创建 .runtime') }
        $actions.Add('已创建 .runtime\launcher')
    } elseif (Test-Path -LiteralPath $launcherPath) {
        [void](Resolve-HiaLauncherStoragePath -ProjectRoot $ProjectRoot)
    }
    foreach ($relative in @('.runtime\tmp')) {
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
    'ConvertFrom-HiaAssetJsonLine',
    'ConvertFrom-HiaKnowledgeIndexJsonLine',
    'ConvertTo-HiaProcessArgument',
    'ConvertTo-HiaRedactedJson',
    'ConvertTo-HiaRedactedText',
    'Copy-HiaLauncherRecoveryHip',
    'Get-HiaBridgePythonCandidates',
    'Get-HiaCodexLoginCommand',
    'Get-HiaCrashRecoveryDecision',
    'Get-HiaEmbeddingCheckResult',
    'Get-HiaEmbeddingContractData',
    'Get-HiaEmbeddingDeviceChoices',
    'Get-HiaEmbeddingInstallLockInfo',
    'Get-HiaEmbeddingProfileChoices',
    'Get-HiaEmbeddingProfileContract',
    'Get-HiaEmbeddingRuntimeState',
    'Get-HiaHoudiniCandidates',
    'Get-HiaLatestLauncherCheckpoint',
    'Get-HiaLatestLauncherCrashHip',
    'Get-HiaManagedBridgePythonPath',
    'Get-HiaManagedBridgePythonState',
    'Get-HiaMcpBackendChoices',
    'Get-HiaOverallLevel',
    'Get-HiaPinnedCodexExecutable',
    'Get-HiaProjectRoot',
    'Get-HiaProbePayload',
    'Get-HiaRecoverableLauncherSession',
    'Invoke-HiaScreenshotCacheCleanup',
    'Invoke-HiaPreflight',
    'Invoke-HiaProcess',
    'New-HiaAssetCliProcessPlan',
    'New-HiaKnowledgeCliProcessPlan',
    'New-HiaKnowledgeIndexProcessPlan',
    'Read-HiaLauncherSettings',
    'Repair-HiaSafeProject',
    'Resolve-HiaRenderOutputDirectory',
    'Resolve-HiaLauncherStoragePath',
    'Resolve-HiaEmbeddingDevice',
    'Resolve-HiaEmbeddingProfile',
    'Resolve-HiaManagedBridgePython',
    'Resolve-HiaMcpBackend',
    'Set-HiaLauncherRecoveryDecision',
    'Test-HiaHoudiniProbeConsistency',
    'Test-HiaLoopbackPorts',
    'Test-HiaRuntimeWritable',
    'Write-HiaEmbeddingPreference',
    'Write-HiaLauncherSettings',
    'Write-HiaPreflightReport'
)
