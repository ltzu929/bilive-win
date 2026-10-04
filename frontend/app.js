const PAGES = {
  library: renderManualWorkbench,
  live: renderNativeRooms,
  publish: renderPublish,
  streamer: renderStreamerProfiles,
  recycle: renderSourceRecycle,
  settings: renderRuntimeSettings,
};
function route() {
  clearTimeout(reviewTimer);
  clearTimeout(roomTimer);
  if (!location.hash) {
    location.hash = "#/library";
    return;
  }
  const page = PAGES[pageKey()] ? pageKey() : "library";
  if (page !== pageKey()) {
    location.hash = "#/library";
    return;
  }
  document.querySelectorAll(".nav-item").forEach((a) => {
    a.classList.toggle("active", a.dataset.page === page);
    if (a.dataset.page === page) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  const main = document.getElementById("main");
  main.className = `main page-${page}`;
  main.scrollTop = 0;
  PAGES[page]();
}
function setSidebarCollapsed(collapsed) {
  document.querySelector(".app").classList.toggle("sidebar-collapsed", collapsed);
  const button = document.getElementById("sidebar-toggle");
  button.setAttribute("aria-expanded", String(!collapsed));
  button.setAttribute("aria-controls", "nav");
  button.title = button.ariaLabel = collapsed ? "展开侧边栏" : "收起侧边栏";
  try { localStorage.setItem("bilive:sidebar-collapsed", String(collapsed)); } catch {}
}
document.querySelectorAll(".nav-item").forEach(link => {
  const text = [...link.childNodes].filter(n => n.nodeType === Node.TEXT_NODE).map(n => n.textContent).join("").trim();
  link.setAttribute("aria-label", text);
  link.title = text;
  const label = document.createElement("span");
  label.className = "nav-label";
  label.textContent = text;
  [...link.childNodes].filter(n => n.nodeType === Node.TEXT_NODE).forEach(n => n.remove());
  link.append(label);
});
let sidebarCollapsed = false;
try { sidebarCollapsed = localStorage.getItem("bilive:sidebar-collapsed") === "true"; } catch {}
setSidebarCollapsed(sidebarCollapsed);
document.getElementById("sidebar-toggle").onclick = () => setSidebarCollapsed(!document.querySelector(".app").classList.contains("sidebar-collapsed"));
window.addEventListener("hashchange", route);
route();
updateServiceIndicator();
