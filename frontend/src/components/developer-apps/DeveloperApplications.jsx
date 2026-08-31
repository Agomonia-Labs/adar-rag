import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { Building2, Copy, Edit3, KeyRound, Plus, RefreshCw, Save, Send, ShieldCheck, Trash2, UserPlus, X } from 'lucide-react';
import {
  createDeveloperApp, createDeveloperOrganization, fetchMcpScopeCatalog, getDeveloperApp,
  getDeveloperAppAudit, listDeveloperAppScopeRequests, listDeveloperApps,
  listDeveloperOrganizationMembers, listDeveloperOrganizations, listWorkspaces,
  removeDeveloperOrganizationMember, requestDeveloperAppScopes, revokeDeveloperApp,
  rotateDeveloperAppSecret, updateDeveloperAppScopes, updateDeveloperAppWorkspaces,
  updateDeveloperOrganization, upsertDeveloperOrganizationMember,
} from '../../services/api.js';
import './developerApplications.css';

const DEFAULT_SCOPES = ['workspaces:read', 'documents:read', 'knowledge:query', 'batches:read', 'events:read'];
const ORG_ROLES = ['owner', 'admin', 'developer', 'viewer'];

export default function DeveloperApplications({ activeWorkspace, onClose }) {
  const [view, setView] = useState('applications');
  const [organizations, setOrganizations] = useState([]);
  const [apps, setApps] = useState([]);
  const [workspaces, setWorkspaces] = useState([]);
  const [scopeCatalog, setScopeCatalog] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [secret, setSecret] = useState(null);
  const [audit, setAudit] = useState(null);
  const [orgName, setOrgName] = useState('');
  const [organizationEditor, setOrganizationEditor] = useState(null);
  const [applicationEditor, setApplicationEditor] = useState(null);
  const [form, setForm] = useState({ name:'', organization_id:'', workspace_id:'', scopes:DEFAULT_SCOPES.join(' ') });

  useEffect(() => { refresh(); }, []);

  async function refresh() {
    setBusy(true); setError('');
    try {
      const [orgResult, appResult, workspaceResult, catalogResult] = await Promise.all([
        listDeveloperOrganizations(), listDeveloperApps(), listWorkspaces(), fetchMcpScopeCatalog(),
      ]);
      const availableWorkspaces = Array.isArray(workspaceResult) ? workspaceResult : [];
      setOrganizations(orgResult.data || []);
      setApps(appResult.data || []);
      setWorkspaces(availableWorkspaces);
      setScopeCatalog(catalogResult.scopes || []);
      setForm(current => {
        const activeId = activeWorkspace?.id;
        const workspaceId = activeId && availableWorkspaces.some(item => item.id === activeId)
          ? activeId
          : availableWorkspaces.some(item => item.id === current.workspace_id)
            ? current.workspace_id : availableWorkspaces[0]?.id || '';
        return { ...current, organization_id:current.organization_id || orgResult.data?.[0]?.id || '', workspace_id:workspaceId };
      });
    } catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }

  async function run(operation) {
    setBusy(true); setError(''); setNotice('');
    try { await operation(); }
    catch (e) { setError(typeof e.message === 'string' ? e.message : 'The operation failed.'); }
    finally { setBusy(false); }
  }

  async function addOrganization(event) {
    event.preventDefault();
    if (!orgName.trim()) return;
    await run(async () => {
      const result = await createDeveloperOrganization({ name:orgName.trim() });
      setOrgName('');
      setOrganizations(current => [...current, result.data]);
      setForm(current => ({ ...current, organization_id:result.data.id }));
      await openOrganization(result.data);
    });
  }

  async function addApplication(event) {
    event.preventDefault();
    await run(async () => {
      const scopes = form.scopes.split(/[\s,]+/).filter(Boolean);
      const result = await createDeveloperApp({
        name:form.name.trim(), client_type:'confidential', organization_id:form.organization_id || null,
        workspace_ids:form.workspace_id ? [form.workspace_id] : [], scopes,
      });
      setSecret({ client_id:result.client_id, client_secret:result.client_secret });
      setForm(current => ({ ...current, name:'' }));
      await refresh();
    });
  }

  async function openOrganization(org) {
    await run(async () => {
      const result = await listDeveloperOrganizationMembers(org.id);
      setOrganizationEditor({ ...org, draftName:org.name, members:result.data || [], memberEmail:'', memberRole:'developer' });
    });
  }

  async function reloadOrganizationEditor() {
    const org = organizations.find(item => item.id === organizationEditor.id) || organizationEditor;
    const result = await listDeveloperOrganizationMembers(org.id);
    setOrganizationEditor(current => ({ ...current, ...org, members:result.data || [] }));
  }

  async function saveOrganization() {
    await run(async () => {
      await updateDeveloperOrganization(organizationEditor.id, { name:organizationEditor.draftName.trim() });
      setOrganizationEditor(current => ({ ...current, name:current.draftName.trim() }));
      await refresh();
      setNotice('Organization updated.');
    });
  }

  async function changeOrganizationStatus() {
    const status = organizationEditor.status === 'active' ? 'suspended' : 'active';
    if (!window.confirm(`${status === 'suspended' ? 'Suspend' : 'Reactivate'} this organization?`)) return;
    await run(async () => {
      await updateDeveloperOrganization(organizationEditor.id, { status });
      setOrganizationEditor(current => ({ ...current, status }));
      await refresh();
      setNotice(`Organization ${status}.`);
    });
  }

  async function saveMember(email, role) {
    await run(async () => {
      await upsertDeveloperOrganizationMember(organizationEditor.id, { email, role });
      await reloadOrganizationEditor();
      setOrganizationEditor(current => ({ ...current, memberEmail:'' }));
      setNotice('Organization membership updated.');
    });
  }

  async function removeMember(member) {
    if (!window.confirm(`Remove ${member.email} from this organization?`)) return;
    await run(async () => {
      await removeDeveloperOrganizationMember(organizationEditor.id, member.user_id);
      await reloadOrganizationEditor();
      setNotice('Organization member removed.');
    });
  }

  async function openApplication(clientId) {
    await run(async () => {
      const [detail, requests] = await Promise.all([getDeveloperApp(clientId), listDeveloperAppScopeRequests(clientId)]);
      const app = detail.data;
      setApplicationEditor({
        ...app, selectedScopes:new Set(app.scopes || []),
        selectedWorkspaces:new Set((app.workspaces || []).map(item => item.workspace_id)),
        requests:requests.data || [], requestScopes:new Set(), requestReason:'',
      });
    });
  }

  function toggleEditorSet(key, value) {
    setApplicationEditor(current => {
      const updated = new Set(current[key]);
      updated.has(value) ? updated.delete(value) : updated.add(value);
      return { ...current, [key]:updated };
    });
  }

  async function saveApplicationAccess() {
    await run(async () => {
      const scopes = [...applicationEditor.selectedScopes].sort();
      if (!scopes.length) throw new Error('An application requires at least one OAuth scope.');
      await updateDeveloperAppScopes(applicationEditor.client_id, scopes);
      await updateDeveloperAppWorkspaces(applicationEditor.client_id, [...applicationEditor.selectedWorkspaces]);
      setNotice('Application access updated. Request a new token to use the changes.');
      await openApplication(applicationEditor.client_id);
      await refresh();
    });
  }

  async function submitScopeRequest() {
    const scopes = [...applicationEditor.requestScopes];
    if (!scopes.length) return;
    await run(async () => {
      const result = await requestDeveloperAppScopes(applicationEditor.client_id, scopes, applicationEditor.requestReason);
      setNotice(result.status === 'pending' ? 'Scope request sent to a DocIntel administrator.' : 'Approved scopes added to the application.');
      await openApplication(applicationEditor.client_id);
      await refresh();
    });
  }

  async function rotate(clientId) {
    if (!window.confirm('Rotate this secret now? The previous secret will stop working.')) return;
    await run(async () => setSecret(await rotateDeveloperAppSecret(clientId)));
  }

  async function revoke(clientId) {
    if (!window.confirm('Revoke this application and all future token access?')) return;
    await run(async () => { await revokeDeveloperApp(clientId); setApplicationEditor(null); await refresh(); });
  }

  async function showAudit(clientId) {
    await run(async () => setAudit({ clientId, events:(await getDeveloperAppAudit(clientId)).data || [] }));
  }

  const scopeNames = scopeCatalog.map(item => item.scope);
  const canManageApp = app => !app.organization_id || ['owner', 'admin'].includes(app.organization_role);

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
      {notice && <div className="devapp-notice">{notice}</div>}
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
              <div className="devapp-row-actions"><button onClick={() => openOrganization(org)}><Edit3 size={14}/>Manage</button></div>
            </article>)}
            {!organizations.length && <div className="devapp-empty">Create an organization before registering an enterprise application.</div>}
          </div>
        </> : <>
          <form className="devapp-create app" onSubmit={addApplication}>
            <label><span>Application name</span><input required value={form.name} onChange={e => setForm({...form, name:e.target.value})} placeholder="Procurement integration"/></label>
            <label><span>Organization</span><select required value={form.organization_id} onChange={e => setForm({...form, organization_id:e.target.value})}>
              <option value="">Select organization</option>{organizations.filter(org => org.status === 'active').map(org => <option key={org.id} value={org.id}>{org.name}</option>)}
            </select></label>
            <label><span>Workspace grant</span><select required value={form.workspace_id} onChange={e => setForm({...form, workspace_id:e.target.value})}>
              <option value="">Select team workspace</option>{workspaces.map(workspace => <option key={workspace.id} value={workspace.id}>{workspace.name}{workspace.my_role ? ` · ${workspace.my_role}` : ''}</option>)}</select></label>
            <label><span>Initial OAuth scopes</span><input value={form.scopes} onChange={e => setForm({...form, scopes:e.target.value})}/></label>
            {!workspaces.length && <div className="devapp-workspace"><span>Workspace grant</span><strong>Create or join a team workspace before registering an organization application.</strong></div>}
            <button disabled={busy || !form.name.trim() || !form.organization_id || !form.workspace_id}><Plus size={15}/>Create application</button>
          </form>
          <div className="devapp-list">
            {apps.filter(app => app.client_type === 'confidential').map(app => <article key={app.client_id}>
              <ShieldCheck size={19}/><div><strong>{app.client_name}</strong><code>{app.client_id}</code><span>{app.organization_name || 'User-owned'} · {(app.scopes || []).length} scopes</span></div>
              <span className={`devapp-state ${app.revoked_at ? 'revoked' : 'active'}`}>{app.revoked_at ? 'revoked' : 'active'}</span>
              <div className="devapp-row-actions">
                {!app.revoked_at && canManageApp(app) && <button onClick={() => openApplication(app.client_id)}><Edit3 size={14}/>Manage</button>}
                <button onClick={() => showAudit(app.client_id)}>Audit</button>
                {!app.revoked_at && canManageApp(app) && <button onClick={() => rotate(app.client_id)} title="Rotate secret"><KeyRound size={14}/></button>}
                {!app.revoked_at && canManageApp(app) && <button onClick={() => revoke(app.client_id)} title="Revoke application"><Trash2 size={14}/></button>}
              </div>
            </article>)}
            {!apps.some(app => app.client_type === 'confidential') && <div className="devapp-empty">No confidential applications are registered.</div>}
          </div>
        </>}
      </main>

      {organizationEditor && <Inspector title="Organization management" onClose={() => setOrganizationEditor(null)}>
        <div className="devapp-inspector-section">
          <h3>Organization</h3>
          <div className="devapp-inline-form">
            <label><span>Name</span><input disabled={!['owner', 'admin'].includes(organizationEditor.role)} value={organizationEditor.draftName} onChange={e => setOrganizationEditor({...organizationEditor, draftName:e.target.value})}/></label>
            {['owner', 'admin'].includes(organizationEditor.role) && <button onClick={saveOrganization} disabled={busy || !organizationEditor.draftName.trim()}><Save size={14}/>Save</button>}
            {organizationEditor.role === 'owner' && <button className="danger" onClick={changeOrganizationStatus}>{organizationEditor.status === 'active' ? 'Suspend' : 'Reactivate'}</button>}
          </div>
          <p className="devapp-help">Suspension blocks organization-managed access without deleting membership or audit history.</p>
        </div>
        <div className="devapp-inspector-section">
          <h3>Members</h3>
          {['owner', 'admin'].includes(organizationEditor.role) && organizationEditor.status === 'active' && <div className="devapp-inline-form member-add">
            <label><span>Registered user email</span><input value={organizationEditor.memberEmail} onChange={e => setOrganizationEditor({...organizationEditor, memberEmail:e.target.value})} placeholder="developer@example.com"/></label>
            <label><span>Role</span><select value={organizationEditor.memberRole} onChange={e => setOrganizationEditor({...organizationEditor, memberRole:e.target.value})}>{ORG_ROLES.filter(role => organizationEditor.role === 'owner' || role !== 'owner').map(role => <option key={role}>{role}</option>)}</select></label>
            <button onClick={() => saveMember(organizationEditor.memberEmail, organizationEditor.memberRole)} disabled={!organizationEditor.memberEmail.trim()}><UserPlus size={14}/>Add</button>
          </div>}
          <div className="devapp-member-list">{organizationEditor.members.map(member => <article key={member.user_id}>
            <div><strong>{member.full_name || member.email}</strong><span>{member.email}</span></div>
            {['owner', 'admin'].includes(organizationEditor.role) && organizationEditor.status === 'active' && (organizationEditor.role === 'owner' || member.role !== 'owner')
              ? <select value={member.role} onChange={e => saveMember(member.email, e.target.value)}>{ORG_ROLES.filter(role => organizationEditor.role === 'owner' || role !== 'owner').map(role => <option key={role}>{role}</option>)}</select>
              : <span>{member.role}</span>}
            {['owner', 'admin'].includes(organizationEditor.role) && organizationEditor.status === 'active' && (organizationEditor.role === 'owner' || member.role !== 'owner') && <button className="devapp-icon danger" onClick={() => removeMember(member)} title="Remove member"><Trash2 size={14}/></button>}
          </article>)}</div>
        </div>
      </Inspector>}

      {applicationEditor && <Inspector title="Application access" onClose={() => setApplicationEditor(null)}>
        <div className="devapp-inspector-section">
          <h3>{applicationEditor.client_name}</h3><code className="devapp-client-id">{applicationEditor.client_id}</code>
          <p className="devapp-help">Changes apply to newly issued tokens. Existing tokens retain their original claims until expiry or revocation.</p>
        </div>
        <div className="devapp-inspector-grid">
          <div className="devapp-inspector-section"><h3>Enabled scopes</h3><div className="devapp-check-list">{scopeNames.map(scope => <label key={scope}><input type="checkbox" checked={applicationEditor.selectedScopes.has(scope)} onChange={() => toggleEditorSet('selectedScopes', scope)}/><span>{scope}</span></label>)}</div></div>
          <div className="devapp-inspector-section"><h3>Workspace grants</h3><div className="devapp-check-list">{workspaces.map(workspace => <label key={workspace.id}><input type="checkbox" checked={applicationEditor.selectedWorkspaces.has(workspace.id)} onChange={() => toggleEditorSet('selectedWorkspaces', workspace.id)}/><span>{workspace.name}</span></label>)}</div></div>
        </div>
        <div className="devapp-footer-action"><button onClick={saveApplicationAccess} disabled={busy}><Save size={14}/>Save application access</button></div>
        <div className="devapp-inspector-section">
          <h3>Request additional scopes</h3>
          <div className="devapp-check-list compact">{scopeNames.filter(scope => !applicationEditor.scopes.includes(scope)).map(scope => <label key={scope}><input type="checkbox" checked={applicationEditor.requestScopes.has(scope)} onChange={() => toggleEditorSet('requestScopes', scope)}/><span>{scope}</span></label>)}</div>
          <textarea value={applicationEditor.requestReason} onChange={e => setApplicationEditor({...applicationEditor, requestReason:e.target.value})} placeholder="Explain the integration need and least-privilege use case."/>
          <button onClick={submitScopeRequest} disabled={busy || !applicationEditor.requestScopes.size}><Send size={14}/>Submit scope request</button>
        </div>
        <div className="devapp-inspector-section"><h3>Request history</h3><div className="devapp-request-list">{applicationEditor.requests.map(item => <article key={item.id}><strong>{item.scope}</strong><span className={`devapp-state ${item.status}`}>{item.status}</span><small>{item.reviewer_note || item.reason || 'No note'}</small></article>)}{!applicationEditor.requests.length && <p className="devapp-help">No additional scope requests.</p>}</div></div>
      </Inspector>}

      {audit && <Inspector title="Application audit" onClose={() => setAudit(null)}><div className="devapp-audit-list">{audit.events.map(event => <article key={event.id}><strong>{event.event_type}</strong><span>{new Date(event.created_at).toLocaleString()}</span><pre>{JSON.stringify(event.metadata, null, 2)}</pre></article>)}</div></Inspector>}
    </section>
  </div>, document.body);
}

function Inspector({ title, onClose, children }) {
  return <div className="devapp-inspector"><header><strong>{title}</strong><button className="devapp-icon" onClick={onClose} aria-label={`Close ${title}`}><X size={17}/></button></header><div className="devapp-inspector-body">{children}</div></div>;
}
