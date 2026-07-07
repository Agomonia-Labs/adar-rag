import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  analyzeRestaurantFeedback,
  approveRestaurantAgentRun,
  compareRestaurantMenu,
  createRestaurantOrderDraft,
  createRestaurantOrderCheckout,
  deleteRestaurant,
  fetchRestaurant,
  fetchRestaurantAgentRun,
  listMyRestaurantOrders,
  listMyRestaurantFeedback,
  listRestaurantOwnerFeedback,
  listRestaurantOwnerOrders,
  listRestaurants,
  recommendRestaurantMenu,
  runRestaurantScribeWorkflow,
  searchRestaurantMenu,
  submitRestaurantFeedback,
  transcribeVoice,
  updateRestaurant,
  updateRestaurantFeedbackStatus,
  updateRestaurantOwnerOrder,
} from '../services/api.js';

const LANGUAGES = [
  { value:'en-US', label:'English' },
  { value:'es-US', label:'Spanish' },
  { value:'bn-BD', label:'Bangla' },
  { value:'hi-IN', label:'Hindi' },
  { value:'ar', label:'Arabic' },
];

const RECORDING_SEGMENT_MS = 180000;

export default function RestaurantPanel({ workspaceId = null, activeWorkspace = null, onClose }) {
  const [tab, setTab] = useState('scribe');
  const [title, setTitle] = useState('');
  const [language, setLanguage] = useState('en-US');
  const [authorized, setAuthorized] = useState(false);
  const [audioBlob, setAudioBlob] = useState(null);
  const [audioSegments, setAudioSegments] = useState([]);
  const [audioName, setAudioName] = useState('');
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [run, setRun] = useState(null);
  const [packet, setPacket] = useState(null);
  const [restaurants, setRestaurants] = useState([]);
  const [selected, setSelected] = useState(null);
  const [selectedDetail, setSelectedDetail] = useState(null);
  const [editingSaved, setEditingSaved] = useState(false);
  const [savedDraft, setSavedDraft] = useState(null);
  const [compareQuery, setCompareQuery] = useState('');
  const [compareRows, setCompareRows] = useState([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchRows, setSearchRows] = useState([]);
  const [recommendRows, setRecommendRows] = useState([]);
  const [cart, setCart] = useState({ restaurant_id:null, restaurant_name:'', items:[] });
  const [customer, setCustomer] = useState({ name:'', phone:'', email:'', pickup_time_request:'', special_instructions:'' });
  const [draftOrder, setDraftOrder] = useState(null);
  const [myOrders, setMyOrders] = useState([]);
  const [ownerOrders, setOwnerOrders] = useState([]);
  const [myFeedback, setMyFeedback] = useState([]);
  const [ownerFeedback, setOwnerFeedback] = useState([]);
  const [feedbackDraft, setFeedbackDraft] = useState(null);
  const [orderStatusFilter, setOrderStatusFilter] = useState('');
  const workspaceRole = activeWorkspace?.my_role || null;
  const canManageRestaurantOps = !workspaceId || ['editor', 'owner'].includes(workspaceRole);
  const canProcessRestaurantOrders = canManageRestaurantOps || ownerOrders.length > 0;
  const tabs = canManageRestaurantOps
    ? [
        ['scribe', '🎙 Scribe intake'],
        ['restaurants', '🍽 Restaurants'],
        ['compare', '⇄ Compare menus'],
        ['orders', '🛒 Carryout orders'],
        ['feedback', '⭐ Feedback'],
      ]
    : [
        ['restaurants', '🍽 Restaurants'],
        ['compare', '⇄ Compare menus'],
        ['orders', '🛒 My carryout orders'],
        ['feedback', '⭐ Feedback'],
      ];
  const mediaRef = useRef(null);
  const streamRef = useRef(null);
  const segmentChunksRef = useRef([]);
  const allSegmentBlobsRef = useRef([]);
  const segmentIndexRef = useRef(0);
  const segmentTimerRef = useRef(null);
  const recordingActiveRef = useRef(false);

  const steps = run?.steps || [];
  const profile = packet?.restaurant_profile || {};
  const menuItems = packet?.menu_items || [];
  const canApprove = packet && profile.name && profile.email && menuItems.length > 0 && !busy;

  useEffect(() => {
    if (!run?.run_id || !['running', 'pending'].includes(run.status)) return;
    const timer = setInterval(async () => {
      try {
        const fresh = await fetchRestaurantAgentRun(run.run_id);
        setRun(fresh);
        const approved = fresh?.result?.approved_packet || fresh?.result;
        if (approved) setPacket(normalizePacket(approved));
        if (!['running', 'pending'].includes(fresh.status)) clearInterval(timer);
      } catch (e) {
        setError(e.message || 'Could not refresh restaurant workflow');
      }
    }, 2500);
    return () => clearInterval(timer);
  }, [run?.run_id, run?.status]);

  useEffect(() => {
    loadRestaurants();
  }, [workspaceId]);

  useEffect(() => {
    if (!tabs.some(([key]) => key === tab)) setTab(tabs[0][0]);
  }, [canManageRestaurantOps, tab]);

  useEffect(() => {
    if (tab === 'orders') loadOrders();
  }, [tab, workspaceId, orderStatusFilter]);

  useEffect(() => {
    if (tab === 'feedback') loadFeedback();
  }, [tab, workspaceId]);

  async function loadRestaurants() {
    try {
      setError('');
      const data = await listRestaurants(workspaceId);
      setRestaurants(data.restaurants || []);
      if (data.restored_count) {
        setError(`Restored ${data.restored_count} old restaurant scribe record${data.restored_count === 1 ? '' : 's'}.`);
      }
    } catch (e) {
      setRestaurants([]);
      setError(e.message || 'Could not load saved restaurants.');
    }
  }

  async function startRecording() {
    setError('');
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      setError('Audio recording is not supported in this browser. Use Upload audio instead.');
      return;
    }
    const stream = await navigator.mediaDevices.getUserMedia({ audio:true });
    streamRef.current = stream;
    segmentChunksRef.current = [];
    allSegmentBlobsRef.current = [];
    segmentIndexRef.current = 0;
    recordingActiveRef.current = true;
    setAudioBlob(null);
    setAudioName('');
    setAudioSegments([]);
    setRecording(true);
    startRecordingSegment(stream);
  }

  function startRecordingSegment(stream) {
    if (!recordingActiveRef.current || !stream) return;
    segmentChunksRef.current = [];
    const recorder = new MediaRecorder(stream);
    mediaRef.current = recorder;
    recorder.ondataavailable = e => {
      if (e.data?.size) segmentChunksRef.current.push(e.data);
    };
    recorder.onstop = () => {
      const chunks = segmentChunksRef.current.slice();
      if (chunks.length) {
        const segmentIndex = segmentIndexRef.current + 1;
        segmentIndexRef.current = segmentIndex;
        const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
        const filename = `restaurant-menu-segment-${String(segmentIndex).padStart(3, '0')}.webm`;
        allSegmentBlobsRef.current = [...allSegmentBlobsRef.current, blob];
        setAudioSegments(prev => [...prev, { blob, filename }]);
        setAudioBlob(new Blob(allSegmentBlobsRef.current, { type: recorder.mimeType || 'audio/webm' }));
        setAudioName(`restaurant-menu-intake-${Date.now()}.webm`);
      }
      if (recordingActiveRef.current) {
        segmentTimerRef.current = window.setTimeout(() => startRecordingSegment(streamRef.current), 250);
      } else {
        stream.getTracks().forEach(t => t.stop());
        streamRef.current = null;
      }
    };
    recorder.start();
    segmentTimerRef.current = window.setTimeout(() => {
      if (recorder.state === 'recording') recorder.stop();
    }, RECORDING_SEGMENT_MS);
  }

  function stopRecording() {
    recordingActiveRef.current = false;
    if (segmentTimerRef.current) window.clearTimeout(segmentTimerRef.current);
    const recorder = mediaRef.current;
    if (recorder?.state === 'recording') {
      recorder.stop();
    } else {
      streamRef.current?.getTracks().forEach(t => t.stop());
      streamRef.current = null;
    }
    setRecording(false);
  }

  async function runWorkflow() {
    setError('');
    if (!authorized) { setError('Please confirm you are authorized to publish or update this restaurant menu.'); return; }
    if (!audioBlob) { setError('Record or upload audio first.'); return; }
    setBusy(true);
    try {
      const data = await runRestaurantScribeWorkflow(audioBlob, {
        language,
        authorizedConfirmed: authorized,
        intakeTitle: title,
        workspaceId,
        filename: audioName || 'restaurant-menu-intake.webm',
        audioSegments: audioSegments.length ? audioSegments : null,
      });
      setRun(data);
      const approved = data?.result?.approved_packet || data?.result;
      if (approved) setPacket(normalizePacket(approved));
    } catch (e) {
      setError(e.message || 'Restaurant workflow failed');
    } finally {
      setBusy(false);
    }
  }

  async function approvePacket() {
    setBusy(true);
    setError('');
    try {
      const data = await approveRestaurantAgentRun(run.run_id, packet, 'Owner approved restaurant/menu data');
      setRun(data);
      await loadRestaurants();
      setTab('restaurants');
    } catch (e) {
      const msg = e.message || 'Could not approve restaurant menu';
      setError(msg === 'Restaurant email is required' ? '' : msg);
    } finally {
      setBusy(false);
    }
  }

  async function openRestaurant(id) {
    setError('');
    setSelected(id);
    setSelectedDetail(null);
    setEditingSaved(false);
    setSavedDraft(null);
    const data = await fetchRestaurant(id, workspaceId);
    setSelectedDetail(data);
  }

  function startSavedEdit() {
    if (!selectedDetail) return;
    if (!selectedDetail?.restaurant?.can_manage) {
      setError('Only a matching restaurant email or workspace owner/editor can update this menu.');
      return;
    }
    setSavedDraft({
      restaurant_profile: { ...(selectedDetail.restaurant || {}) },
      menu_items: (selectedDetail.menu_items || []).map(item => ({ ...item })),
    });
    setEditingSaved(true);
  }

  async function saveRestaurantEdits() {
    if (!selected || !savedDraft) return;
    if (!selectedDetail?.restaurant?.can_manage) {
      setError('Only a matching restaurant email or workspace owner/editor can update this menu.');
      return;
    }
    setBusy(true);
    setError('');
    try {
      const data = await updateRestaurant(selected, savedDraft.restaurant_profile, savedDraft.menu_items);
      setSelectedDetail(data);
      setEditingSaved(false);
      setSavedDraft(null);
      await loadRestaurants();
    } catch (e) {
      const msg = e.message || 'Could not save restaurant changes';
      setError(msg === 'Restaurant email is required' ? '' : msg);
    } finally {
      setBusy(false);
    }
  }

  async function removeSavedRestaurant() {
    if (!selected) return;
    if (!selectedDetail?.restaurant?.can_manage) {
      setError('Only a matching restaurant email or workspace owner/editor can delete this restaurant.');
      return;
    }
    if (!window.confirm('Delete this restaurant, saved menu items, carryout orders, scribe source document, embedded chunks, vectors, and stored files?')) return;
    setBusy(true);
    setError('');
    try {
      await deleteRestaurant(selected);
      setSelected(null);
      setSelectedDetail(null);
      setSavedDraft(null);
      setEditingSaved(false);
      await loadRestaurants();
    } catch (e) {
      setError(e.message || 'Could not delete restaurant');
    } finally {
      setBusy(false);
    }
  }

  async function runSearch() {
    const data = await searchRestaurantMenu({ query: searchQuery, workspaceId });
    setSearchRows(data.items || []);
    setCompareRows([]);
    setRecommendRows([]);
  }

  async function runCompare() {
    if (!compareQuery.trim()) return;
    const data = await compareRestaurantMenu({ query: compareQuery, workspaceId });
    setCompareRows(data.items || []);
    setSearchRows([]);
    setRecommendRows([]);
  }

  async function runRecommend() {
    const query = compareQuery.trim() || searchQuery.trim();
    if (!query) return;
    const data = await recommendRestaurantMenu({ query, workspaceId });
    setRecommendRows(data.items || []);
    setCompareRows([]);
    setSearchRows([]);
  }

  function addCartItem(item) {
    setError('');
    if (!item?.id || !item?.restaurant_id) return;
    if (cart.restaurant_id && cart.restaurant_id !== item.restaurant_id) {
      if (!window.confirm('Cart can include one restaurant only for carryout. Replace current cart?')) return;
      setDraftOrder(null);
      setCart({ restaurant_id:item.restaurant_id, restaurant_name:item.restaurant_name || '', items:[{ ...item, quantity_ordered:1, instructions:'' }] });
      return;
    }
    setDraftOrder(null);
    setCart(c => {
      const existing = c.items.find(x => x.id === item.id);
      const items = existing
        ? c.items.map(x => x.id === item.id ? { ...x, quantity_ordered:(x.quantity_ordered || 1) + 1 } : x)
        : [...c.items, { ...item, quantity_ordered:1, instructions:'' }];
      return { restaurant_id:item.restaurant_id, restaurant_name:item.restaurant_name || c.restaurant_name || '', items };
    });
  }

  function updateCartItem(id, key, value) {
    setDraftOrder(null);
    setCart(c => ({ ...c, items:c.items.map(item => item.id === id ? { ...item, [key]: key === 'quantity_ordered' ? Math.max(1, Number(value) || 1) : value } : item) }));
  }

  function removeCartItem(id) {
    setDraftOrder(null);
    setCart(c => {
      const items = c.items.filter(item => item.id !== id);
      return items.length ? { ...c, items } : { restaurant_id:null, restaurant_name:'', items:[] };
    });
  }

  async function createOrderDraft() {
    if (!cart.items.length) { setError('Add menu items to the cart first.'); return; }
    setBusy(true);
    setError('');
    try {
      const data = await createRestaurantOrderDraft({
        restaurant_id: cart.restaurant_id,
        workspace_id: workspaceId,
        items: cart.items.map(item => ({
          menu_item_id: item.id,
          quantity: item.quantity_ordered || 1,
          instructions: item.instructions || '',
        })),
        customer_name: customer.name,
        customer_phone: customer.phone,
        customer_email: customer.email,
        pickup_time_request: customer.pickup_time_request,
        special_instructions: customer.special_instructions,
        notes: 'Carryout order created from DocIntel restaurant conversation',
      });
      setDraftOrder(data);
    } catch (e) {
      setError(e.message || 'Could not create carryout order');
    } finally {
      setBusy(false);
    }
  }

  async function placeOrder() {
    const orderId = draftOrder?.order?.id;
    if (!orderId) return;
    setBusy(true);
    setError('');
    try {
      const baseUrl = window.location.href.split('?')[0];
      const workspaceParam = workspaceId ? `&workspace_id=${encodeURIComponent(workspaceId)}` : '';
      const data = await createRestaurantOrderCheckout(orderId, {
        workspaceId,
        successUrl: `${baseUrl}?restaurant_payment=success&order_id=${encodeURIComponent(orderId)}${workspaceParam}&session_id={CHECKOUT_SESSION_ID}`,
        cancelUrl: `${baseUrl}?restaurant_payment=cancelled&order_id=${encodeURIComponent(orderId)}${workspaceParam}`,
      });
      if (data.checkout_url) {
        window.location.href = data.checkout_url;
        return;
      }
      if (data.order) setDraftOrder(data.order);
    } catch (e) {
      setError(e.message || 'Could not start payment checkout');
    } finally {
      setBusy(false);
    }
  }

  async function loadOrders() {
    try {
      const [mine, owner] = await Promise.all([
        listMyRestaurantOrders(workspaceId),
        listRestaurantOwnerOrders({ status: orderStatusFilter, workspaceId }),
      ]);
      setMyOrders(mine.orders || []);
      setOwnerOrders(owner.orders || []);
    } catch (e) {
      setError(e.message || 'Could not load restaurant orders');
    }
  }

  async function ownerAction(orderId, action) {
    setBusy(true);
    setError('');
    try {
      await updateRestaurantOwnerOrder(orderId, action, `${action} from restaurant owner console`, workspaceId);
      await loadOrders();
    } catch (e) {
      setError(e.message || `Could not ${action} order`);
    } finally {
      setBusy(false);
    }
  }

  function startFeedback(target = {}) {
    setFeedbackDraft({
      restaurant_id: target.restaurant_id || '',
      restaurant_name: target.restaurant_name || '',
      menu_item_id: target.menu_item_id || target.id || null,
      menu_item_name: target.menu_item_name || target.item_name || '',
      order_id: target.order_id || null,
      rating: 5,
      feedback_text: '',
      language,
      source_type: 'text',
      tags: [],
    });
    setTab('feedback');
  }

  async function submitFeedbackDraft() {
    if (!feedbackDraft?.restaurant_id) { setError('Restaurant is required for feedback.'); return; }
    setBusy(true);
    setError('');
    try {
      await submitRestaurantFeedback({
        restaurant_id: feedbackDraft.restaurant_id,
        menu_item_id: feedbackDraft.menu_item_id,
        order_id: feedbackDraft.order_id,
        rating: Number(feedbackDraft.rating || 5),
        feedback_text: feedbackDraft.feedback_text || '',
        language: feedbackDraft.language || language,
        source_type: feedbackDraft.source_type || (feedbackDraft.order_id ? 'order' : 'menu'),
        tags: feedbackDraft.tags || [],
        signals: {
          ...(feedbackDraft.sentiment_analysis || {}),
          ...(feedbackDraft.source_type === 'voice' ? { voice_feedback: true, transcribed: true } : {}),
        },
        metadata: feedbackDraft.voice_metadata || {},
      });
      setFeedbackDraft(null);
      await Promise.all([loadFeedback(), loadRestaurants()]);
      if (selected) await openRestaurant(selected);
    } catch (e) {
      setError(e.message || 'Could not submit restaurant feedback');
    } finally {
      setBusy(false);
    }
  }

  async function loadFeedback() {
    try {
      const [mine, owner] = await Promise.all([
        listMyRestaurantFeedback(workspaceId),
        listRestaurantOwnerFeedback({ workspaceId }),
      ]);
      setMyFeedback(mine.feedback || []);
      setOwnerFeedback(owner.feedback || []);
    } catch (e) {
      setError(e.message || 'Could not load restaurant feedback');
    }
  }

  async function respondFeedback(feedbackId, status = 'acknowledged') {
    const ownerResponse = window.prompt('Optional restaurant response to customer feedback:', '') || '';
    setBusy(true);
    setError('');
    try {
      await updateRestaurantFeedbackStatus(feedbackId, { status, ownerResponse, workspaceId });
      await loadFeedback();
    } catch (e) {
      setError(e.message || 'Could not update feedback');
    } finally {
      setBusy(false);
    }
  }

  function updateProfile(key, value) {
    setPacket(p => ({ ...normalizePacket(p), restaurant_profile:{ ...(p?.restaurant_profile || {}), [key]:value } }));
  }

  function updateMenu(index, key, value) {
    setPacket(p => {
      const next = normalizePacket(p);
      next.menu_items = next.menu_items.map((item, i) => i === index ? { ...item, [key]: key === 'price' ? parsePrice(value) : value } : item);
      return next;
    });
  }

  function addMenuItem() {
    setPacket(p => {
      const next = normalizePacket(p);
      next.menu_items.push({ category:'', item_name:'', price:null, currency:'USD', quantity:'', description:'', dietary_tags:[], availability:'available' });
      return next;
    });
  }

  function removeMenuItem(index) {
    setPacket(p => {
      const next = normalizePacket(p);
      next.menu_items = next.menu_items.filter((_, i) => i !== index);
      return next;
    });
  }

  function updateSavedProfile(key, value) {
    setSavedDraft(d => ({ ...d, restaurant_profile:{ ...(d?.restaurant_profile || {}), [key]:value } }));
  }

  function updateSavedMenu(index, key, value) {
    setSavedDraft(d => ({
      ...d,
      menu_items:(d?.menu_items || []).map((item, i) => i === index ? { ...item, [key]: key === 'price' ? parsePrice(value) : value } : item),
    }));
  }

  function addSavedMenuItem() {
    setSavedDraft(d => ({
      ...d,
      menu_items:[...(d?.menu_items || []), { category:'', item_name:'', price:null, currency:'USD', quantity:'', description:'', dietary_tags:[], availability:'available' }],
    }));
  }

  function removeSavedMenuItem(index) {
    setSavedDraft(d => ({ ...d, menu_items:(d?.menu_items || []).filter((_, i) => i !== index) }));
  }

  const statusText = useMemo(() => {
    if (!run) return 'Ready for restaurant owner scribe intake';
    if (run.status === 'running') return 'Agent workflow is running';
    if (run.status === 'pending_approval') return 'Review extracted profile and menu before saving';
    if (run.status === 'approved') return 'Restaurant menu saved';
    if (run.status === 'failed') return run.error_message || 'Workflow failed';
    return run.status;
  }, [run]);

  return (
    <div style={s.backdrop}>
      <div style={s.modal}>
        <header style={s.header}>
          <div>
            <h2 style={s.title}>Restaurant Menu Scribe & Carryout Orders</h2>
            <p style={s.subtitle}>Record a restaurant owner conversation, extract profile/menu data, approve it, then compare menus across restaurants.</p>
          </div>
          <button style={s.close} onClick={onClose}>×</button>
        </header>

        <nav style={s.tabs}>
          {tabs.map(([key, label]) => (
            <button key={key} style={{...s.tab, ...(tab===key?s.tabActive:{})}} onClick={() => { setError(''); setTab(key); }}>{label}</button>
          ))}
        </nav>

        {error && <div style={s.error}>{error}</div>}

        {tab === 'scribe' && (
          <section style={s.body}>
            <div style={s.status}>{statusText}</div>
            <div style={s.grid2}>
              <label style={s.field}>Intake title<input value={title} onChange={e=>setTitle(e.target.value)} placeholder="Optional restaurant/menu title" /></label>
              <label style={s.field}>Spoken language<select value={language} onChange={e=>setLanguage(e.target.value)}>{LANGUAGES.map(l => <option key={l.value} value={l.value}>{l.label}</option>)}</select></label>
            </div>
            <label style={s.check}><input type="checkbox" checked={authorized} onChange={e=>setAuthorized(e.target.checked)} /> I am authorized to publish or update this restaurant menu.</label>
            <div style={s.actions}>
              {!recording ? <button style={s.primary} onClick={startRecording}>🎙 Record</button> : <button style={s.danger} onClick={stopRecording}>■ Stop</button>}
              <label style={s.secondary}>Upload audio<input type="file" accept="audio/*" hidden onChange={e => { const f=e.target.files?.[0]; if (f) { setAudioBlob(f); setAudioSegments([{ blob:f, filename:f.name }]); setAudioName(f.name); } }} /></label>
              <button style={s.primary} disabled={busy || !audioBlob} onClick={runWorkflow}>{busy ? 'Running...' : 'Run agent workflow'}</button>
              {audioName && <span style={s.fileName}>{audioName}</span>}
              {audioSegments.length > 1 && <span style={s.fileName}>{audioSegments.length} recording segments</span>}
            </div>

            {steps.length > 0 && <AgentSteps steps={steps} />}
            {packet && (
              <div style={s.review}>
                <TranscriptBox transcript={packet?.conversation_transcript?.transcript_text || ''} meta={packet?.conversation_transcript || {}} />
                <h3 style={s.h3}>Review And Approve</h3>
                <div style={s.grid2}>
                  {['name','cuisine_type','address','phone','email','website'].map(key => (
                    <label key={key} style={s.field}>
                      {labelize(key)}{key === 'email' ? ' *' : ''}
                      <input
                        type={key === 'email' ? 'email' : 'text'}
                        required={key === 'email'}
                        value={profile[key] || ''}
                        onChange={e=>updateProfile(key, e.target.value)}
                      />
                    </label>
                  ))}
                </div>
                <label style={s.field}>Description<textarea rows={3} value={profile.description || ''} onChange={e=>updateProfile('description', e.target.value)} /></label>
                <div style={s.tableWrap}>
                  <table style={s.table}>
                    <thead><tr><th>Category</th><th>Item</th><th>Price</th><th>Qty</th><th>Description</th><th></th></tr></thead>
                    <tbody>
                      {menuItems.map((item, i) => (
                        <tr key={i}>
                          <td><input value={item.category || ''} onChange={e=>updateMenu(i,'category',e.target.value)} /></td>
                          <td><input value={item.item_name || ''} onChange={e=>updateMenu(i,'item_name',e.target.value)} /></td>
                          <td><input value={item.price ?? ''} onChange={e=>updateMenu(i,'price',e.target.value)} /></td>
                          <td><input value={item.quantity || ''} onChange={e=>updateMenu(i,'quantity',e.target.value)} /></td>
                          <td><input value={item.description || ''} onChange={e=>updateMenu(i,'description',e.target.value)} /></td>
                          <td><button style={s.iconBtn} onClick={()=>removeMenuItem(i)}>×</button></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div style={s.actions}>
                  <button style={s.secondaryBtn} onClick={addMenuItem}>+ Menu item</button>
                  <button style={s.primary} disabled={!canApprove} onClick={approvePacket}>Approve and save restaurant</button>
                  {!profile.email && <span style={s.muted}>Restaurant email is required before saving.</span>}
                </div>
              </div>
            )}
          </section>
        )}

        {tab === 'restaurants' && (
          <section style={s.body}>
            <div style={s.actions}><button style={s.secondaryBtn} onClick={loadRestaurants}>Refresh</button></div>
            <div style={s.restaurantGrid}>
              <div style={s.list}>
                {restaurants.map(r => (
                  <button key={r.id} style={{...s.restaurantRow, ...(selected===r.id?s.selected:{})}} onClick={()=>openRestaurant(r.id)}>
                    <strong>{r.name}</strong>
                    <span>{r.cuisine_type || 'Cuisine not set'} · {r.menu_count || 0} items · {r.workspace_id ? 'Workspace' : 'Personal'}</span>
                    <RatingBadge item={r} />
                  </button>
                ))}
                {!restaurants.length && <p style={s.muted}>No approved restaurants yet.</p>}
              </div>
              <div style={s.detail}>
                {selectedDetail ? (
                  editingSaved ? (
                    <SavedRestaurantEditor
                      draft={savedDraft}
                      busy={busy}
                      onProfile={updateSavedProfile}
                      onMenu={updateSavedMenu}
                      onAdd={addSavedMenuItem}
                      onRemove={removeSavedMenuItem}
                      onCancel={() => { setEditingSaved(false); setSavedDraft(null); }}
                      onSave={saveRestaurantEdits}
                    />
                  ) : (
                    <RestaurantDetail data={selectedDetail} onEdit={startSavedEdit} onDelete={removeSavedRestaurant} busy={busy} />
                  )
                ) : <p style={s.muted}>Select a restaurant to view menu details.</p>}
              </div>
            </div>
          </section>
        )}

        {tab === 'compare' && (
          <section style={s.body}>
            <div style={s.searchBar}>
              <input value={searchQuery} onChange={e=>setSearchQuery(e.target.value)} placeholder="Search menu, restaurant, description" />
              <button style={s.secondaryBtn} onClick={runSearch}>Search</button>
              <input value={compareQuery} onChange={e=>setCompareQuery(e.target.value)} placeholder="Compare item, e.g. chicken biryani" />
              <button style={s.primary} onClick={runCompare}>Compare prices</button>
              <button style={s.secondaryBtn} onClick={runRecommend}>Recommend</button>
            </div>
            <div style={s.orderLayout}>
              <MenuTable items={recommendRows.length ? recommendRows : compareRows.length ? compareRows : searchRows} onAdd={addCartItem} onFeedback={startFeedback} />
              <CarryoutCart
                cart={cart}
                customer={customer}
                draftOrder={draftOrder}
                busy={busy}
                onCustomer={setCustomer}
                onUpdateItem={updateCartItem}
                onRemoveItem={removeCartItem}
                onDraft={createOrderDraft}
                onSubmit={placeOrder}
              />
            </div>
          </section>
        )}

        {tab === 'orders' && (
          <section style={s.body}>
            <div style={s.detailHead}>
              <h3 style={s.h3}>{canProcessRestaurantOrders ? 'Carryout Order Processing' : 'My Carryout Orders'}</h3>
              <div style={s.actionsTight}>
                {canProcessRestaurantOrders && (
                  <select value={orderStatusFilter} onChange={e=>setOrderStatusFilter(e.target.value)}>
                    <option value="">All owner orders</option>
                    <option value="submitted">Submitted</option>
                    <option value="accepted">Accepted</option>
                    <option value="ready_for_pickup">Ready</option>
                    <option value="completed">Completed</option>
                    <option value="rejected">Rejected</option>
                  </select>
                )}
                <button style={s.secondaryBtn} onClick={loadOrders}>Refresh</button>
              </div>
            </div>
            <div style={s.orderGrid}>
              {canProcessRestaurantOrders && <OrderList title="Restaurant owner queue" orders={ownerOrders} owner onAction={ownerAction} busy={busy} />}
              <OrderList title="My carryout orders" orders={myOrders} onFeedback={startFeedback} />
            </div>
          </section>
        )}

        {tab === 'feedback' && (
          <section style={s.body}>
            <div style={s.grid2}>
              <FeedbackForm draft={feedbackDraft} setDraft={setFeedbackDraft} busy={busy} language={language} onSubmit={submitFeedbackDraft} />
              <FeedbackList
                title={canProcessRestaurantOrders && ownerFeedback.length ? 'Restaurant feedback queue' : 'My feedback'}
                feedback={canProcessRestaurantOrders && ownerFeedback.length ? ownerFeedback : myFeedback}
                owner={canProcessRestaurantOrders && ownerFeedback.length > 0}
                busy={busy}
                onRespond={respondFeedback}
              />
            </div>
          </section>
        )}
      </div>
    </div>
  );
}

function AgentSteps({ steps }) {
  return (
    <div style={s.steps}>
      <h3 style={s.h3}>Agent Steps</h3>
      {steps.map((step, i) => (
        <div key={`${step.agent_name}-${i}`} style={s.step}>
          <span style={s.stepStatus}>{step.status}</span>
          <strong>{step.agent_name}</strong>
          <p>{step.input_summary}</p>
        </div>
      ))}
    </div>
  );
}

function RestaurantDetail({ data, onEdit, onDelete, busy }) {
  const r = data.restaurant || {};
  const menuItems = data.menu_items || [];
  const canManage = !!r.can_manage;
  return (
    <>
      <div style={s.detailHead}>
        <h3 style={s.h3}>{r.name}</h3>
        {canManage && (
          <div style={s.actionsTight}>
            <button style={s.secondaryBtn} onClick={onEdit}>Edit</button>
            <button style={s.dangerSmall} disabled={busy} onClick={onDelete}>Delete</button>
          </div>
        )}
      </div>
      <p style={s.muted}>{r.cuisine_type} · {r.address} · {r.workspace_id ? 'Workspace menu' : 'Personal menu'}</p>
      <RatingBadge item={r} />
      <p>{r.description}</p>
      <TranscriptBox transcript={data.transcript || ''} />
      {!menuItems.length && (
        <div style={s.warnBox}>
          This restaurant exists, but no menu item rows are saved for it. Use Refresh to restore older approved scribe data, or edit the restaurant and add menu items.
        </div>
      )}
      <MenuTable items={menuItems} restaurantName={r.name || ''} />
    </>
  );
}

function TranscriptBox({ transcript, meta = {} }) {
  if (!transcript) return null;
  return (
    <details style={s.transcriptBox} open>
      <summary style={s.transcriptSummary}>Full transcript</summary>
      {(meta.transcript_chars || meta.transcript_window_count || meta.finish_reason) && (
        <div style={s.transcriptMeta}>
          {meta.audio_segment_count ? <span>{meta.audio_segment_count} audio segment{meta.audio_segment_count === 1 ? '' : 's'}</span> : null}
          {meta.transcript_chars ? <span>{meta.transcript_chars} chars</span> : null}
          {meta.transcript_window_count ? <span>{meta.transcript_window_count} extraction window{meta.transcript_window_count === 1 ? '' : 's'}</span> : null}
          {meta.finish_reason ? <span>finish: {meta.finish_reason}</span> : null}
        </div>
      )}
      <pre style={s.transcriptText}>{transcript}</pre>
    </details>
  );
}

function SavedRestaurantEditor({ draft, busy, onProfile, onMenu, onAdd, onRemove, onCancel, onSave }) {
  const profile = draft?.restaurant_profile || {};
  const items = draft?.menu_items || [];
  return (
    <>
      <div style={s.detailHead}>
        <h3 style={s.h3}>Edit Saved Menu</h3>
        <div style={s.actionsTight}>
          <button style={s.secondaryBtn} onClick={onCancel}>Cancel</button>
          <button style={s.primary} disabled={busy || !profile.name || !profile.email} onClick={onSave}>{busy ? 'Saving...' : 'Save changes'}</button>
        </div>
      </div>
      <div style={s.grid2}>
        {['name','cuisine_type','address','phone','email','website'].map(key => (
          <label key={key} style={s.field}>
            {labelize(key)}{key === 'email' ? ' *' : ''}
            <input
              type={key === 'email' ? 'email' : 'text'}
              required={key === 'email'}
              value={profile[key] || ''}
              onChange={e=>onProfile(key, e.target.value)}
            />
          </label>
        ))}
      </div>
      {!profile.email && <p style={s.muted}>Restaurant email is required before saving.</p>}
      <label style={s.field}>Description<textarea rows={3} value={profile.description || ''} onChange={e=>onProfile('description', e.target.value)} /></label>
      <div style={s.tableWrap}>
        <table style={s.table}>
          <thead><tr><th>Category</th><th>Item</th><th>Price</th><th>Qty</th><th>Description</th><th></th></tr></thead>
          <tbody>
            {items.map((item, i) => (
              <tr key={item.id || i}>
                <td><input value={item.category || ''} onChange={e=>onMenu(i,'category',e.target.value)} /></td>
                <td><input value={item.item_name || ''} onChange={e=>onMenu(i,'item_name',e.target.value)} /></td>
                <td><input value={item.price ?? ''} onChange={e=>onMenu(i,'price',e.target.value)} /></td>
                <td><input value={item.quantity || ''} onChange={e=>onMenu(i,'quantity',e.target.value)} /></td>
                <td><input value={item.description || ''} onChange={e=>onMenu(i,'description',e.target.value)} /></td>
                <td><button style={s.iconBtn} onClick={()=>onRemove(i)}>×</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={s.actions}><button style={s.secondaryBtn} onClick={onAdd}>+ Menu item</button></div>
    </>
  );
}

function CarryoutCart({ cart, customer, draftOrder, busy, onCustomer, onUpdateItem, onRemoveItem, onDraft, onSubmit }) {
  const subtotal = cart.items.reduce((sum, item) => sum + (Number(item.price || 0) * Number(item.quantity_ordered || 1)), 0);
  const order = draftOrder?.order;
  return (
    <aside style={s.cart}>
      <h3 style={s.h3}>Carryout Cart</h3>
      <p style={s.muted}>{cart.restaurant_name || 'Add items from one restaurant to start an order.'}</p>
      <div style={s.cartItems}>
        {cart.items.map(item => (
          <div key={item.id} style={s.cartItem}>
            <div>
              <strong>{item.item_name}</strong>
              <p style={s.muted}>{item.price != null ? `$${Number(item.price).toFixed(2)}` : 'Price not set'}</p>
            </div>
            <input style={s.qtyInput} type="number" min="1" value={item.quantity_ordered || 1} onChange={e=>onUpdateItem(item.id, 'quantity_ordered', e.target.value)} />
            <button style={s.iconBtn} onClick={()=>onRemoveItem(item.id)}>×</button>
            <input style={s.fullInput} value={item.instructions || ''} onChange={e=>onUpdateItem(item.id, 'instructions', e.target.value)} placeholder="Item notes" />
          </div>
        ))}
        {!cart.items.length && <p style={s.empty}>No items in cart.</p>}
      </div>
      <div style={s.cartTotal}><span>Estimated subtotal</span><strong>${subtotal.toFixed(2)}</strong></div>
      <div style={s.grid1}>
        <label style={s.field}>Customer name<input value={customer.name} onChange={e=>onCustomer({ ...customer, name:e.target.value })} /></label>
        <label style={s.field}>Phone<input value={customer.phone} onChange={e=>onCustomer({ ...customer, phone:e.target.value })} /></label>
        <label style={s.field}>Email<input value={customer.email} onChange={e=>onCustomer({ ...customer, email:e.target.value })} /></label>
        <label style={s.field}>Pickup time request<input value={customer.pickup_time_request} onChange={e=>onCustomer({ ...customer, pickup_time_request:e.target.value })} placeholder="ASAP or 6:30 PM" /></label>
        <label style={s.field}>Order notes<textarea rows={2} value={customer.special_instructions} onChange={e=>onCustomer({ ...customer, special_instructions:e.target.value })} /></label>
      </div>
      {order && (
        <div style={s.status}>
          Order draft ready: {order.restaurant_name} · ${Number(order.subtotal || 0).toFixed(2)} · {order.status}
          {order.payment_status ? ` · payment ${order.payment_status}` : ''}
        </div>
      )}
      <div style={s.actions}>
        <button style={s.secondaryBtn} disabled={busy || !cart.items.length} onClick={onDraft}>{busy ? 'Working...' : 'Review order'}</button>
        <button style={s.primary} disabled={busy || !order || !['draft','payment_pending'].includes(order.status)} onClick={onSubmit}>Pay & Place Order</button>
      </div>
      <p style={s.muted}>Carryout only. Payment is collected before the restaurant receives the order.</p>
    </aside>
  );
}

function OrderList({ title, orders, owner = false, onAction, busy, onFeedback = null }) {
  return (
    <div style={s.orderList}>
      <h3 style={s.h3}>{title}</h3>
      {orders.map(order => (
        <div key={order.id} style={s.orderCard}>
          <div style={s.detailHead}>
            <div>
              <strong>{order.restaurant_name}</strong>
              <p style={s.muted}>{order.item_count || 0} item{order.item_count === 1 ? '' : 's'} · ${Number(order.subtotal || 0).toFixed(2)} · {order.fulfillment_type}</p>
            </div>
            <span style={s.statusPill}>{order.status}</span>
          </div>
          <p style={s.muted}>Customer: {order.customer_name || 'Not provided'} {order.customer_phone ? `· ${order.customer_phone}` : ''}</p>
          <p style={s.muted}>Customer email: {order.customer_email || 'Not provided'}</p>
          {order.pickup_time_request && <p style={s.muted}>Pickup: {order.pickup_time_request}</p>}
          <OrderItems items={order.order_items} />
          {!owner && onFeedback && ['submitted','accepted','ready_for_pickup','completed'].includes(order.status) && (
            <div style={s.actionsTight}>
              {(order.order_items || []).map(item => (
                <button
                  key={item.id || item.menu_item_id || item.item_name}
                  style={s.secondaryBtn}
                  onClick={() => onFeedback({
                    restaurant_id: order.restaurant_id,
                    restaurant_name: order.restaurant_name,
                    menu_item_id: item.menu_item_id,
                    menu_item_name: item.item_name,
                    order_id: order.id,
                  })}
                >
                  ⭐ Feedback: {item.item_name}
                </button>
              ))}
            </div>
          )}
          {owner && (
            <div style={s.actionsTight}>
              {order.status === 'submitted' && <button style={s.primary} disabled={busy} onClick={()=>onAction(order.id, 'accept')}>Accept & confirm</button>}
              {order.status === 'submitted' && <button style={s.dangerSmall} disabled={busy} onClick={()=>onAction(order.id, 'reject')}>Reject</button>}
              {order.status === 'accepted' && <button style={s.primary} disabled={busy} onClick={()=>onAction(order.id, 'ready')}>Mark ready</button>}
              {order.status === 'ready_for_pickup' && <button style={s.secondaryBtn} disabled={busy} onClick={()=>onAction(order.id, 'complete')}>Complete</button>}
            </div>
          )}
        </div>
      ))}
      {!orders.length && <p style={s.empty}>No orders to show.</p>}
    </div>
  );
}

function OrderItems({ items }) {
  const rows = Array.isArray(items) ? items : [];
  if (!rows.length) return <p style={s.muted}>No item details available.</p>;
  return (
    <div style={s.orderItems}>
      {rows.map((item, i) => {
        const qty = Number(item.quantity || 1);
        const unit = item.unit_price == null ? null : Number(item.unit_price);
        const line = item.line_total == null ? (unit == null ? null : unit * qty) : Number(item.line_total);
        return (
          <div key={item.id || `${item.item_name}-${i}`} style={s.orderItem}>
            <div>
              <strong>{qty} × {item.item_name || 'Menu item'}</strong>
              {item.category && <span style={s.micro}> {item.category}</span>}
              {item.instructions && <div style={s.micro}>Notes: {item.instructions}</div>}
            </div>
            <div style={s.orderItemPrice}>
              {unit != null && <span>${unit.toFixed(2)} each</span>}
              {line != null && <strong>${line.toFixed(2)}</strong>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function MenuTable({ items, onAdd = null, onFeedback = null, restaurantName = '' }) {
  return (
    <div style={s.tableWrap}>
      <table style={s.table}>
        <thead><tr><th>Restaurant</th><th>Item</th><th>Rating</th><th>Category</th><th>Price</th><th>Qty</th><th>Description</th>{(onAdd || onFeedback) && <th>Action</th>}</tr></thead>
        <tbody>
          {items.map((item, i) => (
            <tr key={item.id || i}>
              <td>
                <strong>{item.restaurant_name || restaurantName || ''}</strong>
                {item.restaurant_id && <div style={s.micro}>Restaurant ID: {item.restaurant_id}</div>}
                {item.restaurant_email && <div style={s.micro}>Email: {item.restaurant_email}</div>}
                {item.restaurant_phone && <div style={s.micro}>Phone: {item.restaurant_phone}</div>}
                {(item.restaurant_address || item.address) && <div style={s.micro}>Address: {item.restaurant_address || item.address}</div>}
              </td>
              <td>
                {item.item_name}
                {item.id && <div style={s.micro}>Menu item ID: {item.id}</div>}
              </td>
              <td><RatingBadge item={item} /></td>
              <td>{item.category}</td>
              <td>{item.price != null ? `$${Number(item.price).toFixed(2)}` : ''}</td>
              <td>{item.quantity}</td>
              <td>
                {item.description}
                {item.recommendation_score != null && (
                  <div style={s.micro}>Recommended: {item.recommendation_score}/100 · {item.recommendation_reason}</div>
                )}
              </td>
              {(onAdd || onFeedback) && (
                <td>
                  <div style={s.actionsTight}>
                    {onAdd && <button style={s.secondaryBtn} onClick={()=>onAdd(item)}>Add</button>}
                    {onFeedback && <button style={s.secondaryBtn} onClick={()=>onFeedback(item)}>Feedback</button>}
                  </div>
                </td>
              )}
            </tr>
          ))}
          {!items.length && <tr><td colSpan={(onAdd || onFeedback) ? 8 : 7} style={s.empty}>No menu items to show.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

function FeedbackForm({ draft, setDraft, busy, language = 'en-US', onSubmit }) {
  const [recordingFeedback, setRecordingFeedback] = useState(false);
  const [transcribingFeedback, setTranscribingFeedback] = useState(false);
  const [analyzingFeedback, setAnalyzingFeedback] = useState(false);
  const [voiceError, setVoiceError] = useState('');
  const recorderRef = useRef(null);
  const voiceChunksRef = useRef([]);
  const voiceStreamRef = useRef(null);

  async function transcribeFeedbackAudio(blob, filename = 'restaurant-feedback.webm') {
    if (!blob) return;
    setVoiceError('');
    setTranscribingFeedback(true);
    try {
      const file = blob instanceof File ? blob : new File([blob], filename, { type: blob.type || 'audio/webm' });
      const result = await transcribeVoice(file, draft?.language || language);
      const text = (result?.text || '').trim();
      if (!text) {
        setVoiceError('No speech was detected. Please try again or type feedback manually.');
        return;
      }
      setDraft(current => ({
        ...current,
        feedback_text: current?.feedback_text ? `${current.feedback_text}\n${text}` : text,
        source_type: 'voice',
        language: current?.language || language,
        voice_metadata: {
          ...(current?.voice_metadata || {}),
          trace_id: result?.trace_id || '',
          audio_type: file.type || blob.type || 'audio/webm',
          audio_bytes: file.size || blob.size || 0,
        },
      }));
      await suggestFeedbackRating(text);
    } catch (e) {
      setVoiceError(e.message || 'Could not transcribe voice feedback.');
    } finally {
      setTranscribingFeedback(false);
    }
  }

  async function startVoiceFeedback() {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setVoiceError('Voice recording is not supported in this browser. You can upload an audio file or type feedback.');
      return;
    }
    setVoiceError('');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      voiceStreamRef.current = stream;
      voiceChunksRef.current = [];
      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : '';
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      recorderRef.current = recorder;
      recorder.ondataavailable = event => {
        if (event.data?.size) voiceChunksRef.current.push(event.data);
      };
      recorder.onstop = async () => {
        const type = recorder.mimeType || 'audio/webm';
        const blob = new Blob(voiceChunksRef.current, { type });
        voiceStreamRef.current?.getTracks().forEach(track => track.stop());
        voiceStreamRef.current = null;
        if (blob.size) await transcribeFeedbackAudio(blob);
      };
      recorder.start();
      setRecordingFeedback(true);
    } catch (e) {
      setVoiceError(e.message || 'Microphone permission was denied or recording failed.');
    }
  }

  function stopVoiceFeedback() {
    setRecordingFeedback(false);
    if (recorderRef.current && recorderRef.current.state !== 'inactive') {
      recorderRef.current.stop();
    } else {
      voiceStreamRef.current?.getTracks().forEach(track => track.stop());
      voiceStreamRef.current = null;
    }
  }

  async function suggestFeedbackRating(text = draft?.feedback_text || '') {
    const feedbackText = (text || '').trim();
    if (!feedbackText) {
      setVoiceError('Type, record, or upload feedback before asking for a suggested rating.');
      return;
    }
    setVoiceError('');
    setAnalyzingFeedback(true);
    try {
      const result = await analyzeRestaurantFeedback({
        feedbackText,
        language: draft?.language || language,
        currentRating: draft?.rating || 5,
        restaurantName: draft?.restaurant_name || '',
        menuItemName: draft?.menu_item_name || '',
      });
      const analysis = result?.analysis || {};
      const suggestedTags = Array.isArray(analysis.tags) ? analysis.tags.map(tag => String(tag).replaceAll('_', ' ')) : [];
      setDraft(current => ({
        ...current,
        rating: Number(analysis.suggested_rating || current?.rating || 5),
        tags: Array.from(new Set([...(current?.tags || []), ...suggestedTags])),
        sentiment_analysis: analysis,
      }));
    } catch (e) {
      setVoiceError(e.message || 'Could not suggest rating from feedback.');
    } finally {
      setAnalyzingFeedback(false);
    }
  }

  if (!draft) {
    return (
      <div style={s.review}>
        <h3 style={s.h3}>Customer Feedback</h3>
        <p style={s.muted}>Select Feedback from a menu row or carryout order item. Customers can type, record, or upload feedback. Ratings appear in restaurant lists, menu search, price comparisons, and recommendations.</p>
      </div>
    );
  }
  const tagOptions = ['taste', 'value', 'portion', 'freshness', 'spice', 'packaging', 'wait time', 'accuracy'];
  const toggleTag = tag => {
    const tags = draft.tags || [];
    setDraft({ ...draft, tags: tags.includes(tag) ? tags.filter(t => t !== tag) : [...tags, tag] });
  };
  return (
    <div style={s.review}>
      <h3 style={s.h3}>Give Feedback</h3>
      <p style={s.muted}>{draft.restaurant_name || 'Restaurant'} {draft.menu_item_name ? `· ${draft.menu_item_name}` : ''}</p>
      <label style={s.field}>Rating
        <select value={draft.rating} onChange={e=>setDraft({ ...draft, rating:Number(e.target.value) })}>
          {[5,4,3,2,1].map(v => <option key={v} value={v}>{'⭐'.repeat(v)} {v}/5</option>)}
        </select>
      </label>
      <label style={s.field}>Feedback
        <textarea rows={5} value={draft.feedback_text} onChange={e=>setDraft({ ...draft, feedback_text:e.target.value })} placeholder="What should future customers or the restaurant know?" />
      </label>
      <div style={s.sentimentBox}>
        <div>
          <strong>Semantic rating suggestion</strong>
          <p style={s.micro}>DocIntel can suggest a rating and tags from sentiment. You can change everything before submit.</p>
          {draft.sentiment_analysis && (
            <p style={s.micro}>
              Suggested: {draft.sentiment_analysis.suggested_rating}/5 · {draft.sentiment_analysis.overall_sentiment}
              {draft.sentiment_analysis.reason ? ` · ${draft.sentiment_analysis.reason}` : ''}
            </p>
          )}
        </div>
        <button type="button" style={s.secondaryBtn} disabled={busy || analyzingFeedback} onClick={()=>suggestFeedbackRating()}>
          {analyzingFeedback ? 'Analyzing...' : 'Suggest rating'}
        </button>
      </div>
      <div style={s.voiceFeedbackBox}>
        <div>
          <strong>Voice feedback</strong>
          <p style={s.micro}>Record or upload a short customer comment. The transcript is editable before submit.</p>
        </div>
        <div style={s.actionsTight}>
          {!recordingFeedback
            ? <button type="button" style={s.secondaryBtn} disabled={busy || transcribingFeedback} onClick={startVoiceFeedback}>🎙 Record</button>
            : <button type="button" style={s.dangerSmall} onClick={stopVoiceFeedback}>■ Stop</button>}
          <label style={s.secondaryBtn}>
            Upload audio
            <input
              type="file"
              accept="audio/*"
              hidden
              disabled={busy || transcribingFeedback}
              onChange={e => {
                const file = e.target.files?.[0];
                if (file) transcribeFeedbackAudio(file, file.name);
                e.target.value = '';
              }}
            />
          </label>
          {transcribingFeedback && <span style={s.micro}>Transcribing...</span>}
        </div>
        {draft.source_type === 'voice' && <p style={s.micro}>Voice transcript ready. Please review before submitting.</p>}
        {voiceError && <div style={s.inlineError}>{voiceError}</div>}
      </div>
      <div style={s.tagRow}>
        {tagOptions.map(tag => (
          <button key={tag} style={{...s.tagBtn, ...((draft.tags || []).includes(tag) ? s.tagBtnOn : {})}} onClick={()=>toggleTag(tag)} type="button">{tag}</button>
        ))}
      </div>
      <div style={s.actions}>
        <button style={s.primary} disabled={busy} onClick={onSubmit}>Submit feedback</button>
        <button style={s.secondaryBtn} disabled={busy} onClick={()=>setDraft(null)}>Cancel</button>
      </div>
    </div>
  );
}

function FeedbackList({ title, feedback, owner = false, busy, onRespond }) {
  const rows = Array.isArray(feedback) ? feedback : [];
  return (
    <div style={s.orderList}>
      <h3 style={s.h3}>{title}</h3>
      {rows.map(row => (
        <div key={row.id} style={s.orderCard}>
          <div style={s.detailHead}>
            <div>
              <strong>{row.restaurant_name}</strong>
              <p style={s.muted}>{row.menu_item_name || 'Restaurant feedback'} · {row.verified_order ? 'Verified order' : 'Unverified'} · {row.status}</p>
            </div>
            <span style={s.ratingPill}>{'⭐'.repeat(Number(row.rating || 0))} {row.rating}/5</span>
          </div>
          {row.feedback_text && <p>{row.feedback_text}</p>}
          {!!(row.tags || []).length && <p style={s.muted}>Tags: {(row.tags || []).join(', ')}</p>}
          {row.owner_response && <p style={s.status}>Restaurant response: {row.owner_response}</p>}
          {owner && (
            <div style={s.actionsTight}>
              <button style={s.secondaryBtn} disabled={busy} onClick={()=>onRespond(row.id, 'acknowledged')}>Acknowledge</button>
              <button style={s.primary} disabled={busy} onClick={()=>onRespond(row.id, 'responded')}>Respond</button>
              <button style={s.secondaryBtn} disabled={busy} onClick={()=>onRespond(row.id, 'resolved')}>Resolve</button>
            </div>
          )}
        </div>
      ))}
      {!rows.length && <p style={s.empty}>No feedback yet.</p>}
    </div>
  );
}

function RatingBadge({ item = {} }) {
  const avg = item.avg_rating == null ? null : Number(item.avg_rating);
  const count = Number(item.rating_count || 0);
  if (!count || !Number.isFinite(avg)) return <span style={s.micro}>No ratings yet</span>;
  return (
    <span style={s.ratingInline}>
      ⭐ {avg.toFixed(1)} · {count} rating{count === 1 ? '' : 's'}
      {Number(item.verified_rating_count || 0) > 0 ? ` · ${item.verified_rating_count} verified` : ''}
    </span>
  );
}

function normalizePacket(packet) {
  const p = packet || {};
  return {
    ...p,
    restaurant_profile: p.restaurant_profile || {},
    menu_items: Array.isArray(p.menu_items) ? p.menu_items : [],
  };
}

function parsePrice(value) {
  if (value === '') return null;
  const n = Number(String(value).replace('$','').replace(',',''));
  return Number.isFinite(n) ? n : null;
}

function labelize(key) {
  return key.replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase());
}

const s = {
  backdrop:{ position:'fixed', inset:0, background:'rgba(0,0,0,.65)', zIndex:1000, display:'flex', alignItems:'center', justifyContent:'center', padding:20 },
  modal:{ width:'min(1180px, 96vw)', height:'min(880px, 92vh)', background:'#0b1711', color:'var(--tx)', border:'1px solid rgba(74,222,128,.22)', borderRadius:10, boxShadow:'0 22px 70px rgba(0,0,0,.55)', display:'flex', flexDirection:'column', overflow:'hidden' },
  header:{ display:'flex', justifyContent:'space-between', gap:16, padding:'18px 20px', borderBottom:'1px solid rgba(74,222,128,.14)' },
  title:{ margin:0, fontSize:20 },
  subtitle:{ margin:'5px 0 0', color:'var(--muted2)', fontSize:13 },
  close:{ background:'transparent', color:'var(--muted2)', border:'1px solid var(--b2)', borderRadius:7, width:34, height:34, cursor:'pointer', fontSize:22 },
  tabs:{ display:'flex', gap:6, padding:'10px 16px', borderBottom:'1px solid rgba(74,222,128,.1)' },
  tab:{ border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx2)', borderRadius:7, padding:'8px 12px', cursor:'pointer', fontWeight:700 },
  tabActive:{ background:'rgba(74,222,128,.14)', color:'#4ade80', borderColor:'rgba(74,222,128,.35)' },
  body:{ padding:16, overflow:'auto', flex:1 },
  status:{ padding:'10px 12px', border:'1px solid rgba(74,222,128,.2)', background:'rgba(74,222,128,.08)', color:'#86efac', borderRadius:7, marginBottom:14, fontSize:13, fontWeight:700 },
  grid2:{ display:'grid', gridTemplateColumns:'repeat(2, minmax(0,1fr))', gap:12 },
  grid1:{ display:'grid', gridTemplateColumns:'1fr', gap:10 },
  field:{ display:'flex', flexDirection:'column', gap:6, fontSize:12, fontWeight:700, color:'var(--tx2)' },
  check:{ display:'flex', alignItems:'center', gap:8, margin:'14px 0', color:'var(--tx2)', fontSize:13 },
  actions:{ display:'flex', alignItems:'center', gap:10, flexWrap:'wrap', margin:'12px 0' },
  primary:{ background:'#16a34a', color:'#06130a', border:'none', borderRadius:7, padding:'9px 13px', fontWeight:800, cursor:'pointer' },
  danger:{ background:'#ef4444', color:'#fff', border:'none', borderRadius:7, padding:'9px 13px', fontWeight:800, cursor:'pointer' },
  secondary:{ border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx)', borderRadius:7, padding:'8px 12px', cursor:'pointer', fontWeight:700 },
  secondaryBtn:{ border:'1px solid var(--b2)', background:'var(--s2)', color:'var(--tx)', borderRadius:7, padding:'9px 13px', cursor:'pointer', fontWeight:700 },
  fileName:{ color:'var(--muted2)', fontSize:12 },
  error:{ margin:'10px 16px 0', padding:'9px 11px', border:'1px solid rgba(248,113,113,.35)', background:'rgba(248,113,113,.12)', color:'#fecaca', borderRadius:7, fontSize:13 },
  warnBox:{ margin:'10px 0', padding:'9px 11px', border:'1px solid rgba(251,191,36,.28)', background:'rgba(251,191,36,.08)', color:'#fde68a', borderRadius:7, fontSize:13, lineHeight:1.45 },
  h3:{ margin:'0 0 10px', fontSize:15 },
  steps:{ marginTop:14, padding:12, border:'1px solid var(--b2)', borderRadius:8, background:'rgba(255,255,255,.02)' },
  step:{ display:'grid', gridTemplateColumns:'90px 1fr', gap:8, borderTop:'1px solid var(--b2)', padding:'8px 0', fontSize:12 },
  stepStatus:{ color:'#4ade80', fontWeight:800 },
  review:{ marginTop:14, padding:12, border:'1px solid rgba(74,222,128,.18)', borderRadius:8 },
  tableWrap:{ width:'100%', overflow:'auto', border:'1px solid var(--b2)', borderRadius:8, marginTop:10 },
  table:{ width:'100%', borderCollapse:'collapse', fontSize:12 },
  iconBtn:{ border:'1px solid var(--b2)', background:'transparent', color:'#fecaca', borderRadius:6, cursor:'pointer' },
  restaurantGrid:{ display:'grid', gridTemplateColumns:'320px 1fr', gap:14, minHeight:420 },
  list:{ border:'1px solid var(--b2)', borderRadius:8, overflow:'auto' },
  restaurantRow:{ display:'flex', flexDirection:'column', gap:3, width:'100%', padding:12, textAlign:'left', background:'transparent', border:'none', borderBottom:'1px solid var(--b2)', color:'var(--tx)', cursor:'pointer' },
  selected:{ background:'rgba(74,222,128,.1)' },
  detail:{ border:'1px solid var(--b2)', borderRadius:8, padding:14, overflow:'auto' },
  detailHead:{ display:'flex', alignItems:'center', justifyContent:'space-between', gap:12, marginBottom:10 },
  actionsTight:{ display:'flex', alignItems:'center', gap:8 },
  tagRow:{ display:'flex', gap:6, flexWrap:'wrap', margin:'10px 0' },
  tagBtn:{ border:'1px solid var(--b2)', background:'transparent', color:'var(--muted2)', borderRadius:20, padding:'5px 9px', cursor:'pointer', fontSize:12 },
  tagBtnOn:{ borderColor:'rgba(251,191,36,.45)', background:'rgba(251,191,36,.12)', color:'#fde68a' },
  ratingInline:{ display:'inline-flex', alignItems:'center', gap:4, border:'1px solid rgba(251,191,36,.22)', background:'rgba(251,191,36,.08)', color:'#fde68a', borderRadius:20, padding:'2px 7px', fontSize:11, fontWeight:800, width:'fit-content', marginTop:3 },
  sentimentBox:{ display:'flex', justifyContent:'space-between', alignItems:'center', gap:12, margin:'10px 0', padding:11, border:'1px solid rgba(251,191,36,.24)', background:'rgba(251,191,36,.07)', borderRadius:8 },
  voiceFeedbackBox:{ margin:'10px 0', padding:11, border:'1px solid rgba(96,165,250,.24)', background:'rgba(96,165,250,.07)', borderRadius:8 },
  inlineError:{ marginTop:8, padding:'8px 10px', border:'1px solid rgba(248,113,113,.32)', background:'rgba(248,113,113,.1)', color:'#fecaca', borderRadius:7, fontSize:12 },
  dangerSmall:{ background:'rgba(239,68,68,.14)', color:'#fecaca', border:'1px solid rgba(239,68,68,.35)', borderRadius:7, padding:'9px 13px', fontWeight:800, cursor:'pointer' },
  transcriptBox:{ border:'1px solid var(--b2)', borderRadius:8, margin:'12px 0', background:'rgba(255,255,255,.02)' },
  transcriptSummary:{ cursor:'pointer', padding:'10px 12px', color:'#86efac', fontWeight:800, fontSize:13 },
  transcriptMeta:{ display:'flex', flexWrap:'wrap', gap:8, padding:'0 12px 8px', color:'var(--muted2)', fontSize:11 },
  transcriptText:{ margin:0, padding:'0 12px 12px', maxHeight:260, overflow:'auto', whiteSpace:'pre-wrap', wordBreak:'break-word', color:'var(--tx2)', fontFamily:'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize:12, lineHeight:1.55 },
  muted:{ color:'var(--muted2)', fontSize:13 },
  micro:{ color:'var(--muted2)', fontSize:11, lineHeight:1.35, marginTop:3, wordBreak:'break-word' },
  searchBar:{ display:'grid', gridTemplateColumns:'1.2fr auto 1.2fr auto', gap:10, alignItems:'center', marginBottom:12 },
  empty:{ textAlign:'center', color:'var(--muted2)', padding:20 },
  orderLayout:{ display:'grid', gridTemplateColumns:'minmax(0,1fr) 340px', gap:14, alignItems:'start' },
  cart:{ border:'1px solid var(--b2)', borderRadius:8, background:'rgba(255,255,255,.02)', padding:12, position:'sticky', top:0 },
  cartItems:{ display:'flex', flexDirection:'column', gap:8, maxHeight:260, overflow:'auto', margin:'10px 0' },
  cartItem:{ display:'grid', gridTemplateColumns:'minmax(0,1fr) 58px 28px', gap:7, alignItems:'center', borderBottom:'1px solid var(--b2)', paddingBottom:8 },
  qtyInput:{ width:'100%', background:'var(--s2)', border:'1px solid var(--b2)', color:'var(--tx)', borderRadius:6, padding:'7px 6px' },
  fullInput:{ gridColumn:'1 / -1', background:'var(--s2)', border:'1px solid var(--b2)', color:'var(--tx)', borderRadius:6, padding:'8px' },
  cartTotal:{ display:'flex', justifyContent:'space-between', alignItems:'center', borderTop:'1px solid var(--b2)', borderBottom:'1px solid var(--b2)', padding:'9px 0', margin:'8px 0 12px', color:'var(--tx2)', fontSize:13 },
  orderGrid:{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:14 },
  orderList:{ border:'1px solid var(--b2)', borderRadius:8, padding:12, minHeight:300, overflow:'auto' },
  orderCard:{ border:'1px solid rgba(255,255,255,.08)', borderRadius:8, padding:11, background:'rgba(255,255,255,.025)', marginBottom:10 },
  orderItems:{ display:'flex', flexDirection:'column', gap:7, margin:'10px 0 12px', padding:'9px 10px', border:'1px solid var(--b2)', borderRadius:7, background:'rgba(255,255,255,.025)' },
  orderItem:{ display:'grid', gridTemplateColumns:'minmax(0,1fr) auto', gap:10, alignItems:'start', paddingBottom:7, borderBottom:'1px solid rgba(255,255,255,.06)' },
  orderItemPrice:{ display:'flex', flexDirection:'column', alignItems:'flex-end', gap:3, color:'var(--tx2)', fontSize:12, whiteSpace:'nowrap' },
  statusPill:{ display:'inline-flex', alignItems:'center', padding:'3px 8px', borderRadius:999, background:'rgba(74,222,128,.12)', color:'#86efac', fontSize:11, fontWeight:800 },
  ratingPill:{ display:'inline-flex', alignItems:'center', padding:'3px 8px', borderRadius:999, background:'rgba(251,191,36,.12)', color:'#fde68a', fontSize:11, fontWeight:800, whiteSpace:'nowrap' },
};
