
    requireLogin();
    applyStoredTheme('theme-neutral');
    mountAvatarDropdown();

    // fx-toast helper
    function fxToast(msg) {
      const el = document.getElementById('fx-toast');
      if (!el) return;
      el.textContent = msg;
      el.classList.add('is-visible');
      clearTimeout(fxToast._t);
      fxToast._t = setTimeout(() => el.classList.remove('is-visible'), 2200);
    }

    // Track latest result for export
    let __lastScanResult = null;

    function copyScanResult() {
      const text = document.getElementById('result-summary').textContent || '';
      const fb = () => document.execCommand && document.execCommand('copy');
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(
          () => fxToast('Copied to clipboard'),
          () => { fb(); fxToast('Copied to clipboard'); }
        );
      } else {
        fb(); fxToast('Copied to clipboard');
      }
    }

    function downloadScanJson() {
      if (!__lastScanResult) { fxToast('No result yet'); return; }
      try {
        const blob = new Blob([JSON.stringify(__lastScanResult, null, 2)], { type: 'application/json' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'mediscan-' + Date.now() + '.json';
        document.body.appendChild(a);
        a.click();
        a.remove();
        fxToast('Downloaded JSON');
      } catch (_) { fxToast('Download failed'); }
    }

    // ---- Auto-fill patient_id for patient accounts ----
    (async function () {
      try {
        const meResp = await apiFetch('/me');
        if (!meResp || !meResp.ok) return;
        const me = await meResp.json();
        const idInput = document.getElementById('patient-id');
        const help = document.getElementById('patient-id-help');
        if (me.role === 'patient') {
          if (me.patient_id) {
            idInput.value = String(me.patient_id);
            idInput.readOnly = true;
            idInput.style.background = '#f1f5f9';
            idInput.style.cursor = 'not-allowed';
          }
          if (help) help.textContent = '(auto — saved to your own patient record)';
        }
      } catch (_) { /* silent */ }
    })();

    document.querySelectorAll('.btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const r = document.createElement('span');
        r.className = 'ripple';
        const rect = btn.getBoundingClientRect();
        const size = Math.max(rect.width, rect.height) / 2;
        r.style.width = r.style.height = size + 'px';
        r.style.left = (e.clientX - rect.left - size/2) + 'px';
        r.style.top  = (e.clientY - rect.top  - size/2) + 'px';
        btn.appendChild(r);
        setTimeout(() => r.remove(), 650);
      });
    });

    // ---- Category picker ----
    let selectedDocType = 'prescription';
    const catPicker = document.getElementById('cat-picker');
    catPicker.addEventListener('click', (e) => {
      const item = e.target.closest('.fx-seg-item');
      if (!item) return;
      catPicker.querySelectorAll('.fx-seg-item').forEach(p => p.classList.remove('is-active'));
      item.classList.add('is-active');
      selectedDocType = item.getAttribute('data-doc-type');
    });

    // ---- Dropzone + preview ----
    const dz = document.getElementById('dropzone');
    const input = document.getElementById('file-input');
    const preview = document.getElementById('preview');
    const previewImg = document.getElementById('preview-img');
    const previewIcon = document.getElementById('preview-icon');
    const previewName = document.getElementById('preview-name');
    const previewSize = document.getElementById('preview-size');

    function handleFile(file) {
      if (!file) return;
      previewName.textContent = file.name;
      previewSize.textContent = (file.size / 1024).toFixed(1) + ' KB · ' + (file.type || 'unknown type');
      if (file.type.startsWith('image/')) {
        previewImg.src = URL.createObjectURL(file);
        previewImg.style.display = '';
        previewIcon.style.display = 'none';
      } else {
        previewImg.removeAttribute('src');
        previewImg.style.display = 'none';
        previewIcon.style.display = '';
      }
      preview.classList.remove('hidden');
      document.getElementById('mediscan-empty').style.display = 'none';
    }
    input.addEventListener('change', e => handleFile(e.target.files[0]));
    ['dragenter','dragover'].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.add('is-drag'); }));
    ['dragleave','drop'].forEach(ev => dz.addEventListener(ev, e => { e.preventDefault(); dz.classList.remove('is-drag'); }));
    dz.addEventListener('drop', e => { const f = e.dataTransfer.files[0]; if (f) { input.files = e.dataTransfer.files; handleFile(f); } });

    function clearSelectedFile() {
      input.value = '';
      preview.classList.add('hidden');
      previewImg.removeAttribute('src');
      previewImg.style.display = 'none';
      previewIcon.style.display = '';
      document.getElementById('mediscan-empty').style.display = '';
    }
    document.getElementById('clear-file').addEventListener('click', clearSelectedFile);
    document.getElementById('replace-file').addEventListener('click', () => input.click());

    // ---- Helpers for the three result panels ----
    function showOnlyPanel(name) {
      ['prescription','lab','imaging'].forEach(n => {
        const el = document.getElementById('panel-' + n);
        if (el) el.classList.add('hidden');
      });
      const target = document.getElementById('panel-' + name);
      if (target) target.classList.remove('hidden');
    }

    function renderPrescriptionPanel(data, patientId) {
      showOnlyPanel('prescription');
      document.getElementById('result-doctor').textContent = data.doctor_name || 'Not detected';
      const meds = Array.isArray(data.medicines) ? data.medicines : [];
      document.getElementById('result-med-count').textContent = meds.length;
      document.getElementById('result-saved').textContent = patientId
        ? `Yes — saved to Patient #${patientId}`
        : 'No — no Patient ID was entered';

      const medList = document.getElementById('medicine-list');
      if (meds.length === 0) {
        medList.innerHTML = '<div style="color:var(--fx-text-muted);font-size:.9rem;text-align:center;padding:14px;">No medicines could be identified in this document.</div>';
      } else {
        medList.innerHTML = meds.map(m => `
          <div class="fx-med-row">
            <div class="fx-med-row-icon">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.5 20.5 3.5 13.5a5 5 0 0 1 0-7.07 5 5 0 0 1 7.07 0l7 7a5 5 0 0 1 0 7.07 5 5 0 0 1-7.07 0Z"/></svg>
            </div>
            <div>
              <div class="fx-med-row-name">${(m.name || 'Unknown')}${m.dosage ? ' — ' + m.dosage : ''}${m.db_verified ? '<span class="fx-med-verified">✓ Verified</span>' : (m.db_verified === false ? '<span class="fx-med-unverified">⚠ Not in DB</span>' : '')}</div>
              <div class="fx-med-row-meta">${m.frequency || '-'} · ${m.duration || '-'}${m.db_info ? ' · ' + (m.db_info.uses || '') : ''}</div>
            </div>
            <span class="fx-med-row-dose">${m.frequency || '-'}</span>
          </div>
        `).join('');
      }
      document.getElementById('result-notes').textContent = data.notes || 'None';
    }

    function renderLabPanel(data) {
      showOnlyPanel('lab');
      document.getElementById('lab-name').textContent = data.lab_name || 'Not detected';
      document.getElementById('lab-date').textContent = data.report_date || 'Not detected';
      const tests = Array.isArray(data.tests) ? data.tests : [];
      document.getElementById('lab-count').textContent = tests.length;

      // Abnormal summary
      const abnormalCount = tests.filter(t => (t.flag||'').toLowerCase() !== 'normal').length;
      const criticalCount = tests.filter(t => (t.flag||'').toLowerCase().includes('critical')).length;

      const tbody = document.querySelector('#lab-table tbody');
      if (tests.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--fx-text-muted);padding:18px;">No tests could be parsed from this report.</td></tr>';
      } else {
        // Update header to include Significance column
        const thead = document.querySelector('#lab-table thead tr');
        if (thead && !thead.querySelector('th:last-child').textContent.includes('Significance')) {
          thead.innerHTML += '<th>Significance</th>';
        }
        tbody.innerHTML = tests.map(t => {
          const flag = (t.flag || 'unknown').toLowerCase();
          const flagClass = 'fx-lab-flag ' + flag;
          const flagLabel = flag.replace('_', ' ');
          const sig = t.clinical_significance || '';
          const rowStyle = flag.includes('critical') ? 'background:#fef2f2;' : flag === 'high' || flag === 'low' ? 'background:#fffbeb;' : '';
          return `
            <tr style="${rowStyle}">
              <td>${t.name || '—'}${t.is_critical ? ' <span style="color:#ef4444;font-weight:800;">⚠</span>' : ''}</td>
              <td><strong>${t.value || '—'}</strong></td>
              <td>${t.unit || '—'}</td>
              <td>${t.reference_range || '—'}</td>
              <td><span class="${flagClass}">${flagLabel}</span></td>
              <td style="font-size:11px;color:var(--fx-text-muted);">${sig || '—'}</td>
            </tr>
          `;
        }).join('');
      }

      // Show abnormal summary above table
      if (abnormalCount > 0) {
        const summaryDiv = document.createElement('div');
        summaryDiv.style.cssText = 'margin-bottom:10px;padding:10px 14px;border-radius:10px;font-size:12px;font-weight:600;';
        if (criticalCount > 0) {
          summaryDiv.style.background = '#fef2f2';
          summaryDiv.style.color = '#991b1b';
          summaryDiv.style.border = '1px solid #fca5a5';
          summaryDiv.innerHTML = '🚨 <strong>' + criticalCount + ' critical</strong> and ' + (abnormalCount - criticalCount) + ' abnormal results detected. Immediate medical review recommended.';
        } else {
          summaryDiv.style.background = '#fefce8';
          summaryDiv.style.color = '#854d0e';
          summaryDiv.style.border = '1px solid #fde047';
          summaryDiv.innerHTML = '⚠️ <strong>' + abnormalCount + ' abnormal</strong> results detected. Please discuss with your doctor.';
        }
        const table = document.getElementById('lab-table');
        table.parentNode.insertBefore(summaryDiv, table);
      }

      document.getElementById('lab-notes').textContent = data.notes || 'None';
    }

    function renderImagingPanel(data) {
      showOnlyPanel('imaging');
      const meta = document.getElementById('imaging-meta');
      const modality = (data.modality || 'unknown').toUpperCase();
      let metaHtml = '<span class="fx-chip-sm" style="background:var(--brand-50);color:var(--brand-700);border-color:transparent;">Modality: <strong style="margin-left:4px;">' + modality + '</strong></span>';
      if (data.body_part) metaHtml += '<span class="fx-chip-sm" style="background:rgba(99,102,241,.14);color:#4f46e5;border-color:transparent;">Region: <strong style="margin-left:4px;">' + data.body_part + '</strong></span>';
      if (data.orientation) metaHtml += '<span class="fx-chip-sm" style="background:rgba(139,92,246,.14);color:#6d28d9;border-color:transparent;">View: <strong style="margin-left:4px;">' + data.orientation + '</strong></span>';
      if (data.technical_quality) {
        var qColor = data.technical_quality === 'good' ? '#047857' : data.technical_quality === 'adequate' ? '#b45309' : '#b91c1c';
        var qBg = data.technical_quality === 'good' ? '16,185,129' : data.technical_quality === 'adequate' ? '245,158,11' : '239,68,68';
        metaHtml += '<span class="fx-chip-sm" style="background:rgba(' + qBg + ',.14);color:' + qColor + ';border-color:transparent;">Quality: <strong style="margin-left:4px;">' + data.technical_quality + '</strong></span>';
      }
      meta.innerHTML = metaHtml;

      if (data.severity_score !== undefined) {
        var sevClass = data.priority === 'high' ? 'high' : data.priority === 'moderate' ? 'moderate' : 'low';
        var sevHtml = '<div style="width:100%;margin-top:10px;">'
          + '<div style="display:flex;justify-content:space-between;font-size:11px;font-weight:600;color:var(--fx-text-soft);margin-bottom:4px;">'
          + '<span>Severity Score</span>'
          + '<span>' + data.severity_score + '/100 (' + (data.priority || 'low') + ')</span>'
          + '</div>'
          + '<div class="fx-severity-bar"><div class="fx-severity-fill ' + sevClass + '" style="width:' + data.severity_score + '%;"></div></div>'
          + '</div>';
        meta.innerHTML += sevHtml;
      }

      var findings = Array.isArray(data.findings) ? data.findings : [];
      var findingsEl = document.getElementById('imaging-findings');
      if (findings.length && typeof findings[0] === 'object') {
        var findingsHtml = '';
        var sigColors = {critical:'#ef4444',severe:'#f97316',moderate:'#f59e0b',mild:'#10b981'};
        for (var fi = 0; fi < findings.length; fi++) {
          var f = findings[fi];
          var c = sigColors[(f.significance||'mild').toLowerCase()] || '#64748b';
          var fHtml = '<li style="margin-bottom:6px;">'
            + '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:' + c + ';margin-right:6px;vertical-align:middle;"></span>'
            + '<strong>' + (f.category || '') + '</strong>: ' + (f.description || '');
          if (f.location) fHtml += ' <span style="color:var(--fx-text-muted);font-size:11px;">(' + f.location + ')</span>';
          if (f.is_abnormal) fHtml += ' <span style="color:#ef4444;font-size:10px;font-weight:700;margin-left:4px;">ABNORMAL</span>';
          fHtml += '</li>';
          findingsHtml += fHtml;
        }
        findingsEl.innerHTML = findingsHtml;
      } else {
        findingsEl.innerHTML = findings.length
          ? findings.map(function(f) { return '<li>' + (typeof f === 'object' ? (f.description || JSON.stringify(f)) : f) + '</li>'; }).join('')
          : '<li>No specific findings identified.</li>';
      }

      document.getElementById('imaging-impression').textContent = data.impression || 'Not determinable';

      var recs = Array.isArray(data.recommendations) ? data.recommendations : [];
      var recsEl = document.getElementById('imaging-recs');
      recsEl.innerHTML = recs.length
        ? recs.map(function(r) { return '<li>' + r + '</li>'; }).join('')
        : '<li>No specific recommendations.</li>';
    }

    function renderWarnings(warnings) {
      const banner = document.getElementById('warnings-banner');
      const text = document.getElementById('warnings-text');
      if (Array.isArray(warnings) && warnings.length) {
        text.textContent = warnings.join(' · ');
        banner.classList.remove('hidden');
      } else {
        banner.classList.add('hidden');
        text.textContent = '';
      }
    }

    // ---- Pipeline Visualization ----
    function renderPipeline(stages) {
      const container = document.getElementById('pipeline-steps');
      if (!container || !stages || !stages.length) {
        document.getElementById('pipeline-container').style.display = 'none';
        return;
      }
      document.getElementById('pipeline-container').style.display = '';
      container.innerHTML = stages.map(s => {
        const statusClass = s.status || 'complete';
        return '<div class="fx-pipeline-step">'
          + '<div class="fx-pipeline-dot ' + statusClass + '">' + (s.icon || '✓') + '</div>'
          + '<div class="fx-pipeline-info">'
          + '<div class="fx-pipeline-name">' + (s.name || '') + '</div>'
          + (s.detail ? '<div class="fx-pipeline-detail">' + s.detail + '</div>' : '')
          + '</div>'
          + '<span class="fx-pipeline-status ' + statusClass + '">' + statusClass + '</span>'
          + '</div>';
      }).join('');
    }

    function renderAnalysisBadges(confidence, urgency) {
      const container = document.getElementById('analysis-badges');
      if (!container) return;
      const confPct = Math.round((confidence || 0) * 100);
      let confClass = 'low';
      if (confPct >= 70) confClass = 'high';
      else if (confPct >= 40) confClass = 'medium';
      const urgencyLabel = {
        routine: '✅ Routine',
        follow_up_soon: '⚠️ Follow-up Soon',
        urgent: '🚨 Urgent',
        emergency: '🔴 Emergency'
      };
      container.innerHTML = '<div class="fx-confidence-badge ' + confClass + '">📊 Confidence: ' + confPct + '%</div>'
        + '<div class="fx-urgency-badge ' + (urgency || 'routine') + '">' + (urgencyLabel[urgency] || '✅ Routine') + '</div>';
    }

    // ---- Submit ----
    document.getElementById('scan-btn').addEventListener('click', async function () {
      if (this.classList.contains('is-loading')) return;

      const msg = document.getElementById('upload-message');
      const patientIdVal = document.getElementById('patient-id').value;
      const empty = document.getElementById('mediscan-empty');

      if (!input.files.length) {
        msg.textContent = 'Please select or drop a file first.';
        msg.className = 'message error';
        return;
      }

      const formData = new FormData();
      formData.append('file', input.files[0]);
      formData.append('doc_type', selectedDocType);
      if (patientIdVal) {
        formData.append('patient_id', patientIdVal);
      }

      msg.textContent = 'Analyzing with MediScan… (calls Gemini, may take a few seconds)';
      msg.className = 'message';
      this.classList.add('is-loading');
      document.getElementById('result-card').classList.add('hidden');
      if (empty) empty.style.display = 'none';
      renderWarnings([]);

      const progressBar = document.getElementById('upload-progress');
      if (progressBar) progressBar.style.display = '';

      const response = await apiFetch('/mediscan/analyze', {
        method: 'POST',
        body: formData
      });

      this.classList.remove('is-loading');
      if (progressBar) progressBar.style.display = 'none';

      if (response && response.ok) {
        const result = await response.json();
        __lastScanResult = result;
        this.classList.add('is-done');
        setTimeout(() => this.classList.remove('is-done'), 1300);

        const titleMap = { prescription: 'Prescription', lab_report: 'Lab report', imaging: 'Imaging scan' };
        document.getElementById('result-title').textContent = titleMap[result.doc_type] || 'MediScan result';
        document.getElementById('result-subtitle').textContent = result.file_name ? `From ${result.file_name}` : 'AI extracted';
        const pill = document.getElementById('result-type-pill');
        pill.textContent = titleMap[result.doc_type] || result.doc_type;
        pill.style.display = '';
        document.getElementById('result-summary').textContent = result.summary || 'No summary was generated.';

        renderWarnings(result.warnings || []);
        renderPipeline(result.pipeline_stages || []);
        renderAnalysisBadges(result.confidence || 0, result.urgency || 'routine');

        const data = result.extracted_data || {};
        if (result.doc_type === 'prescription') {
          renderPrescriptionPanel(data, patientIdVal);
        } else if (result.doc_type === 'lab_report') {
          renderLabPanel(data);
        } else if (result.doc_type === 'imaging') {
          renderImagingPanel(data);
        } else {
          showOnlyPanel('prescription');
        }

        // Apply urgency strip to result hero
        const hero = document.querySelector('.fx-result-hero');
        if (hero) {
          hero.className = 'fx-result-hero';
          const urg = result.urgency || 'routine';
          if (urg === 'urgent' || urg === 'emergency') hero.classList.add('urgent-strip');
          else if (urg === 'follow_up_soon') hero.classList.add('follow-up-strip');
          else hero.classList.add('routine-strip');
        }
        document.getElementById('result-card').classList.remove('hidden');
        document.getElementById('result-card').scrollIntoView({ behavior: 'smooth', block: 'start' });

        const idInput = document.getElementById('patient-id');
        if (idInput && idInput.readOnly) {
          msg.innerHTML = 'Saved to your <a href="patient-records.html" style="color:#6366f1;text-decoration:underline;">medical record</a>.';
          msg.className = 'message';
        } else {
          msg.textContent = '';
        }
      } else {
        let err = 'Analysis failed. Please try again.';
        if (response) {
          try {
            const body = await response.json();
            if (body && body.detail) err = body.detail;
          } catch (_) { /* ignore */ }
        }
        msg.textContent = err;
        msg.className = 'message error';
      }
    });
  