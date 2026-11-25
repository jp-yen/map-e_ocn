// =============================================================================
// tool-mape-calc.js — MAP-E パラメータ計算ツール (全6方式・RFC 7597 / OCN仕様準拠)
// =============================================================================

let confVars = {
  BASE_SUBNET: "2400:4150",
  BR_PREFIX: "2400:4150:3620",
  BR_IPV4_ADDR: "100.127.255.255",
  BR_IPV4_POOL: "10.218.0.0/16",
  BR_IPV6: "2400:4150:3620::647f:ffff",
  SLAAC_BR_BASE: "1000",
  SLAAC_FIX_BR_PREFIX: "3000",
  PD_POOL: "2000",
  PD_FIX_POOL: "4000",
  HGW_BR_BASE: "5000",
  HGW_FIX_BR_BASE: "6000",
  SLAAC_DYN_VLANS: "4",
  PD_DYN_VLANS: "5",
  SLAAC_FIX_VLANS: "6",
  PD_FIX_VLANS: "7",
  HGW_VLANS: "8",
  HGW_FIX_VLANS: "9",
  static_entries: [],
};

let activeTab = "json";

// 接続方式メタデータ
const MODE_INFO = {
  slaac_dyn: {
    title: "① SLAAC (動的)",
    badge: "① SLAAC 動的",
    bannerTitle: "SLAAC 動的セグメント",
    bannerDesc:
      "VLANごとの RA プレフィックス (SLAAC_BR_BASE + VLAN) からIPv6を構成。商用 OCN 仕様 (Rule IPv4 /20, Rule IPv6 /38, EA-bits=18, Offset=6, PSID-bits=6) により 1,008 ポート (16ポート×63セット) のポート制限を行います。",
    vlanRequired: true,
    macRequired: false,
    macHint: "任意 (WAN 側の EUI-64 参考表示用。MAP-E トンネル IPv6 は IPv4 + PSID から決まります)",
    showStaticIp: false,
    showPdPrefix: false,
    eaLen: 18,
    psidLen: 6,
    psidOffset: 6,
  },
  pd_dyn: {
    title: "② DHCPv6-PD (動的)",
    badge: "② PD 動的",
    bannerTitle: "DHCPv6-PD 動的セグメント",
    bannerDesc:
      "Kea DHCPv6 から VLAN ごとの /56 プレフィックス (PD_POOL + VLAN, 第4ヘクステット上位に PSID 埋込) を委譲。商用 OCN 仕様 (Rule IPv4 /20, Rule IPv6 /38, EA-bits=18, Offset=6, PSID-bits=6) により 1,008 ポート (16ポート×63セット) の動的ポート多重化を行います。",
    vlanRequired: true,
    macRequired: false,
    macHint: "CE の MAC アドレス (任意)",
    showStaticIp: false,
    showPdPrefix: true,
    eaLen: 18,
    psidLen: 6,
    psidOffset: 6,
  },
  slaac_fix: {
    title: "③ SLAAC (固定IP)",
    badge: "③ SLAAC 固定IP",
    bannerTitle: "SLAAC 固定IPセグメント",
    bannerDesc:
      "SLAAC_FIX_BR_PREFIX + VLAN にて VLAN ごとに固定IPv4アドレスを1台専有。商用 OCN と同様に MAC / EUI-64 非依存。EA-bits=0, PSID=0, ポート全開放 (1-65535) です。",
    vlanRequired: true,
    macRequired: false,
    macHint: "任意 (固定IP割り当て・MAP-E トンネル IPv6 は VLAN ID と固定IPv4で決まります)",
    showStaticIp: true,
    showPdPrefix: false,
    eaLen: 0,
    psidLen: 0,
    psidOffset: 0,
  },
  pd_fix: {
    title: "④ DHCPv6-PD (固定IP)",
    badge: "④ PD 固定IP",
    bannerTitle: "DHCPv6-PD 固定IPセグメント",
    bannerDesc:
      "PD_FIX_POOL + VLAN から /56 委譲を受け、VLAN ごとに固定IPv4アドレスを1台専有。EA-bits=0, PSID=0, ポート全開放です。",
    vlanRequired: true,
    macRequired: false,
    macHint: "任意 (固定IP割り当ては VLAN ID で決まります)",
    showStaticIp: true,
    showPdPrefix: true,
    eaLen: 0,
    psidLen: 0,
    psidOffset: 0,
  },
  hgw_dyn: {
    title: "⑤ HGW配下模擬 (動的)",
    badge: "⑤ HGW 動的",
    bannerTitle: "HGW配下模擬 動的セグメント (RA /64 + DHCP-PD /60 併用)",
    bannerDesc:
      "RA広報 (HGW_BR_BASE + VLAN) と DHCPv6-PD (/60 委譲) を提供。商用 OCN 仕様 (Rule IPv4 /20, Rule IPv6 /38, EA-bits=18, Offset=6, PSID-bits=6) により 1,008 ポート多重化を行います。",
    vlanRequired: true,
    macRequired: false,
    macHint: "CE の MAC アドレス (任意)",
    showStaticIp: false,
    showPdPrefix: false,
    eaLen: 18,
    psidLen: 6,
    psidOffset: 6,
  },
  hgw_fix: {
    title: "⑥ HGW配下模擬 (固定IP)",
    badge: "⑥ HGW 固定IP",
    bannerTitle: "HGW配下模擬 固定IPセグメント (RA /64 + DHCP-PD /60 併用)",
    bannerDesc:
      "RA広報 (HGW_FIX_BR_BASE + VLAN) と DHCPv6-PD (/60 委譲) を提供し、VLAN ごとに固定IPv4アドレスを1台専有。商用 OCN と同様に MAC / EUI-64 非依存。EA-bits=0, PSID=0, ポート全開放です。",
    vlanRequired: true,
    macRequired: false,
    macHint: "任意 (固定IP割り当ては VLAN ID で決まります)",
    showStaticIp: true,
    showPdPrefix: false,
    eaLen: 0,
    psidLen: 0,
    psidOffset: 0,
  },
};

window.addEventListener("DOMContentLoaded", () => {
  loadMapeConf(false);
});

async function loadMapeConf(showToastMsg) {
  try {
    const res = await fetch("/api/mape-conf-vars");
    if (res.ok) {
      const data = await res.json();
      confVars = { ...confVars, ...data };
      if (showToastMsg) showToast("map-e.conf の最新設定を読み込みました");
    }
  } catch (e) {
    console.error("Failed to load /api/mape-conf-vars:", e);
  }
  applyConfToInputs();
  renderVlanChips();
  renderMacChips();
  calculate();
}

function applyConfToInputs() {
  document.getElementById("cfg_base_subnet").value = confVars.BASE_SUBNET || "2400:4150";
  document.getElementById("cfg_br_prefix").value = confVars.BR_PREFIX || "2400:4150:3620";
  document.getElementById("cfg_br_ipv4_addr").value = confVars.BR_IPV4_ADDR || "100.127.255.255";
  document.getElementById("cfg_br_ipv4_pool").value = confVars.BR_IPV4_POOL || "10.218.0.0/16";
  document.getElementById("cfg_slaac_br_base").value = confVars.SLAAC_BR_BASE || "1000";
  document.getElementById("cfg_slaac_fix_br_prefix").value = confVars.SLAAC_FIX_BR_PREFIX || "3000";
  document.getElementById("cfg_pd_pool").value = confVars.PD_POOL || "2000";
  document.getElementById("cfg_pd_fix_pool").value = confVars.PD_FIX_POOL || "4000";
  const hgwBaseEl = document.getElementById("cfg_hgw_br_base");
  const hgwFixEl = document.getElementById("cfg_hgw_fix_br_base");
  if (hgwBaseEl) hgwBaseEl.value = confVars.HGW_BR_BASE || "5000";
  if (hgwFixEl) hgwFixEl.value = confVars.HGW_FIX_BR_BASE || "6000";
}

function onModeChange() {
  const mode = document.getElementById("calcMode").value;
  const info = MODE_INFO[mode];

  document.getElementById("modeBadge").textContent = info.badge;
  document.getElementById("bannerTitle").textContent = info.bannerTitle;
  document.getElementById("bannerDesc").textContent = info.bannerDesc;

  const vlanGroup = document.getElementById("vlanGroup");
  const vlanBadge = document.getElementById("vlanBadge");
  vlanGroup.style.display = "flex";
  vlanBadge.textContent = info.vlanRequired ? "必須" : "任意";
  vlanBadge.className = info.vlanRequired ? "badge badge-required" : "badge badge-optional";

  const macBadge = document.getElementById("macBadge");
  macBadge.textContent = info.macRequired ? "必須" : "任意";
  macBadge.className = info.macRequired ? "badge badge-required" : "badge badge-optional";
  document.getElementById("macHint").textContent = info.macHint;

  document.getElementById("staticIpGroup").style.display = info.showStaticIp ? "flex" : "none";
  document.getElementById("pdPrefixGroup").style.display = info.showPdPrefix ? "flex" : "none";

  renderVlanChips();
  renderMacChips();

  if (info.showStaticIp) {
    onVlanInput();
  }
  calculate();
}

function renderVlanChips() {
  const mode = document.getElementById("calcMode").value;
  const container = document.getElementById("vlanChips");
  container.innerHTML = "";

  const vlanSourceMap = {
    slaac_dyn: confVars.SLAAC_DYN_VLANS || "4",
    pd_dyn: confVars.PD_DYN_VLANS || "5",
    slaac_fix: confVars.SLAAC_FIX_VLANS || "6",
    pd_fix: confVars.PD_FIX_VLANS || "7",
    hgw_dyn: confVars.HGW_VLANS || "8",
    hgw_fix: confVars.HGW_FIX_VLANS || "9",
  };

  const vlans = (vlanSourceMap[mode] || "").trim().split(/\s+/).filter((v) => /^\d+$/.test(v));
  if (vlans.length === 0) return;

  const currentVal = document.getElementById("vlanInput").value;

  vlans.forEach((vlan, idx) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip" + (currentVal === vlan ? " active" : "");
    chip.textContent = `VLAN ${vlan}`;
    chip.onclick = () => {
      document.getElementById("vlanInput").value = vlan;
      document.querySelectorAll("#vlanChips .chip").forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      onVlanInput();
    };
    container.appendChild(chip);

    if (idx === 0 && (!currentVal || !vlans.includes(currentVal))) {
      document.getElementById("vlanInput").value = vlan;
      chip.classList.add("active");
    }
  });
}

function renderMacChips() {
  const container = document.getElementById("macChips");
  container.innerHTML = "";

  const entries = confVars.static_entries || [];
  if (entries.length === 0) return;

  entries.forEach((entry) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    const label = entry.note ? `${entry.note} (VLAN ${entry.key})` : `VLAN ${entry.key}`;
    chip.textContent = label;
    chip.title = `VLAN: ${entry.key}, IP: ${entry.ipv4} ${entry.note || ""}`;
    chip.onclick = () => {
      document.getElementById("vlanInput").value = entry.key;
      if (entry.ipv4 && document.getElementById("staticIpInput")) {
        document.getElementById("staticIpInput").value = entry.ipv4;
      }
      document.querySelectorAll("#macChips .chip").forEach((c) => c.classList.remove("active"));
      chip.classList.add("active");
      calculate();
    };
    container.appendChild(chip);
  });
}

function onVlanInput() {
  const vlanStr = document.getElementById("vlanInput").value.trim();
  const entries = confVars.static_entries || [];
  const match = entries.find((e) => String(e.key).trim() === vlanStr);
  if (match && match.ipv4) {
    const staticIpInput = document.getElementById("staticIpInput");
    if (staticIpInput) {
      staticIpInput.value = match.ipv4;
    }
  }
  calculate();
}

function onMacInput() {
  calculate();
}

// =========================================================================
// 計算ロジック (MapeCommon.pm / mape_calc.tmpl / RFC 7597 Section 6.2 準拠)
// =========================================================================

function ipv4ToHexHextets(ipv4) {
  const parts = ipv4.trim().split(".");
  if (parts.length !== 4) return "";
  const hex = parts.map((p) => {
    const n = parseInt(p, 10);
    return isNaN(n) ? "00" : n.toString(16).padStart(2, "0");
  });
  return `${hex[0]}${hex[1]}:${hex[2]}${hex[3]}`;
}

function calcBrIpv6(brPrefix, brIpv4) {
  const hexPart = ipv4ToHexHextets(brIpv4);
  if (!hexPart) return "";
  const cleanPrefix = brPrefix.trim().replace(/:+$/, "");
  return `${cleanPrefix}::${hexPart}`;
}

// IPv6 アドレス文字列を展開 8 hextet 配列に正規化する
function expandIpv6Hextets(addrStr) {
  const clean = (addrStr || "").split("/")[0].trim();
  if (!clean) return ["0000", "0000", "0000", "0000", "0000", "0000", "0000", "0000"];
  if (clean.includes("::")) {
    const [leftRaw, rightRaw] = clean.split("::");
    const left = leftRaw ? leftRaw.split(":") : [];
    const right = rightRaw ? rightRaw.split(":") : [];
    const fillCount = Math.max(0, 8 - left.length - right.length);
    const middle = new Array(fillCount).fill("0");
    return [...left, ...middle, ...right].slice(0, 8).map((h) => h.padStart(4, "0").toLowerCase());
  }
  const parts = clean.split(":");
  while (parts.length < 8) parts.push("0");
  return parts.slice(0, 8).map((h) => h.padStart(4, "0").toLowerCase());
}

// RFC 5952 準拠で 8 hextet 配列を圧縮表記に整形する
function compressIpv6Hextets(hextets) {
  const nums = hextets.map((h) => parseInt(h, 16) || 0);
  let bestStart = -1;
  let bestLen = 0;
  let curStart = -1;
  let curLen = 0;

  for (let i = 0; i < 8; i++) {
    if (nums[i] === 0) {
      if (curStart === -1) curStart = i;
      curLen++;
      if (curLen > bestLen && curLen >= 2) {
        bestStart = curStart;
        bestLen = curLen;
      }
    } else {
      curStart = -1;
      curLen = 0;
    }
  }

  const shortHex = nums.map((n) => n.toString(16));
  if (bestStart === -1) return shortHex.join(":");
  const before = shortHex.slice(0, bestStart).join(":");
  const after = shortHex.slice(bestStart + bestLen).join(":");
  return `${before}::${after}`;
}

// mape_calc.convert_to_data_ipv6 準拠:
//   Interface ID = 00 + IPv4(32bit) + PSID(16bit) + 00(8bit)
function convertToDataIpv6(prefixStr, targetIpv4, psidVal = 0) {
  const pfxHextets = expandIpv6Hextets(prefixStr).slice(0, 4);
  const octs = targetIpv4.trim().split(".").map((x) => parseInt(x, 10) || 0);
  while (octs.length < 4) octs.push(0);

  const psid = parseInt(psidVal, 10) || 0;
  const psidHigh = (psid >> 8) & 0xff;
  const psidLow = psid & 0xff;

  const h4 = `00${octs[0].toString(16).padStart(2, "0")}`;
  const h5 = `${octs[1].toString(16).padStart(2, "0")}${octs[2].toString(16).padStart(2, "0")}`;
  const h6 = `${octs[3].toString(16).padStart(2, "0")}${psidHigh.toString(16).padStart(2, "0")}`;
  const h7 = `${psidLow.toString(16).padStart(2, "0")}00`;

  return compressIpv6Hextets([...pfxHextets, h4, h5, h6, h7]);
}

// 動的セグメント共通の割り当て IPv4 算出 (Rule IPv4 /20 + EA-bits 上位12bit)
function calcDynamicIpv4(poolNet, vlan, vlanHextet, psidVal) {
  const v4SubIdx = (vlan - 1) % 16;
  const oct2 = (v4SubIdx * 16) & 0xf0;
  const poolOcts = poolNet.split(".");
  const ruleV4Base = (parseInt(poolOcts[0], 10) << 24) | (parseInt(poolOcts[1], 10) << 16) | (oct2 << 8);
  const eaH3 = vlanHextet & 0x03ff;
  const eaBits = (eaH3 << 8) | (psidVal & 0xff);
  const v4Suffix = (eaBits >> 6) & 0xfff;
  const fullV4Int = (ruleV4Base | v4Suffix) >>> 0;
  return `${(fullV4Int >>> 24) & 0xff}.${(fullV4Int >>> 16) & 0xff}.${(fullV4Int >>> 8) & 0xff}.${fullV4Int & 0xff}`;
}

function parseStaticIpv4Input(staticIp) {
  if (!staticIp) return { ipv4Prefix: "172.16.18.64", ipv4PrefixLen: 28 };
  if (staticIp.includes("/")) {
    const parts = staticIp.split("/");
    return {
      ipv4Prefix: parts[0].trim(),
      ipv4PrefixLen: parseInt(parts[1].trim(), 10) || 32,
    };
  }
  return { ipv4Prefix: staticIp, ipv4PrefixLen: 32 };
}

function calculate() {
  const mode = document.getElementById("calcMode").value;
  const info = MODE_INFO[mode];

  const baseSubnet = document.getElementById("cfg_base_subnet").value.trim().replace(/:+$/, "");
  const brPrefixRaw = document.getElementById("cfg_br_prefix").value.trim();
  const brPrefix = brPrefixRaw.replace("${BASE_SUBNET}", baseSubnet);
  const brIpv4Addr = document.getElementById("cfg_br_ipv4_addr").value.trim();
  const brIpv4PoolRaw = document.getElementById("cfg_br_ipv4_pool").value.trim();
  const slaacBrBase = parseInt(document.getElementById("cfg_slaac_br_base").value.trim(), 10) || 1000;
  const slaacFixBrPrefix = parseInt(document.getElementById("cfg_slaac_fix_br_prefix").value.trim(), 10) || 3000;
  const pdPool = document.getElementById("cfg_pd_pool").value.trim() || "2000";
  const pdFixPool = document.getElementById("cfg_pd_fix_pool").value.trim() || "4000";
  const hgwBrBase = parseInt(document.getElementById("cfg_hgw_br_base")?.value?.trim() || confVars.HGW_BR_BASE || "5000", 10) || 5000;
  const hgwFixBrBase = parseInt(document.getElementById("cfg_hgw_fix_br_base")?.value?.trim() || confVars.HGW_FIX_BR_BASE || "6000", 10) || 6000;

  const vlan = parseInt(document.getElementById("vlanInput").value.trim(), 10) || 4;
  const staticIp = document.getElementById("staticIpInput").value.trim();
  const customPdPrefix = document.getElementById("pdPrefixInput").value.trim();

  let poolNet = "10.218.0.0";
  let poolMask = 16;
  if (brIpv4PoolRaw.includes("/")) {
    const parts = brIpv4PoolRaw.split("/");
    poolNet = parts[0].trim();
    poolMask = parseInt(parts[1].trim(), 10) || 16;
  } else if (brIpv4PoolRaw) {
    poolNet = brIpv4PoolRaw;
  }

  const brIpv6 = calcBrIpv6(brPrefix, brIpv4Addr);

  let ipv4Prefix = poolNet;
  let ipv4PrefixLen = poolMask;
  if (["slaac_dyn", "pd_dyn", "hgw_dyn"].includes(mode)) {
    const v4SubIdx = (vlan - 1) % 16;
    const oct2 = (v4SubIdx * 16) & 0xf0;
    const poolOcts = poolNet.split(".");
    ipv4Prefix = `${poolOcts[0]}.${poolOcts[1]}.${oct2}.0`;
    ipv4PrefixLen = 20;
  }

  let ipv6Prefix = "";
  let ipv6PrefixLen = 64;
  const eaBitsLen = info.eaLen;
  const psidBitsLen = info.psidLen;
  const psidOffset = info.psidOffset;

  let assignedIpv4 = "-";
  let psidVal = 0;
  let portRangesText = "-";
  let ceIpv6 = "-";
  let hostName = "";

  if (mode === "slaac_dyn") {
    const vlanHextet = slaacBrBase + vlan;
    ipv6Prefix = `${baseSubnet}:${slaacBrBase}::`;
    ipv6PrefixLen = 38;
    psidVal = ((vlan - 1) % 63) + 1;
    assignedIpv4 = calcDynamicIpv4(poolNet, vlan, vlanHextet, psidVal);
    const cePrefix = `${baseSubnet}:${vlanHextet}:${psidVal.toString(16).padStart(2, "0")}00::`;
    ceIpv6 = convertToDataIpv6(cePrefix, assignedIpv4, psidVal);
    hostName = `ce-${assignedIpv4}.map.ocn.ad.jp`;
  } else if (mode === "pd_dyn") {
    const pdBaseInt = parseInt(pdPool, 10) || 2000;
    const vlanHextet = pdBaseInt + vlan;
    psidVal = ((vlan - 1) % 63) + 1;
    let cePrefix = `${baseSubnet}:${vlanHextet}:${psidVal.toString(16).padStart(2, "0")}00::`;
    if (customPdPrefix) {
      const parts = customPdPrefix.split("/");
      cePrefix = parts[0].trim().replace(/:+$/, "") + "::";
      const h = expandIpv6Hextets(cePrefix);
      const customVlanHextet = parseInt(h[2], 16) || vlanHextet;
      const customPsid = (parseInt(h[3], 16) >> 8) & 0x3f;
      if (customPsid >= 1 && customPsid <= 63) psidVal = customPsid;
      assignedIpv4 = calcDynamicIpv4(poolNet, vlan, customVlanHextet, psidVal);
    } else {
      assignedIpv4 = calcDynamicIpv4(poolNet, vlan, vlanHextet, psidVal);
    }
    ipv6Prefix = `${baseSubnet}:${pdPool}::`;
    ipv6PrefixLen = 38;
    ceIpv6 = convertToDataIpv6(cePrefix, assignedIpv4, psidVal);
    hostName = `ce-${assignedIpv4}.map.ocn.ad.jp`;
  } else if (mode === "slaac_fix") {
    const vlanHextet = slaacFixBrPrefix + vlan;
    ipv6Prefix = `${baseSubnet}:${vlanHextet}::`;
    ipv6PrefixLen = 56;
    ({ ipv4Prefix, ipv4PrefixLen } = parseStaticIpv4Input(staticIp));
    assignedIpv4 = ipv4Prefix;
    psidVal = 0;
    ceIpv6 = convertToDataIpv6(ipv6Prefix, assignedIpv4, 0);
    const matchEntry = (confVars.static_entries || []).find((e) => String(e.key).trim() === String(vlan));
    hostName = matchEntry && matchEntry.note ? matchEntry.note : `ce-${assignedIpv4}.map.ocn.ad.jp`;
  } else if (mode === "pd_fix") {
    const pdFixBaseInt = parseInt(pdFixPool, 10) || 4000;
    const vlanHextet = pdFixBaseInt + vlan;
    if (customPdPrefix) {
      const parts = customPdPrefix.split("/");
      ipv6Prefix = parts[0].trim().replace(/:+$/, "") + "::";
      ipv6PrefixLen = parts.length > 1 ? parseInt(parts[1], 10) : 56;
    } else {
      ipv6Prefix = `${baseSubnet}:${vlanHextet}::`;
      ipv6PrefixLen = 56;
    }
    ({ ipv4Prefix, ipv4PrefixLen } = parseStaticIpv4Input(staticIp));
    assignedIpv4 = ipv4Prefix;
    psidVal = 0;
    ceIpv6 = convertToDataIpv6(ipv6Prefix, assignedIpv4, 0);
    const matchEntry = (confVars.static_entries || []).find((e) => String(e.key).trim() === String(vlan));
    hostName = matchEntry && matchEntry.note ? matchEntry.note : `ce-${assignedIpv4}.map.ocn.ad.jp`;
  } else if (mode === "hgw_dyn") {
    const vlanHextet = hgwBrBase + vlan;
    ipv6Prefix = `${baseSubnet}:${hgwBrBase}::`;
    ipv6PrefixLen = 38;
    psidVal = ((vlan - 1) % 63) + 1;
    assignedIpv4 = calcDynamicIpv4(poolNet, vlan, vlanHextet, psidVal);
    const cePrefix = `${baseSubnet}:${vlanHextet}:${psidVal.toString(16).padStart(2, "0")}00::`;
    ceIpv6 = convertToDataIpv6(cePrefix, assignedIpv4, psidVal);
    hostName = `ce-${assignedIpv4}.map.ocn.ad.jp`;
  } else if (mode === "hgw_fix") {
    const vlanHextet = hgwFixBrBase + vlan;
    ipv6Prefix = `${baseSubnet}:${vlanHextet}::`;
    ipv6PrefixLen = 56;
    ({ ipv4Prefix, ipv4PrefixLen } = parseStaticIpv4Input(staticIp));
    assignedIpv4 = ipv4Prefix;
    psidVal = 0;
    ceIpv6 = convertToDataIpv6(ipv6Prefix, assignedIpv4, 0);
    const matchEntry = (confVars.static_entries || []).find((e) => String(e.key).trim() === String(vlan));
    hostName = matchEntry && matchEntry.note ? matchEntry.note : `ce-${assignedIpv4}.map.ocn.ad.jp`;
  }

  // 利用可能ポートの計算 (商用 OCN MAP-E 1,008ポート仕様: psidOffset=6, psidLen=6)
  if (eaBitsLen === 0 || psidBitsLen === 0) {
    portRangesText = "1-65535 (全開放: 65,535ポート)";
  } else {
    const ranges = [];
    const numBlocks = (1 << psidOffset) - 1; // 63セット
    const portShift = 16 - psidOffset - psidBitsLen; // 4bit (16ポート)
    const blockSize = 1 << portShift;
    for (let k = 1; k <= numBlocks; k++) {
      const startPort = (k << (16 - psidOffset)) | (psidVal << portShift);
      const endPort = startPort + blockSize - 1;
      if (ranges.length < 4) {
        ranges.push(`${startPort}-${endPort}`);
      }
    }
    const totalPorts = numBlocks * blockSize;
    portRangesText = `${ranges.join(", ")} ... (計 ${totalPorts.toLocaleString()} ポート / ${numBlocks}ブロック)`;
  }

  document.getElementById("res_br").textContent = brIpv6 || "-";
  document.getElementById("res_v4_prefix").textContent = ipv4Prefix;
  document.getElementById("res_v4_len").textContent = ipv4PrefixLen.toString();
  document.getElementById("res_v6_prefix").textContent = ipv6Prefix;
  document.getElementById("res_v6_len").textContent = ipv6PrefixLen.toString();
  document.getElementById("res_ea_len").textContent = eaBitsLen.toString();
  document.getElementById("res_psid_len").textContent = psidBitsLen.toString();
  document.getElementById("res_psid_offset").textContent = psidOffset.toString();

  document.getElementById("detail_assigned_v4").textContent = assignedIpv4;
  document.getElementById("detail_psid").textContent =
    eaBitsLen > 0 ? `${psidVal} (0x${psidVal.toString(16).padStart(2, "0")})` : "0 (固定IP: なし)";
  document.getElementById("detail_ports").textContent = portRangesText;
  document.getElementById("detail_ce_v6").textContent = ceIpv6;

  updateCodeOutput({
    brIpv6,
    ipv4Prefix,
    ipv4PrefixLen,
    ipv6Prefix,
    ipv6PrefixLen,
    eaBitsLen,
    psidBitsLen,
    psidOffset,
    mode,
    assignedIpv4,
    psidVal,
    hostName,
  });
}

function updateCodeOutput(data) {
  const codeBlock = document.getElementById("codeOutput");

  if (activeTab === "json") {
    const ruleObj = {
      ipv6Prefix: data.ipv6Prefix,
      ipv6PrefixLength: data.ipv6PrefixLen.toString(),
      ipv4Prefix: data.ipv4Prefix,
      ipv4PrefixLength: data.ipv4PrefixLen.toString(),
      brIpv6Address: data.brIpv6,
      eaBitLength: data.eaBitsLen.toString(),
      psIdOffset: data.psidOffset.toString(),
      hostName: data.hostName || `ce-${data.ipv4Prefix}.map.ocn.ad.jp`,
    };
    if (data.eaBitsLen > 0) {
      ruleObj.psId = data.psidVal.toString();
    }
    codeBlock.textContent = JSON.stringify({ basicMapRules: [ruleObj] }, null, 4);
  } else if (activeTab === "uci") {
    codeBlock.textContent = `# ----------------------------------------------------
# OpenWRT MAP-E 設定 (/etc/config/network)
# 方式: ${MODE_INFO[data.mode].title}
# ----------------------------------------------------
uci set network.wan6_map=interface
uci set network.wan6_map.proto='map'
uci set network.wan6_map.maptype='map-e'
uci set network.wan6_map.peeraddr='${data.brIpv6}'
uci set network.wan6_map.ipaddr='${data.ipv4Prefix}'
uci set network.wan6_map.ip4prefixlen='${data.ipv4PrefixLen}'
uci set network.wan6_map.ip6prefix='${data.ipv6Prefix}'
uci set network.wan6_map.ip6prefixlen='${data.ipv6PrefixLen}'
uci set network.wan6_map.ealen='${data.eaBitsLen}'
uci set network.wan6_map.psidlen='${data.psidBitsLen}'
uci set network.wan6_map.offset='${data.psidOffset}'
uci commit network
/etc/init.d/network restart`;
  } else if (activeTab === "file") {
    codeBlock.textContent = `# /etc/config/network への追記設定
config interface 'wan6_map'
\toption proto 'map'
\toption maptype 'map-e'
\toption peeraddr '${data.brIpv6}'
\toption ipaddr '${data.ipv4Prefix}'
\toption ip4prefixlen '${data.ipv4PrefixLen}'
\toption ip6prefix '${data.ipv6Prefix}'
\toption ip6prefixlen '${data.ipv6PrefixLen}'
\toption ealen '${data.eaBitsLen}'
\toption psidlen '${data.psidBitsLen}'
\toption offset '${data.psidOffset}'`;
  }
}

function switchCodeTab(tab, btn) {
  activeTab = tab;
  document.querySelectorAll(".code-tab").forEach((t) => t.classList.remove("active"));
  btn.classList.add("active");
  calculate();
}

async function copyTextToClipboard(text) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (e) {
      // fallback
    }
  }
  try {
    const textArea = document.createElement("textarea");
    textArea.value = text;
    textArea.style.position = "fixed";
    textArea.style.left = "-999999px";
    textArea.style.top = "-999999px";
    textArea.style.opacity = "0";
    textArea.setAttribute("readonly", "");
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();
    const successful = document.execCommand("copy");
    document.body.removeChild(textArea);
    if (successful) return true;
  } catch (err) {
    console.warn("execCommand failed:", err);
  }
  window.prompt("Ctrl+C (または Cmd+C) を押してコピーしてください:", text);
  return true;
}

async function copyText(elementId, btnEl) {
  const el = document.getElementById(elementId);
  if (!el) return;
  const text = el.textContent.trim();
  if (!text || text === "-") return;
  try {
    await copyTextToClipboard(text);
    showToast(`「${text}」をコピーしました`);
    if (btnEl) {
      const originalText = btnEl.textContent;
      btnEl.textContent = "コピー済!";
      btnEl.style.color = "var(--accent-emerald, #10b981)";
      btnEl.style.borderColor = "var(--accent-emerald, #10b981)";
      setTimeout(() => {
        btnEl.textContent = originalText;
        btnEl.style.color = "";
        btnEl.style.borderColor = "";
      }, 1500);
    }
  } catch (e) {
    showToast("コピーに失敗しました");
  }
}

async function copyActiveCode(btnEl) {
  const code = document.getElementById("codeOutput").textContent;
  try {
    await copyTextToClipboard(code);
    showToast("設定コードをクリップボードにコピーしました");
    if (btnEl) {
      const originalText = btnEl.innerHTML;
      btnEl.innerHTML = "<span>✅</span> コピー完了!";
      setTimeout(() => {
        btnEl.innerHTML = originalText;
      }, 1500);
    }
  } catch (e) {
    showToast("コピーに失敗しました");
  }
}

function showToast(msg) {
  const toast = document.getElementById("toast");
  const toastMsg = document.getElementById("toastMsg");
  toastMsg.textContent = msg;
  toast.classList.add("show");
  setTimeout(() => {
    toast.classList.remove("show");
  }, 2500);
}
