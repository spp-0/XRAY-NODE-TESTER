async function api(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json();
}

async function refreshStatus() {
  try {
    const st = await api('/api/status');
    document.getElementById('running').textContent = st.running ? '是' : '否';
    document.getElementById('progress').textContent = `${st.done}/${st.total}`;
    document.getElementById('started').textContent = st.started_at || '-';
    document.getElementById('finished').textContent = st.finished_at || '-';
    if (window.__lastFinishedAt !== st.finished_at) {
      window.__lastFinishedAt = st.finished_at;
      await refreshNodeStatuses();
    }
  } catch (e) {
    // ignore
  }
}

async function refreshNodeStatuses() {
  try {
    const ids = Array.from(document.querySelectorAll('tr[data-id]')).map((r) => r.dataset.id);
    const query = ids.length ? `?ids=${ids.join(',')}` : '';
    const data = await api(`/api/nodes/status${query}`);
    for (const [id, st] of Object.entries(data)) {
      const row = document.querySelector(`tr[data-id="${id}"]`);
      if (!row) continue;
      const statusCell = row.querySelector('.status-cell');
      const latencyCell = row.querySelector('.latency-cell');
      const checkedCell = row.querySelector('.checked-cell');
      if (statusCell) {
        statusCell.textContent = st.status || '';
        statusCell.classList.remove('status-ok', 'status-fail');
        if (st.status === 'ok') statusCell.classList.add('status-ok');
        if (st.status === 'fail') statusCell.classList.add('status-fail');
      }
      if (latencyCell) latencyCell.textContent = st.latency_ms || '';
      if (checkedCell) checkedCell.textContent = st.checked_at || '';
    }
  } catch (e) {
    // ignore
  }
}

const importForm = document.getElementById('import-form');
if (importForm) {
  importForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const raw = document.getElementById('import-raw').value.trim();
    if (!raw) return;
    const body = new URLSearchParams({ raw });
    const result = await api('/api/import', { method: 'POST', body });
    const el = document.getElementById('import-result');
    if (el) {
      el.textContent = `总计 ${result.total}，新增 ${result.added}，跳过 ${result.skipped}，错误 ${result.errors}`;
    }
    setTimeout(() => location.reload(), 600);
  });
}

const subForm = document.getElementById('sub-form');
if (subForm) {
  subForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const urls = document.getElementById('sub-urls').value.trim();
    if (!urls) return;
    const body = new URLSearchParams({ urls });
    const result = await api('/api/import/subscription', { method: 'POST', body });
    const el = document.getElementById('sub-result');
    if (el) {
      const failed = result.failed_urls && result.failed_urls.length ? `，失败 ${result.failed_urls.length}` : '';
      el.textContent = `订阅 ${result.total_urls} 个，解析 ${result.total_links} 条，新增 ${result.added}，跳过 ${result.skipped}，错误 ${result.errors}${failed}`;
    }
    setTimeout(() => location.reload(), 600);
  });
}

const testUrlForm = document.getElementById('test-url-form');
if (testUrlForm) {
  testUrlForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const testUrl = document.getElementById('test-url').value.trim();
    if (!testUrl) return;
    const body = new URLSearchParams({ test_url: testUrl });
    await api('/api/settings/test-url', { method: 'POST', body });
    alert('检测网址已保存');
  });
}

const autoForm = document.getElementById('auto-check-form');
if (autoForm) {
  autoForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const enabled = document.getElementById('auto-enabled').checked ? 1 : 0;
    const valueRaw = document.getElementById('auto-interval-value').value.trim();
    const unit = document.getElementById('auto-interval-unit').value;
    const value = parseInt(valueRaw, 10);
    if (!value || value < 1) {
      alert('请输入有效的检测间隔');
      return;
    }
    let minutes = value;
    if (unit === 'hour') minutes = value * 60;
    if (unit === 'day') minutes = value * 1440;
    if (unit === 'week') minutes = value * 10080;
    const body = new URLSearchParams({ enabled, interval_min: minutes });
    const res = await api('/api/settings/auto-check', { method: 'POST', body });
    const el = document.getElementById('next-run');
    if (el) el.textContent = res.next_run_at || '-';
    alert('自动检测设置已保存');
  });
}

const pwdForm = document.getElementById('password-form');
if (pwdForm) {
  pwdForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const oldPassword = document.getElementById('old-password').value.trim();
    const newPassword = document.getElementById('new-password').value.trim();
    const confirmPassword = document.getElementById('confirm-password').value.trim();
    if (!oldPassword || !newPassword || !confirmPassword) return;
    const body = new URLSearchParams({
      old_password: oldPassword,
      new_password: newPassword,
      confirm_password: confirmPassword
    });
    try {
      const res = await api('/api/user/password', { method: 'POST', body });
      if (res.relogin) {
        alert('密码已修改，请重新登录');
        window.location.href = '/api/logout';
      }
    } catch (e) {
      const el = document.getElementById('password-result');
      if (el) el.textContent = e.message;
    }
  });
}

const adminCreate = document.getElementById('admin-create-form');
if (adminCreate) {
  adminCreate.addEventListener('submit', async (e) => {
    e.preventDefault();
    const username = document.getElementById('new-username').value.trim();
    const password = document.getElementById('new-password').value.trim();
    const role = document.getElementById('new-role').value;
    const body = new URLSearchParams({ username, password, role });
    try {
      await api('/api/admin/users', { method: 'POST', body });
      const el = document.getElementById('admin-create-result');
      if (el) el.textContent = '创建成功';
      setTimeout(() => location.reload(), 800);
    } catch (e) {
      const el = document.getElementById('admin-create-result');
      if (el) el.textContent = e.message;
    }
  });
}

for (const btn of document.querySelectorAll('.admin-pass-btn')) {
  btn.addEventListener('click', async () => {
    const username = btn.dataset.username;
    const newPassword = prompt(`为 ${username} 设置新密码：`);
    if (!newPassword) return;
    const body = new URLSearchParams({ new_password: newPassword });
    await api(`/api/admin/users/${username}/password`, { method: 'POST', body });
    alert('已重置密码');
  });
}

for (const btn of document.querySelectorAll('.admin-role-btn')) {
  btn.addEventListener('click', async () => {
    const username = btn.dataset.username;
    const role = prompt(`设置 ${username} 角色（admin/user）：`);
    if (!role) return;
    const body = new URLSearchParams({ role });
    await api(`/api/admin/users/${username}/role`, { method: 'POST', body });
    location.reload();
  });
}

for (const btn of document.querySelectorAll('.admin-logout-btn')) {
  btn.addEventListener('click', async () => {
    const username = btn.dataset.username;
    if (!confirm(`确定踢下线用户 ${username} 吗？`)) return;
    await api(`/api/admin/users/${username}/logout`, { method: 'POST' });
    alert('已踢下线');
  });
}

for (const btn of document.querySelectorAll('.admin-delete-btn')) {
  btn.addEventListener('click', async () => {
    const username = btn.dataset.username;
    if (!confirm(`确定删除用户 ${username} 吗？该用户所有节点会被删除。`)) return;
    await api(`/api/admin/users/${username}/delete`, { method: 'POST' });
    location.reload();
  });
}
for (const btn of document.querySelectorAll('.test-btn')) {
  btn.addEventListener('click', async () => {
    const id = btn.dataset.id;
    btn.disabled = true;
    await api(`/api/nodes/${id}/test`, { method: 'POST' });
    location.reload();
  });
}

for (const btn of document.querySelectorAll('.del-btn')) {
  btn.addEventListener('click', async () => {
    const id = btn.dataset.id;
    if (!confirm('确定删除这个节点吗？')) return;
    await api(`/api/nodes/${id}`, { method: 'DELETE' });
    location.reload();
  });
}

for (const btn of document.querySelectorAll('.edit-btn')) {
  btn.addEventListener('click', async () => {
    const id = btn.dataset.id;
    const currentType = btn.dataset.type;
    const currentRaw = btn.dataset.raw;
    const newType = prompt('节点类型（vmess/vless/trojan/ss）：', currentType);
    if (!newType) return;
    const newRaw = prompt('节点链接：', currentRaw);
    if (!newRaw) return;
    const body = new URLSearchParams({ node_type: newType, raw: newRaw });
    await api(`/api/nodes/${id}`, { method: 'PUT', body });
    location.reload();
  });
}

const testAll = document.getElementById('test-all');
if (testAll) {
  testAll.addEventListener('click', async () => {
    await api('/api/test-all', { method: 'POST' });
    await refreshStatus();
  });
}

const testFiltered = document.getElementById('test-filtered');
if (testFiltered) {
  testFiltered.addEventListener('click', async () => {
    const form = document.getElementById('filter-form');
    const formData = new FormData(form);
    const body = new URLSearchParams();
    for (const [k, v] of formData.entries()) {
      body.append(k, v);
    }
    await api('/api/test-filtered', { method: 'POST', body });
    await refreshStatus();
  });
}

const deleteAll = document.getElementById('delete-all');
if (deleteAll) {
  deleteAll.addEventListener('click', async () => {
    if (!confirm('确定删除全部节点吗？此操作不可恢复。')) return;
    await api('/api/nodes', { method: 'DELETE' });
    location.reload();
  });
}

const deleteFiltered = document.getElementById('delete-filtered');
if (deleteFiltered) {
  deleteFiltered.addEventListener('click', async () => {
    const form = document.getElementById('filter-form');
    const formData = new FormData(form);
    const params = new URLSearchParams();
    for (const [k, v] of formData.entries()) params.append(k, v);
    if (!confirm('确定删除筛选结果中的节点吗？此操作不可恢复。')) return;
    const url = `/api/nodes/filtered?${params.toString()}`;
    await api(url, { method: 'DELETE' });
    location.reload();
  });
}

const testStop = document.getElementById('test-stop');
if (testStop) {
  testStop.addEventListener('click', async () => {
    await api('/api/test-stop', { method: 'POST' });
    await refreshStatus();
  });
}
const pageSize = document.getElementById('page-size');
if (pageSize) {
  pageSize.addEventListener('change', () => {
    const form = document.getElementById('filter-form');
    if (form) form.submit();
  });
}

setInterval(refreshStatus, 4000);
setInterval(refreshNodeStatuses, 4000);
