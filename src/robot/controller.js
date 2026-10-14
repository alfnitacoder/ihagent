import { normalizeState, STATES } from './states';

const SUCCESS_MS = 3200;

function now() {
  return typeof performance !== 'undefined' ? performance.now() : Date.now();
}

const controller = {
  state: 'idle',
  message: '',
  audioLevel: null,
  readAudio: null,
  stopAudio: null,
  reducedMotion: false,
  pointerX: 0,
  pointerY: 0,
  generation: 0,
  enteredAt: now(),
  idleTimer: null,
  listeners: new Set(),
};

function snapshot() {
  return {
    state: controller.state,
    message: controller.message,
    audioLevel: controller.audioLevel,
    generation: controller.generation,
    reducedMotion: controller.reducedMotion,
  };
}

function emit() {
  const snap = snapshot();
  controller.listeners.forEach((fn) => {
    try {
      fn(snap);
    } catch (err) {
      // A subscriber must not break the others.
    }
  });
}

export function getController() {
  return controller;
}

export function getSnapshot() {
  return snapshot();
}

export function subscribe(fn) {
  controller.listeners.add(fn);
  fn(snapshot());
  return () => controller.listeners.delete(fn);
}

/**
 * Move the robot to a state. Unknown names are ignored.
 * Pass `{ message }` to set the speech bubble (empty string clears it).
 * Pass `{ audioLevel }` only when a real analyser produced it.
 * Success plays out, then returns to idle. A newer call cancels that timer.
 */
export function setAgentState(next, extras) {
  const state = normalizeState(next);
  if (!state) return false;
  if (controller.idleTimer) {
    clearTimeout(controller.idleTimer);
    controller.idleTimer = null;
  }
  controller.generation += 1;
  controller.state = state;
  controller.enteredAt = now();
  if (extras && Object.prototype.hasOwnProperty.call(extras, 'message')) {
    controller.message = String(extras.message || '');
  }
  if (extras && typeof extras.audioLevel === 'number' && Number.isFinite(extras.audioLevel)) {
    controller.audioLevel = Math.min(1, Math.max(0, extras.audioLevel));
  } else {
    controller.audioLevel = null;
  }
  if (state === 'success') {
    const gen = controller.generation;
    controller.idleTimer = setTimeout(() => {
      if (controller.generation === gen) setAgentState('idle');
    }, SUCCESS_MS);
    if (controller.idleTimer.unref) controller.idleTimer.unref();
  }
  emit();
  return true;
}

export function setAgentMessage(message) {
  controller.message = String(message || '');
  emit();
}

export function resetController() {
  if (controller.idleTimer) clearTimeout(controller.idleTimer);
  if (typeof controller.stopAudio === 'function') {
    try {
      controller.stopAudio();
    } catch (err) {
      // ignore
    }
  }
  controller.idleTimer = null;
  controller.state = 'idle';
  controller.message = '';
  controller.audioLevel = null;
  controller.readAudio = null;
  controller.stopAudio = null;
  controller.generation += 1;
  controller.enteredAt = now();
  emit();
}

function levelFromAnalyser(analyser, bins) {
  analyser.getByteTimeDomainData(bins);
  let sum = 0;
  for (let i = 0; i < bins.length; i += 1) {
    const v = (bins[i] - 128) / 128;
    sum += v * v;
  }
  return Math.min(1, Math.sqrt(sum / bins.length) * 3.2);
}

/**
 * Drive listening from a real microphone. Rejects when the browser cannot
 * open one. Does not synthesize a level if permission is denied.
 */
export async function enableMicrophone() {
  if (typeof navigator === 'undefined' || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    return { ok: false, reason: 'This browser has no microphone API.' };
  }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    return { ok: false, reason: err && err.message ? err.message : 'Microphone permission was denied.' };
  }
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) {
    stream.getTracks().forEach((track) => track.stop());
    return { ok: false, reason: 'Web Audio is not available, so microphone level is not shown.' };
  }
  const ctx = new Ctx();
  const source = ctx.createMediaStreamSource(stream);
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 256;
  source.connect(analyser);
  const bins = new Uint8Array(analyser.fftSize);
  controller.readAudio = () => levelFromAnalyser(analyser, bins);
  const stop = () => {
    controller.readAudio = null;
    controller.stopAudio = null;
    stream.getTracks().forEach((track) => track.stop());
    ctx.close();
  };
  controller.stopAudio = stop;
  setAgentState('listening');
  return { ok: true, stop };
}

/**
 * Lip-sync from a real HTMLAudioElement. The element is the playback source;
 * nothing is synthesized when it is missing or silent.
 */
export function attachSpeechAudio(audio) {
  if (!audio || typeof audio.play !== 'function') {
    return { ok: false, reason: 'No audio element to play.' };
  }
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) {
    return { ok: false, reason: 'Web Audio is not available, so mouth sync is off.' };
  }
  const ctx = new Ctx();
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 256;
  const source = ctx.createMediaElementSource(audio);
  source.connect(analyser);
  analyser.connect(ctx.destination);
  const bins = new Uint8Array(analyser.fftSize);
  const onPlay = () => {
    controller.readAudio = () => levelFromAnalyser(analyser, bins);
    setAgentState('speaking');
  };
  const onEnd = () => {
    controller.readAudio = null;
    setAgentState('idle');
  };
  audio.addEventListener('play', onPlay);
  audio.addEventListener('ended', onEnd);
  audio.addEventListener('pause', onEnd);
  const stop = () => {
    audio.removeEventListener('play', onPlay);
    audio.removeEventListener('ended', onEnd);
    audio.removeEventListener('pause', onEnd);
    controller.readAudio = null;
    ctx.close();
  };
  return { ok: true, stop };
}

export { STATES, SUCCESS_MS };
