// src/components/RequireAuth.jsx -- the "guest sign-in" gate for any page
// or action that genuinely needs an account (My Orders, Dine-In, For
// Owners, Account, and the checkout step of Cart). Unlike the app's old
// top-level gate (App.jsx used to show AuthFlow instead of the ENTIRE
// app whenever there was no token), this renders inline, in place of just
// the one gated section, so Discover, a restaurant's menu, and the Order
// Assistant stay open to every visitor -- only the specific feature that
// needs a session shows the sign-in/sign-up prompt, right where that
// feature would otherwise be.
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
