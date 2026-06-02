async function fetchPending() {
  const grid = document.getElementById('pendingTxGrid');
  grid.innerHTML = '<div style="grid-column:1/-1; padding:20px; text-align:center; color:rgba(255,255,255,0.6);">Loading transactions…</div>';

  try {
    const res = await fetch('/api/admin/pending-transactions');
    if (!res.ok) throw new Error('Failed to fetch transactions');
    const data = await res.json();
    grid.innerHTML = '';

    if (!data.length) {
      grid.innerHTML = '<div style="grid-column:1/-1; padding:20px; text-align:center; color:rgba(255,255,255,0.6);">No pending transactions</div>';
      return;
    }

    data.forEach(tx => {
      const card = document.createElement('div');
      const typeColor = tx.type === 'deposit' ? '#86efac' : '#fca5a5';
      const typeBg = tx.type === 'deposit' ? 'rgba(34,197,94,0.2)' : 'rgba(239,68,68,0.2)';

      card.style.cssText = `
        padding:16px; background:linear-gradient(135deg, rgba(59,130,246,0.1), rgba(139,92,246,0.1));
        border:1px solid rgba(255,255,255,.08); border-radius:12px; transition:all 0.2s;
        display:flex; flex-direction:column; gap:12px;
      `;
      card.onmouseover = () => card.style.background = 'linear-gradient(135deg, rgba(59,130,246,0.15), rgba(139,92,246,0.15))';
      card.onmouseout = () => card.style.background = 'linear-gradient(135deg, rgba(59,130,246,0.1), rgba(139,92,246,0.1))';

      card.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:start; gap:8px;">
          <div style="flex:1;">
            <div style="font-weight:600; color:rgba(255,255,255,0.95); font-size:14px;">TX: ${tx.tx_id}</div>
            <div style="font-size:12px; color:rgba(255,255,255,0.6); margin-top:4px;">${tx.user_email}</div>
          </div>
          <span style="display:inline-block; background:${typeBg}; color:${typeColor}; padding:2px 8px; border-radius:4px; font-size:11px; font-weight:500;">${tx.type.toUpperCase()}</span>
        </div>
        <div style="background:rgba(255,255,255,.02); padding:12px; border-radius:8px; font-size:12px;">
          <div><strong>${tx.symbol}:</strong> ${tx.amount} <span style="color:rgba(255,255,255,0.5);">units</span></div>
          ${tx.to_address ? `<div style="margin-top:6px; word-break:break-all;"><strong>To:</strong> <span style="color:rgba(255,255,255,0.6); font-size:11px;">${tx.to_address}</span></div>` : ''}
        </div>
        <div style="display:flex; gap:8px; padding-top:8px; border-top:1px solid rgba(255,255,255,.08);">
          <button onclick="adminAction(${tx.id}, 'approve')" style="flex:1; padding:6px 12px; background:rgba(34,197,94,0.3); border:1px solid rgba(34,197,94,0.5); color:#86efac; border-radius:6px; cursor:pointer; font-size:12px; font-weight:500;">✓ Approve</button>
          <button onclick="adminAction(${tx.id}, 'reject')" style="flex:1; padding:6px 12px; background:rgba(239,68,68,0.3); border:1px solid rgba(239,68,68,0.5); color:#fca5a5; border-radius:6px; cursor:pointer; font-size:12px; font-weight:500;">✕ Reject</button>
        </div>
      `;
      grid.appendChild(card);
    });
  } catch (e) {
    grid.innerHTML = `<div style="grid-column:1/-1; padding:20px; text-align:center; color:#fca5a5;">Error: ${e.message}</div>`;
    console.error('Failed to load pending transactions:', e);
  }
}

async function adminAction(id, action) {
  const note = prompt('Optional note for this action:') || '';
  const res = await fetch(`/api/admin/tx/${id}/action`, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({action, note}),
  });
  const data = await res.json();
  if (data.success) {
    alert('Action completed');
    fetchPending();
  } else {
    alert('Error: ' + (data.error || JSON.stringify(data)));
  }
}

async function loadHashes() {
  const grid = document.getElementById('hashGrid');
  grid.innerHTML = '<div style="grid-column:1/-1; padding:20px; text-align:center; color:rgba(255,255,255,0.6);">Loading hashes…</div>';

  try {
    const res = await fetch('/api/admin/get-hashes');
    if (!res.ok) throw new Error('Failed to fetch hashes');
    const data = await res.json();
    grid.innerHTML = '';

    if (!data.length) {
      grid.innerHTML = '<div style="grid-column:1/-1; padding:20px; text-align:center; color:rgba(255,255,255,0.6);">No wallet hashes configured</div>';
      return;
    }

    data.forEach(h => {
      const card = document.createElement('div');
      card.style.cssText = `
        padding:16px; background:linear-gradient(135deg, rgba(34,197,94,0.1), rgba(59,130,246,0.1));
        border:1px solid rgba(255,255,255,.08); border-radius:12px; transition:all 0.2s;
        display:flex; flex-direction:column; gap:12px;
      `;
      card.onmouseover = () => card.style.background = 'linear-gradient(135deg, rgba(34,197,94,0.15), rgba(59,130,246,0.15))';
      card.onmouseout = () => card.style.background = 'linear-gradient(135deg, rgba(34,197,94,0.1), rgba(59,130,246,0.1))';

      card.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; gap:8px;">
          <div>
            <div style="font-weight:600; color:rgba(255,255,255,0.95); font-size:16px;">${h.symbol}</div>
            ${h.note ? `<div style="font-size:12px; color:rgba(255,255,255,0.6); margin-top:4px;">${h.note}</div>` : ''}
          </div>
          <span style="display:inline-block; background:rgba(34,197,94,0.2); color:#86efac; padding:4px 8px; border-radius:4px; font-size:10px; font-weight:500;">ACTIVE</span>
        </div>
        <div style="background:rgba(255,255,255,.02); padding:12px; border-radius:8px; font-size:11px; word-break:break-all; color:rgba(255,255,255,0.7);">
          ${h.address}
        </div>
      `;
      grid.appendChild(card);
    });
  } catch (e) {
    grid.innerHTML = `<div style="grid-column:1/-1; padding:20px; text-align:center; color:#fca5a5;">Error: ${e.message}</div>`;
    console.error('Failed to load hashes:', e);
  }
}

async function saveHash(e) {
  e.preventDefault();
  const symbol = document.getElementById('symbol').value.trim();
  const address = document.getElementById('address').value.trim();
  const note = document.getElementById('note').value.trim();
  const res = await fetch('/api/admin/set-hash', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({symbol, address, note}),
  });
  const data = await res.json();
  if (data.success) {
    alert('Saved');
    loadHashes();
  } else {
    alert('Error: ' + (data.error || JSON.stringify(data)));
  }
}

async function loadTickets() {
  const grid = document.getElementById('ticketGrid');
  grid.innerHTML = '<div style="grid-column:1/-1; padding:20px; text-align:center; color:rgba(255,255,255,0.6);">Loading tickets…</div>';

  try {
    const res = await fetch('/api/admin/support/tickets');
    if (!res.ok) throw new Error('Failed to fetch tickets');
    const data = await res.json();
    grid.innerHTML = '';

    if (!data.length) {
      grid.innerHTML = '<div style="grid-column:1/-1; padding:20px; text-align:center; color:rgba(255,255,255,0.6);">No support tickets</div>';
      return;
    }

    data.forEach(t => {
      const card = document.createElement('div');
      card.style.cssText = `
        padding:16px; background:linear-gradient(135deg, rgba(139,92,246,0.1), rgba(59,130,246,0.1));
        border:1px solid rgba(255,255,255,.08); border-radius:12px; transition:all 0.2s;
        display:flex; flex-direction:column; gap:12px;
      `;
      card.onmouseover = () => card.style.background = 'linear-gradient(135deg, rgba(139,92,246,0.15), rgba(59,130,246,0.15))';
      card.onmouseout = () => card.style.background = 'linear-gradient(135deg, rgba(139,92,246,0.1), rgba(59,130,246,0.1))';

      card.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:start; gap:8px;">
          <div style="flex:1;">
            <div style="font-weight:600; color:rgba(255,255,255,0.95); font-size:14px;">${t.subject}</div>
            <div style="font-size:12px; color:rgba(255,255,255,0.6); margin-top:4px;">${t.user_email}</div>
          </div>
          <span style="display:inline-block; background:rgba(139,92,246,0.2); color:#d8b4fe; padding:2px 8px; border-radius:4px; font-size:10px;">#${t.ticket_id}</span>
        </div>
        <div style="background:rgba(255,255,255,.02); padding:12px; border-radius:8px; font-size:12px; color:rgba(255,255,255,0.7);">
          ${t.subject}
        </div>
        <button onclick="reply('${t.ticket_id}')" style="padding:8px 12px; background:rgba(59,130,246,0.3); border:1px solid rgba(59,130,246,0.5); color:#93c5fd; border-radius:6px; cursor:pointer; font-size:12px; font-weight:500;">↪ Reply</button>
      `;
      grid.appendChild(card);
    });
  } catch (e) {
    grid.innerHTML = `<div style="grid-column:1/-1; padding:20px; text-align:center; color:#fca5a5;">Error: ${e.message}</div>`;
    console.error('Failed to load tickets:', e);
  }
}

function reply(ticket_id) {
  const msg = prompt('Reply message:');
  if (!msg) return;
  fetch(`/api/admin/support/ticket/${ticket_id}/reply`, {
    method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({message: msg})
  }).then(r=>r.json()).then(d=>{
    if (d.success) alert('Replied'); else alert('Error');
  });
}

function formatDate(value) {
  if (!value) return '-';
  try {
    return new Date(value).toLocaleString('en-US', { dateStyle:'medium', timeStyle:'short' });
  } catch (e) {
    return value;
  }
}

async function loadUsers() {
  const grid = document.getElementById('userCardsGrid');
  grid.innerHTML = '<div style="grid-column:1/-1; padding:20px; text-align:center; color:rgba(255,255,255,0.6);">Loading users…</div>';

  try {
    const res = await fetch('/api/admin/users');
    if (!res.ok) {
      const err = await res.text();
      throw new Error(err || 'Failed to fetch user list.');
    }
    const users = await res.json();
    grid.innerHTML = '';

    if (!users.length) {
      grid.innerHTML = '<div style="grid-column:1/-1; padding:20px; text-align:center; color:rgba(255,255,255,0.6);">No users found.</div>';
      return;
    }

    users.forEach(u => {
      const activeLabel = u.is_active ? 'Active' : 'Inactive';
      const roleBadge = u.role === 'admin' ? '<span style="display:inline-block; background:rgba(250,204,21,0.2); color:#facccc; padding:2px 8px; border-radius:4px; font-size:12px; margin-left:8px;">ADMIN</span>' : '';
      const card = document.createElement('div');
      card.style.cssText = `
        padding:16px; background:linear-gradient(135deg, rgba(59,130,246,0.1), rgba(139,92,246,0.1));
        border:1px solid rgba(255,255,255,.08); border-radius:12px; cursor:pointer; transition:all 0.2s;
        display:flex; flex-direction:column; gap:12px;
      `;
      card.onmouseover = () => card.style.background = 'linear-gradient(135deg, rgba(59,130,246,0.15), rgba(139,92,246,0.15))';
      card.onmouseout = () => card.style.background = 'linear-gradient(135deg, rgba(59,130,246,0.1), rgba(139,92,246,0.1))';
      card.onclick = () => viewUserDetail(u.id);

      card.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:start; gap:8px;">
          <div>
            <div style="font-weight:600; color:rgba(255,255,255,0.95);">${u.email}</div>
            <div style="font-size:12px; color:rgba(255,255,255,0.6); margin-top:4px;">${u.full_name || 'N/A'}${roleBadge}</div>
          </div>
          <div style="text-align:right;">
            <span style="display:inline-block; background:${u.is_active ? 'rgba(34,197,94,0.2)' : 'rgba(239,68,68,0.2)'}; color:${u.is_active ? '#86efac' : '#fca5a5'}; padding:2px 6px; border-radius:3px; font-size:11px;">${activeLabel}</span>
          </div>
        </div>
        <div style="font-size:12px; color:rgba(255,255,255,0.5);">Joined: ${formatDate(u.created_at)}</div>
      `;
      grid.appendChild(card);
    });
  } catch (e) {
    grid.innerHTML = `<div style="grid-column:1/-1; padding:20px; text-align:center; color:#fca5a5;">Error: ${e.message}</div>`;
    console.error('Admin user list load failed:', e);
  }
}

async function viewUserDetail(userId) {
  try {
    const res = await fetch(`/api/admin/user/${userId}/activity`);
    const data = await res.json();
    if (data.error) {
      return alert('Unable to load user details: ' + data.error);
    }

    const panel = document.getElementById('userDetailPanel');
    const content = document.getElementById('userDetailContent');

    const transactionsHTML = data.transactions && data.transactions.length > 0
      ? data.transactions.map(tx => `<tr><td>${tx.type}</td><td>${tx.symbol}</td><td>${tx.amount}</td><td>${tx.status}</td><td>${formatDate(tx.created_at)}</td></tr>`).join('')
      : '<tr><td colspan="5" style="text-align:center; color:rgba(255,255,255,0.5);">No transactions</td></tr>';

    const tradesHTML = data.trades && data.trades.length > 0
      ? data.trades.map(t => `<tr><td>${t.pair}</td><td>${t.side}</td><td>${t.amount}</td><td>${t.entry_price || '-'}</td><td>${t.status}</td><td>${formatDate(t.opened_at)}</td></tr>`).join('')
      : '<tr><td colspan="6" style="text-align:center; color:rgba(255,255,255,0.5);">No trades</td></tr>';

    content.innerHTML = `
      <h3 style="margin-top:0; display:flex; justify-content:space-between; align-items:center;">
        <span>${data.user.full_name || data.user.email}</span>
        <span style="font-size:12px; font-weight:400; color:rgba(255,255,255,0.5);">ID: ${data.user.id}</span>
      </h3>

      <!-- User Info Card -->
      <div style="background:rgba(255,255,255,.02); padding:12px; border-radius:8px; margin-bottom:16px;">
        <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; font-size:13px;">
          <div><strong>Email:</strong> ${data.user.email}</div>
          <div><strong>Role:</strong> ${data.user.role.toUpperCase()}</div>
          <div><strong>Status:</strong> <span style="color:${data.user.is_active ? '#86efac' : '#fca5a5'};">${data.user.is_active ? '● Active' : '● Inactive'}</span></div>
          <div><strong>Joined:</strong> ${formatDate(data.user.created_at)}</div>
        </div>
        <div style="margin-top:8px; padding-top:8px; border-top:1px solid rgba(255,255,255,.08);">
          <button onclick="toggleUserActive(${data.user.id})" style="padding:6px 12px; margin-right:8px; background:rgba(${data.user.is_active ? '239,68,68' : '34,197,94'},0.2); border:1px solid rgba(${data.user.is_active ? '239,68,68' : '34,197,94'},0.5); color:${data.user.is_active ? '#fca5a5' : '#86efac'}; border-radius:6px; cursor:pointer; font-size:12px;">
            ${data.user.is_active ? 'Deactivate' : 'Activate'}
          </button>
        </div>
      </div>

      <!-- Tabs -->
      <div style="display:flex; gap:12px; border-bottom:1px solid rgba(255,255,255,.08); margin-bottom:16px;">
        <button onclick="switchUserTab('history')" id="tabHistory" style="padding:8px 12px; background:transparent; border:none; border-bottom:2px solid #3b82f6; color:#3b82f6; cursor:pointer; font-size:13px;">History</button>
        <button onclick="switchUserTab('wallet')" id="tabWallet" style="padding:8px 12px; background:transparent; border:none; border-bottom:2px solid transparent; color:rgba(255,255,255,0.6); cursor:pointer; font-size:13px;">Wallet Activity</button>
        <button onclick="switchUserTab('transactions')" id="tabTransactions" style="padding:8px 12px; background:transparent; border:none; border-bottom:2px solid transparent; color:rgba(255,255,255,0.6); cursor:pointer; font-size:13px;">Transactions</button>
        <button onclick="switchUserTab('trades')" id="tabTrades" style="padding:8px 12px; background:transparent; border:none; border-bottom:2px solid transparent; color:rgba(255,255,255,0.6); cursor:pointer; font-size:13px;">Trades</button>
        <button onclick="switchUserTab('security')" id="tabSecurity" style="padding:8px 12px; background:transparent; border:none; border-bottom:2px solid transparent; color:rgba(255,255,255,0.6); cursor:pointer; font-size:13px;">Security</button>
      </div>

      <!-- Tab Content -->
      <div id="tabHistoryContent" style="display:block;">
        <div style="background:rgba(255,255,255,.02); padding:12px; border-radius:8px; font-size:13px; color:rgba(255,255,255,0.6);">
          Account created ${formatDate(data.user.created_at)}
        </div>
      </div>

      <div id="tabWalletContent" style="display:none;">
        <p style="color:rgba(255,255,255,0.5); font-size:13px;">Wallet details would appear here</p>
      </div>

      <div id="tabTransactionsContent" style="display:none;">
        <table style="width:100%; font-size:12px; border-collapse:collapse;">
          <thead style="border-bottom:1px solid rgba(255,255,255,.08);">
            <tr>
              <th style="text-align:left; padding:6px; color:rgba(255,255,255,0.6);">Type</th>
              <th style="text-align:left; padding:6px; color:rgba(255,255,255,0.6);">Symbol</th>
              <th style="text-align:left; padding:6px; color:rgba(255,255,255,0.6);">Amount</th>
              <th style="text-align:left; padding:6px; color:rgba(255,255,255,0.6);">Status</th>
              <th style="text-align:left; padding:6px; color:rgba(255,255,255,0.6);">Date</th>
            </tr>
          </thead>
          <tbody>${transactionsHTML}</tbody>
        </table>
      </div>

      <div id="tabTradesContent" style="display:none;">
        <table style="width:100%; font-size:12px; border-collapse:collapse;">
          <thead style="border-bottom:1px solid rgba(255,255,255,.08);">
            <tr>
              <th style="text-align:left; padding:6px; color:rgba(255,255,255,0.6);">Pair</th>
              <th style="text-align:left; padding:6px; color:rgba(255,255,255,0.6);">Side</th>
              <th style="text-align:left; padding:6px; color:rgba(255,255,255,0.6);">Amount</th>
              <th style="text-align:left; padding:6px; color:rgba(255,255,255,0.6);">Price</th>
              <th style="text-align:left; padding:6px; color:rgba(255,255,255,0.6);">Status</th>
              <th style="text-align:left; padding:6px; color:rgba(255,255,255,0.6);">Opened</th>
            </tr>
          </thead>
          <tbody>${tradesHTML}</tbody>
        </table>
      </div>

      <div id="tabSecurityContent" style="display:none;">
        <div style="background:rgba(255,255,255,.02); padding:12px; border-radius:8px; font-size:13px;">
          <p><strong>2FA Status:</strong> <span style="color:rgba(255,255,255,0.5);">Not enabled</span></p>
          <p><strong>Last Login:</strong> <span style="color:rgba(255,255,255,0.5);">Unknown</span></p>
          <p style="margin-top:12px; padding-top:12px; border-top:1px solid rgba(255,255,255,.08);">
            <button style="padding:6px 12px; background:rgba(239,68,68,0.2); border:1px solid rgba(239,68,68,0.5); color:#fca5a5; border-radius:6px; cursor:pointer; font-size:12px;">Force Password Reset</button>
          </p>
        </div>
      </div>
    `;

    panel.style.display = 'block';
  } catch (e) {
    alert('Unable to load user details.');
    console.error(e);
  }
}

function switchUserTab(tab) {
  document.querySelectorAll('[id^="tab"]:not([id^="tabHistory"]):not([id^="tabWallet"]):not([id^="tabTransactions"]):not([id^="tabTrades"]):not([id^="tabSecurity"])').forEach(el => el.style.display = 'none');
  document.querySelectorAll('[id$="Content"]').forEach(el => el.style.display = 'none');
  document.querySelectorAll('[id^="tab"]:not([id$="Content"])').forEach(btn => btn.style.borderBottomColor = 'transparent');
  document.querySelectorAll('[id^="tab"]:not([id$="Content"])').forEach(btn => btn.style.color = 'rgba(255,255,255,0.6)');

  const tabBtn = document.getElementById('tab' + tab.charAt(0).toUpperCase() + tab.slice(1));
  const tabContent = document.getElementById('tab' + tab.charAt(0).toUpperCase() + tab.slice(1) + 'Content');

  if (tabBtn) {
    tabBtn.style.borderBottomColor = '#3b82f6';
    tabBtn.style.color = '#3b82f6';
  }
  if (tabContent) {
    tabContent.style.display = 'block';
  }
}

function closeUserDetail() {
  document.getElementById('userDetailPanel').style.display = 'none';
}



async function toggleUserActive(userId) {
  try {
    const res = await fetch(`/api/admin/user/${userId}/toggle-active`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
    });
    const data = await res.json();
    if (data.error) {
      return alert('Error: ' + data.error);
    }
    await loadUsers();
    alert('User status updated.');
  } catch (e) {
    alert('Failed to update user status.');
    console.error(e);
  }
}

function initAdminDashboard() {
  fetchPending();
  loadHashes();
  loadUsers();
  loadTickets();
  loadComingSoonState();
  setInterval(fetchPending, 15000);
  const btn = document.getElementById('comingSoonBtn');
  if (btn) btn.addEventListener('click', toggleComingSoon);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initAdminDashboard);
} else {
  initAdminDashboard();
}

async function loadComingSoonState(){
  try{
    const res = await fetch('/admin/coming-soon/state');
    if (!res.ok) return;
    const j = await res.json();
    const el = document.getElementById('comingSoonStatus');
    if (el) el.textContent = j.coming_soon ? 'ON' : 'OFF';
  }catch(e){console.warn('coming soon state load failed', e)}
}

async function toggleComingSoon(){
  try{
    const res = await fetch('/admin/coming-soon/toggle', {method:'POST'});
    const j = await res.json();
    if (j.error) return alert('Error: '+j.error);
    const el = document.getElementById('comingSoonStatus');
    if (el) el.textContent = j.coming_soon ? 'ON' : 'OFF';
  }catch(e){alert('Toggle failed: '+e)}
}
