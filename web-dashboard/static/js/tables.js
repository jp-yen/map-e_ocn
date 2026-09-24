// =============================================================================
// tables.js — テーブルレンダリング & ソートロジック
//   renderPPPoETable(), renderMAPETable(), sort handlers, formatDateTime()
//   依存: グローバル変数 pppoeClients, mapeClients, currentFilter (main.js)
//         関数 confirmDisconnectPPPoE, confirmDisconnectMAPE (modals.js)
//         関数 openPPPoEDetail, openMAPEDetail (modals.js)
// =============================================================================
function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function formatDateTime(val) {
  if (!val || val === '-') return '-';
  val = String(val).trim();
  if (/^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}$/.test(val)) return val;
  const mIso = val.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})/);
  if (mIso) return `${mIso[1]} ${mIso[2]}`;
  const monthMap = { jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6, jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12 };
  const mSys = val.match(/^([A-Za-z]{3})\s+(\d{1,2})\s+(\d{2}:\d{2}:\d{2})/);
  if (mSys) {
    const mon = monthMap[mSys[1].toLowerCase()];
    if (mon) {
      const year = new Date().getFullYear();
      const pad = (n) => String(n).padStart(2, '0');
      return `${year}-${pad(mon)}-${pad(mSys[2])} ${mSys[3]}`;
    }
  }
  return val;
}

// =============================================================================
// PPPoE サービス名バッジ用 高コントラストカラー生成 (全12系統・衝突防止)
//   - 白文字(#ffffff)とのコントラスト比 4.6:1〜7.1:1 を厳密に満たす12系統のソリッドカラー
//   - サーバーが6台〜12台に増えても色が重複しないよう線形探査(Collision Avoidance)で別々の色を割り当て
//   - ライト/ダークテーマや各Bearded Themeの背景色に左右されず、常に抜群の可読性を保持
// =============================================================================
const SERVICE_BADGE_PALETTES = [
  { bg: '#0369a1', border: '#0284c7', text: '#ffffff' }, // 1. Deep Sky Blue
  { bg: '#6d28d9', border: '#7c3aed', text: '#ffffff' }, // 2. Vivid Purple
  { bg: '#047857', border: '#059669', text: '#ffffff' }, // 3. Emerald Green
  { bg: '#b45309', border: '#d97706', text: '#ffffff' }, // 4. Dark Amber
  { bg: '#be123c', border: '#e11d48', text: '#ffffff' }, // 5. Crimson Rose
  { bg: '#0f766e', border: '#0d9488', text: '#ffffff' }, // 6. Deep Teal
  { bg: '#4338ca', border: '#4f46e5', text: '#ffffff' }, // 7. Indigo Blue
  { bg: '#a21caf', border: '#c026d3', text: '#ffffff' }, // 8. Rich Magenta
  { bg: '#0e7490', border: '#0891b2', text: '#ffffff' }, // 9. Ocean Cyan
  { bg: '#c2410c', border: '#ea580c', text: '#ffffff' }, // 10. Rust Orange
  { bg: '#3f6212', border: '#4d7c0f', text: '#ffffff' }, // 11. Forest Olive
  { bg: '#334155', border: '#475569', text: '#ffffff' }, // 12. Dark Slate
];

const serviceColorMap = new Map();

// IPoE / MAP-E 各接続方式のカラーパレット固定割り当て (PPPoE パレット準拠・統一配色)
serviceColorMap.set('SLAAC 動的', 0);    // 1. Deep Sky Blue (#0369a1)
serviceColorMap.set('SLAAC (動的)', 0);
serviceColorMap.set('DHCP-PD 動的', 2);  // 3. Emerald Green (#047857)
serviceColorMap.set('DHCP-PD (動的)', 2);
serviceColorMap.set('SLAAC 固定', 1);    // 2. Vivid Purple (#6d28d9)
serviceColorMap.set('SLAAC (固定IP)', 1);
serviceColorMap.set('DHCP-PD 固定', 3);  // 4. Dark Amber (#b45309)
serviceColorMap.set('DHCP-PD (固定IP)', 3);

function getServiceBadgeStyle(serviceName) {
  if (!serviceName || serviceName === '*' || serviceName === '-') return '';
  const name = String(serviceName).trim();

  if (!serviceColorMap.has(name)) {
    let hash = 2166136261;
    for (let i = 0; i < name.length; i++) {
      hash ^= name.charCodeAt(i);
      hash = Math.imul(hash, 16777619);
    }
    let desiredIdx = Math.abs(hash) % SERVICE_BADGE_PALETTES.length;
    const usedIndices = new Set(serviceColorMap.values());
    if (usedIndices.size < SERVICE_BADGE_PALETTES.length) {
      while (usedIndices.has(desiredIdx)) {
        desiredIdx = (desiredIdx + 1) % SERVICE_BADGE_PALETTES.length;
      }
    }
    serviceColorMap.set(name, desiredIdx);
  }

  const p = SERVICE_BADGE_PALETTES[serviceColorMap.get(name)];
  return `background-color: ${p.bg} !important; color: ${p.text} !important; border: 1px solid ${p.border} !important; font-weight: 700 !important; text-shadow: 0 1px 2px rgba(0,0,0,0.45);`;
}

// PPPoE Table Sorting State
let pppoeSortColumn = 'connect_time';
let pppoeSortAsc = false;
let currentFilteredPPPoE = [];

function sortPPPoE(column) {
  if (pppoeSortColumn === column) {
    pppoeSortAsc = !pppoeSortAsc;
  } else {
    pppoeSortColumn = column;
    pppoeSortAsc = true;
  }
  renderPPPoETable();
}

// MAP-E Table Sorting State
let mapeSortColumn = 'lease_start';
let mapeSortAsc = false;
let currentFilteredMAPE = [];

function sortMAPE(column) {
  if (mapeSortColumn === column) {
    mapeSortAsc = !mapeSortAsc;
  } else {
    mapeSortColumn = column;
    mapeSortAsc = true;
  }
  renderMAPETable();
}

function ipToNumber(ip) {
  if (!ip) return 0;
  const clean = ip.split('/')[0].trim();
  const parts = clean.split('.');
  if (parts.length === 4) {
    return ((+parts[0] << 24) | (+parts[1] << 16) | (+parts[2] << 8) | +parts[3]) >>> 0;
  }
  return 0;
}

function renderTables() {
  renderPPPoETable();
  renderMAPETable();
  const bP = document.getElementById('badge-pppoe');
  if (bP && typeof currentFilteredPPPoE !== 'undefined') bP.textContent = currentFilteredPPPoE.length;
  const bM = document.getElementById('badge-mape');
  if (bM && typeof currentFilteredMAPE !== 'undefined') bM.textContent = currentFilteredMAPE.length;
}

function renderPPPoETable() {
  const tbody = document.getElementById('tbody-pppoe');
  const activeOnly = document.getElementById('activeOnlyToggle')?.checked || false;
  let filtered = pppoeClients.filter(c => {
    if (activeOnly && !c.is_online && c.status !== 'active') return false;
    if (!currentFilter) return true;
    const text = `${c.username} ${c.remote_ip} ${c.remote_ip_cidr || ''} ${c.ac_name || ''} ${c.mac} ${c.note} ${c.interface} ${c.vlan} ${c.ip_mode || ''}`.toLowerCase();
    return text.includes(currentFilter);
  });

  // Update Sort Indicators
  const sortCols = ['status', 'username', 'remote_ip', 'ac_name', 'note', 'connect_time', 'uptime_sec'];
  sortCols.forEach(col => {
    const ind = document.getElementById(`sort-pppoe-${col}`);
    if (ind) {
      if (pppoeSortColumn === col) {
        ind.textContent = pppoeSortAsc ? ' ▲' : ' ▼';
      } else {
        ind.textContent = '';
      }
    }
  });

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="8" class="empty-state">${currentFilter ? '検索条件に一致するセッションはありません' : '接続中の PPPoE セッションはありません'}</td></tr>`;
    currentFilteredPPPoE = [];
    return;
  }

  // Perform Sorting
  filtered.sort((a, b) => {
    let valA = a[pppoeSortColumn] ?? '';
    let valB = b[pppoeSortColumn] ?? '';

    if (pppoeSortColumn === 'uptime_sec') {
      valA = Number(valA) || 0;
      valB = Number(valB) || 0;
    } else if (pppoeSortColumn === 'remote_ip') {
      valA = ipToNumber(a.remote_ip);
      valB = ipToNumber(b.remote_ip);
    } else if (pppoeSortColumn === 'status') {
      valA = (a.status === 'active' ? 2 : 0) + (a.ping_ok ? 1 : 0);
      valB = (b.status === 'active' ? 2 : 0) + (b.ping_ok ? 1 : 0);
    } else {
      valA = String(valA).toLowerCase();
      valB = String(valB).toLowerCase();
    }

    if (valA < valB) return pppoeSortAsc ? -1 : 1;
    if (valA > valB) return pppoeSortAsc ? 1 : -1;
    return 0;
  });

  currentFilteredPPPoE = filtered;

  tbody.innerHTML = filtered.map((c, fIdx) => {
    const pingBadge = c.ping_ok
      ? `<span class="ping-badge ping-ok" style="margin-top:4px;">● ${c.ping_rtt || '応答あり'}</span>`
      : `<span class="ping-badge ping-fail" style="margin-top:4px;">✕ 応答なし</span>`;

    const srvDisplay = (c.service_name || c.ac_name || '').trim();
    // pppoeServers (main.js のグローバル) から VRF 閉域状態を参照
    const srvMeta = (typeof pppoeServers !== 'undefined')
      ? pppoeServers.find(s => (s.service_name || s.ac_name) === srvDisplay)
      : null;
    const isVrf = srvMeta?.vrf_enabled ?? false;
    const vrfClass = isVrf ? ' server-ac-badge--vrf' : '';
    const vrfIcon = isVrf ? ' 🔒' : '';
    const acBadge = (srvDisplay && srvDisplay !== '*')
      ? `<span class="server-ac-badge${vrfClass}" style="${getServiceBadgeStyle(srvDisplay)}">${escapeHtml(srvDisplay)}${vrfIcon}</span>`
      : '<span style="color:var(--text-faint);">-</span>';

    const rawIp = c.remote_ip_cidr || (c.remote_ip ? c.remote_ip + '/32' : '-');
    const displayIp = rawIp.endsWith('/32') ? rawIp.slice(0, -3) : rawIp;
    const noteText = (c.note || '').trim();
    const userDisplay = (c.username || '-').trim();

    return `
          <tr>
            <td class="clickable-status col-status" onclick="openPPPoEDetail(${fIdx})" title="クリックして詳細情報を表示">
              <div style="display:flex; flex-direction:column; gap:3px; align-items:flex-start; white-space:nowrap;">
                <span class="status-badge badge-active"><span class="pulse-dot" style="width:6px;height:6px;"></span> Active</span>
                ${pingBadge}
              </div>
            </td>
            <td><span class="user-tag">${escapeHtml(userDisplay)}</span></td>
            <td class="mono" style="font-weight:600; color:var(--accent-cyan);" ${c.configured_ip ? `title="設定値: ${escapeHtml(c.configured_ip)}"` : ''}>${escapeHtml(displayIp)}</td>
            <td>${acBadge}</td>
            <td>${noteText ? `<span class="note-text" title="${escapeHtml(noteText)}">${escapeHtml(noteText)}</span>` : '<span style="color:var(--text-faint);">-</span>'}</td>
            <td style="color:var(--text-soft); font-size:12.5px;">${formatDateTime(c.connect_time)}</td>
            <td style="font-weight:500; color:var(--accent-emerald);">${escapeHtml(c.uptime || '-')}</td>
            <td>
              <div class="btn-action-group">
                <button class="btn-disconnect" onclick="event.stopPropagation(); confirmDisconnectPPPoE('${escapeHtml(c.interface)}', ${Number(c.pid) || 0}, '${escapeHtml(c.username)}', '${escapeHtml(c.remote_ip)}')">切断</button>
              </div>
            </td>
          </tr>
        `;
  }).join('');
}

function renderMAPETable() {
  const tbody = document.getElementById('tbody-mape');
  const activeOnly = document.getElementById('activeOnlyToggle')?.checked || false;
  let filtered = mapeClients.filter(c => {
    if (activeOnly && !c.is_online && c.status !== 'active') return false;
    if (!currentFilter) return true;
    const text = `${c.prefix} ${c.ipv4} ${c.ce_ipv6} ${c.hostname} ${c.duid} ${c.hwaddr} ${c.vlan || ''} ${c.note || ''} ${c.interface || ''} ${c.mode_display || ''}`.toLowerCase();
    return text.includes(currentFilter);
  });

  // Update Sort Indicators
  const mapeSortCols = ['status', 'vlan', 'mode_display', 'prefix', 'ipv4', 'ce_ipv6', 'note', 'lease_start', 'lease_expire'];
  mapeSortCols.forEach(col => {
    const ind = document.getElementById(`sort-mape-${col}`);
    if (ind) {
      if (mapeSortColumn === col) {
        ind.textContent = mapeSortAsc ? ' ▲' : ' ▼';
      } else {
        ind.textContent = '';
      }
    }
  });

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="10" class="empty-state">${currentFilter ? '検索条件に一致するクライアントはありません' : '接続中の MAP-E クライアントはありません'}</td></tr>`;
    currentFilteredMAPE = [];
    return;
  }

  // Perform Sorting
  filtered.sort((a, b) => {
    let valA = a[mapeSortColumn] ?? '';
    let valB = b[mapeSortColumn] ?? '';

    if (mapeSortColumn === 'ipv4') {
      valA = ipToNumber(a.ipv4);
      valB = ipToNumber(b.ipv4);
    } else if (mapeSortColumn === 'vlan') {
      valA = parseInt(String(a.vlan).replace(/\D/g, '')) || 0;
      valB = parseInt(String(b.vlan).replace(/\D/g, '')) || 0;
    } else if (mapeSortColumn === 'status') {
      valA = (a.status === 'active' ? 2 : 0) + (a.ping_ok ? 1 : 0);
      valB = (b.status === 'active' ? 2 : 0) + (b.ping_ok ? 1 : 0);
    } else {
      valA = String(valA).toLowerCase();
      valB = String(valB).toLowerCase();
    }

    if (valA < valB) return mapeSortAsc ? -1 : 1;
    if (valA > valB) return mapeSortAsc ? 1 : -1;
    return 0;
  });

  currentFilteredMAPE = filtered;

  const mapeModeStack = (mode) => {
    const norm = String(mode || '-').trim();
    const l1 = (norm.includes('PD') || norm.includes('DHCP-PD')) ? 'DHCP-PD' : 'SLAAC';
    const l2 = (norm.includes('固定') || norm.includes('fix') || norm.includes('static')) ? '固定' : '動的';
    const normKey = `${l1} ${l2}`;
    const badgeStyle = getServiceBadgeStyle(normKey);
    return `<span class="server-ac-badge server-ac-badge--2tier" style="${badgeStyle}">
              <span class="badge-tier-main">${l1}</span>
              <span class="badge-tier-sub">${l2}</span>
            </span>`;
  };

  tbody.innerHTML = filtered.map((c, fIdx) => {
    let pingBadge = '';
    if (c.ping_ok) {
      pingBadge = `<span class="ping-badge ping-ok" style="margin-top:4px;">● ${c.ping_rtt || '応答あり'}</span>`;
    } else if (c.ndp_ok) {
      pingBadge = `<span class="ping-badge ping-fail" style="margin-top:4px;" title="アンダーレイ IPv6 (NDP) は認識されていますが、MAP-E トンネル IPv4 への Ping が不達です。">✕ トンネル未達</span>`;
    } else {
      pingBadge = `<span class="ping-badge ping-fail" style="margin-top:4px;">✕ 不達 (Offline)</span>`;
    }

    let vlanBadge = '<span style="color:var(--text-faint);">-</span>';
    if (c.vlan && c.vlan !== '-') {
      const m = String(c.vlan).match(/\d+/);
      const vid = m ? m[0] : c.vlan;
      vlanBadge = `<div class="vlan-badge" title="VLAN ${vid} (${c.interface || ''})">
        <span class="vlan-badge-sub">VLAN</span>
        <span class="vlan-badge-num">${vid}</span>
      </div>`;
    }

    const noteText = (c.note || '').trim();
    return `
          <tr>
            <td class="clickable-status col-status" onclick="openMAPEDetail(${fIdx})" title="クリックして詳細情報を表示">
              <div style="display:flex; flex-direction:column; gap:3px; align-items:flex-start; white-space:nowrap;">
                <span class="status-badge ${c.status === 'active' ? 'badge-active' : 'badge-expired'}">
                  <span class="pulse-dot" style="width:6px;height:6px;${c.status === 'active' ? '' : 'display:none;'}"></span> ${c.status === 'active' ? 'Active' : 'Expired'}
                </span>
                ${pingBadge}
              </div>
            </td>
            <td class="col-vlan">${vlanBadge}</td>
            <td>${mapeModeStack(c.mode_display)}</td>
            <td class="mono" style="font-weight:600; color:var(--badge-accent-text);">${escapeHtml(c.prefix || '')}</td>
            <td class="mono" style="color:var(--accent-cyan); font-weight:600;">${escapeHtml(c.ipv4 || '')}</td>
            <td class="mono" style="color:var(--text-soft); font-size:12px;">${escapeHtml(c.ce_ipv6 || '')}</td>
            <td>${noteText ? `<span class="note-text" title="${escapeHtml(noteText)}">${escapeHtml(noteText)}</span>` : '<span style="color:var(--text-faint);">-</span>'}</td>
            <td style="color:var(--text-soft); font-size:12px;">${formatDateTime(c.lease_start)}</td>
            <td style="color:var(--text-soft); font-size:12px;">${formatDateTime(c.lease_expire)}</td>
            <td>
              <div class="btn-action-group">
                <button class="btn-disconnect" onclick="event.stopPropagation(); confirmDisconnectMAPE('${escapeHtml(c.prefix)}', '${escapeHtml(c.ipv4)}', '${escapeHtml(c.ce_ipv6)}')">切断</button>
              </div>
            </td>
          </tr>
        `;
  }).join('');
}
