/* ============================================================= *
 *  Anchor — Import Visibility — static web build
 *  Vanilla JS single-page app. Pyodide runs the untouched Phase-3
 *  pipeline in-browser; data never leaves the device.
 * ============================================================= */

const Anchor = (() => {
  'use strict';

  const LS_CTX = 'anchor:ctx';
  const LS_PAGE = 'anchor:page';
  const LS_THEME = 'anchor:theme';
  const LS_NOTES = 'anchor:notes';

  const PAGES = [
    { id: 'Action Centre', icon: 'M13 2L3 14h9l-1 8 10-12h-9l1-8z' },
    { id: 'PO Journey', icon: 'M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5' },
    { id: 'Shipment Visibility', icon: 'M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8zM15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0z' },
    { id: 'Risk & Exposure', icon: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10zM9 12l2 2 4-4' },
    { id: 'Data Quality', icon: 'M9 12l2 2 4-4M7.86 2h8.28L22 7.86v8.28L16.14 22H7.86L2 16.14V7.86z' },
    { id: 'Thresholds & Refresh', icon: 'M21 12a9 9 0 1 1-9-9M21 3v6h-6M3 12h6' },
  ];

  const FRESH_DAYS = 3;
  const PRIORITY_ORDER = ['Critical', 'Urgent', 'Data Review', 'Monitor'];
  const DEFAULT_THRESHOLDS = {
    LC: { India: [45, 30], ASEAN: [60, 45], ChinaEA: [75, 60], Europe: [120, 90] },
    ETD: { India: [30, 20], ASEAN: [40, 30], ChinaEA: [55, 45], Europe: [90, 75] },
  };

  const FOLLOWUP_MAP = [
    ['RDD missing', 'Confirm RDD in BD Tracker; urgency cannot be assessed.', 'Planning data owner'],
    ['Route unknown', 'Validate source country/route mapping before applying thresholds.', 'Planning master-data owner'],
    ['Status complete but open quantity remains', 'Reconcile receipt status and Open PO quantity before treating the PO as closed.', 'Planning + Logistics reconciliation'],
    ['ETA later than RDD', 'Confirm recovery plan and assess planning/production impact.', 'Planning + Logistics review'],
    ['ETA within', 'Obtain updated shipment ETA from logistics/origin.', 'Origin logistics / relevant import coordination owner'],
    ['LC', 'Confirm LC completion with Bangladesh Logistics / Order Management.', 'Bangladesh Logistics / Order Management'],
    ['ETD', 'Obtain booking / confirmed departure schedule from origin logistics.', 'Origin logistics / supplier / import coordination owner'],
    ['OBL', 'Confirm documentation with the document coordination team.', 'Document coordination team'],
    ['Final', 'Confirm final document receipt with the document coordination team.', 'Document coordination team'],
    ['No BD', 'Validate whether the process is unstarted or the data is missing.', 'BD Tracker source-data owner'],
    ['No Eagle', 'Validate whether the shipment process is unstarted or data is missing.', 'Eagle Eye source-data owner'],
    ['No EE', 'Validate whether the shipment process is unstarted or data is missing.', 'Eagle Eye source-data owner'],
  ];

  /* ---------------- state ---------------- */
  const state = {
    ctx: null,         // full processing context (parsed JSON)
    page: 'Action Centre',
    theme: 'light',
    notes: {},
    po: null,          // selected PO
    severityFilter: 'All',
    searchQ: '',
    uploads: {},       // key -> {file, name}
    pyodide: null,
    engine: null,      // web_main module proxy
    busy: false,
  };

  const $ = (sel, root) => (root || document).querySelector(sel);

  function esc(v) {
    return String(v == null ? '' : v)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function toast(msg, isError) {
    const t = $('#toast');
    t.textContent = msg;
    t.classList.toggle('error', !!isError);
    t.classList.add('show');
    clearTimeout(t._h);
    t._h = setTimeout(() => t.classList.remove('show'), 3200);
  }

  /* ---------------- persistence ---------------- */
  function loadLS() {
    try {
      state.ctx = JSON.parse(localStorage.getItem(LS_CTX) || 'null');
      state.page = localStorage.getItem(LS_PAGE) || 'Action Centre';
      state.theme = localStorage.getItem(LS_THEME) || 'light';
      state.notes = JSON.parse(localStorage.getItem(LS_NOTES) || '{}');
    } catch (e) {
      state.ctx = null; state.notes = {};
    }
  }
  function saveCtx() {
    try { localStorage.setItem(LS_CTX, JSON.stringify(state.ctx)); } catch (e) { /* quota */ }
  }
  function saveNotes() {
    try { localStorage.setItem(LS_NOTES, JSON.stringify(state.notes)); } catch (e) {}
  }
  function notesCount() { return Object.keys(state.notes || {}).length; }

  /* ---------------- data helpers ---------------- */
  const headers = () => (state.ctx && state.ctx.master_headers) || [];
  const idx = (col) => { const h = headers(); const i = h.indexOf(col); return i >= 0 ? i : -1; };

  function isMissing(v) {
    return v === null || v === undefined || (typeof v === 'number' && v !== v) ||
      (typeof v === 'string' && !v.trim()) || v === '-';
  }

  function activeRows() {
    const rows = (state.ctx && state.ctx.master) || [];
    const p = idx('Population Status');
    if (p < 0) return rows;
    return rows.filter(r => String(r[p] || '').trim().toLowerCase() === 'active');
  }

  function distinctPoids(rows) {
    const p = idx('Purchasing Document');
    if (p < 0) return 0;
    return new Set(rows.map(r => r[p]).filter(v => v !== null && v !== undefined && String(v).trim())).size;
  }

  function fmtQ(v) {
    if (v === null || v === undefined) return '-';
    const n = Number(v);
    if (Number.isNaN(n)) return isMissing(v) ? '-' : String(v);
    return n.toLocaleString('en-GB', { maximumFractionDigits: 0 });
  }

  function parseISO(s) {
    if (isMissing(s)) return null;
    const t = Date.parse(String(s));
    return Number.isNaN(t) ? null : new Date(t);
  }

  function fmtDate(v) {
    const d = parseISO(v);
    if (!d) return '-';
    const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    return `${String(d.getDate()).padStart(2, '0')} ${months[d.getMonth()]} ${d.getFullYear()}`;
  }

  function rddOffset(v) {
    const d = parseISO(v);
    if (!d) return null;
    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const rdd = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    return Math.round((rdd - today) / 86400000);
  }

  function rddHorizon(days) {
    if (days === null || days === undefined) return 'Unknown';
    if (days < 0) return 'Overdue';
    if (days <= 7) return '0-7d';
    if (days <= 30) return '8-30d';
    if (days <= 60) return '31-60d';
    return '>60d';
  }

  function qtyByUnit(rows, populationOnly = true) {
    const qi = idx('Still to be Delivered (Qty)');
    const ui = idx('Order Unit');
    const pi = idx('Population Status');
    if (qi < 0 || ui < 0) return {};
    const out = {};
    for (const r of rows) {
      if (populationOnly && pi >= 0 && String(r[pi] || '').trim() !== 'Active') continue;
      const q = Number(r[qi]); if (Number.isNaN(q)) continue;
      const unit = String(r[ui] || '').trim() || 'N/A';
      out[unit] = (out[unit] || 0) + q;
    }
    return out;
  }

  function suggestedFollowup(reason) {
    const r = String(reason || '');
    for (const [pat, action, owner] of FOLLOWUP_MAP) {
      if (r.toLowerCase().includes(pat.toLowerCase())) return { action, owner };
    }
    if (r && !/urgent|critical/i.test(r)) return { action: 'Review the PO record and confirm the next required milestone.', owner: r.trim() };
    return { action: 'Review the PO in full context before deciding.', owner: 'Planning team' };
  }

  function dataConfidence(row, fresh = true, inRec = false) {
    const has = (col) => { const i = idx(col); return i >= 0 && !isMissing(row[i]); };
    const rdd = has('RDD'); const country = has('Import Country');
    const reason = String(idx('Primary Reason') >= 0 ? (row[idx('Primary Reason')] || '') : '');
    if (!rdd || !country) return 'Low';
    if (inRec) return 'Low';
    if (!fresh) return 'Low';
    if (/Route unknown|RDD missing/.test(reason)) return 'Low';
    const hasEta = has('BD Tracker ETA') || has('EE ETA');
    if (!hasEta) return 'Medium';
    return 'High';
  }

  function missingCount(col) {
    const i = idx(col);
    if (i < 0) return null;
    const rows = (state.ctx && state.ctx.master) || [];
    return rows.filter(r => i < r.length && isMissing(r[i])).length;
  }

  /* ---------------- theme ---------------- */
  function applyTheme() {
    document.documentElement.setAttribute('data-theme', state.theme);
    localStorage.setItem(LS_THEME, state.theme);
    $('#themeToggle').innerHTML = state.theme === 'dark'
      ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="17" height="17"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>'
      : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="17" height="17"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';
  }

  /* ---------------- freshness ---------------- */
  function freshnessState(meta) {
    const refreshed = meta && meta.refreshed_at;
    if (!refreshed) return { state: 'stale', note: 'no refresh recorded' };
    const dt = parseISO(refreshed);
    if (!dt) return { state: 'stale', note: 'refresh time unreadable' };
    const days = Math.floor((Date.now() - dt.getTime()) / 86400000);
    if (days > FRESH_DAYS) return { state: 'stale', note: `source files ${days} days old` };
    const m = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    return { state: 'current', note: `refreshed ${String(dt.getDate()).padStart(2,'0')} ${m[dt.getMonth()]} ${dt.getFullYear()} ${String(dt.getHours()).padStart(2,'0')}:${String(dt.getMinutes()).padStart(2,'0')}` };
  }

  function renderFreshBadge() {
    const meta = state.ctx ? state.ctx.meta : {};
    const fs = freshnessState(meta);
    const el = $('#freshBadge');
    el.textContent = fs.note;
    el.classList.toggle('stale', fs.state === 'stale');
  }

  /* ---------------- screens ---------------- */
  const screens = {};
  function showScreen(name) {
    document.querySelectorAll('.screen').forEach(s => s.hidden = true);
    $('#screen-app').hidden = true;
    const map = { welcome: 'screen-welcome', restore: 'screen-restore', upload: 'screen-upload', app: 'screen-app' };
    const el = $('#' + map[name]);
    if (el) el.hidden = false;
    if (name === 'app') {
      renderNav();
      renderPage();
      renderFreshBadge();
    }
    window.scrollTo(0, 0);
  }

  function go(name) {
    screens[name] && screens[name]();
    if (name === 'app') localStorage.setItem(LS_PAGE, state.page);
    showScreen(name);
  }

  /* ---------------- nav ---------------- */
  function renderNav() {
    const nav = $('#sideNav');
    nav.innerHTML = PAGES.map(p =>
      `<button class="nav-item ${state.page === p.id ? 'active' : ''}" data-page="${esc(p.id)}">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="${p.icon}"/></svg>
        <span>${esc(p.id)}</span>
      </button>`).join('');
    nav.querySelectorAll('.nav-item').forEach(b =>
      b.addEventListener('click', () => { state.page = b.dataset.page; go('app'); }));
  }

  /* ---------------- shared UI builders ---------------- */
  function section(title, legend, extra) {
    return `<div class="section">
      <div class="section-head"><div><div class="body-md">${esc(title)}</div>
      ${legend ? `<div class="section-sub">${legend}</div>` : ''}</div>${extra || ''}</div>`;
  }
  function closeSection() { return `</div>`; }

  function kpiRow(items) {
    const cls = { crit: 'sev-crit', urg: 'sev-urg', mon: 'sev-mon', dr: 'sev-data', plain: '' };
    return `<div class="kpi-grid app">${items.map(([label, value, kind]) =>
      `<div class="kpi ${cls[kind] || ''}"><div class="kpi-value">${value}</div>
       <div class="kpi-label">${esc(label)}</div></div>`).join('')}</div>`;
  }

  function sevChip(v) {
    const c = String(v);
    const cls = c === 'Critical' ? 'sev-Critical' : c === 'Urgent' ? 'sev-Urgent'
      : c === 'Monitor' ? 'sev-Monitor' : 'sev-Data';
    return `<span class="sev-chip ${cls}">${esc(c)}</span>`;
  }

  function confPill(v) {
    const k = String(v || '').toLowerCase();
    const cls = k === 'high' ? 'sev-Complete' : k === 'medium' ? 'sev-Partial' : 'sev-Missing';
    return `<span class="sev-chip ${cls}">${esc(v || '')}</span>`;
  }

  function hbar(items) {
    if (!items.length) return '<div class="empty">No data</div>';
    const mx = Math.max(...items.map(([, v]) => v)) || 1;
    return `<div>${items.map(([lbl, v]) => {
      const w = Math.max(4, 100 * v / mx);
      return `<div style="display:grid;grid-template-columns:minmax(120px,1.4fr) 44px 2fr;gap:10px;align-items:center;margin:7px 0">
        <div style="font-size:12.8px;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(lbl)}</div>
        <div style="font-size:13.5px;font-weight:800;text-align:right;font-variant-numeric:tabular-nums">${v}</div>
        <div class="bar"><span style="width:${w.toFixed(1)}%"></span></div>
      </div>`; }).join('')}</div>`;
  }

  function dataTable(cols, rows, opts = {}) {
    if (!rows.length) return `<div class="empty">${esc(opts.empty || 'No rows')}</div>`;
    const dateCols = new Set((opts.dateCols || []).map(c => String(c)));
    const head = cols.map((c, i) =>
      `<th class="${opts.numCols && opts.numCols.includes(i) ? 'num' : ''}">${esc(c)}</th>`).join('');
    const body = rows.map((r, ri) => {
      const sev = opts.sevIndex >= 0 ? String(r[opts.sevIndex] || '') : '';
      const rowCls = opts.sevIndex >= 0 ? `row-${sev.toLowerCase().replace(/\s+/g, '-')}` : '';
      const tds = cols.map((c, i) => {
        let v = i < r.length ? r[i] : '';
        if (dateCols.has(String(c))) v = fmtDate(v);
        else if (opts.numCols && opts.numCols.includes(i)) v = fmtQ(v);
        else v = esc(v);
        return `<td class="${opts.numCols && opts.numCols.includes(i) ? 'num' : ''}">${v}</td>`;
      }).join('');
      return `<tr class="${rowCls}">${tds}</tr>`;
    }).join('');
    return `<div class="table-wrap"><table class="data"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  /* ---------------- exports ---------------- */
  function download(name, blob) {
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 4000);
  }

  function buildCsv(headersArr, rows) {
    const lines = [];
    const meta = (state.ctx && state.ctx.meta) || {};
    lines.push('# Anchor - controlled export');
    lines.push(`# Anchor version: ${meta.version || '-'}`);
    lines.push(`# Master refresh: ${meta.refreshed_at || '-'}`);
    lines.push(`# Pipeline / threshold version: ${meta.version || '-'}`);
    lines.push('');
    lines.push(headersArr.map(h => csvCell(h)).join(','));
    for (const r of rows) lines.push(r.map(v => csvCell(v)).join(','));
    return '\ufeff' + lines.join('\n');
  }

  function csvCell(v) {
    const s = String(v === null || v === undefined ? '' : v);
    return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }

  function exportCsv(filename, headersArr, rows) {
    download(filename, new Blob([buildCsv(headersArr, rows)], { type: 'text/csv;charset=utf-8' }));
    toast(`Exported ${filename}`);
  }

  async function exportMasterXlsx() {
    try {
      if (!state.engine) { toast('Engine not ready', true); return; }
      const bytes = state.engine.build_master_xlsx(state.pyodide.toPy(state.ctx));
      download('anchor_master.xlsx', new Blob([bytes.toJs().buffer], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }));
      toast('Exported anchor_master.xlsx');
    } catch (e) { toast('Export failed: ' + e.message, true); }
  }

  /* ---------------- Pyodide engine ---------------- */
  async function bootEngine() {
    if (state.engine) return;
    const pyFiles = [
      'web_main.py', 'clean_open_po.py', 'clean_bd_tracker.py', 'clean_eagle_eye.py',
      'clean_merge.py', 'rule_engine.py',
    ];
    const root = 'py';
    state.pyodide = await loadPyodide();
    await state.pyodide.loadPackage('micropip');
    const micropip = state.pyodide.pyimport('micropip');
    await micropip.install('openpyxl');
    state.pyodide.FS.mkdir('/scripts');
    for (const f of pyFiles) {
      const url = f === 'web_main.py' ? `${root}/${f}` : `${root}/scripts/${f}`;
      const res = await fetch(url);
      if (!res.ok) throw new Error(`Cannot load ${url}`);
      const code = await res.text();
      if (f === 'web_main.py') {
        state.pyodide.FS.writeFile('/web_main.py', code);
      } else {
        state.pyodide.FS.writeFile(`/scripts/${f}`, code);
      }
    }
    state.pyodide.runPython(`import sys; sys.path.insert(0, '/'); sys.path.insert(0, '/scripts')`);
    state.engine = state.pyodide.pyimport('web_main');
  }

  /* ---------------- upload screen ---------------- */
  function renderDropzones() {
    const defs = [
      ['open', 'Open PO', 'Defines the active population of POs. (required)'],
      ['tracker', 'BD Tracker', 'Import milestones: LC, SI, RDD, ETA/ETD, OBL, final docs. (required)'],
      ['ee', 'Eagle Eye', 'Container & shipment visibility: From, DDPO, container, tracking. (required)'],
      ['threshold', 'Country Thresholds', 'Agreed timing rules per route. (optional)'],
    ];
    const dz = $('#dropzones');
    dz.innerHTML = defs.map(([key, label, hint]) =>
      `<div class="dz" id="dz-${key}">
        <div class="dz-label">${esc(label)} <span class="chip chip-green">${key === 'threshold' ? 'optional' : 'required'}</span></div>
        <div class="dz-hint">${esc(hint)}</div>
        <input type="file" class="dz-input" id="input-${key}" accept=".xlsx,.xlsm,.xlsb">
        <div class="dz-name empty" id="name-${key}">No file selected</div>
        <button class="dz-clear" id="clear-${key}" title="Remove file" hidden>&times;</button>
      </div>`).join('');

    for (const [key] of defs) {
      const input = $('#input-' + key);
      const zone = $('#dz-' + key);
      const nameEl = $('#name-' + key);
      const clearBtn = $('#clear-' + key);
      input.addEventListener('change', () => {
        const f = input.files && input.files[0];
        if (!f) return;
        state.uploads[key] = { file: f, name: f.name };
        nameEl.textContent = f.name;
        nameEl.classList.remove('empty');
        zone.classList.add('has-file');
        clearBtn.hidden = false;
        updateProcessBtn();
      });
      ['dragover', 'dragenter'].forEach(ev => zone.addEventListener(ev, e => { e.preventDefault(); zone.classList.add('dragover'); }));
      ['dragleave', 'drop'].forEach(ev => zone.addEventListener(ev, e => e.preventDefault()));
      zone.addEventListener('drop', e => {
        e.preventDefault(); zone.classList.remove('dragover');
        const f = e.dataTransfer.files && e.dataTransfer.files[0];
        if (!f) return;
        input.files = e.dataTransfer.files;
        state.uploads[key] = { file: f, name: f.name };
        nameEl.textContent = f.name;
        nameEl.classList.remove('empty');
        zone.classList.add('has-file');
        clearBtn.hidden = false;
        updateProcessBtn();
      });
      clearBtn.addEventListener('click', e => {
        e.stopPropagation();
        delete state.uploads[key];
        input.value = '';
        nameEl.textContent = 'No file selected';
        nameEl.classList.add('empty');
        zone.classList.remove('has-file');
        clearBtn.hidden = true;
        updateProcessBtn();
      });
    }
  }

  function updateProcessBtn() {
    const ok = state.uploads.open && state.uploads.tracker && state.uploads.ee;
    $('#processBtn').disabled = !ok || state.busy;
  }

  async function process() {
    if (state.busy) return;
    state.busy = true;
    updateProcessBtn();
    const prog = $('#uploadProgress');
    prog.hidden = false;
    prog.innerHTML = `<div class="progress-wrap">
      <div class="progress-label"><span>Booting engine</span><span id="pct">0%</span></div>
      <div class="progress-track"><div class="progress-fill" id="fill"></div></div></div>`;
    const setPct = (pct, label) => {
      $('#fill').style.width = pct + '%';
      $('#pct').textContent = `${pct}%`;
      const lbl = $('#uploadProgress .progress-label span:first-child');
      if (lbl && label) lbl.textContent = label;
    };
    try {
      setPct(6, 'Loading Python engine (Pyodide)...');
      await bootEngine();

      const bytes = {};
      for (const key of ['open', 'tracker', 'ee']) {
        setPct(14 + ['open','tracker','ee'].indexOf(key) * 14, `Reading ${state.uploads[key].name}...`);
        const buf = await state.uploads[key].file.arrayBuffer();
        bytes[key] = { data: new Uint8Array(buf), name: state.uploads[key].name };
      }

      let thBytes = null, thName = '';
      if (state.uploads.threshold) {
        setPct(56, 'Reading Country Thresholds...');
        const buf = await state.uploads.threshold.file.arrayBuffer();
        thBytes = new Uint8Array(buf);
        thName = state.uploads.threshold.name;
      }

      setPct(62, 'Cleaning and merging sources...');
      const py = state.pyodide.toPy({
        open: { name: bytes.open.name, data: bytes.open.data, mtime: state.uploads.open.file.lastModified / 1000 },
        tracker: { name: bytes.tracker.name, data: bytes.tracker.data, mtime: state.uploads.tracker.file.lastModified / 1000 },
        ee: { name: bytes.ee.name, data: bytes.ee.data, mtime: state.uploads.ee.file.lastModified / 1000 },
      });
      const thArg = thBytes ? state.pyodide.toPy(thBytes) : null;

      setPct(78, 'Building control sheets...');
      const result = await state.engine.process_uploads(py, thArg, thName);
      setPct(92, 'Serialising view...');
      state.ctx = result.toJs({ dict_converter: Object.fromEntries });
      state.ctx.is_restored = false;
      saveCtx();
      setPct(100, 'Done');
      setTimeout(() => { prog.hidden = true; state.page = 'Action Centre'; go('app'); }, 350);
    } catch (e) {
      console.error(e);
      prog.hidden = true;
      toast('Could not generate the view: ' + e.message, true);
    } finally {
      state.busy = false;
      updateProcessBtn();
    }
  }

  /* ---------------- restore screen ---------------- */
  function renderRestore() {
    const meta = state.ctx.meta || {};
    const fs = freshnessState(meta);
    $('#restoreNote').textContent = fs.note;
    $('#restoreKpis').innerHTML = kpiRow([
      ['Open POs', meta.open_po_count || '-', 'plain'],
      ['Pipeline', 'v' + (meta.version || '-'), 'plain'],
      ['Last refreshed', String(meta.refreshed_at || '-').slice(0, 16), 'plain'],
    ]);
  }

  function restore() {
    state.ctx = JSON.parse(localStorage.getItem(LS_CTX) || 'null');
    if (!state.ctx || !(state.ctx.master || []).length) {
      toast('No saved view found', true);
      go('welcome');
      return;
    }
    state.ctx.is_restored = true;
    state.page = 'Action Centre';
    saveCtx();
    go('app');
  }

  function confirmClear() { $('#clearModal').hidden = false; }
  function cancelClear() { $('#clearModal').hidden = true; }
  function doClear() {
    ['anchor:ctx', 'anchor:page', 'anchor:notes'].forEach(k => localStorage.removeItem(k));
    state.ctx = null; state.notes = {}; state.page = 'Action Centre';
    $('#clearModal').hidden = true;
    go('welcome');
  }

  /* ---------------- search ---------------- */
  function globalSearch(keyPrefix) {
    const rows = state.ctx.master || [];
    const fields = [];
    for (const c of ['Purchasing Document', 'Short Text', 'Material (AGI)', 'Container No.']) {
      const i = idx(c); if (i >= 0) fields.push(i);
    }
    const poIdx = idx('Purchasing Document');
    const q = (state.searchQ || '').trim().toLowerCase();
    if (!q) return null;
    const cands = new Set();
    for (const r of rows) {
      const blob = fields.map(i => (i < r.length ? r[i] : '')).join(' | ');
      if (blob.toLowerCase().includes(q) && poIdx >= 0 && poIdx < r.length) cands.add(String(r[poIdx]));
    }
    return [...cands].sort();
  }

  /* ---------------- Action Centre ---------------- */
  function actionCentre() {
    const rows = activeRows();
    if (!rows.length) return emptyView('No open actions', 'All POs are complete or monitor-only.');

    const poIdx = idx('Purchasing Document');
    const sev = {};
    const si = idx('Urgency');
    for (const r of rows) { const v = String(r[si] || ''); sev[v] = (sev[v] || 0) + 1; }
    const openCount = distinctPoids(rows);
    const noBd = missingCount('BD Tracker ETA') || 0;
    const noEe = missingCount('EE ETA') || 0;

    let html = viewHead('Action Centre', 'Prioritised open import POs by severity and requested delivery date.');

    html += kpiRow([
      ['Active Open POs', openCount, 'plain'],
      ['Critical', sev['Critical'] || 0, 'crit'],
      ['Urgent', sev['Urgent'] || 0, 'urg'],
      ['Data Review', sev['Data Review'] || 0, 'dr'],
      ['Monitor', sev['Monitor'] || 0, 'mon'],
      ['No BD record', noBd, 'plain'],
      ['No EE evidence', noEe, 'plain'],
    ]);

    html += section('Open requirement by unit', 'KG and litre (\'L\') units are shown separately - never combined.');
    const qty = qtyByUnit(rows, true);
    if (Object.keys(qty).length) {
      html += `<div class="qty-grid">${Object.entries(qty).map(([unit, v]) =>
        `<div class="qty-card"><div class="qty-value">${fmtQ(v)}</div><div class="qty-unit">Open PO requirement · ${esc(unit)}</div></div>`).join('')}</div>`;
    } else {
      html += '<div class="empty">No open quantity</div>';
    }
    html += closeSection();

    html += section('Priority actions', 'Sorted Critical > Urgent > Data Review > Monitor, then RDD ascending.');
    const pdf = priorityTable(rows);
    if (!pdf.length) {
      html += '<div class="empty">No open actions</div>';
    } else {
      html += `<div class="tab-row">
        <button class="tab ${state.severityFilter === 'All' ? 'active' : ''}" data-sev="All">All</button>
        ${PRIORITY_ORDER.filter(s => sev[s]).map(s =>
          `<button class="tab ${state.severityFilter === s ? 'active' : ''}" data-sev="${esc(s)}">${esc(s)} · ${sev[s]}</button>`).join('')}
        <button class="tab ${state.severityFilter === 'No BD record' ? 'active' : ''}" data-sev="No BD record">No BD record · ${noBd}</button>
        <button class="tab ${state.severityFilter === 'No EE evidence' ? 'active' : ''}" data-sev="No EE evidence">No EE evidence · ${noEe}</button>
      </div>`;

      let shown = pdf;
      if (state.severityFilter === 'No BD record') shown = pdf.filter(r => isMissing(r['BD Tracker ETA']));
      else if (state.severityFilter === 'No EE evidence') shown = pdf.filter(r => isMissing(r['EE ETA']));
      else if (state.severityFilter !== 'All') shown = pdf.filter(r => String(r.Urgency) === state.severityFilter);

      const cols = ['Purchasing Document', 'Urgency', 'Primary Reason', 'Still to be Delivered (Qty)', 'Order Unit', 'RDD', 'Overall Status', 'Import Country', 'Supplier Name', 'Short Text', 'Required follow-up', 'Suggested owner *', 'Confidence'];
      const numCols = [3];
      const dateCols = ['RDD'];
      const sevIndex = 1;
      html += dataTable(cols, shown.map(r => [
        r['Purchasing Document'], r.Urgency, r['Primary Reason'], r['Still to be Delivered (Qty)'],
        r['Order Unit'], r.RDD, r['Overall Status'], r['Import Country'], r['Supplier Name'],
        r['Short Text'], r['Required follow-up'], r['Suggested owner *'], r.Confidence,
      ]), { numCols, dateCols, sevIndex });

      html += `<div class="note mut" style="margin-top:10px"><div>Rows are colour-tinted by severity. Follow-up and owner are derived from Primary Reason and are suggestions, not assignments.</div></div>`;
    }
    html += closeSection();

    html += section('Open a PO journey');
    const polist = [...new Set(rows.map(r => String(r[poIdx])).filter(Boolean))].sort();    html += `<div class="filters">
      <input class="search-input" id="ac-po" list="ac-polist" placeholder="Type or pick a PO..." />
      <datalist id="ac-polist">${polist.map(p => `<option value="${esc(p)}">`).join('')}</datalist>
      <button class="btn btn-primary" id="ac-open">Open PO journey</button></div>`;
    html += closeSection();

    $('#viewRoot').innerHTML = html;

    $('#viewRoot').querySelectorAll('.tab').forEach(t =>
      t.addEventListener('click', () => { state.severityFilter = t.dataset.sev; actionCentre(); }));
    $('#ac-open').addEventListener('click', () => {
      const v = $('#ac-po').value.trim();
      if (v) { state.po = v; state.page = 'PO Journey'; go('app'); }
    });
    $('#ac-po').addEventListener('keydown', e => {
      if (e.key === 'Enter') { const v = e.target.value.trim(); if (v) { state.po = v; state.page = 'PO Journey'; go('app'); } }
    });
  }

  function priorityTable(rows) {
    const out = [];
    for (const r of rows) {
      const row = {};
      for (let i = 0; i < headers().length; i++) row[headers()[i]] = i < r.length ? r[i] : null;
      const reason = String(row['Primary Reason'] || '');
      const fup = suggestedFollowup(reason);
      row['Required follow-up'] = fup.action;
      row['Suggested owner *'] = fup.owner;
      row.Confidence = dataConfidence(r, true, false);
      out.push(row);
    }
    const sevRank = (s) => { const i = PRIORITY_ORDER.indexOf(String(s)); return i < 0 ? 99 : i; };
    out.sort((a, b) => {
      const bySev = sevRank(a.Urgency) - sevRank(b.Urgency);
      if (bySev) return bySev;
      const aNo = isMissing(a.RDD) ? 1 : 0, bNo = isMissing(b.RDD) ? 1 : 0;
      if (aNo !== bNo) return aNo - bNo;
      const ad = parseISO(a.RDD), bd = parseISO(b.RDD);
      if (ad && bd) return ad - bd;
      return 0;
    });
    const seen = new Set();
    return out.filter(r => {
      const p = String(r['Purchasing Document'] || '');
      if (seen.has(p)) return false;
      seen.add(p);
      return true;
    });
  }

  /* ---------------- PO Journey ---------------- */
  function poJourney() {
    const rows = activeRows();
    const poIdx = idx('Purchasing Document');
    if (poIdx < 0 || !rows.length) return emptyView('No PO data', 'Restore or upload a view first.');

    let html = viewHead('PO Journey', 'Milestone trail and manager follow-up for a single PO.');
    html += `<div class="filters">
      <input class="search-input" id="pj-search" placeholder="Search PO, product / AGI, or container..." value="${esc(state.searchQ || '')}" />
      <select class="filter-select" id="pj-po"></select>
    </div>`;

    const polist = [...new Set(rows.map(r => String(r[poIdx])).filter(Boolean))].sort();
    const cands = (globalSearch('pj') || []).filter(c => polist.includes(String(c)));
    const q = (state.searchQ || '').trim();
    const chosen = q && cands.length
      ? (state.po && cands.includes(String(state.po)) ? String(state.po) : cands[0])
      : (state.po && polist.includes(String(state.po)) ? String(state.po) : polist[0]);
    state.po = chosen;

    const sub = rows.filter(r => String(r[poIdx]) === String(chosen));
    const one = sub[0];
    const oneObj = {};
    for (let i = 0; i < headers().length; i++) oneObj[headers()[i]] = i < one.length ? one[i] : null;

    const ug = String(oneObj.Urgency || 'Monitor');
    const kind = ug === 'Critical' ? 'crit' : ug === 'Urgent' ? 'urg' : ug === 'Data Review' ? 'dr' : 'mon';

    html += kpiRow([
      ['PO', chosen, 'plain'],
      ['Urgency', ug, kind],
      ['Open Qty', fmtQ(oneObj['Still to be Delivered (Qty)']), 'plain'],
      ['RDD', fmtDate(oneObj.RDD), 'plain'],
      ['Import Country', String(oneObj['Import Country'] || '-'), 'plain'],
      ['Confidence', dataConfidence(one, true, false), 'plain'],
    ]);

    if (q && !cands.length) {
      html += `<div class="note mut" style="margin-top:12px"><div>No active PO matches &ldquo;${esc(q)}&rdquo;. Showing the first PO in the list.</div></div>`;
    }

    if (q && cands.length > 1) {
      html += `<div class="match-note note mut" style="margin-top:12px">
        <div style="margin-bottom:8px"><b>${cands.length} active POs match &ldquo;${esc(q)}&rdquo;</b> — choose one:</div>
        <div class="match-chips">${cands.map(c => `<button type="button" class="match-chip${String(c) === String(chosen) ? ' on' : ''}" data-po="${esc(c)}">${esc(c)}</button>`).join('')}</div>
      </div>`;
    }

    // Milestone journey
    html += section('Milestone journey');
    const milestones = [
      ['LC Date', 'LC', 'LC issued'],
      ['SI Shared Date', 'SI', 'SI shared'],
      ['ETD', 'ETD', 'Schedule / ETD'],
      ['BD Tracker ETA', 'BD ETA', 'BD expected arrival'],
      ['EE ETA', 'EE ETA', 'Shipment ETA'],
      ['OBL/EBL rcvd Date', 'OBL', 'OBL/EBL received'],
      ['Final Docs rcvd Date', 'Final', 'Final docs received'],
    ];
    const msList = milestones.map(([col, lbl, note]) => {
      const v = oneObj[col];
      const done = !isMissing(v);
      return `<div class="ms"><div class="ms-dot ${done ? 'ok' : 'no'}">${done ? '✓' : '!'}</div>
        <div class="ms-label">${lbl}</div><div class="ms-date">${done ? fmtDate(v) : '—'}</div>
        <div class="ms-line ${done ? 'ok' : ''}"></div>
        <div style="font-size:10.3px;color:var(--ink-faint);margin-top:3px">${done ? note : note + ': not recorded'}</div></div>`;
    }).join('');
    html += `<div class="milestone">${msList}</div>`;
    html += closeSection();

    // Partial shipments
    html += section('Partial shipments', 'Open quantity is at PO level - partial rows are process detail and are not totalled as shipment quantity.');
    const partCols = ['Partial Shipment No.', 'Overall Status', 'ETD', 'EE ETA', 'OBL/EBL rcvd Date', 'Final Docs rcvd Date']
      .filter(c => idx(c) >= 0);
    if (partCols.length) {
      html += dataTable(partCols, sub.map(r => partCols.map(c => r[idx(c)])),
        { dateCols: ['ETD', 'EE ETA', 'OBL/EBL rcvd Date', 'Final Docs rcvd Date'] });
    } else {
      html += '<div class="empty">No partial-shipment columns</div>';
    }
    html += `<div class="muted">Open quantity relates to the PO. Partial-shipment rows describe process steps and must not be totalled.</div>`;
    html += closeSection();

    if (idx('Container No.') >= 0 && sub.every(r => isMissing(r[idx('Container No.')]))) {
      html += `<div class="restore-banner"><div><b>Container evidence not confirmed.</b> The link between this PO and container records is not recorded; verify before closing the risk.</div></div>`;
    }

    html += section('Manager follow-up');
    const reason = String(oneObj['Primary Reason'] || '');
    const fup = suggestedFollowup(reason);
    html += `<div class="kv">
      <dt>Primary reason</dt><dd>${esc(reason || '-')}</dd>
      <dt>Suggested follow-up</dt><dd>${esc(fup.action)}</dd>
      <dt>Suggested owner (suggested)</dt><dd>${esc(fup.owner)}</dd>
    </div>
    <div class="note mut" style="margin-top:12px"><div>Follow-up and owner are derived from Primary Reason and are recommendations, not assignments.</div></div>`;
    html += closeSection();

    html += section('Note (saved on this device only)');
    html += `<textarea id="po-note" style="width:100%;min-height:90px;border-radius:12px;border:1px solid var(--border);background:var(--surface-2);color:var(--ink);padding:12px;font-family:inherit;font-size:13.5px">${esc(state.notes[chosen] || '')}</textarea>
      <div style="margin-top:10px"><button class="btn btn-primary" id="save-note">Save note</button></div>`;
    html += closeSection();

    $('#viewRoot').innerHTML = html;

    const sel = $('#pj-po');
    const selList = q && cands.length ? cands : polist;
    selList.forEach(p => {
      const o = document.createElement('option');
      o.value = p; o.textContent = p;
      sel.appendChild(o);
    });
    sel.value = chosen;
    sel.addEventListener('change', () => { state.po = sel.value; poJourney(); });

    $('#viewRoot').querySelectorAll('.match-chip').forEach(b => {
      b.addEventListener('click', () => { state.po = b.getAttribute('data-po'); poJourney(); });
    });

    $('#pj-search').addEventListener('input', () => { state.searchQ = $('#pj-search').value; });
    $('#pj-search').addEventListener('keydown', e => {
      if (e.key === 'Enter') { state.searchQ = e.target.value; poJourney(); }
    });

    $('#save-note').addEventListener('click', () => {
      state.notes[chosen] = $('#po-note').value;
      saveNotes();
      toast('Note saved.');
    });
  }

  /* ---------------- Shipment Visibility ---------------- */
  function shipmentVisibility() {
    const rows = activeRows();
    if (!rows.length) return emptyView('No shipment data', 'Restore or upload a view first.');

    let html = viewHead('Shipment Visibility', 'One row per container / evidence record.');
    html += `<div class="note mut" style="margin-bottom:16px"><div>Open quantity is never summed this page (PO level only).</div></div>`;

    const keep = ['Purchasing Document', 'From', 'Container No.', 'Tracking', 'Status', 'EE ETD', 'EE ETA', 'Import Country']
      .filter(c => idx(c) >= 0);
    const seen = new Set();
    const view = [];
    for (const r of rows) {
      const key = keep.map(c => r[idx(c)]).join('|');
      if (seen.has(key)) continue;
      seen.add(key);
      view.push(keep.map(c => r[idx(c)]));
    }
    html += dataTable(keep, view, { dateCols: ['EE ETD', 'EE ETA'] });

    html += section('Shipment evidence');
    const has = (c) => { const i = idx(c); return i < 0 ? 0 : rows.filter(r => !isMissing(r[i])).length; };
    html += kpiRow([
      ['Container assigned', has('Container No.'), 'plain'],
      ['with EE ETA', has('EE ETA'), 'plain'],
      ['with EE ETD', has('EE ETD'), 'plain'],
    ]);
    html += closeSection();

    $('#viewRoot').innerHTML = html;
  }

  /* ---------------- Risk & Exposure ---------------- */
  function riskAndExposure() {
    const rows = activeRows();
    if (!rows.length) return emptyView('No risk data', 'Restore or upload a view first.');

    let html = viewHead('Risk & Exposure', 'Distinct-PO exposure by country, supplier and delivery window.');
    html += `<div class="note mut" style="margin-bottom:16px"><div>Counts are distinct POs, never per-row across partial shipments; no KG/L quantity is combined.</div></div>`;

    const crit = rows.filter(r => ['Critical', 'Urgent'].includes(String(r[idx('Urgency')] || '')));

    const poIdx = idx('Purchasing Document');
    const countryIdx = idx('Import Country');
    const supIdx = idx('Supplier Name');

    html += `<div style="display:grid;grid-template-columns:1fr 1fr;gap:22px">`;
    html += section('Critical / Urgent by Import country');
    if (countryIdx >= 0 && poIdx >= 0) {
      const g = {};
      for (const r of crit) { const k = String(r[countryIdx] || '-') || '-'; g[k] = (g[k] || new Set()); g[k].add(String(r[poIdx])); }
      html += hbar(Object.entries(g).map(([k, s]) => [k, s.size]).sort((a, b) => b[1] - a[1]));
    } else html += '<div class="empty">No country field in this view.</div>';
    html += closeSection();

    html += section('Critical / Urgent by Supplier');
    if (supIdx >= 0 && poIdx >= 0) {
      const g = {};
      for (const r of crit) { const k = String(r[supIdx] || '-') || '-'; g[k] = (g[k] || new Set()); g[k].add(String(r[poIdx])); }
      html += hbar(Object.entries(g).map(([k, s]) => [k, s.size]).sort((a, b) => b[1] - a[1]).slice(0, 12));
    } else html += '<div class="empty">No supplier field in this view.</div>';
    html += closeSection();
    html += `</div>`;

    html += section('Risk reason x country matrix');
    const reasonIdx = idx('Primary Reason');
    if (countryIdx >= 0 && reasonIdx >= 0 && poIdx >= 0) {
      const reasons = [...new Set(crit.map(r => String(r[reasonIdx] || '')))].filter(Boolean);
      const countries = [...new Set(crit.map(r => String(r[countryIdx] || '-')))].filter(Boolean).sort();
      const m = {};
      for (const r of crit) {
        const c = String(r[countryIdx] || '-');
        const reason = String(r[reasonIdx] || '');
        const key = c + '\u0000' + reason;
        m[key] = (m[key] || new Set());
        m[key].add(String(r[poIdx]));
      }
      const cols = ['Import Country', ...reasons];
      const matrixRows = countries.map(c => [c, ...reasons.map(rs => m[c + '\u0000' + rs] ? m[c + '\u0000' + rs].size : 0)]);
      html += dataTable(cols, matrixRows, { numCols: reasons.map((_, i) => i + 1) });
    } else html += '<div class="empty">Matrix unavailable - required fields not present.</div>';
    html += closeSection();

    html += section('Product exposure');
    const shortIdx = idx('Short Text');
    const qtyIdx = idx('Still to be Delivered (Qty)');
    const rddIdx = idx('RDD');
    const urgIdx = idx('Urgency');
    if (shortIdx >= 0 && poIdx >= 0) {
      const g = {};
      for (const r of rows) {
        const k = String(r[shortIdx] || '');
        const po = String(r[poIdx]);
        if (!g[k]) g[k] = { pos: new Set(), qty: 0, crit: 0, urg: 0, dr: 0, rdds: [] };
        g[k].pos.add(po);
        const q = Number(r[qtyIdx]); if (!Number.isNaN(q)) g[k].qty += q;
        const u = String(r[urgIdx] || '');
        if (u === 'Critical') g[k].crit++; else if (u === 'Urgent') g[k].urg++; else if (u === 'Data Review') g[k].dr++;
        const d = parseISO(r[rddIdx]); if (d) g[k].rdds.push(d);
      }
      const items = Object.entries(g).map(([name, v]) => {
        const earliest = v.rdds.length ? v.rdds.reduce((a, b) => (a < b ? a : b)) : null;
        return [name, v.pos.size, v.qty, v.crit, v.urg, v.dr, earliest ? fmtDate(earliest) : '-'];
      }).sort((a, b) => b[3] - a[3] || b[4] - a[4] || a[0].localeCompare(b[0]));
      html += dataTable(['Product / AGI', 'Open_PO', 'Qty', 'Crit', 'Urg', 'DR', 'Earliest_RDD'], items, { numCols: [1, 2, 3, 4, 5] });
      html += `<div class="note mut" style="margin-top:10px"><div>Quantities carry their labelled unit and are never cross-unit summed.</div></div>`;
    } else html += '<div class="empty">No product field in this view.</div>';
    html += closeSection();

    html += section('RDD exposure horizon');
    if (rddIdx >= 0) {
      const buckets = { 'Overdue': new Set(), '0-7d': new Set(), '8-30d': new Set(), '31-60d': new Set(), '>60d': new Set(), 'Unknown': new Set() };
      for (const r of rows) {
        const off = rddOffset(r[rddIdx]);
        const b = off === null ? 'Unknown' : rddHorizon(off);
        (buckets[b] = buckets[b] || new Set()).add(String(r[poIdx]));
      }
      const order = ['Overdue', '0-7d', '8-30d', '31-60d', '>60d', 'Unknown'];
      html += hbar(order.map(b => [b, buckets[b] ? buckets[b].size : 0]));
    } else html += '<div class="empty">No RDD field in this view.</div>';
    html += closeSection();

    html += `<div class="note" style="margin-top:18px"><div>Exposure summaries for awareness only; use Data Quality exports for a controlled copy.</div></div>`;

    $('#viewRoot').innerHTML = html;
  }

  /* ---------------- Data Quality ---------------- */
  function dataQuality() {
    const control = (state.ctx && state.ctx.control) || {};
    let html = viewHead('Data Quality', 'Reconciliation, quality KPIs and the cleaner\u2019s exception output.');

    const missing = (col) => {
      const i = idx(col);
      if (i < 0) return '-';
      return missingCount(col);
    };

    const qRows = [
      ['RDD missing', missing('RDD')],
      ['Route / country unknown', missing('Import Country')],
      ['No BD record', missing('BD Tracker ETA')],
      ['No Eagle Eye record', missing('EE ETA')],
      ['BD PO not in Open PO', (state.ctx.bd_rows || []).length],
      ['EE PO not in Open PO', (state.ctx.ee_rows || []).length],
      ['Status complete but open qty', missing('Overall Status')],
      ['Container not assigned', missing('Container No.')],
    ];
    html += section('Reconciliation & quality KPI');
    html += kpiRow(qRows.map(([l, v]) => [l, v, 'plain']));
    html += `<div class="note mut" style="margin-top:12px"><div>Data-review flagged rows use the slate Data Review label; a data gap is not treated as Critical.</div></div>`;
    html += closeSection();

    html += section('Exception queue');
    const names = ['Exceptions', 'Unmatched BD', 'Unmatched EE', 'Cleaning Log'].filter(n => control[n]);
    if (names.length) {
      html += `<div class="tab-row">${names.map((n, i) =>
        `<button class="tab ${i === 0 ? 'active' : ''}" data-sheet="${esc(n)}">${esc(n)}</button>`).join('')}</div>`;
      html += `<div id="sheet-host"></div>`;
    } else {
      html += `<div class="note" style="margin-top:12px"><div>No control sheets in this view.</div></div>`;
    }
    html += closeSection();

    const drCount = missingCount('Urgency') !== null
      ? ((state.ctx.master || []).filter(r => String(r[idx('Urgency')] || '').trim() === 'Data Review').length) : 0;
    if (idx('Urgency') >= 0) {
      html += `<div class="restore-banner" style="margin-top:16px"><div><b>${drCount}</b> row(s) with Urgency = Data Review (slate, not red).</div></div>`;
    }

    $('#viewRoot').innerHTML = html;

    let activeSheet = names[0];
    const renderSheet = () => {
      const item = control[activeSheet];
      if (!item) { $('#sheet-host').innerHTML = '<div class="empty">No data</div>'; return; }
      const [h, rows] = item;
      const dateCols = h.map((c, i) => (/date/i.test(String(c)) || /(eta|etd|modified|refresh|cleaned)/i.test(String(c))) ? String(c) : null).filter(Boolean);
      $('#sheet-host').innerHTML = dataTable(h, rows, { dateCols });
    };
    renderSheet();
    $('#viewRoot').querySelectorAll('.tab[data-sheet]').forEach(t =>
      t.addEventListener('click', () => {
        $('#viewRoot').querySelectorAll('.tab[data-sheet]').forEach(x => x.classList.remove('active'));
        t.classList.add('active');
        activeSheet = t.dataset.sheet;
        renderSheet();
      }));
  }

  /* ---------------- Thresholds & Refresh ---------------- */
  function thresholdsRefresh() {
    const meta = (state.ctx && state.ctx.meta) || {};
    const th = (state.ctx && state.ctx.thresholds) || DEFAULT_THRESHOLDS;
    let html = viewHead('Thresholds & Refresh', 'Route timing rules and source freshness.');

    html += section('Thresholds');
    html += `<div class="note" style="margin-bottom:14px"><div>Active route threshold file: <b>${esc(meta.threshold_filename || 'built-in defaults')}</b></div></div>`;
    const rows = [];
    for (const [kind, label] of [['LC', 'LC'], ['ETD', 'Schedule / ETD']]) {
      for (const route of ['India', 'ASEAN', 'ChinaEA', 'Europe']) {
        const kv = (th[kind] && th[kind][route]) || [0, 0];
        rows.push([route, label, kv[0], kv[1],
          `If '${label}' is missing ${kv[0]}d before RDD it is Urgent; ${kv[1]}d it is Critical.`, '-', '-']);
      }
    }
    html += dataTable(['Import route', 'Milestone', 'Urgent', 'Critical', 'Rule', 'Effective date', 'Updated'], rows, { numCols: [2, 3] });
    html += `<div class="note mut" style="margin-top:12px"><div>Threshold editing is read-only here; update the Country Thresholds sheet and re-upload to change timing.</div></div>`;
    html += closeSection();

    html += section('Source file freshness');
    html += freshnessTable(meta);
    html += closeSection();

    html += section('Local data & refresh');
    html += `<div class="note" style="margin-bottom:14px"><div>Current view: <b>${meta.is_restored ? 'restored' : 'fresh'}</b> - last refreshed ${String(meta.refreshed_at || '-').slice(0, 16)}.</div></div>`;
    html += kpiRow([
      ['Notes saved', notesCount(), 'plain'],
      ['Open POs', meta.open_po_count || '-', 'plain'],
    ]);
    html += `<div class="view-actions">
      <button class="btn btn-primary" id="tt-upload">Upload New Full Set</button>
      <button class="btn" id="tt-export">Export Current Master</button>
      <button class="btn btn-danger" id="tt-clear">Clear Local Data & Start Fresh</button>
    </div>`;
    html += closeSection();

    html += section('Controlled exports');
    html += `<div class="view-actions">
      <button class="export-btn" id="exp-actions">⤓ Export action list</button>
      <button class="export-btn" id="exp-journey">⤓ Export PO journey</button>
      <button class="export-btn" id="exp-quality">⤓ Export Data Quality</button>
      <button class="export-btn" id="exp-shipment">⤓ Export Shipment</button>
    </div>`;
    html += closeSection();

    html += `<div class="muted" style="margin-top:16px">Source files (last run):</div>`;
    html += `<ul style="margin:6px 0 0 18px;font-size:13.2px;color:var(--ink-muted)">`;
    for (const s of meta.source_files || []) {
      if (s.filename) html += `<li>${esc(s.filename)}</li>`;
    }
    html += `</ul>`;

    $('#viewRoot').innerHTML = html;

    $('#tt-upload').addEventListener('click', () => go('upload'));
    $('#tt-export').addEventListener('click', exportMasterXlsx);
    $('#tt-clear').addEventListener('click', confirmClear);
    $('#exp-actions').addEventListener('click', () => {
      const rows = priorityTable(activeRows());
      exportCsv('anchor_actions.csv', Object.keys(rows[0] || {}), rows.map(r => Object.values(r)));
    });
    $('#exp-journey').addEventListener('click', () => {
      exportCsv('anchor_po_journey.csv', headers(), state.ctx.master || []);
    });
    $('#exp-quality').addEventListener('click', () => {
      exportCsv('anchor_reconciliation.csv', headers(), state.ctx.master || []);
    });
    $('#exp-shipment').addEventListener('click', () => {
      const ship = ['Purchasing Document', 'From', 'Container No.', 'EE ETA', 'Status'].filter(c => idx(c) >= 0);
      const rows = (state.ctx.master || []).map(r => ship.map(c => r[idx(c)]));
      exportCsv('anchor_shipments.csv', ship, rows);
    });
  }

  function freshnessTable(meta) {
    const rows = (meta.source_files || []).filter(s => s.filename);
    if (!rows.length) return '<div class="empty">No source files recorded</div>';
    const items = rows.map(s => {
      const loaded = s.loaded_at || '';
      const dt = parseISO(loaded);
      let status = 'unknown';
      if (dt) status = (Date.now() - dt.getTime()) / 86400000 <= FRESH_DAYS ? 'FRESH' : 'STALE';
      const pill = status === 'FRESH' ? '<span class="pill pill-pos">FRESH</span>'
        : status === 'STALE' ? '<span class="pill pill-warn">STALE</span>'
        : '<span class="pill pill-mut">unknown</span>';
      return [s.filename, String(loaded).slice(0, 16), FRESH_DAYS + '', pill];
    });
    const body = items.map(r =>
      `<tr><td>${esc(r[0])}</td><td>${esc(r[1])}</td><td class="num">${r[2]}</td><td>${r[3]}</td></tr>`).join('');
    return `<div class="table-wrap"><table class="data"><thead><tr>
      <th>Source file</th><th>Loaded at</th><th class="num">Fresh window (days)</th><th>Status</th>
      </tr></thead><tbody>${body}</tbody></table></div>`;
  }

  /* ---------------- misc renderers ---------------- */
  function viewHead(title, desc) {
    return `<div class="view-head"><div class="view-title"><h1>${esc(title)}</h1></div>
      <div class="view-desc">${esc(desc)}</div></div>`;
  }
  function emptyView(title, body) {
    $('#viewRoot').innerHTML = `<div class="view-head"><div class="view-title"><h1>${esc(title)}</h1></div></div>
      <div class="empty"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M8 15s1.5-2 4-2 4 2 4 2M9 9h.01M15 9h.01"/></svg>
      <div style="font-weight:700;font-size:15px;color:var(--ink)">${esc(title)}</div>
      <div style="margin-top:4px">${esc(body)}</div></div>`;
  }

  function renderPage() {
    const page = state.page;
    const map = {
      'Action Centre': actionCentre,
      'PO Journey': poJourney,
      'Shipment Visibility': shipmentVisibility,
      'Risk & Exposure': riskAndExposure,
      'Data Quality': dataQuality,
      'Thresholds & Refresh': thresholdsRefresh,
    };
    const fn = map[page] || actionCentre;
    fn();
  }

  /* ---------------- init ---------------- */
  function init() {
    loadLS();
    applyTheme();
    $('#themeToggle').addEventListener('click', () => {
      state.theme = state.theme === 'dark' ? 'light' : 'dark';
      applyTheme();
    });
    $('#clearModal').addEventListener('click', e => { if (e.target.id === 'clearModal') cancelClear(); });

    const hasView = state.ctx && (state.ctx.master || []).length;
    if (hasView) {
      renderRestore();
      go('restore');
    } else {
      renderDropzones();
      go('welcome');
    }
  }

  document.addEventListener('DOMContentLoaded', init);

  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('sw.js').catch(() => {});
    });
  }

  return {
    go, restore, process, renderDropzones, confirmClear, cancelClear, doClear,
  };
})();
