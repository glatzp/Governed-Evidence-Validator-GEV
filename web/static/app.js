/* Governed Evidence Validator — Q&A Wizard */

'use strict';

// ── State ────────────────────────────────────────────────────────────────────

const state = {
  txId: null,
  currentStep: 1,
  pendingFiles: [],   // File objects waiting for upload
  docInfo: null,      // response from /api/qa/upload
  question: '',
  topic: '',
  premiseAbsent: false,
  sliderValue: 50,
  precisionLabel: 'Balanced',
  threshold: 85,
  pass1Prompt: '',
  pass1PacketChars: 0,
  pass1AtomCount: 0,
  pass2Prompt: '',
  pass2PacketChars: 0,
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function el(id) { return document.getElementById(id); }

function showError(containerId, message) {
  const c = el(containerId);
  if (!c) return;
  c.innerHTML = `<div class="alert alert-error"><span class="alert-icon">⚠</span><span>${escHtml(message)}</span></div>`;
  c.style.display = 'block';
}

function clearError(containerId) {
  const c = el(containerId);
  if (c) { c.innerHTML = ''; c.style.display = 'none'; }
}

function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function setLoading(btn, loading, originalText) {
  if (loading) {
    btn.disabled = true;
    btn.dataset.original = btn.innerHTML;
    btn.innerHTML = `<span class="spinner"></span> Working…`;
  } else {
    btn.disabled = false;
    btn.innerHTML = originalText || btn.dataset.original || 'Continue';
  }
}

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({ detail: res.statusText }));
  if (!res.ok) throw new Error(data.detail || `Request failed (${res.status})`);
  return data;
}

function fmtNumber(n) {
  return Number(n).toLocaleString();
}

// ── Sidebar step state ────────────────────────────────────────────────────────

function setStep(n) {
  state.currentStep = n;
  document.querySelectorAll('.step-panel').forEach(p => p.classList.remove('active'));
  const panel = el(`panel-${n}`);
  if (panel) panel.classList.add('active');

  document.querySelectorAll('.step-item').forEach(item => {
    const s = parseInt(item.dataset.step, 10);
    item.classList.remove('active', 'completed');
    if (s === n) item.classList.add('active');
    else if (s < n) item.classList.add('completed');
  });

  window.scrollTo(0, 0);
}

// ── Session start ─────────────────────────────────────────────────────────────

async function startSession() {
  const data = await api('POST', '/api/qa/start');
  state.txId = data.tx_id;
}

// ── STEP 1 — UPLOAD ───────────────────────────────────────────────────────────

function renderFileChips() {
  const wrap = el('file-chips');
  wrap.innerHTML = '';
  state.pendingFiles.forEach((f, i) => {
    const chip = document.createElement('div');
    chip.className = 'file-chip';
    chip.innerHTML = `
      <span class="file-chip-icon">${f.name.endsWith('.pdf') ? '📄' : '📃'}</span>
      <span>${escHtml(f.name)}</span>
      <button class="file-chip-remove" data-idx="${i}" title="Remove">×</button>`;
    wrap.appendChild(chip);
  });
  el('btn-step1-next').disabled = state.pendingFiles.length === 0;
}

function addFiles(files) {
  const allowed = ['.pdf', '.txt'];
  Array.from(files).forEach(f => {
    const ext = f.name.slice(f.name.lastIndexOf('.')).toLowerCase();
    if (!allowed.includes(ext)) return;
    if (!state.pendingFiles.find(p => p.name === f.name && p.size === f.size)) {
      state.pendingFiles.push(f);
    }
  });
  renderFileChips();
}

function initUpload() {
  const zone = el('upload-zone');
  const input = el('file-input');

  el('upload-browse-link').addEventListener('click', () => input.click());
  zone.addEventListener('click', () => input.click());
  input.addEventListener('change', () => { addFiles(input.files); input.value = ''; });

  zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('dragover'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
  zone.addEventListener('drop', e => {
    e.preventDefault();
    zone.classList.remove('dragover');
    addFiles(e.dataTransfer.files);
  });

  el('file-chips').addEventListener('click', e => {
    const btn = e.target.closest('.file-chip-remove');
    if (!btn) return;
    state.pendingFiles.splice(parseInt(btn.dataset.idx, 10), 1);
    renderFileChips();
  });

  el('btn-step1-next').addEventListener('click', async () => {
    clearError('error-1');
    const btn = el('btn-step1-next');
    setLoading(btn, true);
    try {
      if (!state.txId) await startSession();

      const form = new FormData();
      form.append('tx_id', state.txId);
      state.pendingFiles.forEach(f => form.append('files', f));

      const res = await fetch('/api/qa/upload', { method: 'POST', body: form });
      const data = await res.json().catch(() => ({ detail: res.statusText }));
      if (!res.ok) throw new Error(data.detail || 'Upload failed');

      state.docInfo = data;
      state.topic = '';
      state.question = '';
      populateDocReview(data);
      renderStep2Intake();
      setStep(2);
    } catch (e) {
      showError('error-1', e.message);
    } finally {
      setLoading(btn, false);
    }
  });
}

// ── STEP 2 — QUESTION + PRECISION ────────────────────────────────────────────

function sliderToLabel(v) {
  if (v <= 20) return 'Strict';
  if (v <= 40) return 'Moderately Strict';
  if (v <= 60) return 'Balanced';
  if (v <= 80) return 'Moderately Exploratory';
  return 'Exploratory';
}

function renderStep2Intake() {
  const container = el('topic-routing-container');
  if (!container || !state.docInfo) return;
  
  container.innerHTML = '';
  
  const isStructured = state.docInfo.is_structured;
  
  const label = document.createElement('label');
  label.className = 'input-label';
  label.setAttribute('for', 'topic-field');
  label.textContent = 'Topic or area of concern';
  container.appendChild(label);
  
  if (isStructured) {
    // Dropdown track
    const select = document.createElement('select');
    select.id = 'topic-field';
    select.className = 'text-input';
    select.style.height = '48px';
    
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = 'Select a topic...';
    select.appendChild(placeholder);
    
    state.docInfo.section_headers.forEach(h => {
      if (h !== 'DOCUMENT' && h !== 'PREAMBLE') {
        const opt = document.createElement('option');
        opt.value = h;
        opt.textContent = h;
        select.appendChild(opt);
      }
    });
    
    container.appendChild(select);
    
    if (state.topic) {
      select.value = state.topic;
    }
  } else {
    // Text input track
    const input = document.createElement('input');
    input.id = 'topic-field';
    input.type = 'text';
    input.className = 'text-input';
    input.placeholder = 'Enter topic...';
    
    container.appendChild(input);
    
    if (state.topic) {
      input.value = state.topic;
    }
  }
}

function initQuestion() {
  el('btn-step2-back').addEventListener('click', () => setStep(1));

  el('btn-step2-next').addEventListener('click', () => {
    clearError('error-2');
    const topicField = el('topic-field');
    const t = topicField ? topicField.value.trim() : '';
    if (!t) {
      showError('error-2', 'Please select or enter a topic concern.');
      return;
    }
    state.topic = t;
    
    const qField = el('question-field');
    if (qField) {
      qField.value = state.question || '';
    }
    setStep(3);
  });
}

// ── STEP 3 — DOCUMENT REVIEW ──────────────────────────────────────────────────

function populateDocReview(info) {
  el('stat-words').textContent = fmtNumber(info.word_count);
  el('stat-sections').textContent = info.section_count;
  el('stat-files').textContent = info.filenames.length;

  const badgeWrap = el('structure-badge-wrap');
  const cls = info.is_structured ? 'structured' : 'flat';
  const icon = info.is_structured ? '🗂' : '📄';
  const badgeText = info.is_structured
    ? `Your document was read as ${info.section_count} named section${info.section_count !== 1 ? 's' : ''}.`
    : 'Your document was read as a single block of text — no section headings were found.';
  badgeWrap.innerHTML = `<div class="structure-badge ${cls}">${icon} ${escHtml(badgeText)}</div>`;

  const list = el('section-outline-list');
  list.innerHTML = '';
  info.section_headers.forEach(h => {
    const li = document.createElement('li');
    li.className = 'section-outline-item';
    li.innerHTML = `<div class="section-dot"></div><span>${escHtml(h)}</span>`;
    list.appendChild(li);
  });

  populateDiagnosticBanner(info.diagnostics || null);
}

function populateDiagnosticBanner(diagnostics) {
  const banner = el('diagnostic-banner');
  if (banner) {
    banner.innerHTML = '';
  }
}

function populateTaskReview() {
  // No-op for backward compatibility
}

function initDocReview() {
  el('btn-step3-back').addEventListener('click', () => {
    setStep(2);
  });
  
  el('btn-step3-approve').addEventListener('click', async () => {
    clearError('error-3');
    const qField = el('question-field');
    const q = qField ? qField.value.trim() : '';
    if (!q) {
      showError('error-3', 'Please enter your question.');
      return;
    }
    state.question = q;
    
    const btn = el('btn-step3-approve');
    setLoading(btn, true);
    try {
      state.sliderValue = 50;
      state.precisionLabel = 'Balanced';
      const qData = await api('POST', '/api/qa/question', {
        tx_id: state.txId,
        question: q,
        slider_value: 50,
        topic: state.topic,
      });
      state.threshold = qData.threshold;

      const data = await api('POST', '/api/qa/approve', { tx_id: state.txId });
      state.sessionDocChars = data.session_doc_chars;
      
      el('session-doc-filename').textContent = `gev_session_${state.txId}.md`;
      el('session-doc-size').textContent = fmtNumber(data.session_doc_chars);
      
      setStep(4);
    } catch (e) {
      showError('error-3', e.message);
    } finally {
      setLoading(btn, false, 'Generate Session Document');
    }
  });
}

function initTaskReview() {
  // Collapsed into Step 3
}

function initExecution() {
  // Collapsed into Step 4
}

function initValidation() {
  el('btn-download-session-document').addEventListener('click', async () => {
    clearError('error-4');
    const btn = el('btn-download-session-document');
    setLoading(btn, true);
    try {
      const res = await fetch('/api/qa/generate-session-document', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tx_id: state.txId })
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(data.detail || 'Download failed');
      }
      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `gev_session_${state.txId}.md`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
    } catch (e) {
      showError('error-4', e.message);
    } finally {
      setLoading(btn, false, '⬇ Download Session Document');
    }
  });

  el('btn-run-verification').addEventListener('click', runVerification);
  el('btn-copy-primer-prompt').addEventListener('click', () => {
    copyText(el('session-primer-prompt').value, el('btn-copy-primer-prompt'));
  });
  el('btn-refresh-audit').addEventListener('click', loadAuditLog);
  el('btn-download-audit').addEventListener('click', () => {
    window.location.href = `/api/qa/audit/download?tx_id=${state.txId}`;
  });
  el('btn-start-over').addEventListener('click', startOver);
}

async function runVerification() {
  clearError('error-4');
  const response = el('session-raw-response-input').value.trim();
  if (!response) {
    showError('error-4', "Please paste the LLM's complete response before running verification.");
    return;
  }

  const btn = el('btn-run-verification');
  setLoading(btn, true);
  try {
    const data = await api('POST', '/api/qa/validate', { tx_id: state.txId, raw_response: response });
    renderResults(data);
    loadAuditLog();
  } catch (e) {
    showError('error-4', e.message);
    setLoading(btn, false);
  }
}


function copyText(text, btn) {
  navigator.clipboard.writeText(text).then(() => {
    const orig = btn.textContent;
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.textContent = orig; }, 1800);
  }).catch(() => {
    // fallback
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    const orig = btn.textContent;
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.textContent = orig; }, 1800);
  });
}


async function submitPass1() {
  clearError('error-6');
  const response = el('pass1-response-input').value.trim();
  if (!response) { showError('error-6', 'Please paste the LLM response before submitting.'); return; }

  const btn = el('btn-submit-pass1');
  setLoading(btn, true);
  try {
    const data = await api('POST', '/api/qa/pass1', { tx_id: state.txId, response });

    if (data.empty) {
      showError('error-6',
        'The LLM returned an empty response — it found no responsive information in the document. ' +
        'Try rephrasing your question or using a different LLM.');
      return;
    }

    state.pass1AtomCount = data.atom_count;
    state.pass2PacketChars = data.pass2_packet_chars;

    el('pass2-packet-size').textContent = fmtNumber(data.pass2_packet_chars);

    const answerWord = data.atom_count === 1 ? 'answer' : 'answers';
    el('pass1-success-text').textContent =
      `Received. ${data.atom_count} ${answerWord} found. Now paste the LLM's follow-up response below.`;

    el('section-pass2').style.display = 'block';
    el('section-pass2').scrollIntoView({ behavior: 'smooth', block: 'start' });

    // Disable pass1 submit area
    el('pass1-response-input').disabled = true;
    btn.disabled = true;
    btn.textContent = '✓ Response Received';
  } catch (e) {
    showError('error-6', e.message);
  } finally {
    if (!el('pass1-response-input').disabled) setLoading(btn, false);
  }
}

async function submitPass2() {
  clearError('error-6');
  const response = el('pass2-response-input').value.trim();
  if (!response) { showError('error-6', 'Please paste the Pass 2 LLM response before submitting.'); return; }

  const btn = el('btn-submit-pass2');
  setLoading(btn, true);
  try {
    const data = await api('POST', '/api/qa/pass2', { tx_id: state.txId, response });
    renderResults(data);
    loadAuditLog();
  } catch (e) {
    showError('error-6', e.message);
    setLoading(btn, false);
  }
}

function atomTypeLabel(type) {
  const map = {
    direct_quote:       'Exact Quote',
    paraphrase:         'Paraphrase',
    procedural_answer:  'Procedural Answer',
    unknown:            'Claim',
  };
  return map[type] || '';
}

function renderResults(data) {
  const grounded = data.grounding_score.grounded;
  const total = data.grounding_score.total;
  const pct = total > 0 ? Math.round((grounded / total) * 100) : 0;

  el('grounding-score-value').textContent = `${grounded} of ${total} answers verified`;
  el('score-fill').style.width = `${pct}%`;
  el('score-sub-label').textContent =
    `Verification level: ${state.precisionLabel} · ${pct}% verified as exact quotes`;

  const container = el('verdict-cards');
  container.innerHTML = '';

  const firstAtom = data.atoms[0];
  const premiseAbsent = firstAtom ? !!firstAtom.premise_absent : false;
  const topic = firstAtom ? firstAtom.topic : '';

  if (premiseAbsent) {
    const absenceBlock = document.createElement('div');
    absenceBlock.className = 'absence-detected-section';
    
    let findingsListHtml = '';
    data.atoms.forEach(atom => {
      findingsListHtml += `
        <li class="absence-list-item">
          — <span class="absence-claim-text">${escHtml(atom.text)}</span>: 
          <span class="verdict-badge absent" style="margin-left: 8px;">Absent</span>
        </li>`;
    });

    absenceBlock.innerHTML = `
      <div class="absence-title">
        ⚠️ Absence Detected — No language found addressing ${escHtml(topic || state.topic)}
      </div>
      <div class="absence-subtitle">
        The following findings relate to this area:
      </div>
      <ul class="absence-list">
        ${findingsListHtml}
      </ul>
    `;
    container.appendChild(absenceBlock);
  }

  data.atoms.forEach(atom => {
    const { badge, badgeClass, detail } = formatVerdict(atom);
    const typeLabel = atomTypeLabel(atom.atom_type || '');
    const atomMeta = typeLabel
      ? `${escHtml(atom.atom_id)} · ${escHtml(typeLabel)}`
      : escHtml(atom.atom_id);
    const card = document.createElement('div');
    card.className = 'verdict-card';
    card.innerHTML = `
      <div class="verdict-card-header">
        <div style="flex:1">
          <div class="verdict-atom-id">${atomMeta}</div>
          <div class="verdict-atom-text">${escHtml(atom.text || '')}</div>
        </div>
        <div class="verdict-badge ${badgeClass}">${badge}</div>
      </div>
      <div class="verdict-card-detail">${detail}</div>`;
    container.appendChild(card);
  });

  el('section-results').style.display = 'block';
  el('section-results').scrollIntoView({ behavior: 'smooth', block: 'start' });

  // Disable pass2 area
  el('pass2-response-input').disabled = true;
  const btn = el('btn-submit-pass2');
  btn.disabled = true;
  btn.textContent = '✓ Verification Complete';

  // Disable consolidated session area
  const sessionInput = el('session-raw-response-input');
  if (sessionInput) sessionInput.disabled = true;
  const verifyBtn = el('btn-run-verification');
  if (verifyBtn) {
    verifyBtn.disabled = true;
    verifyBtn.textContent = '✓ Verification Complete';
  }
}

function formatProvenance(prov) {
  if (!prov) return '';
  const pageStr = prov.page !== null && prov.page !== undefined ? prov.page : 'None';
  return `
    <div class="provenance-details-block">
      <div class="provenance-title">Provenance Verification</div>
      <div class="provenance-grid">
        <div class="provenance-label">Source:</div>
        <div class="provenance-value-mono">${escHtml(prov.source_file)}</div>
        
        <div class="provenance-label">Section:</div>
        <div>${escHtml(prov.section)}</div>
        
        <div class="provenance-label">Page:</div>
        <div>${pageStr}</div>
        
        <div class="provenance-label">Unit:</div>
        <div class="provenance-value-mono" style="color: var(--purple-mid);">${escHtml(prov.primary_unit_id)}</div>
        
        <div class="provenance-label">Offsets:</div>
        <div class="provenance-value-mono">${prov.char_start}–${prov.char_end}</div>
        
        <div class="provenance-label">Extraction method:</div>
        <div><span class="provenance-pill">${escHtml(prov.extraction_method)}</span></div>
      </div>
    </div>
  `;
}

function formatVerdict(atom) {
  const v = atom.verdict || '';
  const passage = atom.proposed_passage || '';
  const section = atom.passage_section || '';
  const score = atom.match_score || 0;
  const nearScore = atom.near_miss_score || 0;
  const nearText = atom.near_miss_text || '';

  let citation = section;
  if (atom.passage_document) {
    citation = `${atom.passage_document} — ${section} [${atom.passage_authority || 'Primary Authority'}]`;
  }

  let badge, badgeClass, detail;

  if (v === 'GROUNDED') {
    badge = '✓ Pass';
    badgeClass = 'pass';
    detail = `This answer was verified as an exact quote from your document.`;
    if (citation) detail += `<div class="verdict-section-ref">Found in: ${escHtml(citation)}</div>`;
    if (passage) detail += `<div class="verdict-passage">"${escHtml(passage)}"</div>`;
    if (atom.provenance) {
      detail += formatProvenance(atom.provenance);
    }

  } else if (v === 'HUMAN_REVIEW_REQUIRED') {
    badge = '⚠ Review Needed';
    badgeClass = 'review';
    if (atom.evidence_located) {
      detail = `Supporting language was found in your document, but this answer is paraphrased rather than an exact quote. It cannot be automatically verified — a person should review it before relying on it.`;
      if (citation) detail += `<div class="verdict-section-ref">Found in: ${escHtml(citation)}</div>`;
      if (passage) detail += `<div class="verdict-passage">${escHtml(passage)}</div>`;
    } else {
      detail = `No supporting language was found in your document for this answer. A person should review it before relying on it.`;
      if (passage) {
        detail += `<div class="verdict-passage" style="opacity:.7">${escHtml(passage)}</div>`;
      }
    }

  } else if (v === 'CITATION_UNVERIFIED') {
    badge = '✗ Fail';
    badgeClass = 'fail';
    detail = `The LLM proposed a passage, but the program could not find it in your document. Verify manually before relying on this answer.`;
    if (passage) detail += `<div class="verdict-passage" style="opacity:.75">${escHtml(passage)}</div>`;
    if (citation) detail += `<div class="verdict-section-ref">Claimed location: ${escHtml(citation)}</div>`;
    if (nearText && nearText !== passage) {
      detail += `<div class="score-detail">Closest match found: "${escHtml(nearText.slice(0, 140))}${nearText.length > 140 ? '…' : ''}"</div>`;
    }

  } else if (v === 'CITATION_REQUIRED') {
    badge = '✗ Fail';
    badgeClass = 'fail';
    detail = `No supporting passage was found in your document for this answer.`;

  } else if (v === 'Absent') {
    badge = '⚠️ Absent';
    badgeClass = 'absent';
    detail = `The premise/topic this claim depends on is missing from the document.`;

  } else if (v === 'LLM_ERROR') {
    badge = '✗ Fail';
    badgeClass = 'fail';
    detail = `A processing error occurred. This answer could not be verified.`;

  } else {
    badge = 'Review Needed';
    badgeClass = 'review';
    detail = 'This answer requires manual verification before it can be relied upon.';
  }

  return { badge, badgeClass, detail };
}

async function loadAuditLog() {
  try {
    const data = await api('GET', `/api/qa/audit?tx_id=${state.txId}`);
    const body = el('audit-log-body');
    body.innerHTML = '';
    if (!data.entries || data.entries.length === 0) {
      body.innerHTML = '<div class="audit-entry"><span class="audit-ts">—</span><span class="audit-event">No entries yet.</span></div>';
      return;
    }
    data.entries.forEach(entry => {
      const row = document.createElement('div');
      row.className = 'audit-entry';
      const ts = entry.timestamp ? entry.timestamp.slice(0, 19).replace('T', ' ') : '—';
      const detail = entry.details ? JSON.stringify(entry.details).slice(0, 120) : '';
      row.innerHTML = `
        <span class="audit-ts">${escHtml(ts)}</span>
        <span class="audit-event">${escHtml(entry.event_type || entry.event || '')}</span>
        <span class="audit-detail">${escHtml(detail)}</span>`;
      body.appendChild(row);
    });
    body.scrollTop = body.scrollHeight;
  } catch (_) {
    // non-fatal
  }
}

async function startOver() {
  state.pendingFiles = [];
  state.txId = null;
  state.docInfo = null;
  state.question = '';
  state.topic = '';
  state.premiseAbsent = false;
  state.sliderValue = 50;
  state.precisionLabel = 'Balanced';
  state.threshold = 85;
  state.pass1Prompt = '';
  state.pass1AtomCount = 0;
  state.pass2Prompt = '';
  state.sessionDocChars = 0;

  // Reset UI
  el('file-chips').innerHTML = '';
  el('btn-step1-next').disabled = true;

  const topicContainer = el('topic-routing-container');
  if (topicContainer) topicContainer.innerHTML = '';

  const qField = el('question-field');
  if (qField) qField.value = '';

  // Reset Step 4 inputs
  const sessionInput = el('session-raw-response-input');
  if (sessionInput) {
    sessionInput.value = '';
    sessionInput.disabled = false;
  }
  const verifyBtn = el('btn-run-verification');
  if (verifyBtn) {
    verifyBtn.disabled = false;
    verifyBtn.textContent = 'Run Verification';
  }
  const docSize = el('session-doc-size');
  if (docSize) docSize.textContent = '—';
  const docFilename = el('session-doc-filename');
  if (docFilename) docFilename.textContent = 'gev_session_document.md';

  // Reset legacy elements to avoid breaking other parts of code safely
  if (el('question-input')) el('question-input').value = '';
  if (el('precision-slider')) el('precision-slider').value = 50;
  if (el('slider-label-text')) el('slider-label-text').textContent = 'Balanced';
  if (el('review-topic')) el('review-topic').textContent = '—';
  if (el('step-2-topic-container')) el('step-2-topic-container').innerHTML = '';
  if (el('step-2-question-container')) el('step-2-question-container').style.display = 'none';

  if (el('pass1-response-input')) {
    el('pass1-response-input').value = '';
    el('pass1-response-input').disabled = false;
  }
  if (el('btn-submit-pass1')) {
    el('btn-submit-pass1').disabled = false;
    el('btn-submit-pass1').textContent = 'Submit Response';
  }
  if (el('pass2-response-input')) {
    el('pass2-response-input').value = '';
    el('pass2-response-input').disabled = false;
  }
  if (el('btn-submit-pass2')) {
    el('btn-submit-pass2').textContent = 'Run Verification';
  }
  if (el('section-pass2')) el('section-pass2').style.display = 'none';
  if (el('section-results')) el('section-results').style.display = 'none';
  if (el('pass2-prompt-display')) el('pass2-prompt-display').value = '';
  if (el('pass1-success-notice')) el('pass1-success-notice').style.display = '';
  el('verdict-cards').innerHTML = '';
  el('audit-log-body').innerHTML = '';
  ['error-1','error-2','error-3','error-4','error-6'].forEach(clearError);

  await startSession();
  setStep(1);
}

// ── Boot ──────────────────────────────────────────────────────────────────────

async function boot() {
  await startSession();
  initUpload();
  initQuestion();
  initDocReview();
  initTaskReview();
  initExecution();
  initValidation();
  setStep(1);
}

document.addEventListener('DOMContentLoaded', boot);
