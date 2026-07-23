# Third-party notices

Big-Chicken Houdini Intelligence Agent is an independent project. Its project license is separate from the licenses listed below.

## OpenAI Codex

Big-Chicken Houdini Intelligence Agent integrates with the Codex CLI/app-server and includes protocol schemas generated from the pinned Codex 0.144.3 executable.

- Project: OpenAI Codex
- Source: <https://github.com/openai/codex>
- License: Apache License 2.0
- Copyright: OpenAI and Codex contributors

The source repository and public Preview package do not contain Codex credentials. If a Codex binary is distributed with a future package, that package must also include the complete applicable Apache 2.0 license and any upstream notices.

## FXHoudiniMCP

Big-Chicken Houdini Intelligence Agent can use FXHoudiniMCP 1.3.0 as an explicitly selected compatibility fallback. It is not copied into Big-Chicken Houdini Intelligence Agent source and is not included in the default public Preview package.

- Project: FXHoudiniMCP
- Source: <https://github.com/healkeiser/fxhoudinimcp>
- License: MIT License
- Copyright: 2026 FXHoudini-MCP Contributors

Any package that redistributes FXHoudiniMCP must include its original copyright notice and MIT license.

## Microsoft .NET

The optional self-contained Windows launcher is built with Microsoft .NET 8 and may redistribute .NET runtime components and native WPF sidecars under their applicable licenses.

- Project: .NET
- Source and license information: <https://github.com/dotnet/runtime>

Published launcher artifacts must retain the Microsoft and third-party notices applicable to the exact `dotnet publish` output.

The public ZIP includes the exact notice files from the project-local .NET 8 SDK
used for the launcher publish:

- `licenses/dotnet/LICENSE.txt`
- `licenses/dotnet/ThirdPartyNotices.txt`

## SideFX Houdini and PySide6

Houdini, HOM/`hou`, `hython`, and the PySide6 runtime used inside Houdini are supplied by the user's SideFX installation. Big-Chicken Houdini Intelligence Agent does not redistribute these components.

- Product: SideFX Houdini
- Website: <https://www.sidefx.com/products/houdini/>

Houdini and SideFX are trademarks of Side Effects Software Inc.

## Artwork

The public Preview uses the launcher's built-in dark gradient. It does not redistribute third-party character or promotional artwork without explicit redistribution permission.

## No endorsement

Big-Chicken Houdini Intelligence Agent is not affiliated with, endorsed by, or sponsored by SideFX, OpenAI, Microsoft, or the authors of the optional compatibility components. Product names are used only to describe interoperability.
