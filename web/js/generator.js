import { readErrorDetail } from "./api.js";
import { state, tStatus, tUi } from "./state.js";

const SOURCE_LIMIT = 12000;

const sourceUrlInput = document.getElementById("source-url");
const reportSelect = document.getElementById("report-select");
const sourceTextarea = document.getElementById("source-text");
const sourceStatsEl = document.getElementById("source-stats");
const sourceOverrideHintEl = document.getElementById("source-override-hint");
const guestNameInput = document.getElementById("guest-name");
const formatSelect = document.getElementById("format-select");
const formatGrid = document.getElementById("format-grid");
const focusQuestionsInput = document.getElementById("focus-questions");
const backendSelect = document.getElementById("backend-select");
const modelInput = document.getElementById("model-input");
const longTextModeSelect = document.getElementById("long-text-mode");
const voiceSpk1 = document.getElementById("voice-speaker-1");
const voiceSpk2 = document.getElementById("voice-speaker-2");
const voiceSpk3 = document.getElementById("voice-speaker-3");
const chkLoudnorm = document.getElementById("chk-loudnorm");
const chkSfx = document.getElementById("chk-sfx");
const chkShowMemory = document.getElementById("chk-show-memory");
const generateBtn = document.getElementById("generate-btn");
const genStatus = document.getElementById("gen-status");
const btnUploadSource = document.getElementById("btn-upload-source");
const sourceFileInput = document.getElementById("source-file-input");
const btnImportTpl = document.getElementById("btn-import-tpl");
const btnDownloadTpl = document.getElementById("btn-download-tpl");
const tplFileInput = document.getElementById("tpl-file-input");

export function updateSourceHints() {
  const chars = sourceTextarea.value.length;
  const estTokens = Math.ceil(chars / 2.2);
  if (chars > SOURCE_LIMIT) {
    sourceStatsEl.textContent = tUi("char_count_warn", {
      chars,
      tokens: estTokens,
      limit: SOURCE_LIMIT,
    });
    sourceStatsEl.classList.add("warn");
  } else {
    sourceStatsEl.textContent = tUi("char_count_ok", { chars, tokens: estTokens });
    sourceStatsEl.classList.remove("warn");
  }
  const hasOverride = Boolean(sourceUrlInput.value.trim() || reportSelect.value);
  sourceOverrideHintEl.hidden = !(hasOverride && chars > 0);
}

const FORMAT_DEFAULT_TURNS = {
  quick_brief: 8,
  noodle_chat: 12,
  debate: 14,
  deep_dive: 16,
};
let turnsDirty = false;
const turnsInputEl = document.getElementById("turns-input");
turnsInputEl?.addEventListener("input", () => {
  turnsDirty = true;
});

export function setInterviewFormat(formatId, { applyDefaultTurns = false } = {}) {
  if (!formatSelect) return;
  const chosen = formatId || "noodle_chat";
  formatSelect.value = chosen;
  if (applyDefaultTurns && !turnsDirty && turnsInputEl && FORMAT_DEFAULT_TURNS[chosen]) {
    turnsInputEl.value = String(FORMAT_DEFAULT_TURNS[chosen]);
  }
  if (!formatGrid) return;
  formatGrid.querySelectorAll(".format-btn").forEach((btn) => {
    const active = btn.dataset.formatId === chosen;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-pressed", String(active));
  });
}

export function renderFormatGrid(formats = []) {
  if (!formatGrid || !formatSelect) return;
  const prevFormat = formatSelect.value;
  formatGrid.innerHTML = "";
  for (const fmt of formats) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "format-btn";
    btn.dataset.formatId = fmt.id;
    btn.textContent = fmt.name;
    btn.title = fmt.description || "";
    btn.addEventListener("click", () =>
      setInterviewFormat(fmt.id, { applyDefaultTurns: true })
    );
    formatGrid.appendChild(btn);
  }
  const chosen =
    prevFormat && formats.some((f) => f.id === prevFormat)
      ? prevFormat
      : formats[0]?.id || "noodle_chat";
  setInterviewFormat(chosen);
}

export function getSelectedVoiceMap() {
  const vm = {};
  if (voiceSpk1?.value) vm["Speaker 1"] = voiceSpk1.value;
  if (voiceSpk2?.value) vm["Speaker 2"] = voiceSpk2.value;
  if (voiceSpk3?.value) vm["Speaker 3"] = voiceSpk3.value;
  return Object.keys(vm).length ? vm : null;
}

export function buildGeneratePayload() {
  const sourceUrl = sourceUrlInput.value.trim();
  const reportPath = reportSelect.value;
  const sourceText = sourceTextarea.value.trim();
  if (!sourceUrl && !reportPath && !sourceText) {
    return null;
  }
  return {
    source_url: sourceUrl || null,
    report_path: !sourceUrl && reportPath ? reportPath : null,
    source_text: !sourceUrl && !reportPath ? sourceText : null,
    host_a: document.getElementById("host-a").value || "志明",
    host_b: document.getElementById("host-b").value || "春嬌",
    guest_name: guestNameInput.value.trim() || null,
    interview_format: formatSelect.value || "noodle_chat",
    focus_questions: focusQuestionsInput.value
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean),
    language: document.getElementById("lang-select").value,
    ui_lang: state.currentUiLang,
    target_turns: Number(document.getElementById("turns-input").value) || 12,
    backend: backendSelect.value || "auto",
    model: modelInput.value.trim() || null,
    long_text_mode: longTextModeSelect?.value || "map_reduce",
    voice_map: getSelectedVoiceMap(),
    enable_loudnorm: chkLoudnorm ? chkLoudnorm.checked : true,
    enable_sfx: chkSfx ? chkSfx.checked : false,
    enable_show_memory: chkShowMemory ? chkShowMemory.checked : true,
  };
}

async function uploadSourceFile(file) {
  if (!file) return;
  genStatus.textContent = `📄 讀取檔案 ${file.name}...`;
  try {
    const buf = await file.arrayBuffer();
    const bytes = new Uint8Array(buf);
    let binary = "";
    const chunkSize = 0x8000;
    for (let i = 0; i < bytes.length; i += chunkSize) {
      binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
    }
    const contentBase64 = btoa(binary);
    const res = await fetch("/api/upload-source", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        filename: file.name,
        content_base64: contentBase64,
        ui_lang: state.currentUiLang,
      }),
    });
    if (!res.ok) throw new Error(await readErrorDetail(res));
    const data = await res.json();
    sourceUrlInput.value = "";
    reportSelect.value = "";
    sourceTextarea.value = data.text;
    updateSourceHints();
    genStatus.textContent = `✅ 已匯入 ${data.filename}（${data.chars} 字元）`;
  } catch (err) {
    genStatus.textContent = tStatus("generate_failed", { detail: err.message });
  } finally {
    sourceFileInput.value = "";
  }
}

export function initGenerator({ onEpisodeCreated, onApplyMeta }) {
  sourceTextarea.addEventListener("input", updateSourceHints);
  sourceUrlInput.addEventListener("input", updateSourceHints);
  reportSelect.addEventListener("change", updateSourceHints);

  const sourceTabs = document.querySelectorAll(".source-tab");
  const paneText = document.getElementById("pane-src-text");
  const paneUrl = document.getElementById("pane-src-url");
  const paneReport = document.getElementById("pane-src-report");

  const audioLangInput = document.getElementById("lang-select");
  const audioLangBtns = document.querySelectorAll(".audio-lang-btn");
  const setAudioLanguage = (lang) => {
    const target = lang || "zh-TW";
    if (audioLangInput) audioLangInput.value = target;
    audioLangBtns.forEach((btn) => {
      const active = btn.dataset.audioLang === target;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-pressed", String(active));
    });
  };
  audioLangBtns.forEach((btn) => {
    btn.addEventListener("click", () => setAudioLanguage(btn.dataset.audioLang));
  });
  setAudioLanguage(audioLangInput?.value || "zh-TW");
  sourceTabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      const target = tab.dataset.sourceTab;
      sourceTabs.forEach((t) => {
        const isCurrent = t === tab;
        t.classList.toggle("active", isCurrent);
        t.setAttribute("aria-selected", String(isCurrent));
      });
      if (paneText) paneText.hidden = target !== "text";
      if (paneUrl) paneUrl.hidden = target !== "url";
      if (paneReport) paneReport.hidden = target !== "report";
      if (target !== "url") sourceUrlInput.value = "";
      if (target !== "report") reportSelect.value = "";
      updateSourceHints();
    });
  });

  btnUploadSource?.addEventListener("click", () => sourceFileInput.click());
  sourceFileInput?.addEventListener("change", () => uploadSourceFile(sourceFileInput.files?.[0]));
  sourceTextarea?.addEventListener("dragover", (e) => e.preventDefault());
  sourceTextarea?.addEventListener("drop", (e) => {
    const file = e.dataTransfer?.files?.[0];
    if (file) {
      e.preventDefault();
      uploadSourceFile(file);
    }
  });

  generateBtn.addEventListener("click", async () => {
    const payload = buildGeneratePayload();
    if (!payload) {
      genStatus.textContent = tStatus("need_input");
      return;
    }
    generateBtn.disabled = true;
    genStatus.textContent = tStatus("generating");

    try {
      const res = await fetch("/api/generate/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(await readErrorDetail(res));

      const reader = res.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      let created = null;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop() || "";
        for (const block of parts) {
          const lines = block.split("\n");
          let ev = "message";
          let dataStr = "";
          for (const line of lines) {
            if (line.startsWith("event:")) ev = line.slice(6).trim();
            else if (line.startsWith("data:")) dataStr += line.slice(5).trim();
          }
          if (!dataStr) continue;
          const data = JSON.parse(dataStr);
          if (ev === "progress") {
            const pct = data.total > 0 ? ` (${data.current}/${data.total})` : "";
            genStatus.textContent = `${tStatus("generating")} [${data.stage}${pct}]`;
          } else if (ev === "error") {
            throw new Error(data.detail || "Generation failed");
          } else if (ev === "done") {
            created = data;
          }
        }
      }

      if (!created) throw new Error("Stream ended without result");
      genStatus.textContent = tStatus("generated", { title: created.title });
      await onEpisodeCreated(created.episode_id);
    } catch (err) {
      genStatus.textContent = tStatus("generate_failed", { detail: err.message });
    } finally {
      generateBtn.disabled = false;
    }
  });

  btnImportTpl.addEventListener("click", () => tplFileInput.click());

  tplFileInput.addEventListener("change", async () => {
    const file = tplFileInput.files?.[0];
    if (!file) return;
    try {
      const raw = await file.text();
      const tpl = JSON.parse(raw);

      if (tpl.custom_preset && typeof tpl.custom_preset === "object") {
        const res = await fetch(
          `/api/presets/import?lang=${encodeURIComponent(state.currentUiLang)}`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(tpl.custom_preset),
          }
        );
        if (!res.ok) throw new Error(await readErrorDetail(res));
        const data = await res.json();
        if (data.meta) onApplyMeta(data.meta);
        if (tpl.custom_preset.id) setInterviewFormat(tpl.custom_preset.id);
      }

      if (tpl.host_a) document.getElementById("host-a").value = tpl.host_a;
      if (tpl.host_b) document.getElementById("host-b").value = tpl.host_b;
      if (tpl.guest_name !== undefined) guestNameInput.value = tpl.guest_name || "";
      if (tpl.interview_format) setInterviewFormat(tpl.interview_format);
      if (Array.isArray(tpl.focus_questions)) {
        focusQuestionsInput.value = tpl.focus_questions.join("\n");
      }
      if (tpl.language) setAudioLanguage(tpl.language);
      if (tpl.target_turns) {
        if (turnsInputEl) turnsInputEl.value = tpl.target_turns;
        turnsDirty = true;
      }
      if (tpl.source_text) {
        document.getElementById("tab-src-text")?.click();
        sourceTextarea.value = tpl.source_text;
        updateSourceHints();
      }

      genStatus.textContent = tStatus("import_ok", { name: tpl.name || file.name });
    } catch (err) {
      genStatus.textContent = tStatus("import_failed", { detail: err.message });
    } finally {
      tplFileInput.value = "";
    }
  });

  btnDownloadTpl.addEventListener("click", () => {
    const sample = {
      name: "老馬麵攤 — 資安紅隊攻防範本",
      host_a: "志明",
      host_b: "春嬌",
      guest_name: "阿華",
      language: "zh-TW",
      target_turns: 12,
      focus_questions: [
        "這個架構在實際部署時最容易被忽略的風險是什麼？",
        "如果記憶體預算有限，有什麼務實的折衷方案？",
      ],
      custom_preset: {
        id: "security_red_team",
        name_zh: "🔒 資安紅隊攻防風",
        name_en: "🔒 Security Red-Team",
        description_zh: "聚焦漏洞分析、攻擊面與防禦成本",
        description_en: "Focuses on attack surface and defense trade-offs",
        host_a_role: "資安長視角，負責追問攻擊面與合規風險",
        host_b_role: "紅隊滲透專家，負責拆解真實漏洞利用路徑",
        guest_role: "系統架構代表，負責說明修補成本與防護設計",
        pacing_instruction: "保持節奏緊湊，每輪都要點出具體風險與務實防禦建議。",
      },
    };
    const blob = new Blob([JSON.stringify(sample, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "laoma-podcast-template.json";
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });

  const advancedTabs = document.querySelectorAll(".advanced-tab[data-drawer]");
  advancedTabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      const targetId = tab.dataset.drawer;
      const wasActive = tab.classList.contains("active");
      advancedTabs.forEach((other) => {
        const shouldOpen = !wasActive && other === tab;
        other.classList.toggle("active", shouldOpen);
        other.setAttribute("aria-expanded", String(shouldOpen));
        const drawer = document.getElementById(other.dataset.drawer);
        if (drawer) drawer.hidden = !shouldOpen;
      });
      if (!wasActive && targetId) {
        document.getElementById(targetId)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
    });
  });
}
