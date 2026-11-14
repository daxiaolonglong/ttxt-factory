"""六角色文件:列出、读取与调度上下文装载(纯项目本地,不写全局配置)。"""

from __future__ import annotations

import re
from pathlib import Path

from ttxt import config as cfg

ROLE_RE = re.compile(r"^[a-z][a-z0-9_\-]{1,32}$")
SKILLS_ROOT = cfg.PROJECT_ROOT / ".dsh" / "skills"

ROLE_DESCRIPTIONS = {
    "orchestrator": "头条智能工厂总调度:任务路由、类目隔离、模型串行队列与验收汇总",
    "collector": "头条智能工厂采集:来源管理、robots 遵守、限速重试、断点续采与去重入库",
    "data_engineer": "头条智能工厂数据工程:清洗、时间归一化、simhash 聚类、schema 迁移与类目隔离",
    "viral_researcher": "头条智能工厂爆款研究:指标分析、爆款标签、关键词评分与知识资产",
    "writer_reviewer": "头条智能工厂写作评审:取材写作、事实证据链、反向评审、修订与草稿导出",
    "maintainer": "头条智能工厂工程维护:测试、备份回滚、磁盘限额、故障恢复与 Wiki 同步",
}


def list_roles() -> list[dict]:
    roles = []
    for path in sorted(cfg.AGENTS_DIR.glob("*.md")):
        roles.append({"name": path.stem, "path": str(path), "bytes": path.stat().st_size})
    return roles


def read_role(name: str) -> str:
    if not ROLE_RE.fullmatch(name):
        raise ValueError(f"invalid role name: {name!r}")
    path = cfg.AGENTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"role not found: {name}")
    return path.read_text(encoding="utf-8")


def role_context(name: str) -> str:
    rules = (cfg.RULES_DIR / "CORE_RULES.md").read_text(encoding="utf-8")
    return (
        f"# 角色:{name}\n\n{read_role(name)}\n\n"
        f"# 核心规则\n\n{rules}\n\n"
        "# 类目隔离\n本次任务只允许操作单一类目;禁止引用其他类目的数据与上下文。\n"
    )


def export_skills(root: Path | None = None) -> list[str]:
    """把六角色导出为 DSH 项目级技能(D:\\ttxt\\.dsh\\skills,rank 100)。

    DSH 项目根 = 最近的 .git 祖先;无 .git 时使用当前工作目录 D:\\ttxt。
    只写项目内目录,不触碰 DSH 全局配置。技能名要求连字符,角色名中的下划线转为连字符。

    ``root`` 可指定输出根目录(``<root>/<skill>/SKILL.md``),用于测试导出到临时目录,
    避免覆盖 git 跟踪的项目技能。
    """
    skills_root = SKILLS_ROOT if root is None else Path(root)
    created: list[str] = []
    for role in list_roles():
        name = f"ttxt-{role['name'].replace('_', '-')}"
        directory = skills_root / name
        directory.mkdir(parents=True, exist_ok=True)
        description = ROLE_DESCRIPTIONS.get(role["name"], f"头条智能工厂角色:{role['name']}")
        body = read_role(role["name"]).strip()
        content = f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n"
        path = directory / "SKILL.md"
        path.write_text(content, encoding="utf-8")
        created.append(str(path))
    return created
