import { fmtTime, state, tUi } from "./state.js";

const audioEl = document.getElementById("audio-player");
const canvas = document.getElementById("waveform-canvas");
const ctx = canvas.getContext("2d");
const waveformTooltip = document.getElementById("waveform-tooltip");
const timeReadout = document.getElementById("time-readout");
const cardHostA = document.getElementById("card-host-a");
const cardHostB = document.getElementById("card-host-b");
const cardHostC = document.getElementById("card-host-c");
const transcriptListEl = document.getElementById("transcript-list");
const chapterPillsEl = document.getElementById("chapter-pills");
const btnAutoFollow = document.getElementById("btn-auto-follow");
const btnPlayerSettings = document.getElementById("btn-player-settings");
const playerSettingsMenu = document.getElementById("player-settings-menu");
const speedBadge = document.getElementById("speed-badge");
const speedPills = document.querySelectorAll(".speed-pill");
const chkLoop = document.getElementById("chk-loop");

export function applyTranscriptFontSize(size) {
  const allowed = ["13px", "14px", "16px"];
  const chosen = allowed.includes(size) ? size : "14px";
  document.documentElement.style.setProperty("--transcript-fs", chosen);
  document.querySelectorAll(".font-size-pill").forEach((pill) => {
    const active = pill.dataset.fontSize === chosen;
    pill.classList.toggle("active", active);
    pill.setAttribute("aria-pressed", String(active));
  });
}

export function applyPlaybackSpeed(speed) {
  const spd = Number(speed) || 1;
  audioEl.playbackRate = spd;
  if (speedBadge) speedBadge.textContent = `${spd}x`;
  speedPills.forEach((pill) => {
    const active = Number(pill.dataset.speed) === spd;
    pill.classList.toggle("active", active);
    pill.setAttribute("aria-pressed", String(active));
  });
}

let audioCtx = null;
let analyser = null;
let sourceNode = null;
let dataArray = null;
let waveformRafId = null;
let pendingSeekSec = null;
let autoFollow = true;
let lastActiveIndex = null;
let manualScrollUntil = 0;

const WAVE_BARS = 140;
let waveformPeaks = null;
let lastDecodedSrc = "";
let hoverRatio = null;
let isScrubbing = false;

export function updateAutoFollowLabel() {
  if (!btnAutoFollow) return;
  btnAutoFollow.classList.toggle("active", autoFollow);
  btnAutoFollow.setAttribute("aria-pressed", String(autoFollow));
  btnAutoFollow.textContent = tUi(
    autoFollow ? "btn_auto_follow_on" : "btn_auto_follow_off"
  );
}

let scrollRafId = null;

function scrollTurnIntoView(el, instant = false) {
  if (!el || !transcriptListEl) return;
  if (scrollRafId !== null) {
    cancelAnimationFrame(scrollRafId);
  }
  scrollRafId = requestAnimationFrame(() => {
    scrollRafId = null;
    if (!el.isConnected || !transcriptListEl.isConnected) return;
    const listRect = transcriptListEl.getBoundingClientRect();
    const elRect = el.getBoundingClientRect();
    const topPadding = 4;
    const targetTop =
      transcriptListEl.scrollTop + (elRect.top - listRect.top) - topPadding;
    transcriptListEl.scrollTo({
      top: Math.max(0, targetTop),
      behavior: instant ? "auto" : "smooth",
    });
  });
}

export function setPendingSeek(sec) {
  pendingSeekSec = sec;
}

function setupWebAudio() {
  if (audioCtx) return;
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  audioCtx = new AudioContextClass();
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 128;
  sourceNode = audioCtx.createMediaElementSource(audioEl);
  sourceNode.connect(analyser);
  analyser.connect(audioCtx.destination);
  dataArray = new Uint8Array(analyser.frequencyBinCount);
}

function speakerColor(speaker, alpha = 1) {
  if (speaker === "Speaker 1") return `rgba(0, 120, 212, ${alpha})`;
  if (speaker === "Speaker 2") return `rgba(127, 186, 0, ${alpha})`;
  if (speaker === "Speaker 3") return `rgba(255, 185, 0, ${alpha})`;
  return `rgba(140, 140, 140, ${alpha})`;
}

function findSegmentAt(t) {
  const segs = state.currentEpisode?.segments || [];
  for (let i = 0; i < segs.length; i++) {
    const seg = segs[i];
    const nextStart = i + 1 < segs.length ? segs[i + 1].start_sec : seg.end_sec;
    const effectiveEnd = Math.max(seg.end_sec, nextStart);
    if (t >= seg.start_sec && (i + 1 < segs.length ? t < effectiveEnd : t <= effectiveEnd)) {
      return seg;
    }
  }
  return null;
}

function buildFallbackPeaks() {
  const ep = state.currentEpisode;
  if (!ep || !ep.duration_sec) return null;
  const peaks = new Float32Array(WAVE_BARS);
  const dur = ep.duration_sec;
  for (let i = 0; i < WAVE_BARS; i++) {
    const t = ((i + 0.5) / WAVE_BARS) * dur;
    const seg = findSegmentAt(t);
    if (!seg) {
      peaks[i] = 0.14;
    } else {
      const wave = 0.45 + 0.35 * Math.abs(Math.sin(i * 0.7 + seg.index * 1.3));
      peaks[i] = Math.min(1, wave);
    }
  }
  return peaks;
}

async function decodeEpisodeWaveform(src) {
  if (!src || src === lastDecodedSrc) return;
  lastDecodedSrc = src;
  waveformPeaks = buildFallbackPeaks();
  renderWaveform();

  try {
    const res = await fetch(src, { credentials: "same-origin" });
    if (!res.ok) return;
    const buf = await res.arrayBuffer();
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) return;
    const tempCtx = new AudioContextClass();
    try {
      const audioBuf = await tempCtx.decodeAudioData(buf);
      if (src !== lastDecodedSrc) return;
      const channel = audioBuf.getChannelData(0);
      const blockSize = Math.max(1, Math.floor(channel.length / WAVE_BARS));
      const peaks = new Float32Array(WAVE_BARS);
      let maxPeak = 0.001;
      for (let i = 0; i < WAVE_BARS; i++) {
        const start = i * blockSize;
        const end = Math.min(channel.length, start + blockSize);
        let sumSq = 0;
        const stride = Math.max(1, Math.floor((end - start) / 64));
        let count = 0;
        for (let j = start; j < end; j += stride) {
          const s = channel[j];
          sumSq += s * s;
          count++;
        }
        const rms = count > 0 ? Math.sqrt(sumSq / count) : 0;
        peaks[i] = rms;
        if (rms > maxPeak) maxPeak = rms;
      }
      for (let i = 0; i < WAVE_BARS; i++) {
        peaks[i] = Math.max(0.1, Math.min(1, peaks[i] / maxPeak));
      }
      waveformPeaks = peaks;
      renderWaveform();
    } finally {
      tempCtx.close().catch(() => {});
    }
  } catch {
    // Keep segment-based fallback peaks if decodeAudioData is unavailable
  }
}

function getEffectiveDuration(ep = state.currentEpisode) {
  return Number.isFinite(audioEl.duration) && audioEl.duration > 0
    ? audioEl.duration
    : ep?.duration_sec || 0;
}

function playAudioSafely() {
  audioEl.play().catch((err) => console.warn("Audio playback blocked:", err));
}

export function renderWaveform() {
  const w = canvas.width;
  const h = canvas.height;
  ctx.fillStyle = "#141414";
  ctx.fillRect(0, 0, w, h);

  const ep = state.currentEpisode;
  const duration = getEffectiveDuration(ep);

  if (!ep || duration <= 0) {
    ctx.strokeStyle = "#363636";
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(0, h / 2);
    ctx.lineTo(w, h / 2);
    ctx.stroke();
    return;
  }

  const peaks = waveformPeaks || buildFallbackPeaks();
  const currentTime = audioEl.currentTime || 0;
  const progressRatio = Math.max(0, Math.min(1, currentTime / duration));
  const segs = ep.segments || [];

  let liveBoost = 0;
  if (analyser && dataArray && !audioEl.paused && !audioEl.ended) {
    analyser.getByteFrequencyData(dataArray);
    let sum = 0;
    const len = Math.min(24, dataArray.length);
    for (let k = 0; k < len; k++) sum += dataArray[k];
    liveBoost = sum / (len * 255);
  }

  // Bottom speaker timeline ribbon (4px)
  const ribbonH = 4;
  const ribbonY = h - ribbonH;
  for (const seg of segs) {
    const x1 = (seg.start_sec / duration) * w;
    const x2 = (Math.max(seg.end_sec, seg.start_sec) / duration) * w;
    ctx.fillStyle = speakerColor(seg.speaker, 0.75);
    ctx.fillRect(x1, ribbonY, Math.max(1, x2 - x1), ribbonH);
  }

  // Chapter boundary ticks
  const seenChapters = new Set();
  ctx.save();
  ctx.font = "10px -apple-system, BlinkMacSystemFont, sans-serif";
  for (const seg of segs) {
    if (!seg.chapter || seenChapters.has(seg.chapter)) continue;
    seenChapters.add(seg.chapter);
    const x = Math.round((seg.start_sec / duration) * w);
    if (x > 4) {
      ctx.strokeStyle = "rgba(255, 255, 255, 0.14)";
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, ribbonY);
      ctx.stroke();
      ctx.setLineDash([]);
    }
    const label = state.appMeta?.chapter_labels?.[seg.chapter] || seg.chapter;
    ctx.fillStyle = "rgba(220, 220, 220, 0.55)";
    ctx.fillText(label, Math.min(w - 70, x + 4), 12);
  }
  ctx.restore();

  // Static overview bars + live FFT pulse near playhead
  const topPad = 18;
  const usableH = ribbonY - topPad - 6;
  const barSlot = w / WAVE_BARS;
  const barW = Math.max(2, barSlot - 2);
  const playheadBar = Math.floor(progressRatio * WAVE_BARS);

  for (let i = 0; i < WAVE_BARS; i++) {
    const barRatio = (i + 0.5) / WAVE_BARS;
    const barTime = barRatio * duration;
    const seg = findSegmentAt(barTime);
    const isPlayed = barRatio <= progressRatio;
    const baseAmp = peaks ? peaks[i] : 0.3;
    const dist = Math.abs(i - playheadBar);
    const pulse = liveBoost > 0 && dist < 8 ? liveBoost * (1 - dist / 8) * 0.45 : 0;
    const amp = Math.min(1, baseAmp + pulse);
    const barH = Math.max(4, amp * usableH);
    const x = i * barSlot + 1;
    const y = topPad + (usableH - barH) / 2;

    ctx.fillStyle = speakerColor(seg?.speaker, isPlayed ? 0.95 : 0.28);
    ctx.fillRect(x, y, barW, barH);
  }

  // Hover guide line
  if (hoverRatio !== null) {
    const hx = Math.round(hoverRatio * w);
    ctx.strokeStyle = "rgba(255, 255, 255, 0.45)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(hx, 0);
    ctx.lineTo(hx, ribbonY);
    ctx.stroke();
  }

  // Playhead cursor
  const px = Math.round(progressRatio * w);
  ctx.strokeStyle = "#ffffff";
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(px, 0);
  ctx.lineTo(px, ribbonY);
  ctx.stroke();
  ctx.fillStyle = "#ffffff";
  ctx.beginPath();
  ctx.arc(px, 6, 3.5, 0, Math.PI * 2);
  ctx.fill();
}

export function stopWaveformLoop() {
  if (waveformRafId !== null) {
    cancelAnimationFrame(waveformRafId);
    waveformRafId = null;
  }
  renderWaveform();
}

function startWaveformLoop() {
  if (waveformRafId !== null) return;
  const tick = () => {
    if (audioEl.paused || audioEl.ended) {
      waveformRafId = null;
      renderWaveform();
      return;
    }
    renderWaveform();
    waveformRafId = requestAnimationFrame(tick);
  };
  waveformRafId = requestAnimationFrame(tick);
}

export function seekAndPlay(sec, { forceScroll = false } = {}) {
  if (!audioEl.src) return;
  const target = Math.max(0, sec);
  if (forceScroll) {
    manualScrollUntil = 0;
    lastActiveIndex = null;
  }
  if (audioEl.readyState >= 1) {
    audioEl.currentTime = target;
    updatePlaybackUI(target, { forceScroll });
    playAudioSafely();
  } else {
    pendingSeekSec = target;
    playAudioSafely();
  }
}

export function updatePlaybackUI(t, { forceScroll = false, instant = false } = {}) {
  if (!state.currentEpisode) return;
  timeReadout.textContent = `${fmtTime(t)} / ${fmtTime(state.currentEpisode.duration_sec)}`;

  const activeSeg = findSegmentAt(t);

  let activeEl = null;
  for (const el of transcriptListEl.children) {
    const idx = Number(el.dataset.index);
    const isActive = Boolean(activeSeg && idx === activeSeg.index);
    el.classList.toggle("active", isActive);
    if (isActive) activeEl = el;
  }

  if (chapterPillsEl) {
    for (const pill of chapterPillsEl.children) {
      pill.classList.toggle(
        "active",
        Boolean(activeSeg?.chapter && pill.dataset.chapter === activeSeg.chapter)
      );
    }
  }

  if (activeSeg && activeEl) {
    const changed = activeSeg.index !== lastActiveIndex;
    lastActiveIndex = activeSeg.index;
    const canAutoScroll = autoFollow && Date.now() >= manualScrollUntil;
    if (forceScroll || (changed && canAutoScroll)) {
      scrollTurnIntoView(activeEl, instant);
    }
  } else if (!activeSeg) {
    lastActiveIndex = null;
  }

  const sp = activeSeg?.speaker;
  cardHostA.classList.toggle("speaking", sp === "Speaker 1");
  cardHostB.classList.toggle("speaking", sp === "Speaker 2");
  cardHostC.classList.toggle("speaking", sp === "Speaker 3");
  if (audioEl.paused || audioEl.ended) {
    renderWaveform();
  }
}

export function initPlayer() {
  audioEl.addEventListener("loadedmetadata", () => {
    const hadPending = pendingSeekSec !== null;
    if (hadPending) {
      const maxDur = Number.isFinite(audioEl.duration) ? audioEl.duration : pendingSeekSec;
      audioEl.currentTime = Math.min(pendingSeekSec, maxDur);
      pendingSeekSec = null;
    }
    decodeEpisodeWaveform(audioEl.currentSrc || audioEl.src);
    updatePlaybackUI(audioEl.currentTime, { forceScroll: hadPending, instant: hadPending });
    renderWaveform();
  });
  audioEl.addEventListener("play", () => {
    setupWebAudio();
    if (audioCtx.state === "suspended") audioCtx.resume();
    startWaveformLoop();
  });
  audioEl.addEventListener("pause", stopWaveformLoop);
  audioEl.addEventListener("ended", () => {
    stopWaveformLoop();
    updatePlaybackUI(state.currentEpisode?.duration_sec || audioEl.currentTime);
  });
  audioEl.addEventListener("timeupdate", () => {
    updatePlaybackUI(audioEl.currentTime);
    if (state.currentEpisode?.episode_id && audioEl.currentTime > 0) {
      localStorage.setItem(
        `vp_pos_${state.currentEpisode.episode_id}`,
        String(Math.floor(audioEl.currentTime))
      );
    }
  });

  speedPills.forEach((pill) => {
    pill.addEventListener("click", () => {
      const spd = Number(pill.dataset.speed) || 1;
      applyPlaybackSpeed(spd);
      localStorage.setItem("vp_playback_speed", String(spd));
    });
  });

  const savedFontSize = localStorage.getItem("vp_transcript_fs") || "14px";
  applyTranscriptFontSize(savedFontSize);
  document.querySelectorAll(".font-size-pill").forEach((pill) => {
    pill.addEventListener("click", () => {
      const size = pill.dataset.fontSize || "14px";
      applyTranscriptFontSize(size);
      localStorage.setItem("vp_transcript_fs", size);
    });
  });

  chkLoop?.addEventListener("change", () => {
    audioEl.loop = Boolean(chkLoop.checked);
  });

  btnPlayerSettings?.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!playerSettingsMenu) return;
    const willOpen = playerSettingsMenu.hidden;
    playerSettingsMenu.hidden = !willOpen;
    btnPlayerSettings.setAttribute("aria-expanded", String(willOpen));
  });

  document.addEventListener("click", (e) => {
    if (!playerSettingsMenu || playerSettingsMenu.hidden) return;
    if (!e.target.closest(".player-settings-wrap")) {
      playerSettingsMenu.hidden = true;
      btnPlayerSettings?.setAttribute("aria-expanded", "false");
    }
  });

  const pauseAutoScrollBriefly = () => {
    if (autoFollow) manualScrollUntil = Date.now() + 6000;
  };
  transcriptListEl?.addEventListener("wheel", pauseAutoScrollBriefly, { passive: true });
  transcriptListEl?.addEventListener("touchmove", pauseAutoScrollBriefly, { passive: true });

  btnAutoFollow?.addEventListener("click", () => {
    autoFollow = !autoFollow;
    manualScrollUntil = 0;
    updateAutoFollowLabel();
    if (autoFollow) {
      updatePlaybackUI(audioEl.currentTime, { forceScroll: true });
    }
  });

  const ratioFromPointer = (e) => {
    const rect = canvas.getBoundingClientRect();
    if (!rect.width) return 0;
    return Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
  };

  const updateTooltip = (ratio) => {
    const ep = state.currentEpisode;
    const duration = getEffectiveDuration(ep);
    if (!waveformTooltip || !ep || duration <= 0) return;
    const targetSec = ratio * duration;
    const seg = findSegmentAt(targetSec);
    const chLabel = seg?.chapter
      ? state.appMeta?.chapter_labels?.[seg.chapter] || seg.chapter
      : "";
    const parts = [fmtTime(targetSec)];
    if (chLabel) parts.push(chLabel);
    if (seg?.speaker_name) parts.push(seg.speaker_name);
    waveformTooltip.textContent = parts.join(" · ");
    waveformTooltip.style.left = `${Math.max(8, Math.min(92, ratio * 100))}%`;
    waveformTooltip.hidden = false;
  };

  canvas.addEventListener("pointerdown", (e) => {
    if (!audioEl.src) return;
    const duration = getEffectiveDuration();
    if (duration <= 0) return;
    isScrubbing = true;
    canvas.setPointerCapture?.(e.pointerId);
    const ratio = ratioFromPointer(e);
    hoverRatio = ratio;
    const targetSec = ratio * duration;
    audioEl.currentTime = targetSec;
    updateTooltip(ratio);
    updatePlaybackUI(targetSec);
    renderWaveform();
  });

  canvas.addEventListener("pointermove", (e) => {
    const duration = getEffectiveDuration();
    if (!state.currentEpisode || duration <= 0) return;
    const ratio = ratioFromPointer(e);
    hoverRatio = ratio;
    updateTooltip(ratio);
    if (isScrubbing && audioEl.src) {
      const targetSec = ratio * duration;
      audioEl.currentTime = targetSec;
      updatePlaybackUI(targetSec);
    }
    if (audioEl.paused || audioEl.ended) renderWaveform();
  });

  const endScrub = (e) => {
    if (!isScrubbing) return;
    isScrubbing = false;
    canvas.releasePointerCapture?.(e.pointerId);
    updatePlaybackUI(audioEl.currentTime, { forceScroll: true });
    if (audioEl.paused && audioEl.src) {
      audioEl.play().catch(() => {});
    }
  };
  canvas.addEventListener("pointerup", endScrub);
  canvas.addEventListener("pointercancel", endScrub);
  canvas.addEventListener("pointerleave", () => {
    hoverRatio = null;
    if (waveformTooltip) waveformTooltip.hidden = true;
    if (audioEl.paused || audioEl.ended) renderWaveform();
  });

  document.addEventListener("keydown", (e) => {
    const active = document.activeElement;
    const tag = active?.tagName;
    if (
      tag === "INPUT" ||
      tag === "TEXTAREA" ||
      tag === "SELECT" ||
      tag === "BUTTON" ||
      tag === "SUMMARY" ||
      active?.isContentEditable
    ) {
      return;
    }
    if (!audioEl.src) return;
    if (e.code === "Space") {
      e.preventDefault();
      if (audioEl.paused) {
        playAudioSafely();
      } else {
        audioEl.pause();
      }
    } else if (e.code === "ArrowLeft") {
      e.preventDefault();
      audioEl.currentTime = Math.max(0, audioEl.currentTime - 10);
    } else if (e.code === "ArrowRight") {
      e.preventDefault();
      audioEl.currentTime = Math.min(
        audioEl.duration || audioEl.currentTime + 10,
        audioEl.currentTime + 10
      );
    }
  });

  renderWaveform();
}
