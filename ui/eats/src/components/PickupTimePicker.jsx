// src/components/PickupTimePicker.jsx -- shared "ASAP or schedule for later"
// pickup-time control used by CartPage.jsx (carryout) and EventsPage.jsx
// (event/stall ordering). The date+time the customer picks here is always
// meant as the RESTAURANT's own local wall-clock time, never the browser's
// -- the backend combines these raw YYYY-MM-DD/HH:MM values with the
// restaurant's actual timezone (see routes/restaurant.py's
// _resolve_pickup_time_request), so this component just needs to make that
// framing clear to the customer and show them what time it currently is
// there, via Intl.DateTimeFormat (no date library needed).
import React, { useEffect, useState } from 'react';

function todayInTimezone(tz) {
  try {
    // en-CA formats as YYYY-MM-DD -- a convenient built-in way to get an
    // ISO-shaped date string for an arbitrary IANA zone without a library.
    return new Intl.DateTimeFormat('en-CA', { timeZone: tz || 'UTC' }).format(new Date());
  } catch {
    return new Date().toISOString().slice(0, 10);
  }
}

function currentTimeLabel(tz) {
  try {
    return new Intl.DateTimeFormat(undefined, {
      timeZone: tz || 'UTC',
      hour: 'numeric', minute: '2-digit', weekday: 'short', month: 'short', day: 'numeric',
    }).format(new Date());
  } catch {
    return null;
  }
}

export default function PickupTimePicker({ timezone, value, onChange }) {
  const [now, setNow] = useState(() => currentTimeLabel(timezone));

  useEffect(() => {
    setNow(currentTimeLabel(timezone));
    const id = setInterval(() => setNow(currentTimeLabel(timezone)), 60000);
    return () => clearInterval(id);
  }, [timezone]);

  const mode = value?.mode || 'asap';
  const minDate = todayInTimezone(timezone);

  function setMode(nextMode) {
    if (nextMode === 'scheduled' && !value?.date) {
      onChange({ mode: nextMode, date: minDate, time: value?.time || '' });
    } else {
      onChange({ ...value, mode: nextMode });
    }
  }

  return (
    <div style={s.wrap}>
      <div style={s.toggleRow}>
        <button
          type="button"
          style={{ ...s.toggleBtn, ...(mode === 'asap' ? s.toggleBtnActive : {}) }}
          onClick={() => setMode('asap')}
        >
          ASAP
        </button>
        <button
          type="button"
          style={{ ...s.toggleBtn, ...(mode === 'scheduled' ? s.toggleBtnActive : {}) }}
          onClick={() => setMode('scheduled')}
        >
          Schedule for later
        </button>
      </div>
      {mode === 'scheduled' && (
        <div style={s.pickerRow}>
          <input
            type="date"
            value={value?.date || minDate}
            min={minDate}
            onChange={(e) => onChange({ ...value, mode: 'scheduled', date: e.target.value })}
            style={s.input}
          />
          <input
            type="time"
            value={value?.time || ''}
            onChange={(e) => onChange({ ...value, mode: 'scheduled', time: e.target.value })}
            style={s.input}
          />
        </div>
      )}
      {now && (
        <p style={s.note}>
          {mode === 'scheduled' ? "Date and time are the restaurant's local time. " : null}
          It's currently {now} at the restaurant.
        </p>
      )}
    </div>
  );
}

const s = {
  wrap: { marginBottom: 4 },
  toggleRow: { display: 'flex', gap: 8 },
  toggleBtn: {
    flex: 1, padding: '8px 10px', borderRadius: 8, border: '1px solid var(--b2)',
    background: 'var(--s1)', color: 'var(--tx)', fontSize: 13, fontWeight: 600,
  },
  toggleBtnActive: { background: 'var(--gold)', borderColor: 'var(--gold)', color: '#fff' },
  pickerRow: { display: 'flex', gap: 8, marginTop: 8 },
  input: { flex: 1, padding: '8px 10px', borderRadius: 8, border: '1px solid var(--b2)', fontSize: 13 },
  note: { fontSize: 11.5, color: 'var(--muted2)', marginTop: 6 },
};
