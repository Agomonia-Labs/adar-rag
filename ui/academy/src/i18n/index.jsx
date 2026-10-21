// src/i18n/index.js -- site-wide UI-chrome translation (menus, buttons,
// labels, empty states). Separate from the per-query "Reply in ___"
// language picker already on AI Tutor/Study Tools/Content Library/Video
// Intelligence -- that one asks the backend to GENERATE an answer in a
// chosen language; this one swaps which fixed dictionary of already-
// authored UI strings gets rendered, entirely client-side, no backend
// call involved. Same 6 languages on both, kept in sync deliberately
// (see LANGUAGES below) so the whole experience is consistent language
// for language.
import React, { createContext, useContext, useEffect, useMemo, useState } from 'react';
import en from './locales/en.js';
import es from './locales/es.js';
import bn from './locales/bn.js';
import hi from './locales/hi.js';
import ar from './locales/ar.js';
import fr from './locales/fr.js';

// code must match backend/services/language.py's LANGUAGE_NAMES codes
// (see GuestPreviewPage.jsx's own LANGUAGE_OPTIONS comment) so a user's
// one choice of "Spanish" means the same thing everywhere in the app.
export const LANGUAGES = [
  { code: 'en', label: 'English', dir: 'ltr' },
  { code: 'es', label: 'Español', dir: 'ltr' },
  { code: 'bn', label: 'বাংলা', dir: 'ltr' },
  { code: 'hi', label: 'हिन्दी', dir: 'ltr' },
  { code: 'ar', label: 'العربية', dir: 'rtl' },
  { code: 'fr', label: 'Français', dir: 'ltr' },
];

const DICTS = { en, es, bn, hi, ar, fr };
const STORAGE_KEY = 'academy_ui_lang';

const LangContext = createContext({ lang: 'en', setLang: () => {}, t: key => key });

export function LangProvider({ children }) {
  const [lang, setLangState] = useState(() => {
    try { return localStorage.getItem(STORAGE_KEY) || 'en'; } catch { return 'en'; }
  });

  useEffect(() => {
    const meta = LANGUAGES.find(l => l.code === lang) || LANGUAGES[0];
    document.documentElement.lang = lang;
    document.documentElement.dir = meta.dir;
  }, [lang]);

  const setLang = code => {
    try { localStorage.setItem(STORAGE_KEY, code); } catch { /* private mode etc -- just skip persisting */ }
    setLangState(code);
  };

  // {placeholder} interpolation -- the only dynamic UI string so far is
  // the footer's copyright year (see locales/*.js's footer_copyright).
  const t = useMemo(() => (key, vars) => {
    const dict = DICTS[lang] || DICTS.en;
    let str = dict[key] ?? DICTS.en[key] ?? key;
    if (vars) for (const [k, v] of Object.entries(vars)) str = str.replaceAll(`{${k}}`, v);
    return str;
  }, [lang]);

  const value = useMemo(() => ({ lang, setLang, t }), [lang, t]);
  return <LangContext.Provider value={value}>{children}</LangContext.Provider>;
}

export function useLang() {
  return useContext(LangContext);
}

// Small, reusable <select> -- used in both the guest and signed-in
// headers. Pass `style` to match whichever header it's dropped into.
export function LanguagePicker({ style }) {
  const { lang, setLang, t } = useLang();
  return (
    <select value={lang} onChange={e => setLang(e.target.value)} style={style} aria-label={t('language')} title={t('language')}>
      {LANGUAGES.map(l => <option key={l.code} value={l.code}>{l.label}</option>)}
    </select>
  );
}
