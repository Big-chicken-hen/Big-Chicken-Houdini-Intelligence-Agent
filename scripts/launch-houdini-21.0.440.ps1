[CmdletBinding()]
param(
    [string]$BridgePython = 'D:\Python_3.10\python.exe',
    [string]$HoudiniExe = ''
)

& (Join-Path $PSScriptRoot 'launch-houdini.ps1') `
    -BridgePython $BridgePython `
    -HoudiniExe $HoudiniExe
