import { readErrorDetail } from "./js/api.js";
import { initGenerator, renderFormatGrid } from "./js/generator.js";
import {
  applyPlaybackSpeed,
  initPlayer,
  setPendingSeek,
  stopWaveformLoop,
  updateAutoFollowLabel,
  updatePlaybackUI,
} from "./js/player.js";
import { escapeHtml, fmtTime, state, tStatus, tUi } from "./js/state.js";
import { renderTranscript, updateGuestCard } from "./js/transcript.js";

const appShell = document.getElementById("app-shell");
const btnModeListen = document.getElementById("mode-listen");
const btnModeStudio = document.getElementById("mode-studio");
const btnToggleSidebar = document.getElementById("btn-toggle-sidebar");
const btnExportMenu = document.getElementById("btn-export-menu");
const exportDropdown = document.getElementById("export-dropdown");
const langBtns = document.querySelectorAll(".lang-btn[data-ui-lang]");
const guestNameInput = document.getElementById("guest-name");
const focusQuestionsInput = document.getElementById("focus-questions");
const chapterPillsEl = document.getElementById("chapter-pills");
const reportSelect = document.getElementById("report-select");
const sourceUrlInput = document.getElementById("source-url");
const sourceTextarea = document.getElementById("source-text");
const modelInput = document.getElementById("model-input");
const generateBtn = document.getElementById("generate-btn");
const genStatus = document.getElementById("gen-status");
const episodeListEl = document.getElementById("episode-list");

const epTitle = document.getElementById("ep-title");
const epSummary = document.getElementById("ep-summary");
const epEngine = document.getElementById("ep-engine");
const cardHostA = document.getElementById("card-host-a");
const cardHostB = document.getElementById("card-host-b");
const cardHostC = document.getElementById("card-host-c");
const labelHostA = document.getElementById("label-host-a");
const labelHostB = document.getElementById("label-host-b");
const labelHostC = document.getElementById("label-host-c");

const audioEl = document.getElementById("audio-player");
const transcriptListEl = document.getElementById("transcript-list");
const timeReadout = document.getElementById("time-readout");
const btnExportSrt = document.getElementById("btn-export-srt");
const btnExportShownotes = document.getElementById("btn-export-shownotes");
const btnOpenRss = document.getElementById("btn-open-rss");
const voiceSpk1 = document.getElementById("voice-speaker-1");
const voiceSpk2 = document.getElementById("voice-speaker-2");
const voiceSpk3 = document.getElementById("voice-speaker-3");
const btnOpenPrefs = document.getElementById("btn-open-prefs");

function syncReportPlaceholder() {
  const tabReport = document.getElementById("tab-src-report");
  if (tabReport) {
    tabReport.textContent = `${tUi("tab_src_report")} (${state.reportCount})`;
  }
  const optManual = document.getElementById("opt-manual");
  if (optManual) {
    optManual.textContent = tUi(
      state.reportCount > 0 ? "opt_manual" : "opt_reports_empty"
    );
  }
  if (reportSelect) {
    reportSelect.disabled = state.reportCount === 0;
  }
}

function syncLangButtons(lang) {
  langBtns.forEach((btn) => {
    const active = btn.dataset.uiLang === lang;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-pressed", String(active));
  });
}

syncLangButtons(state.currentUiLang);
let selectAbortController = null;

function applyMeta(meta) {
  state.appMeta = meta;
  state.userRole = meta.role || null;
  const isAdmin = state.userRole === "admin";
  if (btnModeStudio) btnModeStudio.hidden = !isAdmin;
  if (btnOpenPrefs) btnOpenPrefs.hidden = !isAdmin;
  if (!isAdmin) {
    setWorkspaceMode("listen", false);
  }
  document.documentElement.lang = meta.locale === "en-US" ? "en" : "zh-Hant";
  document.title = meta.brand.html_title;
  document.getElementById("brand-badge").textContent = `🍜 ${meta.brand.badge}`;
  document.getElementById("app-version").textContent = `v${meta.version}`;
  document.getElementById("credits-prefix").textContent = meta.brand.credits_prefix;
  const creditsLink = document.getElementById("credits-link");
  creditsLink.textContent = meta.brand.credits_name;
  creditsLink.href = meta.brand.credits_url;

  document.getElementById("sec-generate").textContent = tUi("section_generate");
  document.getElementById("lbl-url").textContent = tUi("label_url");
  sourceUrlInput.placeholder = tUi("placeholder_url");
  document.getElementById("lbl-report").textContent = tUi("label_report");
  document.getElementById("lbl-source").textContent = tUi("label_source");
  sourceTextarea.placeholder = tUi("placeholder_source");
  document.getElementById("lbl-host-a").textContent = tUi("label_host_a");
  document.getElementById("lbl-host-b").textContent = tUi("label_host_b");
  document.getElementById("lbl-format").textContent = tUi("label_format");
  document.getElementById("lbl-guest").textContent = tUi("label_guest");
  guestNameInput.placeholder = tUi("placeholder_guest");
  document.getElementById("lbl-focus").textContent = tUi("label_focus");
  focusQuestionsInput.placeholder = tUi("placeholder_focus");
  document.getElementById("lbl-lang").textContent = tUi("label_lang");

  renderFormatGrid(meta.interview_formats || []);

  document.getElementById("lbl-turns").textContent = tUi("label_turns");
  document.getElementById("lbl-backend").textContent = tUi("label_backend");
  const backendSel = document.getElementById("backend-select");
  if (backendSel?.options?.length >= 3) {
    backendSel.options[0].textContent = tUi("backend_auto");
    backendSel.options[1].textContent = tUi("backend_agy");
    backendSel.options[2].textContent = tUi("backend_ollama");
  }
  document.getElementById("lbl-model").textContent = tUi("label_model");
  modelInput.placeholder = tUi("placeholder_model");
  const btnPlayerSettings = document.getElementById("btn-player-settings");
  if (btnPlayerSettings) btnPlayerSettings.title = tUi("btn_player_settings_title");

  if (Array.isArray(meta.voices) && voiceSpk1) {
    const voiceSelects = [
      [voiceSpk1, tUi("voice_default_1")],
      [voiceSpk2, tUi("voice_default_2")],
      [voiceSpk3, tUi("voice_default_3")],
    ];
    for (const [sel, defLabel] of voiceSelects) {
      if (!sel) continue;
      if (sel.options.length === 0) {
        const defOpt = document.createElement("option");
        defOpt.value = "";
        defOpt.textContent = defLabel;
        sel.appendChild(defOpt);
        for (const v of meta.voices) {
          const opt = document.createElement("option");
          opt.value = v.id;
          opt.textContent = v.label;
          sel.appendChild(opt);
        }
      } else {
        sel.options[0].textContent = defLabel;
      }
    }
  }

  document.getElementById("summary-role-settings").textContent = tUi("summary_role_settings");
  const prefsTitle = document.getElementById("prefs-modal-title");
  if (prefsTitle) prefsTitle.textContent = tUi("prefs_title");
  if (btnOpenPrefs) btnOpenPrefs.title = tUi("prefs_btn_title");
  const btnPrefsDone = document.getElementById("btn-prefs-done");
  if (btnPrefsDone) btnPrefsDone.textContent = tUi("prefs_close");
  document.getElementById("tab-src-text").textContent = tUi("tab_src_text");
  document.getElementById("tab-src-url").textContent = tUi("tab_src_url");
  syncReportPlaceholder();
  btnModeListen.textContent = tUi("mode_listen");
  btnModeStudio.textContent = tUi("mode_studio");
  btnExportMenu.textContent = tUi("btn_export_menu");
  const isCollapsed = appShell?.classList.contains("sidebar-collapsed");
  btnToggleSidebar.textContent = tUi(
    isCollapsed ? "btn_toggle_sidebar_show" : "btn_toggle_sidebar_hide"
  );

  document.getElementById("btn-upload-source").textContent = tUi("btn_upload_source");
  document.getElementById("source-override-hint").textContent = tUi("source_override_hint");
  document.getElementById("lbl-long-text-mode").textContent = tUi("lbl_long_text_mode");
  document.getElementById("opt-long-map-reduce").textContent = tUi("opt_long_map_reduce");
  document.getElementById("opt-long-truncate").textContent = tUi("opt_long_truncate");
  document.getElementById("lbl-voice-1").textContent = tUi("lbl_voice_1");
  document.getElementById("lbl-voice-2").textContent = tUi("lbl_voice_2");
  document.getElementById("lbl-voice-3").textContent = tUi("lbl_voice_3");
  document.getElementById("lbl-loudnorm-text").textContent = tUi("lbl_loudnorm");
  document.getElementById("lbl-sfx-text").textContent = tUi("lbl_sfx");
  const lblShowMemory = document.getElementById("lbl-show-memory-text");
  if (lblShowMemory) lblShowMemory.textContent = tUi("lbl_show_memory");
  const lblSbSummary = document.getElementById("lbl-show-bible-summary");
  if (lblSbSummary) lblSbSummary.textContent = tUi("show_bible_summary");
  const lblSbShowName = document.getElementById("lbl-sb-show-name");
  if (lblSbShowName) lblSbShowName.textContent = tUi("lbl_sb_show_name");
  const lblSbHostA = document.getElementById("lbl-sb-host-a-persona");
  if (lblSbHostA) lblSbHostA.textContent = tUi("lbl_sb_host_a_persona");
  const lblSbHostB = document.getElementById("lbl-sb-host-b-persona");
  if (lblSbHostB) lblSbHostB.textContent = tUi("lbl_sb_host_b_persona");
  const lblSbCallbacks = document.getElementById("lbl-sb-callbacks");
  if (lblSbCallbacks) lblSbCallbacks.textContent = tUi("lbl_sb_callbacks");
  const lblSbCliches = document.getElementById("lbl-sb-cliches");
  if (lblSbCliches) lblSbCliches.textContent = tUi("lbl_sb_cliches");
  const btnSaveSb = document.getElementById("btn-save-show-bible");
  if (btnSaveSb) btnSaveSb.textContent = tUi("btn_save_show_bible");

  document.getElementById("btn-import-tpl").textContent = tUi("btn_import_tpl");
  document.getElementById("btn-download-tpl").textContent = tUi("btn_download_tpl");
  btnExportSrt.textContent = tUi("btn_export_srt");
  btnExportShownotes.textContent = tUi("btn_export_shownotes");
  btnOpenRss.textContent = tUi("btn_open_rss");
  document.getElementById("lbl-player-speed").textContent = tUi("player_settings_speed");
  const lblTranscriptSize = document.getElementById("lbl-transcript-size");
  if (lblTranscriptSize) lblTranscriptSize.textContent = tUi("player_settings_font_size");
  const btnFsSm = document.getElementById("btn-fs-sm");
  const btnFsMd = document.getElementById("btn-fs-md");
  const btnFsLg = document.getElementById("btn-fs-lg");
  if (btnFsSm) btnFsSm.textContent = tUi("font_size_sm");
  if (btnFsMd) btnFsMd.textContent = tUi("font_size_md");
  if (btnFsLg) btnFsLg.textContent = tUi("font_size_lg");
  document.getElementById("lbl-player-loop").textContent = tUi("player_settings_loop");
  document.getElementById("lbl-player-shortcuts").textContent = tUi("player_settings_shortcuts");
  updateAutoFollowLabel();
  generateBtn.textContent = tUi("btn_generate");
  document.getElementById("sec-episodes").textContent = tUi("section_episodes");
  document.getElementById("tag-speaker-a").textContent = tUi("speaker_1_tag");
  document.getElementById("tag-speaker-b").textContent = tUi("speaker_2_tag");
  document.getElementById("role-a").textContent = tUi("role_a");
  document.getElementById("role-b").textContent = tUi("role_b");
  updateGuestCard(state.currentEpisode?.guest_name || "");
  document.getElementById("transcript-title").textContent = tUi("transcript_title");
  if (!state.currentEpisode) {
    epEngine.textContent = tStatus("ready");
    epTitle.textContent = tUi("empty_title");
    epSummary.textContent = tUi("empty_summary");
  }
}

async function loadMeta(lang) {
  try {
    const res = await fetch(`/api/meta?lang=${encodeURIComponent(lang)}`);
    if (res.status === 401) {
      const entered = window.prompt(
        "🔐 請輸入 VibePodcast Session Token（或使用專屬登入連結）："
      );
      if (entered && entered.trim()) {
        const loginRes = await fetch("/api/login", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token: entered.trim() }),
        });
        if (loginRes.ok) {
          const retry = await fetch(`/api/meta?lang=${encodeURIComponent(lang)}`);
          if (retry.ok) {
            applyMeta(await retry.json());
            return true;
          }
        }
        genStatus.textContent = "🔐 Token 驗證失敗，請確認是否輸入正確。";
      } else {
        genStatus.textContent = "🔐 未通過認證，請先登入後再操作。";
      }
      return false;
    }
    if (!res.ok) return false;
    applyMeta(await res.json());
    return true;
  } catch (err) {
    console.warn("Failed to load metadata:", err);
    return false;
  }
}

async function loadReports() {
  const res = await fetch("/api/reports");
  if (!res.ok) return;
  const items = await res.json();
  state.reportCount = items.length;
  for (const item of items) {
    const opt = document.createElement("option");
    opt.value = item.path;
    opt.textContent = `📄 ${item.name} — ${item.preview.slice(0, 40)}...`;
    reportSelect.appendChild(opt);
  }
  syncReportPlaceholder();
}

async function selectEpisode(episodeId) {
  if (selectAbortController) selectAbortController.abort();
  selectAbortController = new AbortController();
  const { signal } = selectAbortController;

  let res;
  try {
    res = await fetch(`/api/episodes/${encodeURIComponent(episodeId)}/timeline`, {
      cache: "no-store",
      signal,
    });
  } catch (err) {
    if (err.name === "AbortError") return;
    throw err;
  }
  if (!res.ok || signal.aborted) return;
  const ep = await res.json();
  if (signal.aborted) return;
  state.currentEpisode = ep;

  for (const item of episodeListEl.children) {
    item.classList.toggle("active", item.dataset.episodeId === episodeId);
  }

  epTitle.textContent = ep.title;
  epSummary.textContent = ep.summary;
  epEngine.textContent = ep.engine.toUpperCase();
  labelHostA.textContent = ep.host_a;
  labelHostB.textContent = ep.host_b;
  cardHostA.querySelector(".avatar").textContent = ep.host_a.slice(0, 1).toUpperCase();
  cardHostB.querySelector(".avatar").textContent = ep.host_b.slice(0, 1).toUpperCase();
  updateGuestCard(ep.guest_name);

  const encodedId = encodeURIComponent(episodeId);
  setPendingSeek(null);
  audioEl.pause();
  stopWaveformLoop();
  audioEl.src = `/api/episodes/${encodedId}/audio?t=${Date.now()}`;
  audioEl.load();
  const savedSpeed = Number(localStorage.getItem("vp_playback_speed") || "1");
  applyPlaybackSpeed(savedSpeed);
  const savedPos = Number(localStorage.getItem(`vp_pos_${episodeId}`) || "0");
  const hasSavedPos = savedPos > 1 && savedPos < (ep.duration_sec || 0) - 2;
  if (hasSavedPos) {
    setPendingSeek(savedPos);
  }
  localStorage.setItem("vp_last_episode_id", episodeId);
  btnExportSrt.hidden = false;
  btnExportShownotes.hidden = false;
  btnExportSrt.onclick = () => {
    window.location.href = `/api/episodes/${encodedId}/srt`;
  };
  btnExportShownotes.onclick = () => {
    window.location.href = `/api/episodes/${encodedId}/shownotes`;
  };
  renderTranscript(ep.segments, selectEpisode);
  if (!hasSavedPos) {
    transcriptListEl.scrollTop = 0;
  }
  updatePlaybackUI(hasSavedPos ? savedPos : 0, { forceScroll: hasSavedPos, instant: hasSavedPos });
}

async function deleteEpisode(episodeId) {
  const res = await fetch(
    `/api/episodes/${encodeURIComponent(episodeId)}?lang=${encodeURIComponent(state.currentUiLang)}`,
    { method: "DELETE" }
  );
  if (!res.ok) {
    genStatus.textContent = tStatus("delete_failed", { detail: `HTTP ${res.status}` });
    return;
  }
  if (state.currentEpisode?.episode_id === episodeId) {
    state.currentEpisode = null;
    setPendingSeek(null);
    audioEl.pause();
    audioEl.removeAttribute("src");
    audioEl.load();
    stopWaveformLoop();
    epTitle.textContent = tUi("empty_title");
    epSummary.textContent = tUi("empty_summary");
    epEngine.textContent = tStatus("ready");
    labelHostA.textContent = "Host A";
    labelHostB.textContent = "Host B";
    cardHostA.querySelector(".avatar").textContent = "A";
    cardHostB.querySelector(".avatar").textContent = "B";
    updateGuestCard("");
    transcriptListEl.innerHTML = "";
    chapterPillsEl.innerHTML = "";
    btnExportSrt.hidden = true;
    btnExportShownotes.hidden = true;
    timeReadout.textContent = "00:00 / 00:00";
    cardHostA.classList.remove("speaking");
    cardHostB.classList.remove("speaking");
  }
  await loadEpisodes();
}

async function loadEpisodes(selectId = null) {
  const res = await fetch("/api/episodes");
  if (!res.ok) return;
  const episodes = await res.json();
  episodeListEl.innerHTML = "";
  const delLabel = tUi("delete_title");

  const isAdmin = state.userRole === "admin";
  for (const ep of episodes) {
    const li = document.createElement("li");
    li.className =
      "episode-item" + (state.currentEpisode?.episode_id === ep.episode_id ? " active" : "");
    li.dataset.episodeId = ep.episode_id;
    const delBtnHtml = isAdmin
      ? `<button class="btn-delete" title="${escapeHtml(delLabel)}" aria-label="${escapeHtml(
          delLabel
        )}">🗑️</button>`
      : "";
    li.innerHTML = `
      <div class="episode-info">
        <div class="ep-name">${escapeHtml(ep.title)}</div>
        <div class="ep-meta">${escapeHtml(ep.host_a)} & ${escapeHtml(ep.host_b)}${
      ep.guest_name ? ` + ${escapeHtml(ep.guest_name)}` : ""
    } · ${fmtTime(ep.duration_sec)} · ${escapeHtml(ep.engine)}</div>
      </div>
      ${delBtnHtml}
    `;
    li.addEventListener("click", () => selectEpisode(ep.episode_id));
    li.querySelector(".btn-delete")?.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm(tUi("delete_confirm", { title: ep.title }))) return;
      await deleteEpisode(ep.episode_id);
    });
    episodeListEl.appendChild(li);
  }

  if (selectId) {
    await selectEpisode(selectId);
  } else if (!state.currentEpisode && episodes.length > 0) {
    const lastId = localStorage.getItem("vp_last_episode_id");
    const targetId =
      lastId && episodes.some((e) => e.episode_id === lastId)
        ? lastId
        : episodes[0].episode_id;
    await selectEpisode(targetId);
  }
  if (state.userRole === "admin" && !localStorage.getItem("vp_ui_mode")) {
    setWorkspaceMode(episodes.length > 0 ? "listen" : "studio", false);
  } else if (state.userRole !== "admin") {
    setWorkspaceMode("listen", false);
  }
}

function setWorkspaceMode(mode, persist = true) {
  const isListen = mode === "listen";
  appShell?.classList.toggle("mode-listen", isListen);
  btnModeListen?.classList.toggle("active", isListen);
  btnModeStudio?.classList.toggle("active", !isListen);
  btnModeListen?.setAttribute("aria-selected", String(isListen));
  btnModeStudio?.setAttribute("aria-selected", String(!isListen));
  if (persist) {
    localStorage.setItem("vp_ui_mode", mode);
  }
}

function setSidebarCollapsed(collapsed, persist = true) {
  appShell?.classList.toggle("sidebar-collapsed", collapsed);
  if (btnToggleSidebar) {
    btnToggleSidebar.setAttribute("aria-expanded", String(!collapsed));
    btnToggleSidebar.textContent = tUi(
      collapsed ? "btn_toggle_sidebar_show" : "btn_toggle_sidebar_hide"
    );
  }
  if (persist) {
    localStorage.setItem("vp_sidebar_collapsed", collapsed ? "1" : "0");
  }
}

btnModeListen?.addEventListener("click", () => setWorkspaceMode("listen"));
btnModeStudio?.addEventListener("click", () => {
  setWorkspaceMode("studio");
  setSidebarCollapsed(false);
});
btnToggleSidebar?.addEventListener("click", () => {
  const next = !appShell?.classList.contains("sidebar-collapsed");
  setSidebarCollapsed(next);
});

btnExportMenu?.addEventListener("click", (e) => {
  e.stopPropagation();
  if (!exportDropdown) return;
  const willOpen = exportDropdown.hidden;
  exportDropdown.hidden = !willOpen;
  btnExportMenu.setAttribute("aria-expanded", String(willOpen));
});

document.addEventListener("click", (e) => {
  if (!exportDropdown || exportDropdown.hidden) return;
  if (!e.target.closest(".export-menu-wrap")) {
    exportDropdown.hidden = true;
    btnExportMenu?.setAttribute("aria-expanded", "false");
  }
});

const savedMode = localStorage.getItem("vp_ui_mode");
if (savedMode === "listen" || savedMode === "studio") {
  setWorkspaceMode(savedMode, false);
}
if (localStorage.getItem("vp_sidebar_collapsed") === "1") {
  setSidebarCollapsed(true, false);
}

langBtns.forEach((btn) => {
  btn.addEventListener("click", async () => {
    const nextLang = btn.dataset.uiLang;
    if (!nextLang || nextLang === state.currentUiLang) return;
    state.currentUiLang = nextLang;
    localStorage.setItem("laoma_ui_lang", nextLang);
    syncLangButtons(nextLang);
    await loadMeta(nextLang);
  });
});

btnOpenRss?.addEventListener("click", async () => {
  try {
    const res = await fetch("/api/rss-url");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const rssUrl = data?.url || "/feed.xml";
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(rssUrl).catch(() => {});
    }
    if (genStatus) {
      genStatus.textContent = tUi("rss_copied", { url: rssUrl });
    }
    window.open(rssUrl, "_blank", "noopener,noreferrer");
  } catch {
    window.open("/feed.xml", "_blank", "noopener,noreferrer");
  }
});

const PREFS_STORAGE_KEY = "vp_studio_prefs";
const PREF_FIELD_IDS = [
  "host-a",
  "host-b",
  "backend-select",
  "model-input",
  "long-text-mode",
  "voice-speaker-1",
  "voice-speaker-2",
  "voice-speaker-3",
  "chk-loudnorm",
  "chk-sfx",
  "chk-show-memory",
];

function saveStudioPrefs() {
  const data = {};
  for (const id of PREF_FIELD_IDS) {
    const el = document.getElementById(id);
    if (!el) continue;
    data[id] = el.type === "checkbox" ? el.checked : el.value;
  }
  localStorage.setItem(PREFS_STORAGE_KEY, JSON.stringify(data));
}

function restoreStudioPrefs() {
  try {
    const raw = localStorage.getItem(PREFS_STORAGE_KEY);
    if (!raw) return;
    const data = JSON.parse(raw);
    for (const id of PREF_FIELD_IDS) {
      if (!(id in data)) continue;
      const el = document.getElementById(id);
      if (!el) continue;
      if (el.type === "checkbox") {
        el.checked = Boolean(data[id]);
      } else if (el.tagName === "SELECT") {
        if (Array.from(el.options).some((opt) => opt.value === data[id])) {
          el.value = data[id];
        }
      } else {
        el.value = data[id] ?? "";
      }
    }
  } catch {
  }
}

const prefsModal = document.getElementById("prefs-modal");
const btnClosePrefs = document.getElementById("btn-close-prefs");
const btnPrefsDone = document.getElementById("btn-prefs-done");
const sbShowName = document.getElementById("sb-show-name");
const sbHostAPersona = document.getElementById("sb-host-a-persona");
const sbHostBPersona = document.getElementById("sb-host-b-persona");
const sbCallbacks = document.getElementById("sb-callbacks");
const sbCliches = document.getElementById("sb-cliches");
const btnSaveShowBible = document.getElementById("btn-save-show-bible");
const sbStatus = document.getElementById("sb-status");

async function loadShowBible() {
  if (!sbShowName) return;
  try {
    const res = await fetch("/api/show-bible");
    if (!res.ok) return;
    const data = await res.json();
    sbShowName.value = data.show_name || "";
    if (sbHostAPersona) sbHostAPersona.value = data.host_a_persona || "";
    if (sbHostBPersona) sbHostBPersona.value = data.host_b_persona || "";
    if (sbCallbacks) sbCallbacks.value = (data.recurring_callbacks || []).join("\n");
    if (sbCliches) sbCliches.value = (data.banned_cliches || []).join("\n");
  } catch {
  }
}

btnSaveShowBible?.addEventListener("click", async () => {
  if (!sbShowName) return;
  btnSaveShowBible.disabled = true;
  if (sbStatus) sbStatus.textContent = "";
  try {
    const splitLines = (el) =>
      (el?.value || "")
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean);
    const res = await fetch(
      `/api/show-bible?lang=${encodeURIComponent(state.currentUiLang)}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          show_name: sbShowName.value.trim(),
          host_a_persona: sbHostAPersona?.value.trim() || "",
          host_b_persona: sbHostBPersona?.value.trim() || "",
          recurring_callbacks: splitLines(sbCallbacks),
          banned_cliches: splitLines(sbCliches),
        }),
      }
    );
    if (!res.ok) throw new Error(await readErrorDetail(res));
    if (sbStatus) sbStatus.textContent = tUi("sb_saved");
  } catch (err) {
    if (sbStatus) sbStatus.textContent = tUi("sb_save_failed", { detail: err.message });
  } finally {
    btnSaveShowBible.disabled = false;
  }
});

function setPrefsModalOpen(open) {
  if (!prefsModal) return;
  prefsModal.hidden = !open;
  if (open) {
    if (sbStatus) sbStatus.textContent = "";
    loadShowBible();
    btnClosePrefs?.focus();
  } else {
    btnOpenPrefs?.focus();
  }
}

btnOpenPrefs?.addEventListener("click", () => setPrefsModalOpen(true));
btnClosePrefs?.addEventListener("click", () => setPrefsModalOpen(false));
btnPrefsDone?.addEventListener("click", () => setPrefsModalOpen(false));
prefsModal?.addEventListener("click", (e) => {
  if (e.target === prefsModal) setPrefsModalOpen(false);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && prefsModal && !prefsModal.hidden) {
    setPrefsModalOpen(false);
  }
});
for (const id of PREF_FIELD_IDS) {
  const el = document.getElementById(id);
  el?.addEventListener("change", saveStudioPrefs);
  if (el && el.tagName === "INPUT" && el.type === "text") {
    el.addEventListener("input", saveStudioPrefs);
  }
}

initPlayer();
initGenerator({
  onEpisodeCreated: loadEpisodes,
  onApplyMeta: applyMeta,
});

loadMeta(state.currentUiLang).then((ok) => {
  if (!ok) return;
  if (state.userRole === "admin") {
    restoreStudioPrefs();
    loadReports();
  }
  loadEpisodes();
});
