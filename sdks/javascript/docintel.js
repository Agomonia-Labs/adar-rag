export class DocIntelError extends Error {
  constructor(status, payload) { super(`DocIntel API returned HTTP ${status}`); this.status = status; this.payload = payload; }
}

export class DocIntelClient {
  constructor({ baseUrl, accessToken, workspaceId = null }) {
    this.baseUrl = baseUrl.replace(/\/$/, ''); this.accessToken = accessToken; this.workspaceId = workspaceId;
  }
  async request(method, path, body, { idempotencyKey } = {}) {
    const headers = { Authorization:`Bearer ${this.accessToken}`, Accept:'application/json' };
    if (this.workspaceId) headers['X-DocIntel-Workspace-ID'] = this.workspaceId;
    if (idempotencyKey) headers['Idempotency-Key'] = idempotencyKey;
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    const response = await fetch(`${this.baseUrl}${path}`, { method, headers, body:body === undefined ? undefined : JSON.stringify(body) });
    const payload = await response.json().catch(() => null);
    if (!response.ok) throw new DocIntelError(response.status, payload);
    return payload;
  }
  me() { return this.request('GET', '/api/v1/me'); }
  documents() { return this.request('GET', '/api/v1/documents'); }
  workspaces() { return this.request('GET', '/api/v1/workspaces'); }
}
