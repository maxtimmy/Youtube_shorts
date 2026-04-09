const state = {
  dashboard: null,
  workflows: null,
  serialDetail: null,
  accountDetail: null,
  selectedSerial: null,
  selectedAccount: null,
  activeTab: "overview",
};

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ error: "Request failed" }));
    throw new Error(error.error || "Request failed");
  }
  return response.json();
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ru-RU");
}

function setLastUpdated(value) {
  document.getElementById("lastUpdated").textContent = formatDateTime(value);
}

function setTab(tabName) {
  state.activeTab = tabName;
  document.querySelectorAll(".tab-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === tabName);
  });
  document.getElementById("tabOverviewPanel").classList.toggle("active", tabName === "overview");
  document.getElementById("tabWorkflowsPanel").classList.toggle("active", tabName === "workflows");
}

function renderSummary() {
  const grid = document.getElementById("summaryGrid");
  grid.innerHTML = "";
  const data = state.dashboard;
  const metrics = [
    ["Сериалов", data.media.serialCount, "Всего в каталоге"],
    ["Эпизодов", data.media.episodeCount, "Доступны в media-library.sqlite"],
    ["Шортсов", data.media.shortCount, "Готовы к рендеру и публикации"],
    ["Аккаунтов", data.accounts.length, `${data.activeCooldowns.length} на cooldown сейчас`],
  ];
  for (const [label, value, foot] of metrics) {
    const card = el("div", "metric-card");
    card.append(el("div", "metric-label", label));
    card.append(el("div", "metric-value", String(value)));
    card.append(el("div", "metric-foot", foot));
    grid.append(card);
  }
}

function renderSerials() {
  const root = document.getElementById("serialsList");
  root.innerHTML = "";
  for (const serial of state.dashboard.media.serials) {
    const card = el("div", `serial-card${state.selectedSerial === serial.serial_slug ? " active" : ""}`);
    card.onclick = () => loadSerial(serial.serial_slug);
    card.append(el("h3", "", serial.serial_name));
    const meta = el("div", "serial-meta");
    meta.append(el("span", "badge neutral", `${serial.episode_count} эпизодов`));
    meta.append(el("span", "badge neutral", `${serial.short_count} шортсов`));
    card.append(meta);
    root.append(card);
  }
}

function renderCooldowns() {
  const root = document.getElementById("cooldownsList");
  root.innerHTML = "";
  if (!state.dashboard.activeCooldowns.length) {
    root.append(el("div", "detail-empty", "Сейчас нет активных блокировок аккаунтов."));
    return;
  }
  for (const row of state.dashboard.activeCooldowns) {
    const card = el("div", "stack-card");
    card.append(el("h3", "", row.account_name));
    const badges = el("div", "row-meta");
    badges.append(el("span", "badge bad", row.reason_code));
    badges.append(el("span", "badge warn", `до ${formatDateTime(row.blocked_until)}`));
    card.append(badges);
    card.append(el("div", "subtle", row.source_node_name || "Источник не указан"));
    card.append(el("p", "subtle", row.reason_message || ""));
    root.append(card);
  }
}

function renderUploads() {
  const root = document.getElementById("uploadsTable");
  if (!state.dashboard.latestUploads.length) {
    root.innerHTML = '<div class="detail-empty">История загрузок пока пустая.</div>';
    return;
  }
  const table = document.createElement("table");
  table.innerHTML = '<thead><tr><th>Аккаунт</th><th>Шорт</th><th>Статус</th><th>Загрузка</th><th>Публикация</th></tr></thead><tbody></tbody>';
  const tbody = table.querySelector("tbody");
  for (const row of state.dashboard.latestUploads) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><strong>${row.account_name}</strong><br /><span class="subtle">${row.serial_slug || ""}</span></td>
      <td><strong>${row.short_name}</strong><br /><span class="subtle">${row.episode_base_name || ""}</span><br />${row.youtube_url ? `<a href="${row.youtube_url}" target="_blank" rel="noreferrer">Открыть на YouTube</a>` : ""}</td>
      <td><span class="badge ${row.status === "published" ? "ok" : "warn"}">${row.status}</span></td>
      <td>${formatDateTime(row.uploaded_at)}</td>
      <td>${row.publish_at_local || "—"}</td>`;
    tbody.append(tr);
  }
  root.innerHTML = "";
  root.append(table);
}

async function saveActiveSerial(accountSlug, serialSlug) {
  await api(`/api/accounts/${encodeURIComponent(accountSlug)}/active-serial`, { method: "POST", body: JSON.stringify({ serialSlug }) });
  await loadAll();
  await loadAccount(accountSlug);
}

async function clearCooldown(accountSlug) {
  await api(`/api/accounts/${encodeURIComponent(accountSlug)}/clear-cooldown`, { method: "POST" });
  await loadAll();
  await loadAccount(accountSlug);
}

async function toggleAccount(accountSlug) {
  await api(`/api/accounts/${encodeURIComponent(accountSlug)}/toggle-active`, { method: "POST" });
  await loadAll();
  await loadAccount(accountSlug);
}

async function saveSlots(accountSlug, rawValue) {
  const slots = rawValue.split(",").map((item) => item.trim()).filter(Boolean);
  await api(`/api/accounts/${encodeURIComponent(accountSlug)}/schedule-slots`, { method: "POST", body: JSON.stringify({ slots }) });
  await loadAll();
  await loadAccount(accountSlug);
}

async function runWorkflow(kind) {
  await api(`/api/workflows/${kind}/run`, { method: "POST" });
  alert(kind === "render" ? "Render Queue отправлен на ручной запуск." : "YouTube Upload отправлен на ручной запуск.");
  window.setTimeout(() => loadWorkflows().catch((error) => alert(error.message)), 1500);
}

function renderAccounts() {
  const root = document.getElementById("accountsList");
  root.innerHTML = "";
  const serialOptions = state.dashboard.media.serials;
  for (const account of state.dashboard.accounts) {
    const card = el("div", "account-card");
    const top = el("div", "account-top");
    const titleWrap = el("div");
    titleWrap.append(el("h3", "", account.account_name));
    titleWrap.append(el("div", "subtle", account.youtube_credential_name || "Credential не задан"));
    const badges = el("div", "row-meta");
    badges.append(el("span", `badge ${account.is_active ? "ok" : "neutral"}`, account.is_active ? "active" : "disabled"));
    badges.append(el("span", `badge ${account.is_blocked ? "bad" : "ok"}`, account.is_blocked ? "cooldown" : "available"));
    if (account.active_serial_name) badges.append(el("span", "badge neutral", account.active_serial_name));
    top.append(titleWrap);
    top.append(badges);
    card.append(top);
    const meta = el("div", "account-meta");
    meta.append(el("span", "", `Всего загрузок: ${account.uploads_total}`));
    meta.append(el("span", "", `Scheduled: ${account.scheduled_total}`));
    meta.append(el("span", "", `Published: ${account.published_total}`));
    card.append(meta);
    const controls = el("div", "account-controls");
    const selectWrap = el("div", "select-row");
    const select = document.createElement("select");
    for (const serial of serialOptions) {
      const option = document.createElement("option");
      option.value = serial.serial_slug;
      option.textContent = serial.serial_name;
      option.selected = serial.serial_slug === account.active_serial_slug;
      select.append(option);
    }
    const saveSerialButton = el("button", "secondary-button", "Сменить сериал");
    saveSerialButton.onclick = () => saveActiveSerial(account.account_slug, select.value);
    selectWrap.append(select);
    selectWrap.append(saveSerialButton);
    controls.append(selectWrap);
    const actions = el("div", "account-actions");
    const inspectButton = el("button", "secondary-button", "Открыть детали");
    inspectButton.onclick = () => loadAccount(account.account_slug);
    actions.append(inspectButton);
    const toggleButton = el("button", "mini-button", account.is_active ? "Выключить" : "Включить");
    toggleButton.onclick = () => toggleAccount(account.account_slug);
    actions.append(toggleButton);
    if (account.is_blocked) {
      const clearButton = el("button", "mini-button", "Снять блокировку");
      clearButton.onclick = () => clearCooldown(account.account_slug);
      actions.append(clearButton);
    }
    controls.append(actions);
    card.append(controls);
    root.append(card);
  }
}

function renderSerialDetail() {
  const root = document.getElementById("serialDetail");
  root.innerHTML = "";
  if (!state.serialDetail) {
    root.textContent = "Выбери сериал, чтобы увидеть эпизоды и список шортсов.";
    root.className = "detail-empty";
    return;
  }
  root.className = "episode-list";
  for (const episode of state.serialDetail.episodes) {
    const card = el("div", "episode-card");
    card.append(el("h3", "", episode.episode_base_name));
    const meta = el("div", "row-meta");
    meta.append(el("span", "badge neutral", `Шортсов: ${episode.short_count}`));
    meta.append(el("span", "badge neutral", `Chunks: ${episode.chunk_count}`));
    card.append(meta);
    const shorts = el("div", "short-list");
    for (const short of episode.shorts.slice(0, 6)) {
      const item = el("div", "short-item");
      item.innerHTML = `<strong>${short.short_name}</strong><div class="subtle">part ${short.short_part} • ${Number(short.duration || 0).toFixed(1)}s</div><div class="subtle">${(short.text || "").slice(0, 120)}</div>`;
      shorts.append(item);
    }
    if (episode.shorts.length > 6) shorts.append(el("div", "subtle", `И ещё ${episode.shorts.length - 6} шортсов...`));
    card.append(shorts);
    root.append(card);
  }
}

function renderAccountDetail() {
  const root = document.getElementById("accountDetail");
  root.innerHTML = "";
  if (!state.accountDetail) {
    root.textContent = "Выбери аккаунт справа, чтобы увидеть историю и настройки.";
    root.className = "detail-empty";
    return;
  }
  root.className = "";
  const { account, history, cooldowns, scheduleSlots } = state.accountDetail;
  const header = el("div", "stack-card");
  header.innerHTML = `<h3>${account.account_name}</h3><div class="row-meta"><span class="badge neutral">${account.youtube_credential_name || "no credential"}</span><span class="badge neutral">${account.active_serial_name || "serial not selected"}</span><span class="badge ${account.is_active ? "ok" : "neutral"}">${account.is_active ? "active" : "disabled"}</span></div>`;
  root.append(header);
  const scheduleCard = el("div", "stack-card");
  scheduleCard.append(el("h3", "", "Слоты публикации"));
  const editor = el("div", "schedule-editor");
  const input = document.createElement("input");
  input.type = "text";
  input.value = scheduleSlots.map((slot) => slot.slot_time).join(", ");
  input.placeholder = "08:50, 11:50, 13:50";
  const save = el("button", "secondary-button", "Сохранить слоты");
  save.onclick = () => saveSlots(account.account_slug, input.value);
  editor.append(input);
  editor.append(save);
  const chips = el("div", "schedule-grid");
  for (const slot of scheduleSlots) chips.append(el("span", "slot-chip", slot.slot_time));
  scheduleCard.append(editor);
  scheduleCard.append(chips);
  root.append(scheduleCard);
  const cooldownCard = el("div", "stack-card");
  cooldownCard.append(el("h3", "", "История блокировок"));
  const cooldownList = el("div", "cooldown-list");
  if (!cooldowns.length) cooldownList.append(el("div", "subtle", "Блокировок пока не было."));
  else for (const row of cooldowns.slice(0, 10)) {
    const item = el("div", "short-item");
    item.innerHTML = `<strong>${row.reason_code}</strong><div class="subtle">${row.source_node_name || "unknown source"}</div><div class="subtle">до ${formatDateTime(row.blocked_until)}</div><div class="subtle">${row.reason_message || ""}</div>`;
    cooldownList.append(item);
  }
  cooldownCard.append(cooldownList);
  root.append(cooldownCard);
  const historyCard = el("div", "stack-card");
  historyCard.append(el("h3", "", "История загрузок"));
  const historyList = el("div", "history-list");
  if (!history.length) historyList.append(el("div", "subtle", "История публикаций пока пустая."));
  else for (const row of history.slice(0, 30)) {
    const item = el("div", "short-item");
    item.innerHTML = `<strong>${row.short_name}</strong><div class="subtle">${row.episode_base_name || row.serial_slug || ""}</div><div class="row-meta"><span class="badge ${row.status === "published" ? "ok" : "warn"}">${row.status}</span><span class="badge neutral">${row.publish_at_local || "no publish time"}</span></div>${row.youtube_url ? `<a href="${row.youtube_url}" target="_blank" rel="noreferrer">Открыть ролик</a>` : ""}`;
    historyList.append(item);
  }
  historyCard.append(historyList);
  root.append(historyCard);
}

function renderWorkflowStatus() {
  const root = document.getElementById("workflowStatusList");
  root.innerHTML = "";
  if (!state.workflows || !state.workflows.workflows.length) {
    root.append(el("div", "detail-empty", "Workflow-данные пока недоступны."));
    return;
  }
  for (const workflow of state.workflows.workflows) {
    const card = el("div", "stack-card");
    card.append(el("h3", "", workflow.name));
    const badges = el("div", "row-meta");
    badges.append(el("span", `badge ${workflow.active ? "ok" : "neutral"}`, workflow.active ? "active" : "inactive"));
    badges.append(el("span", `badge ${workflow.isRunning ? "warn" : "neutral"}`, workflow.isRunning ? "running" : "idle"));
    if (workflow.lastExecution?.status) {
      const statusClass = workflow.lastExecution.status === "success" ? "ok" : workflow.lastExecution.status === "error" ? "bad" : "warn";
      badges.append(el("span", `badge ${statusClass}`, workflow.lastExecution.status));
    }
    card.append(badges);
    const meta = el("div", "account-meta");
    meta.append(el("span", "", `ID: ${workflow.id}`));
    meta.append(el("span", "", `Обновлён: ${formatDateTime(workflow.updatedAt)}`));
    meta.append(el("span", "", `Последний старт: ${formatDateTime(workflow.lastExecution?.startedAt)}`));
    meta.append(el("span", "", `Последний финиш: ${formatDateTime(workflow.lastExecution?.stoppedAt)}`));
    card.append(meta);
    if (workflow.canRunManually) {
      const hint = el("div", "subtle", `Ручной запуск доступен для ${workflow.runKey === "render" ? "Render Queue" : "YouTube Upload"}.`);
      card.append(hint);
    }
    root.append(card);
  }
}

function renderWorkflowErrors() {
  const root = document.getElementById("workflowErrorsList");
  root.innerHTML = "";
  if (!state.workflows || !state.workflows.errors.length) {
    root.append(el("div", "detail-empty", "Последних ошибок сейчас нет."));
    return;
  }
  for (const row of state.workflows.errors) {
    const card = el("div", "stack-card");
    card.append(el("h3", "", row.workflow_name));
    const badges = el("div", "row-meta");
    badges.append(el("span", "badge bad", row.status || "error"));
    if (row.lastNode) badges.append(el("span", "badge neutral", row.lastNode));
    if (row.httpCode) badges.append(el("span", "badge warn", `HTTP ${row.httpCode}`));
    card.append(badges);
    card.append(el("div", "subtle", `Execution #${row.execution_id} • ${formatDateTime(row.startedAt)}`));
    if (row.description) card.append(el("p", "error-text", row.description));
    if (row.message && row.message !== row.description) card.append(el("p", "subtle", row.message));
    root.append(card);
  }
}

async function loadSerial(serialSlug) {
  state.selectedSerial = serialSlug;
  state.serialDetail = await api(`/api/serials/${encodeURIComponent(serialSlug)}`);
  renderSerials();
  renderSerialDetail();
}

async function loadAccount(accountSlug) {
  state.selectedAccount = accountSlug;
  state.accountDetail = await api(`/api/accounts/${encodeURIComponent(accountSlug)}/history`);
  renderAccountDetail();
}

async function loadDashboard() {
  state.dashboard = await api("/api/dashboard");
  renderSummary();
  renderSerials();
  renderAccounts();
  renderCooldowns();
  renderUploads();
  if (!state.selectedSerial && state.dashboard.media.serials.length) {
    await loadSerial(state.dashboard.media.serials[0].serial_slug);
  }
  if (!state.selectedAccount && state.dashboard.accounts.length) {
    const firstActive = state.dashboard.accounts.find((account) => account.is_active) || state.dashboard.accounts[0];
    await loadAccount(firstActive.account_slug);
  }
}

async function loadWorkflows() {
  state.workflows = await api("/api/workflows");
  renderWorkflowStatus();
  renderWorkflowErrors();
}

async function loadAll() {
  const [dashboard, workflows] = await Promise.all([api("/api/dashboard"), api("/api/workflows")]);
  state.dashboard = dashboard;
  state.workflows = workflows;
  setLastUpdated(dashboard.generatedAt);
  renderSummary();
  renderSerials();
  renderAccounts();
  renderCooldowns();
  renderUploads();
  renderWorkflowStatus();
  renderWorkflowErrors();
  if (!state.selectedSerial && state.dashboard.media.serials.length) {
    await loadSerial(state.dashboard.media.serials[0].serial_slug);
  } else if (state.selectedSerial) {
    await loadSerial(state.selectedSerial);
  }
  if (!state.selectedAccount && state.dashboard.accounts.length) {
    const firstActive = state.dashboard.accounts.find((account) => account.is_active) || state.dashboard.accounts[0];
    await loadAccount(firstActive.account_slug);
  } else if (state.selectedAccount) {
    await loadAccount(state.selectedAccount);
  }
}

document.getElementById("refreshButton").addEventListener("click", () => loadAll().catch((error) => alert(error.message)));
document.getElementById("runRenderButton").addEventListener("click", () => runWorkflow("render").catch((error) => alert(error.message)));
document.getElementById("runUploadButton").addEventListener("click", () => runWorkflow("upload").catch((error) => alert(error.message)));
document.querySelectorAll(".tab-button").forEach((button) => {
  button.addEventListener("click", () => setTab(button.dataset.tab));
});

setTab("overview");
loadAll().catch((error) => alert(error.message));
