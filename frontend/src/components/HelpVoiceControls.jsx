import React, { useEffect, useMemo, useState } from 'react';

export default function HelpVoiceControls({ text, label = 'Listen', lang = 'en-US' }) {
  const [supported, setSupported] = useState(false);
  const [speaking, setSpeaking] = useState(false);

  const cleanText = useMemo(() => normalizeSpeechText(text), [text]);

  useEffect(() => {
    setSupported(typeof window !== 'undefined' && 'speechSynthesis' in window && 'SpeechSynthesisUtterance' in window);
  }, []);

  useEffect(() => {
    if (!supported) return undefined;
    window.speechSynthesis.cancel();
    setSpeaking(false);
    return () => {
      window.speechSynthesis.cancel();
    };
  }, [cleanText, supported]);

  const start = () => {
    if (!supported || !cleanText) return;
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

  const stop = () => {
    if (!supported) return;
    window.speechSynthesis.cancel();
    setSpeaking(false);
  };

  if (!supported) return null;

  return (
    <div style={s.wrap} aria-label="Voice controls">
      <button type="button" style={{...s.btn, ...(speaking ? s.btnActive : {})}} onClick={speaking ? stop : start}>
        {speaking ? 'Stop' : label}
      </button>
      <span style={s.hint}>Voice</span>
    </div>
  );
}

function normalizeSpeechText(value) {
  return String(value || '')
    .replace(/[^\S\r\n]+/g, ' ')
    .replace(/[📘🌿🚀📂💬📝🎥🏥🏢💼👥🛠🏗🧩🔍⚙🤝📦🎙]/g, '')
    .replace(/\s*\n\s*/g, '. ')
    .replace(/\s{2,}/g, ' ')
    .trim();
}

const s = {
  wrap:{ display:'inline-flex', alignItems:'center', gap:7, flexShrink:0 },
  btn:{ border:'1px solid rgba(96,165,250,.35)', background:'rgba(96,165,250,.1)', color:'#93c5fd', borderRadius:8, padding:'8px 11px', cursor:'pointer', fontSize:12, fontWeight:900 },
  btnActive:{ borderColor:'rgba(248,113,113,.45)', background:'rgba(248,113,113,.12)', color:'#fecaca' },
  hint:{ color:'var(--muted2)', fontSize:11, fontWeight:800 },
};
