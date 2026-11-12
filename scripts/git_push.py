#!/usr/bin/env python
"""用 GitHub PAT 推送当前分支（令牌只从环境变量读，不落盘）。

用法：
    set GITHUB_TOKEN=ghp_xxxxxxxx
    scripts\\push.cmd                       # 推当前分支
    scripts\\push.cmd -m "feat: xxx"        # 先提交全部改动再推
    scripts\\push.cmd --dry-run             # 只看状态不推送
"""
from __future__ import annotations

import argparse
import base64
import shutil
import subprocess
import sys
from pathlib import Path

from _github_auth import load_token

ROOT = Path(__file__).resolve().parent.parent
GIT = ROOT / "tools" / "git" / "cmd" / "git.exe"


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    exe = str(GIT) if GIT.exists() else (shutil.which("git") or "git")
    cp = subprocess.run(
        [exe, "-c", "http.sslBackend=openssl", *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and cp.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} 失败:\n{cp.stderr.strip()}")
    return cp


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-m", "--message", help="提交信息；省略则只推送已有提交")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    token, source = load_token()
    if not token:
        print("没有可用凭据：先运行 scripts\\login.cmd 保存令牌（只需一次）", file=sys.stderr)
        return 1
    print(f"凭据来源：{source}")

    remote = git("remote", "get-url", "origin").stdout.strip()
    if "github.com/" not in remote:
        print(f"origin 不是 GitHub 地址：{remote}", file=sys.stderr)
        return 1
    user = remote.split("github.com/")[1].split("/")[0]
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    dirty = git("status", "--porcelain").stdout.strip()
    print(f"仓库 {user} 分支 {branch}，未提交改动 {len(dirty.splitlines())} 个文件")
    if args.dry_run:
        return 0

    if args.message:
        git("add", "-A")
        if git("diff", "--cached", "--name-only").stdout.strip():
            git("commit", "-m", args.message)
            print(f"已提交：{args.message}")
        else:
            print("没有需要提交的改动")

    auth = base64.b64encode(f"{user}:{token}".encode()).decode()
    cp = git(
        "-c", f"http.extraHeader=Authorization: Basic {auth}",
        "-c", "credential.helper=",
        "push", "origin", f"{branch}:{branch}",
        check=False,
    )
    sys.stdout.write(cp.stdout)
    if cp.returncode != 0:
        sys.stderr.write(cp.stderr)
        print("推送失败：检查令牌是否有 repo 权限", file=sys.stderr)
        return 1
    print(f"已推送 → {remote}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
