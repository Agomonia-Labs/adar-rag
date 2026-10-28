import { useCallback, useEffect, useRef, useState } from 'react';

const encoder = new TextEncoder();

// The backend keeps each Google TTS request below the 5,000-byte API limit.
// Split here so long material becomes a complete ordered playback queue.
export function splitSpeechText(text, maxBytes = 2600) {
  const characters = Array.from(String(text || '').trim());
  const chunks = [];
  let start = 0;
  while (start < characters.length) {
    let end = start;
    let bytes = 0;
    let preferredBreak = -1;
    while (end < characters.length) {
      const size = encoder.encode(characters[end]).length;
      if (bytes + size > maxBytes) break;
      bytes += size;
      end += 1;
      if (/\s|[.!?।]/u.test(characters[end - 1]) && end - start > 120) preferredBreak = end;
    }
    const cut = end < characters.length && preferredBreak > start ? preferredBreak : end;
    const chunk = characters.slice(start, cut).join('').trim();
    if (chunk) chunks.push(chunk);
    start = cut;
    while (start < characters.length && /\s/u.test(characters[start])) start += 1;
  }
  return chunks;
}

export default function useSequentialSpeech(synthesize, language = 'en') {
  const [activeKey, setActiveKey] = useState(null);
  const [progress, setProgress] = useState({ current:0, total:0 });
  const [error, setError] = useState('');
  const synthesizeRef = useRef(synthesize);
  const audioRef = useRef(null);
  const releasePlaybackRef = useRef(null);
  const requestRef = useRef(0);
  const activeKeyRef = useRef(null);

  synthesizeRef.current = synthesize;

  const stop = useCallback(() => {
    requestRef.current += 1;
    activeKeyRef.current = null;
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.src = '';
      audioRef.current = null;
    }
    releasePlaybackRef.current?.();
    releasePlaybackRef.current = null;
    setActiveKey(null);
    setProgress({ current:0, total:0 });
  }, []);

  useEffect(() => () => stop(), [stop]);

  const toggle = useCallback(async (key, text) => {
    if (activeKeyRef.current !== null) {
      if (activeKeyRef.current === key) stop();
      return;
    }

    const chunks = splitSpeechText(text);
    if (!chunks.length) return;
    const requestId = ++requestRef.current;
    activeKeyRef.current = key;
    setActiveKey(key);
    setError('');
    setProgress({ current:1, total:chunks.length });

    try {
      for (let index = 0; index < chunks.length; index += 1) {
        if (requestId !== requestRef.current) return;
        setProgress({ current:index + 1, total:chunks.length });
        const result = await synthesizeRef.current(chunks[index], language || 'en');
        if (requestId !== requestRef.current) return;
        await new Promise((resolve, reject) => {
          let settled = false;
          const finish = () => {
            if (settled) return;
            settled = true;
            releasePlaybackRef.current = null;
            resolve();
          };
          const fail = () => {
            if (settled) return;
            settled = true;
            releasePlaybackRef.current = null;
            reject(new Error('Unable to play this audio segment.'));
          };
          const audio = new Audio(`data:${result.mime_type};base64,${result.audio_base64}`);
          audioRef.current = audio;
          releasePlaybackRef.current = finish;
          audio.onended = finish;
          audio.onerror = fail;
          audio.play().catch(fail);
        });
      }
      if (requestId === requestRef.current) stop();
    } catch (cause) {
      if (requestId === requestRef.current) {
        const message = cause?.message || 'Unable to create audio.';
        stop();
        setError(message);
      }
    }
  }, [language, stop]);

  return { activeKey, progress, error, toggle, stop };
}
