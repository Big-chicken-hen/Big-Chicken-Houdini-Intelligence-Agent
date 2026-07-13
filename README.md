# Houdini Intelligence Agent

This repository contains the safety-first foundation for a Houdini-embedded Codex client.

P0-B is intentionally small. It defines the architecture, phase boundaries, repository safety policy, and a Windows path validator. It does not connect to Houdini, start a bridge, install dependencies, or modify a scene.

## Current scope

- Codex is the only intelligent component.
- Houdini-facing components will provide deterministic tools and execution only.
- All project-owned runtime data must remain beneath `E:\houdini-intelligence-agent`.
- The current implementation contains no deletion, move, cleanup, shell, or arbitrary Python facility.

## Offline verification

Run the standard-library unit tests from the repository root:

```powershell
python -m unittest discover -s tests -t . -v
```

No third-party test dependency is required.
