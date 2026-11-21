"""G5:写作 few-shot 样本库(只用实测样本;库空/缺失自动降级,与 R59 viral 先验同一纪律)。

口径:
- 库文件 = ``00_control/fewshot_library.json``,样本只来自实测(探针/烟测的回执),
  ``verified=true`` 且 kind 合法才允许进提示词;手工编的样本一律进不来;
- 每个类目最多 ``FEWSHOT_MAX`` 条,防提示词膨胀(本地 147k 上下文);
- 库缺失/损坏/为空 ⇒ 返回空样本 + 如实状态串,写作链路自动降级为无示例,
  不改门禁、不改判定尺度(800 字门禁、事实门禁、评审阈值原样)。
"""

from __future__ import annotations

import json
from pathlib import Path

from ttxt import config as cfg
from ttxt.logging import get_logger

LOG = get_logger("writing")

FEWSHOT_MAX = 6
LIBRARY_PATH = cfg.CONTROL_DIR / "fewshot_library.json"
VALID_KINDS = ("verified_viral", "verified_junk")


def load_samples(
    category: str,
    limit: int = FEWSHOT_MAX,
    library_path: Path | None = None,
) -> tuple[list[dict], str]:
    """读取类目可用的 verified 样本。

    返回 (样本列表, 状态串)。状态串如实说明降级原因,写进写作回执
    ``fewshot_status``,不许把「没样本」写成功。
    """
    path = Path(library_path) if library_path is not None else LIBRARY_PATH
    if not path.exists():
        return [], "library_missing"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        LOG.warn("fewshot.library_unreadable", path=str(path), error=f"{type(exc).__name__}")
        return [], "library_unreadable"
    samples = raw.get("samples")
    if not isinstance(samples, list) or not samples:
        return [], "library_empty"
    picked: list[dict] = []
    for item in samples:
        if not isinstance(item, dict):
            continue
        if item.get("verified") is not True:
            continue
        if item.get("kind") not in VALID_KINDS:
            continue
        if item.get("category") != category:
            continue
        if not item.get("title") or not item.get("excerpt") or not item.get("note"):
            continue
        picked.append(item)
        if len(picked) >= int(limit):
            break
    if not picked:
        return [], "no_verified_samples"
    return picked, f"ok:{len(picked)}"


def format_samples(samples: list[dict]) -> str:
    """把样本拼成注入草稿提示词的文本块;无样本时返回空串(调用方据此不加块)。"""
    if not samples:
        return ""
    lines = ["写作示例(只参考风格与写法,事实内容必须来自上方参考材料):"]
    for i, sample in enumerate(samples, 1):
        if sample["kind"] == "verified_viral":
            lines.append(
                f"【正例{i}】标题:{sample['title']}\n"
                f"正文节选:{sample['excerpt']}\n"
                f"可学之处:{sample['note']}"
            )
        else:
            lines.append(
                f"【反例{i}(禁止模仿)】标题:{sample['title']}\n"
                f"正文节选:{sample['excerpt']}\n"
                f"问题:{sample['note']}"
            )
    return "\n".join(lines)
