
    dayjs.extend(dayjs_plugin_relativeTime);
    const $ = id => document.getElementById(id);
    function esc(s){ return String(s==null?'':s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
    let ROLE = 'nurse';
    let taskCache = {};       // nurse: list cache by filter
    let createdCache = [];    // manager list
    let currentFilter = 'all';
    let openTaskId = null;

    function toast(msg, ok=true){ const t=$('na-toast'); t.textContent=msg; t.className='toast show '+(ok?'ok':'err'); clearTimeout(t._h); t._h=setTimeout(()=>t.classList.remove('show'), 2600); }
    function priPill(p){ return '<span class="pill pri-'+esc(p)+'">'+esc(p)+'</span>'; }
    function stPill(s){ return '<span class="pill st-'+esc(s)+'">'+esc(s).replace('_',' ')+'</span>'; }
    function fmt(d){ return d ? dayjs(d).format('MMM D, h:mm A') : '—'; }
    function fmtShort(d){ return d ? dayjs(d).format('MMM D') : ''; }
    function dueStr(t){ return t.due_at ? 'Due ' + fmt(t.due_at) + (t.overdue ? ' · OVERDUE':'') : ''; }
    function initials(n){ if(!n) return '??'; const p=n.trim().split(/\s+/); return ((p[0]||'')[0]||'')+((p[1]||'')[0]||''); }
    const COLORS=['#4f46e5','#0ea5e9','#10b981','#f59e0b','#ec4899','#8b5cf6','#ef4444','#14b8a6'];
    function avColor(id){ return COLORS[(id||0)%COLORS.length]; }

    document.addEventListener('DOMContentLoaded', async () => {
      mountNav();
      mountAvatarDropdown();
      loadUserBadge('avatar-initials','user-info');
      try {
        const r = await apiFetch('/me');
        if (r && r.ok){ const me = await r.json(); ROLE = me.role; }
      } catch(e){}
      if (ROLE === 'nurse') renderNurseShell(); else if (ROLE === 'doctor' || ROLE === 'hospital') renderManagerShell(); else renderBlocked();
    });

    function mountNav(){
      const nt = $('nav-toggle'); if (!nt) return;
      nt.addEventListener('click', ()=>{ $('sidebar').classList.toggle('is-open'); const ov=document.querySelector('.sidebar-overlay'); if(ov) ov.classList.toggle('is-open'); });
    }
    function openDrawer(){ $('na-drawer').classList.add('open'); }
    function closeDrawer(){ $('na-drawer').classList.remove('open'); openTaskId=null; }
    document.addEventListener('keydown', e => { if(e.key==='Escape') closeDrawer(); });

    /* ======================================================================
       NURSE VIEW — "My Assignments" tray
       ====================================================================== */
    function renderNurseShell(){
      const w = $('na-wrap');
      w.innerHTML = `
        <div class="na-head">
          <div><h1>My <span>Assignments</span></h1><div class="na-sub">Care tasks assigned to you by doctors &amp; hospital staff.</div></div>
          <div class="na-actions"><button class="na-btn ghost" id="na-refresh" onclick="refreshAll()">↻ Refresh</button></div>
        </div>
        <div class="na-stats" id="na-stats">
          ${[['total','Total open',''],['pending','Pending','blue'],['in_progress','In progress','purple'],['completed_today','Completed today','green'],['overdue','Overdue','red'],['high_priority','High priority','amber']].map(s=>`<div class="na-stat ${s[2]}" data-stat="${s[0]}"><div class="v" id="stat-${s[0]}">…</div><div class="l">${s[1]}</div></div>`).join('')}
        </div>
        <div class="na-card" id="followups-card" style="display:none;">
          <h3>⏰ Upcoming follow-ups</h3><div class="followup-strip" id="fu-strip"></div>
        </div>
        <div class="na-tabs" id="na-filters">
          ${[['all','All'],['pending','Pending'],['in_progress','In progress'],['today','Today'],['high','High priority'],['overdue','Overdue'],['escalated','Escalated'],['followups','Follow-ups'],['completed','Completed']].map(f=>`<button class="na-tab ${f[0]==='all'?'active':''}" data-filter="${f[0]}" onclick="setFilter('${f[0]}')">${f[1]}</button>`).join('')}
        </div>
        <div class="na-layout">
          <div class="na-card" style="padding:6px;">
            <div class="na-list" id="task-list"><div class="na-empty">Loading assignments…</div></div>
          </div>
          <div>
            <div class="na-card">
              <h3>🧭 Care task tips</h3>
              <div style="font-size:13px;color:var(--na-ink2);line-height:1.6;">
                <p style="margin:0 0 8px;">• Open a task → <b>Acknowledge</b> to confirm you received it.</p>
                <p style="margin:0 0 8px;">• <b>Start</b> when you begin work; update <b>Progress</b> as you go.</p>
                <p style="margin:0 0 8px;">• Add <b>Notes</b> (observations / follow-ups) — each is time-stamped and kept.</p>
                <p style="margin:0 0 8px;">• Schedule a <b>Follow-up</b> when a recheck is needed.</p>
                <p style="margin:0;">• <b>Escalate</b> a task when it needs the assigner's human review.</p>
              </div>
            </div>
            <div class="na-card">
              <h3>🩺 Quick links</h3>
              <div style="display:grid;gap:8px;">
                <button class="na-btn ghost" style="text-align:left;" onclick="openAppointmentsTab()">📅 Appointments (legacy schedule)</button>
                <button class="na-btn ghost" style="text-align:left;" onclick="location.href='nurse-dashboard.html'">🏠 Nursing dashboard</button>
              </div>
            </div>
          </div>
        </div>`;
      loadNurseStats();
      setFilter('all');
    }

    function openAppointmentsTab(){
      window.open('appointments.html','_self');
    }

    async function refreshAll(){ loadNurseStats(); setFilter(currentFilter); toast('Refreshed'); }
    function setFilter(f){ currentFilter=f; document.querySelectorAll('#na-filters .na-tab').forEach(b=>b.classList.toggle('active', b.dataset.filter===f)); loadNurseTasks(f); }

    async function loadNurseStats(){
      try {
        const r = await apiFetch('/nurse-tasks/mine/stats');
        if (!r || !r.ok) return;
        const s = await r.json();
        ['total','pending','in_progress','completed_today','overdue','high_priority'].forEach(k=>{ const el=$('stat-'+k); if(el) el.textContent = s[k] ?? 0; });
        const fc = $('followups-card');
        if (s.followups && s.followups.length){
          fc.style.display='';
          $('fu-strip').innerHTML = s.followups.map(f=>`<button class="fu-chip ${f.overdue?'over':''}" onclick="openTask(${f.task_id})"><b>${esc(f.title)}</b> · due ${esc(fmt(f.due_at))}${f.overdue?' · overdue':''}</button>`).join('');
        } else { fc.style.display='none'; }
      } catch(e){}
    }

    async function loadNurseTasks(filter){
      const list = $('task-list');
      if(!list) return;
      list.innerHTML = '<div class="na-empty">Loading…</div>';
      try {
        const r = await apiFetch('/nurse-tasks/mine?filter=' + encodeURIComponent(filter));
        if (!r || !r.ok){ list.innerHTML = '<div class="na-empty">Could not load assignments.</div>'; return; }
        const d = await r.json();
        taskCache[filter] = d.items || [];
        renderNurseList();
      } catch(e){ list.innerHTML='<div class="na-empty">Network error.</div>'; }
    }

    function renderNurseList(){
      const list = $('task-list'); if(!list) return;
      const items = taskCache[currentFilter] || [];
      if (!items.length){ list.innerHTML = `<div class="na-empty"><svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg><div>No ${currentFilter==='all'?'assignments':currentFilter.replace('_',' ')} right now</div></div>`; return; }
      list.innerHTML = items.map(t=>`
        <div class="na-item" onclick="openTask(${t.id})">
          <div class="row">
            <div class="na-avatar" style="width:40px;height:40px;border-radius:12px;flex:none;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:800;font-size:13px;background:${avColor(t.patient_id)};">${esc(initials(t.patient_name))}</div>
            <div style="flex:1;min-width:0;">
              <div class="t">${esc(t.title)}</div>
              <div class="pname">👤 ${esc(t.patient_name)} · assigned by ${esc(t.created_by_name)} (${esc(t.created_by_role)})</div>
            </div>
            <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;justify-content:flex-end;">
              ${t.overdue?'<span class="pill overdue">Overdue</span>':''}
              ${priPill(t.priority)} ${stPill(t.status)}
            </div>
          </div>
          <div class="meta">
            <span>${dueStr(t)}</span>
            ${t.next_followup && t.next_followup.due_at ? '<span>Follow-up: '+esc(fmtShort(t.next_followup.due_at))+'</span>':''}
            <span>Progress: ${t.progress}%</span>
          </div>
          <div class="pbar"><div style="width:${t.progress}%"></div></div>
        </div>`).join('');
    }

    async function openTask(id){
      openTaskId = id;
      openDrawer();
      $('nd-title').textContent = 'Loading…'; $('nd-sub').textContent=''; $('nd-body').innerHTML='<div class="na-empty">Loading task…</div>';
      try {
        const r = await apiFetch('/nurse-tasks/' + id);
        if (!r || !r.ok){ $('nd-body').innerHTML='<div class="na-empty">Could not open this task.</div>'; return; }
        const t = await r.json();
        renderTaskDrawer(t);
      } catch(e){ $('nd-body').innerHTML='<div class="na-empty">Network error.</div>'; }
    }

    function renderTaskDrawer(t){
      $('nd-title').textContent = esc(t.title);
      $('nd-sub').textContent = `Patient: ${t.patient_name} · #TASK-${t.id} · assigned by ${t.created_by_name} (${t.created_by_role})`;
      const canNurseAct = ROLE==='nurse';
      const openSt = ['assigned','acknowledged','in_progress','on_hold'].includes(t.status);
      let actions = '';
      if (canNurseAct){
        if (t.status==='assigned' || t.status==='acknowledged') actions += `<button class="na-btn" onclick="taskAction('acknowledge')">✓ Acknowledge</button>`;
        if (t.status==='assigned' || t.status==='acknowledged') actions += `<button class="na-btn green" onclick="taskAction('start')">▶ Start</button>`;
        if (t.status==='in_progress') actions += `<button class="na-btn amber" onclick="taskAction('hold')">⏸ Hold</button>`;
        if (t.status==='on_hold') actions += `<button class="na-btn" onclick="taskAction('resume')">▶ Resume</button>`;
        if (openSt && t.status!=='in_progress' && t.status!=='on_hold') {}
        if (t.status==='in_progress' || t.status==='on_hold'){
          actions += `<button class="na-btn green" onclick="quickComplete()">✔ Complete</button>`;
          actions += `<button class="na-btn danger" onclick="escalateUI()">⤴ Escalate</button>`;
        }
        if (t.status==='escalated') actions += `<button class="na-btn danger" style="pointer-events:none;opacity:.6;">Escalated — awaiting assigner</button>`;
      } else {
        if (t.status==='escalated') actions += `<button class="na-btn green" onclick="respondEscalation()">↩ Respond to escalation</button>`;
        if (openSt || t.status==='escalated') actions += `<button class="na-btn danger" onclick="cancelTask()">✕ Cancel task</button>`;
      }
      const fu = t.next_followup;
      $('nd-body').innerHTML = `
        <div class="nd-grid">
          <div class="cell"><div class="k">Status</div><div class="v">${stPill(t.status)} ${t.overdue?'<span class="pill overdue">OVERDUE</span>':''}</div></div>
          <div class="cell"><div class="k">Priority</div><div class="v">${priPill(t.priority)}</div></div>
          <div class="cell"><div class="k">Due</div><div class="v">${fmt(t.due_at)}</div></div>
          <div class="cell"><div class="k">Progress</div><div class="v">${t.progress}%</div></div>
          <div class="cell"><div class="k">Department</div><div class="v">${esc(t.department||'—')}</div></div>
          <div class="cell"><div class="k">Created</div><div class="v">${fmt(t.created_at)}</div></div>
        </div>
        <div class="nd-section">
          <h4>Instructions</h4>
          <div style="font-size:13.5px;color:var(--na-ink2);white-space:pre-wrap;">${esc(t.instructions||'No extra instructions.')}</div>
        </div>
        ${actions?`<div class="nd-actions">${actions}</div>`:''}
        ${(canNurseAct && openSt)?`
          <div class="nd-section"><h4>Update progress</h4>
            <div style="display:flex;gap:10px;align-items:center;">
              <input type="range" min="0" max="100" step="5" value="${t.progress}" id="nd-progress" style="flex:1;" oninput="$('nd-progress-val').textContent=this.value+'%'">
              <span id="nd-progress-val" style="font-weight:800;min-width:44px;">${t.progress}%</span>
              <button class="na-btn sm" onclick="saveProgress()">Save</button>
            </div>
          </div>`:''}
        ${canNurseAct?`
          <div class="nd-section"><h4>Add a note</h4>
            <div class="nd-field"><textarea id="nd-note" rows="2" placeholder="Observation, reading or update…"></textarea></div>
            <button class="na-btn sm" onclick="addNote()">+ Save note</button>
          </div>`:''}
        ${canNurseAct?`
          <div class="nd-section"><h4>Follow-up</h4>
            ${fu && fu.due_at ? `<div style="font-size:13px;margin-bottom:8px;">Next follow-up due <b>${esc(fmt(fu.due_at))}</b>${fu.note?' — '+esc(fu.note):''} <button class="na-btn green sm" style="margin-left:6px;" onclick="completeFollowup()">Mark done</button></div>`:''}
            <div class="nd-field"><input type="datetime-local" id="nd-fu-date" /></div>
            <div class="nd-field"><input id="nd-fu-reason" placeholder="Reason (e.g. recheck vitals)" /></div>
            <button class="na-btn sm" onclick="scheduleFollowup()">⏰ Schedule follow-up</button>
          </div>`:''}
        <div class="nd-section"><h4>History</h4>
          <div class="timeline">
            ${(t.events||[]).map(e=>`<div class="tl-item"><div class="who">${esc(e.user_name||'System')}</div><div class="what">${esc(evText(e))}</div><div class="when">${fmt(e.created_at)}</div></div>`).join('')}
          </div>
        </div>`;
    }

    function evText(e){
      const map = {created:'Task created', acknowledged:'Acknowledged', started:'Work started', progress:'Progress updated to '+ (e.progress??'?') + '%', note:'Note', paused:'Paused', resumed:'Resumed', escalated:'Escalated', completed:'Completed', cancelled:'Cancelled', followup_scheduled:'Follow-up scheduled', followup_completed:'Follow-up completed'};
      let txt = map[e.kind] || e.kind;
      if (e.note) txt += ' — ' + e.note;
      return txt;
    }

    async function taskAction(act){
      if (!openTaskId) return;
      try {
        const r = await apiFetch(`/nurse-tasks/${openTaskId}/${act}`, {method:'POST'});
        if (r && r.ok){ toast('Updated'); refreshDetailAndLists(); } else { const e = await r.json().catch(()=>({})); toast('Error: '+(e.detail||'Failed'), false); }
      } catch(e){ toast('Network error', false); }
    }
    async function quickComplete(){
      if (!confirm('Mark this task as completed?')) return;
      await apiFetch(`/nurse-tasks/${openTaskId}/complete`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({note: (prompt('Optional completion note:')||'').trim() || null})});
      refreshDetailAndLists();
    }
    async function saveProgress(){
      const p = parseInt($('nd-progress').value || '0');
      const r = await apiFetch(`/nurse-tasks/${openTaskId}/progress`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({progress:p})});
      if (r && r.ok){ toast('Progress saved'); refreshDetailAndLists(); } else toast('Failed to save progress', false);
    }
    async function addNote(){
      const note = $('nd-note').value.trim(); if(!note){ toast('Write something first', false); return; }
      const r = await apiFetch(`/nurse-tasks/${openTaskId}/notes`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({note})});
      if (r && r.ok){ $('nd-note').value=''; toast('Note saved'); refreshDetailAndLists(); } else toast('Failed to save note', false);
    }
    async function scheduleFollowup(){
      const due = $('nd-fu-date').value; const reason = $('nd-fu-reason').value.trim();
      if(!due){ toast('Pick a date/time for the follow-up', false); return; }
      const r = await apiFetch(`/nurse-tasks/${openTaskId}/followup`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({due_at:new Date(due).toISOString(), reason})});
      if (r && r.ok){ toast('Follow-up scheduled'); refreshDetailAndLists(); } else toast('Failed', false);
    }
    async function completeFollowup(){
      const r = await apiFetch(`/nurse-tasks/${openTaskId}/followup-complete`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({note:'Done'})});
      if (r && r.ok){ toast('Follow-up completed'); refreshDetailAndLists(); } else toast('Failed', false);
    }
    function escalateUI(){
      const reason = prompt('Reason for escalation (what needs review?):'); if(!reason) return;
      const to = confirm('Escalate to the assigner (doctor/hospital) for review?') ? 'doctor' : 'doctor';
      (async()=>{
        const r = await apiFetch(`/nurse-tasks/${openTaskId}/escalate`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({reason, to_role:to})});
        if (r && r.ok){ toast('Task escalated'); refreshDetailAndLists(); } else { const e = await r.json().catch(()=>({})); toast('Error: '+(e.detail||'Failed'), false); }
      })();
    }
    function refreshDetailAndLists(){
      if (openTaskId) openTask(openTaskId);
      loadNurseStats();
      loadNurseTasks(currentFilter);
    }

    /* ======================================================================
       MANAGER VIEW — doctor / hospital: assign + track
       ====================================================================== */
    function renderManagerShell(){
      const w = $('na-wrap');
      const isHosp = ROLE==='hospital';
      w.innerHTML = `
        <div class="na-head">
          <div><h1>Nurse <span>Assignments</span></h1><div class="na-sub">Assign care tasks to nurses and track their progress live.</div></div>
          <div class="na-actions"><button class="na-btn" onclick="openAssignModal()">＋ Assign care task</button></div>
        </div>
        <div class="na-tabs" id="mgr-tabs">
          <button class="na-tab active" data-v="active" onclick="mgrView('active')">Active</button>
          <button class="na-tab" data-v="escalated" onclick="mgrView('escalated')">Escalated</button>
          <button class="na-tab" data-v="overdue" onclick="mgrView('overdue')">Overdue</button>
          <button class="na-tab" data-v="completed" onclick="mgrView('completed')">Completed</button>
          <button class="na-tab" data-v="cancelled" onclick="mgrView('cancelled')">Cancelled</button>
        </div>
        <div class="na-card" style="padding:6px;"><div class="na-list" id="mgr-list"><div class="na-empty">Loading your assigned care tasks…</div></div></div>`;
      loadCreated();
    }
    function mgrView(v){
      document.querySelectorAll('#mgr-tabs .na-tab').forEach(b=>b.classList.toggle('active', b.dataset.v===v));
      renderCreated(v);
    }
    async function loadCreated(){
      try {
        const r = await apiFetch('/nurse-tasks/created');
        if (!r || !r.ok){ $('mgr-list').innerHTML='<div class="na-empty">Could not load tasks.</div>'; return; }
        const d = await r.json();
        createdCache = d.items || [];
        renderCreated('active');
      } catch(e){}
    }
    function renderCreated(view){
      const list=$('mgr-list'); if(!list) return;
      const open = t => ['assigned','acknowledged','in_progress','on_hold'].includes(t.status);
      let items = createdCache.filter(t => view==='active' ? open(t) : t.status===view);
      if (!items.length){ list.innerHTML='<div class="na-empty">No ' + view + ' care tasks.</div>'; return; }
      list.innerHTML = items.map(t=>`
        <div class="na-item" onclick="openTask(${t.id})">
          <div class="row">
            <div style="flex:1;min-width:0;">
              <div class="t">${esc(t.title)} <span style="font-weight:400;color:var(--na-soft);font-size:11px;">#TASK-${t.id}</span></div>
              <div class="pname">👤 ${esc(t.patient_name)} → 🧑‍⚕️ ${esc(t.nurse_name)}</div>
            </div>
            <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;justify-content:flex-end;">
              ${t.overdue?'<span class="pill overdue">Overdue</span>':''}
              ${priPill(t.priority)} ${stPill(t.status)}
            </div>
          </div>
          <div class="meta"><span>${dueStr(t)}</span><span>Progress: ${t.progress}%</span><span>Updated ${t.updated_at?fmt(t.updated_at):'—'}</span></div>
          <div class="pbar"><div style="width:${t.progress}%"></div></div>
        </div>`).join('');
    }

    async function openAssignModal(){
      const m = $('assign-modal'); m.classList.add('open');
      $('am-patient').innerHTML = '<option value="">Loading…</option>'; $('am-nurse').innerHTML='<option value="">Loading…</option>';
      try {
        const r = await apiFetch('/nurse-tasks/options');
        if (!r || !r.ok){ toast('Could not load options', false); return; }
        const d = await r.json();
        $('am-patient').innerHTML = '<option value="">Select patient…</option>' + (d.patients||[]).map(p=>`<option value="${p.id}">${esc(p.name)}</option>`).join('') || '<option value="">No patients available</option>';
        $('am-nurse').innerHTML = '<option value="">Select nurse…</option>' + (d.nurses||[]).map(n=>`<option value="${n.id}">${esc(n.name)}</option>`).join('') || '<option value="">No nurses available</option>';
      } catch(e){ toast('Network error', false); }
    }

    async function submitAssign(){
      const body = {
        patient_id: parseInt($('am-patient').value || '0'),
        nurse_user_id: parseInt($('am-nurse').value || '0'),
        title: $('am-title').value.trim(),
        instructions: $('am-instr').value.trim() || null,
        priority: $('am-pri').value,
        department: $('am-dept').value.trim() || null,
        due_at: $('am-due').value ? new Date($('am-due').value).toISOString() : null
      };
      if (!body.patient_id || !body.nurse_user_id){ toast('Choose a patient and a nurse', false); return; }
      if (!body.title){ toast('Give the task a title', false); return; }
      try {
        const r = await apiFetch('/nurse-tasks/create', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
        if (r && r.ok){ document.getElementById('assign-modal').classList.remove('open'); toast('Care task assigned'); loadCreated(); }
        else { const e = await r.json().catch(()=>({})); toast('Error: '+(e.detail||'Failed'), false); }
      } catch(e){ toast('Network error', false); }
    }

    async function respondEscalation(){
      const note = prompt('Response note for the nurse:'); if(!note) return;
      const r = await apiFetch(`/nurse-tasks/${openTaskId}/respond-escalation`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({note})});
      if (r && r.ok){ toast('Escalation answered — task resumed'); refreshDetailAndLists(); } else toast('Failed', false);
    }
    async function cancelTask(){
      if (!confirm('Cancel this care task? The nurse will be notified.')) return;
      const reason = prompt('Reason for cancelling:') || 'Cancelled by assigner';
      const r = await apiFetch(`/nurse-tasks/${openTaskId}/cancel`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({reason})});
      if (r && r.ok){ toast('Task cancelled'); refreshDetailAndLists(); } else toast('Failed', false);
    }

    function renderBlocked(){
      $('na-wrap').innerHTML = '<div class="na-card" style="text-align:center;padding:40px;"><h3>My Assignments</h3><p style="color:var(--na-soft);">This page is for nurses, doctors and hospital staff.</p></div>';
    }
  