const translations = window.OLDTONEFIX_TRANSLATIONS;
const form = document.querySelector("#denoise-form");
const sliders = [...document.querySelectorAll('input[type="range"]')];
const languageButtons = [...document.querySelectorAll("[data-language]")];
const health = document.querySelector("#health");
const healthText = document.querySelector("#health-text");
const startButton = document.querySelector("#start-button");
const stopButton = document.querySelector("#stop-button");
const resetButton = document.querySelector("#reset-tuning");
const jobStatus = document.querySelector("#job-status");
const command = document.querySelector("#command");
const log = document.querySelector("#log");
const tuningPages = [...document.querySelectorAll(".tuning-page")];
const pageButtons = [...document.querySelectorAll(".page-button")];
const previousPageButton = document.querySelector("#page-prev");
const nextPageButton = document.querySelector("#page-next");
const pageStatus = document.querySelector("#page-status");
const jobProgress = document.querySelector("#job-progress");
const progressFill = document.querySelector("#progress-fill");
const progressLabel = document.querySelector("#progress-label");
const progressText = document.querySelector("#progress-text");
const rangeThumbSize = 19;

let activeJobId = null;
let pollTimer = null;
let currentTuningPage = 0;
let currentLanguage = "zh";
let currentStatus = "idle";
let currentStatusKey = null;
let lastProgress = {};
let lastJob = null;
let healthView = { state: "checking", key: "health.checking", values: {} };

function t(key, values = {}) {
  const dictionary = translations[currentLanguage] || translations.zh;
  const template = dictionary[key] || translations.zh[key] || key;
  return Object.entries(values).reduce(
    (text, [name, value]) => text.replaceAll(`{${name}}`, String(value)),
    template,
  );
}

function setTranslatedMessage(element, key) {
  element.dataset.i18nMessage = key;
  element.textContent = t(key);
}

function applyTranslations() {
  document.querySelectorAll("[data-i18n]").forEach((element) => {
    element.textContent = t(element.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((element) => {
    element.placeholder = t(element.dataset.i18nPlaceholder);
  });
  document.querySelectorAll("[data-i18n-aria-label]").forEach((element) => {
    element.setAttribute("aria-label", t(element.dataset.i18nAriaLabel));
  });
  document.querySelectorAll("[data-i18n-message]").forEach((element) => {
    element.textContent = t(element.dataset.i18nMessage);
  });
  document.querySelectorAll(".default-marker").forEach((marker) => {
    marker.dataset.label = t("common.default");
  });
}

function statusLabel(state) {
  if (state === "running") return t("status.running");
  return t(`status.${state}`);
}

function setStatus(state, key = null) {
  currentStatus = state;
  currentStatusKey = key;
  jobStatus.dataset.state = state;
  jobStatus.textContent = key ? t(key) : statusLabel(state);
  const running = state === "running";
  startButton.disabled = running;
  stopButton.disabled = !running;
}

function renderHealth() {
  health.dataset.state = healthView.state;
  if (healthView.missing) {
    const missing = [];
    if (healthView.missing.ffmpeg) missing.push("FFmpeg");
    if (healthView.missing.model) missing.push(t("health.model"));
    healthText.textContent = t("health.missing", {
      items: missing.join(currentLanguage === "zh" ? "、" : ", "),
    });
    return;
  }
  healthText.textContent = t(healthView.key, healthView.values);
}

function setLanguage(language) {
  currentLanguage = translations[language] ? language : "zh";
  document.documentElement.lang = currentLanguage === "zh" ? "zh-CN" : "en";
  localStorage.setItem("oldtonefix-language", currentLanguage);
  languageButtons.forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.language === currentLanguage));
  });
  applyTranslations();
  renderHealth();
  setStatus(currentStatus, currentStatusKey);
  renderProgress(lastProgress);
  if (lastJob) renderJob(lastJob);
}

function renderProgress(progress = {}) {
  lastProgress = progress;
  const total = Math.max(0, Number(progress.total) || 0);
  const completed = Math.max(0, Math.min(Number(progress.completed) || 0, total));
  const percent = total ? Math.round((completed / total) * 100) : 0;
  progressFill.style.width = `${percent}%`;
  progressLabel.textContent = total
    ? t("progress.count", { completed, total })
    : t(currentStatus === "idle" ? "progress.waiting" : "progress.scanning");
  progressText.textContent = `${percent}%`;
  jobProgress.setAttribute("aria-valuenow", String(percent));
  jobProgress.dataset.state = total && completed === total ? "completed" : "active";
}

function updateSlider(slider) {
  const min = Number(slider.min);
  const max = Number(slider.max);
  const current = Number(slider.value);
  const defaultValue = Number(slider.dataset.default);
  const fill = ((current - min) / (max - min)) * 100;
  const defaultRatio = (defaultValue - min) / (max - min);
  const usableTrackWidth = Math.max(0, slider.clientWidth - rangeThumbSize);
  const defaultPosition = rangeThumbSize / 2 + defaultRatio * usableTrackWidth;
  slider.style.setProperty("--fill", `${fill}%`);
  slider.parentElement.style.setProperty("--default-position", `${defaultPosition}px`);
  const value = document.querySelector(`output[for="${slider.id}"] span`);
  if (value) value.textContent = slider.value;
}

function updateAllSliders() {
  sliders.forEach(updateSlider);
}

function showTuningPage(requestedPage) {
  currentTuningPage = Math.max(0, Math.min(requestedPage, tuningPages.length - 1));
  tuningPages.forEach((page, pageIndex) => {
    page.hidden = pageIndex !== currentTuningPage;
  });
  pageButtons.forEach((button, pageIndex) => {
    if (pageIndex === currentTuningPage) {
      button.setAttribute("aria-current", "page");
    } else {
      button.removeAttribute("aria-current");
    }
  });
  previousPageButton.disabled = currentTuningPage === 0;
  nextPageButton.disabled = currentTuningPage === tuningPages.length - 1;
  pageStatus.textContent = `${currentTuningPage + 1} / ${tuningPages.length}`;
  updateAllSliders();
}

languageButtons.forEach((button) => {
  button.addEventListener("click", () => setLanguage(button.dataset.language));
});
sliders.forEach((slider) => slider.addEventListener("input", () => updateSlider(slider)));
window.addEventListener("resize", updateAllSliders);
previousPageButton.addEventListener("click", () => showTuningPage(currentTuningPage - 1));
nextPageButton.addEventListener("click", () => showTuningPage(currentTuningPage + 1));
pageButtons.forEach((button) => {
  button.addEventListener("click", () => showTuningPage(Number(button.dataset.pageTarget)));
});

resetButton.addEventListener("click", () => {
  sliders.forEach((slider) => {
    slider.value = slider.dataset.default;
    updateSlider(slider);
  });
  document.querySelector('input[name="afftdn_nt"][value="white"]').checked = true;
  document.querySelector("#afftdn-tn").checked = true;
});

async function checkHealth() {
  healthView = { state: "checking", key: "health.checking", values: {} };
  renderHealth();
  try {
    const response = await fetch("/api/health");
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || t("error.health"));
    if (data.ok) {
      healthView = { state: "ready", key: "health.ready", values: {} };
    } else {
      healthView = {
        state: "error",
        key: "health.missing",
        values: {},
        missing: { ffmpeg: !data.ffmpeg, model: !data.model },
      };
    }
  } catch (error) {
    healthView = { state: "error", key: "error.health", values: {} };
    if (error.message && error.message !== t("error.health")) healthView = { state: "error", key: "health.missing", values: { items: error.message } };
  }
  renderHealth();
}

function collectPayload() {
  const noiseType = document.querySelector('input[name="afftdn_nt"]:checked');
  const payload = {
    input: document.querySelector("#input-path").value.trim(),
    output: document.querySelector("#output-path").value.trim(),
    keep_existing: document.querySelector("#keep-existing").checked,
    afftdn_nt: noiseType.value,
    afftdn_tn: document.querySelector("#afftdn-tn").checked,
  };
  sliders.forEach((slider) => {
    payload[slider.name] = Number(slider.value);
  });
  return payload;
}

function renderJob(job) {
  lastJob = job;
  setStatus(job.status);
  renderProgress(job.progress);
  delete command.dataset.i18nMessage;
  command.textContent = (job.command || []).join(" ") || t("job.commandWaiting");
  const lines = job.logs || [];
  delete log.dataset.i18nMessage;
  log.textContent = lines.length ? lines.join("\n") : t("job.waitingOutput");
  log.scrollTop = log.scrollHeight;
}

async function pollJob(jobId) {
  if (jobId !== activeJobId) return;
  try {
    const response = await fetch(`/api/jobs/${jobId}`);
    const job = await response.json();
    if (!response.ok) throw new Error(job.error || t("error.readJob"));
    renderJob(job);
    if (job.status === "running") {
      pollTimer = setTimeout(() => pollJob(jobId), 450);
    } else {
      activeJobId = null;
      pollTimer = null;
    }
  } catch (error) {
    setStatus("failed");
    delete log.dataset.i18nMessage;
    log.textContent += `\n${error.message}`;
    activeJobId = null;
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  clearTimeout(pollTimer);
  lastJob = null;
  setStatus("running", "status.starting");
  renderProgress();
  setTranslatedMessage(log, "job.submitting");
  setTranslatedMessage(command, "job.buildingCommand");
  try {
    const response = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectPayload()),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || t("error.startJob"));
    activeJobId = data.job_id;
    await pollJob(activeJobId);
  } catch (error) {
    setStatus("failed");
    delete log.dataset.i18nMessage;
    log.textContent = error.message;
  }
});

stopButton.addEventListener("click", async () => {
  if (!activeJobId) return;
  stopButton.disabled = true;
  try {
    const response = await fetch(`/api/jobs/${activeJobId}/stop`, { method: "POST" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || t("error.stopJob"));
    await pollJob(activeJobId);
  } catch (error) {
    setStatus("failed");
    delete log.dataset.i18nMessage;
    log.textContent += `\n${error.message}`;
  }
});

setTranslatedMessage(command, "result.commandPending");
setTranslatedMessage(log, "result.logEmpty");
const savedLanguage = localStorage.getItem("oldtonefix-language");
setLanguage(savedLanguage === "en" ? "en" : "zh");
showTuningPage(0);
updateAllSliders();
checkHealth();
