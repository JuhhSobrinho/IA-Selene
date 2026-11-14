import { useEffect } from 'react';
import { create } from 'zustand';
import { transcribeAudio, fetchSpeechHealth } from '../lib/api';

export type SpeechState = 'idle' | 'recording' | 'transcribing';

// Mirrors the terminal's record_until_silence (src/openjarvis/speech/voice_io.py)
// so browser recording behaves the same way: stop once the speaker goes
// quiet, give up early if nothing is said at all, and never run forever.
// Lowered from an initial 0.12 — this user's mic/browser combo peaks well
// under that while speaking normally (see the startup-grace console log
// for the actual peak reached), so 0.12 never registered real speech at all.
const SPEECH_LEVEL = 0.035; // `level` (0..1) above this counts as speech
const SILENCE_MS = 1600; // quiet time after speech before auto-stop
const STARTUP_GRACE_MS = 6000; // give up if speech never starts
const MAX_RECORD_MS = 30000; // safety ceiling

/**
 * Mic input is a single shared resource, like TTS output in lib/tts.ts: one
 * store rather than per-component state, so the live amplitude (`level`) is
 * readable from anywhere on screen (e.g. VoiceOrb) without prop drilling,
 * and without opening a second, independent microphone stream.
 */
interface SpeechStore {
  state: SpeechState;
  error: string | null;
  available: boolean;
  /** Live input amplitude while recording, 0..1. 0 when not recording. */
  level: number;
  /** Text from the most recently finished recording (manual or auto-stopped). */
  lastTranscript: string;
  /** Bumped every time `lastTranscript` is set, so effects can react to a
   *  fresh transcript even if the text happens to repeat. */
  transcriptSeq: number;
  _probeHealth: () => void;
  startRecording: () => Promise<void>;
  stopRecording: () => Promise<string>;
}

// Playback/recording handles are not render state — kept out of the store
// for the same reason lib/tts.ts keeps its <audio> element module-level.
let mediaRecorder: MediaRecorder | null = null;
let chunks: Blob[] = [];
let micStream: MediaStream | null = null;
let audioCtx: AudioContext | null = null;
let analyser: AnalyserNode | null = null;
let levelFrame: number | null = null;
let healthProbe: Promise<void> | null = null;

function stopLevelMeter(): void {
  if (levelFrame !== null) {
    cancelAnimationFrame(levelFrame);
    levelFrame = null;
  }
  if (audioCtx) {
    void audioCtx.close().catch(() => {});
    audioCtx = null;
  }
  analyser = null;
}

function startLevelMeter(
  stream: MediaStream,
  onLevel: (level: number) => void,
  onSilence: () => void,
): void {
  audioCtx = new AudioContext();
  console.log('[Speech] AudioContext state after create:', audioCtx.state);
  if (audioCtx.state === 'suspended') {
    // Browsers can create a new AudioContext already suspended (autoplay
    // policy). Suspended means getByteTimeDomainData returns a flat
    // 128-everywhere buffer forever (peakLevel stuck at exactly 0) while
    // MediaRecorder — a separate API — keeps recording real audio fine, so
    // this failed silently with no error anywhere.
    void audioCtx.resume().then(() =>
      console.log('[Speech] AudioContext resumed, state:', audioCtx?.state),
    );
  }
  const source = audioCtx.createMediaStreamSource(stream);
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 512;
  analyser.smoothingTimeConstant = 0.6;
  source.connect(analyser);

  const data = new Uint8Array(analyser.frequencyBinCount);
  const startedAt = performance.now();
  let hasSpeech = false;
  let silenceSince: number | null = null;
  let peakLevel = 0;

  const tick = () => {
    if (!analyser) return;
    analyser.getByteTimeDomainData(data);
    let sumSquares = 0;
    for (let i = 0; i < data.length; i++) {
      const v = (data[i] - 128) / 128;
      sumSquares += v * v;
    }
    const rms = Math.sqrt(sumSquares / data.length);
    // RMS for normal speech sits well under 1; scale up so the orb's
    // reaction is visible without clipping on loud input.
    const level = Math.min(1, rms * 4);
    onLevel(level);
    if (level > peakLevel) peakLevel = level;

    const now = performance.now();
    if (level > SPEECH_LEVEL) {
      hasSpeech = true;
      silenceSince = null;
    } else if (!hasSpeech) {
      if (now - startedAt > STARTUP_GRACE_MS) {
        console.log(
          '[Speech] startup grace expired, peakLevel=', peakLevel.toFixed(4),
          'threshold=', SPEECH_LEVEL,
        );
        onSilence();
        return;
      }
    } else {
      if (silenceSince === null) silenceSince = now;
      if (now - silenceSince > SILENCE_MS) {
        onSilence();
        return;
      }
    }
    if (now - startedAt > MAX_RECORD_MS) {
      onSilence();
      return;
    }

    levelFrame = requestAnimationFrame(tick);
  };
  tick();
}

export const useSpeechStore = create<SpeechStore>((set, get) => ({
  state: 'idle',
  error: null,
  available: false,
  level: 0,
  lastTranscript: '',
  transcriptSeq: 0,

  _probeHealth: () => {
    if (healthProbe) return;
    healthProbe = fetchSpeechHealth()
      .then((health) => set({ available: health.available }))
      .catch(() => set({ available: false }));
  },

  startRecording: async () => {
    console.log('[Speech] startRecording called');
    set({ error: null });

    if (!navigator.mediaDevices?.getUserMedia) {
      console.error('[Speech] getUserMedia unsupported');
      set({ error: 'Microphone not supported in this browser' });
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      micStream = stream;

      const recorder = new MediaRecorder(stream);
      chunks = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunks.push(e.data);
      };
      recorder.start();
      mediaRecorder = recorder;
      console.log('[Speech] recorder started, mimeType=', recorder.mimeType);
      startLevelMeter(
        stream,
        (level) => set({ level }),
        () => {
          // Auto-stop on silence/timeout — same entry point as a manual
          // click, so both paths produce one lastTranscript/transcriptSeq.
          console.log('[Speech] auto-silence fired, recorder.state=', mediaRecorder?.state);
          void get().stopRecording();
        },
      );
      set({ state: 'recording' });
    } catch (err) {
      console.error('[Speech] startRecording failed', err);
      set({ error: 'Microphone access denied', state: 'idle' });
    }
  },

  stopRecording: () => {
    console.log('[Speech] stopRecording called, recorder.state=', mediaRecorder?.state);
    return new Promise<string>((resolve, reject) => {
      const recorder = mediaRecorder;
      if (!recorder || recorder.state !== 'recording') {
        console.warn('[Speech] stopRecording: not recording, rejecting');
        reject(new Error('Not recording'));
        return;
      }

      recorder.onstop = async () => {
        console.log('[Speech] recorder onstop fired, chunks=', chunks.length);
        stopLevelMeter();
        set({ state: 'transcribing', level: 0 });

        micStream?.getTracks().forEach((track) => track.stop());
        micStream = null;

        const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
        chunks = [];
        console.log('[Speech] blob built, size=', blob.size, 'calling transcribeAudio');

        try {
          const result = await transcribeAudio(blob);
          console.log('[Speech] transcribeAudio resolved, text=', result.text);
          set((s) => ({
            state: 'idle',
            lastTranscript: result.text,
            transcriptSeq: s.transcriptSeq + 1,
          }));
          resolve(result.text);
        } catch (err) {
          console.error('[Speech] transcribeAudio failed', err);
          set({ state: 'idle' });
          const msg = err instanceof Error ? err.message : 'Transcription failed';
          set({ error: msg });
          reject(err);
        }
      };

      recorder.stop();
    });
  },
}));

export function useSpeech() {
  const state = useSpeechStore((s) => s.state);
  const error = useSpeechStore((s) => s.error);
  const available = useSpeechStore((s) => s.available);
  const startRecording = useSpeechStore((s) => s.startRecording);
  const stopRecording = useSpeechStore((s) => s.stopRecording);
  const probeHealth = useSpeechStore((s) => s._probeHealth);

  useEffect(() => {
    probeHealth();
  }, [probeHealth]);

  return {
    state,
    error,
    available,
    startRecording,
    stopRecording,
    isRecording: state === 'recording',
    isTranscribing: state === 'transcribing',
  };
}
