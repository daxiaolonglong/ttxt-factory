#!/usr/bin/env python
"""保存 / 检查 GitHub 令牌，保存一次之后 scripts\\push.cmd 不再需要贴令牌。

用法：
    scripts\\login.cmd                      # 交互输入（输入时不回显），并校验
    scripts\\login.cmd --token ghp_xxx      # 直接给令牌
    scripts\\login.cmd --check              # 只检查当前用的是哪份凭据
    scripts\\login.cmd --forget             # 删除本地凭据
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
import urllib.error
import urllib.request

from _github_auth import load_token, mask, save_token, token_path


def whoami(token: str) -> dict | None:
    req = urllib.request.Request(
        "https://api.github.com/user",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "ttxt-login",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        print(f"令牌被拒绝（HTTP {e.code}）：{e.reason}", file=sys.stderr)
        return None
    except Exception as e:  # 网络问题
        print(f"校验失败（{e}），令牌仍已保存", file=sys.stderr)
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", help="直接提供令牌（否则交互输入）")
    ap.add_argument("--check", action="store_true", help="只看当前凭据")
    ap.add_argument("--forget", action="store_true", help="删除本地凭据")
    args = ap.parse_args()

    if args.forget:
        path = token_path()
        if path.is_file():
            path.unlink()
            print(f"已删除 {path}")
        else:
            print("本地没有保存过令牌")
        return 0

    if args.check:
        token, source = load_token()
        if not token:
            print("没有可用凭据：请运行 scripts\\login.cmd 保存令牌")
            return 1
        print(f"凭据来源：{source}")
        print(f"令牌：{mask(token)}")
        me = whoami(token)
        if me:
            print(f"账号：{me.get('login')}")
        return 0

    token = (args.token or "").strip()
    if not token:
        token = getpass.getpass("粘贴 GitHub 令牌（输入不回显，回车确认）：").strip()
    if not token:
        print("没有输入令牌", file=sys.stderr)
        return 1
    if not token.startswith(("ghp_", "github_pat_")):
        print("这不像 GitHub 令牌（应以 ghp_ 或 github_pat_ 开头）", file=sys.stderr)
        return 1

    me = whoami(token)
    if me is None:
        return 1

    path = save_token(token)
    print(f"已保存到 {path}")
    print(f"账号：{me.get('login')}，令牌：{mask(token)}")
    print("以后直接运行：scripts\\push.cmd -m \"说明\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
