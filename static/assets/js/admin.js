async function fetchPending() {
  const res = await fetch('/api/admin/pending-transactions');
  const data = await res.json();
  const tbody = document.querySelector('#pendingTxTable tbody');
  tbody.innerHTML = '';
  data.forEach(tx => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${tx.tx_id}</td>
      <td>${tx.user_email}</td>
      <td>${tx.type}</td>
      <td>${tx.symbol}</td>
      <td>${tx.amount}</td>
      <td>${tx.to_address || ''}</td>
      <td>
        <button onclick="adminAction(${tx.id}, 'approve')">Approve</button>
        <button onclick="adminAction(${tx.id}, 'reject')">Reject</button>
      </td>
    `;
    tbody.appendChild(tr);
  });
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
  const res = await fetch('/api/admin/get-hashes');
  const data = await res.json();
  const container = document.getElementById('hashList');
  container.innerHTML = '';
  data.forEach(h => {
    const div = document.createElement('div');
    div.innerHTML = `<strong>${h.symbol}</strong>: ${h.address} <em>${h.note||''}</em>`;
    container.appendChild(div);
  });
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
  const res = await fetch('/api/admin/support/tickets');
  const data = await res.json();
  const container = document.getElementById('ticketList');
  container.innerHTML = '';
  data.forEach(t => {
    const div = document.createElement('div');
    div.innerHTML = `<strong>${t.ticket_id}</strong> ${t.user_email} — ${t.subject} <button onclick="reply('${t.ticket_id}')">Reply</button>`;
    container.appendChild(div);
  });
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
  const tbody = document.querySelector('#adminUsersTable tbody');
  tbody.innerHTML = '<tr><td colspan="5" class="table-loading">Loading users…</td></tr>';

  try {
    const res = await fetch('/api/admin/users');
    if (!res.ok) {
      const err = await res.text();
      throw new Error(err || 'Failed to fetch user list.');
    }
    const users = await res.json();
    tbody.innerHTML = '';

    if (!users.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="table-loading">No users found.</td></tr>';
      return;
    }

    users.forEach(u => {
      const tr = document.createElement('tr');
      const activeLabel = u.is_active ? 'Active' : 'Inactive';
      const toggleLabel = u.is_active ? 'Deactivate' : 'Activate';
      const disableActions = u.role === 'admin' ? 'disabled' : '';
      tr.className = 'clickable-row';
      tr.style.cursor = 'pointer';
      tr.onclick = () => viewUserActivity(u.id);
      tr.innerHTML = `
        <td>${u.email}</td>
        <td>${u.role}</td>
        <td>${activeLabel}</td>
        <td>${formatDate(u.created_at)}</td>
        <td>
          <button onclick="event.stopPropagation(); viewUserActivity(${u.id})">Activity</button>
          <button onclick="event.stopPropagation(); toggleUserActive(${u.id})" ${disableActions}>${toggleLabel}</button>
        </td>
      `;
      tbody.appendChild(tr);
    });
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="5" class="table-loading">Unable to load users: ${e.message}</td></tr>`;
    console.error('Admin user list load failed:', e);
  }
}

async function viewUserActivity(userId) {
  try {
    const res = await fetch(`/api/admin/user/${userId}/activity`);
    const data = await res.json();
    if (data.error) {
      return alert('Unable to load activity: ' + data.error);
    }

    document.getElementById('userActivitySection').style.display = 'block';
    document.getElementById('userActivityInfo').innerHTML = `
      <p><strong>${data.user.full_name || data.user.email}</strong> &mdash; ${data.user.role.toUpperCase()}</p>
      <p>Status: <strong>${data.user.is_active ? 'Active' : 'Inactive'}</strong></p>
      <p>Joined: ${formatDate(data.user.created_at)}</p>
    `;

    document.getElementById('userTransactions').innerHTML = `
      <h4>Transactions</h4>
      <table class="table">
        <thead><tr><th>Type</th><th>Symbol</th><th>Amount</th><th>Status</th><th>When</th></tr></thead>
        <tbody>${data.transactions.map(tx => `
          <tr>
            <td>${tx.type}</td>
            <td>${tx.symbol}</td>
            <td>${tx.amount}</td>
            <td>${tx.status}</td>
            <td>${formatDate(tx.created_at)}</td>
          </tr>
        `).join('')}</tbody>
      </table>
    `;

    document.getElementById('userTrades').innerHTML = `
      <h4>Trades</h4>
      <table class="table">
        <thead><tr><th>Pair</th><th>Side</th><th>Amount</th><th>Price</th><th>Status</th><th>Opened</th></tr></thead>
        <tbody>${data.trades.map(t => `
          <tr>
            <td>${t.pair}</td>
            <td>${t.side}</td>
            <td>${t.amount}</td>
            <td>${t.entry_price || '-'}</td>
            <td>${t.status}</td>
            <td>${formatDate(t.opened_at)}</td>
          </tr>
        `).join('')}</tbody>
      </table>
    `;
  } catch (e) {
    alert('Unable to load user activity.');
    console.error(e);
  }
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
