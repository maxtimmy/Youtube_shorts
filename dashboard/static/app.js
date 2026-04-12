const state = {
  dashboard: null,
  workflows: null,
  serialDetail: null,
  accountDetail: null,
  selectedSerial: null,
  selectedAccount: null,
  activeTab: "overview",
  selectedErrorIds: new Set(),
  workflowActionState: {},
  cooldownModalAccountSlug: null,
  serialModalOpen: false,
  accountModalOpen: false,
  uploadModalSerialSlug: null,
  uploadModalFile: null,
  expandedEpisodes: new Set(),
  expandedAccountSections: new Set(["slots", "cooldowns", "history"]),
  pendingRequests: 0,
  selectedCalendarDate: null,
  youtubeCredentials: [],
  youtubeAnalytics: null,
};

async function api(path, options = {}) {
  state.pendingRequests += 1;
  setLoadingState(true, "Обновляем данные...");
  try {
    const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ error: "Request failed" }));
      throw new Error(error.error || "Request failed");
    }
    return response.json();
  } finally {
    state.pendingRequests = Math.max(0, state.pendingRequests - 1);
    if (!state.pendingRequests) setLoadingState(false);
  }
}

async function apiUpload(path, file, headers = {}) {
  state.pendingRequests += 1;
  setLoadingState(true, "Загружаем файл...");
  try {
    const response = await fetch(path, {
      method: "POST",
      headers,
      body: file,
    });
    if (!response.ok) {
      const error = await response.json().catch(() => ({ error: "Upload failed" }));
      throw new Error(error.error || "Upload failed");
    }
    return response.json();
  } finally {
    state.pendingRequests = Math.max(0, state.pendingRequests - 1);
    if (!state.pendingRequests) setLoadingState(false);
  }
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function showToast(message, type = "info") {
  const stack = document.getElementById("toastStack");
  if (!stack || !message) return;
  const toast = el("div", `toast toast-${type}`);
  toast.append(el("div", "toast-message", message));
  stack.append(toast);
  requestAnimationFrame(() => toast.classList.add("visible"));
  window.setTimeout(() => {
    toast.classList.remove("visible");
    window.setTimeout(() => toast.remove(), 260);
  }, 3200);
}

function setStatusBar(message, type = "ready") {
  const text = document.getElementById("appStatusText");
  const dot = document.getElementById("appStatusDot");
  if (!text || !dot) return;
  text.textContent = message;
  dot.className = `app-status-dot app-status-${type}`;
}

function setLoadingState(isVisible, message = "Обновляем данные...") {
  if (isVisible) setStatusBar(message, "loading");
}

function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ru-RU");
}

function setLastUpdated(value) {
  document.getElementById("lastUpdated").textContent = formatDateTime(value);
  setStatusBar(`Данные обновлены: ${formatDateTime(value)}`, "ready");
}

function setTab(tabName) {
  state.activeTab = tabName;
  document.querySelectorAll(".tab-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === tabName);
  });
  document.getElementById("tabOverviewPanel").classList.toggle("active", tabName === "overview");
  document.getElementById("tabAnalyticsPanel").classList.toggle("active", tabName === "analytics");
  document.getElementById("tabWorkflowsPanel").classList.toggle("active", tabName === "workflows");
  if (tabName === "analytics" && !state.youtubeAnalytics) {
    loadAnalytics().catch((error) => showToast(error.message, "error"));
  }
}

function formatCompactNumber(value) {
  const number = Number(value || 0);
  return new Intl.NumberFormat("ru-RU", { notation: "compact", maximumFractionDigits: 1 }).format(number);
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
    const top = el("div", "metric-top");
    const textCol = el("div", "metric-text");
    textCol.append(el("div", "metric-label", label));
    textCol.append(el("div", "metric-foot", foot));
    top.append(textCol);
    top.append(el("div", "metric-value", String(value)));
    card.append(top);
    grid.append(card);
  }
}

function renderSerials() {
  const root = document.getElementById("serialsList");
  root.innerHTML = "";
  const addCard = el("button", "serial-card add-serial-card", "+");
  addCard.type = "button";
  addCard.title = "Создать новый сериал";
  addCard.onclick = () => openSerialModal();
  root.append(addCard);
  for (const serial of state.dashboard.media.serials) {
    const card = el("div", `serial-card${state.selectedSerial === serial.serial_slug ? " active selected-pulse" : ""}`);
    card.onclick = () => loadSerial(serial.serial_slug);
    const top = el("div", "serial-card-top");
    const titleRow = el("div", "serial-card-title-row");
    titleRow.append(el("h3", "", serial.serial_name));
    const deleteButton = el("button", "serial-delete-button");
    deleteButton.type = "button";
    deleteButton.title = `Удалить сериал ${serial.serial_name}`;
    deleteButton.setAttribute("aria-label", `Удалить сериал ${serial.serial_name}`);
    deleteButton.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 3h6l1 2h4v2H4V5h4l1-2Zm-2 6h2v8H7V9Zm4 0h2v8h-2V9Zm4 0h2v8h-2V9ZM6 7h12l-1 13a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2L6 7Z"/></svg>';
    deleteButton.onclick = (event) => {
      event.stopPropagation();
      deleteSerial(serial).catch((error) => showToast(error.message, "error"));
    };
    titleRow.append(deleteButton);
    top.append(titleRow);
    const meta = el("div", "serial-meta");
    meta.append(el("span", "badge neutral", `${serial.episode_count} эпизодов`));
    meta.append(el("span", "badge neutral", `${serial.short_count} шортсов`));
    top.append(meta);
    card.append(top);
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
  const events = state.dashboard.publicationCalendar || [];
  if (!events.length) {
    root.innerHTML = '<div class="detail-empty">Календарь публикаций пока пустой.</div>';
    return;
  }
  const days = [...new Set(events.map((row) => row.publish_date).filter(Boolean))];
  const today = new Date().toLocaleDateString("en-CA");
  if (!state.selectedCalendarDate || !days.includes(state.selectedCalendarDate)) {
    state.selectedCalendarDate = days.includes(today)
      ? today
      : (days.find((day) => day >= today) || days[0]);
  }
  const selectedDate = state.selectedCalendarDate;
  const selectedEvents = events.filter((row) => row.publish_date === selectedDate);

  const shell = el("div", "calendar-shell");
  const dayStrip = el("div", "calendar-day-strip");
  for (const day of days) {
    const dayButton = el("button", `calendar-day-chip${day === selectedDate ? " active" : ""}`);
    dayButton.type = "button";
    const date = new Date(`${day}T00:00:00`);
    dayButton.innerHTML = `<strong>${date.toLocaleDateString("ru-RU", { day: "2-digit", month: "short" })}</strong><span>${date.toLocaleDateString("ru-RU", { weekday: "short" })}</span>`;
    dayButton.onclick = () => {
      state.selectedCalendarDate = day;
      renderUploads();
    };
    dayStrip.append(dayButton);
  }
  shell.append(dayStrip);

  const timeline = el("div", "calendar-timeline");
  if (!selectedEvents.length) {
    timeline.append(el("div", "detail-empty", "На выбранную дату публикаций нет."));
  } else {
    for (const row of selectedEvents) {
      const item = el("div", "calendar-event-card");
      const left = el("div", "calendar-event-time");
      left.append(el("strong", "", row.publish_time || "—"));
      left.append(el("span", "subtle", row.account_name));

      const middle = el("div", "calendar-event-main");
      middle.append(el("div", "calendar-event-title", row.short_name));
      middle.append(el("div", "subtle", row.episode_base_name || row.serial_slug || ""));
      if (row.youtube_title) {
        middle.append(el("div", "subtle", row.youtube_title));
      }

      const right = el("div", "calendar-event-side");
      right.append(el("span", `badge ${row.status === "published" ? "ok" : "warn"}`, row.status));
      if (row.youtube_url) {
        const link = document.createElement("a");
        link.href = row.youtube_url;
        link.target = "_blank";
        link.rel = "noreferrer";
        link.textContent = "Открыть ролик";
        right.append(link);
      }

      item.append(left);
      item.append(middle);
      item.append(right);
      timeline.append(item);
    }
  }
  shell.append(timeline);

  root.innerHTML = "";
  root.append(shell);
}

function renderAnalytics() {
  const summaryRoot = document.getElementById("analyticsSummaryGrid");
  const accountsRoot = document.getElementById("analyticsAccountsList");
  const topRoot = document.getElementById("analyticsTopList");
  summaryRoot.innerHTML = "";
  accountsRoot.innerHTML = "";
  topRoot.innerHTML = "";

  const payload = state.youtubeAnalytics;
  if (!payload) {
    topRoot.append(el("div", "detail-empty", "Аналитика пока не загружена."));
    return;
  }

  const maxTrackedViews = Math.max(1, ...(payload.accounts || []).map((item) => item.trackedSummary?.views || 0));
  const maxSubscribers = Math.max(1, ...(payload.accounts || []).map((item) => item.channel?.subscribers || 0));
  const summaryMetrics = [
    ["Охват", formatCompactNumber(payload.summary.trackedViews), "просмотры наших шортсов"],
    ["Реакции", formatCompactNumber(payload.summary.trackedLikes + payload.summary.trackedComments), "лайки и комментарии"],
    ["Подписчики", formatCompactNumber(payload.summary.channelSubscribers), "по всем каналам"],
    ["Каналы", payload.summary.accounts, "с живой аналитикой"],
  ];
  const hero = el("div", "metric-card analytics-hero-card");
  const heroMain = el("div", "analytics-hero-main");
  const heroTitle = el("div");
  heroTitle.append(el("div", "metric-label", "Общая картина"));
  heroTitle.append(el("div", "analytics-hero-title", `${formatCompactNumber(payload.summary.trackedViews)} просмотров`));
  heroTitle.append(el("div", "metric-foot", "Живой срез по опубликованным шортсам на всех подключённых каналах"));
  heroMain.append(heroTitle);
  const heroStats = el("div", "analytics-hero-stats");
  for (const [label, value] of [["Лайки", formatCompactNumber(payload.summary.trackedLikes)], ["Комм.", formatCompactNumber(payload.summary.trackedComments)], ["Подписч.", formatCompactNumber(payload.summary.channelSubscribers)]]) {
    const stat = el("div", "analytics-hero-stat");
    stat.append(el("span", "analytics-mini-label", label));
    stat.append(el("strong", "analytics-mini-value", String(value)));
    heroStats.append(stat);
  }
  heroMain.append(heroStats);
  hero.append(heroMain);
  summaryRoot.append(hero);

  const compare = el("div", "metric-card analytics-compare-card");
  compare.append(el("div", "metric-label", "Сравнение каналов"));
  const compareList = el("div", "analytics-compare-list");
  for (const account of payload.accounts || []) {
    const row = el("div", "analytics-compare-row");
    row.append(el("strong", "analytics-compare-name", account.channel?.title || account.accountName));
    const track = el("div", "analytics-channel-track");
    const fill = el("div", "analytics-channel-fill analytics-channel-fill-views");
    fill.style.width = `${Math.max(10, Math.round(((account.trackedSummary?.views || 0) / maxTrackedViews) * 100))}%`;
    track.append(fill);
    row.append(track);
    row.append(el("span", "analytics-channel-bar-value", formatCompactNumber(account.trackedSummary?.views || 0)));
    compareList.append(row);
  }
  compare.append(compareList);
  summaryRoot.append(compare);

  const allTopVideos = [];
  for (const account of payload.accounts || []) {
    const card = el("div", "stack-card analytics-account-card");
    const top = el("div", "analytics-account-top");
    const titleWrap = el("div");
    titleWrap.append(el("h3", "", account.channel?.title || account.accountName));
    titleWrap.append(el("div", "subtle", account.credentialName || account.accountName));
    const badges = el("div", "row-meta");
    if (account.activeSerialName) badges.append(el("span", "badge neutral", account.activeSerialName));
    badges.append(el("span", "badge ok", account.error ? "частично недоступно" : "live data"));
    top.append(titleWrap);
    top.append(badges);
    card.append(top);

    if (account.error) {
      card.append(el("div", "subtle", account.error));
    }

    const visual = el("div", "analytics-channel-visual");
    const score = el("div", "analytics-score-orb");
    score.append(el("span", "analytics-score-label", "просмотры"));
    score.append(el("strong", "analytics-score-value", formatCompactNumber(account.trackedSummary?.views || 0)));
    visual.append(score);
    const bars = el("div", "analytics-bar-stack");
    const trackedBar = el("div", "analytics-channel-bar-row");
    trackedBar.append(el("span", "analytics-channel-bar-label", "Шортсы"));
    const trackedTrack = el("div", "analytics-channel-track");
    const trackedFill = el("div", "analytics-channel-fill analytics-channel-fill-views");
    trackedFill.style.width = `${Math.max(10, Math.round(((account.trackedSummary?.views || 0) / maxTrackedViews) * 100))}%`;
    trackedTrack.append(trackedFill);
    trackedBar.append(trackedTrack);
    trackedBar.append(el("strong", "analytics-channel-bar-value", formatCompactNumber(account.trackedSummary?.views || 0)));
    bars.append(trackedBar);

    const subscribersBar = el("div", "analytics-channel-bar-row");
    subscribersBar.append(el("span", "analytics-channel-bar-label", "Подписч."));
    const subscribersTrack = el("div", "analytics-channel-track");
    const subscribersFill = el("div", "analytics-channel-fill analytics-channel-fill-subs");
    subscribersFill.style.width = `${Math.max(10, Math.round(((account.channel?.subscribers || 0) / maxSubscribers) * 100))}%`;
    subscribersTrack.append(subscribersFill);
    subscribersBar.append(subscribersTrack);
    subscribersBar.append(el("strong", "analytics-channel-bar-value", formatCompactNumber(account.channel?.subscribers || 0)));
    bars.append(subscribersBar);

    const metaRow = el("div", "analytics-chip-row");
    metaRow.append(el("span", "badge neutral", `${formatCompactNumber(account.channel?.views || 0)} просмотров канала`));
    metaRow.append(el("span", "badge neutral", `${formatCompactNumber(account.channel?.videos || 0)} видео`));
    metaRow.append(el("span", "badge neutral", `${formatCompactNumber(account.trackedSummary?.videos || 0)} наших шортсов`));
    bars.append(metaRow);
    visual.append(bars);
    card.append(visual);

    const topTitle = el("div", "analytics-subtitle", "Лучшие шортсы канала");
    card.append(topTitle);
    const topVideosList = el("div", "analytics-video-list");
    const channelTop = [...(account.topVideos || [])].sort((a, b) => (b.views || 0) - (a.views || 0)).slice(0, 3);
    const channelLeader = channelTop[0]?.views || 1;
    if (!channelTop.length) {
      topVideosList.append(el("div", "subtle", "По этому каналу пока нечего показывать."));
    } else {
      for (const video of channelTop) {
        const row = el("div", "analytics-video-row");
        const left = el("div", "analytics-video-main");
        left.append(el("strong", "", video.youtube_title || video.short_name));
        const miniBar = el("div", "analytics-bar analytics-bar-thin");
        const miniFill = el("div", "analytics-bar-fill");
        miniFill.style.width = `${Math.max(10, Math.round(((video.views || 0) / channelLeader) * 100))}%`;
        miniBar.append(miniFill);
        left.append(miniBar);
        const right = el("div", "analytics-video-stats");
        right.append(el("span", "badge neutral", `${formatCompactNumber(video.views)}`));
        if (video.youtube_url) {
          const link = document.createElement("a");
          link.href = video.youtube_url;
          link.target = "_blank";
          link.rel = "noreferrer";
          link.textContent = "Открыть";
          right.append(link);
        }
        row.append(left);
        row.append(right);
        topVideosList.append(row);
      }
    }
    card.append(topVideosList);
    accountsRoot.append(card);

    for (const video of account.topVideos || []) {
      allTopVideos.push({ ...video, accountName: account.channel?.title || account.accountName });
    }
  }

  allTopVideos.sort((a, b) => (b.views || 0) - (a.views || 0));
  const leaderMax = allTopVideos[0]?.views || 1;
  if (!allTopVideos.length) {
    topRoot.append(el("div", "detail-empty", "Пока нет роликов для аналитики."));
  } else {
    const spotlight = allTopVideos[0];
    const heroTop = el("div", "stack-card analytics-top-hero");
    heroTop.append(el("div", "analytics-subtitle", "Главный лидер"));
    heroTop.append(el("h3", "", spotlight.youtube_title || spotlight.short_name));
    heroTop.append(el("div", "subtle", `${spotlight.accountName} • ${spotlight.short_name}`));
    heroTop.append(el("div", "analytics-top-hero-value", `${formatCompactNumber(spotlight.views)} просмотров`));
    const spotlightBar = el("div", "analytics-bar");
    const spotlightFill = el("div", "analytics-bar-fill");
    spotlightFill.style.width = "100%";
    spotlightBar.append(spotlightFill);
    heroTop.append(spotlightBar);
    if (spotlight.youtube_url) {
      const link = document.createElement("a");
      link.href = spotlight.youtube_url;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = "Открыть ролик";
      heroTop.append(link);
    }
    topRoot.append(heroTop);

    for (const video of allTopVideos.slice(1, 6)) {
      const card = el("div", "stack-card analytics-top-card compact");
      const top = el("div", "analytics-top-head");
      const left = el("div");
      left.append(el("strong", "", video.short_name));
      left.append(el("div", "subtle", video.accountName));
      const right = el("div", "analytics-top-metrics");
      right.append(el("span", "badge ok", `${formatCompactNumber(video.views)}`));
      top.append(left);
      top.append(right);
      card.append(top);
      const bar = el("div", "analytics-bar analytics-bar-thin");
      const fill = el("div", "analytics-bar-fill");
      fill.style.width = `${Math.max(8, Math.round(((video.views || 0) / leaderMax) * 100))}%`;
      bar.append(fill);
      card.append(bar);
      if (video.youtube_url) {
        const link = document.createElement("a");
        link.href = video.youtube_url;
        link.target = "_blank";
        link.rel = "noreferrer";
        link.textContent = "Открыть ролик";
        card.append(link);
      }
      topRoot.append(card);
    }
  }
}

async function saveActiveSerial(accountSlug, serialSlug) {
  await api(`/api/accounts/${encodeURIComponent(accountSlug)}/active-serial`, { method: "POST", body: JSON.stringify({ serialSlug }) });
  await loadAll();
  await loadAccount(accountSlug);
}

function normalizeSlotTime(value) {
  const match = String(value || "").trim().match(/^(\d{1,2}):(\d{2})$/);
  if (!match) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (!Number.isInteger(hours) || !Number.isInteger(minutes) || hours < 0 || hours > 23 || minutes < 0 || minutes > 59) return null;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
}

function parseSlotValue(rawValue) {
  const unique = new Set();
  return String(rawValue || "")
    .split(",")
    .map((item) => normalizeSlotTime(item))
    .filter((item) => {
      if (!item || unique.has(item)) return false;
      unique.add(item);
      return true;
    })
    .sort((a, b) => a.localeCompare(b));
}

async function deleteSerial(serial) {
  const serialName = serial?.serial_name || serial?.serial_slug || "этот сериал";
  if (!confirm(`Вы уверены, что хотите удалить сериал "${serialName}" и все его содержимое?`)) return;
  const result = await api(`/api/serials/${encodeURIComponent(serial.serial_slug)}/delete`, { method: "POST" });
  if (state.selectedSerial === serial.serial_slug) {
    state.selectedSerial = null;
    state.serialDetail = null;
    state.expandedEpisodes = new Set();
  }
  await loadAll();
  showToast(`Сериал удалён: ${result.serialName}`, "success");
}

async function clearCooldown(accountSlug) {
  await api(`/api/accounts/${encodeURIComponent(accountSlug)}/clear-cooldown`, { method: "POST" });
  await loadAll();
  await loadAccount(accountSlug);
  showToast("Активная блокировка снята.", "success");
}

async function createCooldown(accountSlug, reasonMessage, blockedUntil) {
  await api(`/api/accounts/${encodeURIComponent(accountSlug)}/cooldowns/create`, {
    method: "POST",
    body: JSON.stringify({ reasonMessage, blockedUntil }),
  });
  await loadAll();
  await loadAccount(accountSlug);
}

async function deleteCooldown(accountSlug, cooldownId) {
  await api(`/api/accounts/${encodeURIComponent(accountSlug)}/cooldowns/delete`, {
    method: "POST",
    body: JSON.stringify({ cooldownId }),
  });
  await loadAll();
  await loadAccount(accountSlug);
}

async function toggleAccount(accountSlug) {
  await api(`/api/accounts/${encodeURIComponent(accountSlug)}/toggle-active`, { method: "POST" });
  await loadAll();
  await loadAccount(accountSlug);
  showToast("Статус аккаунта обновлён.", "success");
}

async function saveSlots(accountSlug, rawValue) {
  const slots = Array.isArray(rawValue) ? rawValue.map((item) => normalizeSlotTime(item)).filter(Boolean) : parseSlotValue(rawValue);
  await api(`/api/accounts/${encodeURIComponent(accountSlug)}/schedule-slots`, { method: "POST", body: JSON.stringify({ slots }) });
  await loadAll();
  await loadAccount(accountSlug);
  showToast("Слоты публикации сохранены.", "success");
}

async function runWorkflow(kind) {
  setTab("workflows");
  const previousWorkflow = (state.workflows?.workflows || []).find((item) => item.runKey === kind);
  const previousExecutionId = previousWorkflow?.lastExecution?.id || null;
  state.workflowActionState[kind] = "starting";
  setStatusBar(kind === "render" ? "Запускаем Render Queue..." : "Запускаем Upload...", "loading");
  renderWorkflowStatus();
  try {
    await api(`/api/workflows/${kind}/run`, { method: "POST" });
    await waitForWorkflowStartOrFinish(kind, previousExecutionId, 4, 600);
    showToast("Команда на запуск отправлена.", "success");
  } finally {
    delete state.workflowActionState[kind];
    renderWorkflowStatus();
  }
}

async function stopWorkflow(kind) {
  if (!confirm("Остановить текущий запуск? Это перезапустит n8n и прервёт активные выполнения.")) return;
  setTab("workflows");
  state.workflowActionState[kind] = "stopping";
  setStatusBar("Останавливаем workflow...", "loading");
  renderWorkflowStatus();
  try {
    await api(`/api/workflows/${kind}/stop`, { method: "POST" });
    await waitForWorkflowState(kind, false, 12, 1500);
    showToast("Workflow остановлен.", "success");
  } finally {
    delete state.workflowActionState[kind];
    renderWorkflowStatus();
  }
}

async function waitForWorkflowState(kind, shouldBeRunning, attempts = 8, delayMs = 1000) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    await new Promise((resolve) => window.setTimeout(resolve, delayMs));
    try {
      await loadWorkflows();
    } catch (error) {
      if (attempt === attempts - 1) throw error;
      continue;
    }
    const workflow = (state.workflows?.workflows || []).find((item) => item.runKey === kind);
    if (workflow && Boolean(workflow.isRunning) === shouldBeRunning) return;
  }
}

async function waitForWorkflowStartOrFinish(kind, previousExecutionId, attempts = 8, delayMs = 1000) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    await new Promise((resolve) => window.setTimeout(resolve, delayMs));
    try {
      await loadWorkflows();
    } catch (error) {
      if (attempt === attempts - 1) throw error;
      continue;
    }
    const workflow = (state.workflows?.workflows || []).find((item) => item.runKey === kind);
    if (!workflow) continue;
    const currentExecutionId = workflow.lastExecution?.id || null;
    if (workflow.isRunning) return;
    if (currentExecutionId && currentExecutionId !== previousExecutionId) return;
  }
}

function toggleErrorSelection(executionId) {
  if (state.selectedErrorIds.has(executionId)) state.selectedErrorIds.delete(executionId);
  else state.selectedErrorIds.add(executionId);
  renderWorkflowErrors();
}

function toggleAllErrorSelections() {
  const errorIds = (state.workflows?.errors || []).map((item) => item.execution_id);
  const shouldSelectAll = errorIds.some((id) => !state.selectedErrorIds.has(id));
  state.selectedErrorIds = shouldSelectAll ? new Set(errorIds) : new Set();
  renderWorkflowErrors();
}

async function deleteSelectedErrors() {
  if (!state.selectedErrorIds.size) {
    showToast("Сначала выбери хотя бы одну ошибку.", "warning");
    return;
  }
  if (!confirm("Удалить выбранные error execution из истории?")) return;
  await api("/api/workflows/errors/delete", {
    method: "POST",
    body: JSON.stringify({ executionIds: [...state.selectedErrorIds] }),
  });
  state.selectedErrorIds = new Set();
  await loadWorkflows();
  showToast("Выбранные ошибки удалены.", "success");
}

function addHoursLocal(date, hours) {
  const next = new Date(date.getTime());
  next.setHours(next.getHours() + hours);
  return next;
}

function toDateTimeLocalValue(date) {
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function toggleAccountSection(sectionKey) {
  if (state.expandedAccountSections.has(sectionKey)) state.expandedAccountSections.delete(sectionKey);
  else state.expandedAccountSections.add(sectionKey);
  renderAccountDetail();
}

function openCooldownModal(accountSlug, accountName) {
  state.cooldownModalAccountSlug = accountSlug;
  const modal = document.getElementById("cooldownModal");
  document.getElementById("cooldownModalAccountLabel").textContent = `Аккаунт: ${accountName}`;
  document.getElementById("cooldownReasonInput").value = "";
  document.getElementById("cooldownUntilInput").value = toDateTimeLocalValue(addHoursLocal(new Date(), 24));
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden", "false");
}

function closeCooldownModal() {
  state.cooldownModalAccountSlug = null;
  const modal = document.getElementById("cooldownModal");
  modal.classList.add("hidden");
  modal.setAttribute("aria-hidden", "true");
}

function openSerialModal() {
  state.serialModalOpen = true;
  const modal = document.getElementById("serialModal");
  document.getElementById("serialNameInput").value = "";
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden", "false");
}

function closeSerialModal() {
  state.serialModalOpen = false;
  const modal = document.getElementById("serialModal");
  modal.classList.add("hidden");
  modal.setAttribute("aria-hidden", "true");
}

async function openAccountModal() {
  state.accountModalOpen = true;
  if (!state.youtubeCredentials.length) {
    const result = await api("/api/n8n/credentials/youtube");
    state.youtubeCredentials = result.credentials || [];
  }
  const modal = document.getElementById("accountModal");
  document.getElementById("accountNameInput").value = "";
  const credentialSelect = document.getElementById("accountCredentialSelect");
  credentialSelect.innerHTML = "";
  for (const credential of state.youtubeCredentials) {
    const option = document.createElement("option");
    option.value = credential.id;
    option.textContent = credential.assigned_account_name
      ? `${credential.name} — занято: ${credential.assigned_account_name}`
      : credential.name;
    option.disabled = Boolean(credential.assigned_account_name);
    credentialSelect.append(option);
  }
  const firstAvailableCredential = state.youtubeCredentials.find((credential) => !credential.assigned_account_name);
  if (firstAvailableCredential) credentialSelect.value = firstAvailableCredential.id;
  const serialSelect = document.getElementById("accountSerialSelect");
  serialSelect.innerHTML = "";
  for (const serial of state.dashboard.media.serials) {
    const option = document.createElement("option");
    option.value = serial.serial_slug;
    option.textContent = serial.serial_name;
    serialSelect.append(option);
  }
  if (!firstAvailableCredential) {
    showToast("Нет свободных YouTube credential. Сначала создай новый credential в n8n.", "warning");
  }
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden", "false");
}

function closeAccountModal() {
  state.accountModalOpen = false;
  const modal = document.getElementById("accountModal");
  modal.classList.add("hidden");
  modal.setAttribute("aria-hidden", "true");
}

async function submitAccountModal() {
  const accountName = document.getElementById("accountNameInput").value.trim();
  const credentialId = document.getElementById("accountCredentialSelect").value;
  const serialSlug = document.getElementById("accountSerialSelect").value;
  if (!accountName) {
    showToast("Укажи название аккаунта.", "warning");
    return;
  }
  if (!credentialId) {
    showToast("Выбери YouTube credential.", "warning");
    return;
  }
  if (!serialSlug) {
    showToast("Выбери активный сериал.", "warning");
    return;
  }
  const result = await api("/api/accounts/create", {
    method: "POST",
    body: JSON.stringify({
      accountName,
      credentialId,
      serialSlug,
      slots: ["08:50", "11:50", "13:50", "15:50", "18:50"],
    }),
  });
  closeAccountModal();
  await loadAll();
  await loadAccount(result.account.account_slug);
  showToast("Аккаунт создан и добавлен в upload workflow.", "success");
}

async function submitSerialModal() {
  const name = document.getElementById("serialNameInput").value.trim();
  if (!name) {
    showToast("Укажи название сериала.", "warning");
    return;
  }
  const result = await api("/api/serials/create", {
    method: "POST",
    body: JSON.stringify({ name }),
  });
  closeSerialModal();
  await loadAll();
  await loadSerial(result.serial.serial_slug);
  showToast("Сериал создан.", "success");
}

function openEpisodeUploadModal(serialSlug, serialName, defaults = {}) {
  state.uploadModalSerialSlug = serialSlug;
  state.uploadModalFile = null;
  document.getElementById("episodeUploadSerialLabel").textContent = `Сериал: ${serialName}`;
  document.getElementById("episodeNameInput").value = defaults.defaultEpisodeStem || "";
  document.getElementById("episodeSelectedFile").textContent = "Файл пока не выбран";
  document.getElementById("episodeFileInput").value = "";
  const modal = document.getElementById("episodeUploadModal");
  modal.classList.remove("hidden");
  modal.setAttribute("aria-hidden", "false");
}

function closeEpisodeUploadModal() {
  state.uploadModalSerialSlug = null;
  state.uploadModalFile = null;
  const modal = document.getElementById("episodeUploadModal");
  modal.classList.add("hidden");
  modal.setAttribute("aria-hidden", "true");
}

function setUploadModalFile(file) {
  if (!file) return;
  if (!String(file.name || "").toLowerCase().endsWith(".mp4")) {
    showToast("Можно загружать только mp4.", "warning");
    return;
  }
  state.uploadModalFile = file;
  document.getElementById("episodeSelectedFile").textContent = `Выбран файл: ${file.name}`;
}

async function submitEpisodeUploadModal() {
  if (!state.uploadModalSerialSlug) return;
  if (!state.uploadModalFile) {
    showToast("Сначала выбери mp4 файл.", "warning");
    return;
  }
  const desiredName = document.getElementById("episodeNameInput").value.trim();
  const result = await apiUpload(
    `/api/serials/${encodeURIComponent(state.uploadModalSerialSlug)}/episodes/upload`,
    state.uploadModalFile,
    {
      "X-Filename": state.uploadModalFile.name,
      "X-Desired-Name": desiredName,
    },
  );
  closeEpisodeUploadModal();
  await loadAll();
  await loadSerial(state.uploadModalSerialSlug);
  showToast(`Серия загружена: ${result.fileName}`, "success");
}

async function submitCooldownModal() {
  const accountSlug = state.cooldownModalAccountSlug;
  if (!accountSlug) return;
  const reasonMessage = document.getElementById("cooldownReasonInput").value.trim();
  const blockedUntilLocal = document.getElementById("cooldownUntilInput").value;
  if (!reasonMessage) {
    showToast("Укажи причину блокировки.", "warning");
    return;
  }
  if (!blockedUntilLocal) {
    showToast("Укажи дату и время окончания блокировки.", "warning");
    return;
  }
  const blockedUntil = new Date(blockedUntilLocal);
  if (Number.isNaN(blockedUntil.getTime())) {
    showToast("Некорректная дата или время блокировки.", "error");
    return;
  }
  if (blockedUntil.getTime() <= Date.now()) {
    showToast("Дата окончания блокировки должна быть в будущем.", "warning");
    return;
  }
  await createCooldown(accountSlug, reasonMessage, blockedUntil.toISOString());
  closeCooldownModal();
  showToast("Блокировка сохранена.", "success");
}

function renderAccounts() {
  const root = document.getElementById("accountsList");
  root.innerHTML = "";
  const addCard = el("button", "account-card add-account-card", "+");
  addCard.type = "button";
  addCard.title = "Добавить YouTube аккаунт";
  addCard.onclick = () => openAccountModal().catch((error) => showToast(error.message, "error"));
  root.append(addCard);
  const serialOptions = state.dashboard.media.serials;
  for (const account of state.dashboard.accounts) {
    const card = el("div", `account-card${state.selectedAccount === account.account_slug ? " active selected-pulse" : ""}`);
    card.onclick = (event) => {
      const interactive = event.target.closest("button, select, input, a");
      if (interactive) return;
      loadAccount(account.account_slug).catch((error) => showToast(error.message, "error"));
    };
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
    meta.append(
      el(
        "span",
        "",
        `В активном сериале осталось шортсов: ${account.remaining_uploadable_total ?? 0} из ${account.active_serial_short_total ?? 0}`
      )
    );
    card.append(meta);
    const controls = el("div", "account-controls-grid");
    const serialBlock = el("div", "account-control-block");
    serialBlock.append(el("div", "account-section-title", "Активный сериал"));
    const selectWrap = el("div", "select-row select-row-inline");
    const select = document.createElement("select");
    for (const serial of serialOptions) {
      const option = document.createElement("option");
      option.value = serial.serial_slug;
      option.textContent = serial.serial_name;
      option.selected = serial.serial_slug === account.active_serial_slug;
      select.append(option);
    }
    const saveSerialButton = el("button", "secondary-button account-primary-action", "Сохранить сериал");
    saveSerialButton.onclick = () => saveActiveSerial(account.account_slug, select.value);
    selectWrap.append(select);
    selectWrap.append(saveSerialButton);
    serialBlock.append(selectWrap);
    controls.append(serialBlock);

    const actionsBlock = el("div", "account-control-block");
    actionsBlock.append(el("div", "account-section-title", "Управление"));
    const actions = el("div", "account-actions-toolbar");
    const toggleButton = el("button", "account-uniform-button", account.is_active ? "Выключить" : "Включить");
    toggleButton.title = account.is_active
      ? "Исключить аккаунт из автоподбора и загрузок"
      : "Вернуть аккаунт в автоподбор и загрузки";
    toggleButton.onclick = () => toggleAccount(account.account_slug);
    actions.append(toggleButton);
    if (account.is_blocked) {
      const clearButton = el("button", "account-uniform-button", "Разблокировать");
      clearButton.onclick = () => clearCooldown(account.account_slug);
      actions.append(clearButton);
    }
    const timeoutButton = el("button", "account-uniform-button", "Выдать timeout");
    timeoutButton.onclick = () => openCooldownModal(account.account_slug, account.account_name);
    actions.append(timeoutButton);
    actionsBlock.append(actions);
    controls.append(actionsBlock);
    card.append(controls);
    root.append(card);
  }
}

function renderSerialDetail() {
  const root = document.getElementById("serialDetail");
  root.innerHTML = "";
  if (!state.serialDetail) {
    root.textContent = "Выбери сериал, чтобы увидеть эпизоды и список шортсов.";
    root.className = "detail-empty panel-scroll scroll-xl";
    return;
  }
  root.className = "episode-list panel-scroll scroll-xl";
  const uploadCard = el("button", "episode-card add-episode-card", "+");
  uploadCard.type = "button";
  uploadCard.title = "Загрузить новую серию";
  uploadCard.onclick = () =>
    openEpisodeUploadModal(
      state.serialDetail.serial.serial_slug,
      state.serialDetail.serial.serial_name,
      state.serialDetail.uploadDefaults || {},
    );
  root.append(uploadCard);
  for (const episode of state.serialDetail.episodes) {
    const expanded = state.expandedEpisodes.has(episode.episode_base_name);
    const card = el("div", `episode-card episode-accordion${expanded ? " expanded" : ""}`);
    const header = el("button", "episode-summary");
    header.type = "button";
    header.onclick = () => {
      if (state.expandedEpisodes.has(episode.episode_base_name)) {
        state.expandedEpisodes.clear();
      } else {
        state.expandedEpisodes.clear();
        state.expandedEpisodes.add(episode.episode_base_name);
      }
      renderSerialDetail();
    };
    const titleWrap = el("div", "episode-summary-main");
    titleWrap.append(el("h3", "", episode.episode_base_name));
    const meta = el("div", "row-meta");
    meta.append(el("span", "badge neutral", `Шортсов: ${episode.short_count}`));
    meta.append(el("span", "badge neutral", `Chunks: ${episode.chunk_count}`));
    titleWrap.append(meta);
    header.append(titleWrap);
    header.append(el("span", "episode-toggle", expanded ? "−" : "+"));
    card.append(header);
    const content = el("div", `episode-content${expanded ? " expanded" : ""}`);
    const shorts = el("div", "short-list");
    for (const short of episode.shorts.slice(0, 6)) {
      const item = el("div", "short-item");
      item.innerHTML = `<strong>${short.short_name}</strong><div class="subtle">part ${short.short_part} • ${Number(short.duration || 0).toFixed(1)}s</div><div class="subtle">${(short.text || "").slice(0, 120)}</div>`;
      shorts.append(item);
    }
    if (episode.shorts.length > 6) shorts.append(el("div", "subtle", `И ещё ${episode.shorts.length - 6} шортсов...`));
    content.append(shorts);
    card.append(content);
    root.append(card);
  }
}

function renderAccountDetail() {
  const root = document.getElementById("accountDetail");
  root.innerHTML = "";
  if (!state.accountDetail) {
    root.textContent = "Выбери аккаунт справа, чтобы увидеть историю и настройки.";
    root.className = "detail-empty panel-scroll scroll-xl";
    return;
  }
  root.className = "panel-scroll scroll-xl detail-stack";
  const { account, history, cooldowns, scheduleSlots } = state.accountDetail;
  const header = el("div", "stack-card account-detail-header");
  const headerMain = el("div", "account-detail-header-main");
  headerMain.innerHTML = `<h3>${account.account_name}</h3><div class="subtle">${account.youtube_credential_name || "no credential"}</div>`;
  const headerMeta = el("div", "row-meta account-detail-header-meta");
  headerMeta.append(el("span", "badge neutral", account.active_serial_name || "serial not selected"));
  headerMeta.append(el("span", `badge ${account.is_active ? "ok" : "neutral"}`, account.is_active ? "active" : "disabled"));
  header.append(headerMain);
  header.append(headerMeta);
  root.append(header);
  const scheduleExpanded = state.expandedAccountSections.has("slots");
  const scheduleCard = el("div", `stack-card episode-accordion account-section-accordion${scheduleExpanded ? " expanded" : ""}`);
  const scheduleHeader = el("button", "episode-summary account-section-summary");
  scheduleHeader.type = "button";
  scheduleHeader.onclick = () => toggleAccountSection("slots");
  const scheduleTitleWrap = el("div", "episode-summary-main");
  scheduleTitleWrap.append(el("h3", "", "Слоты публикации"));
  const scheduleMeta = el("div", "row-meta");
  scheduleMeta.append(el("span", "badge neutral", `${scheduleSlots.length} слотов`));
  scheduleTitleWrap.append(scheduleMeta);
  scheduleHeader.append(scheduleTitleWrap);
  scheduleHeader.append(el("span", "episode-toggle", scheduleExpanded ? "−" : "+"));
  scheduleCard.append(scheduleHeader);
  const scheduleContent = el("div", `episode-content${scheduleExpanded ? " expanded" : ""}`);
  const editor = el("div", "schedule-editor schedule-editor-pretty");
  const toolbar = el("div", "schedule-toolbar");
  const picker = el("div", "slot-time-picker");
  const hourSelect = document.createElement("select");
  hourSelect.className = "slot-select";
  for (let hour = 0; hour < 24; hour += 1) {
    const option = document.createElement("option");
    option.value = String(hour).padStart(2, "0");
    option.textContent = String(hour).padStart(2, "0");
    hourSelect.append(option);
  }
  const minuteSelect = document.createElement("select");
  minuteSelect.className = "slot-select";
  for (const minute of ["00", "05", "10", "15", "20", "25", "30", "35", "40", "45", "50", "55"]) {
    const option = document.createElement("option");
    option.value = minute;
    option.textContent = minute;
    minuteSelect.append(option);
  }
  const initialSlot = normalizeSlotTime(scheduleSlots[0]?.slot_time || "08:50") || "08:50";
  const [initialHour, initialMinute] = initialSlot.split(":");
  hourSelect.value = initialHour;
  minuteSelect.value = initialMinute;
  picker.append(hourSelect);
  picker.append(el("span", "slot-picker-separator", ":"));
  picker.append(minuteSelect);
  const addButton = el("button", "secondary-button slot-add-button", "Добавить слот");
  addButton.onclick = async () => {
    const normalized = normalizeSlotTime(`${hourSelect.value}:${minuteSelect.value}`);
    if (!normalized) {
      showToast("Выбери корректное время слота.", "warning");
      return;
    }
    const currentSlots = scheduleSlots.map((slot) => slot.slot_time);
    if (currentSlots.includes(normalized)) {
      showToast("Такой слот уже существует.", "warning");
      return;
    }
    await saveSlots(account.account_slug, [...currentSlots, normalized]);
  };
  toolbar.append(picker);
  toolbar.append(addButton);
  editor.append(toolbar);
  const chips = el("div", "schedule-grid schedule-grid-live");
  if (!scheduleSlots.length) {
    chips.append(el("div", "subtle", "Слотов пока нет."));
  } else {
    for (const slot of scheduleSlots) {
      const chip = el("div", "slot-chip slot-chip-removable");
      chip.append(el("span", "slot-chip-label", slot.slot_time));
      const removeButton = el("button", "slot-chip-remove", "×");
      removeButton.type = "button";
      removeButton.title = `Удалить слот ${slot.slot_time}`;
      removeButton.onclick = async (event) => {
        event.stopPropagation();
        if (!confirm(`Вы уверены, что хотите удалить слот ${slot.slot_time}?`)) return;
        const nextSlots = scheduleSlots.map((item) => item.slot_time).filter((item) => item !== slot.slot_time);
        await saveSlots(account.account_slug, nextSlots);
      };
      chip.append(removeButton);
      chips.append(chip);
    }
  }
  scheduleContent.append(editor);
  scheduleContent.append(chips);
  scheduleCard.append(scheduleContent);
  root.append(scheduleCard);
  const cooldownExpanded = state.expandedAccountSections.has("cooldowns");
  const cooldownCard = el("div", `stack-card episode-accordion account-section-accordion${cooldownExpanded ? " expanded" : ""}`);
  const cooldownHead = el("button", "episode-summary account-section-summary");
  cooldownHead.type = "button";
  cooldownHead.onclick = () => toggleAccountSection("cooldowns");
  const cooldownTitleWrap = el("div", "episode-summary-main");
  cooldownTitleWrap.append(el("h3", "", "История блокировок"));
  const cooldownMeta = el("div", "row-meta");
  cooldownMeta.append(el("span", "badge neutral", `${cooldowns.length} записей`));
  if (cooldowns.some((item) => item.is_current_active)) cooldownMeta.append(el("span", "badge bad", "есть активная"));
  cooldownTitleWrap.append(cooldownMeta);
  const cooldownHeadActions = el("div", "account-actions");
  const addCooldownButton = el("button", "mini-button", "Выдать timeout");
  addCooldownButton.type = "button";
  addCooldownButton.onclick = (event) => {
    event.stopPropagation();
    openCooldownModal(account.account_slug, account.account_name);
  };
  if (cooldowns.some((item) => item.is_current_active)) {
    const clearActiveButton = el("button", "mini-button", "Снять активную");
    clearActiveButton.type = "button";
    clearActiveButton.onclick = (event) => {
      event.stopPropagation();
      clearCooldown(account.account_slug);
    };
    cooldownHeadActions.append(clearActiveButton);
  }
  cooldownHeadActions.append(addCooldownButton);
  cooldownTitleWrap.append(cooldownHeadActions);
  cooldownHead.append(cooldownTitleWrap);
  cooldownHead.append(el("span", "episode-toggle", cooldownExpanded ? "−" : "+"));
  cooldownCard.append(cooldownHead);
  const cooldownContent = el("div", `episode-content${cooldownExpanded ? " expanded" : ""}`);
  const cooldownList = el("div", "cooldown-list");
  if (!cooldowns.length) cooldownList.append(el("div", "subtle", "Блокировок пока не было."));
  else for (const row of cooldowns.slice(0, 10)) {
    const item = el("div", "short-item");
    const top = el("div", "account-top");
    const titleWrap = el("div");
    titleWrap.innerHTML = `<strong>${row.reason_code}</strong><div class="subtle">${row.source_node_name || "unknown source"}</div>`;
    top.append(titleWrap);
    const rowActions = el("div", "account-actions");
    if (row.is_current_active) rowActions.append(el("span", "badge bad", "активна"));
    else rowActions.append(el("span", "badge neutral", "история"));
    const deleteButton = el("button", "mini-button", "Удалить");
    deleteButton.onclick = async () => {
      if (!confirm("Удалить эту запись блокировки?")) return;
      await deleteCooldown(account.account_slug, row.id)
        .then(() => showToast("Запись блокировки удалена.", "success"))
        .catch((error) => showToast(error.message, "error"));
    };
    rowActions.append(deleteButton);
    top.append(rowActions);
    item.append(top);
    item.append(el("div", "subtle", `до ${formatDateTime(row.blocked_until)}`));
    if (row.reason_message) item.append(el("div", "subtle", row.reason_message));
    cooldownList.append(item);
  }
  cooldownContent.append(cooldownList);
  cooldownCard.append(cooldownContent);
  root.append(cooldownCard);
  const historyExpanded = state.expandedAccountSections.has("history");
  const historyCard = el("div", `stack-card episode-accordion account-section-accordion${historyExpanded ? " expanded" : ""}`);
  const historyHead = el("button", "episode-summary account-section-summary");
  historyHead.type = "button";
  historyHead.onclick = () => toggleAccountSection("history");
  const historyTitleWrap = el("div", "episode-summary-main");
  historyTitleWrap.append(el("h3", "", "История загрузок"));
  const historyMeta = el("div", "row-meta");
  historyMeta.append(el("span", "badge neutral", `${history.length} записей`));
  historyTitleWrap.append(historyMeta);
  historyHead.append(historyTitleWrap);
  historyHead.append(el("span", "episode-toggle", historyExpanded ? "−" : "+"));
  historyCard.append(historyHead);
  const historyContent = el("div", `episode-content${historyExpanded ? " expanded" : ""}`);
  const historyList = el("div", "history-list");
  if (!history.length) historyList.append(el("div", "subtle", "История публикаций пока пустая."));
  else for (const row of history.slice(0, 30)) {
    const item = el("div", "history-row");
    const left = el("div", "history-row-main");
    left.innerHTML = `<strong>${row.short_name}</strong><div class="subtle">${row.episode_base_name || row.serial_slug || ""}</div>`;
    const right = el("div", "history-row-side");
    const badges = el("div", "row-meta history-row-badges");
    badges.append(el("span", `badge ${row.status === "published" ? "ok" : "warn"}`, row.status));
    badges.append(el("span", "badge neutral", row.publish_at_local || "no publish time"));
    right.append(badges);
    if (row.youtube_url) {
      const link = document.createElement("a");
      link.href = row.youtube_url;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = "Открыть ролик";
      right.append(link);
    }
    item.append(left);
    item.append(right);
    historyList.append(item);
  }
  historyContent.append(historyList);
  historyCard.append(historyContent);
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
    const pendingAction = state.workflowActionState[workflow.runKey];
    const card = el("div", `stack-card workflow-card workflow-card-${workflow.statusKey || "scheduled"}`);
    const header = el("div", "workflow-card-top");
    const titleWrap = el("div");
    titleWrap.append(el("h3", "", workflow.name));
    titleWrap.append(el("div", "subtle", pendingAction === "starting" ? "Отправляем команду на запуск..." : pendingAction === "stopping" ? "Останавливаем текущий запуск..." : workflow.statusDetail || ""));
    header.append(titleWrap);
    if (workflow.canRunManually) {
      const actions = el("div", "workflow-actions");
      const runButton = el("button", "icon-button", "▶");
      runButton.title = workflow.runKey === "render" ? "Запустить Render Queue" : "Запустить Upload";
      runButton.disabled = workflow.isRunning || pendingAction === "starting" || pendingAction === "stopping";
      runButton.onclick = () => runWorkflow(workflow.runKey).catch((error) => showToast(error.message, "error"));
      actions.append(runButton);
      const retryButton = el("button", "icon-button", "↻");
      retryButton.title = "Повторить запуск";
      retryButton.disabled = workflow.isRunning || pendingAction === "starting" || pendingAction === "stopping";
      retryButton.onclick = () => runWorkflow(workflow.runKey).catch((error) => showToast(error.message, "error"));
      actions.append(retryButton);
      if (workflow.isRunning || pendingAction === "stopping") {
        const stopButton = el("button", "icon-button danger-button", "■");
        stopButton.title = "Остановить текущий запуск";
        stopButton.disabled = pendingAction === "stopping";
        stopButton.onclick = () => stopWorkflow(workflow.runKey).catch((error) => showToast(error.message, "error"));
        actions.append(stopButton);
      }
      header.append(actions);
    }
    card.append(header);
    const badges = el("div", "row-meta");
    const statusBadgeClass = workflow.statusKey === "running" ? "warn" : workflow.statusKey === "attention" ? "bad" : workflow.statusKey === "inactive" ? "neutral" : "ok";
    badges.append(el("span", `badge ${statusBadgeClass}`, pendingAction === "starting" ? "Запускаем..." : pendingAction === "stopping" ? "Останавливаем..." : workflow.statusLabel || "Статус"));
    if (workflow.runSource) {
      const sourceLabel = workflow.runSource === "webhook"
        ? "вручную"
        : workflow.runSource === "schedule"
          ? "по расписанию"
          : workflow.runSource === "user-manual"
            ? "вручную"
            : workflow.runSource;
      badges.append(el("span", "badge neutral", sourceLabel));
    } else if (workflow.active) {
      badges.append(el("span", "badge neutral", "по расписанию"));
    }
    if (workflow.currentStepIndex && workflow.stepTotal) {
      badges.append(el("span", "badge neutral", `Шаг ${workflow.currentStepIndex}/${workflow.stepTotal}`));
    }
    if (workflow.currentNode) {
      badges.append(el("span", "badge neutral", workflow.currentNode));
    }
    const meta = el("div", "account-meta");
    meta.append(el("span", "", `Последний старт: ${formatDateTime(workflow.lastExecution?.startedAt)}`));
    meta.append(el("span", "", `Последнее обновление: ${formatDateTime(workflow.lastEventAt || workflow.lastExecution?.stoppedAt || workflow.updatedAt)}`));
    if (workflow.runSource) meta.append(el("span", "", `Источник: ${workflow.runSource === "webhook" ? "ручной запуск" : workflow.runSource}`));
    card.append(badges);
    if (workflow.progressPercent) {
      const progress = el("div", "workflow-progress");
      const fill = el("div", "workflow-progress-fill");
      fill.style.width = `${workflow.progressPercent}%`;
      progress.append(fill);
      card.append(progress);
    }
    card.append(meta);
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
    const top = el("div", "selection-row");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.selectedErrorIds.has(row.execution_id);
    checkbox.addEventListener("change", () => toggleErrorSelection(row.execution_id));
    const label = el("span", "selection-label", `Execution #${row.execution_id}`);
    top.append(checkbox);
    top.append(label);
    card.append(top);
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

async function loadAnalytics() {
  state.youtubeAnalytics = await api("/api/youtube-analytics");
  renderAnalytics();
}

async function loadAll() {
  const [dashboardResult, workflowsResult, analyticsResult] = await Promise.allSettled([
    api("/api/dashboard"),
    api("/api/workflows"),
    api("/api/youtube-analytics"),
  ]);
  if (dashboardResult.status !== "fulfilled") throw dashboardResult.reason;
  if (workflowsResult.status !== "fulfilled") throw workflowsResult.reason;
  state.dashboard = dashboardResult.value;
  state.workflows = workflowsResult.value;
  if (analyticsResult.status === "fulfilled") {
    state.youtubeAnalytics = analyticsResult.value;
    renderAnalytics();
  } else {
    state.youtubeAnalytics = null;
    renderAnalytics();
    showToast(`YouTube Analytics временно недоступна: ${analyticsResult.reason.message}`, "warning");
  }
  setLastUpdated(state.dashboard.generatedAt);
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

document.getElementById("refreshButton").addEventListener("click", () => loadAll().catch((error) => showToast(error.message, "error")));
document.getElementById("selectAllErrorsButton").addEventListener("click", toggleAllErrorSelections);
document.getElementById("deleteSelectedErrorsButton").addEventListener("click", () => deleteSelectedErrors().catch((error) => showToast(error.message, "error")));
document.getElementById("saveCooldownButton").addEventListener("click", () => submitCooldownModal().catch((error) => showToast(error.message, "error")));
document.getElementById("cancelCooldownButton").addEventListener("click", closeCooldownModal);
document.getElementById("closeCooldownModalButton").addEventListener("click", closeCooldownModal);
document.getElementById("saveSerialButton").addEventListener("click", () => submitSerialModal().catch((error) => showToast(error.message, "error")));
document.getElementById("cancelSerialButton").addEventListener("click", closeSerialModal);
document.getElementById("closeSerialModalButton").addEventListener("click", closeSerialModal);
document.getElementById("saveAccountButton").addEventListener("click", () => submitAccountModal().catch((error) => showToast(error.message, "error")));
document.getElementById("cancelAccountButton").addEventListener("click", closeAccountModal);
document.getElementById("closeAccountModalButton").addEventListener("click", closeAccountModal);
document.getElementById("uploadEpisodeButton").addEventListener("click", () => submitEpisodeUploadModal().catch((error) => showToast(error.message, "error")));
document.getElementById("cancelEpisodeUploadButton").addEventListener("click", closeEpisodeUploadModal);
document.getElementById("closeEpisodeUploadModalButton").addEventListener("click", closeEpisodeUploadModal);
document.querySelectorAll("[data-close-account-modal='true']").forEach((node) => {
  node.addEventListener("click", closeAccountModal);
});
document.querySelectorAll("[data-close-serial-modal='true']").forEach((node) => {
  node.addEventListener("click", closeSerialModal);
});
document.querySelectorAll("[data-close-upload-modal='true']").forEach((node) => {
  node.addEventListener("click", closeEpisodeUploadModal);
});
document.querySelectorAll("[data-close-modal='true']").forEach((node) => {
  node.addEventListener("click", closeCooldownModal);
});
document.getElementById("browseEpisodeFileButton").addEventListener("click", () => {
  document.getElementById("episodeFileInput").click();
});
document.getElementById("episodeFileInput").addEventListener("change", (event) => {
  setUploadModalFile(event.target.files?.[0]);
});
const episodeDropzone = document.getElementById("episodeDropzone");
episodeDropzone.addEventListener("dragover", (event) => {
  event.preventDefault();
  episodeDropzone.classList.add("dragover");
});
episodeDropzone.addEventListener("dragleave", () => {
  episodeDropzone.classList.remove("dragover");
});
episodeDropzone.addEventListener("drop", (event) => {
  event.preventDefault();
  episodeDropzone.classList.remove("dragover");
  setUploadModalFile(event.dataTransfer?.files?.[0]);
});
document.querySelectorAll(".tab-button").forEach((button) => {
  button.addEventListener("click", () => setTab(button.dataset.tab));
});

setTab("overview");
loadAll().catch((error) => showToast(error.message, "error"));
window.setInterval(() => {
  if (state.activeTab === "workflows" || (state.workflows?.workflows || []).some((item) => item.isRunning)) {
    loadWorkflows().catch(() => {});
  }
}, 3000);
