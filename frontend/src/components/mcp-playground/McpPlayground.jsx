import React, { useEffect, useRef, useState } from 'react';
import { BookOpen, Check, ChevronDown, CircleStop, Copy, Link, Play, Plug, Search, Terminal, Trash2, X } from 'lucide-react';
import {
  disconnectMcpPlayground,
  executeMcpPlayground,
  getMcpPlaygroundExamples,
  getMcpPlaygroundStatus,
  startMcpPlaygroundOAuth,
} from '../../services/mcpPlaygroundApi.js';
import './mcpPlayground.css';
import './mcpCatalog.css';
import { getPanelText } from '../panelTranslations.js';

const SENSITIVE = ['delete_document', 'delete_chat_session', 'approve_vertical_run', 'generate_vertical_packet', 'cancel_batch_job'];
const API_ORIGIN = new URL(import.meta.env.VITE_API_URL || window.location.origin, window.location.origin).origin;
const SCOPE_PROFILES = {
  read: ['workspaces:read','documents:read','knowledge:query','knowledge:generate','video:read','workflows:read','batches:read'],
  content: ['workspaces:read','documents:read','documents:write','knowledge:query','knowledge:generate','sessions:write','video:read','video:process','workflows:read','batches:read','batches:write'],
  governed: ['workspaces:read','documents:read','documents:write','knowledge:query','knowledge:generate','sessions:write','video:read','video:process','workflows:read','workflows:write','reviews:write','reviews:approve','packets:write','batches:read','batches:write'],
};

export default function McpPlayground({ onClose, language = 'en' }) {
  const tx = getPanelText(language);
  const [status, setStatus] = useState({ connected:false, scopes:[] });
  const [command, setCommand] = useState("mcp_tool list_workspaces '{}' | tool_data | jq '.'");
  const [entries, setEntries] = useState([]);
  const [examples, setExamples] = useState([]);
  const [busy, setBusy] = useState(false);
  const [showExamples, setShowExamples] = useState(false);
  const [exampleSearch, setExampleSearch] = useState('');
  const [exampleCategory, setExampleCategory] = useState('All');
  const [view, setView] = useState('result');
  const [historyIndex, setHistoryIndex] = useState(-1);
  const [scopeProfile, setScopeProfile] = useState('read');
  const outputRef = useRef(null);

  useEffect(() => {
    refreshStatus();
    getMcpPlaygroundExamples().then(data => setExamples(data.examples || [])).catch(() => {});
    const receive = event => {
      if (![window.location.origin, API_ORIGIN].includes(event.origin) || event.data?.type !== 'docintel-mcp-oauth') return;
      addSystem(event.data.message, event.data.success ? 'success' : 'error');
      refreshStatus();
    };
    window.addEventListener('message', receive);
    return () => window.removeEventListener('message', receive);
  }, []);

  useEffect(() => {
    outputRef.current?.scrollTo({ top:outputRef.current.scrollHeight, behavior:'smooth' });
  }, [entries, busy]);

  async function refreshStatus() {
    try { setStatus(await getMcpPlaygroundStatus()); }
    catch (error) { addSystem(error.message, 'error'); }
  }

  async function connect() {
    const popup = window.open('', 'docintel-mcp-oauth', 'width=620,height=760');
    try {
      setBusy(true);
      const data = await startMcpPlaygroundOAuth(SCOPE_PROFILES[scopeProfile]);
      if (popup) popup.location = data.authorization_url;
      else window.location.assign(data.authorization_url);
    } catch (error) {
      popup?.close();
      addSystem(error.message, 'error');
    } finally { setBusy(false); }
  }

  async function disconnect() {
    try {
      setBusy(true);
      await disconnectMcpPlayground();
      setStatus({ connected:false, scopes:[] });
      addSystem('MCP OAuth session disconnected and revoked.', 'success');
    } catch (error) { addSystem(error.message, 'error'); }
    finally { setBusy(false); }
  }

  async function run(confirm = false) {
    const value = command.trim();
    if (!value || busy) return;
    const needsConfirmation = SENSITIVE.some(name => value.includes(`mcp_tool ${name}`));
    if (needsConfirmation && !confirm && !window.confirm('This command changes or generates governed data. Continue?')) return;
    const entry = { id:Date.now(), command:value, loading:true };
    setEntries(previous => [...previous, entry]);
    setHistoryIndex(-1);
    setBusy(true);
    try {
      const response = await executeMcpPlayground(value, needsConfirmation || confirm);
      setEntries(previous => previous.map(item => item.id === entry.id ? { ...item, loading:false, response } : item));
      if (!status.connected && !response?.local_command) refreshStatus();
    } catch (error) {
      setEntries(previous => previous.map(item => item.id === entry.id ? { ...item, loading:false, error:error.message } : item));
    } finally { setBusy(false); }
  }

  function addSystem(message, type = 'info') {
    setEntries(previous => [...previous, { id:Date.now() + Math.random(), system:true, message, type }]);
  }

  function keyDown(event) {
    if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); run(); return; }
    const commands = entries.filter(item => item.command).map(item => item.command);
    if (!commands.length || !['ArrowUp', 'ArrowDown'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'ArrowUp'
      ? Math.min(historyIndex + 1, commands.length - 1)
      : Math.max(historyIndex - 1, -1);
    setHistoryIndex(next);
    setCommand(next < 0 ? '' : commands[commands.length - 1 - next]);
  }

  const categories = ['All', ...new Set(examples.map(example => example.category))];
  const normalizedSearch = exampleSearch.trim().toLowerCase();
  const filteredExamples = examples.filter(example => {
    const categoryMatch = exampleCategory === 'All' || example.category === exampleCategory;
    const searchMatch = !normalizedSearch || [example.name, example.tool, example.description, example.category]
      .some(value => String(value || '').toLowerCase().includes(normalizedSearch));
    return categoryMatch && searchMatch;
  });

  return (
    <div className="mcp-overlay" role="dialog" aria-modal="true" aria-label={tx.mcpTitle}>
      <section className="mcp-shell">
        <header className="mcp-header">
          <div className="mcp-title-wrap">
            <span className="mcp-logo"><Terminal size={20}/></span>
            <div><p>{tx.mcpIntegration}</p><h2>{tx.mcpTitle}</h2></div>
          </div>
          <div className="mcp-header-actions">
            <span className={`mcp-status ${status.connected ? 'connected' : ''}`}>
              {status.connected ? <Check size={14}/> : <CircleStop size={14}/>} {status.connected ? tx.connected : tx.notConnected}
            </span>
            {status.connected && <button className="mcp-btn primary" onClick={connect} disabled={busy}><Plug size={15}/>Update access</button>}
            {status.connected
              ? <button className="mcp-btn secondary" onClick={disconnect} disabled={busy}><Link size={15}/>{tx.disconnect}</button>
              : <button className="mcp-btn primary" onClick={connect} disabled={busy}><Plug size={15}/>{tx.connect}</button>}
            <button className="mcp-icon-btn" onClick={onClose} aria-label={tx.closeMcp}><X size={19}/></button>
          </div>
        </header>

        <div className="mcp-scope-bar">
          <span>{tx.endpoint}</span><code>https://mcp.docintel.adar.agomoniai.com/mcp</code>
          <select value={scopeProfile} onChange={event => setScopeProfile(event.target.value)} aria-label="Requested MCP access profile">
            <option value="read">Read and query</option>
            <option value="content">Content operations</option>
            <option value="governed">Governed workflows</option>
          </select>
          {status.connected && <span className="mcp-scope-count" title={status.scopes.join(', ')}>{status.scopes.length} {tx.scopes}</span>}
        </div>

        <div className="mcp-toolbar">
          <div className="mcp-examples-wrap">
            <button className="mcp-btn secondary" onClick={() => setShowExamples(value => !value)}>
              <BookOpen size={15}/>{tx.examples}<ChevronDown size={14}/>
            </button>
            {showExamples && <div className="mcp-examples-menu">
              <div className="mcp-example-controls">
                <select value={exampleCategory} onChange={event => setExampleCategory(event.target.value)} aria-label="Filter command category">
                  {categories.map(category => <option key={category}>{category}</option>)}
                </select>
                <span>{filteredExamples.length} / {examples.length} {tx.commands}</span>
              </div>
              <div className="mcp-example-list">
                {filteredExamples.map((example, index) => <button key={`${example.category}-${example.name}-${index}`} onClick={() => { setCommand(example.command); setShowExamples(false); }}>
                  <span><strong>{example.name}</strong><small>{example.category}</small></span>
                  <p>{example.description}</p><code>{example.command}</code>
                </button>)}
                {!filteredExamples.length && <p className="mcp-example-empty">{tx.noCommands}</p>}
              </div>
            </div>}
          </div>
          <label className="mcp-toolbar-search">
            <Search size={14}/>
            <input
              value={exampleSearch}
              onChange={event => {
                setExampleSearch(event.target.value);
                setShowExamples(true);
              }}
              onFocus={() => setShowExamples(true)}
              placeholder={`${tx.searchCommands} (${examples.length})`}
              aria-label="Search MCP example commands"
            />
            {exampleSearch && <button type="button" onClick={() => setExampleSearch('')} aria-label="Clear command search"><X size={13}/></button>}
          </label>
          <div className="mcp-view-tabs">
            <button className={view === 'result' ? 'active' : ''} onClick={() => setView('result')}>{tx.formatted}</button>
            <button className={view === 'raw' ? 'active' : ''} onClick={() => setView('raw')}>{tx.rawMcp}</button>
          </div>
          <button className="mcp-icon-btn" title={tx.clearTerminal} onClick={() => setEntries([])}><Trash2 size={16}/></button>
        </div>

        <div className="mcp-output" ref={outputRef}>
          {!entries.length && <div className="mcp-welcome">
            <Terminal size={28}/><strong>{tx.restrictedTerminal}</strong>
            <p>{tx.terminalHelp}</p>
          </div>}
          {entries.map(entry => entry.system
            ? <div key={entry.id} className={`mcp-system ${entry.type}`}>{entry.message}</div>
            : <div key={entry.id} className="mcp-entry">
                <div className="mcp-command-line"><span>$</span><code>{entry.command}</code><button onClick={() => navigator.clipboard?.writeText(entry.command)} title="Copy command"><Copy size={13}/></button></div>
                {entry.loading ? <div className="mcp-running">{tx.running}</div>
                  : entry.error ? <pre className="mcp-error">{entry.error}</pre>
                  : <>
                      <pre>{JSON.stringify(view === 'raw' ? entry.response?.raw : entry.response?.result, null, 2)}</pre>
                      <small>{entry.response?.duration_ms != null ? `${entry.response.duration_ms} ms` : 'Local command'}</small>
                    </>}
              </div>)}
        </div>

        <footer className="mcp-input-area">
          <span className="mcp-prompt">$</span>
          <textarea value={command} onChange={event => setCommand(event.target.value)} onKeyDown={keyDown}
            placeholder="mcp_tool list_workspaces '{}' | tool_data | jq '.'" rows={2} spellCheck={false}/>
          <button className="mcp-run" onClick={() => run()} disabled={busy || !command.trim()} title={tx.runCommand}><Play size={18}/></button>
        </footer>
      </section>
    </div>
  );
}
