# Websockets exposer for the spacenav driver (spacenav‑ws)

![PyPI version](https://img.shields.io/pypi/v/spacenav-ws)
![Build Status](https://github.com/rmstorm/spacenav-ws/workflows/Test/badge.svg)
![License](https://img.shields.io/github/license/rmstorm/spacenav-ws)

## Table of Contents

- [About](#about)  
- [Prerequisites](#prerequisites)  
- [Usage](#usage)  
- [Development](#development)  

## About

**spacenav‑ws** is a tiny Python CLI that exposes your 3Dconnexion SpaceMouse over a secure WebSocket, so Onshape on Linux can finally consume it. Under the hood it reverse‑engineers the same traffic Onshape’s Windows client uses and proxies it into your browser.

This lets you use [FreeSpacenav/spacenavd](https://github.com/FreeSpacenav/spacenavd) on Linux with Onshape.

## Prerequisites

- [uv/uvx](https://docs.astral.sh/uv/getting-started/installation/) or another Python env manager.
- A running instance of [spacenavd](https://github.com/FreeSpacenav/spacenavd)  
- A modern browser (Chrome/Firefox) with a userscript manager (Tampermonkey/Greasemonkey)  

## Usage

1. **Validate spacenavd**
```bash
uvx spacenav-ws@latest read-mouse
# → should print spacemouse events
```

2. **Run the server and trust the cert**
```bash
uvx spacenav-ws@latest serve
```
Now open: [https://127.51.68.120:8181](https://127.51.68.120:8181) Add a browser exception for the self‑signed cert.

3. **Install Tampermonkey and add the userscript**

Install [Tampermonkey](https://addons.mozilla.org/en-US/firefox/addon/tampermonkey/?utm_source=addons.mozilla.org&utm_medium=referral&utm_content=search). After installing, click this [link](https://greasyfork.org/en/scripts/533516-onshape-3d-mouse-on-linux-in-page-patch) for one‑click install of the script.

## Developing
```bash
git clone https://github.com/you/spacenav-ws.git
cd spacenav-ws
uv run spacenav-ws serve --hot-reload
```
This starts the server with Uvicorn's `code watching / hot reload` feature enabled.

## Deploying to pypi
Just run

```bash
uv build
uv publish
```
