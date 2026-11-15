export const state = {
  currentEpisode: null,
  appMeta: null,
  userRole: "admin",
  reportCount: 0,
  currentUiLang: localStorage.getItem("laoma_ui_lang") || "zh-TW",
};

export function escapeHtml(str) {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function fmtTime(sec) {
  const s = Math.max(0, Math.floor(sec || 0));
  const m = String(Math.floor(s / 60)).padStart(2, "0");
  const r = String(s % 60).padStart(2, "0");
  return `${m}:${r}`;
}

function translateBucket(bucket, key, vars = {}) {
  let msg = state.appMeta?.[bucket]?.[key] ?? key;
  for (const [k, v] of Object.entries(vars)) {
    msg = msg.replace(`{${k}}`, String(v));
  }
  return msg;
}

export function tUi(key, vars = {}) {
  return translateBucket("ui", key, vars);
}

export function tStatus(key, vars = {}) {
  return translateBucket("status", key, vars);
}
