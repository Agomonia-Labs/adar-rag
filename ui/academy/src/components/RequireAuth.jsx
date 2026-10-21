// src/components/RequireAuth.jsx -- direct port of ui/eats's gate.
import React from 'react';
import { AuthFlow } from '../pages/AuthPages.jsx';

export default function RequireAuth({ token, onLogin, prompt, initialScreen, children }) {
  if (token) return children;
  return (
    <div>
      {prompt && <p style={s.prompt}>{prompt}</p>}
      <AuthFlow onLogin={onLogin} embedded initialScreen={initialScreen} />
    </div>
  );
}

const s = {
  prompt: { color:'var(--muted2)', fontSize:13.5, textAlign:'center', maxWidth:420, margin:'0 auto 10px' },
};
