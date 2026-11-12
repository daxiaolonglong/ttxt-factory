#!/usr/bin/env python
"""一键把本地仓库接到 GitHub：建仓（如不存在）-> 设 remote -> 推送。

用法（令牌只从环境变量读，不落盘、不进 .git/config）：
    set GITHUB_TOKEN=ghp_xxxxxxxx
    .venv\\Scripts\\python.exe -X utf8 scripts\\github_connect.py --repo ttxt-factory
    .venv\\Scripts\\python.exe -X utf8 scripts\\github_connect.py --repo ttxt-factory --dry-run

退出码：0 成功 / 1 失败
"""
from __future__ import annotations

import argparse
import base64
import http.client
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"
ROOT = Path(__file__).resolve().parent.parent
GIT = ROOT / "tools" / "git" / "cmd" / "git.exe"


def _read_body(resp) -> bytes:
    """本机代理偶发截断响应体，读多少算多少。"""
    try:
        return resp.read()
    except http.client.IncompleteRead as e:  # noqa: PERF203
        return e.partial


def _json(raw: bytes) -> dict:
    try:
        return json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return {"message": raw.decode("utf-8", "replace")}


def api(path: str, token: str, method: str = "GET", payload: dict | None = None, retries: int = 3):
    data = json.dumps(payload).encode() if payload is not None else None
    last_err: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(
            API + path,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "ttxt-connect",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.status, _json(_read_body(r))
        except urllib.error.HTTPError as e:
            # 4xx/5xx 是明确结果，不重试
            return e.code, _json(_read_body(e))
        except Exception as e:  # URLError / 超时 / 连接被重置
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise SystemExit(f"网络请求失败（{method} {path}）：{last_err}")


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    exe = str(GIT) if GIT.exists() else (shutil.which("git") or "git")
    cp = subprocess.run(
        [exe, "-c", "core.quotepath=false", *args],
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
    ap.add_argument("--repo", required=True, help="GitHub 仓库名")
    ap.add_argument("--visibility", choices=["private", "public"], default="private")
    ap.add_argument(
        "--description",
        default="头条智能工厂：采集→清洗→去重→类目隔离→爆款分析→写作→评审→知识进化",
    )
    ap.add_argument("--dry-run", action="store_true", help="只检查令牌与仓库名，不建仓不推送")
    ap.add_argument("--message", default="chore: 初始化头条智能工厂源码仓库", help="首次提交的提交信息")
    args = ap.parse_args()

    token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if not token:
        print("缺少 GITHUB_TOKEN 环境变量", file=sys.stderr)
        return 1

    status, me = api("/user", token)
    if status != 200:
        print(f"令牌无效或权限不足（HTTP {status}）：{me.get('message')}", file=sys.stderr)
        return 1
    user = me["login"]
    print(f"已认证：{user}")

    full = f"{user}/{args.repo}"
    remote_url = f"https://github.com/{full}.git"
    status, info = api(f"/repos/{full}", token)
    if status == 200:
        print(f"仓库已存在：https://github.com/{full}")
    elif status == 404:
        if args.dry_run:
            print(f"[dry-run] 将要创建 {args.visibility} 仓库：{full}")
            return 0
        status, info = api(
            "/user/repos",
            token,
            method="POST",
            payload={
                "name": args.repo,
                "private": args.visibility == "private",
                "description": args.description,
                "has_issues": True,
                "has_wiki": True,
                "auto_init": False,
            },
        )
        if status in (200, 201):
            print(f"已创建仓库：https://github.com/{full}（{args.visibility}）")
        elif status == 422:
            print(f"同名仓库已存在，直接复用：https://github.com/{full}")
        else:
            print(f"建仓失败（HTTP {status}）：{info.get('message')}", file=sys.stderr)
            return 1
    else:
        print(f"查询仓库失败（HTTP {status}）：{info.get('message')}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"[dry-run] 将要设置 origin={remote_url} 并推送")
        return 0

    # 首次连接：若还没有提交，用 GitHub 账号身份完成首次提交（邮箱用 noreply，不泄露真实邮箱）
    if git("rev-parse", "--verify", "HEAD", check=False).returncode != 0:
        staged = git("diff", "--cached", "--name-only").stdout.strip()
        if not staged:
            print("仓库里还没有待提交内容（git add 后重试）", file=sys.stderr)
            return 1
        git("config", "user.name", user)
        git("config", "user.email", f"{user}@users.noreply.github.com")
        git("commit", "-m", args.message)
        print(f"已提交 {len(staged.splitlines())} 个文件：{args.message}")

    auth = base64.b64encode(f"{user}:{token}".encode()).decode()

    remotes = git("remote").stdout.split()
    if "origin" in remotes:
        git("remote", "set-url", "origin", remote_url)
    else:
        git("remote", "add", "origin", remote_url)
    print(f"origin -> {remote_url}")

    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "main"
    cp = git(
        "-c",
        f"http.extraHeader=Authorization: Basic {auth}",
        "-c",
        "credential.helper=",
        "push",
        "-u",
        "origin",
        f"{branch}:{branch}",
        check=False,
    )
    sys.stdout.write(cp.stdout)
    if cp.returncode != 0:
        sys.stderr.write(cp.stderr)
        print("推送失败（令牌需 repo 权限，且仓库未被保护分支拦截）", file=sys.stderr)
        return 1
    print(f"完成：https://github.com/{full}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
