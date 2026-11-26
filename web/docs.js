/* Dokumen tab: upload documents, browse versions and outline, download, roll back. */
(function () {
  'use strict';
  const $ = (sel) => document.querySelector(sel);

  const I18N = {
    id: {
      documents: 'Dokumen', upload: 'Unggah DOCX/PDF', title_ph: 'Judul (opsional)', label_ph: 'Label, mis. laporan bulanan (opsional)',
      pick: 'Pilih dokumen', new_version: 'Unggah versi baru', delete_doc: 'Hapus dokumen', versions: 'Versi',
      source: 'Sumber', time: 'Waktu', status: 'Status', note: 'Catatan', outline: 'Outline', download: 'Unduh',
      download_original: 'Unduh asli', restore: 'Kembalikan ke versi ini', view: 'Lihat', empty: 'Belum ada dokumen. Unggah DOCX atau PDF di atas.',
      uploading: 'Mengunggah…', confirm_delete: 'Hapus dokumen ini beserta semua versinya?',
      confirm_restore: 'Buat versi baru yang isinya sama dengan v{n}?', blocks_of: 'Isi bagian', versions_n: '{n} versi',
      src_upload: 'unggah', src_rollback: 'rollback', src_revision: 'revisi AI', src_instructed_revision: 'revisi dari bahan', src_update: 'update berkala',
      st_ready: 'siap', st_processing: 'diproses', st_failed: 'gagal',
      warn_tracked_changes_accepted: 'Dokumen berisi tracked changes; semua perubahan diterima di versi kerja. File asli tetap bisa diunduh.',
      warn_no_text_extracted: 'Tidak ada teks yang terbaca (mungkin PDF hasil scan). Dokumen ini tidak bisa direview sebelum di-OCR.',
      t_heading: 'judul', t_paragraph: 'paragraf', t_list_item: 'daftar', t_table_cell: 'sel tabel',
      review: 'Review', review_start: 'Review versi ini', cancel: 'Batalkan', see_office: 'Lihat di Kantor',
      cat_language: 'Bahasa', cat_consistency: 'Konsistensi', cat_structure: 'Struktur', cat_substance: 'Substansi',
      findings: 'Temuan', all_categories: 'Semua kategori', all_status: 'Semua status',
      fs_valid: 'Valid', fs_needs_human: 'Perlu dicek', fs_rejected: 'Ditolak',
      col_category: 'Kategori', col_severity: 'Tingkat', col_location: 'Lokasi', col_quote: 'Kutipan', col_problem: 'Masalah', col_suggestion: 'Saran',
      sev_high: 'tinggi', sev_medium: 'sedang', sev_low: 'rendah',
      job_queued: 'mengantre', job_running: 'berjalan', job_paused: 'dijeda — kuota habis', job_finalizing: 'cek konsistensi antarbab',
      job_done: 'selesai', job_partial: 'selesai sebagian', job_failed: 'gagal', job_cancelled: 'dibatalkan',
      progress: '{done}/{total} bagian', resume_at: 'lanjut {time}', failed_n: '{n} gagal',
      counts: 'valid {valid} · perlu dicek {needs_human} · ditolak {rejected}', no_findings: 'Tidak ada temuan.',
      pick_category: 'Pilih minimal satu kategori.', vs: 'vs',
      search_title: 'Cari di dokumen (uji RAG)', search: 'Cari', search_ph: 'mis. jumlah responden', no_results: 'Tidak ada hasil.',
      idx_none: 'belum diindeks', idx_indexing: 'sedang diindeks', idx_ready: 'indeks siap', idx_fts_only: 'indeks kata kunci saja', idx_failed: 'indeks gagal',
    },
    en: {
      documents: 'Documents', upload: 'Upload DOCX/PDF', title_ph: 'Title (optional)', label_ph: 'Label, e.g. monthly report (optional)',
      pick: 'Pick a document', new_version: 'Upload new version', delete_doc: 'Delete document', versions: 'Versions',
      source: 'Source', time: 'Time', status: 'Status', note: 'Note', outline: 'Outline', download: 'Download',
      download_original: 'Download original', restore: 'Restore this version', view: 'View', empty: 'No documents yet. Upload a DOCX or PDF above.',
      uploading: 'Uploading…', confirm_delete: 'Delete this document and all its versions?',
      confirm_restore: 'Create a new version with the same content as v{n}?', blocks_of: 'Section content', versions_n: '{n} versions',
      src_upload: 'upload', src_rollback: 'rollback', src_revision: 'AI revision', src_instructed_revision: 'revision from notes', src_update: 'periodic update',
      st_ready: 'ready', st_processing: 'processing', st_failed: 'failed',
      warn_tracked_changes_accepted: 'The document had tracked changes; all were accepted in the working version. The original file can still be downloaded.',
      warn_no_text_extracted: 'No text could be extracted (maybe a scanned PDF). It cannot be reviewed until it is OCRed.',
      t_heading: 'heading', t_paragraph: 'paragraph', t_list_item: 'list', t_table_cell: 'table cell',
      review: 'Review', review_start: 'Review this version', cancel: 'Cancel', see_office: 'See in Office',
      cat_language: 'Language', cat_consistency: 'Consistency', cat_structure: 'Structure', cat_substance: 'Substance',
      findings: 'Findings', all_categories: 'All categories', all_status: 'All statuses',
      fs_valid: 'Valid', fs_needs_human: 'Needs check', fs_rejected: 'Rejected',
      col_category: 'Category', col_severity: 'Severity', col_location: 'Location', col_quote: 'Quote', col_problem: 'Problem', col_suggestion: 'Suggestion',
      sev_high: 'high', sev_medium: 'medium', sev_low: 'low',
      job_queued: 'queued', job_running: 'running', job_paused: 'paused — usage limit', job_finalizing: 'cross-section consistency check',
      job_done: 'done', job_partial: 'partly done', job_failed: 'failed', job_cancelled: 'cancelled',
      progress: '{done}/{total} sections', resume_at: 'resumes {time}', failed_n: '{n} failed',
      counts: 'valid {valid} · needs check {needs_human} · rejected {rejected}', no_findings: 'No findings.',
      pick_category: 'Pick at least one category.', vs: 'vs',
      search_title: 'Search the document (RAG test)', search: 'Search', search_ph: 'e.g. number of respondents', no_results: 'No results.',
      idx_none: 'not indexed', idx_indexing: 'indexing', idx_ready: 'index ready', idx_fts_only: 'keyword index only', idx_failed: 'index failed',
    },
  };
  const lang = () => (document.documentElement.lang === 'en' ? 'en' : 'id');
  const t = (key, vars) => {
    let s = I18N[lang()][key] ?? I18N.id[key] ?? key;
    if (vars) Object.entries(vars).forEach(([k, v]) => { s = s.replace(`{${k}}`, v); });
    return s;
  };

  const state = { docs: [], doc: null, versions: [], versionId: null, sectionId: null, sections: [], job: null, poll: null };
  const ACTIVE = ['queued', 'running', 'paused', 'finalizing'];

  async function api(url, opts = {}) {
    const res = await fetch(url, opts);
    const body = res.headers.get('content-type')?.includes('json') ? await res.json() : null;
    if (!res.ok) throw new Error(body?.detail || `HTTP ${res.status}`);
    return body;
  }
  function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    Object.entries(props).forEach(([k, v]) => {
      if (k === 'class') node.className = v;
      else if (k === 'dataset') Object.assign(node.dataset, v);
      else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    });
    children.flat().forEach((c) => node.append(c instanceof Node ? c : document.createTextNode(String(c ?? ''))));
    return node;
  }
  function showError(msg) { const e = $('#docs-error'); e.hidden = !msg; e.textContent = msg || ''; }
  const fmtTime = (ts) => new Date(ts * 1000).toLocaleString(lang() === 'en' ? 'en-GB' : 'id-ID', { dateStyle: 'medium', timeStyle: 'short' });

  function applyStaticText() {
    document.querySelectorAll('#docs-view [data-d]').forEach((n) => { n.textContent = t(n.dataset.d); });
    document.querySelectorAll('#docs-view [data-dp]').forEach((n) => { n.placeholder = t(n.dataset.dp); });
  }

  // ------------------------------------------------------------- list
  async function loadList(selectId) {
    state.docs = await api('/api/docs');
    const ul = $('#docs-items');
    ul.innerHTML = '';
    if (!state.docs.length) ul.append(el('li', { class: 'muted' }, t('empty')));
    state.docs.forEach((d) => {
      const li = el('li', { class: d.id === state.doc?.id ? 'active' : '', onclick: () => openDoc(d.id) },
        el('b', {}, d.title),
        el('span', { class: 'muted' }, [d.label, t('versions_n', { n: d.version_count })].filter(Boolean).join(' · ')));
      ul.append(li);
    });
    if (selectId) await openDoc(selectId);
  }

  // ------------------------------------------------------------- detail
  async function openDoc(id) {
    showError('');
    const data = await api(`/api/docs/${id}`);
    state.doc = data.document;
    state.versions = data.versions;
    document.querySelectorAll('#docs-items li').forEach((li, i) => li.classList.toggle('active', state.docs[i]?.id === id));
    $('#doc-title').textContent = state.doc.title;
    $('#doc-title').removeAttribute('data-d');
    const label = $('#doc-label');
    label.hidden = !state.doc.label;
    label.textContent = state.doc.label;
    $('#doc-body').hidden = false;
    renderVersions();
    const latest = state.doc.latest_version_id || state.versions[state.versions.length - 1]?.id;
    if (latest) await loadOutline(latest);
  }

  function renderVersions() {
    const tbody = $('#doc-versions');
    tbody.innerHTML = '';
    [...state.versions].reverse().forEach((v) => {
      const actions = el('td', { class: 'actions' });
      if (v.status === 'ready') {
        actions.append(
          el('button', { class: 'btn small', onclick: () => loadOutline(v.id) }, t('view')),
          el('a', { class: 'btn small', href: `/api/docs/versions/${v.id}/download` }, t('download')));
        if (v.original_path) actions.append(el('a', { class: 'btn small', href: `/api/docs/versions/${v.id}/download?variant=original` }, t('download_original')));
        if (v.id !== state.doc.latest_version_id) actions.append(el('button', { class: 'btn small', onclick: () => restoreVersion(v) }, t('restore')));
      }
      const note = [v.note, ...v.warnings.map((w) => '⚠ ' + t('warn_' + w))].filter(Boolean).join(' ');
      tbody.append(el('tr', { class: v.id === state.versionId ? 'current' : '' },
        el('td', {}, `v${v.version_no}`),
        el('td', {}, t('src_' + v.source)),
        el('td', {}, fmtTime(v.created_at)),
        el('td', {}, el('span', { class: `tag st-${v.status}` }, t('st_' + v.status))),
        el('td', { class: 'note' }, note || v.error || ''),
        actions));
    });
  }

  async function loadOutline(versionId) {
    state.versionId = versionId;
    state.sectionId = null;
    renderVersions();
    const v = state.versions.find((x) => x.id === versionId);
    $('#outline-version').textContent = v ? `v${v.version_no} · ${v.block_count} blok · ${t('idx_' + v.index_status)}` : '';
    $('#search-results').innerHTML = '';
    loadReview(versionId).catch((err) => showError(err.message));
    const sections = await api(`/api/docs/versions/${versionId}/outline`);
    state.sections = sections;
    const ul = $('#doc-outline');
    ul.innerHTML = '';
    sections.forEach((s) => {
      ul.append(el('li', { style: `padding-left:${(s.level - 1) * 16 + 8}px`, dataset: { id: s.id }, onclick: () => loadBlocks(s) },
        el('span', {}, s.heading), el('span', { class: 'muted' }, ` (${s.block_count})`)));
    });
    $('#doc-blocks').hidden = true;
  }

  async function loadBlocks(section) {
    state.sectionId = section.id;
    document.querySelectorAll('#doc-outline li').forEach((li) => li.classList.toggle('active', li.dataset.id === section.id));
    const blocks = await api(`/api/docs/versions/${state.versionId}/blocks?section_id=${encodeURIComponent(section.id)}`);
    const box = $('#doc-blocks');
    box.innerHTML = '';
    box.append(el('h3', {}, `${t('blocks_of')}: ${section.path}`));
    blocks.forEach((b) => {
      const where = b.type === 'table_cell' ? ` [${b.row + 1},${b.col + 1}]` : '';
      box.append(el('div', { class: `blk blk-${b.type}`, dataset: { id: b.id } }, el('small', { class: 'muted' }, `${b.id} · ${t('t_' + b.type)}${where}`), el('p', {}, b.text)));
    });
    box.hidden = false;
  }

  // ------------------------------------------------------------- review
  function stopPoll() { if (state.poll) { clearTimeout(state.poll); state.poll = null; } }

  async function loadReview(versionId) {
    stopPoll();
    state.job = null;
    renderJob(null);
    const jobs = await api(`/api/docs/versions/${versionId}/jobs`);
    const job = jobs.find((j) => j.type === 'review');
    if (job && state.versionId === versionId) await refreshJob(job.id);
  }

  async function refreshJob(jobId) {
    stopPoll();
    const data = await api(`/api/docs/jobs/${jobId}`);
    if (data.job.version_id !== state.versionId) return;
    state.job = data;
    renderJob(data);
    if (ACTIVE.includes(data.job.status)) state.poll = setTimeout(() => refreshJob(jobId).catch((err) => showError(err.message)), 2000);
    else loadFindings().catch((err) => showError(err.message));
    if (ACTIVE.includes(data.job.status) && data.counts.valid) loadFindings().catch(() => {});
  }

  function renderJob(data) {
    const status = $('#review-status');
    const active = !!data && ACTIVE.includes(data.job.status);
    const v = state.versions.find((x) => x.id === state.versionId);
    $('#doc-review').disabled = active || !v || v.status !== 'ready';
    $('#review-cancel').hidden = !active;
    $('#review-office').hidden = !data?.job.params?.session_id;
    const prog = $('#review-progress');
    prog.innerHTML = '';
    if (!data) { status.textContent = ''; $('#findings-box').hidden = true; return; }
    const j = data.job;
    const parts = [t('job_' + j.status), t('progress', { done: j.done_tasks + j.failed_tasks, total: j.total_tasks })];
    if (j.failed_tasks) parts.push(t('failed_n', { n: j.failed_tasks }));
    if (j.status === 'paused' && j.resume_at) parts.push(t('resume_at', { time: new Date(j.resume_at * 1000).toLocaleTimeString(lang() === 'en' ? 'en-GB' : 'id-ID', { timeStyle: 'short' }) }));
    if (j.error) parts.push(j.error);
    status.textContent = parts.join(' · ');
    data.tasks.forEach((task) => prog.append(el('li', { class: task.status, title: task.error || task.heading }, task.heading)));
    $('#findings-box').hidden = false;
    $('#findings-counts').textContent = t('counts', data.counts);
  }

  async function startReview() {
    const cats = [...document.querySelectorAll('input[name="review-cat"]:checked')].map((c) => c.value);
    if (!cats.length) { showError(t('pick_category')); return; }
    showError('');
    $('#doc-review').disabled = true;
    try {
      const res = await api(`/api/docs/versions/${state.versionId}/reviews`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ categories: cats }) });
      await refreshJob(res.job.id);
    } catch (err) { showError(err.message); renderJob(state.job); }
  }

  async function cancelReview() {
    if (!state.job) return;
    try { await api(`/api/docs/jobs/${state.job.job.id}/cancel`, { method: 'POST' }); await refreshJob(state.job.job.id); } catch (err) { showError(err.message); }
  }

  async function openOffice() {
    const sid = state.job?.job.params?.session_id;
    if (!sid) return;
    setView('office');
    if (window.Office) await window.Office.openSession(sid);
  }

  async function loadFindings() {
    if (!state.job) return;
    const q = new URLSearchParams();
    if ($('#findings-status').value) q.set('status', $('#findings-status').value);
    if ($('#findings-cat').value) q.set('category', $('#findings-cat').value);
    const rows = await api(`/api/docs/jobs/${state.job.job.id}/findings?${q}`);
    const tbody = $('#findings-rows');
    tbody.innerHTML = '';
    if (!rows.length) tbody.append(el('tr', {}, el('td', { colspan: '6', class: 'muted' }, t('no_findings'))));
    rows.forEach((f) => {
      const loc = el('td', { class: 'loc' }, el('div', {}, f.heading_path || ''), el('small', { class: 'muted' }, f.block_id));
      if (f.other_block_id) loc.append(el('div', { class: 'muted' }, `${t('vs')} ${f.other_heading_path || ''} (${f.other_block_id})`));
      const problem = el('td', {}, f.description);
      if (f.validator_note && f.status !== 'valid') problem.append(el('div', { class: 'muted' }, f.validator_note));
      tbody.append(el('tr', { onclick: () => showBlock(f.section_id, f.block_id) },
        el('td', {}, t('cat_' + f.category)),
        el('td', { class: `sev-${f.severity}` }, t('sev_' + f.severity)),
        loc,
        el('td', { class: 'quote' }, f.quote),
        problem,
        el('td', {}, f.suggestion || '')));
    });
  }

  async function showBlock(sectionId, blockId) {
    const section = state.sections.find((s) => s.id === sectionId);
    if (!section) return;
    await loadBlocks(section);
    const node = document.querySelector(`#doc-blocks .blk[data-id="${CSS.escape(blockId)}"]`);
    if (node) { node.classList.add('hl'); node.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
  }

  // ------------------------------------------------------------- search
  async function searchDoc(e) {
    e.preventDefault();
    const q = $('#doc-search-q').value.trim();
    if (!q || !state.versionId) return;
    const ul = $('#search-results');
    try {
      const hits = await api(`/api/docs/versions/${state.versionId}/search?q=${encodeURIComponent(q)}&k=8`);
      ul.innerHTML = '';
      if (!hits.length) ul.append(el('li', { class: 'muted' }, t('no_results')));
      hits.forEach((h) => {
        const src = [h.fts_rank ? `FTS #${h.fts_rank}` : '', h.vec_rank ? `vektor #${h.vec_rank}` : ''].filter(Boolean).join(' · ');
        ul.append(el('li', { onclick: () => showBlock(h.section_id, h.block_ids[0]) },
          el('b', {}, h.heading_path), el('span', { class: 'muted' }, ` · skor ${h.score.toFixed(4)} · ${src}`),
          el('p', {}, h.content.split('\n').slice(1).join('\n'))));
      });
    } catch (err) { showError(err.message); }
  }

  // ------------------------------------------------------------- actions
  async function uploadNew(e) {
    e.preventDefault();
    const file = $('#docs-file').files[0];
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    form.append('title', $('#docs-title').value);
    form.append('label', $('#docs-label').value);
    const btn = e.submitter || $('#docs-upload button');
    btn.disabled = true;
    btn.textContent = t('uploading');
    try {
      const res = await api('/api/docs', { method: 'POST', body: form });
      e.target.reset();
      await loadList(res.document.id);
      showError('');
    } catch (err) { showError(err.message); } finally { btn.disabled = false; btn.textContent = t('upload'); }
  }

  async function uploadVersion(e) {
    const file = e.target.files[0];
    if (!file || !state.doc) return;
    const form = new FormData();
    form.append('file', file);
    try {
      await api(`/api/docs/${state.doc.id}/versions`, { method: 'POST', body: form });
      await loadList(state.doc.id);
      showError('');
    } catch (err) { showError(err.message); } finally { e.target.value = ''; }
  }

  async function restoreVersion(v) {
    if (!confirm(t('confirm_restore', { n: v.version_no }))) return;
    try {
      await api(`/api/docs/versions/${v.id}/restore`, { method: 'POST' });
      await loadList(state.doc.id);
    } catch (err) { showError(err.message); }
  }

  async function deleteDoc() {
    if (!state.doc || !confirm(t('confirm_delete'))) return;
    try {
      await api(`/api/docs/${state.doc.id}`, { method: 'DELETE' });
      state.doc = null;
      stopPoll();
      $('#doc-body').hidden = true;
      $('#doc-title').dataset.d = 'pick';
      $('#doc-title').textContent = t('pick');
      $('#doc-label').hidden = true;
      await loadList();
    } catch (err) { showError(err.message); }
  }

  // ------------------------------------------------------------- view switching
  function setView(view) {
    const docs = view === 'docs';
    $('#docs-view').hidden = !docs;
    document.querySelector('main.layout').hidden = docs;
    $('#tab-docs').classList.toggle('active', docs);
    $('#tab-office').classList.toggle('active', !docs);
    try { localStorage.setItem('office.view', view); } catch (_) { /* storage unavailable */ }
    if (!docs) stopPoll();
    if (docs) { applyStaticText(); loadList(state.doc?.id).catch((err) => showError(err.message)); }
  }

  window.DocsView = { show: () => setView('docs'), hide: () => setView('office') };

  $('#tab-docs').addEventListener('click', () => setView('docs'));
  $('#tab-office').addEventListener('click', () => setView('office'));
  $('#docs-upload').addEventListener('submit', uploadNew);
  $('#doc-new-version').addEventListener('change', uploadVersion);
  $('#doc-delete').addEventListener('click', deleteDoc);
  $('#doc-review').addEventListener('click', startReview);
  $('#review-cancel').addEventListener('click', cancelReview);
  $('#review-office').addEventListener('click', openOffice);
  $('#findings-status').addEventListener('change', () => loadFindings().catch((err) => showError(err.message)));
  $('#findings-cat').addEventListener('change', () => loadFindings().catch((err) => showError(err.message)));
  $('#doc-search').addEventListener('submit', searchDoc);
  $('#lang-select').addEventListener('change', () => {
    applyStaticText();
    if (!$('#docs-view').hidden) { if (state.doc) openDoc(state.doc.id); else loadList(); }
  });

  let saved = 'office';
  try { saved = localStorage.getItem('office.view') || 'office'; } catch (_) { /* storage unavailable */ }
  if (saved === 'docs') setView('docs');
})();
