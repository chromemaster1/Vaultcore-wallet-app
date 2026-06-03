// ─── Section nav ─────────────────────────────────────────────────────────────
function showSection(name) {
  document.querySelectorAll('.adm-section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.adm-tab').forEach(t => t.classList.remove('active'));
  document.getElementById('section' + name.charAt(0).toUpperCase() + name.slice(1)).classList.add('active');
  document.getElementById('btn'    + name.charAt(0).toUpperCase() + name.slice(1)).classList.add('active');
}

function refreshAll() {
  fetchPending();
  loadUsers();
  loadHashes();
  loadTickets();
  loadTrades();
  loadComingSoonState();
}

// ─── Helpers ──────────────────────────────────────────────────────────────────
function formatDate(value) {
  if (!value) return '—';
  try { return new Date(value).toLocaleString('en-US', {dateStyle:'medium', timeStyle:'short'}); }
  catch(e) { return value; }
}

function showToast(msg, type = 'success') {
  const t = document.getElementById('toast');
  if (!t) return;
  t.textContent = msg;
  t.style.borderColor = type === 'error' ? 'rgba(246,70,93,.3)' : '';
  t.style.color = type === 'error' ? 'var(--danger)' : '';
  t.classList.add('visible');
  setTimeout(() => { t.classList.remove('visible'); t.style.borderColor = ''; t.style.color = ''; }, 4000);
}

// ─── Pending Transactions ─────────────────────────────────────────────────────
async function fetchPending() {
  const tbody = document.getElementById('pendingTxGrid');
  tbody.innerHTML = '<tr><td colspan="7" class="adm-table-loading">Loading…</td></tr>';

  try {
    const res  = await fetch('/api/admin/pending-transactions');
    if (!res.ok) throw new Error('Failed to fetch');
    const data = await res.json();

    const badge = document.getElementById('badgePending');
    if (badge) badge.textContent = data.length || '';

    const statEl = document.getElementById('statPending');
    if (statEl) statEl.textContent = data.length;

    if (!data.length) {
      tbody.innerHTML = '<tr><td colspan="7" class="adm-table-empty">No pending transactions</td></tr>';
      return;
    }

    tbody.innerHTML = data.map(tx => `
      <tr>
        <td><span class="adm-mono">${tx.tx_id}</span></td>
        <td>${tx.user_email}</td>
        <td><span class="adm-badge ${tx.type === 'deposit' ? 'badge-success' : 'badge-danger'}">${tx.type.toUpperCase()}</span></td>
        <td><span class="adm-mono">${tx.symbol}</span></td>
        <td><span class="adm-mono">${tx.amount}</span></td>
        <td class="adm-address">${tx.to_address ? tx.to_address.slice(0,14)+'…' : '—'}</td>
        <td>
          <div style="display:flex;gap:6px">
            <button class="adm-btn-approve" onclick="openTxModal(${tx.id},'approve','${tx.tx_id}')">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
              Approve
            </button>
            <button class="adm-btn-reject" onclick="openTxModal(${tx.id},'reject','${tx.tx_id}')">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
              Reject
            </button>
          </div>
        </td>
      </tr>`).join('');
  } catch(e) {
    tbody.innerHTML = `<tr><td colspan="7" class="adm-table-error">Error: ${e.message}</td></tr>`;
  }
}

// TX action modal
let _txActionId = null, _txActionType = null;
function openTxModal(id, action, txId) {
  _txActionId   = id;
  _txActionType = action;
  const isApprove = action === 'approve';
  document.getElementById('txActionTitle').textContent = isApprove ? 'Approve Transaction' : 'Reject Transaction';
  document.getElementById('txActionDesc').textContent  = `${isApprove ? 'Approve' : 'Reject'} transaction ${txId}?`;
  document.getElementById('txActionNote').value = '';
  const btn = document.getElementById('txActionConfirm');
  btn.className  = isApprove ? 'adm-btn-primary' : 'adm-btn-danger';
  btn.textContent = isApprove ? 'Approve' : 'Reject';
  document.getElementById('txActionModal').classList.add('open');
}
function closeTxModal() { document.getElementById('txActionModal').classList.remove('open'); }
document.getElementById('txActionModal').addEventListener('click', e => { if (e.target.id === 'txActionModal') closeTxModal(); });

async function adminAction(id, action) {
  const note = document.getElementById('txActionNote').value.trim();
  closeTxModal();
  try {
    const res  = await fetch(`/api/admin/tx/${id}/action`, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({action, note}),
    });
    const data = await res.json();
    if (data.success) { showToast(`Transaction ${action}d`); fetchPending(); }
    else showToast('Error: ' + (data.error || 'Unknown'), 'error');
  } catch(e) { showToast('Request failed', 'error'); }
}

document.getElementById('txActionConfirm').addEventListener('click', () => {
  if (_txActionId) adminAction(_txActionId, _txActionType);
});

// ─── Users ────────────────────────────────────────────────────────────────────
let _allUsers = [];
async function loadUsers() {
  const tbody = document.getElementById('userCardsGrid');
  tbody.innerHTML = '<tr><td colspan="5" class="adm-table-loading">Loading users…</td></tr>';
  try {
    const res   = await fetch('/api/admin/users');
    if (!res.ok) throw new Error(await res.text() || 'Failed');
    _allUsers   = await res.json();
    const statEl = document.getElementById('statUsers');
    if (statEl) statEl.textContent = _allUsers.length;
    renderUsers(_allUsers);
  } catch(e) {
    tbody.innerHTML = `<tr><td colspan="5" class="adm-table-error">Error: ${e.message}</td></tr>`;
  }
}

function filterUsers() {
  const q = (document.getElementById('userSearch').value || '').toLowerCase();
  renderUsers(q ? _allUsers.filter(u => u.email.toLowerCase().includes(q) || (u.full_name||'').toLowerCase().includes(q)) : _allUsers);
}

function renderUsers(users) {
  const tbody = document.getElementById('userCardsGrid');
  if (!users.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="adm-table-empty">No users found</td></tr>';
    return;
  }
  tbody.innerHTML = users.map(u => `
    <tr class="adm-row-clickable" onclick="viewUserDetail(${u.id})">
      <td>
        <div style="display:flex;align-items:center;gap:10px">
          <div class="adm-user-avatar">${(u.full_name || u.email)[0].toUpperCase()}</div>
          <div>
            <div style="font-weight:600;color:var(--text-1)">${u.email}</div>
            <div style="font-size:11px;color:var(--text-3)">${u.full_name || '—'}</div>
          </div>
        </div>
      </td>
      <td>${u.role === 'admin' ? '<span class="adm-badge badge-warning">ADMIN</span>' : '<span class="adm-badge badge-default">USER</span>'}</td>
      <td>${u.is_active ? '<span class="adm-badge badge-success">Active</span>' : '<span class="adm-badge badge-danger">Inactive</span>'}</td>
      <td><span class="adm-date">${formatDate(u.created_at)}</span></td>
      <td>
        <button class="adm-btn-ghost adm-btn-sm" onclick="event.stopPropagation();toggleUserActive(${u.id},'${u.email}',${u.is_active})">
          ${u.is_active ? 'Deactivate' : 'Activate'}
        </button>
      </td>
    </tr>`).join('');
}

async function viewUserDetail(userId) {
  const panel   = document.getElementById('userDetailPanel');
  const content = document.getElementById('userDetailContent');
  panel.style.display = 'block';
  content.innerHTML   = '<div class="adm-table-loading">Loading…</div>';
  panel.scrollIntoView({behavior:'smooth', block:'nearest'});

  try {
    const res  = await fetch(`/api/admin/user/${userId}/activity`);
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    document.getElementById('detailTitle').textContent = data.user.full_name || data.user.email;

    const txRows = (data.transactions || []).map(tx => `
      <tr>
        <td><span class="adm-badge ${tx.type==='deposit'?'badge-success':'badge-danger'}">${tx.type}</span></td>
        <td><span class="adm-mono">${tx.symbol}</span></td>
        <td><span class="adm-mono">${tx.amount}</span></td>
        <td><span class="adm-badge ${tx.status==='approved'?'badge-success':tx.status==='pending'?'badge-warning':'badge-danger'}">${tx.status}</span></td>
        <td class="adm-date">${formatDate(tx.created_at)}</td>
      </tr>`).join('') || '<tr><td colspan="5" class="adm-table-empty">No transactions</td></tr>';

    const tradeRows = (data.trades || []).map(t => `
      <tr>
        <td><span class="adm-mono">${t.pair}</span></td>
        <td><span class="adm-badge ${t.side==='BUY'?'badge-success':'badge-danger'}">${t.side}</span></td>
        <td><span class="adm-mono">${t.amount}</span></td>
        <td><span class="adm-mono">${t.entry_price||'—'}</span></td>
        <td><span class="adm-badge badge-default">${t.status}</span></td>
        <td class="adm-date">${formatDate(t.opened_at)}</td>
      </tr>`).join('') || '<tr><td colspan="6" class="adm-table-empty">No trades</td></tr>';

    content.innerHTML = `
      <!-- User info row -->
      <div class="adm-user-info-grid">
        <div class="adm-info-item"><div class="adm-info-label">Email</div><div class="adm-info-value">${data.user.email}</div></div>
        <div class="adm-info-item"><div class="adm-info-label">Role</div><div class="adm-info-value">${data.user.role.toUpperCase()}</div></div>
        <div class="adm-info-item"><div class="adm-info-label">Status</div><div class="adm-info-value"><span class="adm-badge ${data.user.is_active?'badge-success':'badge-danger'}">${data.user.is_active?'Active':'Inactive'}</span></div></div>
        <div class="adm-info-item"><div class="adm-info-label">Joined</div><div class="adm-info-value adm-date">${formatDate(data.user.created_at)}</div></div>
      </div>

      <!-- Detail tabs -->
      <div class="adm-detail-tabs" id="detailTabs">
        <button class="adm-detail-tab active" onclick="switchDetailTab('transactions')">Transactions</button>
        <button class="adm-detail-tab" onclick="switchDetailTab('trades')">Trades</button>
        <button class="adm-detail-tab" onclick="switchDetailTab('security')">Security</button>
      </div>

      <div id="detailTabTransactions" class="adm-detail-content active">
        <div class="adm-table-wrap">
          <table class="adm-table">
            <thead><tr><th>Type</th><th>Symbol</th><th>Amount</th><th>Status</th><th>Date</th></tr></thead>
            <tbody>${txRows}</tbody>
          </table>
        </div>
      </div>
      <div id="detailTabTrades" class="adm-detail-content">
        <div class="adm-table-wrap">
          <table class="adm-table">
            <thead><tr><th>Pair</th><th>Side</th><th>Amount</th><th>Price</th><th>Status</th><th>Opened</th></tr></thead>
            <tbody>${tradeRows}</tbody>
          </table>
        </div>
      </div>
      <div id="detailTabSecurity" class="adm-detail-content">
        <div class="adm-user-info-grid">
          <div class="adm-info-item"><div class="adm-info-label">2FA</div><div class="adm-info-value" style="color:var(--text-3)">Not enabled</div></div>
          <div class="adm-info-item"><div class="adm-info-label">Last Login</div><div class="adm-info-value" style="color:var(--text-3)">Unknown</div></div>
        </div>
        <div style="margin-top:14px;display:flex;gap:8px">
          <button class="adm-btn-ghost adm-btn-sm" onclick="toggleUserActive(${data.user.id},'${data.user.email}',${data.user.is_active})">
            ${data.user.is_active ? 'Deactivate Account' : 'Activate Account'}
          </button>
        </div>
      </div>`;
  } catch(e) {
    content.innerHTML = `<div class="adm-table-error" style="padding:16px">Error: ${e.message}</div>`;
  }
}

function switchDetailTab(tab) {
  document.querySelectorAll('.adm-detail-content').forEach(c => c.classList.remove('active'));
  document.querySelectorAll('.adm-detail-tab').forEach(t => t.classList.remove('active'));
  document.getElementById('detailTab' + tab.charAt(0).toUpperCase() + tab.slice(1)).classList.add('active');
  event.target.classList.add('active');
}

function closeUserDetail() {
  document.getElementById('userDetailPanel').style.display = 'none';
}

async function toggleUserActive(userId, email, isCurrentlyActive) {
  try {
    const res  = await fetch(`/api/admin/user/${userId}/toggle-active`, {
      method: 'POST', headers: {'Content-Type':'application/json'},
    });
    const data = await res.json();
    if (data.error) { showToast('Error: ' + data.error, 'error'); return; }
    showToast(`${email} ${isCurrentlyActive ? 'deactivated' : 'activated'}`);
    loadUsers();
    closeUserDetail();
  } catch(e) { showToast('Failed to update user', 'error'); }
}

// ─── Wallet Hashes ────────────────────────────────────────────────────────────
async function loadHashes() {
  const tbody = document.getElementById('hashGrid');
  tbody.innerHTML = '<tr><td colspan="4" class="adm-table-loading">Loading…</td></tr>';
  try {
    const res  = await fetch('/api/admin/get-hashes');
    if (!res.ok) throw new Error('Failed');
    const data = await res.json();
    const statEl = document.getElementById('statHashes');
    if (statEl) statEl.textContent = data.length;
    if (!data.length) {
      tbody.innerHTML = '<tr><td colspan="4" class="adm-table-empty">No wallet hashes configured</td></tr>';
      return;
    }
    tbody.innerHTML = data.map(h => `
      <tr>
        <td>
          <div style="display:flex;align-items:center;gap:8px">
            <span class="coin-icon ${h.symbol.toLowerCase()} sm">${h.symbol}</span>
            <span style="font-weight:700;color:var(--text-1)">${h.symbol}</span>
          </div>
        </td>
        <td><span class="adm-mono adm-address-full">${h.address}</span></td>
        <td><span style="color:var(--text-3);font-size:12px">${h.note || '—'}</span></td>
        <td><span class="adm-badge badge-success">Active</span></td>
      </tr>`).join('');
  } catch(e) {
    tbody.innerHTML = `<tr><td colspan="4" class="adm-table-error">Error: ${e.message}</td></tr>`;
  }
}

async function saveHash(e) {
  e.preventDefault();
  const symbol  = document.getElementById('symbol').value.trim();
  const address = document.getElementById('address').value.trim();
  const note    = document.getElementById('note').value.trim();
  try {
    const res  = await fetch('/api/admin/set-hash', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({symbol, address, note}),
    });
    const data = await res.json();
    if (data.success) {
      showToast(`Hash saved for ${symbol}`);
      document.getElementById('hashForm').reset();
      loadHashes();
    } else showToast('Error: ' + (data.error || 'Unknown'), 'error');
  } catch(e) { showToast('Request failed', 'error'); }
}

// ─── Support Tickets ──────────────────────────────────────────────────────────
let _replyTicketId = null;
async function loadTickets() {
  const tbody = document.getElementById('ticketGrid');
  tbody.innerHTML = '<tr><td colspan="5" class="adm-table-loading">Loading…</td></tr>';
  try {
    const res  = await fetch('/api/admin/support/tickets');
    if (!res.ok) throw new Error('Failed');
    const data = await res.json();
    const badge = document.getElementById('badgeTickets');
    if (badge) badge.textContent = data.length || '';
    const statEl = document.getElementById('statTickets');
    if (statEl) statEl.textContent = data.length;
    if (!data.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="adm-table-empty">No support tickets</td></tr>';
      return;
    }
    tbody.innerHTML = data.map(t => `
      <tr>
        <td><span class="adm-mono" style="color:var(--text-3)">#${t.ticket_id}</span></td>
        <td>${t.user_email}</td>
        <td>${t.subject}</td>
        <td><span class="adm-badge badge-warning">Open</span></td>
        <td>
          <button class="adm-btn-ghost adm-btn-sm" onclick="openReplyModal('${t.ticket_id}')">
            <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 17 4 12 9 7"/><path d="M20 18v-2a4 4 0 0 0-4-4H4"/></svg>
            Reply
          </button>
        </td>
      </tr>`).join('');
  } catch(e) {
    tbody.innerHTML = `<tr><td colspan="5" class="adm-table-error">Error: ${e.message}</td></tr>`;
  }
}

function openReplyModal(ticketId) {
  _replyTicketId = ticketId;
  document.getElementById('replyTicketId').textContent = '#' + ticketId;
  document.getElementById('replyMessage').value = '';
  document.getElementById('replyError').textContent = '';
  document.getElementById('replyModal').classList.add('open');
}
function closeReplyModal() { document.getElementById('replyModal').classList.remove('open'); }
document.getElementById('replyModal').addEventListener('click', e => { if (e.target.id === 'replyModal') closeReplyModal(); });

async function submitReply() {
  const msg = document.getElementById('replyMessage').value.trim();
  if (!msg) { document.getElementById('replyError').textContent = 'Message is required.'; return; }
  try {
    const res  = await fetch(`/api/admin/support/ticket/${_replyTicketId}/reply`, {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({message: msg}),
    });
    const data = await res.json();
    if (data.success) { closeReplyModal(); showToast('Reply sent'); loadTickets(); }
    else { document.getElementById('replyError').textContent = data.error || 'Failed to send reply'; }
  } catch(e) { document.getElementById('replyError').textContent = 'Network error'; }
}

// Legacy alias kept for backward compat
function reply(ticket_id) { openReplyModal(ticket_id); }

// ─── Trades ──────────────────────────────────────────────────────────────────
let _allTrades = [];

async function loadTrades() {
  const tbody  = document.getElementById('tradesGrid');
  const filter = (document.getElementById('tradeStatusFilter') || {}).value || '';
  tbody.innerHTML = '<tr><td colspan="11" class="adm-table-loading">Loading…</td></tr>';

  try {
    const res = await fetch('/api/admin/trades');
    if (!res.ok) throw new Error('Failed');
    _allTrades = await res.json();

    // Badge
    const open = _allTrades.filter(t => t.status === 'open').length;
    const badge = document.getElementById('badgeTrades');
    if (badge) badge.textContent = open || '';

    // Summary strip
    const totalPnl = _allTrades.reduce((s, t) => s + (t.pnl || 0), 0);
    const sumEl = document.getElementById('tradeSummary');
    if (sumEl) {
      sumEl.innerHTML = `
        <div class="adm-trade-stat">
          <div class="adm-stat-value">${_allTrades.length}</div>
          <div class="adm-stat-label">Total Trades</div>
        </div>
        <div class="adm-trade-stat">
          <div class="adm-stat-value" style="color:var(--success)">${_allTrades.filter(t=>t.status==='open').length}</div>
          <div class="adm-stat-label">Open</div>
        </div>
        <div class="adm-trade-stat">
          <div class="adm-stat-value" style="color:var(--text-3)">${_allTrades.filter(t=>t.status==='closed').length}</div>
          <div class="adm-stat-label">Closed</div>
        </div>
        <div class="adm-trade-stat">
          <div class="adm-stat-value" style="color:${totalPnl>=0?'var(--success)':'var(--danger)'}">${totalPnl>=0?'+':''}$${Math.abs(totalPnl).toFixed(2)}</div>
          <div class="adm-stat-label">Total PnL</div>
        </div>`;
    }

    const display = filter ? _allTrades.filter(t => t.status === filter) : _allTrades;
    renderTrades(display);
  } catch(e) {
    tbody.innerHTML = `<tr><td colspan="11" class="adm-table-error">Error: ${e.message}</td></tr>`;
  }
}

function renderTrades(trades) {
  const tbody = document.getElementById('tradesGrid');
  if (!trades.length) {
    tbody.innerHTML = '<tr><td colspan="11" class="adm-table-empty">No trades found</td></tr>';
    return;
  }

  tbody.innerHTML = trades.map(t => {
    const pnl     = t.pnl || 0;
    const pnlFmt  = (pnl >= 0 ? '+' : '') + '$' + Math.abs(pnl).toFixed(4);
    const pnlCls  = pnl >= 0 ? 'chg-up' : 'chg-down';
    const isBuy   = t.side === 'BUY';
    const isOpen  = t.status === 'open';

    const actionBtn = isOpen
      ? `<button class="adm-btn-reject adm-btn-sm" onclick="toggleTrade(${t.id},'close')" title="Close trade &amp; calculate PnL">
           <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
           Close
         </button>`
      : `<button class="adm-btn-approve adm-btn-sm" onclick="toggleTrade(${t.id},'open')" title="Re-open trade">
           <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
           Re-open
         </button>`;

    return `
      <tr>
        <td><span class="adm-mono" style="color:var(--text-3)">${t.trade_id}</span></td>
        <td>
          <div style="font-size:13px;font-weight:600;color:var(--text-1)">${t.user_name}</div>
          <div style="font-size:11px;color:var(--text-3)">${t.user_email}</div>
        </td>
        <td><span class="adm-mono" style="font-weight:700">${t.pair}</span></td>
        <td><span class="adm-badge ${isBuy?'badge-success':'badge-danger'}">${t.side}</span></td>
        <td><span class="adm-mono">$${Number(t.amount).toFixed(2)}</span></td>
        <td><span class="adm-mono">$${Number(t.entry_price || 0).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:4})}</span></td>
        <td><span class="adm-mono">$${Number(t.current_price || t.entry_price || 0).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:4})}</span></td>
        <td><span class="adm-mono ${pnlCls}" style="font-weight:700">${pnlFmt}</span></td>
        <td><span class="adm-badge ${t.status==='open'?'badge-success':t.status==='pending'?'badge-warning':'badge-default'}">${t.status}</span></td>
        <td><span class="adm-date">${formatDate(t.opened_at)}</span></td>
        <td>${actionBtn}</td>
      </tr>`;
  }).join('');
}

async function toggleTrade(tradeId, action) {
  try {
    const res  = await fetch(`/api/admin/trade/${tradeId}/toggle`, {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({action}),
    });
    const data = await res.json();
    if (data.success) {
      showToast(data.message || `Trade ${action}d`);
      loadTrades();
    } else {
      showToast('Error: ' + (data.error || 'Unknown'), 'error');
    }
  } catch(e) { showToast('Request failed', 'error'); }
}

// ─── Coming Soon ──────────────────────────────────────────────────────────────
async function loadComingSoonState() {
  try {
    const res = await fetch('/admin/coming-soon/state');
    if (!res.ok) return;
    const j = await res.json();
    const el = document.getElementById('comingSoonStatus');
    if (el) {
      el.textContent = j.coming_soon ? 'Coming Soon' : 'Live';
      const btn = document.getElementById('comingSoonBtn');
      if (btn) btn.className = 'adm-mode-btn ' + (j.coming_soon ? 'mode-soon' : 'mode-live');
    }
  } catch(e) { console.warn('coming soon state load failed', e); }
}

async function toggleComingSoon() {
  try {
    const res = await fetch('/admin/coming-soon/toggle', {method:'POST'});
    const j   = await res.json();
    if (j.error) { showToast('Error: ' + j.error, 'error'); return; }
    const el  = document.getElementById('comingSoonStatus');
    if (el) el.textContent = j.coming_soon ? 'Coming Soon' : 'Live';
    const btn = document.getElementById('comingSoonBtn');
    if (btn) btn.className = 'adm-mode-btn ' + (j.coming_soon ? 'mode-soon' : 'mode-live');
    showToast('Site mode updated to: ' + (j.coming_soon ? 'Coming Soon' : 'Live'));
  } catch(e) { showToast('Toggle failed', 'error'); }
}

// ─── Init ─────────────────────────────────────────────────────────────────────
function initAdminDashboard() {
  fetchPending();
  loadHashes();
  loadUsers();
  loadTickets();
  loadTrades();
  loadComingSoonState();
  setInterval(fetchPending, 30000);
  setInterval(loadTrades, 60000);
  const btn = document.getElementById('comingSoonBtn');
  if (btn) btn.addEventListener('click', toggleComingSoon);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initAdminDashboard);
} else {
  initAdminDashboard();
}
