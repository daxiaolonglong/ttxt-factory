/* 头条智能工厂面板:五页签 + 常驻类目选择器。
 * 所有数字一律来自接口现算(每次切类目重新取),前端不写任何统计常量;
 * 写操作(入队采集 / 本地分析 / 补采 / 生成)先弹确认并显示目标类目,
 * 下发后按钮禁用显示"正在跑",靠轮询恢复;in-flight 返回 deduped 时按"已在跑"提示。
 */

const state = {
  category: "",
  tab: "overview",
  offset: 0,
  q: "",
  inflight: {}, // action -> { via: "client" | "server", since, note }
  logs: { scope: "category", seen: new Set(), count: 0, key: "" },
};

// 分页/抓取上限(是每次请求的条数参数,不是任何展示用的统计数字)
const PAGES = { articles: 20, jobs: 10, logs: 80, keep: 400 };

const $ = (id) => document.getElementById(id);
const fmt = (value) => (typeof value === "number" ? value.toLocaleString("zh-CN") : text(value));

async function getJSON(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status} ${url}`);
  return res.json();
}

async function postJSON(url, payload) {
  const res = await fetch(url, {
    method: "POST",
    cache: "no-store",
    headers: { "Content-Type": "application/json", "X-TTXT-Task": "1" },
    body: JSON.stringify(payload),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status} ${url}`);
  return data;
}

function clear(node) { while (node && node.firstChild) node.removeChild(node.firstChild); }
function text(value) { return value === null || value === undefined || value === "" ? "—" : String(value); }

function card(label, value, extra = "") {
  const div = document.createElement("div");
  div.className = "card";
  const l = document.createElement("div");
  l.className = "card-label";
  l.textContent = label;
  const v = document.createElement("div");
  v.className = "card-value";
  v.textContent = value;
  div.appendChild(l);
  div.appendChild(v);
  if (extra) {
    const e = document.createElement("div");
    e.className = "card-extra";
    e.textContent = extra;
    div.appendChild(e);
  }
  return div;
}

function cardRow(items, targetId) {
  const wrap = document.createElement("div");
  wrap.className = "cards";
  items.forEach((c) => wrap.appendChild(c));
  setContent(targetId, wrap);
}

function table(headers, rows) {
  const wrap = document.createElement("div");
  wrap.className = "table-wrap";
  const t = document.createElement("table");
  const thead = document.createElement("thead");
  const tr = document.createElement("tr");
  headers.forEach((h) => {
    const th = document.createElement("th");
    th.textContent = h;
    tr.appendChild(th);
  });
  thead.appendChild(tr);
  t.appendChild(thead);
  const tbody = document.createElement("tbody");
  if (!rows.length) {
    const emptyRow = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = headers.length;
    td.className = "empty";
    td.textContent = "暂无数据";
    emptyRow.appendChild(td);
    tbody.appendChild(emptyRow);
  } else {
    rows.forEach((row) => {
      const r = document.createElement("tr");
      row.forEach((cell) => {
        const td = document.createElement("td");
        td.textContent = text(cell);
        r.appendChild(td);
      });
      tbody.appendChild(r);
    });
  }
  t.appendChild(tbody);
  wrap.appendChild(t);
  return wrap;
}

function setContent(id, node) {
  const target = $(id);
  if (!target) return;
  clear(target);
  target.appendChild(node);
}

function setStatus(id, message, kind) {
  const node = $(id);
  if (!node) return;
  node.textContent = message;
  node.classList.toggle("error", kind === "error");
  node.classList.toggle("running", kind === "running");
  node.classList.toggle("notice", kind === "notice");
}

const base = () => `/api/category/${encodeURIComponent(state.category)}`;

// ---------------------------------------------------------------------------
// 写操作:确认弹层 + 单飞(按钮禁用 + 进行中)+ 轮询恢复

let confirmResolver = null;

function confirmAction(title, detail) {
  return new Promise((resolve) => {
    confirmResolver = resolve;
    $("confirm-title").textContent = title;
    $("confirm-category").textContent = state.category;
    $("confirm-detail").textContent = detail;
    $("confirm-ok").textContent = `确认对「${state.category}」执行`;
    $("confirm-modal").hidden = false;
    $("confirm-ok").focus();
  });
}

function closeConfirm(value) {
  if (!confirmResolver) return;
  const resolve = confirmResolver;
  confirmResolver = null;
  $("confirm-modal").hidden = true;
  resolve(value);
}

function isRunning(action) { return !!state.inflight[action]; }

function markRunning(action, via, note) {
  state.inflight[action] = { via, since: new Date().toISOString(), note: note || "" };
  syncButtons();
}

function markDone(action) {
  delete state.inflight[action];
  syncButtons();
}

function buttonState(id, action, idleLabel, busyLabel) {
  const btn = $(id);
  if (!btn) return;
  const busy = isRunning(action);
  btn.disabled = busy;
  btn.textContent = busy ? busyLabel : idleLabel;
}

function syncButtons() {
  buttonState("btn-enqueue-crawl", "crawl", "入队采集", "正在入队…");
  buttonState("btn-run-analyze", "analyze", "分析当前类目", "正在分析…");
  buttonState("btn-backfill", "metrics-backfill", "补采该类目(立即执行一批)", "正在跑 · 补采中…");
  buttonState("btn-write", "write", "生成草稿", "正在跑 · 生成中…");
}

function runningSuffix(action) {
  const info = state.inflight[action];
  if (!info) return "";
  return info.note ? ` (${info.note})` : "";
}

async function enqueueCrawl() {
  const statusId = "collect-status";
  const sourceId = $("dispatch-source").value;
  if (!sourceId) { setStatus(statusId, "请先选择采集来源", "error"); return; }
  const sourceCategory = $("dispatch-source").selectedOptions[0].dataset.category || state.category;
  const ok = await confirmAction(
    "入队采集",
    `来源 ${sourceId} 将入队到类目「${sourceCategory}」;只入队,真实抓取由 ttxt crawl-queue --run 按调度执行。`
  );
  if (!ok) return;
  markRunning("crawl", "client");
  setStatus(statusId, `正在入队…${runningSuffix("crawl")}`, "running");
  try {
    const res = await postJSON("/api/tasks/crawl", { source_id: sourceId, category: state.category });
    if (res.deduped) {
      setStatus(statusId, `#${res.job_id} 已在跑:${res.note}`, "notice");
    } else {
      setStatus(statusId, `#${res.job_id} → ${res.category}:${res.note}`);
    }
    await loadTab.overview();
  } catch (err) {
    setStatus(statusId, `入队失败:${err.message}`, "error");
  } finally {
    markDone("crawl");
  }
}

async function runAnalyze() {
  const statusId = "collect-status";
  const ok = await confirmAction(
    "本地分析当前类目",
    "分析只做本地计算(词表覆盖、爆款分层、知识资产),关键词反哺会自动入队采集任务;不会触发写作、发布或模型调用。"
  );
  if (!ok) return;
  markRunning("analyze", "client");
  setStatus(statusId, `正在分析「${state.category}」…`, "running");
  try {
    const res = await postJSON("/api/tasks/analyze", { category: state.category });
    setStatus(
      statusId,
      `分析完成:关键词 ${fmt(res.keywords)} · 零覆盖 ${fmt(res.zero_coverage)} · 缺口 ${fmt(res.feedback_gaps)}` +
      (res.feedback_job ? ` · 反哺入队 #${res.feedback_job}` : "")
    );
    await loadTab.collect();
  } catch (err) {
    setStatus(statusId, `分析失败:${err.message}`, "error");
  } finally {
    markDone("analyze");
  }
}

async function runBackfill() {
  const statusId = "metrics-status";
  const ok = await confirmAction(
    "补采公开指标",
    "对目标类目立即执行一批补采:走既有白名单前置、下架台账、infra 冷却与 attempts 退避;同步执行,一批跑完才返回。"
  );
  if (!ok) return;
  markRunning("metrics-backfill", "client");
  setStatus(statusId, `正在跑:「${state.category}」补采进行中,队列读数会随进度变化…`, "running");
  try {
    const res = await postJSON("/api/tasks/metrics-backfill", { category: state.category, confirm: state.category });
    if (res.deduped) {
      const who = res.running && res.running.started_at ? `起始 ${res.running.started_at}` : "";
      setStatus(statusId, `已在跑,未重复启动${who ? `:${who}` : ""}`, "notice");
    } else {
      // 只渲染接口真实回传的字段:run_due 的回执里 collection 是 {candidates, fetched, written, ...},
      // skipped 只可能是 None 或 "infra_paused"(整条链路冷却),绝不能当计数用。
      const parts = [`认领 ${fmt(res.claimed ?? 0)}`];
      const col = res.collection || null;
      if (col) {
        parts.push(`取回 ${fmt(col.fetched ?? 0)}/${fmt(col.candidates ?? 0)}`);
        parts.push(`写入 ${fmt(col.written ?? 0)}`);
        if (col.fetch_errors) parts.push(`抓取失败 ${fmt(col.fetch_errors)}`);
        if (col.robots_refused) parts.push(`robots 拒访 ${fmt(col.robots_refused)}`);
        if (col.known_gone_skipped) parts.push(`下架跳过 ${fmt(col.known_gone_skipped)}`);
      }
      if (res.unsupported_host_skipped) parts.push(`白名单外跳过 ${fmt(res.unsupported_host_skipped)}`);
      if (res.windows_refreshed) parts.push(`窗口刷新 ${fmt(res.windows_refreshed)}`);
      if (res.skipped === "infra_paused") {
        setStatus(statusId, "补采未执行:指标链路在 infra 冷却窗口内(上一条回执是基础设施故障),一个请求都不发", "notice");
      } else if (res.infra_failures) {
        setStatus(statusId, `补采完成:${parts.join(" · ")};本批 ${fmt(res.infra_failures)} 次基础设施故障,链路进入冷却窗口`, "notice");
      } else {
        setStatus(statusId, `补采完成:${parts.join(" · ")}`);
      }
    }
  } catch (err) {
    setStatus(statusId, `补采失败:${err.message}`, "error");
  } finally {
    markDone("metrics-backfill");
    await loadTab.metrics();
  }
}

async function runWrite() {
  const statusId = "writing-status";
  const topic = $("write-topic").value.trim();
  if (!topic) { setStatus(statusId, "请先填写写作主题", "error"); return; }
  const ok = await confirmAction(
    "生成草稿",
    `主题「${topic}」:模型调用经 model_gate 串行队列,同类目同一时刻只允许一次生成;产出只落草稿与评审,不会发布。`
  );
  if (!ok) return;
  markRunning("write", "client");
  setStatus(statusId, `正在跑:「${state.category}」生成已下发…`, "running");
  try {
    const res = await postJSON("/api/tasks/write", { category: state.category, topic, confirm: state.category });
    if (res.deduped) {
      const who = res.running && res.running.started_at ? `起始 ${res.running.started_at}` : "";
      setStatus(statusId, `已在跑,未重复启动${who ? `:${who}` : ""}`, "notice");
      markRunning("write", "server");
    } else {
      setStatus(statusId, `已下发 pid ${res.pid},串行队列执行中…`, "running");
    }
  } catch (err) {
    setStatus(statusId, `下发失败:${err.message}`, "error");
    markDone("write");
  }
}

// ---------------------------------------------------------------------------
// 只读读数

async function loadCategories() {
  const cats = await getJSON("/api/categories");
  const select = $("category");
  clear(select);
  cats.forEach((c) => {
    const opt = document.createElement("option");
    opt.value = c.slug;
    opt.textContent = c.demo ? `${c.slug}(演示 · ${fmt(c.articles)} 篇)` : `${c.slug}(${fmt(c.articles)} 篇)`;
    select.appendChild(opt);
  });
  const remembered = state.category && cats.some((c) => c.slug === state.category) ? state.category : "";
  const preferred = remembered || (cats.find((c) => c.demo) || cats[0] || {}).slug || "";
  state.category = preferred;
  if (state.category) select.value = state.category;
}

async function loadDispatchSources() {
  const select = $("dispatch-source");
  if (!select) return;
  const list = await getJSON("/api/sources");
  const previous = select.value;
  clear(select);
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "选择来源…";
  select.appendChild(placeholder);
  let matched = 0;
  list.forEach((s) => {
    if (!s.enabled || s.category !== state.category) return;
    matched += 1;
    const opt = document.createElement("option");
    opt.value = s.id;
    opt.dataset.category = s.category;
    opt.textContent = `${s.id} → ${s.category}${s.demo ? "(演示)" : ""} · ${s.name}`;
    select.appendChild(opt);
  });
  if (!matched) placeholder.textContent = "该类目无启用来源";
  else if (previous && [...select.options].some((o) => o.value === previous)) select.value = previous;
}

function renderOverview(data, categories) {
  const health = data.strata || {};
  const jobs = data.crawl_jobs || {};
  const model = data.model_jobs || {};
  cardRow(
    [
      card("类目", fmt(categories.length)),
      card("文章总数", fmt(categories.reduce((sum, c) => sum + (c.articles || 0), 0))),
      card("采集任务", fmt(Object.values(jobs).reduce((a, b) => a + b, 0)), JSON.stringify(jobs)),
      card("模型任务", fmt(Object.values(model).reduce((a, b) => a + b, 0)), JSON.stringify(model)),
      card(
        "模型服务",
        health.loaded ? "已加载" : "未就绪",
        health.loaded ? `${health.model} · 上下文 ${health.max_context}` : "等待模型"
      ),
      card("项目磁盘", `${data.disk.project_total_gb} / ${data.disk.budget_gb} GB`, `D: 空闲 ${data.disk.free_gb} GB`),
    ],
    "overview-cards"
  );
  $("schema").textContent = `schema ${data.schema_version}`;
}

function renderCategoryJobs(jobs) {
  setContent(
    "crawl-jobs",
    table(
      ["ID", "来源", "类目", "状态", "尝试", "错误", "创建", "完成"],
      (jobs.crawl || []).map((j) => [j.id, j.source, j.category, j.status, j.attempts, j.last_error, j.created_at, j.finished_at])
    )
  );
  setContent(
    "model-jobs",
    table(
      ["ID", "类型", "类目", "状态", "尝试", "错误", "开始", "结束"],
      (jobs.model || []).map((j) => [j.id, j.job_type, j.category, j.status, j.attempts, j.last_error, j.started_at, j.finished_at])
    )
  );
  setContent(
    "attempts-table",
    table(
      ["文章 ID", "队列状态", "尝试", "上次结果", "上次错误", "上次尝试", "下次尝试"],
      (jobs.metrics_attempts || []).map((a) => [
        a.article_id, a.state, a.attempts, a.last_outcome, a.last_error, a.last_attempt_at, a.next_attempt_at,
      ])
    )
  );
}

function renderTaskQueue(data) {
  const pending = (data.pending || []).map((j) => [
    j.id, j.source, j.category, j.status, `${j.attempts}/${j.max_attempts}`, j.created_at,
  ]);
  const sched = data.schedule || {};
  const jobs = (sched.jobs || []).map((j) => [
    j.id, j.kind, `${j.interval_min} 分钟`, j.enabled ? "启用" : "停用",
    j.due ? "是" : "否", j.last_run_at || "未运行", j.last_status || "—",
  ]);
  const wrap = document.createElement("div");
  wrap.appendChild(table(["任务 id", "来源", "类目", "状态", "重试", "入队时间"], pending));
  const note = document.createElement("p");
  note.className = "muted";
  note.textContent = `调度器:${sched.enabled ? "启用" : "停用"} · 执行入口 ttxt schedule --once / crawl-queue --run(面板只入队)`;
  wrap.appendChild(note);
  if (sched.jobs) {
    wrap.appendChild(table(["计划", "类型", "周期", "状态", "到期", "上次运行", "上次结果"], jobs));
  }
  setContent("task-queue-table", wrap);
}

function renderDisk(overview) {
  setContent(
    "disk-table",
    table(
      ["指标", "值"],
      [
        ["项目占用(GB)", overview.disk.project_total_gb],
        ["预算(GB)", overview.disk.budget_gb],
        ["是否超预算", overview.disk.within_budget ? "否" : "是"],
        ["D: 空闲(GB)", overview.disk.free_gb],
        ["schema 版本", overview.schema_version],
      ]
    )
  );
}

function renderArticles(data) {
  $("articles-summary").textContent = `共 ${fmt(data.total)} 篇`;
  const wrap = document.createElement("div");
  wrap.appendChild(
    table(
      ["ID", "标题", "来源", "发布时间", "字数", "聚类"],
      (data.items || []).map((a) => [a.id, a.title, a.source, a.published_at, a.text_length, a.cluster_id])
    )
  );
  const pager = document.createElement("div");
  pager.className = "pager";
  const prev = document.createElement("button");
  prev.type = "button";
  prev.textContent = "上一页";
  prev.disabled = state.offset <= 0;
  prev.addEventListener("click", () => {
    state.offset = Math.max(0, state.offset - PAGES.articles);
    loadTab.collect();
  });
  const next = document.createElement("button");
  next.type = "button";
  next.textContent = "下一页";
  const shown = (data.items || []).length;
  next.disabled = shown === 0 || state.offset + shown >= data.total;
  next.addEventListener("click", () => {
    state.offset += PAGES.articles;
    loadTab.collect();
  });
  const pos = document.createElement("span");
  pos.className = "muted";
  pos.textContent = `第 ${fmt(state.offset + 1)}–${fmt(state.offset + shown)} 篇 / ${fmt(data.total)} 篇`;
  pager.appendChild(prev);
  pager.appendChild(next);
  pager.appendChild(pos);
  wrap.appendChild(pager);
  setContent("articles-table", wrap);
}

function renderKeywords(list) {
  setContent(
    "keywords-table",
    table(
      ["关键词", "来源", "状态", "采集数", "优质数", "平均字数", "评分"],
      list.map((k) => [k.keyword, k.origin, k.status, k.collected, k.quality_articles, k.avg_text_length, k.score])
    )
  );
  const gaps = list.filter((k) => k.collected === 0);
  setContent(
    "keyword-gap",
    table(
      ["关键词", "来源", "状态", "说明"],
      gaps.map((k) => [k.keyword, k.origin, k.status, "零覆盖,应进入下一轮采集"])
    )
  );
}

function renderMetrics(data) {
  const queue = data.queue || {};
  cardRow(
    [
      card("文章", fmt(data.articles), data.exists ? data.category : "该类目无本地库"),
      card("可量文章(白名单主机)", fmt(data.measurable_articles), "补采真正能取到读数的部分"),
      card("待补采", fmt(data.pending_backfill), JSON.stringify(queue)),
      card("已有指标读数", fmt(data.with_metrics_snapshot), "article_metrics 去重文章数"),
      card("最近 24 小时入库", fmt(data.articles_24h)),
    ],
    "metrics-cards"
  );
  setContent("queue-table", table(["队列状态", "数量"], Object.entries(queue).map(([k, v]) => [k, v])));
  const outcomes = Object.entries(data.failure_outcomes || {}).map(([k, v]) => [k, v]);
  setContent("failure-table", table(["失败归类", "数量"], outcomes));
}

function renderAnalysis(data) {
  const tiers = data.viral_tiers || {};
  const labels = data.viral_labels || {};
  cardRow(
    [
      card("文章", fmt(data.articles)),
      card("平均字数", fmt(data.avg_text_length)),
      card("相似聚类", fmt(data.clusters)),
      card("缺数据未评分", fmt(tiers.missing_data || 0)),
      card("已验证爆款", fmt(labels.verified_viral || 0)),
      card("潜力文章", fmt(labels.potential || 0)),
    ],
    "analysis-cards"
  );
  setContent(
    "viral-table",
    table(["分层", "数量"], Object.entries(tiers).map(([tier, count]) => [tier, count]))
  );
  setContent(
    "assets-table",
    table(
      ["类型", "陈述", "支持数", "置信度"],
      (data.knowledge_assets || []).map((a) => [a.kind, a.statement, a.support_count, a.confidence])
    )
  );
}

function renderWriting(data) {
  const status = data.status || {};
  const panel = document.createElement("div");
  panel.className = "run-line";
  const badge = document.createElement("span");
  badge.className = status.running ? "pill running" : "pill idle";
  badge.textContent = status.running ? "正在跑" : "空闲";
  panel.appendChild(badge);
  const detail = document.createElement("span");
  detail.className = "muted";
  detail.textContent = status.running
    ? `pid ${status.pid} · 起始 ${text(status.started_at)} · 已耗时 ${text(status.elapsed_s)} 秒`
    : `上次生成 ${text(status.started_at)}`;
  panel.appendChild(detail);
  setContent("write-run", panel);
  $("write-log").textContent = status.log_tail || "（无日志）";

  setContent(
    "drafts-table",
    table(
      ["草稿 ID", "版本", "主题", "字数", "生成时间"],
      (data.drafts || []).map((d) => [d.draft_id, d.version, d.topic, d.chars, d.created_at])
    )
  );
  setContent(
    "evaluations-table",
    table(
      ["草稿 ID", "版本", "评分", "评审模型", "问题", "时间"],
      (data.evaluations || []).map((e) => [
        e.draft_id, e.version, e.score, e.evaluator,
        (e.issues || []).map((issue) => (typeof issue === "string" ? issue : JSON.stringify(issue))).join(" / "),
        e.created_at,
      ])
    )
  );

  // 服务端在跑 ⇒ 客户端必须认为"正在跑"(刷新页面也能恢复),靠轮询恢复按钮
  if (status.running) {
    if (!isRunning("write")) markRunning("write", "server");
    setStatus("writing-status", `正在跑:${detail.textContent}`, "running");
  } else if (isRunning("write")) {
    markDone("write");
    setStatus("writing-status", "生成已结束(空闲);草稿与评审已刷新");
  }
}

// ---------------------------------------------------------------------------
// 日志:增量拉取(不一次拉全量),按类目过滤,自动追加并滚到底

function logKey(record) {
  return `${record.ts}|${record.level}|${record.module}|${record.event}|${record.message}|${JSON.stringify(record.meta || "")}`;
}

function recordCategory(record) {
  const meta = record.meta || {};
  return meta.category || meta.slug || "";
}

function matchScope(record) {
  if (state.logs.scope === "all") return true;
  if (!state.category) return false;
  if (recordCategory(record) === state.category) return true;
  const haystack = `${record.module} ${record.event} ${record.message}`;
  return haystack.split(/[^0-9a-zA-Z_-]+/).includes(state.category);
}

function appendLogNodes(records) {
  const list = $("logs-list");
  records.forEach((record) => {
    const entry = document.createElement("div");
    entry.className = `log-entry ${record.level || "info"}`;
    const ts = document.createElement("span");
    ts.className = "log-ts";
    ts.textContent = record.ts || "";
    const level = document.createElement("span");
    level.className = "log-level";
    level.textContent = record.level || "";
    const msg = document.createElement("span");
    const meta = record.meta ? ` ${JSON.stringify(record.meta)}` : "";
    msg.textContent = `${record.module || ""} ${record.event || ""} ${record.message || ""}${meta}`;
    entry.appendChild(ts);
    entry.appendChild(level);
    entry.appendChild(msg);
    list.appendChild(entry);
  });
  while (list.childElementCount > PAGES.keep) list.removeChild(list.firstChild);
  list.scrollTop = list.scrollHeight;
}

async function pollLogs() {
  try {
    if (state.logs.key !== `${state.category}|${state.logs.scope}`) {
      state.logs.key = `${state.category}|${state.logs.scope}`;
      state.logs.seen = new Set();
      state.logs.count = 0;
      clear($("logs-list"));
    }
    const records = await getJSON(`/api/logs?limit=${PAGES.logs}`);
    const fresh = records.filter((r) => matchScope(r) && !state.logs.seen.has(logKey(r))).reverse();
    fresh.forEach((r) => state.logs.seen.add(logKey(r)));
    state.logs.count += fresh.length;
    if (fresh.length) appendLogNodes(fresh);
    $("logs-count").textContent =
      `本次会话新增 ${fmt(state.logs.count)} 条 · 页面保留 ${fmt($("logs-list").childElementCount)} 条 · ` +
      `最近抓取 ${fmt(records.length)} 条 · ${new Date().toLocaleTimeString()}`;
  } catch (err) {
    $("logs-count").textContent = `日志刷新失败:${err.message}`;
  }
}

// ---------------------------------------------------------------------------
// 每个页签各自的取数(切类目后所有数字重新取)

const loadTab = {
  async overview() {
    if (!state.category) return;
    const [overview, categories, jobs, tasks] = await Promise.all([
      getJSON("/api/overview"),
      getJSON("/api/categories"),
      getJSON(`${base()}/jobs?limit=${PAGES.jobs}`),
      getJSON(`/api/tasks?limit=${PAGES.jobs}`),
    ]);
    renderOverview(overview, categories);
    renderCategoryJobs(jobs);
    renderTaskQueue(tasks);
    renderDisk(overview);
  },
  async collect() {
    if (!state.category) return;
    const [articles, keywords] = await Promise.all([
      getJSON(`${base()}/articles?limit=${PAGES.articles}&offset=${state.offset}` +
        (state.q ? `&q=${encodeURIComponent(state.q)}` : "")),
      getJSON(`${base()}/keywords`),
    ]);
    renderArticles(articles);
    renderKeywords(keywords);
  },
  async metrics() {
    if (!state.category) return;
    const [metrics, jobs, analysis] = await Promise.all([
      getJSON(`${base()}/metrics`),
      getJSON(`${base()}/jobs?limit=${PAGES.jobs}`),
      getJSON(`${base()}/analysis`),
    ]);
    renderMetrics(metrics);
    renderCategoryJobs(jobs);
    renderAnalysis(analysis);
  },
  async writing() {
    if (!state.category) return;
    renderWriting(await getJSON(`${base()}/writing`));
  },
  async logs() { await pollLogs(); },
};

async function refreshCurrentTab() {
  try {
    await loadTab[state.tab]();
    $("refreshAt").textContent = `刷新 ${new Date().toLocaleTimeString()}`;
  } catch (err) {
    $("refreshAt").textContent = `加载失败:${err.message}`;
  }
}

function switchTab(tab) {
  state.tab = tab;
  document.querySelectorAll("nav#tabs button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  });
  document.querySelectorAll("main .tab").forEach((section) => {
    section.classList.toggle("active", section.id === `tab-${tab}`);
  });
  refreshCurrentTab();
}

async function onCategoryChange(event) {
  state.category = event.target.value;
  state.offset = 0;
  state.q = "";
  $("search-q").value = "";
  state.logs.seen = new Set();
  state.logs.count = 0;
  state.logs.key = "";
  const cats = await getJSON("/api/categories");
  const found = cats.find((c) => c.slug === state.category) || {};
  $("category-note").textContent = found.demo
    ? "演示命名空间"
    : `该类目 ${fmt(found.articles)} 篇(数字来自接口)`;
  await loadDispatchSources();
  await refreshCurrentTab();
}

async function onSearch(event) {
  event.preventDefault();
  state.offset = 0;
  state.q = $("search-q").value.trim();
  try {
    await loadTab.collect();
  } catch (err) {
    $("articles-summary").textContent = `检索失败:${err.message}`;
  }
}

async function tick() {
  if (state.tab === "logs") await pollLogs();
  if (isRunning("write")) { try { await loadTab.writing(); } catch (err) { console.error(err); } }
  if (isRunning("metrics-backfill")) { try { await loadTab.metrics(); } catch (err) { console.error(err); } }
}

async function init() {
  document.querySelectorAll("nav#tabs button").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });
  $("category").addEventListener("change", onCategoryChange);
  $("search-form").addEventListener("submit", onSearch);
  $("btn-enqueue-crawl").addEventListener("click", enqueueCrawl);
  $("btn-run-analyze").addEventListener("click", runAnalyze);
  $("btn-backfill").addEventListener("click", runBackfill);
  $("btn-write").addEventListener("click", runWrite);
  $("log-scope").addEventListener("change", (event) => {
    state.logs.scope = event.target.value;
    state.logs.seen = new Set();
    state.logs.count = 0;
    state.logs.key = "";
    pollLogs();
  });
  $("btn-logs-reset").addEventListener("click", () => {
    state.logs.seen = new Set();
    state.logs.count = 0;
    state.logs.key = "";
    pollLogs();
  });
  $("confirm-ok").addEventListener("click", () => closeConfirm(true));
  $("confirm-cancel").addEventListener("click", () => closeConfirm(false));
  $("confirm-modal").addEventListener("click", (event) => { if (event.target.id === "confirm-modal") closeConfirm(false); });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeConfirm(false); });

  try {
    await loadCategories();
    const cats = await getJSON("/api/categories");
    const found = cats.find((c) => c.slug === state.category) || {};
    $("category-note").textContent = found.demo
      ? "演示命名空间"
      : `该类目 ${fmt(found.articles)} 篇(数字来自接口)`;
    await loadDispatchSources();
    syncButtons();
    await refreshCurrentTab();
  } catch (err) {
    console.error(err);
    $("refreshAt").textContent = `初始化失败:${err.message}`;
    return;
  }
  // 单飞:上一轮还没回来就不叠加下一轮(补采期间读数可能被 SQLite 写事务挡住)
  let busyTick = false;
  setInterval(() => {
    if (busyTick) return;
    busyTick = true;
    Promise.resolve(tick()).finally(() => { busyTick = false; });
  }, 3000);
  let busyRefresh = false;
  setInterval(() => {
    if (busyRefresh || state.tab === "logs") return;
    busyRefresh = true;
    Promise.resolve(refreshCurrentTab()).finally(() => { busyRefresh = false; });
  }, 12000);
}

init();
