/* Agent Office web UI — vanilla JS over REST + SSE. Depends on scene.js (window.Scene). */
(() => {
  const $ = (sel) => document.querySelector(sel);
  const api = {
    async json(url, opts = {}) {
      const res = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...opts });
      if (!res.ok) {
        let detail = res.statusText;
        try { detail = (await res.json()).detail || detail; } catch (_) {}
        throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
      }
      return res.status === 204 ? null : res.json();
    },
  };

  // ---------------------------------------------------------------- i18n
  const I18N = {
    id: {
      tagline: 'visualisasi multi-agent', new: '+ Baru', delete: 'Hapus sesi', workflow: 'Kantor', plan: 'Rencana',
      plan_extra: 'Kriteria penerimaan & asumsi', activity: 'Aktivitas', clear: 'bersihkan', chat_with: 'Chat dengan {name}',
      working: 'sedang bekerja…', cancel: 'batalkan', approval_text: 'Rencana menunggu keputusan Anda.',
      approve: 'Setujui & eksekusi', reject: 'Minta perubahan', upload_title: 'Unggah .pdf .docx .xlsx .txt', send: 'Kirim',
      placeholder: 'Ceritakan tugas untuk tim… (Enter kirim, Shift+Enter baris baru)',
      st_discussing: 'Diskusi', st_planning: 'Rencana', st_awaiting_approval: 'Persetujuan', st_executing: 'Eksekusi', st_done: 'Selesai',
      empty: 'Ceritakan kebutuhan Anda ke {name}. Ia akan memperjelas, menyusun rencana bersama supervisor, lalu para worker mengerjakannya. Unggah PDF/DOCX/XLSX/TXT lewat 📎.',
      empty_external: 'Sesi ini dikendalikan workflow eksternal. Pesan Anda diteruskan lewat event stream ke workflow tersebut.',
      you: 'Kamu', typing: '{name} sedang mengetik…', new_session: 'Sesi baru', external: 'eksternal',
      reject_prompt: 'Apa yang perlu diubah di rencana?', delete_confirm: 'Hapus sesi ini beserta dokumennya?',
      plan_draft: 'draf', plan_agreed: 'disepakati', plan_awaiting: 'menunggu persetujuan', plan_done: 'selesai',
      attempt: 'percobaan', step: 'Langkah', stage: 'Tahap', show_output: 'lihat output', doc_added: 'Dokumen ditambahkan', doc_removed: 'Dokumen dihapus',
      user_to_c: 'pesan', c_to_user: 'balasan', cancelled: 'Dibatalkan oleh pengguna', team_changed: 'Tim diperbarui', registered: 'bergabung',
      mock_banner: '⚠️ <b>Mode demo</b>: {list} memakai provider <code>mock</code> dan tidak benar-benar berpikir. Buka ⚙ Pengaturan tim untuk mengisi provider, model, dan API key tiap agent.',
      settings: 'Pengaturan tim', settings_hint: 'Satu client (bicara dengan Anda), satu supervisor (merencanakan & meninjau), dan berapa pun worker. Setiap agent punya provider, model, dan API key sendiri.',
      f_name: 'Nama', f_role: 'Peran', f_provider: 'Provider', f_model: 'Model', f_key: 'API key', f_key_hint: 'kosongkan = tetap pakai yang tersimpan', f_base: 'Base URL',
      f_skills: 'Keahlian', f_skills_hint: 'dipakai supervisor saat membagi tugas', f_persona: 'Persona', f_persona_hint: 'instruksi tambahan (opsional)',
      f_max: 'Max tokens', f_effort: 'Effort', f_temp: 'Temperature', f_color: 'Warna baju', f_hair: 'Warna rambut', f_clear: 'hapus API key',
      delete_agent: 'Hapus agent', add_agent: '+ Agent baru', save: 'Simpan', saved: 'Tersimpan', delete_agent_confirm: 'Hapus agent {name}?', key_set: 'key tersimpan', key_missing: 'tanpa key',
      status: { idle: 'idle', thinking: 'berpikir', planning: 'menyusun rencana', 'revising plan': 'merevisi rencana', 'reviewing plan': 'meninjau rencana',
        writing: 'menulis', 'writing delivery': 'menulis hasil', working: 'mengerjakan langkah', reviewing: 'meninjau langkah' },
      flow: { brief: 'brief', 'change request': 'minta ubah', 'draft plan': 'draf rencana', 'revise plan': 'revisi rencana', 'revised plan': 'rencana revisi',
        go: 'mulai', revise: 'revisi', result: 'hasil', 'all approved': 'semua disetujui', step: 'langkah' },
      roles: { client: 'Client', supervisor: 'Supervisor', worker: 'Worker' },
      verdict: { approve: 'disetujui', revise: 'revisi' },
      stages: { discussing: 'Diskusi', planning: 'Rencana', awaiting_approval: 'Persetujuan', executing: 'Eksekusi', done: 'Selesai' },
    },
    en: {
      tagline: 'multi-agent visualiser', new: '+ New', delete: 'Delete session', workflow: 'Office', plan: 'Plan',
      plan_extra: 'Acceptance criteria & assumptions', activity: 'Activity', clear: 'clear view', chat_with: 'Chat with {name}',
      working: 'working…', cancel: 'cancel', approval_text: 'The plan is waiting for your decision.',
      approve: 'Approve & execute', reject: 'Request changes', upload_title: 'Upload .pdf .docx .xlsx .txt', send: 'Send',
      placeholder: 'Describe the task for the team… (Enter to send, Shift+Enter for newline)',
      st_discussing: 'Discuss', st_planning: 'Plan', st_awaiting_approval: 'Approval', st_executing: 'Execute', st_done: 'Delivered',
      empty: 'Tell {name} what you need. They will clarify, plan it with the supervisor, and the workers will execute. Upload PDF/DOCX/XLSX/TXT with 📎.',
      empty_external: 'This session is driven by an external workflow. Your messages are relayed to it through the event stream.',
      you: 'You', typing: '{name} is typing…', new_session: 'New session', external: 'external',
      reject_prompt: 'What should change in the plan?', delete_confirm: 'Delete this session and its uploads?',
      plan_draft: 'draft', plan_agreed: 'agreed', plan_awaiting: 'awaiting approval', plan_done: 'completed',
      attempt: 'attempt', step: 'Step', stage: 'Stage', show_output: 'show output', doc_added: 'Document added', doc_removed: 'Document removed',
      user_to_c: 'message', c_to_user: 'reply', cancelled: 'Cancelled by user', team_changed: 'Team updated', registered: 'joined',
      mock_banner: '⚠️ <b>Demo mode</b>: {list} use the <code>mock</code> provider and do no real reasoning. Open ⚙ Team settings to set each agent\'s provider, model and API key.',
      settings: 'Team settings', settings_hint: 'One client (talks to you), one supervisor (plans & reviews), any number of workers. Every agent has its own provider, model and API key.',
      f_name: 'Name', f_role: 'Role', f_provider: 'Provider', f_model: 'Model', f_key: 'API key', f_key_hint: 'leave blank to keep the stored key', f_base: 'Base URL',
      f_skills: 'Skills', f_skills_hint: 'used by the supervisor when assigning steps', f_persona: 'Persona', f_persona_hint: 'extra instructions (optional)',
      f_max: 'Max tokens', f_effort: 'Effort', f_temp: 'Temperature', f_color: 'Shirt colour', f_hair: 'Hair colour', f_clear: 'clear API key',
      delete_agent: 'Delete agent', add_agent: '+ New agent', save: 'Save', saved: 'Saved', delete_agent_confirm: 'Delete agent {name}?', key_set: 'key stored', key_missing: 'no key',
      status: { idle: 'idle', thinking: 'thinking', planning: 'planning', 'revising plan': 'revising plan', 'reviewing plan': 'reviewing plan',
        writing: 'writing', 'writing delivery': 'writing delivery', working: 'working on step', reviewing: 'reviewing step' },
      flow: {}, roles: { client: 'Client', supervisor: 'Supervisor', worker: 'Worker' },
      verdict: { approve: 'approved', revise: 'revise' },
      stages: { discussing: 'Discuss', planning: 'Plan', awaiting_approval: 'Approval', executing: 'Execute', done: 'Delivered' },
    },
  };
  let lang = 'id';
  const t = (key) => I18N[lang][key] ?? I18N.en[key] ?? key;
  const fmt = (key, vars) => Object.entries(vars).reduce((s, [k, v]) => s.replace(`{${k}}`, v), t(key));

  function translateStatus(status) {
    if (!status || status === 'idle') return 'idle';
    const m = status.match(/^(working on step|reviewing step) (\d+)$/);
    if (m) return `${t('status')[m[1].split(' ')[0]]} ${m[2]}`;
    return t('status')[status] || status;
  }
  function translateFlow(label) {
    const m = (label || '').match(/^step (\d+)$/);
    if (m) return `${t('flow').step || 'step'} ${m[1]}`;
    return t('flow')[label] || label || '';
  }

  const state = { session: null, source: null, config: null, agents: [] };
  const agentOf = (id) => state.agents.find((a) => a.id === id);
  const nameOf = (id) => (id === 'user' ? t('you') : (agentOf(id)?.name || id));
  const clientAgent = () => state.agents.find((a) => a.role === 'client') || state.agents[0];

  function applyLanguage(next) {
    lang = next;
    document.documentElement.lang = lang;
    localStorage.setItem('office.lang', lang);
    $('#lang-select').value = lang;
    document.querySelectorAll('[data-i18n]').forEach((el) => { el.innerHTML = t(el.dataset.i18n); });
    document.querySelectorAll('[data-i18n-title]').forEach((el) => { el.title = t(el.dataset.i18nTitle); });
    document.querySelectorAll('[data-i18n-placeholder]').forEach((el) => { el.placeholder = t(el.dataset.i18nPlaceholder); });
    $('#send').textContent = t('send');
    if (state.session) renderSnapshot(state.session);
    renderSessionList(state.session?.id);
  }

  // ---------------------------------------------------------------- markdown
  const escapeHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  function inline(md) {
    return md
      .replace(/`([^`]+)`/g, (_, c) => `<code>${c}</code>`)
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
      .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  }
  function markdown(text) {
    const lines = escapeHtml(text || '').split('\n');
    const out = [];
    let i = 0, list = null, para = [];
    const flushPara = () => { if (para.length) { out.push(`<p>${inline(para.join('<br>'))}</p>`); para = []; } };
    const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };
    while (i < lines.length) {
      const line = lines[i];
      if (line.startsWith('```')) {
        flushPara(); closeList();
        const buf = []; i++;
        while (i < lines.length && !lines[i].startsWith('```')) buf.push(lines[i++]);
        out.push(`<pre><code>${buf.join('\n')}</code></pre>`); i++; continue;
      }
      const h = line.match(/^(#{1,4})\s+(.*)/);
      if (h) { flushPara(); closeList(); out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); i++; continue; }
      const ul = line.match(/^\s*[-*]\s+(.*)/), ol = line.match(/^\s*\d+[.)]\s+(.*)/);
      if (ul || ol) {
        flushPara();
        const kind = ul ? 'ul' : 'ol';
        if (list !== kind) { closeList(); out.push(`<${kind}>`); list = kind; }
        out.push(`<li>${inline((ul || ol)[1])}</li>`); i++; continue;
      }
      if (line.startsWith('&gt; ')) { flushPara(); closeList(); out.push(`<blockquote>${inline(line.slice(5))}</blockquote>`); i++; continue; }
      if (line.includes('|') && lines[i + 1] && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
        flushPara(); closeList();
        const cells = (l) => l.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map((c) => inline(c.trim()));
        const rows = [`<tr>${cells(line).map((c) => `<th>${c}</th>`).join('')}</tr>`]; i += 2;
        while (i < lines.length && lines[i].includes('|')) rows.push(`<tr>${cells(lines[i++]).map((c) => `<td>${c}</td>`).join('')}</tr>`);
        out.push(`<table>${rows.join('')}</table>`); continue;
      }
      if (line.trim() === '') { flushPara(); closeList(); i++; continue; }
      closeList(); para.push(line); i++;
    }
    flushPara(); closeList();
    return out.join('');
  }

  // ---------------------------------------------------------------- rendering
  const fmtTime = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

  function renderRoster(agents) {
    state.agents = agents || [];
    $('#agent-chips').innerHTML = state.agents.map((a) =>
      `<div class="chip ${a.configured === false ? 'unconfigured' : ''}" title="${escapeHtml(a.skills || '')}"><b><span class="swatch" style="background:${escapeHtml(a.color || '#888')}"></span>${escapeHtml(a.name)} · ${t('roles')[a.role] || a.role}</b><span>${escapeHtml(a.provider ? `${a.provider} · ${a.model}` : t('external'))}</span></div>`).join('');
    const mocked = state.agents.filter((a) => a.provider === 'mock').map((a) => a.name);
    $('#mock-banner').hidden = !mocked.length || state.session?.kind === 'external';
    $('#mock-banner').innerHTML = fmt('mock_banner', { list: mocked.join(', ') });
    const client = clientAgent();
    $('#chat-title').innerHTML = fmt('chat_with', { name: escapeHtml(client ? client.name : 'Agent') }) + (state.session?.kind === 'external' ? `<span class="kind-badge">${t('external')}</span>` : '');
    Scene.setRoster(state.agents);
  }

  async function renderSessionList(selectedId) {
    const sessions = await api.json('/api/sessions');
    const select = $('#session-select');
    select.innerHTML = sessions.map((s) => `<option value="${s.id}">${s.kind === 'external' ? '⇄ ' : ''}${escapeHtml(s.title === 'New session' ? t('new_session') : s.title)} · ${t('stages')[s.stage] || s.stage}</option>`).join('');
    if (selectedId) select.value = selectedId;
    return sessions;
  }

  function planStatusLabel(s) {
    if (!s.plan) return '';
    if (s.stage === 'awaiting_approval') return t('plan_awaiting');
    if (s.stage === 'done') return t('plan_done');
    return t('plan_agreed');
  }

  function renderSnapshot(s) {
    state.session = s;
    renderRoster(s.agents);
    $('#messages').innerHTML = '';
    if (!s.chat.length) $('#messages').innerHTML = `<div class="empty">${s.kind === 'external' ? t('empty_external') : fmt('empty', { name: escapeHtml(clientAgent()?.name || 'Agent') })}</div>`;
    s.chat.forEach(addMessage);
    renderStage(s.stage);
    Object.entries(s.agent_status || {}).forEach(([k, v]) => setNodeStatus(k, v));
    renderPlan(s.plan, planStatusLabel(s));
    // restore per-step progress from the event log (built-in and external workflows alike)
    (s.events || []).forEach((e) => {
      if (e.type === 'step') setStepStatus(e.step_id, e.status, e.status === 'done' ? undefined : `${t('attempt')} ${e.attempt}`);
      if (e.type === 'review') setStepStatus(e.step_id, e.verdict === 'revise' ? 'revising' : 'done', `${t('verdict')[e.verdict] || e.verdict}${e.score != null ? ' · ' + e.score + '/10' : ''}`);
    });
    (s.results || []).forEach((r) => setStepStatus(r.step_id, 'done', `${t('verdict')[r.verdict] || r.verdict}${r.score != null ? ' · ' + r.score + '/10' : ''}`));
    renderDocs(s.documents);
    setBusy(s.busy);
    showError(s.error);
    $('#log').innerHTML = '';
    (s.events || []).forEach(logEvent);
  }

  function addMessage(m) {
    const box = $('#messages');
    const empty = box.querySelector('.empty'); if (empty) empty.remove();
    const el = document.createElement('div');
    el.className = `msg ${m.role}`;
    const who = m.role === 'user' ? t('you') : escapeHtml(m.author ? nameOf(m.author) : (clientAgent()?.name || 'Agent'));
    el.innerHTML = `<div class="who"><span class="author">${who}</span> · ${fmtTime(m.ts)}</div>${markdown(m.content)}`;
    box.appendChild(el);
    box.scrollTop = box.scrollHeight;
  }

  function setTyping(on) {
    let el = $('#messages .typing');
    if (on && !el) { el = document.createElement('div'); el.className = 'typing'; el.textContent = fmt('typing', { name: clientAgent()?.name || 'Agent' }); $('#messages').appendChild(el); $('#messages').scrollTop = 1e9; }
    if (!on && el) el.remove();
  }

  function renderStage(stage) {
    const order = ['discussing', 'planning', 'awaiting_approval', 'executing', 'done'];
    const idx = order.indexOf(stage);
    document.querySelectorAll('#stepper span').forEach((el) => {
      const i = order.indexOf(el.dataset.stage);
      el.classList.toggle('active', i === idx);
      el.classList.toggle('past', i < idx);
    });
    $('#approval').hidden = stage !== 'awaiting_approval';
  }

  function setNodeStatus(agentId, status) {
    Scene.setStatus(agentId, status === 'idle' ? 'idle' : translateStatus(status));
    if (agentId === clientAgent()?.id) setTyping(status !== 'idle');
  }

  function renderPlan(plan, status) {
    const card = $('#plan-card');
    if (!plan) { card.hidden = true; return; }
    card.hidden = false;
    $('#plan-status').textContent = status || '';
    $('#plan-objective').textContent = plan.objective || '';
    const list = $('#plan-steps'); list.innerHTML = '';
    (plan.steps || []).forEach((step) => {
      const li = $('#step-tpl').content.firstElementChild.cloneNode(true);
      li.dataset.stepId = step.id;
      const who = step.assignee ? ` — ${nameOf(step.assignee)}` : '';
      li.querySelector('.title').textContent = `${step.id}. ${step.title}${who}`;
      li.title = `${step.instructions || ''}\n→ ${step.deliverable || ''}`;
      list.appendChild(li);
    });
    const extra = [];
    if (plan.acceptance_criteria?.length) extra.push(`<ul>${plan.acceptance_criteria.map((c) => `<li>${escapeHtml(c)}</li>`).join('')}</ul>`);
    if (plan.assumptions?.length) extra.push(`<ul>${plan.assumptions.map((c) => `<li><em>${escapeHtml(c)}</em></li>`).join('')}</ul>`);
    $('#plan-extra-body').innerHTML = extra.join('');
    $('#plan-extra').hidden = !extra.length;
  }

  function setStepStatus(stepId, status, meta) {
    const li = document.querySelector(`#plan-steps li[data-step-id="${stepId}"]`);
    if (!li) return;
    li.dataset.status = status;
    if (meta !== undefined) li.querySelector('.meta').textContent = meta;
  }

  function renderDocs(docs) {
    $('#docs').innerHTML = (docs || []).map((d) =>
      `<span class="doc" data-id="${d.id}">📄 ${escapeHtml(d.name)} <small>(${(d.chars / 1000).toFixed(1)}k)</small><button title="✕" data-remove="${d.id}">✕</button></span>`).join('');
  }

  function setBusy(busy) {
    $('#busy').hidden = !busy;
    $('#send').disabled = busy;
    $('#approve').disabled = busy; $('#reject').disabled = busy;
  }

  function showError(msg) { const el = $('#error'); el.hidden = !msg; el.textContent = msg || ''; }

  const ICONS = { chat: '💬', stage: '🧭', agent_status: '◌', flow: '→', plan: '🗂', step: '▸', output: '📝', review: '🔍', note: '✎', error: '⚠', document: '📄', roster: '👥', register_agent: '👤' };
  function logEvent(e) {
    if ((e.type === 'agent_status' && e.status === 'idle') || e.type === 'busy') return;
    const li = document.createElement('li');
    li.className = e.type;
    let text = '';
    switch (e.type) {
      case 'chat': text = `${e.role === 'user' ? t('you') : nameOf(e.author || clientAgent()?.id)}: ${e.content.slice(0, 120)}${e.content.length > 120 ? '…' : ''}`; break;
      case 'stage': text = `${t('stage')} → ${t('stages')[e.stage] || e.stage}`; break;
      case 'agent_status': text = `${nameOf(e.agent)}: ${translateStatus(e.status)}`; break;
      case 'flow': text = `${nameOf(e.source)} → ${nameOf(e.target)}: ${translateFlow(e.label)}`; break;
      case 'plan': text = `${t('plan')} ${e.status === 'draft' ? t('plan_draft') : t('plan_agreed')} · ${(e.plan?.steps || []).length} ${t('step').toLowerCase()}`; break;
      case 'step': text = `${t('step')} ${e.step_id}${e.assignee ? ` (${nameOf(e.assignee)})` : ''}: ${e.status} (${t('attempt')} ${e.attempt})`; break;
      case 'review': text = `${t('step').toLowerCase()} ${e.step_id}: ${t('verdict')[e.verdict] || e.verdict}${e.score != null ? ` · ${e.score}/10` : ''} — ${e.feedback || ''}`; break;
      case 'output': text = `${nameOf(e.agent)} · ${t('step').toLowerCase()} ${e.step_id} (${t('attempt')} ${e.attempt})`; break;
      case 'note': text = `${nameOf(e.agent)}: ${e.message}`; break;
      case 'error': text = e.message === 'Cancelled by user' ? t('cancelled') : e.message; break;
      case 'document': text = e.action === 'added' ? `${t('doc_added')}: ${e.document.name}` : t('doc_removed'); break;
      case 'roster': text = t('team_changed'); break;
      case 'register_agent': text = `${e.agent.name} ${t('registered')}`; break;
      default: text = JSON.stringify(e);
    }
    li.innerHTML = `<time>${fmtTime(e.ts)}</time><span>${ICONS[e.type] || '•'}</span><span>${escapeHtml(text)}</span>`;
    if (e.type === 'output') li.innerHTML += `<details><summary>${t('show_output')}</summary><pre>${escapeHtml(e.output)}</pre></details>`;
    const log = $('#log');
    log.appendChild(li);
    log.scrollTop = log.scrollHeight;
  }

  // ---------------------------------------------------------------- events (SSE)
  function connect(sessionId) {
    if (state.source) state.source.close();
    const src = new EventSource(`/api/sessions/${sessionId}/events`);
    state.source = src;
    src.addEventListener('snapshot', (ev) => renderSnapshot(JSON.parse(ev.data)));
    const on = (type, fn) => src.addEventListener(type, (ev) => { const e = JSON.parse(ev.data); fn(e); logEvent(e); });
    on('chat', (e) => {
      state.session.chat.push(e);
      addMessage(e);
      const client = clientAgent()?.id || 'user';
      const author = e.author || client;
      Scene.send(e.role === 'user' ? 'user' : author, e.role === 'user' ? client : 'user', e.role === 'user' ? t('user_to_c') : t('c_to_user'));
      if (e.role === 'user' && state.session.title === 'New session') { state.session.title = e.content.slice(0, 60); renderSessionList(state.session.id); }
    });
    on('stage', (e) => { state.session.stage = e.stage; renderStage(e.stage); $('#plan-status').textContent = planStatusLabel(state.session); });
    on('agent_status', (e) => { state.session.agent_status[e.agent] = e.status; setNodeStatus(e.agent, e.status); });
    on('flow', (e) => Scene.send(e.source, e.target, translateFlow(e.label)));
    on('plan', (e) => { state.session.plan = e.plan; renderPlan(e.plan, e.status === 'draft' ? t('plan_draft') : t('plan_agreed')); });
    on('step', (e) => setStepStatus(e.step_id, e.status, e.status === 'done' ? undefined : `${t('attempt')} ${e.attempt}`));
    on('review', (e) => setStepStatus(e.step_id, e.verdict === 'revise' ? 'revising' : 'done', `${t('verdict')[e.verdict] || e.verdict}${e.score != null ? ' · ' + e.score + '/10' : ''}`));
    on('output', () => {});
    on('note', () => {});
    on('error', (e) => { state.session.error = e.message; showError(e.message); });
    on('busy', (e) => { state.session.busy = e.busy; setBusy(e.busy); if (e.busy) showError(null); });
    on('roster', (e) => { state.session.agents = e.agents; renderRoster(e.agents); });
    on('register_agent', (e) => { state.session.agents = [...state.session.agents.filter((a) => a.id !== e.agent.id), e.agent]; renderRoster(state.session.agents); });
    on('document', (e) => {
      const docs = state.session.documents;
      if (e.action === 'added') docs.push(e.document); else state.session.documents = docs.filter((d) => d.id !== e.document.id);
      renderDocs(state.session.documents);
    });
    src.onerror = () => { /* EventSource reconnects automatically; the snapshot re-syncs the UI */ };
  }

  // ---------------------------------------------------------------- actions
  function openSession(id) { localStorage.setItem('office.session', id); $('#session-select').value = id; connect(id); }
  async function newSession() { const s = await api.json('/api/sessions', { method: 'POST', body: '{}' }); await renderSessionList(s.id); openSession(s.id); }
  async function send(text) {
    if (!text.trim() || !state.session) return;
    try { await api.json(`/api/sessions/${state.session.id}/chat`, { method: 'POST', body: JSON.stringify({ message: text, lang }) }); $('#input').value = ''; }
    catch (err) { showError(err.message); }
  }
  async function upload(files) {
    for (const file of files) {
      const form = new FormData(); form.append('file', file);
      try {
        const res = await fetch(`/api/sessions/${state.session.id}/documents`, { method: 'POST', body: form });
        if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
      } catch (err) { showError(`${file.name}: ${err.message}`); }
    }
    $('#file-input').value = '';
  }

  // ---------------------------------------------------------------- team settings
  const dialog = $('#settings'), form = $('#agent-form');
  let editing = null; // agent id being edited, or null for a new one
  function renderAgentList(agents) {
    $('#agent-list').innerHTML = agents.map((a) =>
      `<li data-id="${a.id}" class="${a.id === editing ? 'active' : ''}"><span class="swatch" style="background:${escapeHtml(a.color)}"></span><div class="meta"><b>${escapeHtml(a.name)}</b><span>${t('roles')[a.role]} · ${a.provider} · ${a.has_key ? t('key_set') : t('key_missing')}</span></div></li>`).join('');
  }
  function fillAgentForm(a) {
    editing = a ? a.id : null;
    const f = form.elements;
    f.id.value = a?.id || ''; f.name.value = a?.name || ''; f.role.value = a?.role || 'worker'; f.provider.value = a?.provider || 'anthropic';
    f.model.value = a?.model || 'claude-opus-5-5'; f.api_key.value = ''; f.base_url.value = a?.base_url || ''; f.skills.value = a?.skills || '';
    f.persona.value = a?.persona || ''; f.max_tokens.value = a?.max_tokens || 16000; f.effort.value = a?.effort || ''; f.temperature.value = a?.temperature ?? '';
    const n = state.config.agents.length;
    f.color.value = a?.color || state.config.colors[n % state.config.colors.length]; f.hair.value = a?.hair || state.config.hairs[n % state.config.hairs.length];
    f.clear_key.checked = false;
    $('#agent-delete').hidden = !a || a.role !== 'worker';
    $('#agent-error').hidden = true;
    renderAgentList(state.config.agents);
  }
  async function refreshAgents() { state.config.agents = await api.json('/api/agents'); renderAgentList(state.config.agents); }
  $('#open-settings').addEventListener('click', async () => { await refreshAgents(); fillAgentForm(state.config.agents[0]); dialog.showModal(); });
  $('#settings-close').addEventListener('click', () => dialog.close());
  $('#agent-list').addEventListener('click', (e) => { const li = e.target.closest('li'); if (li) fillAgentForm(state.config.agents.find((a) => a.id === li.dataset.id)); });
  $('#agent-new').addEventListener('click', () => fillAgentForm(null));
  $('#agent-delete').addEventListener('click', async () => {
    const a = state.config.agents.find((x) => x.id === editing); if (!a || !confirm(fmt('delete_agent_confirm', { name: a.name }))) return;
    try { await api.json(`/api/agents/${a.id}`, { method: 'DELETE' }); await refreshAgents(); fillAgentForm(state.config.agents[0]); }
    catch (err) { $('#agent-error').hidden = false; $('#agent-error').textContent = err.message; }
  });
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = form.elements;
    const body = { id: f.id.value || undefined, name: f.name.value, role: f.role.value, provider: f.provider.value, model: f.model.value, api_key: f.api_key.value,
      base_url: f.base_url.value, skills: f.skills.value, persona: f.persona.value, max_tokens: f.max_tokens.value, effort: f.effort.value,
      temperature: f.temperature.value, color: f.color.value, hair: f.hair.value, clear_key: f.clear_key.checked };
    try {
      const saved = await api.json('/api/agents', { method: 'POST', body: JSON.stringify(body) });
      await refreshAgents(); fillAgentForm(state.config.agents.find((a) => a.id === saved.id));
      $('#agent-error').hidden = false; $('#agent-error').style.color = 'var(--ok)'; $('#agent-error').textContent = t('saved');
      setTimeout(() => { $('#agent-error').hidden = true; $('#agent-error').style.color = ''; }, 1500);
    } catch (err) { $('#agent-error').hidden = false; $('#agent-error').style.color = ''; $('#agent-error').textContent = err.message; }
  });

  // ---------------------------------------------------------------- wiring
  $('#composer').addEventListener('submit', (e) => { e.preventDefault(); send($('#input').value); });
  $('#input').addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send($('#input').value); } });
  $('#file-input').addEventListener('change', (e) => upload(e.target.files));
  $('#docs').addEventListener('click', async (e) => {
    const id = e.target.dataset.remove; if (!id) return;
    try { await api.json(`/api/sessions/${state.session.id}/documents/${id}`, { method: 'DELETE' }); } catch (err) { showError(err.message); }
  });
  $('#approve').addEventListener('click', async () => { try { await api.json(`/api/sessions/${state.session.id}/plan/approve`, { method: 'POST', body: JSON.stringify({ lang }) }); } catch (err) { showError(err.message); } });
  $('#reject').addEventListener('click', async () => {
    const feedback = prompt(t('reject_prompt')); if (feedback === null) return;
    try { await api.json(`/api/sessions/${state.session.id}/plan/reject`, { method: 'POST', body: JSON.stringify({ feedback, lang }) }); } catch (err) { showError(err.message); }
  });
  $('#cancel').addEventListener('click', () => api.json(`/api/sessions/${state.session.id}/cancel`, { method: 'POST' }).catch((err) => showError(err.message)));
  $('#new-session').addEventListener('click', newSession);
  $('#delete-session').addEventListener('click', async () => {
    if (!state.session || !confirm(t('delete_confirm'))) return;
    await api.json(`/api/sessions/${state.session.id}`, { method: 'DELETE' });
    localStorage.removeItem('office.session');
    const sessions = await renderSessionList();
    sessions.length ? openSession(sessions[0].id) : newSession();
  });
  $('#session-select').addEventListener('change', (e) => openSession(e.target.value));
  $('#clear-log').addEventListener('click', () => { $('#log').innerHTML = ''; });
  $('#lang-select').addEventListener('change', (e) => applyLanguage(e.target.value));

  (async function init() {
    state.config = await api.json('/api/config');
    $('#file-input').accept = state.config.allowed_extensions.join(',');
    applyLanguage(localStorage.getItem('office.lang') || (state.config.lang === 'en' ? 'en' : 'id'));
    const sessions = await renderSessionList();
    const saved = localStorage.getItem('office.session');
    const pick = sessions.find((s) => s.id === saved) || sessions[0];
    pick ? openSession(pick.id) : newSession();
  })().catch((err) => showError(err.message));
})();
