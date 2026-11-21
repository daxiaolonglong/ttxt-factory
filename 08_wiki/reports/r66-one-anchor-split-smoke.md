# R66 一句一锚点：根因修复、全量回归与同题烟测（2026-11-21）

## 一、结论

- R63 唯一 critical「有人退、有人退」的确定性根因链已修复：同题烟测中，目标句被
  `multi_anchor_split` 精确拆成两句，R63 的残缺句 critical **未再出现**。
- 但本轮烟测仍为 **rejected**：3 轮独立评审均判「修改后再评」，问题集中在**引用/来源规范**
  （第 1、3 轮）、**弹性退休政策表述重复与术语精度**（第 2 轮）、**标题与正文兑现不足**
  （第 2 轮 + 本地 issue 的标题词面覆盖 44%），而不是 R63 的生成故障。
- 因此只能记为「R66 修复项已命中并验证」，**不能记为真实 L4 验收通过**，也不能据此宣布工厂完成。

## 二、代码交付（已推送 origin/main）

分支 `r66-writing-quality`，`0c19819..dd6d98e` 两个提交（快进、未 force；随后检查点
`b63662d` 已用 `git ls-remote` 核验）：

1. `fa1318a` R66-1 一句一锚点拆分
   - `ttxt/writing/style.py`：新增 `_split_two_anchor_sentence` / `_split_multi_anchor_line` /
     `_hard_numbers`，接入 `copyedit_draft`；只拆「恰好两个 ASCII 数字锚点、分处不同分句、
     非日期句」的句子，标点级拆分，不增删字词；`[n]` 引用标记随每个事实句保留；
     标题/表格/列表/多锚点政策句不动；拆不动原样返回。
   - `ttxt/writing/prompts.py`：`revise_prompt` 保留「删数字、保留动作与条件」，
     新增禁止「有人退、有人退」这类残缺/相邻重复/无主干碎片；新增「一句话只写一个数字锚点」；
     `repair_sentence_prompt` 增加同一禁令。
2. `dd6d98e` R66 检查点（只改 `08_wiki/session_checkpoint.md`）。

未动：`verifier.py` 判定与阈值（`QUOTE_MIN_OVERLAP=0.34`、±1 句、可核率放行线）、
`review.py` 盲评 prompt、独立评审 3 轮配额与轮次模型表、`repair.py` 的 `anchor_erased` 拒收、
任何门禁阈值。

## 三、全量回归

服务静默窗口内停 supervisor/scheduler/worker 后实跑（409.26s，日志落盘 22:28:51）；
同一停服窗口随后完成第四节的同题烟测，跑完 22:43:34 按
`worker --clear-stop → supervisor --clear-stop → supervisor --ensure` 恢复在线：

```text
python -m pytest 10_tests -q -p _r66_warm \
  --basetemp=D:/ttxt/07_tmp/pytest_basetemp/r66_full
```

- 结果：**1107 passed，2 warnings，409.26s，exit 0**。
- R65 全量基线 1101，R66 新增 6 条用例，数量吻合，零回归。
- `_r66_warm` 用 meta_path finder 强制 `ttxt`/子模块解析到本 worktree（本 worktree 的
  `10_tests/conftest.py` 会把 `D:\ttxt` 插到 `sys.path[0]`）；日志开头三行已确认
  `ttxt`、`style` 均来自 `d88451` worktree。
- 两条 warning 为环境类：浏览器锁测试读取子进程输出时编码告警；`.pytest_cache` 在工作树不可写。
  无测试失败。

## 四、同题烟测（延迟退休 养老金 / social）

口径与 R63 相同：真实本地 Strata 写作 + 真实 DeepSeek 独立评审（flash → v4-pro → flash）。
为避免再犯 R63 的 worktree 误写生产库，本轮把 `cfg.LOG_DIR`、`db.CONTROL_DIR`、
`db.CATEGORIES_DIR`、`gate.GATE_LOCK`、`queue.system_db_path` 全部重定向到
`D:/ttxt/07_tmp/r66_smoke`；模型队列、日志、导出、类目库均走快照。烟测 22:30:47 开始，
回执 22:42:40 落盘。回执：`03_intelligence/r66_smoke_receipt_one_anchor.json`。

| 读数 | R63（15:26） | R66（本次） | 说明 |
|---|---|---|---|
| status | rejected | rejected | 仍未打通真实 L4 |
| versions / revisions | 8 / 4 | 8 / 4 | — |
| score | 0.60 | **0.95** | 本地评分提高，不等于独立放行 |
| fact_checkability | 1.0（6/6） | 1.0（9/9） | 可核事实句 6→9，未降低放行线 |
| copyedit | 0 处 | **1 处 `multi_anchor_split`** | 目标句已被拆分 |
| 独立评审 | 2 轮，均「修改后再评」 | 3 轮（flash 25.4s / v4-pro 70.8s / flash 28.6s），均「修改后再评」 | 轮次模型规则生效 |
| R63 残缺句 critical | 有 | **无** | 修复目标命中 |
| 最终 critical | 「有人退、有人退」 | 仅「独立评审判定不通过:修改后再评」 | 不是生成故障回归 |

拆分实锤（回执 `copyedit_fixes`）：

```text
before: 同样是灵活就业身份，有人50岁顺利办退休领养老金，有人却被告知要等到55岁才能办[3]。
after : 同样是灵活就业身份，有人50岁顺利办退休领养老金[3]。有人却被告知要等到55岁才能办[3]。
```

定向修复账：claims 43 → facts_failed 12 → fixed_first 10 / unfixed 2
（均为 `grafted(锚点在引句窗口外·移花接木)`）；`anchor_erased=4`（守卫仍挡下
「删事实换放行」）。耗时 712.4s，比 R63 的 326.8s 长，原因是 3 轮独立评审与多轮修订。

### 生产零写入证据

烟测驱动器在运行前后对生产 `00_control/system.db`、social `articles.db`、`09_logs` 全量
做 size/mtime 指纹比对，回执 `production_guard.unchanged = true`。复核：生产系统库
`model_jobs` 仍为 2242 行（全部 done），social 生产库仍为 40746 行；模型任务只写进
`07_tmp/r66_smoke/00_control/system.db` 的副本（2268 行）。本轮没有新增生产库写入。

## 五、剩余缺口（按影响排序）

1. **引用/来源规范是第 1、3 轮的共同 blocker。** 两轮都点名「没有参考文献或来源说明」，
   并指出 [1]-[5] 没有文末来源；第 2 轮虽然主攻政策表述，也没有给出可发布结论。实际素材前 5 篇
   （`material_stats.selected_ids = 3747, 12004, 18622, 22884, 3978`）全部是
   `www.toutiao.com` 自媒体文章，`selected_distinct_hosts = 1`；只在文末补「参考资料」
   会把「来源是自媒体」暴露得更清楚，不能解决权威性问题。下一步应先补**权威来源采集**
   （政策原文/人社部/中国政府网/中新网政策稿）与权威来源优先的选材/引用格式，
   再评估独立评审；不得编造参考文献。
2. **引用编号与证据表不闭合。** 最终稿出现 `[1]`–`[5]`（[1]×5、[2]×4、[3]×5、[4]×4、[5]×2），
   但导出 `evidence` 只有 9 条，全部来自 3747（8 条）和 18622（1 条）；[2]、[4]、[5] 没有
   对应证据行。下一轮做参考文献/引用格式时必须同时打通 `materials → [n] → evidence → 文末来源`
   的完整映射，否则即使补了文末列表也无法审计。
3. **政策表述精度是第 2 轮指出的首要硬伤。** 它指出正文把弹性退休写成
   「符合条件可最多提前3年……；符合条件可最多提前3年，和单位协商一致也可最多延后3年退休」，
   前半句重复、后半句错位，属于会误导读者的关键政策错误；同时要求把「原55岁女干部」
   改为「原法定退休年龄为55周岁的女职工」。本地 `issues` 另记标题词面覆盖 44%、
   「打工人」偏口语、第二段偏长。这些是内容质量问题，需在权威素材到位后按评审意见定向修订；
   不能用放宽门禁或改盲评 prompt 解决。
4. `ttxt/config.py:10` 仍硬编码 `PROJECT_ROOT=Path("D:/ttxt")`。本轮烟测的零写入是
   **驱动器运行时打补丁**实现的，不是代码级保护；R63 已发生的生产误写仍在。R67 应做
   环境变量/隔离根覆盖与 worktree 写保护，避免下一次探针再次误写生产。
5. R65 遗留：挖词边界证据、`MINE_TOP_N=10` 不跨轮累积，另开一轮，不与写作修复混做。

## 六、证据文件

- 烟测回执：`03_intelligence/r66_smoke_receipt_one_anchor.json`
- 全量测试命令与读数：`D:/ttxt/07_tmp/r66_verify/full_tests.log`（运行时证据，未入库）
- 隔离烟测日志：`D:/ttxt/07_tmp/r66_smoke/09_logs/ttxt-20261121.jsonl`（未入库）
- 代码：`fa1318a`；检查点：`dd6d98e`、`b63662d`
