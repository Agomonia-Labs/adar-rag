const BASE = '/api/mcp-playground';

function headers(json = false) {
  return {
    ...(json ? { 'Content-Type': 'application/json' } : {}),
    Authorization: `Bearer ${localStorage.getItem('token') || ''}`,
  };
}

async function result(response) {
  if (response.ok) return response.json();
  const body = await response.json().catch(() => ({}));
  const detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail || body);
  const error = new Error(detail || `HTTP ${response.status}`);
  error.status = response.status;
  throw error;
}

export async function getMcpPlaygroundStatus() {
  return result(await fetch(`${BASE}/status`, { headers: headers(), credentials: 'include' }));
}

export async function startMcpPlaygroundOAuth(scopes = []) {
  return result(await fetch(`${BASE}/oauth/start`, {
    method: 'POST', headers: headers(true), credentials: 'include', body: JSON.stringify({ scopes }),
  }));
}

export async function disconnectMcpPlayground() {
  return result(await fetch(`${BASE}/disconnect`, {
    method: 'POST', headers: headers(), credentials: 'include',
  }));
}

export async function executeMcpPlayground(command, confirm = false) {
  return result(await fetch(`${BASE}/execute`, {
    method: 'POST', headers: headers(true), credentials: 'include', body: JSON.stringify({ command, confirm }),
  }));
}

export async function getMcpPlaygroundExamples() {
  return result(await fetch(`${BASE}/examples`, { headers: headers(), credentials: 'include' }));
}
