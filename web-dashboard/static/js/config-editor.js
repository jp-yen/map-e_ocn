// =============================================================================
// config-editor.js — 設定エディター全機能
//   Config Modal 開閉 / タブ切り替え
//   フォームバインド / ステージング
//   PPPoE サービス・ユーザーテーブル操作
//   固定IP テーブル操作
//   バリデーション / Apply / Reset / Discard
//   コンテナ再起動
//   依存: showToast(), loadData() (main.js)
// =============================================================================
// ==========================================
// Config Editor Logic
// ==========================================
let currentConfigLive = null;
let currentConfigStaging = null;
let isConfigDirty = false;
let configDebounceTimer = null;
let activeConfigPanel = 1;

function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

async function openConfigModal() {
  const modal = document.getElementById('configModal');
  if (modal) {
    modal.style.display = 'flex';
    await loadConfigData();
    switchConfigTab(6);
  } else if (typeof openWindow === 'function') {
    openWindow('/config', 'ConfigEditor', 1100, 800);
  } else {
    window.location.href = '/config';
  }
}

function closeConfigModal() {
  const modal = document.getElementById('configModal');
  if (modal) {
    modal.style.display = 'none';
  } else {
    window.close();
  }
}

function notifyReloadData() {
  if (typeof loadData === 'function') {
    try { loadData(true); } catch (e) {}
  }
  if (window.opener && typeof window.opener.loadData === 'function') {
    try { window.opener.loadData(true); } catch (e) {}
  }
}

function switchConfigTab(panelIndex) {
  activeConfigPanel = panelIndex;
  const tabBtns = document.querySelectorAll('.config-tab-btn');
  tabBtns.forEach((btn, idx) => {
    if (idx + 1 === panelIndex) {
      btn.classList.add('active');
    } else {
      btn.classList.remove('active');
    }
  });

  for (let i = 1; i <= 6; i++) {
    const panel = document.getElementById(`configPanel${i}`);
    if (panel) {
      if (i === panelIndex) {
        panel.classList.add('active');
      } else {
        panel.classList.remove('active');
      }
    }
  }

  if (panelIndex === 2) {
    renderPPPoEConfigTab();
  }
}

function toggleAccordion(id) {
  const el = document.getElementById(id);
  if (el) el.classList.toggle('open');
}

async function loadConfigData() {
  try {
    const res = await fetch('/api/config');
    if (!res.ok) throw new Error('Failed to load config');
    const data = await res.json();
    currentConfigLive = data.live || {};
    currentConfigStaging = data.staging || {};
    const hasPending = !!data.has_pending_changes;

    bindConfigToForm(currentConfigStaging);
    updateConfigUIState(hasPending);
  } catch (err) {
    console.error('Error loading config:', err);
    showToast('設定の読み込みに失敗しました: ' + err.message, false);
  }
}

function updateConfigUIState(hasPending) {
  isConfigDirty = hasPending;
  const discardBtn = document.getElementById('btnConfigDiscard');
  const applyBtn = document.getElementById('btnConfigApply');
  const syncBadge = document.getElementById('configSyncBadge');
  const headerDot = document.getElementById('headerConfigDot');

  if (hasPending) {
    discardBtn.style.display = 'inline-flex';
    applyBtn.classList.add('dirty');
    syncBadge.className = 'config-sync-badge pending';
    syncBadge.textContent = '⚠️ 未反映の変更あり';
    if (headerDot) headerDot.style.display = 'inline-block';
  } else {
    discardBtn.style.display = 'none';
    applyBtn.classList.remove('dirty');
    syncBadge.className = 'config-sync-badge synced';
    syncBadge.textContent = '● 稼働設定と同期中';
    if (headerDot) headerDot.style.display = 'none';
  }
}

function isValidDDNSHostname(val) {
  if (!val || val.trim() === '' || val.trim() === '-') return true; // 空欄・未指定は正常
  const trimmed = val.trim().replace(/\.$/, ''); // 末尾ドットは除去して判定
  if (trimmed.length < 3 || trimmed.length > 253) return false;
  // @, 空白, 全角, 特殊記号が含まれているかチェック
  if (/[@_\s]/.test(trimmed)) return false;
  // 非ASCII文字 (全角など) が含まれているかチェック
  if (/[^\x20-\x7E]/.test(trimmed)) return false;

  const parts = trimmed.split('.');
  if (parts.length < 2) return false; // 最低2ラベル必要 (例: host.domain)
  const labelRegex = /^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$/;
  return parts.every(p => labelRegex.test(p));
}

function validateDDNSInput(inputEl) {
  if (!inputEl) return;
  const val = inputEl.value;
  const isValid = isValidDDNSHostname(val);
  if (isValid) {
    inputEl.classList.remove('input-invalid');
    inputEl.title = '';
  } else {
    inputEl.classList.add('input-invalid');
    inputEl.title = '⚠️ 不適切なDDNSホスト名です。英数字、ハイフン、ドットのみ使用可能です（@や全角、アンダースコア等の記号は不可。例: router1.example.jp）';
  }
}

function validatePPPoEIPPool(inputEl) {
  if (!inputEl) return;
  const val = inputEl.value.trim();
  if (!val) {
    // 空欄は有効 (プールなし)
    inputEl.classList.remove('input-invalid');
    inputEl.title = '';
    return;
  }
  const cidrRe = /^(\d{1,3}\.){3}\d{1,3}\/(\d{1,2})$/;
  let valid = false;
  if (cidrRe.test(val)) {
    const [ipPart, prefixPart] = val.split('/');
    const octets = ipPart.split('.').map(Number);
    const prefix = parseInt(prefixPart, 10);
    if (octets.every(o => o >= 0 && o <= 255) && prefix >= 0 && prefix <= 32) {
      valid = true;
    }
  }
  if (valid) {
    inputEl.classList.remove('input-invalid');
    inputEl.title = '';
  } else {
    inputEl.classList.add('input-invalid');
    inputEl.title = '⚠️ CIDR 形式で入力してください（例: 10.9.0.0/24）';
  }
}

function getAllConfiguredVlans() {
  const low = (currentConfigStaging && currentConfigStaging.low_layer) ? currentConfigStaging.low_layer : {};
  const vlanMap = new Map(); // vlanId -> typeLabel

  const parseAndAdd = (field, label) => {
    const el = document.getElementById(`cfg_${field}`);
    const val = (el ? el.value : (low[field] || '')).trim();
    if (val) {
      val.split(/[\s,]+/).forEach(v => {
        const cleanV = v.trim();
        if (cleanV && /^\d+$/.test(cleanV)) {
          if (!vlanMap.has(cleanV)) {
            vlanMap.set(cleanV, label);
          }
        }
      });
    }
  };

  parseAndAdd('PPPOE_VLANS', 'PPPoE');
  parseAndAdd('SLAAC_DYN_VLANS', 'SLAAC動');
  parseAndAdd('PD_DYN_VLANS', 'PD動');
  parseAndAdd('SLAAC_FIX_VLANS', 'SLAAC固');
  parseAndAdd('PD_FIX_VLANS', 'PD固');

  return Array.from(vlanMap.entries())
    .map(([vlan, type]) => ({ vlan, type, num: parseInt(vlan, 10) }))
    .sort((a, b) => a.num - b.num);
}

function bindConfigToForm(cfg) {
  if (!cfg) return;

  // 1. Low Layer
  const ll = cfg.low_layer || {};
  [
    'PPPOE_VLANS', 'SLAAC_DYN_VLANS', 'PD_DYN_VLANS', 'SLAAC_FIX_VLANS', 'PD_FIX_VLANS',
    'MAPE_IF', 'PPPOE_SERVER_BASE_IP', 'PPPOE_IP_POOL', 'MGT_IF', 'MGT_IP', 'MGT_GW',
    'WEB_DASHBOARD_PORT', 'SYSTEM_DNS', 'SYSTEM_NTP', 'DOMAIN'
  ].forEach(k => {
    const el = document.getElementById(`cfg_${k}`);
    if (el) el.value = ll[k] ?? '';
  });
  // IPプール入力欄の初期バリデーション
  const poolEl = document.getElementById('cfg_PPPOE_IP_POOL');
  if (poolEl) validatePPPoEIPPool(poolEl);
  const elNat = document.getElementById('cfg_NAT_ENABLED');
  if (elNat) {
    elNat.checked = (ll.NAT_ENABLED === '1' || ll.NAT_ENABLED === true || ll.NAT_ENABLED === 1);
  }

  // 2. PPPoE
  renderPPPoEConfigTab();

  // 3. IPoE Common
  const ipoe = cfg.ipoe_common || {};
  [
    'BR_IPV4_POOL', 'BASE_SUBNET', 'BR_PREFIX', 'BR_IPV4_ADDR',
    'BR_IF', 'PROV_IF', 'MAPE_DOMAIN_SEARCH', 'MAPE_PROV_PREFIX'
  ].forEach(k => {
    const el = document.getElementById(`cfg_${k}`);
    if (el) el.value = ipoe[k] ?? '';
  });
  const staticList = ipoe.static_ips || cfg?.slaac?.static_ips || [];
  renderStaticIPTable('ipoe', staticList);

  // 4. SLAAC
  const slaac = cfg.slaac || {};
  [
    'SLAAC_BR_BASE', 'SLAAC_BR_SUFFIX', 'SLAAC_FIX_BR_PREFIX', 'SLAAC_FIX_BR_SUFFIX'
  ].forEach(k => {
    const el = document.getElementById(`cfg_${k}`);
    if (el) el.value = slaac[k] ?? '';
  });

  // 5. DHCP-PD
  const pd = cfg.dhcp_pd || {};
  [
    'PD_POOL', 'PD_FIX_POOL'
  ].forEach(k => {
    const el = document.getElementById(`cfg_${k}`);
    if (el) el.value = pd[k] ?? '';
  });

  renderIPoEVlanBanner(cfg);
}

function renderIPoEVlanBanner(cfg) {
  const container = document.getElementById('cfg_ipoe_vlan_banner');
  if (!container) return;
  const ll = cfg?.low_layer || {};
  const modeConfigs = [
    { label: 'SLAAC 動的', l1: 'SLAAC', l2: '動的', key: 'SLAAC_DYN_VLANS' },
    { label: 'DHCP-PD 動的', l1: 'DHCP-PD', l2: '動的', key: 'PD_DYN_VLANS' },
    { label: 'SLAAC 固定', l1: 'SLAAC', l2: '固定', key: 'SLAAC_FIX_VLANS' },
    { label: 'DHCP-PD 固定', l1: 'DHCP-PD', l2: '固定', key: 'PD_FIX_VLANS' }
  ];
  container.innerHTML = modeConfigs.map(m => {
    const rawVal = (ll[m.key] || '').trim();
    const vList = rawVal ? rawVal.split(/[\s,]+/).filter(Boolean) : [];
    const vlanDisplay = vList.length > 0 ? vList.join(', ') : '(未割当)';
    const badgeStyle = typeof getServiceBadgeStyle === 'function' ? getServiceBadgeStyle(m.label) : '';
    return `
        <div class="server-info-item">
          <span class="server-ac-badge server-ac-badge--2tier" style="${badgeStyle}">
            <span class="badge-tier-main">${m.l1}</span>
            <span class="badge-tier-sub">${m.l2}</span>
          </span>
          <span class="server-detail">VLAN: <strong style="color:var(--accent-cyan);">${vlanDisplay}</strong></span>
        </div>
      `;
  }).join('');
}

/* === Config editor table sorting === */
const configSortState = {
  pppoeSrv: { col: '', asc: true },
  pppoeUsr: { col: '', asc: true },
  ipoeStatic: { col: '', asc: true },
};

// 元の配列インデックスを保持したままソートした (item, idx) リストを返す
function sortStagedList(list, sortState) {
  const pairs = (list || []).map((item, i) => ({ item, idx: i }));
  if (!sortState || !sortState.col) return pairs;
  const col = sortState.col;
  pairs.sort((a, b) => {
    let va = a.item[col] ?? '';
    let vb = b.item[col] ?? '';
    if (col === 'ip' || col === 'server_ip') {
      va = ipToNumber(a.item[col]);
      vb = ipToNumber(b.item[col]);
    } else {
      va = String(va).toLowerCase();
      vb = String(vb).toLowerCase();
    }
    if (va < vb) return sortState.asc ? -1 : 1;
    if (va > vb) return sortState.asc ? 1 : -1;
    return 0;
  });
  return pairs;
}

const CONFIG_SORT_PREFIX = { pppoeSrv: 'srv', pppoeUsr: 'usr', ipoeStatic: 'static' };

function sortConfigTable(tableKey, col) {
  const st = configSortState[tableKey];
  if (!st) return;
  if (st.col === col) {
    st.asc = !st.asc;
  } else {
    st.col = col;
    st.asc = true;
  }
  updateConfigSortIndicators();

  if (tableKey === 'pppoeSrv' || tableKey === 'pppoeUsr') {
    renderPPPoEConfigTab();
  } else if (tableKey === 'ipoeStatic') {
    renderStaticIPTable('ipoe', currentConfigStaging?.ipoe_common?.static_ips || []);
  }
}

function updateConfigSortIndicators() {
  const fields = {
    pppoeSrv: ['name'],
    pppoeUsr: ['username', 'password', 'ip', 'service_name', 'ddns_v4', 'note'],
    ipoeStatic: ['mac', 'ip', 'ddns_v6', 'ddns_v4', 'note'],
  };
  Object.keys(fields).forEach(tk => {
    const st = configSortState[tk];
    fields[tk].forEach(col => {
      const ind = document.getElementById(`${CONFIG_SORT_PREFIX[tk]}Ind-${col}`);
      if (ind) {
        ind.textContent = (st.col === col) ? (st.asc ? ' ▲' : ' ▼') : '';
      }
    });
  });
}

function getPPPoEServerInstances() {
  if (!currentConfigStaging) currentConfigStaging = {};
  if (!currentConfigStaging.pppoe) currentConfigStaging.pppoe = {};
  
  if (Array.isArray(currentConfigStaging.pppoe.server_instances)) {
    return currentConfigStaging.pppoe.server_instances;
  }

  // 既存の services { [vlan]: [...] } から集約
  const instances = [];
  const srvMap = new Map();
  const servicesByVlan = currentConfigStaging.pppoe.services || {};

  Object.entries(servicesByVlan).forEach(([vlan, srvList]) => {
    if (Array.isArray(srvList)) {
      srvList.forEach(s => {
        const name = (s.name || s.ac_name || '').trim();
        if (!name) return;
        if (!srvMap.has(name)) {
          const newInst = {
            name: name,
            vrf_enabled: !!s.vrf_enabled
          };
          srvMap.set(name, newInst);
          instances.push(newInst);
        } else {
          const inst = srvMap.get(name);
          if (s.vrf_enabled) inst.vrf_enabled = true;
        }
      });
    }
  });

  currentConfigStaging.pppoe.server_instances = instances;
  return instances;
}

function syncPPPoEServicesToStaging() {
  if (!currentConfigStaging) currentConfigStaging = {};
  if (!currentConfigStaging.pppoe) currentConfigStaging.pppoe = {};
  currentConfigStaging.pppoe.server_instances = getPPPoEServerInstances();
}

function getPPPoEDefinedServices() {
  const instances = getPPPoEServerInstances();
  const set = new Set();
  instances.forEach(inst => {
    const name = (inst.name || '').trim();
    if (name) set.add(name);
  });
  return Array.from(set);
}

function getPPPoECommonUsers() {
  if (!currentConfigStaging) currentConfigStaging = {};
  if (!currentConfigStaging.pppoe) currentConfigStaging.pppoe = {};

  if (Array.isArray(currentConfigStaging.pppoe.common_users)) {
    return currentConfigStaging.pppoe.common_users;
  }
  if (Array.isArray(currentConfigStaging.pppoe.users)) {
    currentConfigStaging.pppoe.common_users = currentConfigStaging.pppoe.users;
    return currentConfigStaging.pppoe.common_users;
  }
  if (currentConfigStaging.pppoe.users && typeof currentConfigStaging.pppoe.users === 'object') {
    for (const [v, ulist] of Object.entries(currentConfigStaging.pppoe.users)) {
      if (Array.isArray(ulist) && ulist.length > 0) {
        currentConfigStaging.pppoe.common_users = JSON.parse(JSON.stringify(ulist));
        return currentConfigStaging.pppoe.common_users;
      }
    }
  }
  currentConfigStaging.pppoe.common_users = [];
  return currentConfigStaging.pppoe.common_users;
}

function syncPPPoECommonUsersToStaging() {
  const users = getPPPoECommonUsers();
  if (!currentConfigStaging) currentConfigStaging = {};
  if (!currentConfigStaging.pppoe) currentConfigStaging.pppoe = {};
  currentConfigStaging.pppoe.common_users = users;
  currentConfigStaging.pppoe.users = users;
}

function renderPPPoEConfigTab() {
  const instances = getPPPoEServerInstances();
  const srvTbody = document.getElementById('cfg_pppoe_servers_tbody');

  // 1. サーバーテーブル描画 (サービス名, 閉域VRF, 操作)
  if (srvTbody) {
    if (instances.length === 0) {
      srvTbody.innerHTML = `<tr><td colspan="3" style="text-align:center; color:var(--text-faint); padding:12px;">サービス定義がありません。「＋ サービス追加」から追加できます。</td></tr>`;
    } else {
      srvTbody.innerHTML = sortStagedList(instances, configSortState.pppoeSrv).map(({ item: s, idx }) => {
        return `
          <tr>
            <td><input type="text" class="table-input" value="${escapeHtml(s.name || '')}" maxlength="11" placeholder="例: OCN (最大11文字)" oninput="onPPPoEServerChanged(${idx}, 'name', this.value, this)"></td>
            <td style="text-align:center; white-space:nowrap;">
              <label class="vrf-toggle" title="閉域 VRF: ON にすると同一サービス内のクライアント同士のみ通信可能になります">
                <input type="checkbox" ${s.vrf_enabled ? 'checked' : ''}
                  onchange="onPPPoEServerChanged(${idx}, 'vrf_enabled', this.checked, this)">
                <span class="vrf-toggle-label">🔒 閉域</span>
              </label>
            </td>
            <td style="text-align:center;"><button class="btn-del-row" onclick="deletePPPoEServerRow(${idx})">削除</button></td>
          </tr>
        `;
      }).join('');
    }
  }

  // 2. クライアント認証セクション描画 (全 VLAN 共通)
  renderPPPoEUsersTable();
}

let pppoeEditMode = 'table'; // 'table' or 'csv'

function formatPPPoEUsersToCSV(users) {
  if (!Array.isArray(users)) return '';
  const lines = [
    '# ユーザー名, パスワード, 割当IP, サービス名, DDNSホスト名, 備考'
  ];
  users.forEach(u => {
    const uname = (u.username || '').trim();
    const pwd = (u.password || '').trim();
    const ip = (u.ip || '').trim();
    let srv = (u.service_name || u.ac_name || '*').trim() || '*';
    srv = srv.replace(/\s+/g, '_');
    const ddns = (u.ddns_v4 || '').trim();
    const note = (u.note || '').trim();

    // 全項目空の行はスキップ
    if (!uname && !pwd && !ip && !note && !ddns) return;

    lines.push(`${uname}, ${pwd}, ${ip}, ${srv}, ${ddns}, ${note}`);
  });
  return lines.join('\n');
}

function parsePPPoEUsersFromCSV(csvText) {
  const list = [];
  if (!csvText) return list;
  const lines = csvText.split(/\r?\n/);
  lines.forEach(rawLine => {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) return;

    let parts = [];
    if (line.includes('\t')) {
      parts = line.split('\t').map(s => s.trim());
    } else if (line.includes(',')) {
      parts = line.split(',').map(s => s.trim());
    } else {
      parts = line.split(/\s+/).map(s => s.trim());
    }

    if (parts.length === 0) return;

    const uname = parts[0] || '';
    const pwd = parts[1] || '';
    const ip = parts[2] || '';
    let srv = (parts[3] || '*').replace(/\s+/g, '_');
    if (!srv) srv = '*';

    let ddns = '';
    let note = '';

    if (line.includes(',') || line.includes('\t')) {
      ddns = parts[4] || '';
      if (parts.length > 5) {
        note = parts.slice(5).join(', ').trim();
      }
    } else {
      // スペース区切り形式
      if (parts.length === 5) {
        note = parts[4];
      } else if (parts.length >= 6) {
        ddns = parts[4];
        note = parts.slice(5).join(' ');
      }
    }

    list.push({
      username: uname,
      password: pwd,
      ip: ip,
      service_name: srv,
      ac_name: srv,
      ddns_v4: ddns,
      note: note
    });
  });
  return list;
}

function togglePPPoEUserEditMode() {
  const tableContainer = document.getElementById('cfg_pppoe_users_table_container');
  const csvContainer = document.getElementById('cfg_pppoe_users_csv_container');
  const toggleBtnText = document.getElementById('btn_pppoe_csv_toggle_text');
  const addBtn = document.getElementById('btn_pppoe_add_user');
  const textarea = document.getElementById('cfg_pppoe_users_csv_textarea');

  if (pppoeEditMode === 'table') {
    const users = getPPPoECommonUsers();
    if (textarea) textarea.value = formatPPPoEUsersToCSV(users);
    if (tableContainer) tableContainer.style.display = 'none';
    if (addBtn) addBtn.style.display = 'none';
    if (csvContainer) csvContainer.style.display = 'block';
    if (toggleBtnText) toggleBtnText.textContent = '📊 テーブル編集モード';
    pppoeEditMode = 'csv';
    if (textarea) textarea.focus();
  } else {
    applyPPPoECSVToTable();
    if (csvContainer) csvContainer.style.display = 'none';
    if (tableContainer) tableContainer.style.display = 'block';
    if (addBtn) addBtn.style.display = 'inline-block';
    if (toggleBtnText) toggleBtnText.textContent = '📝 CSV編集モード';
    pppoeEditMode = 'table';
    renderPPPoEUsersTable();
  }
}

function applyPPPoECSVToTable() {
  const textarea = document.getElementById('cfg_pppoe_users_csv_textarea');
  if (!textarea) return;

  const parsedUsers = parsePPPoEUsersFromCSV(textarea.value);
  currentConfigStaging.pppoe.common_users = parsedUsers;
  syncPPPoECommonUsersToStaging();
  renderPPPoEUsersTable();
  onConfigInputChanged();
}

function onPPPoECSVInputChanged() {
  const textarea = document.getElementById('cfg_pppoe_users_csv_textarea');
  if (!textarea) return;

  const parsedUsers = parsePPPoEUsersFromCSV(textarea.value);
  currentConfigStaging.pppoe.common_users = parsedUsers;
  syncPPPoECommonUsersToStaging();
  onConfigInputChanged();
}

function renderPPPoEUsersTable() {
  const usrTbody = document.getElementById('cfg_pppoe_users_tbody');
  if (!usrTbody) return;

  const usrList = getPPPoECommonUsers();
  const definedServices = getPPPoEDefinedServices();

  if (usrList.length === 0) {
    usrTbody.innerHTML = `<tr><td colspan="7" style="text-align:center; color:var(--text-faint); padding:12px;">登録ユーザーがありません。「＋ ユーザー追加」または「CSV編集モード」から追加できます。</td></tr>`;
  } else {
    usrTbody.innerHTML = sortStagedList(usrList, configSortState.pppoeUsr).map(({ item: u, idx }) => {
      let currentSrv = (u.service_name || u.ac_name || '*').trim() || '*';
      currentSrv = currentSrv.replace(/\s+/g, '_');
      let optionsHtml = `<option value="*">* (全サービス共通 / 未指定)</option>`;
      definedServices.forEach(s => {
        const isSel = (s === currentSrv);
        optionsHtml += `<option value="${escapeHtml(s)}"${isSel ? ' selected' : ''}>${escapeHtml(s)}</option>`;
      });
      if (currentSrv !== '*' && !definedServices.includes(currentSrv)) {
        optionsHtml += `<option value="${escapeHtml(currentSrv)}" selected>${escapeHtml(currentSrv)} (未定義)</option>`;
      }

      return `
        <tr>
          <td><input type="text" class="table-input" value="${escapeHtml(u.username || '')}" placeholder="例: user01@example" oninput="onPPPoEUserChanged(${idx}, 'username', this.value)"></td>
          <td><input type="text" class="table-input" value="${escapeHtml(u.password || '')}" placeholder="パスワード" oninput="onPPPoEUserChanged(${idx}, 'password', this.value)"></td>
          <td><input type="text" class="table-input" value="${escapeHtml(u.ip || '')}" placeholder="192.168.1.1 / * / 空欄（プール）" oninput="onPPPoEUserChanged(${idx}, 'ip', this.value)"></td>
          <td>
            <select class="table-input pppoe-user-srv-select" data-user-idx="${idx}" onchange="onPPPoEUserChanged(${idx}, 'service_name', this.value); onPPPoEUserChanged(${idx}, 'ac_name', this.value);">
              ${optionsHtml}
            </select>
          </td>
          <td><input type="text" class="table-input ddns-input" value="${escapeHtml(u.ddns_v4 || '')}" placeholder="例: router1.pppoe.ocn.ad.jp" oninput="onPPPoEUserChanged(${idx}, 'ddns_v4', this.value); validateDDNSInput(this);"></td>
          <td><input type="text" class="table-input" value="${escapeHtml(u.note || '')}" placeholder="備考" oninput="onPPPoEUserChanged(${idx}, 'note', this.value)"></td>
          <td style="text-align:center;"><button class="btn-del-row" onclick="deletePPPoEUserRow(${idx})">削除</button></td>
        </tr>
      `;
    }).join('');
    usrTbody.querySelectorAll('.ddns-input').forEach(validateDDNSInput);
  }
}

function updatePPPoEUserServiceSelects() {
  const usrList = getPPPoECommonUsers();
  const definedServices = getPPPoEDefinedServices();

  const selects = document.querySelectorAll('.pppoe-user-srv-select');
  selects.forEach(selectEl => {
    const idx = parseInt(selectEl.getAttribute('data-user-idx'), 10);
    const u = usrList[idx] || {};
    let currentSrv = (u.service_name || u.ac_name || selectEl.value || '*').trim() || '*';
    currentSrv = currentSrv.replace(/\s+/g, '_');
    let optionsHtml = `<option value="*">* (全サービス共通 / 未指定)</option>`;
    definedServices.forEach(s => {
      const isSel = (s === currentSrv);
      optionsHtml += `<option value="${escapeHtml(s)}"${isSel ? ' selected' : ''}>${escapeHtml(s)}</option>`;
    });
    if (currentSrv !== '*' && !definedServices.includes(currentSrv)) {
      optionsHtml += `<option value="${escapeHtml(currentSrv)}" selected>${escapeHtml(currentSrv)} (未定義)</option>`;
    }
    if (selectEl.innerHTML !== optionsHtml) {
      selectEl.innerHTML = optionsHtml;
      selectEl.value = currentSrv;
    }
  });
}

function addPPPoEServerRow() {
  const instances = getPPPoEServerInstances();

  const existingNames = new Set(instances.map(s => (s.name || '').trim()).filter(Boolean));
  let nextNum = 1;
  let newName = `Group${nextNum}`;
  while (existingNames.has(newName)) {
    nextNum++;
    newName = `Group${nextNum}`;
  }

  instances.push({
    name: newName,
    vrf_enabled: false
  });

  syncPPPoEServicesToStaging();
  renderPPPoEConfigTab();
  onConfigInputChanged();
}

function deletePPPoEServerRow(idx) {
  const instances = getPPPoEServerInstances();
  if (instances[idx]) {
    instances.splice(idx, 1);
    syncPPPoEServicesToStaging();
    renderPPPoEConfigTab();
    onConfigInputChanged();
  }
}

function onPPPoEServerChanged(idx, key, val, inputEl) {
  const instances = getPPPoEServerInstances();
  const inst = instances[idx];
  if (!inst) return;

  if (key === 'name') {
    // スペースはアンダースコアに変換、最大11文字に制限
    let normalized = (val || '').replace(/\s+/g, '_');
    if (normalized.length > 11) {
      normalized = normalized.slice(0, 11);
    }
    if (inputEl && inputEl.value !== normalized) {
      const start = inputEl.selectionStart;
      const end = inputEl.selectionEnd;
      inputEl.value = normalized;
      if (start !== null && end !== null) {
        inputEl.setSelectionRange(Math.min(start, 11), Math.min(end, 11));
      }
    }
    val = normalized;
  } else if (key === 'vrf_enabled') {
    val = !!val; // boolean に正規化
  }
  inst[key] = val;
  syncPPPoEServicesToStaging();
  if (key === 'name') {
    updatePPPoEUserServiceSelects();
  }
  onConfigInputChanged();
}

function addPPPoEUserRow() {
  const usrList = getPPPoECommonUsers();
  usrList.unshift({ username: '', password: '', ip: '', service_name: '*', ac_name: '*', ddns_v4: '', note: '' });
  syncPPPoECommonUsersToStaging();
  renderPPPoEConfigTab();
  onConfigInputChanged();
}

function deletePPPoEUserRow(idx) {
  const usrList = getPPPoECommonUsers();
  if (usrList[idx]) {
    usrList.splice(idx, 1);
    syncPPPoECommonUsersToStaging();
    renderPPPoEConfigTab();
    onConfigInputChanged();
  }
}

function onPPPoEUserChanged(idx, key, val) {
  const usrList = getPPPoECommonUsers();
  if (usrList[idx]) {
    if (key === 'service_name' || key === 'ac_name') {
      val = (val || '').replace(/\s+/g, '_');
    }
    usrList[idx][key] = val;
    syncPPPoECommonUsersToStaging();
    onConfigInputChanged();
  }
}

function renderStaticIPTable(type, list) {
  const tbody = document.getElementById(`cfg_${type}_static_tbody`);
  if (!tbody) return;
  if (!list || list.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:var(--text-faint); padding:12px;">固定 IP / 端末備考の登録はありません。「＋ 固定 IP / 端末追加」から登録できます。</td></tr>`;
  } else {
    tbody.innerHTML = sortStagedList(list, configSortState.ipoeStatic).map(({ item, idx }) => `
        <tr>
          <td><input type="text" class="table-input" value="${escapeHtml(item.mac || '')}" placeholder="例: 841 または 04:01:a1:8f:a9:3a" oninput="onStaticIPChanged('${type}', ${idx}, 'mac', this.value)"></td>
          <td><input type="text" class="table-input" value="${escapeHtml(item.ip || '')}" placeholder="空欄で動的 (例: 192.168.200.1/32)" oninput="onStaticIPChanged('${type}', ${idx}, 'ip', this.value)" onblur="onStaticIPBlur('${type}', ${idx}, this)"></td>
          <td><input type="text" class="table-input ddns-input" value="${escapeHtml(item.ddns_v6 || '')}" placeholder="例: cebox1.p-ns.flets-west.jp" oninput="onStaticIPChanged('${type}', ${idx}, 'ddns_v6', this.value); validateDDNSInput(this);"></td>
          <td><input type="text" class="table-input ddns-input" value="${escapeHtml(item.ddns_v4 || '')}" placeholder="例: cebox1.v4.flets-west.jp" oninput="onStaticIPChanged('${type}', ${idx}, 'ddns_v4', this.value); validateDDNSInput(this);"></td>
          <td><input type="text" class="table-input" value="${escapeHtml(item.note || '')}" placeholder="例: 動的検証機 (NEC Aterm)" oninput="onStaticIPChanged('${type}', ${idx}, 'note', this.value)"></td>
          <td style="text-align:center;"><button class="btn-del-row" onclick="deleteStaticIPRow('${type}', ${idx})">削除</button></td>
        </tr>
      `).join('');
    tbody.querySelectorAll('.ddns-input').forEach(validateDDNSInput);
  }
}

function normalizeIPv4Subnet(cidrStr) {
  if (!cidrStr || typeof cidrStr !== 'string') return cidrStr;
  const trimmed = cidrStr.trim();
  if (!trimmed.includes('/')) return trimmed;
  const parts = trimmed.split('/');
  if (parts.length !== 2) return trimmed;
  const ip = parts[0].trim();
  const prefixLen = parseInt(parts[1].trim(), 10);
  if (isNaN(prefixLen) || prefixLen < 0 || prefixLen > 32) return trimmed;

  const octets = ip.split('.').map(Number);
  if (octets.length !== 4 || octets.some(o => isNaN(o) || o < 0 || o > 255)) return trimmed;

  const ipNum = ((octets[0] << 24) | (octets[1] << 16) | (octets[2] << 8) | octets[3]) >>> 0;
  const mask = prefixLen === 0 ? 0 : (~0 << (32 - prefixLen)) >>> 0;
  const netNum = (ipNum & mask) >>> 0;

  const n1 = (netNum >>> 24) & 255;
  const n2 = (netNum >>> 16) & 255;
  const n3 = (netNum >>> 8) & 255;
  const n4 = netNum & 255;

  return `${n1}.${n2}.${n3}.${n4}/${prefixLen}`;
}

function onStaticIPBlur(type, idx, inputEl) {
  if (!inputEl) return;
  const raw = inputEl.value;
  const normalized = normalizeIPv4Subnet(raw);
  if (normalized && normalized !== raw) {
    inputEl.value = normalized;
    onStaticIPChanged(type, idx, 'ip', normalized);
  }
}

function addStaticIPRow(type) {
  if (!currentConfigStaging) currentConfigStaging = {};
  const targetObj = (type === 'ipoe')
    ? (currentConfigStaging.ipoe_common = currentConfigStaging.ipoe_common || {})
    : (currentConfigStaging[type] = currentConfigStaging[type] || {});
  if (!targetObj.static_ips) {
    targetObj.static_ips = (currentConfigStaging?.slaac?.static_ips || currentConfigStaging?.dhcp_pd?.static_ips || []);
  }
  targetObj.static_ips.unshift({ mac: '', ip: '', ddns_v6: '', ddns_v4: '', note: '' });
  renderStaticIPTable(type, targetObj.static_ips);
  onConfigInputChanged();
}

function deleteStaticIPRow(type, idx) {
  const targetObj = (type === 'ipoe')
    ? currentConfigStaging?.ipoe_common
    : currentConfigStaging?.[type];
  if (targetObj?.static_ips) {
    targetObj.static_ips.splice(idx, 1);
    renderStaticIPTable(type, targetObj.static_ips);
    onConfigInputChanged();
  }
}

function onStaticIPChanged(type, idx, key, val) {
  const targetObj = (type === 'ipoe')
    ? currentConfigStaging?.ipoe_common
    : currentConfigStaging?.[type];
  if (targetObj?.static_ips?.[idx]) {
    targetObj.static_ips[idx][key] = val;
    onConfigInputChanged();
  }
}

function readFormValuesToStaging() {
  if (!currentConfigStaging) currentConfigStaging = {};
  if (!currentConfigStaging.low_layer) currentConfigStaging.low_layer = {};
  if (!currentConfigStaging.ipoe_common) currentConfigStaging.ipoe_common = {};
  if (!currentConfigStaging.slaac) currentConfigStaging.slaac = {};
  if (!currentConfigStaging.dhcp_pd) currentConfigStaging.dhcp_pd = {};

  // 1. Low Layer
  [
    'PPPOE_VLANS', 'SLAAC_DYN_VLANS', 'PD_DYN_VLANS', 'SLAAC_FIX_VLANS', 'PD_FIX_VLANS',
    'MAPE_IF', 'PPPOE_SERVER_BASE_IP', 'PPPOE_IP_POOL', 'MGT_IF', 'MGT_IP', 'MGT_GW',
    'WEB_DASHBOARD_PORT', 'SYSTEM_DNS', 'SYSTEM_NTP', 'DOMAIN'
  ].forEach(k => {
    const el = document.getElementById(`cfg_${k}`);
    if (el) currentConfigStaging.low_layer[k] = el.value.trim();
  });
  const elNatForm = document.getElementById('cfg_NAT_ENABLED');
  if (elNatForm) {
    currentConfigStaging.low_layer.NAT_ENABLED = elNatForm.checked ? '1' : '0';
  }

  // 3. IPoE Common
  [
    'BR_IPV4_POOL', 'BASE_SUBNET', 'BR_PREFIX', 'BR_IPV4_ADDR',
    'BR_IF', 'PROV_IF', 'MAPE_DOMAIN_SEARCH', 'MAPE_PROV_PREFIX'
  ].forEach(k => {
    const el = document.getElementById(`cfg_${k}`);
    if (el) currentConfigStaging.ipoe_common[k] = el.value.trim();
  });

  // 4. SLAAC
  [
    'SLAAC_BR_BASE', 'SLAAC_BR_SUFFIX', 'SLAAC_FIX_BR_PREFIX', 'SLAAC_FIX_BR_SUFFIX'
  ].forEach(k => {
    const el = document.getElementById(`cfg_${k}`);
    if (el) currentConfigStaging.slaac[k] = el.value.trim();
  });

  // 5. DHCP-PD
  [
    'PD_POOL', 'PD_FIX_POOL'
  ].forEach(k => {
    const el = document.getElementById(`cfg_${k}`);
    if (el) currentConfigStaging.dhcp_pd[k] = el.value.trim();
  });

  // PPPoE CSV 編集モードが開かれている場合はテキストエリアの内容を同期
  if (pppoeEditMode === 'csv') {
    const textarea = document.getElementById('cfg_pppoe_users_csv_textarea');
    if (textarea) {
      const parsedUsers = parsePPPoEUsersFromCSV(textarea.value);
      if (!currentConfigStaging.pppoe) currentConfigStaging.pppoe = {};
      currentConfigStaging.pppoe.common_users = parsedUsers;
      currentConfigStaging.pppoe.users = parsedUsers;
    }
  }
}

function onConfigInputChanged() {
  readFormValuesToStaging();
  renderIPoEVlanBanner(currentConfigStaging);
  updateConfigUIState(true);

  if (configDebounceTimer) clearTimeout(configDebounceTimer);
  configDebounceTimer = setTimeout(async () => {
    try {
      const res = await fetch('/api/config/stage', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(currentConfigStaging)
      });
      const ret = await res.json();
      updateConfigUIState(!!ret.has_pending_changes);
    } catch (e) {
      console.error('Failed to auto-stage config:', e);
    }
  }, 500);
}

// Reset (Template Initialize) Modal & Action
function promptResetConfig() {
  document.getElementById('resetConfirmModal').style.display = 'flex';
}
async function executeResetConfig() {
  document.getElementById('resetConfirmModal').style.display = 'none';
  try {
    const res = await fetch('/api/config/template');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const tmpl = data.template;
    if (!tmpl) throw new Error('No template data returned');

    // テンプレートをステージングとして採用
    currentConfigStaging = tmpl;

    // ステージングをサーバーへ送信
    await fetch('/api/config/stage', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(currentConfigStaging)
    });

    // フォームを再描画
    bindConfigToForm(currentConfigStaging);
    updateConfigUIState(true);
  } catch (e) {
    alert('設定の初期化に失敗しました: ' + e.message);
    console.error('Reset config error:', e);
  }
}

// Discard Modal & Action
function promptDiscardConfig() {
  document.getElementById('discardConfirmModal').style.display = 'flex';
}
function closeDiscardConfirmModal() {
  document.getElementById('discardConfirmModal').style.display = 'none';
}
async function executeDiscardConfig() {
  closeDiscardConfirmModal();
  try {
    const res = await fetch('/api/config/discard', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({})
    });
    const ret = await res.json();
    if (ret.success) {
      currentConfigLive = ret.config;
      currentConfigStaging = JSON.parse(JSON.stringify(ret.config));
      bindConfigToForm(currentConfigStaging);
      updateConfigUIState(false);
      showToast('設定を現在稼働中の状態に戻しました');
    } else {
      showToast('設定のリセットに失敗しました', false);
    }
  } catch (err) {
    showToast('エラー: ' + err.message, false);
  }
}

// Config Validation
function validateConfigData(config) {
  const errors = [];
  if (!config) return ['設定データが存在しません。'];

  // 1. PPPoE 設定
  const pppoe = config.pppoe || {};

  // サービス定義
  const instances = Array.isArray(pppoe.server_instances) ? pppoe.server_instances : [];
  const seenSvcs = new Set();
  instances.forEach((s, idx) => {
    let name = (s.name || '').trim().replace(/\s+/g, '_');
    s.name = name;
    const rowNum = idx + 1;
    if (!name) {
      errors.push(`PPPoE サービス定義: ${rowNum}行目のサービス名が未入力です。`);
    } else if (!/^[a-zA-Z0-9_-]+$/.test(name)) {
      errors.push(`PPPoE サービス定義: サービス名「${name}」に使用できない不正な文字が含まれています。半角英数字、アンダースコア（_）、ハイフン（-）を使用してください（例: Flets_VPN_west）。`);
    } else if (seenSvcs.has(name.toLowerCase())) {
      errors.push(`PPPoE サービス定義: サービス名「${name}」が重複しています。`);
    } else {
      seenSvcs.add(name.toLowerCase());
    }
  });

  // ユーザー設定
  const usersByVlan = pppoe.users || {};
  for (const [vlan, users] of Object.entries(usersByVlan)) {
    if (!Array.isArray(users)) continue;
    const seenUnames = new Set();
    users.forEach((u, idx) => {
      const uname = (u.username || '').trim();
      const pwd = (u.password || '').trim();
      const ip = (u.ip || '').trim();
      let srv = (u.service_name || u.ac_name || '*').trim().replace(/\s+/g, '_');
      u.service_name = srv;
      u.ac_name = srv;
      const note = (u.note || '').trim();
      const ddns = (u.ddns_v4 || '').trim();
      const rowNum = idx + 1;

      // すべて空欄の行は入力意図がないため読み飛ばす
      if (!uname && !pwd && !ip && !note && !ddns) return;

      // IP 欄が '*' または空欄の場合はプール払い出し指定 -> バリデーションスキップ
      const isPoolUser = (ip === '*' || ip === '');

      const missing = [];
      if (!uname) missing.push('ユーザー名');
      if (!pwd) missing.push('パスワード');
      if (!ip && !isPoolUser) missing.push('クライアントIP');

      if (missing.length > 0) {
        const target = uname ? `ユーザー「${uname}」` : `${rowNum}行目`;
        errors.push(`PPPoE (VLAN ${vlan}): ${target} の必須項目（${missing.join(', ')}）が未入力です。`);
      } else if (uname) {
        if (seenUnames.has(uname)) {
          errors.push(`PPPoE (VLAN ${vlan}): ユーザー名「${uname}」が重複しています。`);
        }
        seenUnames.add(uname);
      }
    });
  }

  // 2. 固定IP設定
  let staticIps = config.ipoe_common ? config.ipoe_common.static_ips : null;
  if (!staticIps) {
    const slaacStatic = (config.slaac && config.slaac.static_ips) || [];
    const pdStatic = (config.dhcp_pd && config.dhcp_pd.static_ips) || [];
    staticIps = [...slaacStatic, ...pdStatic];
  }
  if (Array.isArray(staticIps)) {
    const seenMacs = new Set();
    staticIps.forEach((entry, idx) => {
      const mac = (entry.mac || '').trim();
      const ip = (entry.ip || '').trim();
      const note = (entry.note || '').trim();
      const rowNum = idx + 1;

      // すべて空欄の行は入力意図がないため読み飛ばす
      if (!mac && !ip && !note) return;

      if (!mac) {
        errors.push(`固定IP設定: ${rowNum}行目のVLAN・MACアドレスが未入力です。`);
      } else if (seenMacs.has(mac.toLowerCase())) {
        errors.push(`固定IP設定: VLAN・MACアドレス「${mac}」が重複しています。`);
      } else {
        seenMacs.add(mac.toLowerCase());
      }

      // IPv4プレフィックスが空欄の場合は '-' に補完
      if (!ip) {
        entry.ip = '-';
      }
    });
  }

  return errors;
}

// Apply Modal & Action
function promptApplyConfig() {
  readFormValuesToStaging();
  const errors = validateConfigData(currentConfigStaging);
  if (errors.length > 0) {
    alert('【入力エラー】\n設定内容に未入力・不備があるため反映できません。\n以下の項目を確認・修正してください:\n\n・' + errors.join('\n・'));
    return;
  }
  document.getElementById('applyConfirmModal').style.display = 'flex';
}
function closeApplyConfirmModal() {
  document.getElementById('applyConfirmModal').style.display = 'none';
}
async function executeApplyConfig() {
  closeApplyConfirmModal();
  readFormValuesToStaging();

  const errors = validateConfigData(currentConfigStaging);
  if (errors.length > 0) {
    alert('【入力エラー】\n設定内容に未入力・不備があるため反映できません。\n以下の項目を確認・修正してください:\n\n・' + errors.join('\n・'));
    return;
  }

  const pModal = document.getElementById('applyProgressModal');
  const pTitle = document.getElementById('applyProgressTitle');
  const pIcon = document.getElementById('applyProgressIcon');
  const pSpinner = document.getElementById('applyProgressSpinner');
  const pLog = document.getElementById('applyLogBox');
  const pFooter = document.getElementById('applyProgressFooter');

  pModal.style.display = 'flex';
  pTitle.textContent = '設定を反映中...';
  pIcon.textContent = '⏳';
  pSpinner.style.display = 'flex';
  pLog.textContent = 'サービス再起動を開始しました...\n';
  pFooter.style.display = 'none';

  try {
    const res = await fetch('/api/config/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(currentConfigStaging)
    });
    const ret = await res.json();
    pSpinner.style.display = 'none';
    pFooter.style.display = 'flex';

    if (ret.success) {
      lastApplySuccess = true;
      pTitle.textContent = '反映が完了しました';
      pIcon.textContent = '✅';
      pLog.textContent += '\n' + (ret.logs || ret.message);
      showToast('設定の反映が完了しました');
      updateConfigUIState(false);
      await loadConfigData();
      notifyReloadData();
    } else {
      lastApplySuccess = false;
      pTitle.textContent = 'エラーが発生しました';
      pIcon.textContent = '❌';
      let errText = '';
      if (ret.message) errText += '\n【エラー】' + ret.message + '\n';
      if (ret.logs) errText += '\n' + ret.logs;
      pLog.textContent += errText || '\n詳細不明なエラーが発生しました。';
      showToast('設定反映中にエラーが発生しました', false);
    }
  } catch (err) {
    lastApplySuccess = false;
    pSpinner.style.display = 'none';
    pFooter.style.display = 'flex';
    pTitle.textContent = '通信エラー';
    pIcon.textContent = '❌';
    pLog.textContent += '\n例外エラー: ' + err.message;
    showToast('エラー: ' + err.message, false);
  } finally {
    if (pLog) {
      pLog.scrollTop = pLog.scrollHeight;
      requestAnimationFrame(() => { pLog.scrollTop = pLog.scrollHeight; });
    }
  }
}

let lastApplySuccess = false;

function closeApplyProgressModal() {
  const pModal = document.getElementById('applyProgressModal');
  if (pModal) pModal.style.display = 'none';
  if (lastApplySuccess) {
    closeConfigModal();
  }
}

// Container Restart Modal & Action
function promptRestartContainers() {
  const m = document.getElementById("restartConfirmModal");
  if (m) m.style.display = "flex";
}

function closeRestartConfirmModal() {
  const m = document.getElementById("restartConfirmModal");
  if (m) m.style.display = "none";
}

async function executeRestartContainers() {
  closeRestartConfirmModal();

  const pModal = document.getElementById("applyProgressModal");
  const pTitle = document.getElementById("applyProgressTitle");
  const pIcon = document.getElementById("applyProgressIcon");
  const pSpinner = document.getElementById("applyProgressSpinner");
  const pLog = document.getElementById("applyLogBox");
  const pFooter = document.getElementById("applyProgressFooter");

  pModal.style.display = "flex";
  pTitle.textContent = "コンテナを再起動中...";
  pIcon.textContent = "⟳";
  pSpinner.style.display = "flex";
  pSpinner.querySelector("span").textContent = "ネットワーク設定とコンテナを再起動しています...";
  pLog.textContent = "処理を開始しました...\n";
  pFooter.style.display = "none";

  try {
    const res = await fetch("/api/containers/restart", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}"
    });
    const ret = await res.json();
    pSpinner.style.display = "none";
    pFooter.style.display = "flex";

    if (ret.success) {
      pTitle.textContent = "再起動が完了しました";
      pIcon.textContent = "✅";
      pLog.textContent += "\n" + (ret.logs || ret.message);
      showToast("コンテナを再起動しました");
      notifyReloadData();
    } else {
      pTitle.textContent = "再起動エラー";
      pIcon.textContent = "❌";
      let errText = "";
      if (ret.message) errText += "\n【エラー】" + ret.message + "\n";
      if (ret.logs) errText += "\n" + ret.logs;
      pLog.textContent += errText || "\nコンテナ再起動に失敗しました。";
      showToast("コンテナ再起動に失敗しました", false);
    }
  } catch (err) {
    pSpinner.style.display = "none";
    pFooter.style.display = "flex";
    pTitle.textContent = "通信エラー";
    pIcon.textContent = "❌";
    pLog.textContent += "\n例外エラー: " + err.message;
    showToast("エラー: " + err.message, false);
  } finally {
    if (pLog) {
      pLog.scrollTop = pLog.scrollHeight;
      requestAnimationFrame(() => { pLog.scrollTop = pLog.scrollHeight; });
    }
  }
}

