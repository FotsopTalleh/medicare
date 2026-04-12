/* ═══════════════════════════════════════════════════════════
   partograph.js — Digital Partograph Frontend Engine
   SaveTheMommy MediCare
   ═══════════════════════════════════════════════════════════ */

'use strict';

/* ── State ─────────────────────────────────────────────────── */
const state = {
  fhr: [], cervix: [], descent: [], moulding: [],
  contractions: [], 'amniotic-fluid': [], vitals: [],
  temperature: [], medications: [], urine: []
};

let charts = {};
let autoSaveTimer = null;
let pendingDelete = { section: null, id: null };

/* ── Helpers ────────────────────────────────────────────────── */
function nowTime() {
  const d = new Date();
  return d.getHours().toString().padStart(2,'0') + ':' + d.getMinutes().toString().padStart(2,'0');
}

function hoursElapsed(eventTime) {
  try {
    const [ah, am] = ADMISSION_TIME.split(':').map(Number);
    const [eh, em] = eventTime.split(':').map(Number);
    return Math.max(0, (eh * 60 + em - (ah * 60 + am)) / 60);
  } catch { return 0; }
}

// WHO standard: alert line 4cm→10cm at 1cm/hr; action line 4hrs to the right
function alertLine(h)  { return (h >= 0 && h <= 6)  ? 4 + h       : null; }
function actionLine(h) { return (h >= 4 && h <= 10) ? 4 + (h - 4) : null; }

function cervixZone(h, cm) {
  // Being BELOW a reference line = progressing slower than expected = bad
  const al  = alertLine(h);
  const acl = actionLine(h);
  if (acl !== null && cm < acl) return { label: 'Action', cls: 'danger' };
  if (al  !== null && cm < al)  return { label: 'Alert',  cls: 'warning' };
  return { label: 'Normal', cls: 'success' };
}

function toast(msg, type = 'success') {
  const el = document.createElement('div');
  el.className = `parto-toast alert alert-${type} py-2 px-3`;
  el.innerHTML = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

function showAlert(containerId, msg, type) {
  const c = document.getElementById(containerId);
  if (!c) return;
  c.innerHTML = `<div class="parto-alert ${type}">${msg}</div>`;
}
function clearAlert(containerId) {
  const c = document.getElementById(containerId);
  if (c) c.innerHTML = '';
}

/* ── Section accordion ──────────────────────────────────────── */
function toggleSection(id) {
  const body = document.getElementById(id);
  const icon = document.getElementById(id + '-icon');
  if (!body) return;
  const open = body.style.display !== 'none';
  body.style.display = open ? 'none' : '';
  if (icon) icon.className = open ? 'fas fa-chevron-right' : 'fas fa-chevron-down';
}
window.toggleSection = toggleSection;

/* ── Prefill current time in all forms ─────────────────────── */
function prefillTimes() {
  const t = nowTime();
  ['fhrTime','aflTime','cervixTime','descentTime','mouldTime',
   'contrTime','vitalsTime','tempTime','medsTime','urineTime']
    .forEach(id => { const el = document.getElementById(id); if (el && !el.value) el.value = t; });
}

/* ═══════════════════════════════════════════════════════════
   DATA SUBMISSION
   ═══════════════════════════════════════════════════════════ */
const sectionConfig = {
  fhr: {
    getPayload: () => ({ time: el('fhrTime').value, fhr_value: +el('fhrValue').value }),
    validate: p => p.time && p.fhr_value >= 80 && p.fhr_value <= 200,
    errorMsg: 'Time and FHR (80–200 bpm) required.',
    editId: 'fhrEditId', btnLabel: 'fhrBtnLabel', errorEl: 'fhrError',
    reset: () => { el('fhrValue').value = ''; el('fhrEditId').value = ''; el('fhrBtnLabel').textContent = 'Record FHR'; }
  },
  'amniotic-fluid': {
    getPayload: () => ({ time: el('aflTime').value, status: el('aflStatus').value }),
    validate: p => p.time && p.status,
    editId: 'aflEditId', btnLabel: 'aflBtnLabel',
    reset: () => { el('aflEditId').value = ''; el('aflBtnLabel').textContent = 'Record Status'; }
  },
  cervix: {
    getPayload: () => ({ time: el('cervixTime').value, dilatation_cm: +el('cervixValue').value }),
    validate: p => p.time && p.dilatation_cm >= 0 && p.dilatation_cm <= 10,
    errorMsg: 'Time and dilatation (0–10 cm) required.',
    editId: 'cervixEditId', btnLabel: 'cervixBtnLabel', errorEl: 'cervixError',
    reset: () => { el('cervixValue').value = ''; el('cervixEditId').value = ''; el('cervixBtnLabel').textContent = 'Record Cervix'; }
  },
  descent: {
    getPayload: () => ({ time: el('descentTime').value, descent_value: +el('descentValue').value }),
    validate: p => p.time && p.descent_value >= -5 && p.descent_value <= 5,
    editId: 'descentEditId', btnLabel: 'descentBtnLabel',
    reset: () => { el('descentValue').value = ''; el('descentEditId').value = ''; el('descentBtnLabel').textContent = 'Record Descent'; }
  },
  moulding: {
    getPayload: () => ({ time: el('mouldTime').value, grade: el('mouldGrade').value }),
    validate: p => p.time && p.grade,
    editId: 'mouldEditId', btnLabel: 'mouldBtnLabel',
    reset: () => { el('mouldEditId').value = ''; el('mouldBtnLabel').textContent = 'Record Moulding'; }
  },
  contractions: {
    getPayload: () => ({
      time: el('contrTime').value, frequency: +el('contrFreq').value,
      intensity: el('contrIntensity').value, duration_seconds: +el('contrDur').value
    }),
    validate: p => p.time && p.frequency >= 0 && p.frequency <= 5 && p.duration_seconds >= 20 && p.duration_seconds <= 90,
    errorMsg: 'All fields required. Freq 0–5, Duration 20–90s.',
    editId: 'contrEditId', btnLabel: 'contrBtnLabel',
    reset: () => { el('contrFreq').value = ''; el('contrDur').value = ''; el('contrEditId').value = ''; el('contrBtnLabel').textContent = 'Record'; }
  },
  vitals: {
    getPayload: () => ({
      time: el('vitalsTime').value,
      systolic_bp: el('vitalsSys').value ? +el('vitalsSys').value : null,
      diastolic_bp: el('vitalsDia').value ? +el('vitalsDia').value : null,
      pulse_bpm: el('vitalsPulse').value ? +el('vitalsPulse').value : null
    }),
    validate: p => p.time,
    editId: 'vitalsEditId', btnLabel: 'vitalsBtnLabel',
    reset: () => { ['vitalsSys','vitalsDia','vitalsPulse'].forEach(i => el(i).value = ''); el('vitalsEditId').value = ''; el('vitalsBtnLabel').textContent = 'Record Vitals'; }
  },
  temperature: {
    getPayload: () => ({ time: el('tempTime').value, celsius: +el('tempValue').value }),
    validate: p => p.time && p.celsius >= 34 && p.celsius <= 41,
    errorMsg: 'Time and temperature (34–41°C) required.',
    editId: 'tempEditId', btnLabel: 'tempBtnLabel',
    reset: () => { el('tempValue').value = ''; el('tempEditId').value = ''; el('tempBtnLabel').textContent = 'Record Temp'; }
  },
  medications: {
    getPayload: () => ({
      time: el('medsTime').value, medication_type: el('medsType').value,
      medication_name: el('medsName').value, dose: el('medsDose').value, route: el('medsRoute').value
    }),
    validate: p => p.time && p.dose,
    editId: 'medsEditId', btnLabel: 'medsBtnLabel',
    reset: () => { el('medsName').value = ''; el('medsDose').value = ''; el('medsEditId').value = ''; el('medsBtnLabel').textContent = 'Record'; }
  },
  urine: {
    getPayload: () => ({
      time: el('urineTime').value, protein: el('urineProtein').value,
      acetone: el('urineAcetone').value, volume_ml: el('urineVolume').value ? +el('urineVolume').value : null
    }),
    validate: p => p.time,
    editId: 'urineEditId', btnLabel: 'urineBtnLabel',
    reset: () => { el('urineVolume').value = ''; el('urineEditId').value = ''; el('urineBtnLabel').textContent = 'Record'; }
  }
};

function el(id) { return document.getElementById(id); }

async function submitEntry(section) {
  const cfg = sectionConfig[section];
  const payload = cfg.getPayload();
  if (!cfg.validate(payload)) {
    if (cfg.errorEl) el(cfg.errorEl).textContent = cfg.errorMsg || 'Please fill all required fields.';
    return;
  }
  if (cfg.errorEl) el(cfg.errorEl).textContent = '';

  const editId = cfg.editId ? el(cfg.editId)?.value : '';
  const url = editId
    ? `/api/partograph/${CASE_ID}/${section}/${editId}`
    : `/api/partograph/${CASE_ID}/${section}`;
  const method = editId ? 'PUT' : 'POST';

  try {
    const res = await fetch(url, {
      method, headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (data.success !== false) {
      toast(`✅ ${section} entry ${editId ? 'updated' : 'recorded'}`);
      cfg.reset();
      await loadSection(section);
      refreshSidebar();
    } else {
      toast(`❌ ${data.error}`, 'danger');
    }
  } catch (e) {
    toast('❌ Network error', 'danger');
  }
}
window.submitEntry = submitEntry;

async function deleteEntry(section, id) {
  pendingDelete = { section, id };
  new bootstrap.Modal(document.getElementById('deleteModal')).show();
}
window.deleteEntry = deleteEntry;

document.getElementById('confirmDelete')?.addEventListener('click', async () => {
  const { section, id } = pendingDelete;
  bootstrap.Modal.getInstance(document.getElementById('deleteModal')).hide();
  await fetch(`/api/partograph/${CASE_ID}/${section}/${id}`, { method: 'DELETE' });
  toast('✅ Entry deleted', 'warning');
  await loadSection(section);
  refreshSidebar();
});

function editEntry(section, entry) {
  const cfg = sectionConfig[section];
  // Pre-fill edit ID and button
  if (cfg.editId) el(cfg.editId).value = entry.id;
  if (cfg.btnLabel) el(cfg.btnLabel).textContent = 'Update';

  // Pre-fill form fields
  const map = {
    fhr:             { fhrTime: 'time', fhrValue: 'fhr_value' },
    'amniotic-fluid':{ aflTime: 'time', aflStatus: 'status' },
    cervix:          { cervixTime: 'time', cervixValue: 'dilatation_cm' },
    descent:         { descentTime: 'time', descentValue: 'descent_value' },
    moulding:        { mouldTime: 'time', mouldGrade: 'grade' },
    contractions:    { contrTime: 'time', contrFreq: 'frequency', contrIntensity: 'intensity', contrDur: 'duration_seconds' },
    vitals:          { vitalsTime: 'time', vitalsSys: 'systolic_bp', vitalsDia: 'diastolic_bp', vitalsPulse: 'pulse_bpm' },
    temperature:     { tempTime: 'time', tempValue: 'celsius' },
    medications:     { medsTime: 'time', medsType: 'medication_type', medsName: 'medication_name', medsDose: 'dose', medsRoute: 'route' },
    urine:           { urineTime: 'time', urineProtein: 'protein', urineAcetone: 'acetone', urineVolume: 'volume_ml' }
  };
  const fieldMap = map[section] || {};
  for (const [elId, key] of Object.entries(fieldMap)) {
    const elem = el(elId);
    if (elem && entry[key] !== undefined && entry[key] !== null) elem.value = entry[key];
  }
  // Scroll to section header
  const sectId = { fhr:'fhrSection', 'amniotic-fluid':'aflSection', cervix:'cervixSection',
    descent:'cervixSection', moulding:'mouldSection', contractions:'contrSection',
    vitals:'vitalsSection', temperature:'tempSection', medications:'medsSection', urine:'urineSection' }[section];
  document.getElementById(sectId)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/* ═══════════════════════════════════════════════════════════
   DATA LOADING & TABLE RENDERING
   ═══════════════════════════════════════════════════════════ */
async function loadSection(section) {
  try {
    const res = await fetch(`/api/partograph/${CASE_ID}/${section}`);
    const data = await res.json();
    if (data.success !== false) {
      state[section] = data.entries || [];
      renderSection(section);
    }
  } catch (e) { console.error('Load error:', section, e); }
}

function renderSection(section) {
  switch (section) {
    case 'fhr':            renderFHR(); break;
    case 'amniotic-fluid': renderAFL(); break;
    case 'cervix':         renderCervix(); break;
    case 'descent':        renderDescent(); break;
    case 'moulding':       renderMoulding(); break;
    case 'contractions':   renderContractions(); break;
    case 'vitals':         renderVitals(); break;
    case 'temperature':    renderTemp(); break;
    case 'medications':    renderMeds(); break;
    case 'urine':          renderUrine(); break;
  }
}

/* ── Actions cell ───────────────────────────── */
function actionBtns(section, entry) {
  return `<td class="no-print">
    <button class="btn btn-sm btn-outline-primary btn-edit" onclick='editEntry("${section}", ${JSON.stringify(entry)})'>
      <i class="fas fa-edit"></i>
    </button>
    <button class="btn btn-sm btn-outline-danger btn-delete ms-1" onclick='deleteEntry("${section}", ${entry.id})'>
      <i class="fas fa-trash"></i>
    </button>
  </td>`;
}

/* ── FHR ─────────────────────────────────────── */
function renderFHR() {
  const entries = state.fhr;
  // Alert check
  if (entries.length) {
    const last = entries[entries.length - 1].fhr_value;
    if (last > 160) showAlert('fhrAlerts', `⚠️ Fetal tachycardia: ${last} bpm (>160). Monitor closely.`, 'warning');
    else if (last < 120) showAlert('fhrAlerts', `⚠️ Fetal bradycardia: ${last} bpm (<120). Check fetal status.`, 'warning');
    else clearAlert('fhrAlerts');
  }
  // Chart
  const labels = entries.map(e => e.time);
  const vals   = entries.map(e => e.fhr_value);
  buildLineChart('fhrChart', labels, vals, {
    label: 'FHR (bpm)', color: '#1a73e8', min: 80, max: 200,
    annotations: {
      normalLow:  { type:'line', yMin:120, yMax:120, borderColor:'rgba(40,167,69,.5)', borderWidth:1, borderDash:[4,4] },
      normalHigh: { type:'line', yMin:160, yMax:160, borderColor:'rgba(220,53,69,.5)', borderWidth:1, borderDash:[4,4] },
      normalZone: { type:'box', yMin:120, yMax:160, backgroundColor:'rgba(40,167,69,.06)', borderWidth:0 }
    }
  });
  // Table
  const tb = el('fhrTable');
  tb.innerHTML = entries.map(e => `
    <tr>
      <td>${e.time}</td>
      <td><strong>${e.fhr_value}</strong> bpm</td>
      <td><span class="badge bg-${e.fhr_value >= 120 && e.fhr_value <= 160 ? 'success' : 'danger'}">
        ${e.fhr_value >= 120 && e.fhr_value <= 160 ? 'Normal' : e.fhr_value > 160 ? 'Tachycardia' : 'Bradycardia'}
      </span></td>
      ${actionBtns('fhr', e)}
    </tr>`).join('');
}

/* ── Amniotic Fluid ──────────────────────────── */
const AFL_META = {
  intact:   { cls:'afl-intact',   label:'I', text:'Intact' },
  clear:    { cls:'afl-clear',    label:'C', text:'Clear' },
  green:    { cls:'afl-green',    label:'G', text:'Green' },
  yellow:   { cls:'afl-yellow',   label:'Y', text:'Yellow' },
  ruptured: { cls:'afl-ruptured', label:'R', text:'Ruptured' }
};

function renderAFL() {
  const entries = state['amniotic-fluid'];
  // Alert
  const last = entries.length ? entries[entries.length - 1] : null;
  if (last && (last.status === 'green' || last.status === 'yellow')) {
    showAlert('aflAlerts', '⚠️ Meconium staining detected. Increase fetal monitoring. Prepare for neonatal resuscitation.', 'warning');
  } else clearAlert('aflAlerts');
  // Timeline
  const tl = el('aflTimeline');
  tl.innerHTML = entries.map(e => {
    const m = AFL_META[e.status] || {};
    return `<div class="timeline-box ${m.cls}" title="${m.text} at ${e.time}" onclick='editEntry("amniotic-fluid",${JSON.stringify(e)})'>
      <span>${m.label}</span><span class="tl-time">${e.time}</span>
    </div>`;
  }).join('') || '<span class="text-muted" style="font-size:.8rem;">No entries yet</span>';
  // Table
  el('aflTable').innerHTML = entries.map(e => {
    const m = AFL_META[e.status] || {};
    return `<tr><td>${e.time}</td><td><span class="badge" style="background:${m.cls}">${m.text}</span></td>${actionBtns('amniotic-fluid', e)}</tr>`;
  }).join('');
}

/* ── Cervix (main chart) ──────────────────────── */
function renderCervix() {
  const cx = state.cervix;
  const de = state.descent;

  // WHO partograph reference lines — 1hr intervals
  // Alert line : (0,4)→(6,10) at 1cm/hr
  // Action line: (4,4)→(10,10) same slope, 4hrs to the right
  const alertPts  = [];
  const actionPts = [];
  for (let h = 0; h <= 10; h += 1) {
    const al  = alertLine(h);
    const acl = actionLine(h);
    if (al  !== null) alertPts.push({ x: h, y: al });
    if (acl !== null) actionPts.push({ x: h, y: acl });
  }

  // Map cervix/descent to elapsed hours
  const cxData = cx.map(e => ({ x: hoursElapsed(e.time), y: e.dilatation_cm, entry: e }));
  const deData = de.map(e => ({ x: hoursElapsed(e.time), y: e.descent_value + 5, entry: e })); // shift +5 so -5 maps to 0

  // Destroy old chart
  if (charts.cervix) { charts.cervix.destroy(); delete charts.cervix; }

  const ctx = el('cervixChart');
  if (!ctx) return;

  charts.cervix = new Chart(ctx, {
    type: 'scatter',
    data: {
      datasets: [
        {
          label: 'Cervix (X)', data: cxData, borderColor: '#000', backgroundColor: '#000',
          pointRadius: 6, pointStyle: 'cross', showLine: true, tension: 0, order: 1
        },
        {
          label: 'Head Descent (O)', data: deData, borderColor: '#0dcaf0', backgroundColor: '#0dcaf0',
          pointRadius: 5, pointStyle: 'circle', showLine: true, tension: 0, borderDash: [4,4], order: 2
        },
        {
          label: 'Alert Line', data: alertPts, borderColor: '#FFA500', backgroundColor: 'transparent',
          borderWidth: 2, borderDash: [6,4], pointRadius: 0, showLine: true, order: 3
        },
        {
          label: 'Action Line', data: actionPts, borderColor: '#DC143C', backgroundColor: 'transparent',
          borderWidth: 2, borderDash: [6,4], pointRadius: 0, showLine: true, order: 4
        }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        annotation: {
          annotations: {
            normalZone: {
              type: 'box', xMin: 0, xMax: 24, yMin: 0, yMax: 10,
              backgroundColor: 'rgba(40,167,69,.05)', borderWidth: 0
            }
          }
        },
        tooltip: {
          callbacks: {
            label: ctx => {
              const ds = ctx.dataset.label;
              if (ds === 'Cervix (X)') return `${ds}: ${ctx.parsed.y} cm at ${ctx.raw.entry?.time||''}`;
              if (ds === 'Head Descent (O)') return `${ds}: ${(ctx.parsed.y - 5).toFixed(0)} at ${ctx.raw.entry?.time||''}`;
              return `${ds}: ${ctx.parsed.y.toFixed(1)} cm`;
            }
          }
        },
        legend: { position: 'top' }
      },
      scales: {
        x: { type: 'linear', min: 0, max: 12,
             title: { display: true, text: 'Hours in labour' },
             ticks: { stepSize: 1 } },
        y: { min: 0, max: 10,
             title: { display: true, text: 'Cervical dilatation (cm)' },
             ticks: { stepSize: 1 } }
      }
    }
  });

  // Progress panel
  const prog = el('cervixProgress');
  if (cx.length >= 2) {
    const last = cx[cx.length - 1];
    const prev = cx[cx.length - 2];
    const dh = hoursElapsed(last.time) - hoursElapsed(prev.time);
    const rate = dh > 0 ? ((last.dilatation_cm - prev.dilatation_cm) / dh).toFixed(2) : null;
    let rateMsg = '⚠️ Insufficient data', rateCls = 'warning';
    if (rate !== null) {
      if (rate >= 1.0)      { rateMsg = `✅ Adequate: ${rate} cm/hr`; rateCls = 'success'; }
      else if (rate >= 0.5) { rateMsg = `⚠️ Slow: ${rate} cm/hr — monitor`; rateCls = 'warning'; }
      else                  { rateMsg = `🔴 Very slow: ${rate} cm/hr — assess for CPD`; rateCls = 'danger'; }
    }
    const zone = cervixZone(hoursElapsed(last.time), last.dilatation_cm);
    prog.innerHTML = `
      <div class="col-md-6">
        <div class="alert alert-${rateCls} py-2 mb-0 text-center" style="font-size:.85rem;">
          <strong>Cervical change rate:</strong> ${rateMsg}
        </div>
      </div>
      <div class="col-md-6">
        <div class="alert alert-${zone.cls} py-2 mb-0 text-center" style="font-size:.85rem;">
          <strong>Current zone:</strong> ${zone.label} zone (${last.dilatation_cm} cm)
        </div>
      </div>`;
    // Zone alert
    if (zone.label === 'Action') {
      showAlert('cervixAlerts', '🔴 ACTION ZONE REACHED. Cervical dilatation has crossed the action line. Initiate augmentation or consider operative delivery.', 'critical');
    } else if (zone.label === 'Alert') {
      showAlert('cervixAlerts', '⚠️ Alert zone. Labour is slower than ideal. Heightened monitoring required.', 'warning');
    } else clearAlert('cervixAlerts');
  } else { prog.innerHTML = ''; }

  // Tables
  el('cervixTable').innerHTML = cx.map(e => {
    const h = hoursElapsed(e.time);
    const z = cervixZone(h, e.dilatation_cm);
    return `<tr><td>${e.time}</td><td>${e.dilatation_cm} cm</td>
      <td><span class="badge bg-${z.cls}">${z.label}</span></td>${actionBtns('cervix', e)}</tr>`;
  }).join('');
  el('descentTable').innerHTML = de.map(e =>
    `<tr><td>${e.time}</td><td>${e.descent_value >= 0 ? '+' : ''}${e.descent_value}</td>${actionBtns('descent', e)}</tr>`
  ).join('');
}

function renderDescent() { renderCervix(); } // descent shares the cervix chart

/* ── Moulding ─────────────────────────────────── */
const MLD_META = {
  '0':   { cls:'mld-0', text:'0 — None' },
  '+':   { cls:'mld-1', text:'+ Mild' },
  '++':  { cls:'mld-2', text:'++ Moderate' },
  '+++': { cls:'mld-3', text:'+++ Severe' }
};

function renderMoulding() {
  const entries = state.moulding;
  const last = entries.length ? entries[entries.length - 1] : null;
  if (last && last.grade === '+++') {
    showAlert('mouldAlerts', '🔴 SEVERE MOULDING. High risk of cephalopelvic disproportion (CPD). Reassess labour progress.', 'critical');
  } else clearAlert('mouldAlerts');
  const tl = el('mouldTimeline');
  tl.innerHTML = entries.map(e => {
    const m = MLD_META[e.grade] || {};
    return `<div class="timeline-box ${m.cls}" title="${m.text} at ${e.time}" onclick='editEntry("moulding",${JSON.stringify(e)})'>
      <span>${e.grade}</span><span class="tl-time">${e.time}</span>
    </div>`;
  }).join('') || '<span class="text-muted" style="font-size:.8rem;">No entries yet</span>';
  el('mouldTable').innerHTML = entries.map(e => {
    const m = MLD_META[e.grade] || {};
    return `<tr><td>${e.time}</td><td>${m.text}</td>${actionBtns('moulding', e)}</tr>`;
  }).join('');
}

/* ── Contractions ─────────────────────────────── */
function renderContractions() {
  const entries = state.contractions;
  const labels  = entries.map(e => e.time);
  const freqs   = entries.map(e => e.frequency);
  const colors  = entries.map(e =>
    e.intensity === 'severe' ? 'rgba(220,53,69,.85)' :
    e.intensity === 'moderate' ? 'rgba(255,193,7,.85)' : 'rgba(253,126,20,.4)'
  );

  if (charts.contr) { charts.contr.destroy(); delete charts.contr; }
  const ctx = el('contrChart');
  if (ctx) {
    charts.contr = new Chart(ctx, {
      type: 'bar',
      data: { labels, datasets: [{ label: 'Contractions/10 min', data: freqs, backgroundColor: colors, borderRadius: 4 }] },
      options: {
        responsive: true, maintainAspectRatio: false,
        scales: { y: { min: 0, max: 5, ticks: { stepSize: 1 }, title: { display: true, text: 'Freq / 10 min' } } },
        plugins: { legend: { display: false } }
      }
    });
  }

  // Summary
  const last = entries.length ? entries[entries.length - 1] : null;
  const summEl = el('contrSummary');
  if (last) {
    let cls = 'success', msg = '✅ Adequate contractions';
    if (last.frequency < 2) { cls = 'danger'; msg = '🔴 Inadequate (< 2/10 min). Consider oxytocin augmentation.'; }
    else if (last.frequency < 3) { cls = 'warning'; msg = '⚠️ Borderline (2–3/10 min). Monitor closely.'; }
    summEl.innerHTML = `<div class="alert alert-${cls} py-2 mb-2" style="font-size:.84rem;">
      <strong>Latest:</strong> ${last.frequency}/10 min | ${last.intensity} | ${last.duration_seconds}s — ${msg}</div>`;
  } else summEl.innerHTML = '';

  el('contrTable').innerHTML = entries.map(e =>
    `<tr><td>${e.time}</td><td>${e.frequency}</td><td>${e.intensity}</td><td>${e.duration_seconds}</td>${actionBtns('contractions', e)}</tr>`
  ).join('');
}

/* ── Vitals ───────────────────────────────────── */
function renderVitals() {
  const entries = state.vitals;
  const labels = entries.map(e => e.time);

  // BP chart
  buildLineChart('bpChart', labels,
    [entries.map(e => e.systolic_bp), entries.map(e => e.diastolic_bp)],
    {
      multiDataset: true,
      datasets: [
        { label: 'Systolic', borderColor: '#0d6efd', backgroundColor: 'rgba(13,110,253,.1)' },
        { label: 'Diastolic', borderColor: '#6ea8fe', backgroundColor: 'rgba(110,168,254,.08)' }
      ],
      min: 60, max: 180,
      annotations: {
        sys140: { type:'line', yMin:140, yMax:140, borderColor:'rgba(220,53,69,.5)', borderWidth:1, borderDash:[4,4] },
        dia90:  { type:'line', yMin:90,  yMax:90,  borderColor:'rgba(220,53,69,.3)', borderWidth:1, borderDash:[4,4] }
      }
    }
  );

  // Pulse chart
  buildLineChart('pulseChart', labels, entries.map(e => e.pulse_bpm), {
    label: 'Pulse (bpm)', color: '#198754', min: 40, max: 140,
    annotations: {
      p100: { type:'line', yMin:100, yMax:100, borderColor:'rgba(220,53,69,.4)', borderWidth:1, borderDash:[4,4] },
      p60:  { type:'line', yMin:60,  yMax:60,  borderColor:'rgba(220,53,69,.3)', borderWidth:1, borderDash:[4,4] }
    }
  });

  // Alerts
  const last = entries.length ? entries[entries.length - 1] : null;
  if (last) {
    if (last.systolic_bp > 160 || last.diastolic_bp > 110) {
      showAlert('vitalsAlerts', `🔴 SEVERE HYPERTENSION: ${last.systolic_bp}/${last.diastolic_bp} mmHg. Risk of eclampsia. Intervention required.`, 'critical');
    } else if (last.systolic_bp < 90 || last.diastolic_bp < 60) {
      showAlert('vitalsAlerts', `⚠️ Hypotension: ${last.systolic_bp}/${last.diastolic_bp} mmHg. Check for bleeding or dehydration.`, 'warning');
    } else clearAlert('vitalsAlerts');
  }

  el('vitalsTable').innerHTML = entries.map(e => {
    let cls = 'success', label = 'Normal';
    if (e.systolic_bp > 160 || e.diastolic_bp > 110) { cls = 'danger'; label = 'Severe HTN'; }
    else if (e.systolic_bp > 140 || e.diastolic_bp > 90) { cls = 'warning'; label = 'Elevated'; }
    return `<tr><td>${e.time}</td><td>${e.systolic_bp||'—'}</td><td>${e.diastolic_bp||'—'}</td>
      <td>${e.pulse_bpm||'—'}</td><td><span class="badge bg-${cls}">${label}</span></td>${actionBtns('vitals', e)}</tr>`;
  }).join('');
}

/* ── Temperature ──────────────────────────────── */
function renderTemp() {
  const entries = state.temperature;
  buildLineChart('tempChart', entries.map(e => e.time), entries.map(e => e.celsius), {
    label: 'Temp (°C)', color: '#9b59b6', min: 34, max: 41,
    annotations: {
      normal: { type:'line', yMin:37.5, yMax:37.5, borderColor:'rgba(220,53,69,.5)', borderWidth:1, borderDash:[4,4] },
      zone:   { type:'box', yMin:34, yMax:37.5, backgroundColor:'rgba(40,167,69,.05)', borderWidth:0 }
    }
  });
  const last = entries.length ? entries[entries.length - 1] : null;
  if (last && last.celsius > 38) {
    showAlert('tempAlerts', `⚠️ FEVER: ${last.celsius}°C. Assess for chorioamnionitis. Consider antibiotics.`, 'warning');
  } else clearAlert('tempAlerts');
  el('tempTable').innerHTML = entries.map(e => {
    const fever = e.celsius > 37.5;
    return `<tr><td>${e.time}</td><td>${e.celsius}°C</td>
      <td><span class="badge bg-${fever ? 'danger' : 'success'}">${fever ? 'Fever' : 'Normal'}</span></td>
      ${actionBtns('temperature', e)}</tr>`;
  }).join('');
}

/* ── Medications ──────────────────────────────── */
function renderMeds() {
  el('medsTable').innerHTML = state.medications.map(e =>
    `<tr><td>${e.time}</td><td>${e.medication_type}</td><td>${e.medication_name||'—'}</td>
     <td>${e.dose}</td><td>${e.route}</td>${actionBtns('medications', e)}</tr>`
  ).join('');
}

/* ── Urine ────────────────────────────────────── */
function renderUrine() {
  const entries = state.urine;
  const last = entries.length ? entries[entries.length - 1] : null;
  if (last) {
    if (['++','+++'].includes(last.protein)) {
      showAlert('urineAlerts', `⚠️ Significant proteinuria (${last.protein}). Monitor for preeclampsia.`, 'warning');
    } else if (last.acetone === 'present') {
      showAlert('urineAlerts', '⚠️ Acetone in urine. Increase IV/oral hydration.', 'warning');
    } else clearAlert('urineAlerts');
  }
  el('urineTable').innerHTML = entries.map(e =>
    `<tr><td>${e.time}</td><td>${e.protein}</td><td>${e.acetone}</td><td>${e.volume_ml||'—'}</td>${actionBtns('urine', e)}</tr>`
  ).join('');
}

/* ═══════════════════════════════════════════════════════════
   CHART BUILDER UTILITY
   ═══════════════════════════════════════════════════════════ */
function buildLineChart(canvasId, labels, data, opts = {}) {
  if (charts[canvasId]) { charts[canvasId].destroy(); delete charts[canvasId]; }
  const ctx = el(canvasId);
  if (!ctx) return;

  let datasets;
  if (opts.multiDataset) {
    datasets = opts.datasets.map((ds, i) => ({
      label: ds.label,
      data: data[i],
      borderColor: ds.borderColor,
      backgroundColor: ds.backgroundColor,
      tension: 0.3, fill: true, pointRadius: 4
    }));
  } else {
    datasets = [{
      label: opts.label || 'Value',
      data,
      borderColor: opts.color || '#1a73e8',
      backgroundColor: (opts.color || '#1a73e8') + '22',
      tension: 0.3, fill: true, pointRadius: 4
    }];
  }

  charts[canvasId] = new Chart(ctx, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        annotation: { annotations: opts.annotations || {} },
        legend: { display: opts.multiDataset || false }
      },
      scales: {
        x: { title: { display: true, text: 'Time' } },
        y: {
          min: opts.min, max: opts.max,
          title: { display: true, text: opts.label || 'Value' }
        }
      }
    }
  });
}

/* ═══════════════════════════════════════════════════════════
   SIDEBAR: Summary + Alerts
   ═══════════════════════════════════════════════════════════ */
async function refreshSidebar() {
  try {
    const [sumRes, altRes] = await Promise.all([
      fetch(`/api/partograph/${CASE_ID}/summary`),
      fetch(`/api/partograph/${CASE_ID}/alerts`)
    ]);
    const sum = await sumRes.json();
    const alt = await altRes.json();

    if (sum.success) {
      // Status badge
      const badge = el('statusBadge');
      const txt   = el('statusText');
      badge.className = `status-badge ${sum.status_badge}`;
      txt.textContent = sum.status_badge === 'action' ? '🔴 Action Zone — INTERVENE' :
                        sum.status_badge === 'alert'  ? '🟡 Alert Zone — Monitor Closely' :
                                                        '🟢 Normal Progression';

      // Summary panel
      const rate = sum.cervical_change_rate;
      el('summaryPanel').innerHTML = `
        <div class="stat-item"><span class="stat-label">Time in labour</span><span class="stat-value">${sum.time_in_labor_hours?.toFixed(1)||'—'} hrs</span></div>
        <div class="stat-item"><span class="stat-label">Latest FHR</span><span class="stat-value">${sum.latest_fhr||'—'} bpm</span></div>
        <div class="stat-item"><span class="stat-label">Cervical dilation</span><span class="stat-value">${sum.latest_cervical_dilatation||'—'} cm</span></div>
        <div class="stat-item"><span class="stat-label">Cervix rate</span><span class="stat-value">${rate !== null ? rate+' cm/hr' : '—'}</span></div>
        <div class="stat-item"><span class="stat-label">BP</span><span class="stat-value">${sum.latest_systolic_bp||'—'}/${sum.latest_diastolic_bp||'—'}</span></div>
        <div class="stat-item"><span class="stat-label">Pulse</span><span class="stat-value">${sum.latest_pulse||'—'} bpm</span></div>
        <div class="stat-item"><span class="stat-label">Temperature</span><span class="stat-value">${sum.latest_temperature||'—'}°C</span></div>`;
    }

    if (alt.success) {
      const a = alt.alerts;
      const all = [...(a.critical||[]).map(x=>({...x,type:'critical'})),
                   ...(a.warning||[]).map(x=>({...x,type:'warning'})),
                   ...(a.info||[]).map(x=>({...x,type:'info'}))];
      el('alertCount').textContent = (a.critical?.length||0) + (a.warning?.length||0);
      el('alertsPanel').innerHTML = all.length
        ? all.map(x => `<div class="parto-alert ${x.type}" style="font-size:.8rem;">${x.message}</div>`).join('')
        : '<div class="text-center text-muted py-2" style="font-size:.82rem;">No alerts</div>';
    }
  } catch (e) { console.error('Sidebar error', e); }
}

/* ═══════════════════════════════════════════════════════════
   INIT
   ═══════════════════════════════════════════════════════════ */
async function init() {
  prefillTimes();
  const sections = ['fhr','amniotic-fluid','cervix','descent','moulding','contractions','vitals','temperature','medications','urine'];
  await Promise.all(sections.map(s => loadSection(s)));
  await refreshSidebar();

  // Auto-save indicator (cosmetic — all saves are per-entry)
  setInterval(() => {
    const now = new Date();
    const t = now.getHours().toString().padStart(2,'0') + ':' + now.getMinutes().toString().padStart(2,'0');
    const ind = el('autoSaveIndicator');
    if (ind) ind.textContent = `Last refreshed: ${t}`;
    refreshSidebar();
  }, 60000);
}

document.addEventListener('DOMContentLoaded', init);
