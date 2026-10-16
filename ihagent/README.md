# IHAgent package

Python package for [IHAgent](../README.md) (InnovatelHub Solutions Ltd). The console commands are `ihagent` and `hfagent` (both `hfagent.cli:main`). Version 0.4.0.

Install, hosts, configuration, slash commands, browser tools, MCP servers (including the Google Workspace preset), the macOS iheye overlay, Kaggle notes, and troubleshooting are in the [repository README](../README.md). This file used to duplicate that guide. An example `mcp.json` is in [`examples/mcp.json`](examples/mcp.json).

From this directory:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[browser,iheye]'
.venv/bin/playwright install chrome
.venv/bin/ihagent
```

`iheye` installs PyObjC on macOS (`pyobjc-framework-Cocoa`). The iheye bubble does nothing on other platforms. Desktop mouse control is optional on macOS, Linux, and Windows: `pip install -e '.[desktop]'` (off until `/mouse on`). That extra also draws the ihmouse robot (AppKit on macOS, PySide6 on Windows and Linux). See the repository README for permissions, the overlay, and the vision-model settings.

Tests:

```bash
.venv/bin/pip install -e '.[dev,browser,iheye]'
.venv/bin/pytest
```

Setup scripts in `scripts/` (`setup-machine`, `setup-huggingface`, `serve-local`, plus the `.ps1` variants) are described in the repository README.
