/* 头条智能工厂面板:全部数据来自真实 API,无模拟状态。 */

const state = { category: "", tab: "overview" };
const $ = (id) => document.getElementById(id);

async function getJSON(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status} ${url}`);
  return res.json();
}

function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
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

async function loadCategories() {
  const cats = await getJSON("/api/categories");
  const select = $("category");
  clear(select);
  cats.forEach((c) => {
    const opt = document.createElement("option");
    opt.value = c.slug;
    opt.textContent = c.demo ? `${c.slug}(演示 · ${c.articles} 篇)` : `${c.slug}(${c.articles} 篇)`;
    select.appendChild(opt);
  });
  const preferred = cats.find((c) => c.demo) || cats[0];
  state.category = preferred ? preferred.slug : "";
  if (state.category) select.value = state.category;
}

function renderOverview(data) {
  const total = data.categories.reduce((sum, c) => sum + (c.articles || 0), 0);
  const health = data.strata || {};
  const cards = [
    card("类目", data.categories.length),
    card("文章总数", total),
    card("采集任务", Object.values(data.crawl_jobs || {}).reduce((a, b) => a + b, 0), JSON.stringify(data.crawl_jobs || {})),
    card("模型任务", Object.values(data.model_jobs || {}).reduce((a, b) => a + b, 0), JSON.stringify(data.model_jobs || {})),
    card(
      "Strata 状态",
      health.loaded ? "已加载" : "未就绪",
      health.loaded ? `${health.model} · 上下文 ${health.max_context}` : "等待模型"
    ),
    card("项目磁盘", `${data.disk.project_total_gb} / ${data.disk.budget_gb} GB`, `D: 空闲 ${data.disk.free_gb} GB`),
  ];
  const wrap = document.createElement("div");
  wrap.className = "cards";
  cards.forEach((c) => wrap.appendChild(c));
  setContent("overview-cards", wrap);
  $("schema").textContent = `schema ${data.schema_version}`;
}

function renderJobs(jobs) {
  setContent(
    "crawl-jobs",
    table(
      ["ID", "来源", "类目", "状态", "尝试", "错误", "完成时间"],
      (jobs.crawl || []).map((j) => [j.id, j.source, j.category, j.status, j.attempts, j.last_error, j.finished_at])
    )
  );
  setContent(
    "model-jobs",
    table(
      ["ID", "类型", "类目", "状态", "尝试", "错误", "开始", "结束"],
      (jobs.model || []).map((j) => [j.id, j.job_type, j.category, j.status, j.attempts, j.last_error, j.started_at, j.finished_at])
    )
  );
}

function renderArticles(data) {
  $("articles-summary").textContent = `共 ${data.total} 篇`;
  setContent(
    "articles-table",
    table(
      ["ID", "标题", "来源", "发布时间", "字数", "聚类"],
      (data.items || []).map((a) => [a.id, a.title, a.source, a.published_at, a.text_length, a.cluster_id])
    )
  );
}

function renderKeywords(list) {
  setContent(
    "keywords-table",
    table(
      ["关键词", "来源", "采集数", "优质数", "平均字数", "评分", "状态"],
      list.map((k) => [
        k.keyword,
        k.origin,
        k.collected,
        k.quality_articles,
        k.avg_text_length,
        k.score,
        k.collected === 0 ? "待采集(反哺候选)" : "已覆盖",
      ])
    )
  );
}

function renderAnalysis(data) {
  const tiers = data.viral_tiers || {};
  const cards = document.createElement("div");
  cards.className = "cards";
  [
    card("文章", data.articles),
    card("平均字数", data.avg_text_length),
    card("相似聚类", data.clusters),
    card("未评分(缺数据)", tiers.missing_data || 0),
    card("已验证爆款", (data.viral_labels || {}).verified_viral || 0),
    card("潜力文章", (data.viral_labels || {}).potential || 0),
  ].forEach((c) => cards.appendChild(c));
  setContent("analysis-cards", cards);
  setContent(
    "viral-table",
    table(
      ["标签", "置信度", "数量"],
      Object.entries(tiers).map(([tier, count]) => ["—", tier, count])
    )
  );
  setContent(
    "assets-table",
    table(
      ["类型", "陈述", "支持数", "置信度"],
      (data.knowledge_assets || []).map((a) => [a.kind, a.statement, a.support_count, a.confidence])
    )
  );
  setContent(
    "drafts-table",
    table(
      ["草稿ID", "版本", "主题", "字数", "生成时间"],
      (data.drafts || []).map((d) => [d.draft_id, d.version, d.topic, d.chars, d.created_at])
    )
  );
  setContent(
    "evaluations-table",
    table(
      ["草稿ID", "版本", "评分", "问题", "时间"],
      (data.evaluations || []).map((e) => [e.draft_id, e.version, e.score, e.issues_json, e.created_at])
    )
  );
}

function renderKnowledge(keywords, data) {
  setContent(
    "knowledge-table",
    table(
      ["类型", "陈述", "支持数", "置信度"],
      (data.knowledge_assets || []).map((a) => [a.kind, a.statement, a.support_count, a.confidence])
    )
  );
  const gaps = keywords.filter((k) => k.collected === 0);
  setContent(
    "keyword-gap",
    table(
      ["关键词", "状态", "说明"],
      gaps.map((k) => [k.keyword, k.status, "零覆盖,应进入下一轮采集"])
    )
  );
}

function renderSystem(overview, model) {
  const cards = document.createElement("div");
  cards.className = "cards";
  const health = model.health || {};
  const queue = model.queue || {};
  [
    card("项目占用", `${overview.disk.project_total_gb} GB`, `预算 ${overview.disk.budget_gb} GB`),
    card("D: 空闲", `${overview.disk.free_gb} GB`),
    card("Strata", health.loaded ? "运行中" : "未就绪", health.model || ""),
    card("队列任务", Object.values(queue).reduce((a, b) => a + b, 0), JSON.stringify(queue)),
  ].forEach((c) => cards.appendChild(c));
  setContent("system-cards", cards);
  setContent(
    "queue-table",
    table(["状态", "数量"], Object.entries(queue).map(([status, count]) => [status, count]))
  );
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

function renderLogs(records) {
  const list = $("logs-list");
  clear(list);
  if (!records.length) {
    const empty = document.createElement("div");
    empty.className = "log-entry";
    empty.textContent = "暂无日志";
    list.appendChild(empty);
    return;
  }
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
    msg.textContent = `${record.module || ""} ${record.event || ""} ${record.message || ""}`;
    entry.appendChild(ts);
    entry.appendChild(level);
    entry.appendChild(msg);
    list.appendChild(entry);
  });
}

async function refreshLogs() {
  try {
    renderLogs(await getJSON("/api/logs?limit=80"));
  } catch (err) {
    console.error(err);
  }
}

async function refreshAll() {
  try {
    const [overview, jobs, model] = await Promise.all([
      getJSON("/api/overview"),
      getJSON("/api/jobs?limit=10"),
      getJSON("/api/model"),
    ]);
    renderOverview(overview);
    renderJobs(jobs);
    renderSystem(overview, model);
    if (state.category) {
      const base = `/api/category/${encodeURIComponent(state.category)}`;
      const [articles, keywords, analysis] = await Promise.all([
        getJSON(`${base}/articles?limit=20`),
        getJSON(`${base}/keywords`),
        getJSON(`${base}/analysis`),
      ]);
      renderArticles(articles);
      renderKeywords(keywords);
      renderAnalysis(analysis);
      renderKnowledge(keywords, analysis);
    }
  } catch (err) {
    console.error(err);
    $("refreshAt").textContent = `加载失败:${err.message}`;
    return;
  }
  $("refreshAt").textContent = `刷新 ${new Date().toLocaleTimeString()}`;
}

function switchTab(tab) {
  state.tab = tab;
  document.querySelectorAll("nav#tabs button").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.tab === tab);
  });
  document.querySelectorAll("main .tab").forEach((section) => {
    section.classList.toggle("active", section.id === `tab-${tab}`);
  });
  if (tab === "logs") refreshLogs();
}

function onCategoryChange(event) {
  state.category = event.target.value;
  ["articles-table", "keywords-table", "analysis-cards", "viral-table", "assets-table",
   "drafts-table", "evaluations-table", "knowledge-table", "keyword-gap"].forEach((id) => {
    const node = $(id);
    if (node) clear(node);
  });
  refreshAll();
}

async function onSearch(event) {
  event.preventDefault();
  if (!state.category) return;
  const q = $("search-q").value.trim();
  try {
    const base = `/api/category/${encodeURIComponent(state.category)}/articles?limit=20`;
    const data = await getJSON(q ? `${base}&q=${encodeURIComponent(q)}` : base);
    renderArticles(data);
  } catch (err) {
    console.error(err);
  }
}

async function init() {
  document.querySelectorAll("nav#tabs button").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });
  $("category").addEventListener("change", onCategoryChange);
  $("search-form").addEventListener("submit", onSearch);
  try {
    await loadCategories();
    await refreshAll();
    await refreshLogs();
  } catch (err) {
    console.error(err);
    $("refreshAt").textContent = `初始化失败:${err.message}`;
  }
  setInterval(() => {
    if (state.tab === "logs") refreshLogs();
  }, 3000);
  setInterval(() => {
    if (state.tab !== "logs") refreshAll();
  }, 10000);
}

init();
