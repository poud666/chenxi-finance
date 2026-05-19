/**
 * 股市预警 · 仪表盘数据渲染
 * 加载 data.json，按分类渲染指标卡片
 */

const DATA_URL = './data.json';

// 状态 → 边框颜色/文字
const STATUS_MAP = {
  ok:      { className: 'status-ok',    label: '正常',  color: 'green' },
  warn:    { className: 'status-warn',  label: '⚠ 警戒', color: 'amber' },
  alert:   { className: 'status-alert', label: '🚨 危险', color: 'red'   },
  unknown: { className: 'status-ok',    label: '数据获取失败', color: 'mute' },
};

function formatChange(change, changePct, unit) {
  if (change == null) return '';
  const sign = change > 0 ? '▲' : (change < 0 ? '▼' : '—');
  const color = change > 0 ? 'green' : (change < 0 ? 'red' : 'mute');
  const pct = changePct != null ? ` (${changePct > 0 ? '+' : ''}${changePct}%)` : '';
  return `<div class="ind-change ${color}">${sign} ${Math.abs(change)}${unit}${pct}</div>`;
}

function formatValue(value, unit) {
  if (value == null) return '—';
  if (Math.abs(value) >= 1000) {
    return value.toLocaleString('en-US', { maximumFractionDigits: 2 }) + unit;
  }
  return value + unit;
}

function renderRangeBar(ind) {
  // 简单的位置示意：用 value 在阈值范围里的位置
  const t = ind.threshold || {};
  let pct = 50;
  if (t.normal && t.alert) {
    pct = Math.min(100, Math.max(0, ((ind.value - t.normal[0]) / (t.alert - t.normal[0])) * 100));
  }
  const color = STATUS_MAP[ind.status]?.color || 'green';
  return `<div class="ind-bar"><div class="bar-fill" style="width:${pct}%;background:var(--${color})"></div></div>`;
}

function renderTag(ind) {
  const t = ind.threshold || {};
  if (ind.status === 'alert') return `<div class="ind-tag">🚨 危险</div>`;
  if (ind.status === 'warn')  return `<div class="ind-tag">⚠ 警戒</div>`;
  if (t.normal) return `<div class="ind-tag">正常区间 (${t.normal[0]}-${t.normal[1]}${ind.unit})</div>`;
  return `<div class="ind-tag">监控中</div>`;
}

function renderIndicator(ind) {
  const status = STATUS_MAP[ind.status] || STATUS_MAP.ok;
  const valueDisplay = formatValue(ind.value, ind.unit || '');

  return `
    <div class="ind-card ${status.className}">
      <div class="ind-name">${ind.name} <span>${ind.sub || ''}</span></div>
      <div class="ind-val">${valueDisplay}</div>
      ${formatChange(ind.change, ind.change_pct, ind.unit || '')}
      ${renderRangeBar(ind)}
      ${renderTag(ind)}
      <p class="ind-desc">${ind.desc || ''}</p>
    </div>
  `;
}

function renderSentiment(s) {
  if (!s) return '';
  const ratingMap = {
    'extreme fear': { cn: '极度恐慌', color: 'red' },
    'fear':         { cn: '恐慌',     color: 'red' },
    'neutral':      { cn: '中性',     color: 'amber' },
    'greed':        { cn: '贪婪',     color: 'amber' },
    'extreme greed':{ cn: '极度贪婪', color: 'red' },
  };
  const r = ratingMap[s.rating?.toLowerCase()] || ratingMap.neutral;

  let advice = '';
  if (s.value >= 75) advice = `当前市场处于<b class="amber">${r.cn}</b>区间。历史经验显示，连续 >75 后易出现回调。建议保持仓位但警惕情绪反转。`;
  else if (s.value >= 55) advice = `当前市场处于<b class="amber">${r.cn}</b>区间。情绪偏多，谨慎追高。`;
  else if (s.value >= 45) advice = `当前市场处于<b>中性</b>区间，情绪平稳。`;
  else if (s.value >= 25) advice = `当前市场处于<b class="red">${r.cn}</b>区间。机会大于风险，可分批布局。`;
  else                    advice = `当前市场处于<b class="red">${r.cn}</b>区间。历史经验显示，这往往是中期底部信号。`;

  return `
    <div class="sentiment">
      <div class="gauge" style="background:conic-gradient(
        var(--red) 0deg,
        var(--amber) 90deg,
        var(--green) 180deg,
        var(--amber) 225deg,
        var(--red) 270deg,
        var(--bg-elev) 270deg)">
        <div class="gauge-val" style="color:var(--${r.color})">${s.value}</div>
        <div class="gauge-label">${r.cn}</div>
      </div>
      <div class="sentiment-info">
        <div class="sent-row"><span>极度恐慌</span><span class="sent-bar"><i style="left:${s.value}%"></i></span><span>极度贪婪</span></div>
        <p>${advice}</p>
        <div class="sent-meta">
          <div><span>昨日</span><b>${s.prev_close}</b></div>
          <div><span>一周前</span><b>${s.prev_1_week}</b></div>
          <div><span>一月前</span><b>${s.prev_1_month}</b></div>
          <div><span>一年前</span><b>${s.prev_1_year}</b></div>
        </div>
      </div>
    </div>
  `;
}

async function load() {
  const tier1Container = document.querySelector('[data-tier="1"]');
  const tier2Container = document.querySelector('[data-tier="2"]');
  const tier4Container = document.querySelector('[data-tier="4"]');
  const sentimentContainer = document.querySelector('[data-sentiment]');
  const updatedEl = document.querySelector('[data-updated]');

  try {
    const r = await fetch(DATA_URL + '?t=' + Date.now());
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();

    // 按分类分组
    const groups = { tier1: [], tier2: [], tier4: [] };
    data.indicators.forEach(ind => {
      if (groups[ind.category]) groups[ind.category].push(ind);
    });

    if (tier1Container) tier1Container.innerHTML = groups.tier1.map(renderIndicator).join('');
    if (tier2Container) tier2Container.innerHTML = groups.tier2.map(renderIndicator).join('');
    if (tier4Container) tier4Container.innerHTML = groups.tier4.map(renderIndicator).join('');
    if (sentimentContainer) sentimentContainer.innerHTML = renderSentiment(data.sentiment);

    if (updatedEl) {
      updatedEl.textContent = `数据更新于：${data.updated_at_cn} (北京时间)`;
    }

  } catch (err) {
    console.error('数据加载失败:', err);
    if (tier1Container) {
      tier1Container.innerHTML = `
        <div class="ind-card" style="grid-column:1/-1;text-align:center;padding:40px;">
          <p style="color:var(--text-soft)">⚠ 数据加载失败，请稍后刷新</p>
          <p style="color:var(--text-mute);font-size:0.85rem;margin-top:8px">${err.message}</p>
        </div>
      `;
    }
  }
}

// 刷新按钮
document.addEventListener('DOMContentLoaded', () => {
  load();
  const refreshBtn = document.querySelector('[data-refresh]');
  if (refreshBtn) refreshBtn.addEventListener('click', load);

  // 自动每 5 分钟刷新一次（数据本身 15 分钟更新一次，足够）
  setInterval(load, 5 * 60 * 1000);
});
