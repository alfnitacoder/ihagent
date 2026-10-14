/** Seven robot states. Listening and speaking are real poses; mic level and lip sync stay off until audio exists. */

export const STATES = [
  'idle',
  'thinking',
  'listening',
  'speaking',
  'working',
  'success',
  'error',
];

export const STATE_LABELS = {
  idle: 'Idle',
  thinking: 'Thinking',
  listening: 'Listening',
  speaking: 'Speaking',
  working: 'Working',
  success: 'Success',
  error: 'Error',
};

/** LED / ring colors. Error is amber; every other state stays in the cyan family. */
export const PALETTE = {
  idle: { led: '#3ecbff', ring: '#2f9dff', glow: 1 },
  thinking: { led: '#67e6ff', ring: '#49b7ff', glow: 1.35 },
  listening: { led: '#b9f6ff', ring: '#5ad0ff', glow: 1.7 },
  speaking: { led: '#5ad8ff', ring: '#3aaeff', glow: 1.25 },
  working: { led: '#3ecbff', ring: '#1f8fff', glow: 1.6 },
  success: { led: '#e7fbff', ring: '#8aefff', glow: 2.15 },
  error: { led: '#ff9a3c', ring: '#ff7a18', glow: 1.35 },
};

export function normalizeState(value) {
  const text = String(value || '').trim().toLowerCase();
  return STATES.indexOf(text) === -1 ? '' : text;
}
