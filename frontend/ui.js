/* Shared presentation helpers. API authority stays with the existing backend. */
const htmlEscape = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const icon = (name) =>
  `<img class="icon" src="assets/icons/${name}.svg" alt="" aria-hidden="true">`;
const pageKey = () =>
  location.hash.replace(/^#\//, "").split("?")[0] || "library";
const pageParams = () =>
  new URLSearchParams(location.hash.split("?")[1] || location.search.slice(1));
const pageLink = (page, params = {}) =>
  `#/${page}${Object.keys(params).length ? "?" + new URLSearchParams(params) : ""}`;
let pageEpoch = 0;
const pageScroll = new Map();
function savePageScroll(page) {
  const main = document.getElementById("main");
  if (main.dataset.renderedPage === page) {
    pageScroll.set(page, {
      main: main.scrollTop,
      list: main.querySelector(".master-scroll")?.scrollTop,
      detail: main.querySelector(".detail-panel")?.scrollTop,
    });
  }
}
const beginPage = (page) => {
  savePageScroll(page);
  const epoch = ++pageEpoch;
  return () => epoch === pageEpoch && pageKey() === page;
};
function restorePageScroll(page) {
  const main = document.getElementById("main"),
    saved = pageScroll.get(page);
  main.dataset.renderedPage = page;
  if (!saved) return;
  main.scrollTop = saved.main;
  const list = main.querySelector(".master-scroll"),
    detail = main.querySelector(".detail-panel");
  if (list && saved.list != null) list.scrollTop = saved.list;
  if (detail && saved.detail != null) detail.scrollTop = saved.detail;
  pageScroll.delete(page);
}
function timecode(value, short = false, precise = false) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0)
    return "—";
  const ms = precise ? Math.round(value * 1000) : Math.floor(value) * 1000,
    sec = Math.floor(ms / 1000);
  const fraction =
    ms % 1000
      ? "." +
        String(ms % 1000)
          .padStart(3, "0")
          .replace(/0+$/, "")
      : "";
  const minutes = Math.floor(sec / 60);
  return (
    (short
      ? String(minutes).padStart(2, "0")
      : `${String(Math.floor(sec / 3600)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`) +
    ":" +
    String(sec % 60).padStart(2, "0") +
    fraction
  );
}
function parseTimecode(value) {
  const text = String(value).trim();
  if (!/^\d+(?::\d{1,2}){0,2}(?:\.\d{1,3})?$/.test(text))
    throw new Error("时间格式应为 时:分:秒 或 分:秒，最多三位小数");
  const parts = text.split(":").map(Number);
  if (parts.slice(1).some((n) => n >= 60))
    throw new Error("时间中的分、秒必须小于 60");
  const seconds = parts.reduce((sum, n) => sum * 60 + n, 0);
  if (!Number.isFinite(seconds)) throw new Error("时间数值无效");
  return seconds;
}
const beijingTime = (value) => {
  if (value == null || value === "") return "时间未知";
  const date =
    typeof value === "number" ? new Date(value * 1000) : new Date(value);
  return Number.isNaN(date.getTime())
    ? "时间未知"
    : new Intl.DateTimeFormat("zh-CN", {
        timeZone: "Asia/Shanghai",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      })
        .format(date)
        .replaceAll("/", "-");
};
const statusBadge = (label, tone = "muted") =>
  `<span class="badge ${tone}">${htmlEscape(label)}</span>`;
const notice = (text, tone = "info") =>
  `<div class="notice ${tone}">${icon(tone === "warning" ? "alert-circle" : "info-circle")}<span>${text}</span></div>`;
const pageHead = (title, subtitle = "", actions = "") =>
  `<header class="page-head"><div class="page-title"><h1>${title}</h1>${subtitle ? `<span class="sub">${subtitle}</span>` : ""}</div><div class="head-actions">${actions}</div></header>`;
const emptyState = (text) =>
  `<div class="empty-state">${icon("inbox")}<p>${htmlEscape(text)}</p></div>`;
function pageError(title, error, retry) {
  document.getElementById("main").innerHTML =
    pageHead(title) +
    notice(htmlEscape(error.message), "warning") +
    '<button class="btn btn-secondary" id="page-retry">重新读取</button>';
  document.getElementById("page-retry").onclick = retry;
}
const tabsHTML = (items, selected, attribute) =>
  `<div class="tabs" role="tablist">${items.map(([key, label]) => `<button role="tab" aria-selected="${selected === key}" class="tab ${selected === key ? "active" : ""}" ${attribute}="${key}">${label}</button>`).join("")}</div>`;
function bindTabKeyboard(root) {
  root.querySelectorAll('[role="tablist"]').forEach((list) => {
    const tabs = [...list.querySelectorAll('[role="tab"]')];
    const panels = {
      source: "source-media", final: "final-media",
      content: "content-panel", subtitles: "subtitle-panel",
    };
    tabs.forEach((tab) => {
      const id = tab.dataset.reviewTab === "final" ? "final-panel" : panels[tab.dataset.mediaTab || tab.dataset.reviewTab];
      const panel = root.querySelector(`#${id}`);
      if (!panel) return;
      tab.id = `${panel.id}-tab`;
      tab.setAttribute("aria-controls", panel.id);
      panel.setAttribute("role", "tabpanel");
      panel.setAttribute("aria-labelledby", tab.id);
    });
    const update = () => tabs.forEach((tab) => {
      tab.tabIndex = tab.getAttribute("aria-selected") === "true" ? 0 : -1;
    });
    update();
    list.addEventListener("click", update);
    list.onkeydown = (event) => {
      const index = tabs.indexOf(event.target);
      if (index < 0 || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 :
        (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
      tabs[next].click();
      tabs[next].focus();
    };
  });
}
function setMessage(id, text) {
  const element = document.getElementById(id);
  if (element) element.textContent = text;
}
function showDialog(id) {
  document.getElementById(id)?.showModal();
}
function bindDialogs() {
  document
    .querySelectorAll("[data-dialog]")
    .forEach(
      (button) => (button.onclick = () => showDialog(button.dataset.dialog)),
    );
  document
    .querySelectorAll("[data-close-dialog]")
    .forEach(
      (button) => (button.onclick = () => button.closest("dialog").close()),
    );
}
function dialogHTML(id, title, content) {
  return `<dialog id="${id}"><header><h2>${title}</h2><button class="icon-button" aria-label="关闭" data-close-dialog>${icon("x")}</button></header>${content}</dialog>`;
}
const mediaPlaceholder = (label = "暂无封面") =>
  `<div class="media-empty">${icon("photo")}<span>${label}</span></div>`;
/* A separate muted video extracts a real frame; it never seeks the user's player. */
async function loadVideoThumbnails() {
  const epoch = pageEpoch;
  for (const host of document.querySelectorAll("[data-media-url]")) {
    if (epoch !== pageEpoch || !host.isConnected) break;
    const video = document.createElement("video");
    video.muted = true;
    video.preload = "metadata";
    await new Promise((resolve) => {
      let done = false;
      const finish = () => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        video.removeAttribute("src");
        video.load();
        resolve();
      };
      const timer = setTimeout(finish, 5000);
      video.onerror = finish;
      video.onloadedmetadata = () => {
        if (video.duration > 0)
          video.currentTime = Math.min(0.5, video.duration / 2);
        else finish();
      };
      video.onseeked = () => {
        if (host.isConnected && epoch === pageEpoch && video.videoWidth) {
          try {
            const canvas = document.createElement("canvas");
            canvas.width = 480;
            canvas.height = 270;
            const ctx = canvas.getContext("2d"),
              scale = Math.max(480 / video.videoWidth, 270 / video.videoHeight);
            ctx.drawImage(
              video,
              (480 - video.videoWidth * scale) / 2,
              (270 - video.videoHeight * scale) / 2,
              video.videoWidth * scale,
              video.videoHeight * scale,
            );
            const img = document.createElement("img");
            img.alt = "成片预览";
            img.src = canvas.toDataURL("image/jpeg", 0.85);
            host.replaceChildren(img);
            if (host.dataset.duration) {
              const label = document.createElement("span");
              label.className = "duration";
              label.textContent = host.dataset.duration;
              host.append(label);
            }
          } catch {
            /* Keep the explicit unavailable preview. */
          }
        }
        finish();
      };
      video.src = host.dataset.mediaUrl;
    });
  }
}
async function readAllPages(path, current) {
  const items = [];
  let offset = 0;
  while (true) {
    const data = await reviewAPI(
      `${path}${path.includes("?") ? "&" : "?"}limit=100&offset=${offset}`,
    );
    if (!current()) return null;
    items.push(...data.items);
    offset += data.items.length;
    if (offset >= data.total) return items;
    if (!data.items.length) throw new Error("列表读取未完成，请刷新后重试");
  }
}
async function updateServiceIndicator() {
  const element = document.getElementById("service-indicator");
  try {
    const services = await reviewAPI("/api/services");
    const healthy = ["ok", "running", "idle"];
    const core = [services.recorder?.state, services.workbench?.state];
    const normal = core.every((state) => healthy.includes(state));
    element.className = `service-dot ${normal ? "healthy" : "warning"}`;
    element.innerHTML = `<i></i>${normal ? "本机服务正常" : "部分服务不可用"}`;
  } catch {
    element.className = "service-dot offline";
    element.innerHTML = "<i></i>本机服务离线";
  }
}
