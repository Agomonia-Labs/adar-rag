import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { Building2, Copy, KeyRound, Plus, RefreshCw, ShieldCheck, Trash2, X } from 'lucide-react';
import {
  createDeveloperApp,
  createDeveloperOrganization,
  getDeveloperAppAudit,
  listDeveloperApps,
  listDeveloperOrganizations,
  listWorkspaces,
  revokeDeveloperApp,
  rotateDeveloperAppSecret,
} from '../../services/api.js';
import './developerApplications.css';

const DEFAULT_SCOPES = ['workspaces:read', 'documents:read', 'knowledge:query', 'batches:read', 'events:read'];

export default function DeveloperApplications({ activeWorkspace, onClose }) {
  const [view, setView] = useState('applications');
  const [organizations, setOrganizations] = useState([]);
  const [apps, setApps] = useState([]);
  const [workspaces, setWorkspaces] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [secret, setSecret] = useState(null);
  const [audit, setAudit] = useState(null);
  const [orgName, setOrgName] = useState('');
  const [form, setForm] = useState({ name:'', organization_id:'', workspace_id:'', scopes:DEFAULT_SCOPES.join(' ') });

  useEffect(() => { refresh(); }, []);

  async function refresh() {
    setBusy(true); setError('');
    try {
      const [orgResult, appResult, workspaceResult] = await Promise.all([
        listDeveloperOrganizations(),
        listDeveloperApps(),
        listWorkspaces(),
      ]);
      const availableWorkspaces = Array.isArray(workspaceResult) ? workspaceResult : [];
      setOrganizations(orgResult.data || []);
      setApps(appResult.data || []);
      setWorkspaces(availableWorkspaces);
      setForm(current => {
        const activeId = activeWorkspace?.id;
        const workspaceId = activeId && availableWorkspaces.some(workspace => workspace.id === activeId)
          ? activeId
          : availableWorkspaces.some(workspace => workspace.id === current.workspace_id)
            ? current.workspace_id
            : availableWorkspaces[0]?.id || '';
        return {
          ...current,
          organization_id:current.organization_id || orgResult.data?.[0]?.id || '',
          workspace_id:workspaceId,
        };
      });
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  async function addOrganization(event) {
    event.preventDefault();
    if (!orgName.trim()) return;
    setBusy(true); setError('');
    try {
      const result = await createDeveloperOrganization({ name:orgName.trim() });
      setOrgName('');
      setOrganizations(current => [...current, result.data]);
      setForm(current => ({ ...current, organization_id:result.data.id }));
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  async function addApplication(event) {
    event.preventDefault();
    setBusy(true); setError('');
    try {
      const scopes = form.scopes.split(/[\s,]+/).filter(Boolean);
      const result = await createDeveloperApp({
        name:form.name.trim(), client_type:'confidential', organization_id:form.organization_id || null,
        workspace_ids:form.workspace_id ? [form.workspace_id] : [], scopes,
      });
      setSecret({ client_id:result.client_id, client_secret:result.client_secret });
      setForm(current => ({ ...current, name:'' }));
      await refresh();
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  async function rotate(clientId) {
    if (!window.confirm('Rotate this secret now? The previous secret will stop working.')) return;
    setBusy(true); setError('');
    try { setSecret(await rotateDeveloperAppSecret(clientId)); }
    catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  async function revoke(clientId) {
    if (!window.confirm('Revoke this application and all future token access?')) return;
    setBusy(true); setError('');
    try { await revokeDeveloperApp(clientId); await refresh(); }
    catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  async function showAudit(clientId) {
    setBusy(true); setError('');
    try { setAudit({ clientId, events:(await getDeveloperAppAudit(clientId)).data || [] }); }
    catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  return createPortal(<div className="devapp-overlay" style={{ zIndex:30000, isolation:'isolate' }} role="dialog" aria-modal="true" aria-label="Developer Applications">
    <section className="devapp-shell" style={{ position:'relative' }}>
      <header className="devapp-header">
        <div><p>Developers</p><h2>Applications and Credentials</h2></div>
        <div className="devapp-head-actions">
          <button className="devapp-icon" onClick={refresh} disabled={busy} title="Refresh"><RefreshCw size={17}/></button>
          <button className="devapp-icon" onClick={onClose} aria-label="Close Developer Applications"><X size={19}/></button>
        </div>
      </header>

      <nav className="devapp-tabs">
        <button className={view === 'applications' ? 'active' : ''} onClick={() => setView('applications')}><KeyRound size={15}/>Applications</button>
        <button className={view === 'organizations' ? 'active' : ''} onClick={() => setView('organizations')}><Building2 size={15}/>Organizations</button>
      </nav>

      {error && <div className="devapp-error">{error}</div>}
      {secret && <div className="devapp-secret">
        <div><strong>Save this secret now</strong><span>It will not be shown again.</span></div>
        <code>{secret.client_id}</code><code>{secret.client_secret}</code>
        <button onClick={() => navigator.clipboard?.writeText(secret.client_secret)}><Copy size={14}/>Copy secret</button>
        <button className="devapp-icon" onClick={() => setSecret(null)} aria-label="Dismiss secret"><X size={16}/></button>
      </div>}

      <main className="devapp-content">
        {view === 'organizations' ? <>
          <form className="devapp-create" onSubmit={addOrganization}>
            <label><span>Organization name</span><input value={orgName} onChange={e => setOrgName(e.target.value)} placeholder="Enterprise integration team"/></label>
            <button disabled={busy || !orgName.trim()}><Plus size={15}/>Create</button>
          </form>
          <div className="devapp-list">
            {organizations.map(org => <article key={org.id}>
              <Building2 size={19}/><div><strong>{org.name}</strong><span>{org.slug} · {org.role}</span></div>
              <span className={`devapp-state ${org.status}`}>{org.status}</span>
            </article>)}
            {!organizations.length && <div className="devapp-empty">Create an organization before registering an enterprise application.</div>}
          </div>
        </> : <>
          <form className="devapp-create app" onSubmit={addApplication}>
            <label><span>Application name</span><input required value={form.name} onChange={e => setForm({...form, name:e.target.value})} placeholder="Procurement integration"/></label>
            <label><span>Organization</span><select required value={form.organization_id} onChange={e => setForm({...form, organization_id:e.target.value})}>
              <option value="">Select organization</option>{organizations.map(org => <option key={org.id} value={org.id}>{org.name}</option>)}
            </select></label>
            <label><span>Workspace grant</span><select required value={form.workspace_id} onChange={e => setForm({...form, workspace_id:e.target.value})}>
              <option value="">Select team workspace</option>{workspaces.map(workspace => <option key={workspace.id} value={workspace.id}>{workspace.name}{workspace.my_role ? ` · ${workspace.my_role}` : ''}</option>)}
            </select></label>
            <label className="wide"><span>OAuth scopes</span><input value={form.scopes} onChange={e => setForm({...form, scopes:e.target.value})}/></label>
            {!workspaces.length && <div className="devapp-workspace"><span>Workspace grant</span><strong>Create or join a team workspace before registering an organization application.</strong></div>}
            <button disabled={busy || !form.name.trim() || !form.organization_id || !form.workspace_id}><Plus size={15}/>Create application</button>
          </form>
          <div className="devapp-list">
            {apps.filter(app => app.client_type === 'confidential').map(app => <article key={app.client_id}>
              <ShieldCheck size={19}/><div><strong>{app.client_name}</strong><code>{app.client_id}</code><span>{app.organization_name || 'User-owned'} · {(app.scopes || []).length} scopes</span></div>
              <span className={`devapp-state ${app.revoked_at ? 'revoked' : 'active'}`}>{app.revoked_at ? 'revoked' : 'active'}</span>
              <div className="devapp-row-actions">
                <button onClick={() => showAudit(app.client_id)}>Audit</button>
                {!app.revoked_at && <button onClick={() => rotate(app.client_id)} title="Rotate secret"><KeyRound size={14}/></button>}
                {!app.revoked_at && <button onClick={() => revoke(app.client_id)} title="Revoke application"><Trash2 size={14}/></button>}
              </div>
            </article>)}
            {!apps.some(app => app.client_type === 'confidential') && <div className="devapp-empty">No confidential applications are registered.</div>}
          </div>
        </>}
      </main>

      {audit && <div className="devapp-audit">
        <header><strong>Application audit</strong><button className="devapp-icon" onClick={() => setAudit(null)} aria-label="Close application audit"><X size={17}/></button></header>
        <div>{audit.events.map(event => <article key={event.id}><strong>{event.event_type}</strong><span>{new Date(event.created_at).toLocaleString()}</span><pre>{JSON.stringify(event.metadata, null, 2)}</pre></article>)}</div>
      </div>}
    </section>
  </div>, document.body);
}
