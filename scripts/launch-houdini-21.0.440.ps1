[CmdletBinding()]
param(
    [string]$BridgePython = '',
    [string]$HoudiniExe = '',
    [ValidateSet('hia_v2', 'fxhoudini')][string]$McpBackend = 'hia_v2'
)

& (Join-Path $PSScriptRoot 'launch-houdini.ps1') `
    -BridgePython $BridgePython `
    -HoudiniExe $HoudiniExe `
    -McpBackend $McpBackend
