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
      showNotice(data.warnings.length ? data.warnings.join("；") : "结构化提取完成，结果等待审核。");
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
  glossary: "术语",
  resource: "资源",
  method: "方法",
  dataset: "数据集",
  finding: "实验发现",
  limitation: "局限",
  suggested_tag: "建议标签",
};

function formatFactValue(value) {
  return Object.entries(value || {})
    .map(([key, item]) => `${key}: ${typeof item === "object" ? JSON.stringify(item) : item}`)
    .join("\n");
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
        <span class="review-status">${fact.review_status === "accepted" ? "已接受" : fact.review_status === "rejected" ? "已拒绝" : "待审核"}</span>
      </div>
      <div class="fact-value">${escapeHtml(formatFactValue(fact.value))}</div>
      ${fact.evidence_text ? `<blockquote class="evidence">证据：${escapeHtml(fact.evidence_text)}</blockquote>` : ""}
      <div class="review-actions">
        <button class="small-button reject-button ${fact.review_status === "rejected" ? "rejected" : ""}" data-status="rejected">拒绝</button>
        <button class="small-button accept-button ${fact.review_status === "accepted" ? "accepted" : ""}" data-status="accepted">接受</button>
      </div>
    </article>
  `).join("");
}

$("#analysis-results").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-status]");
  if (!button) return;
  const card = button.closest(".fact-card");
  button.disabled = true;
  try {
    const fact = await request(`/api/v1/facts/${card.dataset.factId}/review`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: button.dataset.status }),
    });
    card.querySelector(".review-status").textContent = fact.review_status === "accepted" ? "已接受" : "已拒绝";
    card.querySelectorAll("[data-status]").forEach((item) => item.classList.remove("accepted", "rejected"));
    button.classList.add(fact.review_status);
    if (fact.fact_type === "suggested_tag" && fact.review_status === "accepted") {
      showNotice("建议标签已写入资料库。");
    }
  } catch (error) {
    showNotice(error.message, true);
  } finally {
    button.disabled = false;
  }
});

function setView(viewName) {
  document.querySelectorAll(".view").forEach((view) => view.classList.remove("active"));
  document.querySelectorAll(".tab-button").forEach((button) => button.classList.toggle("active", button.dataset.view === viewName));
  $(`#view-${viewName}`).classList.add("active");
  $("#open-add").classList.toggle("hidden", viewName !== "library");
  showNotice("");
  if (viewName === "llm") loadLLMSettings();
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

runSearch();
loadLLMSettings();
