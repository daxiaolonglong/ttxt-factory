# 一次性初始化项目虚拟环境(全部缓存落在 D:\ttxt)
$ErrorActionPreference = "Stop"
$root = "D:\ttxt"
$py = "C:\Users\yangl\AppData\Local\Programs\Python\Python312\python.exe"
if (-not (Test-Path $py)) { throw "Python 3.12 not found: $py" }

New-Item -ItemType Directory -Force -Path "$root\.pip-cache" | Out-Null
$env:PIP_CACHE_DIR = "$root\.pip-cache"
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
$env:PYTHONUTF8 = "1"

if (-not (Test-Path "$root\.venv\Scripts\python.exe")) {
    Write-Host "creating venv at $root\.venv ..."
    & $py -m venv "$root\.venv"
}

& "$root\.venv\Scripts\python.exe" -m pip install -r "$root\requirements-dev.txt"
& "$root\.venv\Scripts\python.exe" -m pytest --version
Write-Host "venv ready: $root\.venv"
