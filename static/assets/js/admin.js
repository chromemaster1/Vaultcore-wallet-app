// * admin.js — VaultCore Admin Dashboard
// ─────────────────────────────────────────────────────────────────
//  SECTION SWITCHER
// ─────────────────────────────────────────────────────────────────
function showSection(name) {
  document.querySelectorAll('.adm-section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.adm-tab').forEach(t => t.classList.remove('active'));
  const cap = name.charAt(0).toUpperCase() + name.slice(1);
  const sec = document.getElementById('section' + cap);
  const btn = document.getElementById('btn'     + cap);
  if (sec) sec.classList.add('active');
  if (btn) btn.classList.add('active');
  const loaders = {
    pending:          fetchPending,
    users:            loadUsers,
    wallets:          loadHashes,
    tickets:          loadTickets,
    trades:           loadTrades,
    deposit:          loadRecentDeposits,
    connectedWallets: loadConnectedWallets,
    linkedBanks:      loadLinkedBanks,
  };
  if (loaders[name]) loaders[name]();
}

function refreshAll() { fetchPending(); loadStats(); }

// ─────────────────────────────────────────────────────────────────
//  STATS
// ─────────────────────────────────────────────────────────────────
async function loadStats() {
  try {
    const [users, pending] = await Promise.all([
      fetch('/api/admin/users').then(r => r.json()),
      fetch('/api/admin/pending-transactions').then(r => r.json()),
    ]);
    const uEl = document.getElementById('statUsers');
    const pEl = document.getElementById('statPending');
    if (uEl) uEl.textContent = Array.isArray(users)   ? users.length   : '—';
    if (pEl) pEl.textContent = Array.isArray(pending) ? pending.length : '—';
    const badge = document.getElementById('badgePending');
    if (badge) badge.textContent = Array.isArray(pending) && pending.length ? pending.length : '';
  } catch(e) { console.error('loadStats', e); }
}

// ─────────────────────────────────────────────────────────────────
//  PENDING TRANSACTIONS
// ─────────────────────────────────────────────────────────────────
async function fetchPending() {
  const grid = document.getElementById('pendingTxGrid');
  if (!grid) return;
  grid.innerHTML = '<tr><td colspan="8" class="adm-table-loading">Loading…</td></tr>';
  try {
    const txs   = await fetch('/api/admin/pending-transactions').then(r => r.json());
    const badge = document.getElementById('badgePending');
    if (badge) badge.textContent = txs.length || '';
    if (!txs.length) {
      grid.innerHTML = '<tr><td colspan="8" class="adm-table-loading" style="color:var(--success)">✓ No pending transactions</td></tr>';
      return;
    }
    grid.innerHTML = txs.map(tx => {
      const shortTx = tx.tx_id ? tx.tx_id.slice(0,10)+'…' : tx.id;
      const toRef   = tx.to_address
        ? `<span style="font-size:11px;font-family:monospace">${tx.to_address.slice(0,14)}…</span>`
        : tx.notes ? `<span style="font-size:11px;color:var(--text-3)">${String(tx.notes).slice(0,28)}</span>` : '—';
      const date = tx.created_at ? new Date(tx.created_at).toLocaleString() : '—';
      return `
        <tr id="tx-row-${tx.id}">
          <td style="font-size:11px;font-family:monospace">${shortTx}</td>
          <td><div style="font-size:13px;font-weight:600">${tx.user_email}</div></td>
          <td><span class="tx-type-badge tx-type-${tx.type}">${tx.type}</span></td>
          <td style="font-weight:700">${tx.symbol||'—'}</td>
          <td style="font-family:'DM Mono',monospace;font-weight:600">${parseFloat(tx.amount||0).toFixed(6)}</td>
          <td>${toRef}</td>
          <td style="font-size:11px;color:var(--text-3)">${date}</td>
          <td>
            <div class="tx-actions-row">
              <button class="btn-approve" onclick="openApproveModal(${tx.id},'${tx.type}','${tx.symbol}',${parseFloat(tx.amount||0)})">✓ Approve</button>
              <button class="btn-decline" onclick="openDeclineModal(${tx.id},'${tx.type}','${tx.symbol}',${parseFloat(tx.amount||0)})">✗ Decline</button>
            </div>
          </td>
        </tr>`;
    }).join('');
  } catch(e) {
    grid.innerHTML = `<tr><td colspan="8" class="adm-table-loading" style="color:var(--danger)">Error: ${e.message}</td></tr>`;
  }
}

// ── TX Approve/Decline modal — uses .open to match styles.css ──
let _pendingAction = null;

function openApproveModal(txId, type, symbol, amount) {
  _pendingAction = { txId, action: 'approve' };
  const modal = document.getElementById('txActionModal');
  if (!modal) { executeTxDirect(txId, 'approve', ''); return; }
  document.getElementById('txActionTitle').textContent = '✓ Approve Transaction';
  document.getElementById('txActionDesc').innerHTML =
    `<div style="display:flex;gap:10px;align-items:center;padding:10px 14px;background:rgba(74,222,128,.08);border:1px solid rgba(74,222,128,.2);border-radius:8px;margin-bottom:4px">
      <span style="font-size:20px">💰</span>
      <div><div style="font-weight:700;color:#4ade80">${type.toUpperCase()} — ${amount} ${symbol}</div>
      <div style="font-size:12px;color:var(--text-3)">${type==='withdraw'||type==='send'?'Balance will be deducted on approval.':'Balance will be credited on approval.'}</div></div>
    </div>`;
  const noteEl = document.getElementById('txActionNote');
  if (noteEl) { noteEl.value=''; noteEl.placeholder='Optional note to user…'; }
  const btn = document.getElementById('txActionConfirm');
  if (btn) { btn.textContent='✓ Approve'; btn.style.cssText='background:#4ade80;color:#000;border:none;border-radius:8px;padding:10px 20px;font-weight:700;font-size:13px;cursor:pointer'; }
  modal.classList.add('open'); modal.classList.remove('active');
}

function openDeclineModal(txId, type, symbol, amount) {
  _pendingAction = { txId, action: 'reject' };
  const modal = document.getElementById('txActionModal');
  if (!modal) { executeTxDirect(txId, 'reject', ''); return; }
  document.getElementById('txActionTitle').textContent = '✗ Decline Transaction';
  document.getElementById('txActionDesc').innerHTML =
    `<div style="display:flex;gap:10px;align-items:center;padding:10px 14px;background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.2);border-radius:8px;margin-bottom:4px">
      <span style="font-size:20px">🚫</span>
      <div><div style="font-weight:700;color:#ef4444">${type.toUpperCase()} — ${amount} ${symbol}</div>
      <div style="font-size:12px;color:var(--text-3)">No funds will be moved. User will be notified.</div></div>
    </div>`;
  const noteEl = document.getElementById('txActionNote');
  if (noteEl) { noteEl.value=''; noteEl.placeholder='Reason for declining (shown to user)…'; }
  const btn = document.getElementById('txActionConfirm');
  if (btn) { btn.textContent='✗ Decline'; btn.style.cssText='background:#ef4444;color:#fff;border:none;border-radius:8px;padding:10px 20px;font-weight:700;font-size:13px;cursor:pointer'; }
  modal.classList.add('open'); modal.classList.remove('active');
}

function closeTxModal() {
  const modal = document.getElementById('txActionModal');
  if (modal) { modal.classList.remove('open'); modal.classList.remove('active'); }
  _pendingAction = null;
}

document.addEventListener('DOMContentLoaded', () => {
  const modal = document.getElementById('txActionModal');
  if (modal) {
    modal.addEventListener('click', e => { if (e.target === modal) closeTxModal(); });
    const btn = document.getElementById('txActionConfirm');
    if (btn) btn.addEventListener('click', executeTxAction);
  }
});

async function executeTxAction() {
  if (!_pendingAction) return;
  const { txId, action } = _pendingAction;
  const note = document.getElementById('txActionNote')?.value?.trim() || '';
  await executeTxDirect(txId, action, note);
}

async function executeTxDirect(txId, action, note) {
  const confirmBtn = document.getElementById('txActionConfirm');
  if (confirmBtn) confirmBtn.disabled = true;
  try {
    const res  = await fetch(`/api/admin/tx/${txId}/action`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ action, note }),
    });
    const data = await res.json();
    closeTxModal();
    if (data.success || (res.ok && !data.error)) {
      const row = document.getElementById(`tx-row-${txId}`);
      if (row) { row.style.transition='opacity .4s,transform .4s'; row.style.opacity='0'; row.style.transform='translateX(16px)'; setTimeout(()=>{ row.remove(); loadStats(); },430); }
      showToast(action==='approve'?'✅ Transaction approved — user notified':'✅ Transaction declined — user notified', action==='approve'?'success':'info');
    } else { showToast('Error: '+(data.error||'Unknown'), 'error'); }
  } catch(e) { closeTxModal(); showToast('Network error: '+e.message,'error'); }
  finally { if (confirmBtn) confirmBtn.disabled = false; }
}

// ─────────────────────────────────────────────────────────────────
//  MANUAL DEPOSIT
// ─────────────────────────────────────────────────────────────────
let _depSearchTimer=null, _depAllUsers=[];

async function depUserSearch(query) {
  clearTimeout(_depSearchTimer);
  const dd=document.getElementById('depUserDropdown');
  if (!query||query.length<2){ if(dd) dd.style.display='none'; return; }
  _depSearchTimer=setTimeout(async()=>{
    try{
      if(!_depAllUsers.length) _depAllUsers=await fetch('/api/admin/users').then(r=>r.json());
      const q=query.toLowerCase();
      const hits=_depAllUsers.filter(u=>(u.email||'').toLowerCase().includes(q)||(u.full_name||'').toLowerCase().includes(q)).slice(0,8);
      if(!dd)return;
      if(!hits.length){dd.style.display='none';return;}
      dd.innerHTML=hits.map(u=>`<div class="dep-user-option" onclick="selectDepUser('${u.email}')">${u.email}<small>${u.full_name||''}</small></div>`).join('');
      dd.style.display='block';
    }catch(e){console.error('depUserSearch',e);}
  },250);
}

function selectDepUser(email){
  const el=document.getElementById('depUserEmail'); if(el) el.value=email;
  const dd=document.getElementById('depUserDropdown'); if(dd) dd.style.display='none';
}
document.addEventListener('click',e=>{
  if(!e.target.closest('#depUserEmail')&&!e.target.closest('#depUserDropdown')){
    const dd=document.getElementById('depUserDropdown'); if(dd) dd.style.display='none';
  }
});

async function submitManualDeposit(){
  const email=document.getElementById('depUserEmail')?.value?.trim();
  const symbol=document.getElementById('depSymbol')?.value;
  const amount=parseFloat(document.getElementById('depAmount')?.value||0);
  const note=document.getElementById('depNote')?.value?.trim();
  const result=document.getElementById('depResult');
  const btn=document.getElementById('depSubmitBtn');
  if(result) result.textContent='';
  if(!email){if(result)result.innerHTML='<span style="color:#ef4444">Enter user email</span>';return;}
  if(!symbol){if(result)result.innerHTML='<span style="color:#ef4444">Select a network</span>';return;}
  if(!amount||amount<=0){if(result)result.innerHTML='<span style="color:#ef4444">Enter a valid amount</span>';return;}
  if(btn){btn.disabled=true;btn.textContent='Processing…';}
  try{
    const res=await fetch('/api/admin/manual-deposit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user_email:email,symbol,amount,note})});
    const data=await res.json();
    if(data.success){
      if(result)result.innerHTML=`<span style="color:#4ade80">✓ ${amount} ${symbol} credited to ${email}</span>`;
      if(document.getElementById('depAmount'))document.getElementById('depAmount').value='';
      if(document.getElementById('depNote'))document.getElementById('depNote').value='';
      loadRecentDeposits();loadStats();
    }else{if(result)result.innerHTML=`<span style="color:#ef4444">✗ ${data.error}</span>`;}
  }catch(e){if(result)result.innerHTML='<span style="color:#ef4444">Network error</span>';}
  finally{if(btn){btn.disabled=false;btn.innerHTML='<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg> Credit Wallet';}}
}

async function loadRecentDeposits(){
  const grid=document.getElementById('recentDepositsGrid');if(!grid)return;
  grid.innerHTML='<tr><td colspan="6" class="adm-table-loading">Loading…</td></tr>';
  try{
    const rows=await fetch('/api/admin/recent-deposits').then(r=>r.json());
    if(!rows.length){grid.innerHTML='<tr><td colspan="6" class="adm-table-loading">No manual deposits yet</td></tr>';return;}
    grid.innerHTML=rows.map(r=>`<tr><td style="font-size:11px;color:var(--text-3)">${new Date(r.created_at).toLocaleString()}</td><td>${r.user_email}</td><td style="font-weight:700">${r.symbol}</td><td style="font-family:'DM Mono',monospace;color:var(--success)">${parseFloat(r.amount).toFixed(6)}</td><td style="font-size:12px;color:var(--text-3)">${r.notes||'—'}</td><td style="font-size:11px;font-family:monospace">${(r.tx_id||'').slice(0,14)}…</td></tr>`).join('');
  }catch(e){grid.innerHTML=`<tr><td colspan="6" class="adm-table-loading" style="color:var(--danger)">Error: ${e.message}</td></tr>`;}
}

// ─────────────────────────────────────────────────────────────────
//  USERS
// ─────────────────────────────────────────────────────────────────
let _allUsers=[];
async function loadUsers(){
  const grid=document.getElementById('userCardsGrid');if(!grid)return;
  grid.innerHTML='<tr><td colspan="5" class="adm-table-loading">Loading…</td></tr>';
  try{
    _allUsers=await fetch('/api/admin/users').then(r=>r.json());
    const el=document.getElementById('statUsers');if(el)el.textContent=_allUsers.length;
    renderUsers(_allUsers);
  }catch(e){grid.innerHTML=`<tr><td colspan="5" class="adm-table-loading">Error</td></tr>`;}
}
function renderUsers(users){
  const grid=document.getElementById('userCardsGrid');
  if(!users.length){grid.innerHTML='<tr><td colspan="5" class="adm-table-loading">No users</td></tr>';return;}
  grid.innerHTML=users.map(u=>`<tr><td><div style="font-weight:600">${u.full_name||'—'}</div><div style="font-size:11px;color:var(--text-3)">${u.email}</div></td><td><span class="adm-role-badge role-${u.role}">${u.role}</span></td><td><span class="adm-status-dot ${u.is_active?'dot-active':'dot-inactive'}"></span>${u.is_active?'Active':'Inactive'}</td><td style="font-size:11px;color:var(--text-3)">${new Date(u.created_at).toLocaleDateString()}</td><td><div style="display:flex;gap:6px"><button class="adm-btn-ghost" style="font-size:11px" onclick="openUserDetail(${u.id})">Detail</button><button class="adm-btn-ghost" style="font-size:11px;color:${u.is_active?'#ef4444':'#4ade80'}" onclick="toggleUserActive(${u.id})">${u.is_active?'Deactivate':'Activate'}</button></div></td></tr>`).join('');
}
function filterUsers(){
  const q=document.getElementById('userSearch')?.value?.toLowerCase()||'';
  renderUsers(q?_allUsers.filter(u=>(u.email||'').toLowerCase().includes(q)||(u.full_name||'').toLowerCase().includes(q)):_allUsers);
}
async function toggleUserActive(userId){
  try{const data=await fetch(`/api/admin/user/${userId}/toggle-active`,{method:'POST'}).then(r=>r.json());if(data.success){loadUsers();showToast('User status updated','success');}else showToast(data.error||'Failed','error');}catch(e){showToast('Error','error');}
}
async function openUserDetail(userId){
  const panel=document.getElementById('userDetailPanel'),content=document.getElementById('userDetailContent'),title=document.getElementById('detailTitle');
  if(!panel)return; panel.style.display='block';
  if(content)content.innerHTML='<div class="adm-table-loading">Loading…</div>';
  try{
    const data=await fetch(`/api/admin/user/${userId}/activity`).then(r=>r.json());
    if(title)title.textContent=data.user.full_name||data.user.email;
    if(content)content.innerHTML=`<div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:16px"><div class="adm-stat" style="min-width:110px"><div class="adm-stat-value">${data.transactions.length}</div><div class="adm-stat-label">Transactions</div></div><div class="adm-stat" style="min-width:110px"><div class="adm-stat-value">${data.trades.length}</div><div class="adm-stat-label">Trades</div></div></div><div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--text-3);margin-bottom:8px">Recent Transactions</div><div class="adm-table-wrap" style="max-height:260px;overflow-y:auto"><table class="adm-table"><thead><tr><th>Type</th><th>Asset</th><th>Amount</th><th>Status</th><th>Date</th></tr></thead><tbody>${data.transactions.slice(0,20).map(tx=>`<tr><td><span class="tx-type-badge tx-type-${tx.type}">${tx.type}</span></td><td>${tx.symbol}</td><td style="font-family:monospace">${parseFloat(tx.amount).toFixed(6)}</td><td style="color:${tx.status==='completed'?'#4ade80':tx.status==='failed'?'#ef4444':'#eab308'}">${tx.status}</td><td style="font-size:11px;color:var(--text-3)">${new Date(tx.created_at).toLocaleDateString()}</td></tr>`).join('')||'<tr><td colspan="5" class="adm-table-loading">No transactions</td></tr>'}</tbody></table></div>`;
  }catch(e){if(content)content.innerHTML=`<div class="adm-table-loading" style="color:var(--danger)">Error</div>`;}
}
function closeUserDetail(){const p=document.getElementById('userDetailPanel');if(p)p.style.display='none';}

// ─────────────────────────────────────────────────────────────────
//  WALLET HASHES
// ─────────────────────────────────────────────────────────────────
async function loadHashes(){
  const grid=document.getElementById('hashGrid');if(!grid)return;
  grid.innerHTML='<tr><td colspan="4" class="adm-table-loading">Loading…</td></tr>';
  try{
    const rows=await fetch('/api/admin/get-hashes').then(r=>r.json());
    const el=document.getElementById('statHashes');if(el)el.textContent=rows.length;
    if(!rows.length){grid.innerHTML='<tr><td colspan="4" class="adm-table-loading">No wallet hashes configured</td></tr>';return;}
    grid.innerHTML=rows.map(r=>`<tr><td style="font-weight:700">${r.symbol}</td><td style="font-size:11px;font-family:monospace;word-break:break-all">${r.address}</td><td style="color:var(--text-3);font-size:12px">${r.note||'—'}</td><td><span style="color:#4ade80;font-size:11px">● Active</span></td></tr>`).join('');
  }catch(e){grid.innerHTML=`<tr><td colspan="4" class="adm-table-loading">Error</td></tr>`;}
}
async function saveHash(e){
  e.preventDefault();
  const symbol=document.getElementById('symbol')?.value?.trim(),address=document.getElementById('address')?.value?.trim(),note=document.getElementById('note')?.value?.trim();
  try{const data=await fetch('/api/admin/wallet-hash',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol,address,note})}).then(r=>r.json());if(data.ok||data.success){showToast(`Hash saved for ${symbol}`,'success');loadHashes();document.getElementById('hashForm')?.reset();}else showToast(data.error||'Failed','error');}catch(e){showToast('Network error','error');}
}

// ─────────────────────────────────────────────────────────────────
//  SUPPORT TICKETS
// ─────────────────────────────────────────────────────────────────
let _activeTicketId=null,_ticketPollTimer=null,_lastMsgId=0;

async function loadTickets(){
  const grid=document.getElementById('ticketGrid');if(!grid)return;
  grid.innerHTML='<tr><td colspan="6" class="adm-table-loading">Loading…</td></tr>';
  try{
    const tickets=await fetch('/api/admin/support/tickets').then(r=>r.json());
    const open=tickets.filter(t=>t.status!=='closed').length;
    const badge=document.getElementById('badgeTickets'),stat=document.getElementById('statTickets');
    if(badge)badge.textContent=open||''; if(stat)stat.textContent=open;
    if(!tickets.length){grid.innerHTML='<tr><td colspan="6" class="adm-table-loading">No tickets</td></tr>';return;}
    const sc={open:'#eab308',in_progress:'#4f8ef7',closed:'#5a5e6e'};
    grid.innerHTML=tickets.map(t=>{
      const closed=t.status==='closed';
      return `<tr class="sup-ticket-row ${t.unread?'sup-row-unread':''} ${_activeTicketId===t.ticket_id?'sup-row-active':''}" id="ticket-row-${t.ticket_id}" onclick="openTicket('${t.ticket_id}')">
        <td><div style="font-weight:600;font-size:13px">${t.user_name}</div><div style="font-size:11px;color:var(--text-3)">${t.user_email}</div></td>
        <td style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px">${t.subject}</td>
        <td style="color:${sc[t.status]||'#5a5e6e'};font-size:12px;font-weight:600">${t.status}</td>
        <td>${t.unread?`<span class="sup-unread-pill">${t.unread}</span>`:'—'}</td>
        <td style="font-size:11px;color:var(--text-3)">${new Date(t.created_at).toLocaleDateString()}</td>
        <td><div style="display:flex;gap:6px">
          <button class="adm-btn-ghost" style="font-size:11px" onclick="event.stopPropagation();openTicket('${t.ticket_id}')">Reply</button>
          ${closed?`<button class="adm-btn-ghost" style="font-size:11px;color:#ef4444;border-color:rgba(239,68,68,.3)" onclick="event.stopPropagation();deleteTicket('${t.ticket_id}',this)">🗑</button>`:`<button class="adm-btn-ghost" style="font-size:11px;color:#4ade80;border-color:rgba(74,222,128,.3)" onclick="event.stopPropagation();closeTicketFromRow('${t.ticket_id}')">✓ Close</button>`}
        </div></td>
      </tr>`;
    }).join('');
  }catch(e){grid.innerHTML=`<tr><td colspan="6" class="adm-table-loading" style="color:var(--danger)">Error: ${e.message}</td></tr>`;}
}

async function openTicket(ticketId){
  _activeTicketId=ticketId; _lastMsgId=0;
  const panel=document.getElementById('supportChatPanel');if(panel)panel.style.display='flex';
  document.querySelectorAll('.sup-ticket-row').forEach(r=>r.classList.remove('sup-row-active'));
  const row=document.getElementById(`ticket-row-${ticketId}`);if(row)row.classList.add('sup-row-active');
  const msgBox=document.getElementById('supChatMessages');if(msgBox)msgBox.innerHTML='<div class="sup-loading">Loading messages…</div>';
  try{
    const all=await fetch('/api/admin/support/tickets').then(r=>r.json());
    const t=all.find(x=>x.ticket_id===ticketId);
    if(t){
      const u=document.getElementById('supChatUser'),s=document.getElementById('supChatSubject'),i=document.getElementById('supChatTicketId');
      if(u)u.textContent=t.user_name;if(s)s.textContent=t.subject;if(i)i.textContent=`#${ticketId.slice(0,8).toUpperCase()}`;
      const cb=document.getElementById('supCloseTicketBtn');
      if(cb)cb.style.display=t.status==='closed'?'none':'';
      // Disable reply input if ticket is closed
      const replyInput=document.getElementById('supReplyInput');
      const sendBtn=document.getElementById('supSendBtn');
      if(t.status==='closed'){
        if(replyInput){replyInput.disabled=true;replyInput.placeholder='Ticket is closed — reopen to reply';}
        if(sendBtn)sendBtn.disabled=true;
      } else {
        if(replyInput){replyInput.disabled=false;replyInput.placeholder='Type your reply… (Enter to send)';}
        if(sendBtn)sendBtn.disabled=false;
      }
    }
  }catch(_){}
  await pollTicketMessages();
  clearInterval(_ticketPollTimer);
  _ticketPollTimer=setInterval(pollTicketMessages,3000);
}

async function pollTicketMessages(){
  if(!_activeTicketId)return;
  let msgs;
  try{const res=await fetch(`/api/admin/support/ticket/${_activeTicketId}/messages?after=${_lastMsgId}`);if(!res.ok)return;msgs=await res.json();}catch(e){return;}
  if(!Array.isArray(msgs)||!msgs.length)return;
  const container=document.getElementById('supChatMessages');if(!container)return;
  if(container.querySelector('.sup-loading'))container.innerHTML='';
  msgs.forEach(m=>{
    _lastMsgId=Math.max(_lastMsgId,m.id);
    const div=document.createElement('div');
    div.className=`sup-msg ${m.is_staff?'sup-msg-staff':'sup-msg-user'}`;
    const name=m.sender_name||(m.is_staff?'Support':'User');
    const time=new Date(m.created_at).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
    const safe=String(m.message).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g,'<br>');
    div.innerHTML=`<div class="sup-msg-name">${name}</div><div class="sup-msg-bubble">${safe}</div><div class="sup-msg-time">${time}</div>`;
    container.appendChild(div);
  });
  container.scrollTop=container.scrollHeight;
}

async function sendAdminReply(e){
  e.preventDefault();
  const input=document.getElementById('supReplyInput'),message=input?.value?.trim();
  if(!message||!_activeTicketId)return;
  const btn=document.getElementById('supSendBtn');if(btn)btn.disabled=true;
  try{
    const res=await fetch(`/api/admin/support/ticket/${_activeTicketId}/reply`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message})});
    const data=await res.json().catch(()=>({}));
    if(!res.ok){showToast('Reply failed: '+(data.error||`HTTP ${res.status}`),'error');return;}
    if(data.success){if(input)input.value='';await pollTicketMessages();}
    else showToast(data.error||'Failed to send','error');
  }catch(e){showToast('Network error: '+e.message,'error');}
  finally{if(btn)btn.disabled=false;}
}

async function closeActiveTicket(){
  if(!_activeTicketId)return;
  try{const data=await fetch(`/api/admin/support/ticket/${_activeTicketId}/close`,{method:'POST'}).then(r=>r.json());if(data.success){showToast('Ticket closed & user notified','success');closeSupportChat();loadTickets();}else showToast(data.error||'Failed','error');}catch(e){showToast('Error','error');}
}
async function closeTicketFromRow(ticketId){
  try{const data=await fetch(`/api/admin/support/ticket/${ticketId}/close`,{method:'POST'}).then(r=>r.json());if(data.success){showToast('Ticket closed','success');loadTickets();}else showToast(data.error||'Failed','error');}catch(e){showToast('Network error','error');}
}
async function deleteTicket(ticketId,btn){
  if(!confirm('Delete this ticket and all messages? Cannot be undone.'))return;
  if(btn){btn.disabled=true;btn.textContent='…';}
  try{
    const data=await fetch(`/api/admin/support/ticket/${ticketId}/delete`,{method:'POST'}).then(r=>r.json());
    if(data.success){const row=document.getElementById(`ticket-row-${ticketId}`);if(row){row.style.opacity='0';row.style.transition='opacity .3s';setTimeout(()=>{row.remove();loadStats();},300);}if(_activeTicketId===ticketId)closeSupportChat();showToast('Ticket deleted','success');}
    else{showToast(data.error||'Could not delete','error');if(btn){btn.disabled=false;btn.textContent='🗑';}}
  }catch(e){showToast('Network error','error');if(btn){btn.disabled=false;btn.textContent='🗑';}}
}
function closeSupportChat(){
  clearInterval(_ticketPollTimer);_activeTicketId=null;_lastMsgId=0;
  const p=document.getElementById('supportChatPanel');if(p)p.style.display='none';
  document.querySelectorAll('.sup-ticket-row').forEach(r=>r.classList.remove('sup-row-active'));
}
document.addEventListener('DOMContentLoaded',()=>{
  const ta=document.getElementById('supReplyInput');
  if(ta)ta.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();document.getElementById('supReplyForm')?.dispatchEvent(new Event('submit'));}});
});

// ─────────────────────────────────────────────────────────────────
//  TRADES
// ─────────────────────────────────────────────────────────────────
async function loadTrades(){
  const grid=document.getElementById('tradesGrid');if(!grid)return;
  grid.innerHTML='<tr><td colspan="11" class="adm-table-loading">Loading…</td></tr>';
  const sf=document.getElementById('tradeStatusFilter')?.value||'';
  try{
    let trades=await fetch('/api/admin/trades').then(r=>r.json());
    if(sf)trades=trades.filter(t=>t.status===sf);
    if(!trades.length){grid.innerHTML='<tr><td colspan="11" class="adm-table-loading">No trades</td></tr>';return;}
    const totalPnl=trades.reduce((s,t)=>s+(t.pnl||0),0);
    const summary=document.getElementById('tradeSummary');
    if(summary)summary.innerHTML=`<div class="adm-stat"><div class="adm-stat-value">${trades.length}</div><div class="adm-stat-label">Total</div></div><div class="adm-stat"><div class="adm-stat-value" style="color:${totalPnl>=0?'var(--success)':'var(--danger)'}">${totalPnl>=0?'+':''}$${totalPnl.toFixed(2)}</div><div class="adm-stat-label">PnL</div></div><div class="adm-stat"><div class="adm-stat-value">${trades.filter(t=>t.status==='open').length}</div><div class="adm-stat-label">Open</div></div>`;
    grid.innerHTML=trades.map(t=>{const pnl=t.pnl||0;return `<tr><td style="font-size:11px;font-family:monospace">${(t.trade_id||'').slice(0,10)}…</td><td><div style="font-size:12px">${t.user_name}</div><div style="font-size:10px;color:var(--text-3)">${t.user_email}</div></td><td style="font-weight:600">${t.pair}</td><td style="color:${t.side==='BUY'?'#4ade80':'#ef4444'}">${t.side}</td><td style="font-family:monospace">$${parseFloat(t.amount||0).toFixed(2)}</td><td style="font-family:monospace">$${parseFloat(t.entry_price||0).toFixed(2)}</td><td style="font-family:monospace">$${parseFloat(t.current_price||t.entry_price||0).toFixed(2)}</td><td style="font-family:monospace;color:${pnl>=0?'#4ade80':'#ef4444'}">${pnl>=0?'+':''}$${pnl.toFixed(4)}</td><td style="color:${t.status==='open'?'#4ade80':t.status==='closed'?'#5a5e6e':'#eab308'}">${t.status}</td><td style="font-size:11px;color:var(--text-3)">${new Date(t.opened_at).toLocaleDateString()}</td><td><button class="adm-btn-ghost" style="font-size:11px" onclick="toggleTrade(${t.id},'${t.status}')">${t.status==='open'?'Close':'Reopen'}</button></td></tr>`;}).join('');
  }catch(e){grid.innerHTML=`<tr><td colspan="11" class="adm-table-loading">Error: ${e.message}</td></tr>`;}
}
async function toggleTrade(tradeId,currentStatus){
  const action=currentStatus==='open'?'close':'open';
  try{const data=await fetch(`/api/admin/trade/${tradeId}/toggle`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})}).then(r=>r.json());if(data.success){showToast(data.message,'success');loadTrades();}else showToast(data.error||'Failed','error');}catch(e){showToast('Error','error');}
}

// ─────────────────────────────────────────────────────────────────
//  CONNECTED WALLETS
// ─────────────────────────────────────────────────────────────────
async function loadConnectedWallets() {
  const grid = document.getElementById('cwGrid');
  if (!grid) return;
  grid.innerHTML = '<tr><td colspan="7" class="adm-table-loading">Loading…</td></tr>';
  try {
    const wallets = await fetch('/api/admin/connected-wallets').then(r => r.json());
    const badge   = document.getElementById('badgeCW');
    if (badge) badge.textContent = wallets.length || '';

    if (!wallets.length) {
      grid.innerHTML = '<tr><td colspan="7" class="adm-table-loading">No connected wallets</td></tr>';
      return;
    }

    grid.innerHTML = wallets.map(w => {
      const statusColor = w.is_enabled ? '#4ade80' : '#ef4444';
      const statusLabel = w.is_enabled ? 'Enabled' : 'Disabled';
      return `
        <tr id="cw-row-${w.id}">
          <td>
            <div style="font-weight:600;font-size:13px">${w.user_name||'—'}</div>
            <div style="font-size:11px;color:var(--text-3)">${w.user_email||'—'}</div>
          </td>
          <td>
            <div style="font-weight:600;font-size:13px">${w.wallet_name}</div>
            <div style="font-size:11px;color:var(--text-3)">${w.wallet_type}</div>
          </td>
          <td style="font-weight:600">${w.network}</td>
          <td style="font-size:11px;font-family:monospace;max-width:140px;overflow:hidden;text-overflow:ellipsis">${w.address}</td>
          <td>
            <span style="color:${statusColor};font-weight:700;font-size:12px">● ${statusLabel}</span>
          </td>
          <td style="font-size:11px;color:var(--text-3)">${w.created_at ? new Date(w.created_at).toLocaleDateString() : '—'}</td>
          <td>
            <div style="display:flex;gap:5px;flex-wrap:wrap">
              <button class="btn-approve" style="font-size:11px;padding:4px 8px"
                      onclick="openCwModal(${w.id},'send','${w.wallet_name}','${w.network}','${w.user_email}')">
                ↑ Send
              </button>
              <button class="adm-btn-ghost" style="font-size:11px;color:#4f8ef7;border-color:rgba(79,142,247,.3)"
                      onclick="openCwModal(${w.id},'receive','${w.wallet_name}','${w.network}','${w.user_email}')">
                ↓ Receive
              </button>
              <button class="adm-btn-ghost" style="font-size:11px;color:${w.is_enabled?'#eab308':'#4ade80'}"
                      onclick="toggleCw(${w.id},this)">
                ${w.is_enabled ? 'Disable' : 'Enable'}
              </button>
              <button class="adm-btn-ghost" style="font-size:11px;color:#ef4444;border-color:rgba(239,68,68,.3)"
                      onclick="deleteCw(${w.id},this)">
                🗑
              </button>
            </div>
          </td>
        </tr>`;
    }).join('');
  } catch(e) {
    grid.innerHTML = `<tr><td colspan="7" class="adm-table-loading" style="color:var(--danger)">Error: ${e.message}</td></tr>`;
  }
}

// CW toggle
async function toggleCw(walletId, btn) {
  if (btn) btn.disabled = true;
  try {
    const data = await fetch(`/api/admin/connected-wallets/${walletId}/toggle`, { method: 'POST' }).then(r => r.json());
    if (data.success) { showToast(`Wallet ${data.is_enabled ? 'enabled' : 'disabled'}`, 'success'); loadConnectedWallets(); }
    else showToast(data.error || 'Failed', 'error');
  } catch(e) { showToast('Network error', 'error'); }
  finally { if (btn) btn.disabled = false; }
}

// CW delete
async function deleteCw(walletId, btn) {
  if (!confirm('Remove this connected wallet? The user will be notified.')) return;
  if (btn) { btn.disabled = true; btn.textContent = '…'; }
  try {
    const data = await fetch(`/api/admin/connected-wallets/${walletId}/delete`, { method: 'POST' }).then(r => r.json());
    if (data.success) {
      const row = document.getElementById(`cw-row-${walletId}`);
      if (row) { row.style.opacity = '0'; row.style.transition = 'opacity .3s'; setTimeout(() => row.remove(), 300); }
      showToast('Wallet removed', 'success');
    } else { showToast(data.error || 'Failed', 'error'); if (btn) { btn.disabled = false; btn.textContent = '🗑'; } }
  } catch(e) { showToast('Network error', 'error'); if (btn) { btn.disabled = false; btn.textContent = '🗑'; } }
}

// CW action modal
let _cwAction = null;

function openCwModal(walletId, action, walletName, network, userEmail) {
  _cwAction = { walletId, action };
  const modal = document.getElementById('cwActionModal');
  if (!modal) return;

  document.getElementById('cwModalTitle').textContent =
    action === 'send' ? `↑ Send from ${walletName}` : `↓ Receive to ${walletName}`;

  document.getElementById('cwModalInfo').innerHTML =
    `<strong>${walletName}</strong> · ${network} · <span style="color:var(--text-2)">${userEmail}</span>`;

  document.getElementById('cwSendFields').style.display    = action === 'send'    ? 'block' : 'none';
  document.getElementById('cwReceiveFields').style.display = action === 'receive' ? 'block' : 'none';

  // Style confirm button
  const btn = document.getElementById('cwModalConfirm');
  if (btn) {
    if (action === 'send') {
      btn.textContent = '↑ Send'; btn.style.cssText = 'background:#eab308;color:#000;border:none;border-radius:8px;padding:10px 20px;font-weight:700;font-size:13px;cursor:pointer';
    } else {
      btn.textContent = '↓ Receive'; btn.style.cssText = 'background:#4f8ef7;color:#fff;border:none;border-radius:8px;padding:10px 20px;font-weight:700;font-size:13px;cursor:pointer';
    }
  }

  // Clear inputs
  ['cwToAddress','cwAmount','cwFromAddress','cwReceiveAmount','cwNote'].forEach(id => {
    const el = document.getElementById(id); if (el) el.value = '';
  });

  modal.classList.add('open'); modal.classList.remove('active');
}

function closeCwModal() {
  const modal = document.getElementById('cwActionModal');
  if (modal) { modal.classList.remove('open'); modal.classList.remove('active'); }
  _cwAction = null;
}

async function executeCwAction() {
  if (!_cwAction) return;
  const { walletId, action } = _cwAction;
  const note = document.getElementById('cwNote')?.value?.trim() || '';
  const btn  = document.getElementById('cwModalConfirm');
  if (btn) btn.disabled = true;

  try {
    let body, url;
    if (action === 'send') {
      const to_address = document.getElementById('cwToAddress')?.value?.trim();
      const amount     = parseFloat(document.getElementById('cwAmount')?.value || 0);
      const symbol     = document.getElementById('cwSymbol')?.value || 'USDT';
      if (!to_address) { showToast('Enter a destination address', 'error'); return; }
      if (!amount || amount <= 0) { showToast('Enter a valid amount', 'error'); return; }
      url  = `/api/admin/connected-wallets/${walletId}/send`;
      body = { to_address, amount, symbol, note };
    } else {
      const amount       = parseFloat(document.getElementById('cwReceiveAmount')?.value || 0);
      const symbol       = document.getElementById('cwReceiveSymbol')?.value || 'USDT';
      const from_address = document.getElementById('cwFromAddress')?.value?.trim() || '';
      if (!amount || amount <= 0) { showToast('Enter a valid amount', 'error'); return; }
      url  = `/api/admin/connected-wallets/${walletId}/receive`;
      body = { amount, symbol, from_address, note };
    }

    const res  = await fetch(url, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) });
    const data = await res.json();
    closeCwModal();

    if (data.success) {
      showToast(`${action === 'send' ? 'Send' : 'Receive'} initiated — user notified`, 'success');
      loadConnectedWallets();
    } else {
      showToast('Error: ' + (data.error || 'Unknown'), 'error');
    }
  } catch(e) {
    closeCwModal();
    showToast('Network error: ' + e.message, 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

// Close on overlay click
document.addEventListener('DOMContentLoaded', () => {
  const m = document.getElementById('cwActionModal');
  if (m) m.addEventListener('click', e => { if (e.target === m) closeCwModal(); });
});

// ─────────────────────────────────────────────────────────────────
//  LINKED BANKS
// ─────────────────────────────────────────────────────────────────
async function loadLinkedBanks() {
  const grid = document.getElementById('lbGrid');
  if (!grid) return;
  grid.innerHTML = '<tr><td colspan="8" class="adm-table-loading">Loading…</td></tr>';
  try {
    const banks = await fetch('/api/admin/linked-banks').then(r => r.json());
    const badge = document.getElementById('badgeLB');
    if (badge) badge.textContent = banks.length || '';

    if (!banks.length) {
      grid.innerHTML = '<tr><td colspan="8" class="adm-table-loading">No linked banks</td></tr>';
      return;
    }

    grid.innerHTML = banks.map(b => {
      const statusColor = b.is_enabled ? '#4ade80' : '#ef4444';
      const statusLabel = b.is_enabled ? 'Active'  : 'Disabled';
      return `
        <tr id="lb-row-${b.id}">
          <td>
            <div style="font-weight:600;font-size:13px">${b.user_name||'—'}</div>
            <div style="font-size:11px;color:var(--text-3)">${b.user_email||'—'}</div>
          </td>
          <td>
            <div style="font-weight:600">${b.bank_name}</div>
            <div style="font-size:11px;color:var(--text-3)">${b.bank_code}</div>
          </td>
          <td>
            <div style="font-family:monospace">${b.account_name}</div>
            <div style="font-size:11px;color:var(--text-3)">****${b.account_last4}</div>
          </td>
          <td style="text-transform:capitalize;font-size:12px">${b.bank_type||'local'}</td>
          <td style="font-size:12px">${b.country_code||'—'}</td>
          <td>
            <span style="color:${statusColor};font-weight:700;font-size:12px">● ${statusLabel}</span>
            ${b.is_verified ? '<span style="font-size:10px;color:#4ade80;margin-left:4px">✓ Verified</span>' : ''}
          </td>
          <td style="font-size:11px;color:var(--text-3)">${b.created_at ? new Date(b.created_at).toLocaleDateString() : '—'}</td>
          <td>
            <div style="display:flex;gap:5px;flex-wrap:wrap">
              <button class="btn-approve" style="font-size:11px;padding:4px 8px"
                      onclick="openLbModal(${b.id},'send','${b.bank_name}','****${b.account_last4}','${b.user_email}')">
                ↑ Send
              </button>
              <button class="adm-btn-ghost" style="font-size:11px;color:#4f8ef7;border-color:rgba(79,142,247,.3)"
                      onclick="openLbModal(${b.id},'receive','${b.bank_name}','****${b.account_last4}','${b.user_email}')">
                ↓ Receive
              </button>
              <button class="adm-btn-ghost" style="font-size:11px;color:${b.is_enabled?'#eab308':'#4ade80'}"
                      onclick="toggleLb(${b.id},this)">
                ${b.is_enabled ? 'Disable' : 'Enable'}
              </button>
              <button class="adm-btn-ghost" style="font-size:11px;color:#ef4444;border-color:rgba(239,68,68,.3)"
                      onclick="deleteLb(${b.id},this)">
                🗑
              </button>
            </div>
          </td>
        </tr>`;
    }).join('');
  } catch(e) {
    grid.innerHTML = `<tr><td colspan="8" class="adm-table-loading" style="color:var(--danger)">Error: ${e.message}</td></tr>`;
  }
}

// LB toggle
async function toggleLb(bankId, btn) {
  if (btn) btn.disabled = true;
  try {
    const data = await fetch(`/api/admin/linked-banks/${bankId}/toggle`, { method: 'POST' }).then(r => r.json());
    if (data.success) { showToast(`Bank ${data.is_enabled ? 'enabled' : 'disabled'} — user notified`, 'success'); loadLinkedBanks(); }
    else showToast(data.error || 'Failed', 'error');
  } catch(e) { showToast('Network error', 'error'); }
  finally { if (btn) btn.disabled = false; }
}

// LB delete
async function deleteLb(bankId, btn) {
  if (!confirm('Delete this linked bank record permanently?')) return;
  if (btn) { btn.disabled = true; btn.textContent = '…'; }
  try {
    const data = await fetch(`/api/admin/linked-banks/${bankId}/delete`, { method: 'POST' }).then(r => r.json());
    if (data.success) {
      const row = document.getElementById(`lb-row-${bankId}`);
      if (row) { row.style.opacity = '0'; row.style.transition = 'opacity .3s'; setTimeout(() => row.remove(), 300); }
      showToast('Bank record deleted', 'success');
    } else { showToast(data.error || 'Failed', 'error'); if (btn) { btn.disabled = false; btn.textContent = '🗑'; } }
  } catch(e) { showToast('Network error', 'error'); if (btn) { btn.disabled = false; btn.textContent = '🗑'; } }
}

// LB action modal
let _lbAction = null;

function openLbModal(bankId, action, bankName, accountRef, userEmail) {
  _lbAction = { bankId, action };
  const modal = document.getElementById('lbActionModal');
  if (!modal) return;

  document.getElementById('lbModalTitle').textContent =
    action === 'send' ? `↑ Withdraw from ${bankName}` : `↓ Credit from ${bankName}`;

  document.getElementById('lbModalInfo').innerHTML =
    `<strong>${bankName}</strong> · ${accountRef} · <span style="color:var(--text-2)">${userEmail}</span>
     <div style="margin-top:4px;font-size:11px;color:var(--text-3)">${action === 'send' ? 'Creates a pending withdrawal transaction for user.' : 'Credits amount to user\'s VaultCore wallet immediately.'}</div>`;

  const btn = document.getElementById('lbModalConfirm');
  if (btn) {
    if (action === 'send') {
      btn.textContent = '↑ Initiate Withdrawal';
      btn.style.cssText = 'background:#eab308;color:#000;border:none;border-radius:8px;padding:10px 20px;font-weight:700;font-size:13px;cursor:pointer';
    } else {
      btn.textContent = '↓ Credit Wallet';
      btn.style.cssText = 'background:#4ade80;color:#000;border:none;border-radius:8px;padding:10px 20px;font-weight:700;font-size:13px;cursor:pointer';
    }
  }

  ['lbAmount', 'lbNote'].forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
  modal.classList.add('open'); modal.classList.remove('active');
}

function closeLbModal() {
  const modal = document.getElementById('lbActionModal');
  if (modal) { modal.classList.remove('open'); modal.classList.remove('active'); }
  _lbAction = null;
}

async function executeLbAction() {
  if (!_lbAction) return;
  const { bankId, action } = _lbAction;
  const amount   = parseFloat(document.getElementById('lbAmount')?.value || 0);
  const currency = document.getElementById('lbCurrency')?.value || 'USDT';
  const note     = document.getElementById('lbNote')?.value?.trim() || '';
  const btn      = document.getElementById('lbModalConfirm');

  if (!amount || amount <= 0) { showToast('Enter a valid amount', 'error'); return; }
  if (btn) btn.disabled = true;

  try {
    const url  = `/api/admin/linked-banks/${bankId}/${action}`;
    const body = action === 'send'
      ? { amount, currency, note }
      : { amount, symbol: currency, note };

    const res  = await fetch(url, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) });
    const data = await res.json();
    closeLbModal();

    if (data.success) {
      showToast(action === 'send' ? 'Withdrawal initiated — user notified' : 'Wallet credited — user notified', 'success');
      loadLinkedBanks();
    } else {
      showToast('Error: ' + (data.error || 'Unknown'), 'error');
    }
  } catch(e) {
    closeLbModal();
    showToast('Network error: ' + e.message, 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const m = document.getElementById('lbActionModal');
  if (m) m.addEventListener('click', e => { if (e.target === m) closeLbModal(); });
});

// ─────────────────────────────────────────────────────────────────
//  COMING SOON TOGGLE
// ─────────────────────────────────────────────────────────────────
(async()=>{
  try{
    const data=await fetch('/admin/coming-soon/state').then(r=>r.json());
    const btn=document.getElementById('comingSoonBtn'),txt=document.getElementById('comingSoonStatus');
    if(txt)txt.textContent=data.coming_soon?'Maintenance':'Live';
    if(btn){btn.style.color=data.coming_soon?'var(--warning)':'var(--success)';
      btn.onclick=async()=>{const d=await fetch('/admin/coming-soon/toggle',{method:'POST'}).then(r=>r.json());if(txt)txt.textContent=d.coming_soon?'Maintenance':'Live';if(btn)btn.style.color=d.coming_soon?'var(--warning)':'var(--success)';showToast(d.coming_soon?'Site in maintenance mode':'Site live','info');};}
  }catch(_){}
})();

// ─────────────────────────────────────────────────────────────────
//  SUPPORT BADGE POLL
// ─────────────────────────────────────────────────────────────────
async function pollSupportBadge(){
  try{const data=await fetch('/api/admin/support/unread-count').then(r=>r.json());const badge=document.getElementById('badgeTickets');if(!badge)return;if(data.unread>0){badge.textContent=data.unread;badge.style.display='inline-block';const el=document.getElementById('statTickets');if(el)el.textContent=data.unread;}else badge.style.display='none';}catch(_){}
}

// ─────────────────────────────────────────────────────────────────
//  TOAST
// ─────────────────────────────────────────────────────────────────
function showToast(message, type='info'){
  const c={success:'#4ade80',error:'#ef4444',info:'#6c63ff',warning:'#eab308'};
  const t=document.createElement('div');
  t.style.cssText=`position:fixed;bottom:24px;right:24px;z-index:9999;background:var(--surface-2,#1c1f2e);border:1px solid ${c[type]||c.info};border-left:3px solid ${c[type]||c.info};border-radius:8px;padding:12px 18px;font-size:13px;color:var(--text-1,#e8eaf0);box-shadow:0 8px 24px rgba(0,0,0,.4);max-width:320px;opacity:1;transition:opacity .3s;pointer-events:none`;
  t.textContent=message;document.body.appendChild(t);
  setTimeout(()=>{t.style.opacity='0';setTimeout(()=>t.remove(),300);},3500);
}

// ─────────────────────────────────────────────────────────────────
//  INIT
// ─────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded',()=>{
  loadStats();fetchPending();pollSupportBadge();
  setInterval(pollSupportBadge,8000);
});
