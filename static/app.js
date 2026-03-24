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

for (const btn of document.querySelectorAll('.admin-restore-blacklist-btn')) {
  btn.addEventListener('click', async () => {
    const owner = btn.dataset.owner;
    const nodeId = btn.dataset.nodeId;
    if (!owner || !nodeId) return;
    if (!confirm(`确认恢复用户 ${owner} 的节点 ${nodeId} 吗？`)) return;
    await api(`/api/admin/blacklist/${encodeURIComponent(owner)}/${encodeURIComponent(nodeId)}/restore`, { method: 'POST' });
    location.reload();
  });
}

const adminDefaultSubIntervalForm = document.getElementById('admin-default-sub-interval-form');
if (adminDefaultSubIntervalForm) {
  adminDefaultSubIntervalForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const value = document.getElementById('default-sub-interval').value.trim();
    const interval = parseInt(value, 10);
    if (!interval || interval < 1) {
      alert('请输入有效的分钟数');
      return;
    }
    const body = new URLSearchParams({ interval_min: interval.toString() });
    const res = await api('/api/admin/settings/default-sub-interval', { method: 'POST', body });
    const el = document.getElementById('admin-default-sub-interval-result');
    if (el) el.textContent = `已保存，当前默认间隔：${res.default_sub_interval_min} 分钟`;
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

// Subscriptions page
const subCreateForm = document.getElementById('sub-create-form');
if (subCreateForm) {
  subCreateForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = document.getElementById('sub-name').value.trim();
    const url = document.getElementById('sub-url').value.trim();
    const type = document.getElementById('sub-type').value;
    const enabled = document.getElementById('sub-enabled').value;
    const interval = document.getElementById('sub-interval').value.trim();
    if (!name || !url) return;
    const body = new URLSearchParams({ name, url, type, enabled });
    if (interval) body.append('interval_min', interval);
    try {
      await api('/api/subscriptions', { method: 'POST', body });
      location.reload();
    } catch (err) {
      const el = document.getElementById('sub-create-result');
      if (el) el.textContent = err.message;
    }
  });
}

for (const btn of document.querySelectorAll('.sub-edit-btn')) {
  btn.addEventListener('click', async () => {
    const id = btn.dataset.id;
    const row = btn.closest('tr');
    if (!row) return;
    const name = prompt('订阅名称：', row.dataset.name || '');
    if (!name) return;
    const url = prompt('订阅地址：', row.dataset.url || '');
    if (!url) return;
    const type = prompt('订阅类型（auto/clash/raw/base64）：', row.dataset.type || 'auto');
    if (!type) return;
    const interval = prompt('拉取间隔（分钟，空为默认）：', row.dataset.interval || '');
    const body = new URLSearchParams({ name, url, type });
    if (interval !== null && interval !== '') body.append('interval_min', interval);
    await api(`/api/subscriptions/${id}`, { method: 'PUT', body });
    location.reload();
  });
}

for (const btn of document.querySelectorAll('.sub-toggle-btn')) {
  btn.addEventListener('click', async () => {
    const id = btn.dataset.id;
    const enabled = btn.dataset.enabled === '1' ? '0' : '1';
    const body = new URLSearchParams({ enabled });
    await api(`/api/subscriptions/${id}`, { method: 'PUT', body });
    location.reload();
  });
}

for (const btn of document.querySelectorAll('.sub-pull-btn')) {
  btn.addEventListener('click', async () => {
    const id = btn.dataset.id;
    btn.disabled = true;
    try {
      await api(`/api/subscriptions/${id}/pull`, { method: 'POST' });
      location.reload();
    } catch (err) {
      alert(`拉取失败: ${err.message}`);
      btn.disabled = false;
    }
  });
}

for (const btn of document.querySelectorAll('.sub-del-btn')) {
  btn.addEventListener('click', async () => {
    const id = btn.dataset.id;
    if (!confirm('确定删除这个订阅吗？')) return;
    await api(`/api/subscriptions/${id}`, { method: 'DELETE' });
    location.reload();
  });
}

const subBlacklistForm = document.getElementById('sub-blacklist-form');
if (subBlacklistForm) {
  subBlacklistForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const enabled = document.getElementById('sub-auto-blacklist-enabled').checked ? 1 : 0;
    const body = new URLSearchParams({ enabled: enabled.toString() });
    const res = await api('/api/subscriptions/auto-blacklist', { method: 'POST', body });
    const el = document.getElementById('sub-blacklist-result');
    if (el) {
      el.textContent = res.enabled === 1 ? '自动拉黑已开启' : '自动拉黑已关闭';
    }
  });
}

// Exports page
let exportRulesDraft = [];
function renderExportRuleDraft() {
  const box = document.getElementById('rule-list');
  if (!box) return;
  if (!exportRulesDraft.length) {
    box.innerHTML = '<div class="hint">当前无规则，默认导出全部可用节点</div>';
    const hidden = document.getElementById('export-rules-json');
    if (hidden) hidden.value = '[]';
    return;
  }
  box.innerHTML = exportRulesDraft
    .map((r, idx) => `<div class="rule-item"><span class="mono">${r.type} ${r.op} ${r.value}</span><button data-rule-idx="${idx}" class="rule-del-btn">删除</button></div>`)
    .join('');
  const hidden = document.getElementById('export-rules-json');
  if (hidden) hidden.value = JSON.stringify(exportRulesDraft);
  for (const btn of box.querySelectorAll('.rule-del-btn')) {
    btn.addEventListener('click', () => {
      const i = parseInt(btn.dataset.ruleIdx || '-1', 10);
      if (i >= 0) {
        exportRulesDraft.splice(i, 1);
        renderExportRuleDraft();
      }
    });
  }
}

const ruleAddBtn = document.getElementById('rule-add-btn');
if (ruleAddBtn) {
  ruleAddBtn.addEventListener('click', (e) => {
    e.preventDefault();
    const type = document.getElementById('rule-type').value;
    const op = document.getElementById('rule-op').value;
    const valueRaw = document.getElementById('rule-value').value.trim();
    const value = Number(valueRaw);
    if (!valueRaw || Number.isNaN(value)) {
      alert('请输入有效数值');
      return;
    }
    exportRulesDraft.push({ type, op, value });
    document.getElementById('rule-value').value = '';
    renderExportRuleDraft();
  });
}

const exportCreateForm = document.getElementById('export-create-form');
if (exportCreateForm) {
  renderExportRuleDraft();
  exportCreateForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const name = document.getElementById('export-name').value.trim();
    const format = document.getElementById('export-format').value;
    const enabled = document.getElementById('export-enabled').value;
    const rulesJson = document.getElementById('export-rules-json').value.trim() || '[]';
    if (!name) return;
    const body = new URLSearchParams({ name, format, enabled, rules_json: rulesJson });
    try {
      await api('/api/export-rules', { method: 'POST', body });
      location.reload();
    } catch (err) {
      const el = document.getElementById('export-create-result');
      if (el) el.textContent = err.message;
    }
  });
}

for (const btn of document.querySelectorAll('.export-copy-btn')) {
  btn.addEventListener('click', async () => {
    const url = btn.dataset.url || '';
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      btn.textContent = '已复制';
      setTimeout(() => { btn.textContent = '复制链接'; }, 1200);
    } catch (_) {
      prompt('复制失败，请手动复制：', url);
    }
  });
}

for (const btn of document.querySelectorAll('.export-edit-btn')) {
  btn.addEventListener('click', async () => {
    const id = btn.dataset.id;
    const row = btn.closest('tr');
    if (!id || !row) return;
    const name = prompt('规则名称：', row.dataset.name || '');
    if (!name) return;
    const format = prompt('输出格式（clash/v2ray/base64/singbox）：', row.dataset.format || 'clash');
    if (!format) return;
    const enabled = prompt('启用状态（1启用/0禁用）：', row.dataset.enabled || '1');
    if (!enabled) return;
    const rulesJson = prompt('规则JSON：', row.dataset.rulesJson || '[]');
    if (rulesJson === null) return;
    const body = new URLSearchParams({ name, format, enabled, rules_json: rulesJson });
    await api(`/api/export-rules/${id}`, { method: 'PUT', body });
    location.reload();
  });
}

for (const btn of document.querySelectorAll('.export-del-btn')) {
  btn.addEventListener('click', async () => {
    const id = btn.dataset.id;
    if (!id) return;
    if (!confirm('确定删除这个导出规则吗？')) return;
    await api(`/api/export-rules/${id}`, { method: 'DELETE' });
    location.reload();
  });
}

setInterval(refreshStatus, 4000);
setInterval(refreshNodeStatuses, 4000);
