// =============================================================================
// main.js — エントリーポイント・グローバル状態
//   グローバル変数: pppoeClients, mapeClients, isFetching, currentFilter
//   loadData()    : /api/clients をポーリングしてテーブルを更新
//   showToast()   : トースト通知
//   applyFilter() : フィルター入力連動
//   openWindow()  : ポップアップウィンドウ
//   初期化 / setInterval ポーリング
// =============================================================================
let pppoeClients = [];
let mapeClients = [];
let pppoeServers = [];   // PPPoE サーバーメタデータ (vrf_enabled を含む)
let isFetching = false;
let currentFilter = '';

function openWindow(url, title, width, height) {
  const left = (screen.width - width) / 2;
  const top = (screen.height - height) / 2;
  window.open(url, title, `width=${width},height=${height},top=${top},left=${left},resizable=yes,scrollbars=yes,status=no`);
}

function showToast(message, isSuccess = true) {
  const container = document.getElementById('toastContainer');
  const toast = document.createElement('div');
  toast.className = `toast ${isSuccess ? 'toast-success' : 'toast-error'}`;
  toast.innerHTML = `<span>${isSuccess ? '✓' : '⚠️'}</span> <span>${message}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(10px)';
    toast.style.transition = 'all 0.3s';
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

function applyFilter() {
  currentFilter = document.getElementById('filterInput').value.toLowerCase().trim();
  renderTables();
}

async function loadData(manual = false) {
  if (isFetching && !manual) return;
  isFetching = true;
  try {
    const res = await fetch('/api/clients');
    if (!res.ok) throw new Error('API response failed');
    const data = await res.json();

    pppoeClients = data.pppoe_clients || [];
    mapeClients = data.mape_clients || [];
    pppoeServers = data.pppoe_servers || [];

    document.getElementById('val-pppoe').textContent = data.summary?.pppoe_count ?? 0;
    document.getElementById('val-mape').textContent = data.summary?.mape_count ?? 0;
    document.getElementById('val-total').textContent = data.summary?.total_count ?? 0;

    // Render Timestamp (2 lines for saving width: YYYY-MM-DD and HH:MM:SS)
    if (data.timestamp && data.timestamp.includes(' ')) {
      const parts = data.timestamp.split(' ');
      const dEl = document.getElementById('val-time-date');
      const tEl = document.getElementById('val-time-clock');
      if (dEl) dEl.textContent = parts[0];
      if (tEl) tEl.textContent = parts[1];
    } else {
      const dEl = document.getElementById('val-time-date');
      if (dEl) dEl.textContent = data.timestamp ?? '-';
    }
    const valTime = document.getElementById('val-time');
    if (valTime) valTime.textContent = data.timestamp ?? '-';

    document.getElementById('badge-pppoe').textContent = pppoeClients.length;
    document.getElementById('badge-mape').textContent = mapeClients.length;

    // Render PPPoE Server Info Banner
    if (data.pppoe_servers && data.pppoe_servers.length > 0) {
      const banner = document.getElementById('pppoeServerInfoBanner');
      banner.innerHTML = data.pppoe_servers.map(s => {
        const srv = s.service_name || s.ac_name;
        const vrfClass = s.vrf_enabled ? ' server-ac-badge--vrf' : '';
        return `
            <div class="server-info-item">
              <span class="server-ac-badge${vrfClass}" style="${getServiceBadgeStyle(srv)}">${srv}</span>
              ${s.vrf_enabled ? `<span class="server-detail" style="color:var(--accent-yellow); font-size:11px; font-weight:600;">🔒 VRF閉域</span>` : ''}
            </div>
          `;
      }).join('');
    }

    // Render MAP-E BR Info Banner
    if (data.br_info) {
      if (data.br_info.br_ipv6) document.getElementById('brIpv6Display').textContent = data.br_info.br_ipv6;
      if (data.br_info.br_ipv4) document.getElementById('brIpv4Display').textContent = data.br_info.br_ipv4;
    }

    // Render MAP-E IPoE VLAN Info Banner
    const mapeVlanContainer = document.getElementById('mapeVlanInfoItems');
    if (mapeVlanContainer && data.ipoe_vlans) {
      const modeConfigs = [
        { key: 'slaac_dyn', label: 'SLAAC 動的', l1: 'SLAAC', l2: '動的' },
        { key: 'pd_dyn', label: 'DHCP-PD 動的', l1: 'DHCP-PD', l2: '動的' },
        { key: 'slaac_fix', label: 'SLAAC 固定', l1: 'SLAAC', l2: '固定' },
        { key: 'pd_fix', label: 'DHCP-PD 固定', l1: 'DHCP-PD', l2: '固定' }
      ];
      mapeVlanContainer.innerHTML = modeConfigs.map(m => {
        const vList = data.ipoe_vlans[m.key] || [];
        const vlanDisplay = vList.length > 0 ? vList.join(', ') : '(なし)';
        const bStyle = typeof getServiceBadgeStyle === 'function' ? getServiceBadgeStyle(m.label) : '';
        return `
            <div class="server-info-item">
              <span class="server-ac-badge server-ac-badge--2tier" style="${bStyle}">
                <span class="badge-tier-main">${m.l1}</span>
                <span class="badge-tier-sub">${m.l2}</span>
              </span>
              <span class="server-detail">VLAN: <strong style="color:var(--accent-cyan);">${vlanDisplay}</strong></span>
            </div>
          `;
      }).join('');
    }

    // Render Container Statuses
    if (data.containers) {
      renderContainerStatuses(data.containers);
    }

    renderTables();
  } catch (err) {
    console.error('Failed to load data:', err);
  } finally {
    isFetching = false;
  }
}

function renderContainerStatuses(c) {
  if (!c) return;

  function setDot(cellId, info) {
    const el = document.getElementById(cellId);
    if (!el) return;
    if (!info) {
      el.innerHTML = '<span class="status-dot dot-down" title="未検出"></span>';
      return;
    }
    const lvl = info.level || (info.state === 'running' ? 'ok' : 'down');
    const title = `${info.name || cellId}: ${info.status || info.state}`;
    el.innerHTML = `<span class="status-dot dot-${lvl}" title="${title.replace(/"/g, '&quot;')}"></span>`;
  }

  setDot('cs-dot-radvd', c.radvd);
  setDot('cs-dot-bind9', c.bind9);
  setDot('cs-dot-kea', c.kea_dhcp6);
  setDot('cs-dot-axosyslog', c.axosyslog);
  setDot('cs-dot-chrony', c.chrony);
  setDot('cs-dot-prov', c.provisioning_server);

  const pppoeCell = document.getElementById('cs-dot-pppoe');
  if (pppoeCell) {
    if (Array.isArray(c.pppoe) && c.pppoe.length > 0) {
      pppoeCell.innerHTML = c.pppoe.map(item => {
        const lvl = item.level || (item.state === 'running' ? 'ok' : 'down');
        const title = `${item.name}: ${item.status || item.state}`;
        return `<span class="status-dot dot-${lvl}" title="${title.replace(/"/g, '&quot;')}"></span>`;
      }).join('');
    } else {
      pppoeCell.innerHTML = '<span class="status-dot dot-down" title="pppoe: 停止中"></span>';
    }
  }
}

// =============================================================================
// Initialize
// =============================================================================
// Initialize
loadData();
// 初回設定ステータス取得
fetch('/api/config')
  .then(r => r.json())
  .then(d => {
    if (d && d.has_pending_changes) {
      const dot = document.getElementById('headerConfigDot');
      if (dot) dot.style.display = 'inline-block';
    }
  })
  .catch(e => console.error(e));
setInterval(() => loadData(), 5000);
