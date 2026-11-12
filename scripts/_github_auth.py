"""GitHub 令牌的本地保存与读取。

优先级：环境变量 GITHUB_TOKEN / GH_TOKEN → 本机凭据文件。
凭据文件默认放在项目下的 `.secrets/github_token`（已被 .gitignore 排除），
可用环境变量 TTXT_GITHUB_TOKEN_FILE 指向别处。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def token_path() -> Path:
    override = (os.environ.get("TTXT_GITHUB_TOKEN_FILE") or "").strip()
    if override:
        return Path(override)
    return ROOT / ".secrets" / "github_token"


def load_token() -> tuple[str, str]:
    """返回 (令牌, 来源说明)；没有则返回 ("", "")。"""
    for name in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value, f"环境变量 {name}"
    path = token_path()
    if path.is_file():
        value = path.read_text(encoding="utf-8").strip()
        if value:
            return value, f"本地凭据文件 {path}"
    return "", ""


def save_token(token: str) -> Path:
    """保存令牌并把文件权限收紧到当前用户。"""
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token.strip() + "\n", encoding="utf-8")
    user = os.environ.get("USERNAME") or ""
    if user:
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
            capture_output=True,
            check=False,
        )
    return path


def mask(token: str) -> str:
    return f"{token[:7]}…{token[-4:]}" if len(token) > 14 else "***"
