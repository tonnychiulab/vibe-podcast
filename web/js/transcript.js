import { escapeHtml, fmtTime, state, tStatus, tUi } from "./state.js";
import { seekAndPlay } from "./player.js";
import { readErrorDetail } from "./api.js";

const transcriptListEl = document.getElementById("transcript-list");
const chapterPillsEl = document.getElementById("chapter-pills");
const cardHostC = document.getElementById("card-host-c");
const labelHostC = document.getElementById("label-host-c");
const tagSpeakerC = document.getElementById("tag-speaker-c");
const roleC = document.getElementById("role-c");
const genStatus = document.getElementById("gen-status");

const INVALID_SPEAKER_NAMES = new Set([
  "hook",
  "deep_dive",
  "challenge",
  "takeaway",
  "speaker 1",
  "speaker 2",
  "speaker 3",
  "speaker_1",
  "speaker_2",
  "speaker_3",
]);

function resolveSpeakerName(speaker, rawName, meta) {
  const trimmed = (rawName || "").trim();
  if (trimmed && !INVALID_SPEAKER_NAMES.has(trimmed.toLowerCase())) {
    return trimmed;
  }
  if (speaker === "Speaker 1") return meta?.host_a || "Speaker 1";
  if (speaker === "Speaker 2") return meta?.host_b || "Speaker 2";
  if (speaker === "Speaker 3") return meta?.guest_name || tUi("speaker_3_tag") || "Speaker 3";
  return trimmed || speaker || "";
}

export function updateGuestCard(guestName) {
  if (!cardHostC) return;
  cardHostC.hidden = false;
  const avatarEl = cardHostC.querySelector(".avatar");
  if (guestName) {
    cardHostC.classList.remove("no-guest");
    if (tagSpeakerC) tagSpeakerC.textContent = tUi("speaker_3_tag");
    if (labelHostC) labelHostC.textContent = guestName;
    if (roleC) roleC.textContent = tUi("role_guest");
    if (avatarEl) avatarEl.textContent = guestName.slice(0, 1).toUpperCase();
  } else {
    cardHostC.classList.add("no-guest");
    cardHostC.classList.remove("speaking");
    if (tagSpeakerC) tagSpeakerC.textContent = tUi("no_guest_tag");
    if (labelHostC) labelHostC.textContent = tUi("no_guest_name");
    if (roleC) roleC.textContent = tUi("no_guest_role");
    if (avatarEl) avatarEl.textContent = "🍜";
  }
}

export function renderTranscript(segments, onEpisodeReload) {
  transcriptListEl.innerHTML = "";
  chapterPillsEl.innerHTML = "";
  const seenChapters = new Set();

  for (const seg of segments) {
    if (seg.chapter && !seenChapters.has(seg.chapter)) {
      seenChapters.add(seg.chapter);
      const pill = document.createElement("button");
      pill.type = "button";
      pill.className = "chapter-pill";
      pill.dataset.chapter = seg.chapter;
      const chLabel = state.appMeta?.chapter_labels?.[seg.chapter] || seg.chapter;
      pill.textContent = `${chLabel} · ${fmtTime(seg.start_sec)}`;
      pill.addEventListener("click", () => seekAndPlay(seg.start_sec, { forceScroll: true }));
      chapterPillsEl.appendChild(pill);
    }

    const div = document.createElement("div");
    const spNum = seg.speaker.split(" ")[1] || "1";
    div.className = `turn-bubble speaker-${spNum}`;
    div.dataset.index = seg.index;
    const chBadge = seg.chapter
      ? `<span class="chapter-tag">${escapeHtml(
          state.appMeta?.chapter_labels?.[seg.chapter] || seg.chapter
        )}</span>`
      : "";
    const canEditTurn =
      state.userRole === "admin" && state.currentEpisode?.engine === "edge_dialogue";
    const editBtnHtml = canEditTurn
      ? `<button type="button" class="btn-edit-turn" title="${escapeHtml(tUi("btn_edit_turn_title"))}">✏️</button>`
      : "";
    const displaySpeaker = resolveSpeakerName(seg.speaker, seg.speaker_name, state.currentEpisode);
    div.innerHTML = `
      <div class="turn-head">
        <span><strong>${escapeHtml(displaySpeaker)}</strong>${chBadge}${editBtnHtml}</span>
        <span>${fmtTime(seg.start_sec)} – ${fmtTime(seg.end_sec)}</span>
      </div>
      <div class="turn-text">${escapeHtml(seg.text)}</div>
    `;
    div.addEventListener("click", () => seekAndPlay(seg.start_sec));
    const editBtn = div.querySelector(".btn-edit-turn");
    if (editBtn) {
      editBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        if (div.querySelector(".turn-edit-row")) return;
        const row = document.createElement("div");
        row.className = "turn-edit-row";
        row.innerHTML = `<textarea class="draft-textarea" rows="2"></textarea><button type="button" class="btn-primary btn-sm">${escapeHtml(tUi("btn_resynth"))}</button>`;
        const ta = row.querySelector("textarea");
        const saveBtn = row.querySelector("button");
        ta.value = seg.text;
        row.addEventListener("click", (ev) => ev.stopPropagation());
        saveBtn.addEventListener("click", async () => {
          const newText = ta.value.trim();
          if (!newText) return;
          saveBtn.disabled = true;
          saveBtn.textContent = tUi("btn_resynthing");
          try {
            const res = await fetch(
              `/api/episodes/${encodeURIComponent(state.currentEpisode.episode_id)}/turns/${seg.index}`,
              {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ text: newText, ui_lang: state.currentUiLang }),
              }
            );
            if (!res.ok) throw new Error(await readErrorDetail(res));
            await onEpisodeReload(state.currentEpisode.episode_id);
          } catch (err) {
            genStatus.textContent = tStatus("generate_failed", { detail: err.message });
            saveBtn.disabled = false;
            saveBtn.textContent = tUi("btn_resynth");
          }
        });
        div.appendChild(row);
      });
    }
    transcriptListEl.appendChild(div);
  }
}
