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
const JSON_HEADERS = { 'Content-Type': 'application/json' };
const KIND = { video: 'Video', audio: 'Audio', image: 'Gambar' };
const ACTIVE = ['queued', 'running'];

const state = {
  fmts: [], byId: new Map(), groups: [], profiles: [],
  fmt: null,              // id format terpilih
  values: {},             // values[idFormat] = nilai parameter; diingat per format
  last: {},               // format terakhir yang dipilih di tiap tab
  files: new Map(),       // file yang sudah terunggah
  pending: new Map(),     // unggahan berjalan atau gagal
  jobs: new Map(),
  pv: null,               // hasil pratinjau terakhir dari server
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
// Perintah untuk ditampilkan: tanda kutip ganda berlaku di cmd.exe, PowerShell, dan bash.
const quoteArg = (a) => /[\s"'&|<>^%()\[\];,*?]/.test(a) || a === '' ? `"${a.replace(/"/g, '\\"')}"` : a;
const shellJoin = (args) => args.map(quoteArg).join(' ');

async function api(path, opts) {
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `Galat ${r.status}`);
  return data;
}
const post = (path, body) => api(path, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body ?? {}) });

function setNotice(text, kind) {
  const n = $('#notice');
  n.textContent = text || '';
  if (kind) n.dataset.kind = kind; else delete n.dataset.kind;
}

async function openFolder(path, button) {
  const label = button.textContent;
  button.disabled = true;
  button.textContent = 'Membuka…';
  try {
    await post(path);
    button.textContent = 'Terkirim';
    setNotice('Permintaan buka folder terkirim.');
    setTimeout(() => {
      if (!button.isConnected) return;
      button.textContent = label;
      button.disabled = false;
    }, 1200);
  } catch (e) {
    button.textContent = label;
    button.disabled = false;
    setNotice(e.message, 'error');
  }
}

// ---------- format & nilai parameter ----------
const curFmt = () => state.byId.get(state.fmt);
const curVals = () => state.values[state.fmt];
const accepts = (fmt, f) => fmt.kinds.includes(f.kind) && (fmt.group !== 'audio' || f.has_audio);
const usableFiles = (fmt) => [...state.files.values()].filter((f) => accepts(fmt, f));
const usable = (fmt) => !state.files.size || usableFiles(fmt).length > 0;

function defaults(fmt) {
  const v = {};
  for (const s of fmt.params) v[s.id] = Array.isArray(s.default) ? [...s.default] : s.default;
  return v;
}

// Pastikan nilai (dari localStorage atau preset) cocok dengan opsi yang ada sekarang.
function sanitize(fmt, v = {}) {
  const d = defaults(fmt), out = {};
  for (const s of fmt.params) {
    let x = s.id in v ? v[s.id] : d[s.id];
    if (s.type === 'select' && !s.options.some((o) => o.v === String(x))) x = d[s.id];
    if (s.type === 'select') x = String(x);
    if (s.type === 'check') x = !!x;
    if (s.type === 'text') x = String(x ?? '');
    if (s.type === 'multi') {
      x = Array.isArray(x) ? x.filter((a) => s.options.some((o) => o.v === a)) : [];
      if (!x.length) x = [...d[s.id]];
    }
    out[s.id] = x;
  }
  return out;
}

const persist = () => store.set('konvert.v2', JSON.stringify({ fmt: state.fmt, values: state.values, last: state.last }));

function setFmt(id, values) {
  const fmt = state.byId.get(id);
  if (!fmt) return;
  state.fmt = id;
  state.last[fmt.group] = id;
  if (values) state.values[id] = sanitize(fmt, { ...defaults(fmt), ...values });
  persist();
  renderPanel();
}

// Bila format terpilih tidak cocok dengan file yang ada, pindah ke format yang cocok (tab yang sama dulu).
function ensureFmt() {
  const fmt = curFmt();
  if (fmt && usable(fmt)) return;
  const pick = state.fmts.find((f) => f.group === fmt?.group && usable(f)) || state.fmts.find(usable);
  if (pick) { state.fmt = pick.id; state.last[pick.group] = pick.id; persist(); }
}

// ---------- panel pengaturan ----------
function renderGroups() {
  const root = $('#groups');
  root.querySelectorAll('label').forEach((n) => n.remove());
  const cur = curFmt();
  for (const g of state.groups) {
    const list = state.fmts.filter((f) => f.group === g.id);
    if (!list.length) continue;
    const label = make('label');
    const input = make('input');
    input.type = 'radio';
    input.name = 'group';
    input.value = g.id;
    input.checked = cur?.group === g.id;
    input.disabled = !list.some(usable);
    input.addEventListener('change', () => {
      const ok = list.filter(usable);
      const remembered = ok.find((f) => f.id === state.last[g.id]);
      setFmt((remembered || ok[0]).id);
    });
    label.append(input, make('span', null, g.label));
    root.append(label);
  }
}

function renderFormats() {
  const root = $('#formats');
  root.replaceChildren();
  const cur = curFmt();
  for (const f of state.fmts.filter((x) => x.group === cur.group)) {
    const label = make('label', 'chip');
    const input = make('input');
    input.type = 'radio';
    input.name = 'fmt';
    input.value = f.id;
    input.checked = f.id === cur.id;
    input.disabled = !usable(f);
    input.addEventListener('change', () => setFmt(f.id));
    label.append(input, make('span', null, f.label));
    root.append(label);
  }
  $('#fmtDesc').textContent = cur.desc;
}

function controlRow(spec) {
  const vals = curVals();
  const lab = make('span', 'lab', spec.label);
  if (spec.help) lab.append(make('small', null, spec.help));
  const set = (v) => { vals[spec.id] = v; persist(); schedulePreview(); renderProfiles(); };
  let row, el;
  if (spec.type === 'select') {
    row = make('label', 'row');
    el = make('select');
    for (const o of spec.options) el.append(new Option(o.l, o.v));
    el.value = vals[spec.id];
    el.addEventListener('change', () => set(el.value));
    row.append(lab, el);
  } else if (spec.type === 'check') {
    row = make('label', 'row check');
    el = make('input');
    el.type = 'checkbox';
    el.checked = vals[spec.id];
    el.addEventListener('change', () => set(el.checked));
    row.append(el, lab);
  } else if (spec.type === 'multi') {
    row = make('div', 'row multi');
    row.setAttribute('role', 'group');
    row.setAttribute('aria-label', spec.label);
    const opts = make('div', 'multi-opts');
    for (const o of spec.options) {
      const l = make('label');
      const cb = make('input');
      cb.type = 'checkbox';
      cb.checked = vals[spec.id].includes(o.v);
      cb.addEventListener('change', () => {
        const next = spec.options.map((x) => x.v).filter((v) => v === o.v ? cb.checked : vals[spec.id].includes(v));
        if (!next.length) { cb.checked = true; return; }  // minimal satu resolusi
        set(next);
      });
      l.append(cb, make('span', null, o.l));
      opts.append(l);
    }
    row.append(lab, opts);
  } else {
    row = make('label', 'row stack');
    el = make('input');
    el.type = 'text';
    el.value = vals[spec.id];
    el.placeholder = spec.placeholder || spec.default || '';
    el.spellcheck = false;
    el.addEventListener('input', () => set(el.value));
    row.append(lab, el);
  }
  return row;
}

function renderParams() {
  const fmt = curFmt();
  const main = fmt.params.filter((s) => !s.adv), adv = fmt.params.filter((s) => s.adv);
  $('#params').replaceChildren(...main.map(controlRow));
  $('#advParams').replaceChildren(...adv.map(controlRow));
  $('#advWrap').hidden = !adv.length;
}

function renderPanel() {
  renderGroups();
  renderFormats();
  renderParams();
  renderProfiles();
  schedulePreview();
}

// ---------- preset cepat ----------
const sameValue = (a, b) => JSON.stringify(a) === JSON.stringify(b);

function renderProfiles() {
  const root = $('#profiles');
  root.replaceChildren();
  if (!state.profiles.length) {
    root.append(make('p', 'empty-note', 'Belum ada preset. Atur format, lalu klik "Simpan pengaturan ini".'));
    return;
  }
  for (const p of state.profiles) {
    const fmt = state.byId.get(p.format);
    if (!fmt) continue;  // format tidak ada di ffmpeg ini
    const chip = make('span', 'pchip');
    const b = make('button', 'pbtn', p.label);
    b.type = 'button';
    const on = p.format === state.fmt && fmt.params.every((s) => sameValue(p.params[s.id], curVals()[s.id]));
    b.setAttribute('aria-pressed', String(on));
    b.disabled = !usable(fmt);
    b.addEventListener('click', () => setFmt(p.format, p.params));
    const x = make('button', 'px', '×');
    x.type = 'button';
    x.setAttribute('aria-label', `Hapus preset ${p.label}`);
    x.addEventListener('click', async () => {
      if (!confirm(`Hapus preset "${p.label}"?`)) return;
      try {
        state.profiles = (await api(`/api/profiles/${p.id}`, { method: 'DELETE' })).profiles;
        renderProfiles();
      } catch (e) { setNotice(e.message, 'error'); }
    });
    chip.append(b, x);
    root.append(chip);
  }
}

$('#saveToggle').addEventListener('click', () => {
  const form = $('#saveForm');
  form.hidden = !form.hidden;
  if (!form.hidden) $('#profileName').focus();
});
$('#saveCancel').addEventListener('click', () => { $('#saveForm').hidden = true; });
$('#saveForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  const label = $('#profileName').value.trim();
  if (!label) return;
  try {
    state.profiles = (await post('/api/profiles', { label, format: state.fmt, params: curVals() })).profiles;
    $('#profileName').value = '';
    $('#saveForm').hidden = true;
    renderProfiles();
    setNotice(`Preset "${label}" tersimpan.`);
  } catch (err) { setNotice(err.message, 'error'); }
});

// ---------- pratinjau perintah & lokasi hasil ----------
let pvSeq = 0, pvTimer = 0;
function schedulePreview() {
  clearTimeout(pvTimer);
  pvTimer = setTimeout(runPreview, 150);
}

async function runPreview() {
  const fmt = curFmt();
  if (!fmt) return;
  const seq = ++pvSeq;
  let pv;
  try {
    pv = await post('/api/preview', { files: usableFiles(fmt).map((f) => f.id), format: fmt.id, params: curVals() });
  } catch (e) {
    pv = { error: e.message };
  }
  if (seq !== pvSeq) return;  // sudah ada pratinjau yang lebih baru
  state.pv = pv;
  $('#cmdText').textContent = pv.command ? shellJoin(pv.command) : '';
  $('#outPreview').replaceChildren(...(pv.display
    ? ['Hasil: ', Object.assign(make('code'), { textContent: pv.display })] : []));
  $('#warns').replaceChildren(...(pv.warnings || []).map((w) => make('li', null, w)));
  syncGo();
}

function syncGo() {
  const fmt = curFmt();
  const ok = fmt ? usableFiles(fmt) : [];
  const skipped = state.files.size - ok.length;
  const go = $('#go');
  go.disabled = ok.length === 0 || !!state.pv?.error;
  go.textContent = ok.length > 1 ? `Konversi ${ok.length} file` : 'Konversi';
  if (state.pv?.error) setNotice(state.pv.error, 'error');
  else if (!state.files.size) setNotice('Tambahkan file dulu.');
  else if (skipped) setNotice(`${skipped} file dilewati karena tidak cocok dengan format ini.`);
  else setNotice('');
}

$('#copyCmd').addEventListener('click', async (e) => {
  const btn = e.currentTarget, text = $('#cmdText').textContent;
  try {
    await navigator.clipboard.writeText(text);
    btn.textContent = 'Tersalin';
  } catch {
    const range = document.createRange();
    range.selectNodeContents($('#cmdText'));
    getSelection().removeAllRanges();
    getSelection().addRange(range);
    btn.textContent = 'Teks terpilih, tekan Ctrl+C';
  }
  setTimeout(() => { btn.textContent = 'Salin perintah'; }, 2000);
});

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
function filesChanged() {
  renderFiles();
  ensureFmt();
  renderPanel();
}

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

// ---------- hasil ----------
function statusText(j) {
  switch (j.status) {
    case 'queued': return 'Menunggu giliran';
    case 'running': {
      const speed = j.speed && j.speed !== 'N/A' ? `, kecepatan ${j.speed}` : '';
      return j.percent < 1 ? `Memproses…${speed}` : `${Math.floor(j.percent)}%${speed}`;
    }
    case 'done': return `Selesai, ${fmtSize(j.size)}`;
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
  mid.append(strip, make('div', 'status'), make('div', 'job-path'));
  const err = make('details', 'job-err');
  err.hidden = true;
  err.append(make('summary', null, 'Pesan dari ffmpeg'), make('pre'));
  li.append(main, mid, make('div', 'actions'), err);
  return li;
}

function buildActions(j) {
  const out = [];
  if (j.status === 'done') {
    if (j.kind === 'file') {
      const a = make('a', 'btn solid', 'Unduh');
      a.href = `/api/jobs/${j.id}/download`;
      a.download = j.out_name;
      out.push(a);
    }
    const open = make('button', 'btn', 'Buka folder');
    open.type = 'button';
    open.onclick = () => openFolder(`/api/jobs/${j.id}/reveal`, open);
    out.push(open);
  }
  let b;
  if (ACTIVE.includes(j.status)) {
    b = make('button', 'btn', 'Batal');
    b.onclick = () => post(`/api/jobs/${j.id}/cancel`).catch((e) => setNotice(e.message, 'error'));
  } else {
    b = make('button', 'icon', '×');
    b.setAttribute('aria-label', `Hapus ${j.name} dari daftar`);
    b.title = 'Hapus dari daftar. File hasil di folder tidak ikut terhapus.';
    b.onclick = () => api(`/api/jobs/${j.id}`, { method: 'DELETE' })
      .then(() => removeJob(j.id)).catch((e) => setNotice(e.message, 'error'));
  }
  b.type = 'button';
  out.push(b);
  return out;
}

function upsertJob(j) {
  state.jobs.set(j.id, j);
  let li = $(`.job[data-id="${j.id}"]`);
  if (!li) { li = jobRow(j.id); $('#jobs').append(li); }
  $('.job-name', li).textContent = j.name;
  $('.job-name', li).title = j.name;
  $('.job-fmt', li).textContent = j.label + (j.summary ? `, ${j.summary}` : '');
  const strip = $('.strip', li);
  strip.style.setProperty('--p', `${j.percent}%`);
  strip.setAttribute('aria-valuenow', String(Math.round(j.percent)));
  strip.setAttribute('aria-label', `Progres ${j.name}`);
  $('.status', li).textContent = statusText(j);
  $('.job-path', li).textContent = j.status === 'done'
    ? `${j.rel}${j.kind === 'dir' ? `/ (${j.files} file)` : ''}` : '';
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
  const fmt = curFmt();
  if (!fmt) return;
  $('#go').disabled = true;
  try {
    const res = await post('/api/jobs', { files: usableFiles(fmt).map((f) => f.id), format: fmt.id, params: curVals() });
    res.jobs.forEach(upsertJob);
    if (res.errors.length) {
      setNotice(res.errors.map((e) => e.error).join(' '), 'error');
      $('#go').disabled = false;
      return;
    }
  } catch (e) {
    setNotice(e.message, 'error');
  }
  syncGo();
});

$('#clear').addEventListener('click', () => {
  [...state.jobs.values()].filter((j) => !ACTIVE.includes(j.status)).forEach((j) =>
    api(`/api/jobs/${j.id}`, { method: 'DELETE' }).then(() => removeJob(j.id)).catch(() => {}));
});

$('#openOut').addEventListener('click', (e) => openFolder('/api/open-out', e.currentTarget));

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
    state.fmts = info.formats;
    state.byId = new Map(info.formats.map((f) => [f.id, f]));
    state.groups = info.groups;
    state.profiles = info.profiles;
    engineText = info.ffmpeg.replace(/\s+Copyright.*$/, '');
    $('#engine').textContent = engineText;
    $('#outDir').textContent = info.out_dir;
    if (info.profile_warnings.length) setNotice(info.profile_warnings.join(' '), 'error');

    let saved = {};
    try { saved = JSON.parse(store.get('konvert.v2') || '{}'); } catch { /* abaikan */ }
    for (const f of state.fmts) state.values[f.id] = sanitize(f, saved.values?.[f.id]);
    state.last = saved.last || {};
    state.fmt = state.byId.has(saved.fmt) ? saved.fmt : (state.byId.has('mp4-h264') ? 'mp4-h264' : state.fmts[0].id);
    state.last[curFmt().group] = state.fmt;

    renderFiles();
    renderPanel();
    jobsChrome();
    connect();
  } catch (e) {
    const engine = $('#engine');
    engine.textContent = e.message;
    engine.dataset.kind = 'error';
  }
}
init();
