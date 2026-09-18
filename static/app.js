const $ = (selector) => document.querySelector(selector);
const results = $("#results");
const notice = $("#notice");
const dialog = $("#add-dialog");
const analysisDialog = $("#analysis-dialog");

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function safeUrl(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}

function showNotice(message, isError = false) {
  if (!message) {
    notice.classList.add("hidden");
    return;
  }
  notice.textContent = message;
  notice.style.borderLeftColor = isError ? "#a33c2f" : "#8b5b14";
  notice.classList.remove("hidden");
}

async function request(url, options = {}) {
  const response = await fetch(url, options);
  let payload;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const detail = payload?.detail;
    const message = typeof detail === "string" ? detail : JSON.stringify(detail || payload || response.statusText);
    throw new Error(message);
  }
  return payload;
}

function stateOptions(current) {
  const values = [
    ["unread", "未读"], ["queued", "待读"], ["reading", "阅读中"],
    ["read", "已读"], ["archived", "归档"],
  ];
  return values.map(([value, label]) => `<option value="${value}" ${current === value ? "selected" : ""}>${label}</option>`).join("");
}

function renderHit(hit) {
  const work = hit.work;
  const authors = work.authors.join(", ");
  const yearVenue = [work.publication_year, work.venue].filter(Boolean).join(" · ");
  const tags = work.tags.map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("");
  const doc = work.documents.find((item) => item.parse_status !== "failed") || work.documents[0];
  const pdf = doc ? `<a class="pdf-link" target="_blank" href="/api/v1/documents/${doc.id}/file">打开 PDF</a>` : "";
  const ranks = [
    hit.lexical_rank ? `词 #${hit.lexical_rank}` : "",
    hit.semantic_rank ? `向量 #${hit.semantic_rank}` : "",
  ].filter(Boolean).join(" · ");
  return `
    <article class="result-card" data-work-id="${work.id}">
      <div class="result-head">
        <div>
          <h3>${escapeHtml(work.title)}</h3>
          <p class="meta">${escapeHtml(authors)}${authors && yearVenue ? " · " : ""}${escapeHtml(yearVenue)}</p>
        </div>
        <span class="rank">${escapeHtml(ranks)}</span>
      </div>
      <div class="tags">${tags}</div>
      <p class="snippet">${escapeHtml(hit.snippet || work.abstract || "暂无摘要")}</p>
      <p class="meta">${escapeHtml(hit.matched_section_title || "")}${hit.page_start ? ` · 第 ${hit.page_start} 页` : ""} · ${work.embedded_section_count}/${work.section_count} 段已向量化</p>
      <div class="state-row">
        <label>状态<select class="reading-status">${stateOptions(work.state.reading_status)}</select></label>
        <label>重要度<input class="importance" type="number" min="0" max="5" value="${work.state.importance}" /></label>
        <label>熟悉度<input class="familiarity" type="number" min="0" max="5" value="${work.state.familiarity}" /></label>
        <button class="small-button save-state">保存状态</button>
        <button class="small-button extract-work">LLM 提取</button>
        <button class="small-button view-analysis">查看提取</button>
        ${pdf}
      </div>
    </article>`;
}

async function runSearch() {
  results.innerHTML = '<div class="empty">正在检索……</div>';
  showNotice("");
  const params = new URLSearchParams({
    q: $("#query").value,
    mode: $("#mode").value,
    limit: "30",
  });
  const optional = {
    year_from: $("#year-from").value,
    year_to: $("#year-to").value,
    tag: $("#tag").value,
    min_importance: $("#min-importance").value,
    max_familiarity: $("#max-familiarity").value,
  };
  Object.entries(optional).forEach(([key, value]) => value && params.set(key, value));
  try {
    const data = await request(`/api/v1/search?${params}`);
    if (data.warnings.length) showNotice(data.warnings.join("\n"));
    results.innerHTML = data.hits.length
      ? data.hits.map(renderHit).join("")
      : '<div class="empty">没有找到结果。可以换个词，或先添加一篇资料。</div>';
  } catch (error) {
    results.innerHTML = '<div class="empty">检索失败</div>';
    showNotice(error.message, true);
  }
}

results.addEventListener("click", async (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  const card = button.closest(".result-card");
  if (!card) return;
  if (button.classList.contains("extract-work")) {
    button.disabled = true;
    button.textContent = "正在提取，请稍候……";
    showNotice("正在调用模型。云端模型会接收该论文的文本，请不要关闭页面。");
    try {
      const data = await request(`/api/v1/works/${card.dataset.workId}/extract`, { method: "POST" });
      renderAnalysis(data);
      analysisDialog.showModal();
      showNotice(data.warnings.length ? data.warnings.join("；") : "结构化提取完成，分类结果与标签已自动采用。");
    } catch (error) {
      showNotice(error.message, true);
    } finally {
      button.disabled = false;
      button.textContent = "LLM 提取";
    }
    return;
  }
  if (button.classList.contains("view-analysis")) {
    button.disabled = true;
    try {
      const data = await request(`/api/v1/works/${card.dataset.workId}/analysis`);
      renderAnalysis(data);
      analysisDialog.showModal();
    } catch (error) {
      showNotice(error.message, true);
    } finally {
      button.disabled = false;
    }
    return;
  }
  if (!button.classList.contains("save-state")) return;
  button.disabled = true;
  try {
    await request(`/api/v1/works/${card.dataset.workId}/state`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        reading_status: card.querySelector(".reading-status").value,
        importance: Number(card.querySelector(".importance").value),
        familiarity: Number(card.querySelector(".familiarity").value),
      }),
    });
    button.textContent = "已保存";
    setTimeout(() => { button.textContent = "保存状态"; }, 1200);
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    button.disabled = false;
  }
});

$("#search").addEventListener("click", runSearch);
$("#query").addEventListener("keydown", (event) => {
  if (event.key === "Enter") runSearch();
});
$("#open-add").addEventListener("click", () => dialog.showModal());
$("#close-add").addEventListener("click", () => dialog.close());
$("#close-analysis").addEventListener("click", () => analysisDialog.close());

$("#add-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#submit-add");
  button.disabled = true;
  button.textContent = "正在解析与建立索引……";
  showNotice("");
  try {
    const data = await request("/api/v1/works/upload", {
      method: "POST",
      body: new FormData(event.target),
    });
    event.target.reset();
    dialog.close();
    if (data.warnings.length) showNotice(`已保存，但有提示：${data.warnings.join("；")}`);
    else showNotice("资料已保存并完成索引。重要度和熟悉度可以随时修改。");
    await runSearch();
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "保存并索引";
  }
});

const factLabels = {
  summary: "摘要",
  research_goal: "研究目标 / 问题",
  contribution: "主要贡献",
  terminology: "术语与定义",
  task: "研究任务",
  method: "方法流程",
  algorithm: "算法",
  architecture: "模型 / 系统架构",
  dataset: "数据集 / 语料",
  benchmark: "基准任务 / 套件",
  model: "模型",
  software: "软件 / 框架 / 库",
  code_repository: "代码仓库 / 实现资源",
  hardware: "硬件设备",
  compute_environment: "算力与运行环境",
  baseline: "对比基线",
  metric: "评测指标",
  hyperparameter: "超参数",
  training_setup: "训练配置",
  experimental_setup: "实验设置",
  evaluation_protocol: "评测协议",
  statistical_test: "统计检验",
  experimental_result: "实验结果",
  ablation: "消融实验",
  engineering_trick: "工程技巧 / 实现细节",
  limitation: "局限与未解决问题",
  future_work: "未来工作",
  ethical_consideration: "伦理、安全与社会影响",
  tag: "自动标签",
  glossary: "术语（历史数据）",
  resource: "资源（历史数据）",
  finding: "实验发现（历史数据）",
  suggested_tag: "建议标签（历史数据）",
};

const valueLabels = {
  text: "内容",
  subtype: "子类",
  name: "名称",
  description: "说明",
  page: "页码",
  attributes: "类别专属属性",
  category_label: "分类",
};

function formatFactValue(value) {
  return Object.entries(value || {})
    .filter(([, item]) => item !== null && item !== "" && !(typeof item === "object" && !Object.keys(item).length))
    .map(([key, item]) => {
      const label = valueLabels[key] || key;
      if (key === "page") return `${label}: 第 ${item} 页`;
      if (key === "attributes" && typeof item === "object") {
        const details = Object.entries(item).map(([attribute, detail]) => `  ${attribute}: ${typeof detail === "object" ? JSON.stringify(detail) : detail}`).join("\n");
        return `${label}:\n${details}`;
      }
      return `${label}: ${typeof item === "object" ? JSON.stringify(item) : item}`;
    })
    .join("\n");
}

function confidenceLabel(value) {
  if (value === null || value === undefined) return "置信度未提供";
  return `置信度 ${Math.round(value * 100)}%`;
}

function renderAnalysis(data) {
  $("#analysis-title").textContent = data.work_title;
  const container = $("#analysis-results");
  if (!data.facts.length) {
    container.innerHTML = '<div class="empty">尚无提取结果。关闭窗口后点击“LLM 提取”。</div>';
    return;
  }
  container.innerHTML = data.facts.map((fact) => `
    <article class="fact-card" data-fact-id="${fact.id}">
      <div class="fact-head">
        <span class="fact-type">${escapeHtml(factLabels[fact.fact_type] || fact.fact_type)}</span>
        <span class="review-status">自动采用 · ${confidenceLabel(fact.confidence)}</span>
      </div>
      <div class="fact-value">${escapeHtml(formatFactValue(fact.value))}</div>
      ${fact.evidence_text ? `<blockquote class="evidence">证据：${escapeHtml(fact.evidence_text)}</blockquote>` : ""}
    </article>
  `).join("");
}

function setView(viewName) {
  document.querySelectorAll(".view").forEach((view) => view.classList.remove("active"));
  document.querySelectorAll(".tab-button").forEach((button) => button.classList.toggle("active", button.dataset.view === viewName));
  $(`#view-${viewName}`).classList.add("active");
  $("#open-add").classList.toggle("hidden", viewName !== "library");
  showNotice("");
  if (viewName === "llm") loadLLMSettings();
  if (viewName === "zotero") loadZoteroSettings(true);
  if (viewName === "discover") {
    loadS2Settings();
    loadSeedWorks();
  }
}

document.querySelectorAll(".tab-button").forEach((button) => {
  button.addEventListener("click", () => setView(button.dataset.view));
});

function applyProviderPreset(force = false) {
  const provider = $("#llm-provider").value;
  const base = $("#llm-base-url");
  const model = $("#llm-model");
  if (provider === "ollama") {
    if (force || !base.value) base.value = "http://host.docker.internal:11434/v1";
    if (force || !model.value) model.value = "qwen3:8b";
  } else {
    if (force || !base.value) base.value = "https://openrouter.ai/api/v1";
    if (force || !model.value) model.value = "qwen/qwen3.5-9b";
  }
}

$("#llm-provider").addEventListener("change", () => applyProviderPreset(true));

async function loadLLMSettings() {
  try {
    const config = await request("/api/v1/llm/settings");
    $("#llm-enabled").checked = config.enabled;
    $("#llm-provider").value = config.provider;
    $("#llm-base-url").value = config.base_url;
    $("#llm-model").value = config.model;
    $("#llm-api-key").value = "";
    $("#llm-clear-key").checked = false;
    $("#llm-max-chars").value = config.max_input_chars;
    $("#llm-temperature").value = config.temperature;
    $("#key-state").textContent = config.api_key_configured ? `API Key 已保存在本机，仅当前系统用户可读（${config.api_key_hint}）` : "尚未保存 API Key；本机 Ollama 不需要密钥。";
    const status = $("#llm-status");
    status.textContent = config.enabled ? `已启用 · ${config.model}` : "尚未启用";
    status.classList.toggle("ready", config.enabled);
  } catch (error) {
    showNotice(error.message, true);
  }
}

$("#llm-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#save-llm");
  button.disabled = true;
  try {
    const payload = {
      enabled: $("#llm-enabled").checked,
      provider: $("#llm-provider").value,
      base_url: $("#llm-base-url").value,
      model: $("#llm-model").value,
      api_key: $("#llm-api-key").value || null,
      clear_api_key: $("#llm-clear-key").checked,
      max_input_chars: Number($("#llm-max-chars").value),
      temperature: Number($("#llm-temperature").value),
    };
    await request("/api/v1/llm/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    await loadLLMSettings();
    showNotice("LLM 设置已保存在本机。下一步请点击“测试连接”。");
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    button.disabled = false;
  }
});

$("#test-llm").addEventListener("click", async () => {
  const button = $("#test-llm");
  button.disabled = true;
  button.textContent = "正在测试……";
  try {
    const result = await request("/api/v1/llm/test", { method: "POST" });
    showNotice(`${result.message}；耗时 ${result.latency_ms} ms。`);
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "测试连接";
  }
});

let zoteroAutoSynced = false;

async function loadZoteroSettings(runAutomaticSync = false) {
  try {
    const config = await request("/api/v1/zotero/settings");
    $("#zotero-enabled").checked = config.enabled;
    $("#zotero-api-key").value = "";
    $("#zotero-clear-key").checked = false;
    $("#zotero-collection").value = config.collection_name;
    $("#zotero-auto-push").checked = config.auto_push_discovered;
    $("#zotero-key-state").textContent = config.api_key_configured
      ? `API Key 已保存在本机（${config.api_key_hint}）${config.username ? `；账号：${config.username}` : ""}`
      : "尚未配置 Key。请创建只供 Paperlib 使用的 Zotero Key。";
    const status = $("#zotero-status");
    status.textContent = config.enabled
      ? (config.username ? `已启用 · ${config.username}` : "已启用 · 等待测试")
      : "尚未启用";
    status.classList.toggle("ready", config.enabled && config.api_key_configured);
    if (runAutomaticSync && config.enabled && config.api_key_configured && !zoteroAutoSynced) {
      zoteroAutoSynced = true;
      await runZoteroSync(true);
    }
    return config;
  } catch (error) {
    showNotice(error.message, true);
    return null;
  }
}

$("#zotero-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#save-zotero");
  button.disabled = true;
  try {
    await request("/api/v1/zotero/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        enabled: $("#zotero-enabled").checked,
        api_key: $("#zotero-api-key").value || null,
        clear_api_key: $("#zotero-clear-key").checked,
        collection_name: $("#zotero-collection").value,
        auto_push_discovered: $("#zotero-auto-push").checked,
      }),
    });
    zoteroAutoSynced = false;
    await loadZoteroSettings(false);
    showNotice("Zotero 设置已保存在本机。下一步请点击“测试连接”。");
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    button.disabled = false;
  }
});

$("#test-zotero").addEventListener("click", async () => {
  const button = $("#test-zotero");
  button.disabled = true;
  button.textContent = "正在测试……";
  try {
    const config = await request("/api/v1/zotero/test", { method: "POST" });
    await loadZoteroSettings(false);
    showNotice(`连接成功：${config.username || `用户 ${config.user_id}`}，已确认个人资料库读写权限。`);
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "测试连接";
  }
});

function renderZoteroSummary(result) {
  const summary = $("#zotero-summary");
  summary.innerHTML = `
    <p class="eyebrow">LAST SYNC</p>
    <h3>同步完成</h3>
    <div class="sync-metrics">
      <span><strong>${result.imported}</strong> 从 Zotero 新增</span>
      <span><strong>${result.linked}</strong> 已有记录关联</span>
      <span><strong>${result.enriched}</strong> 本地记录补全</span>
      <span><strong>${result.pushed}</strong> 写入 Zotero</span>
      <span><strong>${result.skipped}</strong> 跳过</span>
    </div>
    <p class="hint">Zotero library version：${result.library_version}</p>`;
  summary.classList.remove("hidden");
}

async function runZoteroSync(automatic = false) {
  const button = $("#sync-zotero");
  button.disabled = true;
  button.textContent = "正在同步……";
  if (!automatic) showNotice("正在非破坏式同步 Zotero 与 Paperlib，请稍候……");
  try {
    const result = await request("/api/v1/zotero/sync", { method: "POST" });
    renderZoteroSummary(result);
    const message = `Zotero 同步完成：导入 ${result.imported}，关联 ${result.linked}，补全 ${result.enriched}，写回 ${result.pushed}。`;
    showNotice(result.warnings.length ? `${message} ${result.warnings.join("；")}` : message, result.warnings.length > 0);
    await runSearch();
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "立即双向同步";
  }
}

$("#sync-zotero").addEventListener("click", () => runZoteroSync(false));

let s2Context = { seedIds: [], relation: "none" };

async function loadS2Settings() {
  try {
    const config = await request("/api/v1/s2/settings");
    $("#s2-api-key").value = "";
    $("#s2-clear-key").checked = false;
    $("#s2-key-state").textContent = config.api_key_configured
      ? `API Key 已保存在本机，仅当前系统用户可读（${config.api_key_hint}）`
      : "当前使用匿名访问；繁忙时可能受到共享限流。";
    const status = $("#s2-status");
    status.textContent = config.api_key_configured ? "API Key 已配置" : "匿名访问";
    status.classList.toggle("ready", config.api_key_configured);
  } catch (error) {
    showNotice(error.message, true);
  }
}

$("#s2-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = $("#save-s2");
  button.disabled = true;
  try {
    await request("/api/v1/s2/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        api_key: $("#s2-api-key").value || null,
        clear_api_key: $("#s2-clear-key").checked,
      }),
    });
    await loadS2Settings();
    showNotice("Semantic Scholar 设置已保存在本机。");
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    button.disabled = false;
  }
});

$("#test-s2").addEventListener("click", async () => {
  const button = $("#test-s2");
  button.disabled = true;
  button.textContent = "正在测试……";
  try {
    const result = await request("/api/v1/s2/test", { method: "POST" });
    showNotice(`${result.message}${result.sample_title ? `；示例结果：${result.sample_title}` : ""}`);
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "测试连接";
  }
});

async function loadSeedWorks() {
  try {
    const data = await request("/api/v1/search?q=&mode=keyword&limit=100");
    const options = data.hits.map(({ work }) => {
      const suffix = [work.publication_year, work.authors.slice(0, 2).join(", ")].filter(Boolean).join(" · ");
      return `<option value="${work.id}">${escapeHtml(work.title)}${suffix ? ` — ${escapeHtml(suffix)}` : ""}</option>`;
    }).join("");
    const empty = '<option value="" disabled>请先在资料库添加论文</option>';
    $("#s2-expand-seed").innerHTML = options || empty;
    $("#s2-intersection-seeds").innerHTML = options || empty;
  } catch (error) {
    showNotice(error.message, true);
  }
}

function renderS2Paper(paper) {
  const authors = paper.authors.slice(0, 8).join(", ");
  const meta = [paper.year, paper.venue, authors].filter(Boolean).join(" · ");
  const matches = paper.matched_seed_titles?.length
    ? `<p class="seed-match">匹配 ${paper.match_count} 篇种子：${paper.matched_seed_titles.map(escapeHtml).join("；")}</p>`
    : "";
  const semanticScholarUrl = safeUrl(paper.url);
  const openPdfUrl = safeUrl(paper.open_access_pdf_url);
  return `
    <article class="result-card s2-card ${paper.already_in_library ? "imported" : ""}" data-paper-id="${escapeHtml(paper.paper_id)}">
      <div class="result-head">
        <div>
          <h3>${escapeHtml(paper.title)}</h3>
          <p class="meta">${escapeHtml(meta)}</p>
        </div>
        <span class="rank">${paper.already_in_library ? "已在本地库" : "候选"}</span>
      </div>
      <div class="s2-metrics">
        ${paper.citation_count != null ? `<span class="metric">被引 ${paper.citation_count}</span>` : ""}
        ${paper.reference_count != null ? `<span class="metric">参考文献 ${paper.reference_count}</span>` : ""}
        ${paper.doi ? `<span class="metric">DOI</span>` : ""}
        ${paper.arxiv_id ? `<span class="metric">arXiv</span>` : ""}
      </div>
      ${matches}
      <p class="snippet">${escapeHtml(paper.abstract || "暂无摘要")}</p>
      <div class="s2-actions">
        <div class="external-links">
          ${semanticScholarUrl ? `<a href="${escapeHtml(semanticScholarUrl)}" target="_blank" rel="noreferrer">Semantic Scholar</a>` : ""}
          ${openPdfUrl ? `<a href="${escapeHtml(openPdfUrl)}" target="_blank" rel="noreferrer">开放 PDF</a>` : ""}
        </div>
        <button class="small-button import-s2" ${paper.already_in_library ? "disabled" : ""}>${paper.already_in_library ? "已导入" : "导入本地库"}</button>
      </div>
    </article>`;
}

function renderS2Results(data, context) {
  s2Context = context;
  const heading = $("#s2-result-heading");
  heading.textContent = `${data.query || "发现结果"}：${data.total} 篇`;
  heading.classList.remove("hidden");
  if (data.warnings?.length) showNotice(data.warnings.join("；"));
  $("#s2-results").innerHTML = data.papers.length
    ? data.papers.map(renderS2Paper).join("")
    : '<div class="empty">没有找到结果。可以换一个关键词，或提高每篇种子的抓取上限。</div>';
}

async function runS2Search() {
  const query = $("#s2-query").value.trim();
  if (query.length < 2) {
    showNotice("请输入至少两个字符的搜索词。", true);
    return;
  }
  $("#s2-results").innerHTML = '<div class="empty">正在查询 Semantic Scholar……</div>';
  try {
    const data = await request(`/api/v1/s2/search?q=${encodeURIComponent(query)}&limit=30`);
    renderS2Results(data, { seedIds: [], relation: "none" });
  } catch (error) {
    showNotice(error.message, true);
    $("#s2-results").innerHTML = '<div class="empty">查询失败</div>';
  }
}

$("#s2-search").addEventListener("click", runS2Search);
$("#s2-query").addEventListener("keydown", (event) => {
  if (event.key === "Enter") runS2Search();
});

async function expandS2(direction, button) {
  const seedId = $("#s2-expand-seed").value;
  if (!seedId) {
    showNotice("请先选择一篇种子论文。", true);
    return;
  }
  button.disabled = true;
  $("#s2-results").innerHTML = '<div class="empty">正在展开一跳引用网络……</div>';
  try {
    const data = await request("/api/v1/s2/discover/expand", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ seed_work_id: seedId, direction, limit: 100 }),
    });
    renderS2Results(data, {
      seedIds: [seedId],
      relation: direction === "citations" ? "cites_seeds" : "cited_by_seeds",
    });
  } catch (error) {
    showNotice(error.message, true);
    $("#s2-results").innerHTML = '<div class="empty">展开失败</div>';
  } finally {
    button.disabled = false;
  }
}

$("#s2-citations").addEventListener("click", (event) => expandS2("citations", event.currentTarget));
$("#s2-references").addEventListener("click", (event) => expandS2("references", event.currentTarget));

$("#s2-intersection").addEventListener("click", async () => {
  const button = $("#s2-intersection");
  const seedIds = Array.from($("#s2-intersection-seeds").selectedOptions).map((option) => option.value);
  if (seedIds.length < 2 || seedIds.length > 5) {
    showNotice("请选择 2–5 篇种子论文。", true);
    return;
  }
  button.disabled = true;
  button.textContent = "正在逐篇查询……";
  $("#s2-results").innerHTML = '<div class="empty">正在查找同时引用全部种子的论文。系统会遵守 API 限流，请稍候……</div>';
  try {
    const yearValue = $("#s2-year-from").value;
    const data = await request("/api/v1/s2/discover/intersection", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        seed_work_ids: seedIds,
        limit_per_seed: Number($("#s2-limit").value),
        year_from: yearValue ? Number(yearValue) : null,
      }),
    });
    renderS2Results(data, { seedIds, relation: "cites_seeds" });
  } catch (error) {
    showNotice(error.message, true);
    $("#s2-results").innerHTML = '<div class="empty">交集查询失败</div>';
  } finally {
    button.disabled = false;
    button.textContent = "查找交集";
  }
});

$("#s2-results").addEventListener("click", async (event) => {
  const button = event.target.closest(".import-s2");
  if (!button) return;
  const card = button.closest(".s2-card");
  button.disabled = true;
  button.textContent = "正在导入……";
  try {
    const result = await request("/api/v1/s2/import", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        paper_id: card.dataset.paperId,
        seed_work_ids: s2Context.seedIds,
        relation: s2Context.relation,
      }),
    });
    card.classList.add("imported");
    card.querySelector(".rank").textContent = "已在本地库";
    button.textContent = result.created ? "已导入" : "已存在";
    const warningText = result.warnings?.length ? ` 提示：${result.warnings.join("；")}` : "";
    showNotice(`《${result.work.title}》已进入本地库；新增 ${result.citation_edges_added} 条引用关系。${warningText}`, Boolean(result.warnings?.length));
    await loadSeedWorks();
  } catch (error) {
    button.disabled = false;
    button.textContent = "导入本地库";
    showNotice(error.message, true);
  }
});

runSearch();
loadLLMSettings();
loadS2Settings();
loadZoteroSettings(false);
