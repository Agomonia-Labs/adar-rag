// src/components/VoiceControls.jsx -- "Listen" button for assistant
// replies. Mirrors adar-mobile's apps/eats/src/ListenButton.tsx exactly:
// server-side Google Cloud TTS (routes/voice.py's /speak) for the
// languages that have a proven voice mapped there (same voices
// adar-core's Geetabitan /api/tts, ADAR Front Desk, and the mobile app's
// own Listen button already use -- most notably the explicitly male
// en-US-Chirp3-HD-Fenrir / bn-IN-Chirp3-HD-Fenrir voices), and the
// browser's native speechSynthesis only as a fallback for a language
// with no server voice mapped yet (same as mobile falling back to
// on-device expo-speech for French). Keeps the web assistant sounding
// like the same product as the mobile app instead of a different one.
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { speakText } from '../services/api.js';

// Same set as adar-mobile's i18n/languages.ts SERVER_TTS_LANGUAGES --
// keep the two lists in sync.
const SERVER_TTS_LANGUAGES = new Set(['en', 'es', 'hi', 'bn']);

function serverLangCode(lang) {
  return String(lang || 'en').split('-')[0].toLowerCase();
}

export default function VoiceControls({ text, label = 'Listen', lang = 'en-US', synthesize = null }) {
  const [browserSupported, setBrowserSupported] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [synthesizing, setSynthesizing] = useState(false);
  const [error, setError] = useState('');
  const cleanText = useMemo(() => normalizeSpeechText(text), [text]);
  const audioRef = useRef(null);
  const code = serverLangCode(lang);
  // `synthesize`, when passed, overrides where the audio comes from
  // entirely -- e.g. the dine-in assistant's own scheduling-domain guest
  // TTS (adar-core), a different backend/voice pipeline than this app's
  // own /api/voice/speak. Without it, the normal per-language routing
  // below applies (our own backend's Fenrir voices, or the browser as a
  // last resort).
  const useServerVoice = !!synthesize || SERVER_TTS_LANGUAGES.has(code);

  useEffect(() => {
    setBrowserSupported(typeof window !== 'undefined' && 'speechSynthesis' in window && 'SpeechSynthesisUtterance' in window);
  }, []);

  const stopAll = () => {
    if (typeof window !== 'undefined' && window.speechSynthesis) window.speechSynthesis.cancel();
    if (audioRef.current) { audioRef.current.pause(); audioRef.current = null; }
    setSpeaking(false);
    setSynthesizing(false);
  };

  useEffect(() => {
    stopAll();
    return stopAll;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cleanText, lang]);

  const speakViaServer = async () => {
    setSynthesizing(true);
    setError('');
    try {
      const { audioBase64, mimeType } = synthesize ? await synthesize(cleanText, lang) : await speakText(cleanText, code);
      const audio = new Audio(`data:${mimeType};base64,${audioBase64}`);
      audioRef.current = audio;
      audio.onended = () => setSpeaking(false);
      audio.onerror = () => { setSpeaking(false); setError('Could not read this aloud.'); };
      setSynthesizing(false);
      setSpeaking(true);
      await audio.play();
    } catch (e) {
      setSynthesizing(false);
      setSpeaking(false);
      setError(e.message || 'Could not read this aloud.');
    }
  };

  const speakViaBrowser = () => {
    if (!browserSupported) return;
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(cleanText);
    utterance.lang = lang;
    utterance.rate = 0.95;
    utterance.pitch = 1;
    utterance.onend = () => setSpeaking(false);
    utterance.onerror = () => setSpeaking(false);
    setSpeaking(true);
    window.speechSynthesis.speak(utterance);
  };

  const toggle = () => {
    if (speaking || synthesizing) { stopAll(); return; }
    if (!cleanText) return;
    if (useServerVoice) speakViaServer();
    else speakViaBrowser();
  };

  if (!cleanText || (!useServerVoice && !browserSupported)) return null;

  return (
    <button type="button" style={{ ...s.btn, ...(speaking || synthesizing ? s.btnActive : {}) }} onClick={toggle} disabled={synthesizing}>
      {synthesizing ? '…' : error ? `⚠️ ${error}` : speaking ? '■ Stop' : `🔊 ${label}`}
    </button>
  );
}

function normalizeSpeechText(value) {
  return String(value || '')
    .replace(/[^\S\r\n]+/g, ' ')
    .replace(/[📘🌿🚀📂💬📝🎥🏥🏢💼👥🛠🏗🧩🔍⚙🤝📦🎙]/g, '')
    .replace(/[*_`#>]/g, '')
    .replace(/\s*\n\s*/g, '. ')
    .replace(/\s{2,}/g, ' ')
    .trim();
}

const s = {
  btn: { border:'1px solid var(--gold-mid)', background:'var(--gold-soft)', color:'var(--gold-h)', borderRadius:8, padding:'4px 9px', fontSize:11.5, fontWeight:700 },
  btnActive: { borderColor:'rgba(192,57,43,.4)', background:'rgba(192,57,43,.08)', color:'var(--red)' },
};
