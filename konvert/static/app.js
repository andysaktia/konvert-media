'use strict';

const $ = (sel, root = document) => root.querySelector(sel);
const make = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* mode privat: abaikan */ } },
};

const KIND = { video: 'Video', audio: 'Audio', image: 'Gambar' };
const GROUPS = [['video', 'Video'], ['audio', 'Audio'], ['image', 'Gambar']];
const ACTIVE = ['queued', 'running'];

const state = {
  presets: [],
  files: new Map(),    // file yang sudah terunggah
  pending: new Map(),  // unggahan berjalan atau gagal
  jobs: new Map(),
  pref: store.get('konvert.preset') || 'mp4-h264',
};
let engineText = '';
let tmp = 0;

// ---------- format angka ----------
const nf = new Intl.NumberFormat('id-ID', { maximumFractionDigits: 1 });
const fmtSize = (n) => n < 1024 ? `${n} B`
  : n < 1048576 ? `${nf.format(n / 1024)} KB`
  : n < 1073741824 ? `${nf.format(n / 1048576)} MB`
  : `${nf.format(n / 1073741824)} GB`;
const fmtDur = (s) => {
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
  const pad = (v) => String(v).padStart(2, '0');
  return h ? `${h}:${pad(m)}:${pad(x)}` : `${m}:${pad(x)}`;
};

async function api(path, opts) {
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `Galat ${r.status}`);
  return data;
}

function setNotice(text, kind) {
  const n = $('#notice');
  n.textContent = text || '';
  if (kind) n.dataset.kind = kind; else delete n.dataset.kind;
}

// ---------- preset & panel format ----------
const fileKinds = () => new Set([...state.files.values()].map((f) => f.kind));

function effectivePreset() {
  const kinds = fileKinds();
  const usable = (p) => !kinds.size || p.kinds.some((k) => kinds.has(k));
  const pref = state.presets.find((p) => p.id === state.pref);
  return (pref && usable(pref)) ? pref : (state.presets.find(usable) || null);
}

function renderPresets() {
  const root = $('#presets');
  const kinds = fileKinds();
  const current = effectivePreset();
  root.replaceChildren();
  for (const [group, title] of GROUPS) {
    const items = state.presets.filter((p) => p.group === group);
    if (!items.length) continue;
    const fs = make('fieldset');
    fs.append(make('legend', null, title));
    for (const p of items) {
      const usable = !kinds.size || p.kinds.some((k) => kinds.has(k));
      const label = make('label', 'preset' + (usable ? '' : ' is-off'));
      const input = make('input');
      input.type = 'radio';
      input.name = 'preset';
      input.value = p.id;
      input.disabled = !usable;
      input.checked = !!current && current.id === p.id;
      input.addEventListener('change', () => {
        state.pref = p.id;
        store.set('konvert.preset', p.id);
        syncSetup();
      });
      label.append(input, make('span', 'preset-name', p.label), make('span', 'preset-ext', '.' + p.ext),
        make('span', 'preset-desc', p.desc));
      fs.append(label);
    }
    root.append(fs);
  }
  syncSetup();
}

function syncSetup() {
  const p = effectivePreset();
  $('#heightWrap').hidden = !(p && p.scalable);
  const ok = p ? [...state.files.values()].filter((f) => p.kinds.includes(f.kind)) : [];
  const skipped = state.files.size - ok.length;
  const go = $('#go');
  go.disabled = ok.length === 0;
  go.textContent = ok.length > 1 ? `Konversi ${ok.length} file` : 'Konversi';
  if (!state.files.size) setNotice('Tambahkan file dulu.');
  else if (skipped) setNotice(`${skipped} file dilewati karena tidak cocok dengan format ini.`);
  else setNotice('');
}

// ---------- file ----------
function pendingRow(item) {
  const li = make('li', 'file');
  li.append(make('div', 'file-name', item.name));
  const meta = make('div', 'file-meta',
    item.error || (item.pct < 100 ? `Mengunggah ${item.pct}%` : 'Memeriksa file…'));
  if (item.error) {
    meta.dataset.kind = 'error';
    meta.setAttribute('role', 'alert');
    const x = make('button', 'icon', '×');
    x.type = 'button';
    x.setAttribute('aria-label', `Tutup pesan untuk ${item.name}`);
    x.onclick = () => {
      for (const [k, v] of state.pending) if (v === item) state.pending.delete(k);
      renderFiles();
    };
    li.append(meta, x);
  } else {
    li.append(meta);
  }
  return li;
}

function fileRow(f) {
  const li = make('li', 'file');
  const name = make('div', 'file-name', f.name);
  name.title = f.name;
  const meta = make('div', 'file-meta');
  const parts = [KIND[f.kind], f.summary];
  if (f.duration > 0 && f.kind !== 'image') parts.push(fmtDur(f.duration));
  parts.push(fmtSize(f.size));
  for (const t of parts) meta.append(make('span', null, t));
  const x = make('button', 'icon', '×');
  x.type = 'button';
  x.setAttribute('aria-label', `Hapus ${f.name} dari daftar`);
  x.onclick = async () => {
    try {
      await api(`/api/files/${f.id}`, { method: 'DELETE' });
      state.files.delete(f.id);
      filesChanged();
    } catch (e) { setNotice(e.message, 'error'); }
  };
  li.append(name, meta, x);
  return li;
}

function renderFiles() {
  $('#files').replaceChildren(
    ...[...state.pending.values()].map(pendingRow),
    ...[...state.files.values()].map(fileRow));
}
function filesChanged() { renderFiles(); renderPresets(); }

function upload(file) {
  const id = 'p' + (++tmp);
  const item = { name: file.name, pct: 0, error: '' };
  state.pending.set(id, item);
  renderFiles();
  const xhr = new XMLHttpRequest();
  xhr.open('POST', '/api/upload');
  xhr.setRequestHeader('X-Filename', encodeURIComponent(file.name));
  xhr.upload.onprogress = (e) => {
    if (!e.lengthComputable) return;
    const pct = Math.round((e.loaded / e.total) * 100);
    if (pct !== item.pct) { item.pct = pct; renderFiles(); }
  };
  xhr.onload = () => {
    let data = {};
    try { data = JSON.parse(xhr.responseText); } catch { /* biarkan kosong */ }
    if (xhr.status === 200) {
      state.pending.delete(id);
      state.files.set(data.id, data);
      filesChanged();
    } else {
      item.error = data.error || `Unggah gagal (galat ${xhr.status})`;
      renderFiles();
    }
  };
  xhr.onerror = () => { item.error = 'Koneksi ke server terputus.'; renderFiles(); };
  xhr.send(file);
}

// ---------- hasil / job ----------
function statusText(j) {
  switch (j.status) {
    case 'queued': return 'Menunggu giliran';
    case 'running': {
      const speed = j.speed && j.speed !== 'N/A' ? `, kecepatan ${j.speed}` : '';
      return j.percent < 1 ? `Memproses…${speed}` : `${Math.floor(j.percent)}%${speed}`;
    }
    case 'done': return `Selesai, ${fmtSize(j.out_size)}`;
    case 'error': return 'Gagal';
    case 'cancelled': return 'Dibatalkan';
    default: return j.status;
  }
}

function jobRow(id) {
  const li = make('li', 'job');
  li.dataset.id = id;
  const main = make('div', 'job-main');
  main.append(make('div', 'job-name'), make('div', 'job-fmt'));
  const strip = make('div', 'strip');
  strip.setAttribute('role', 'progressbar');
  strip.setAttribute('aria-valuemin', '0');
  strip.setAttribute('aria-valuemax', '100');
  strip.append(make('i'));
  const mid = make('div', 'job-mid');
  mid.append(strip, make('div', 'status'));
  const err = make('details', 'job-err');
  err.hidden = true;
  err.append(make('summary', null, 'Pesan dari ffmpeg'), make('pre'));
  li.append(main, mid, make('div', 'actions'), err);
  return li;
}

function buildActions(j) {
  const out = [];
  if (j.status === 'done') {
    const a = make('a', 'btn solid', 'Unduh');
    a.href = `/api/jobs/${j.id}/download`;
    a.download = j.out_name;
    out.push(a);
  }
  const b = make('button', 'btn');
  b.type = 'button';
  if (ACTIVE.includes(j.status)) {
    b.textContent = 'Batal';
    b.onclick = () => api(`/api/jobs/${j.id}/cancel`, { method: 'POST' }).catch((e) => setNotice(e.message, 'error'));
  } else {
    b.textContent = 'Hapus';
    b.onclick = () => api(`/api/jobs/${j.id}`, { method: 'DELETE' })
      .then(() => removeJob(j.id)).catch((e) => setNotice(e.message, 'error'));
  }
  out.push(b);
  return out;
}

function upsertJob(j) {
  state.jobs.set(j.id, j);
  let li = $(`.job[data-id="${j.id}"]`);
  if (!li) { li = jobRow(j.id); $('#jobs').append(li); }
  $('.job-name', li).textContent = j.name;
  $('.job-name', li).title = j.name;
  $('.job-fmt', li).textContent = j.label + (j.max_height ? `, maks ${j.max_height}p` : '');
  const strip = $('.strip', li);
  strip.style.setProperty('--p', `${j.percent}%`);
  strip.setAttribute('aria-valuenow', String(Math.round(j.percent)));
  strip.setAttribute('aria-label', `Progres ${j.name}`);
  $('.status', li).textContent = statusText(j);
  if (li.dataset.status !== j.status) {  // tombol dibangun ulang hanya saat status berubah
    li.dataset.status = j.status;
    $('.actions', li).replaceChildren(...buildActions(j));
  }
  const err = $('.job-err', li);
  err.hidden = !j.error;
  $('pre', err).textContent = j.error;
  jobsChrome();
}

function removeJob(id) {
  state.jobs.delete(id);
  $(`.job[data-id="${id}"]`)?.remove();
  jobsChrome();
}

function jobsChrome() {
  $('#jobsEmpty').hidden = state.jobs.size > 0;
  $('#clear').hidden = ![...state.jobs.values()].some((j) => !ACTIVE.includes(j.status));
}

// ---------- aksi ----------
$('#go').addEventListener('click', async () => {
  const p = effectivePreset();
  if (!p) return;
  const ids = [...state.files.values()].filter((f) => p.kinds.includes(f.kind)).map((f) => f.id);
  const h = !$('#heightWrap').hidden && $('#height').value ? Number($('#height').value) : null;
  $('#go').disabled = true;
  try {
    const res = await api('/api/jobs', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ files: ids, preset: p.id, max_height: h }),
    });
    res.jobs.forEach(upsertJob);
    if (res.errors.length) {
      setNotice(res.errors.map((e) => `${state.files.get(e.file_id)?.name || 'File'}: ${e.error}`).join(' '), 'error');
      $('#go').disabled = false;
      return;
    }
  } catch (e) {
    setNotice(e.message, 'error');
  }
  syncSetup();
});

$('#height').addEventListener('change', (e) => store.set('konvert.height', e.target.value));

$('#clear').addEventListener('click', async () => {
  if (!confirm('Hapus semua hasil yang sudah selesai? File yang belum diunduh akan hilang.')) return;
  const finished = [...state.jobs.values()].filter((j) => !ACTIVE.includes(j.status));
  await Promise.allSettled(finished.map((j) =>
    api(`/api/jobs/${j.id}`, { method: 'DELETE' }).then(() => removeJob(j.id))));
});

$('#picker').addEventListener('change', (e) => {
  [...e.target.files].forEach(upload);
  e.target.value = '';
});

// seret file ke mana saja di halaman
let depth = 0;
const hasFiles = (e) => e.dataTransfer && [...e.dataTransfer.types].includes('Files');
addEventListener('dragenter', (e) => { if (hasFiles(e)) { depth++; document.body.classList.add('dragging'); } });
addEventListener('dragleave', () => { depth = Math.max(0, depth - 1); if (!depth) document.body.classList.remove('dragging'); });
addEventListener('dragover', (e) => { if (hasFiles(e)) e.preventDefault(); });
addEventListener('drop', (e) => {
  if (!hasFiles(e)) return;
  e.preventDefault();
  depth = 0;
  document.body.classList.remove('dragging');
  [...e.dataTransfer.files].forEach(upload);
});

// ---------- koneksi real-time ----------
function connect() {
  const engine = $('#engine');
  const es = new EventSource('/api/events');
  es.onopen = () => { engine.textContent = engineText; delete engine.dataset.kind; };
  es.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.type === 'job') upsertJob(m.job);
    else if (m.type === 'removed') removeJob(m.id);
  };
  es.onerror = () => {
    engine.textContent = 'Terputus dari server. Jalankan ulang Konvert Studio, lalu muat ulang halaman.';
    engine.dataset.kind = 'error';
  };
}

async function init() {
  try {
    const info = await api('/api/info');
    state.presets = info.presets;
    engineText = info.ffmpeg.replace(/\s+Copyright.*$/, '');
    $('#engine').textContent = engineText;
    const sel = $('#height');
    sel.append(new Option('Ukuran asli', ''));
    for (const h of info.heights) sel.append(new Option(`${h}p`, String(h)));
    sel.value = store.get('konvert.height') || '';
    if (sel.value !== (store.get('konvert.height') || '')) sel.value = '';
    renderPresets();
    jobsChrome();
    connect();
  } catch (e) {
    const engine = $('#engine');
    engine.textContent = e.message;
    engine.dataset.kind = 'error';
  }
}
init();
