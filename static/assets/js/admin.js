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

// init
document.addEventListener('DOMContentLoaded', ()=>{
  fetchPending();
  loadHashes();
  loadTickets();
  loadComingSoonState();
  setInterval(fetchPending, 15000);
  // wire toggle button if present
  const btn = document.getElementById('comingSoonBtn');
  if (btn) btn.addEventListener('click', toggleComingSoon);
});

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
