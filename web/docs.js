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
    },
  };
  const lang = () => (document.documentElement.lang === 'en' ? 'en' : 'id');
  const t = (key, vars) => {
    let s = I18N[lang()][key] ?? I18N.id[key] ?? key;
    if (vars) Object.entries(vars).forEach(([k, v]) => { s = s.replace(`{${k}}`, v); });
    return s;
  };

  const state = { docs: [], doc: null, versions: [], versionId: null, sectionId: null };

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
    $('#outline-version').textContent = v ? `v${v.version_no} · ${v.block_count} blok` : '';
    const sections = await api(`/api/docs/versions/${versionId}/outline`);
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
      box.append(el('div', { class: `blk blk-${b.type}` }, el('small', { class: 'muted' }, `${b.id} · ${t('t_' + b.type)}${where}`), el('p', {}, b.text)));
    });
    box.hidden = false;
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
    if (docs) { applyStaticText(); loadList(state.doc?.id).catch((err) => showError(err.message)); }
  }

  window.DocsView = { show: () => setView('docs'), hide: () => setView('office') };

  $('#tab-docs').addEventListener('click', () => setView('docs'));
  $('#tab-office').addEventListener('click', () => setView('office'));
  $('#docs-upload').addEventListener('submit', uploadNew);
  $('#doc-new-version').addEventListener('change', uploadVersion);
  $('#doc-delete').addEventListener('click', deleteDoc);
  $('#lang-select').addEventListener('change', () => {
    applyStaticText();
    if (!$('#docs-view').hidden) { if (state.doc) openDoc(state.doc.id); else loadList(); }
  });

  let saved = 'office';
  try { saved = localStorage.getItem('office.view') || 'office'; } catch (_) { /* storage unavailable */ }
  if (saved === 'docs') setView('docs');
})();
