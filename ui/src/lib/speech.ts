/**
 * Voice input and output, on top of the browser's own speech engine.
 *
 * Nothing here talks to a server. Recognition runs in the browser and yields
 * text; only that text is ever sent onward, by the same path a typed question
 * takes. No audio is recorded, stored or transmitted by this application.
 *
 * Support is uneven and always has been: `SpeechRecognition` ships prefixed in
 * Chrome and Edge and is absent in Firefox. Every entry point here reports
 * whether it is usable rather than throwing, so the assistant degrades to a
 * plain text box instead of presenting a button that does nothing.
 */

// --- Minimal typings ---------------------------------------------------------
// The Web Speech API is not in TypeScript's DOM library. These cover exactly
// what is used below rather than the whole specification.

interface SpeechRecognitionAlternative {
  transcript: string;
  confidence: number;
}

interface SpeechRecognitionResult {
  readonly length: number;
  isFinal: boolean;
  [index: number]: SpeechRecognitionAlternative;
}

interface SpeechRecognitionResultList {
  readonly length: number;
  [index: number]: SpeechRecognitionResult;
}

interface SpeechRecognitionEvent extends Event {
  resultIndex: number;
  results: SpeechRecognitionResultList;
}

interface SpeechRecognitionErrorEvent extends Event {
  error: string;
  message: string;
}

interface SpeechRecognitionLike extends EventTarget {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  maxAlternatives: number;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((event: SpeechRecognitionEvent) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
  onstart: (() => void) | null;
  onspeechend: (() => void) | null;
}

type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

function ctor(): SpeechRecognitionCtor | null {
  if (typeof window === 'undefined') return null;
  const w = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export const canListen = (): boolean => ctor() !== null;

export const canSpeak = (): boolean =>
  typeof window !== 'undefined' && 'speechSynthesis' in window;

/** Why the microphone is unavailable, in words worth showing a user. */
export function listenUnavailableReason(): string {
  if (typeof window === 'undefined') return 'Voice needs a browser.';
  return (
    'This browser has no speech recognition. Chrome or Edge support it; ' +
    'Firefox does not. You can still type.'
  );
}

export interface ListenHandlers {
  /** Fires repeatedly as the phrase forms, so it can be shown while spoken. */
  onPartial: (text: string) => void;
  /** Fires once with the settled phrase. */
  onFinal: (text: string) => void;
  onError: (message: string) => void;
  onEnd: () => void;
}

/** A live recognition session. Call `stop` to end it early. */
export interface Listener {
  stop: () => void;
}

const ERRORS: Record<string, string> = {
  'not-allowed':
    'Microphone access was refused. Allow it in the browser address bar to speak.',
  'service-not-allowed':
    'Microphone access was refused. Allow it in the browser address bar to speak.',
  'no-speech': 'I did not catch anything. Try again.',
  'audio-capture': 'No microphone was found.',
  network: 'The speech service could not be reached.',
  aborted: '',
};

/**
 * Listen for one utterance.
 *
 * `continuous` is false deliberately: the session closes when the speaker
 * stops, which is what makes "ask a question and it goes" work without a second
 * click. Interim results are on so the words appear while they are being said —
 * an assistant that shows nothing until you stop talking feels broken.
 */
export function listen(handlers: ListenHandlers, lang = 'en-GB'): Listener | null {
  const Ctor = ctor();
  if (!Ctor) {
    handlers.onError(listenUnavailableReason());
    return null;
  }

  const recognition = new Ctor();
  recognition.lang = lang;
  recognition.continuous = false;
  recognition.interimResults = true;
  recognition.maxAlternatives = 1;

  let settled = '';
  let stopped = false;

  recognition.onresult = (event) => {
    let interim = '';
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const result = event.results[i];
      const text = result[0]?.transcript ?? '';
      if (result.isFinal) settled += text;
      else interim += text;
    }
    const combined = (settled + interim).trim();
    if (combined) handlers.onPartial(combined);
  };

  recognition.onerror = (event) => {
    const message = ERRORS[event.error] ?? `Voice input failed (${event.error}).`;
    if (message) handlers.onError(message);
  };

  recognition.onend = () => {
    const text = settled.trim();
    // A stop the user asked for is a cancel, not a submission — otherwise
    // pressing stop would fire off the half-formed sentence they changed their
    // mind about.
    if (text && !stopped) handlers.onFinal(text);
    handlers.onEnd();
  };

  try {
    recognition.start();
  } catch {
    handlers.onError('Voice input could not start. It may already be listening.');
    return null;
  }

  return {
    stop: () => {
      stopped = true;
      try {
        recognition.stop();
      } catch {
        /* already closed */
      }
    },
  };
}

/**
 * Read text aloud.
 *
 * Answers can run to several paragraphs with citations and figures; reading all
 * of that is tiresome and unhelpful. Callers pass the headline or a short
 * summary, and `trim` below caps whatever arrives.
 */
export function speak(text: string, lang = 'en-GB'): void {
  if (!canSpeak()) return;
  const clean = forSpeech(text);
  if (!clean) return;

  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(clean);
  utterance.lang = lang;
  utterance.rate = 1.02;
  utterance.pitch = 1;
  window.speechSynthesis.speak(utterance);
}

export function stopSpeaking(): void {
  if (canSpeak()) window.speechSynthesis.cancel();
}

const MAX_SPOKEN_CHARS = 420;

/**
 * Strip what does not survive being read out, and cap the length at a sentence
 * boundary so the voice never stops mid-clause.
 */
export function forSpeech(text: string): string {
  const flat = (text ?? '')
    .replace(/[*_`#>|]/g, ' ')
    .replace(/\[(.*?)\]\(.*?\)/g, '$1')
    .replace(/\s+/g, ' ')
    .trim();

  if (flat.length <= MAX_SPOKEN_CHARS) return flat;

  const cut = flat.slice(0, MAX_SPOKEN_CHARS);
  const lastStop = Math.max(cut.lastIndexOf('. '), cut.lastIndexOf('? '), cut.lastIndexOf('! '));
  return lastStop > 120 ? cut.slice(0, lastStop + 1) : `${cut.trimEnd()}…`;
}
