/* ============================================================
   拾知 StudyLoop — 前端逻辑
   纯原生 JS，无依赖。所有数据来自本地 /api/*。
   ============================================================ */
'use strict';

/* ---------------- 基础工具 ---------------- */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const pad2 = (n) => String(n).padStart(2, '0');
const localDate = (d = new Date()) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;

async function api(path, opts = {}) {
  const init = { headers: {}, ...opts };
  if (init.body && typeof init.body !== 'string' && !(init.body instanceof FormData)) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(init.body);
  }
  const res = await fetch(path, init);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }
  if (!res.ok) throw new Error((data && data.error) || `请求失败 (${res.status})`);
  return data;
}

function toast(msg, type = '') {
  const box = $('#toasts');
  const el = document.createElement('div');
  el.className = 'toast ' + type;
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; el.style.transform = 'translateY(8px)'; }, 2600);
  setTimeout(() => el.remove(), 3000);
}

/* ---------------- 弹窗 ---------------- */
let modalOnClose = null;
function openModal({ title, body, foot = [], onClose = null }) {
  $('#modalTitle').textContent = title;
  $('#modalBody').innerHTML = body;
  const f = $('#modalFoot');
  f.innerHTML = '';
  foot.forEach((b) => {
    const btn = document.createElement('button');
    btn.className = 'btn ' + (b.cls || 'ghost');
    if (b.id) btn.id = b.id;
    btn.textContent = b.label;
    btn.onclick = b.onClick;
    f.appendChild(btn);
  });
  modalOnClose = onClose;
  $('#modalBackdrop').classList.remove('hidden');
}
function closeModal() {
  $('#modalBackdrop').classList.add('hidden');
  $('#modalBody').innerHTML = '';
  if (modalOnClose) { const fn = modalOnClose; modalOnClose = null; fn(); }
}

/* ---------------- 全局状态 ---------------- */
const S = {
  decks: [],
  settings: {},
  overview: null,
  stats: null,
  aiReady: false,
  aiStatus: null,     // /api/ai/providers 结果
  analysis: null,     // AI 逐页讲解弹窗状态 { analyses: [], running: false, engine: '' }
  view: 'today',
  deck: null,          // 当前打开的讲义详情
  deckFilter: 'all',
  queue: [], qi: 0, revealed: false, conf: null,
  cardT0: 0, rvStats: { n: 0, good: 0, ms: 0, neu: 0 },
  session: null, focus: { timer: null, left: 25 * 60, total: 25 * 60, running: false },
  quiz: null,
};

const TIPS = [
  '先复习旧的，再学新的：中断的复习间隔会让旧知识崩得比新知识还快。',
  '翻面前先自己说出来。哪怕说错，也比直接看答案记牢得多（测试效应）。',
  '看不懂的一页，试试用「向 12 岁的孩子解释」的方式写下来——讲不清，就是没真懂（费曼技巧）。',
  '学 25 分钟歇 5 分钟，比连学 2 小时更有效：注意力是会疲劳的资源。',
  '同一份讲义连着刷三遍，不如隔 1 天、3 天、7 天各刷一遍（间隔效应）。',
  '把手机放到另一个房间。每一次「就看一眼」都在重置你的注意力。',
  '今晚睡够 7 小时。慢波睡眠里，海马体会把白天的内容重放给大脑皮层。',
  '记不住时先问「我缺的是线索还是知识」：多数遗忘是提取线索丢了，不是没存进去。',
  '在有点吃力的时候复习，收益最大。太轻松说明间隔太短，完全想不起说明太长。',
  '先用一句话说出这章的主线，再往里填细节。结构先于细节。',
  '别追求「看着眼熟」。眼熟是熟悉感的错觉，闭上眼睛能复述才算掌握。',
  '错题不要只记答案，要记「我为什么会想岔」——这才是可迁移的知识。',
];

const METHOD = [
  ['提取练习', '每次复习都要求你先回忆再看答案，而不是重读划线。这叫 testing effect，是所有记忆方法里性价比最高的一个。'],
  ['间隔重复', '复习时间由 FSRS-4.5 算法按你的每张卡计算：记得越稳，间隔越长；忘了就立刻重来。'],
  ['交错练习', '新卡按讲义轮流发放，而不是整块刷同一份，迫使大脑「分辨这是哪一类问题」。'],
  ['元认知校准', '翻面前先自评「有把握 / 不确定 / 没把握」，统计里会显示你的自信和实际正确率差多少。'],
  ['费曼技巧', '讲义详情里的「费曼模式」，让你用大白话解释一页内容，系统存档，供以后回看自己理解的演化。'],
  ['睡眠巩固', '晚上 21 点后会出现「睡前巩固」入口：睡前 15 分钟复习最难的内容，记忆会在睡眠中被重放。'],
  ['打卡与连续天数', '小步持续比一次性猛学有效。打卡记录学了多少分钟，连续天数用来对抗「今天算了」的破窗效应。'],
  ['补签机制', '每周 1 次补签机会，防止一次断签就彻底放弃（避免全有或全无心态）。'],
];

/* ---------------- 视图切换 ---------------- */
const VIEW_LOADERS = {
  today: loadToday,
  library: loadLibrary,
  review: loadReviewView,
  quiz: loadQuizView,
  stats: loadStats,
  settings: loadSettings,
};

async function go(view) {
  S.view = view;
  $$('.view').forEach((v) => v.classList.add('hidden'));
  const target = $('#view-' + view);
  if (target) target.classList.remove('hidden');
  $$('.nav-item').forEach((b) => b.classList.toggle('active', b.dataset.nav === view));
  if (location.hash.slice(1) !== view) history.replaceState(null, '', '#' + view);
  try { if (VIEW_LOADERS[view]) await VIEW_LOADERS[view](); }
  catch (e) { toast(e.message, 'err'); }
}

/* ============================================================
   今日
   ============================================================ */
function greetingText() {
  const h = new Date().getHours();
  if (h < 5) return '夜深了，学到这就好';
  if (h < 9) return '早上好，先来一轮复习';
  if (h < 12) return '上午效率最高，安排最难的内容';
  if (h < 14) return '午间小睡后，记忆会更牢';
  if (h < 18) return '下午适合做题与自测';
  if (h < 22) return '晚上适合睡前巩固';
  return '睡前 15 分钟，抵过白天 1 小时';
}

async function loadToday() {
  const [ov, st, plan] = await Promise.all([
    api('/api/today'), api('/api/stats?days=35'), api('/api/plan')]);
  S.overview = ov; S.stats = st; S.plan = plan;
  renderToday();
}

function renderToday() {
  const ov = S.overview, st = S.stats || {};
  const goal = ov.goal_minutes || 30;
  const pct = Math.min(100, Math.round((ov.minutes_today / goal) * 100));
  $('#greeting').textContent = greetingText();
  $('#todaySub').textContent = `${ov.date} · 共 ${ov.total_decks} 份讲义 / ${ov.total_cards} 张卡片`;
  $('#goalPct').textContent = pct + '%';
  $('#minutesToday').textContent = ov.minutes_today + ' 分钟';
  $('#reviewedToday').textContent = ov.reviewed_today + ' 张';
  $('#reviewedToday').parentElement.querySelector('.k').textContent = '今日复习';
  $('#retentionToday').textContent = ov.retention_30d == null ? '—' : Math.round(ov.retention_30d * 100) + '%';
  const ring = $('#goalRing');
  const C = 2 * Math.PI * 52;
  ring.style.strokeDasharray = C;
  ring.style.strokeDashoffset = C * (1 - Math.min(1, ov.minutes_today / goal));
  ring.style.stroke = pct >= 100 ? 'var(--mint)' : 'var(--accent)';

  const s = ov.streak || {};
  $('#streakText').textContent = `连续 ${s.streak || 0} 天`;
  $('#sideStreak').querySelector('span').textContent = `${s.streak || 0} 天`;
  $('#studiedText').textContent = s.studied_today ? '今天已打卡' : '今天还没开始';

  // 任务清单
  const newLeft = Math.max(0, (ov.new_limit || 20) - (ov.new_today || 0));
  const tasks = [
    ['due', ov.due, '张到期复习', '忘得最快的先来'],
    ['new', newLeft, `张新卡（上限 ${ov.new_limit}）`, ov.new_available ? `共有 ${ov.new_available} 张待学` : '还没有新卡'],
    ['min', Math.max(0, goal - ov.minutes_today), '分钟到今日目标', `已学 ${ov.minutes_today} 分钟`],
  ];
  $('#taskList').innerHTML = tasks.map(([cls, n, t, sub]) => `
    <div class="task">
      <div class="num ${cls}">${n}</div>
      <div><div>${esc(t)}</div><div class="lbl">${esc(sub)}</div></div>
    </div>`).join('');
  const examPlans = ((S.plan && S.plan.decks) || []).filter((d) => d.days_left != null);
  $('#questHint').innerHTML = examPlans.length
    ? examPlans.map((d) => `距「${esc(d.title)}」<b style="color:var(--amber)">${d.days_left}</b> 天 → 每天 <b>${d.per_day}</b> 张新卡`).join('<br>')
    : (ov.due === 0 && newLeft === 0
      ? '今天没有到期卡片了。可以自测一下，或看一份新讲义。'
      : '到期卡片会按遗忘风险排序，先复习旧的，再学新的。');

  // 未来 7 天
  const fc = ov.forecast || [];
  const max = Math.max(1, ...fc.map((f) => f.count));
  $('#forecastChart').innerHTML = fc.map((f, i) => `
    <div class="bar-wrap" title="${f.date}：${f.count} 张">
      <div class="bar-val">${f.count || ''}</div>
      <div class="bar ${i === 0 ? 'today' : ''}" style="height:${Math.round((f.count / max) * 72) + 4}%"></div>
      <div class="bar-lbl">${i === 0 ? '今天' : '周' + f.weekday}</div>
    </div>`).join('');

  $('#tipText').textContent = TIPS[new Date().getDate() % TIPS.length];
  $('#sideTip').textContent = ov.due > 0 ? `还有 ${ov.due} 张到期卡` : '到期卡已清空 🎉';

  // 迷你热力图
  renderHeat($('#miniHeat'), st.heatmap || [], false);
  const todayKey = localDate();
  const y = new Date(); y.setDate(y.getDate() - 1);
  const yKey = localDate(y);
  const yRow = (st.heatmap || []).find((r) => r.date === yKey);
  const yStudied = yRow && (yRow.reviews > 0 || yRow.minutes > 0);
  $('#makeupBtn').hidden = !!yStudied;
  const hr = new Date().getHours();
  $('#sleepBtn').hidden = !(hr >= 21 || hr < 4);

  // 讲义进度
  const decks = S.decks.slice(0, 4);
  $('#recentDecks').innerHTML = decks.length ? decks.map((d) => `
    <div class="mini-item" data-deck="${d.id}">
      <i class="mini-dot" style="background:${esc(d.color || 'var(--accent)')}"></i>
      <span class="mini-name">${esc(d.title)}</span>
      <span class="mini-bar"><i style="width:${d.cards ? Math.round(((d.cards - (d.new_cards || 0)) / d.cards) * 100) : 0}%"></i></span>
      <span class="mini-num">${d.cards} 张</span>
    </div>`).join('')
    : '<p class="hint">还没有讲义，去「讲义库」拖一份进来。</p>';
  $$('#recentDecks .mini-item').forEach((el) => {
    el.style.cursor = 'pointer';
    el.onclick = () => openDeck(Number(el.dataset.deck));
  });

  // 计时显示
  renderTimer();
}

function renderHeat(box, heat, big) {
  if (!box) return;
  const todayKey = localDate();
  box.innerHTML = heat.map((d) => {
    const lvl = levelOf(d);
    const isToday = d.date === todayKey;
    return `<i data-l="${lvl}" class="${isToday ? 'today' : ''}" title="${d.date}：复习 ${d.reviews} 张 · ${d.minutes} 分钟"></i>`;
  }).join('');
}
function levelOf(d) {
  if (!d) return 0;
  if (d.reviews >= 50) return 4;
  if (d.reviews >= 25) return 3;
  if (d.reviews >= 10) return 2;
  if (d.reviews >= 1 || d.minutes > 0) return 1;
  return 0;
}

/* ---------------- 打卡 ---------------- */
async function doCheckin() {
  openModal({
    title: '今日打卡',
    body: `
      <label>今天学了多久（分钟）
        <input type="number" id="ckMin" value="${Math.min(S.overview?.minutes_today || 0, 240) || 30}" min="0" max="720">
      </label>
      <label>状态
        <select id="ckMood">
          <option value="good">状态不错</option>
          <option value="ok">一般</option>
          <option value="tired">有点累</option>
        </select>
      </label>
      <label>今天最有收获的一件事（可选）
        <textarea id="ckNote" rows="2" placeholder="例如：终于分清了 LTP 和 LTD 的区别"></textarea>
      </label>
      <p class="hint">打卡记录的是「持续」，不是「完美」。哪怕只学 10 分钟，也值得记下来。</p>`,
    foot: [
      { label: '取消', onClick: closeModal },
      {
        label: '确认打卡', cls: 'primary', onClick: async () => {
          try {
            const r = await api('/api/checkin', {
              method: 'POST',
              body: {
                minutes: Number($('#ckMin').value) || 0,
                note: $('#ckNote').value, mood: $('#ckMood').value,
              },
            });
            closeModal();
            S.overview = r.today;
            toast(`已打卡，连续 ${r.streak.streak} 天 🔥`, 'ok');
            loadToday();
          } catch (e) { toast(e.message, 'err'); }
        },
      },
    ],
  });
}

async function doMakeup() {
  const y = new Date(); y.setDate(y.getDate() - 1);
  const key = `${y.getFullYear()}-${pad2(y.getMonth() + 1)}-${pad2(y.getDate())}`;
  openModal({
    title: `补签 ${key}`,
    body: `<p>每周有 1 次补签机会。它不是作弊，是给「昨天真的来不及」留的一条缓冲带。</p>
           <label>补记时长（分钟）<input type="number" id="mkMin" value="15" min="1" max="300"></label>`,
    foot: [
      { label: '取消', onClick: closeModal },
      {
        label: '补签', cls: 'primary', onClick: async () => {
          try {
            await api('/api/checkin/makeup', { method: 'POST', body: { date: key, minutes: Number($('#mkMin').value) || 15 } });
            closeModal(); toast('补签成功', 'ok'); loadToday();
          } catch (e) { toast(e.message, 'err'); }
        },
      },
    ],
  });
}

/* ---------------- 专注计时 ---------------- */
function renderTimer() {
  const f = S.focus;
  const m = Math.floor(f.left / 60), s = f.left % 60;
  const disp = $('#timerDisplay');
  if (disp) disp.textContent = `${pad2(m)}:${pad2(s)}`;
  const btn = $('#timerBtn');
  if (btn) btn.textContent = f.running ? '暂停' : (f.left === f.total ? '开始专注' : '继续');
}

async function toggleFocus() {
  const f = S.focus;
  if (f.running) {
    clearInterval(f.timer); f.running = false; f.timer = null;
    renderTimer();
    // 提前结束：按已用时间记录
    const used = Math.round((f.total - f.left) / 60);
    if (used >= 1 && S.session) {
      try {
        await api('/api/sessions/stop', { method: 'POST', body: { id: S.session, minutes: used } });
        toast(`这次专注 ${used} 分钟，已记录`, 'ok');
        S.session = null; loadToday();
      } catch (e) { toast(e.message, 'err'); }
    }
    return;
  }
  if (f.left <= 0) f.left = f.total;
  if (!S.session) {
    try {
      const r = await api('/api/sessions/start', { method: 'POST', body: { kind: 'focus' } });
      S.session = r.id;
    } catch (e) { /* 计时仍可继续，只是不落库 */ }
  }
  f.running = true;
  f.timer = setInterval(async () => {
    f.left -= 1;
    renderTimer();
    if (f.left <= 0) {
      clearInterval(f.timer); f.running = false; f.timer = null;
      const mins = Math.round(f.total / 60);
      try {
        if (S.session) {
          await api('/api/sessions/stop', { method: 'POST', body: { id: S.session, minutes: mins } });
          S.session = null;
        }
        toast(`专注完成 ${mins} 分钟！起来走两步，喝口水`, 'ok');
        loadToday();
      } catch (e) { toast(e.message, 'err'); }
      f.left = f.total;
    }
  }, 1000);
  renderTimer();
}

/* ============================================================
   讲义库
   ============================================================ */
async function loadLibrary() {
  const decks = await api('/api/decks');
  S.decks = decks;
  const grid = $('#deckGrid');
  if (!decks.length) {
    grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1"><p>还没有讲义。把 PPT 或 PDF 拖到上面试试。</p></div>';
    return;
  }
  grid.innerHTML = decks.map((d) => {
    const learned = (d.cards || 0) - (d.new_cards || 0);
    const pct = d.cards ? Math.round((learned / d.cards) * 100) : 0;
    return `<div class="deck-card" data-id="${d.id}" style="--dc:${esc(d.color || 'var(--accent)')}">
      <h4>${esc(d.title)}</h4>
      <div class="meta">
        <span>${esc(d.source_type || '')}</span>
        <span>${d.sections || d.page_count || 0} 节</span>
        ${d.exam_date ? `<span>考试 ${esc(d.exam_date)}</span>` : ''}
      </div>
      <div class="deck-stats">
        <span><b>${d.cards || 0}</b> 张卡</span>
        <span>待复习 <b>${d.due_cards || 0}</b></span>
        <span>新卡 <b>${d.new_cards || 0}</b></span>
        <span>掌握 ${pct}%</span>
      </div>
      <div class="deck-actions">
        <button class="btn sm ghost" data-act="open">详情</button>
        <button class="btn sm ghost" data-act="review">复习</button>
      </div>
    </div>`;
  }).join('');
  $$('#deckGrid .deck-card').forEach((card) => {
    const id = Number(card.dataset.id);
    card.addEventListener('click', (e) => {
      const act = e.target.dataset?.act;
      if (act === 'review') { startReview(id); return; }
      openDeck(id);
    });
  });
}

async function uploadFiles(files) {
  if (!files || !files.length) return;
  for (const f of files) {
    const fd = new FormData();
    fd.append('file', f);
    fd.append('title', f.name.replace(/\.[^.]+$/, ''));
    const t = document.createElement('div');
    t.className = 'toast';
    t.textContent = `正在导入 ${f.name} …`;
    $('#toasts').appendChild(t);
    try {
      const r = await api('/api/decks/upload', { method: 'POST', body: fd });
      t.textContent = `${f.name} 导入完成`;
      t.className = 'toast ok';
      setTimeout(() => t.remove(), 2600);
      const b = await api('/api/bootstrap');
      S.decks = b.decks; S.overview = b.overview; S.settings = b.settings;
      await loadLibrary();
      const did = r.deck_ids && r.deck_ids[0];
      if (!did) continue;
      await openDeck(did);
      // 导入后自动开始 AI 逐页讲解；没有可用引擎则只提示
      try { if (!S.aiStatus) S.aiStatus = await api('/api/ai/providers'); }
      catch (e) { S.aiStatus = { effective: null }; }
      if (S.aiStatus?.effective) {
        openAnalysis({ auto: true });
      } else {
        toast('已导入。还没配置 AI 引擎：到「设置」填直连 Key 或装好本机 dsh，再点「AI 逐页讲解」', 'err');
      }
    } catch (e) {
      t.textContent = `${f.name} 失败：${e.message}`;
      t.className = 'toast err';
      setTimeout(() => t.remove(), 5000);
    }
  }
}

async function openDeck(id) {
  try {
    const d = await api('/api/decks/' + id);
    S.deck = d;
    S.deckFilter = 'all';
    go('library').then(() => {
      $('#deckGrid').classList.add('hidden');
      $('#dropzone').classList.add('hidden');
      $('#deckDetail').classList.remove('hidden');
      renderDeckDetail();
    });
  } catch (e) { toast(e.message, 'err'); }
}

function renderDeckDetail() {
  const d = S.deck;
  if (!d) return;
  $('#detailTitle').textContent = d.title;
  $('#detailMeta').innerHTML = `
    <span>${esc(d.source_type || '')}</span>
    <span>${d.sections.length} 个小节</span>
    <span>${d.stats?.n || 0} 张卡片（新 ${d.stats?.new || 0}）</span>
    <span>正确率 ${d.retention == null ? '—' : Math.round(d.retention * 100) + '%'}</span>
    ${d.exam_date ? `<span>考试日期 ${esc(d.exam_date)}</span>` : ''}
    <span>导入于 ${esc((d.created_at || '').slice(0, 10))}</span>`;
  $('#sectionList').innerHTML = d.sections.map((s) => `
    <div class="section-item" data-sec="${s.id}">
      <div class="st">${esc(s.title || '第 ' + (s.idx + 1) + ' 节')}<span>${s.cards || 0} 张卡</span></div>
      <p class="sp">${esc((s.text || '').replace(/\s+/g, ' ').slice(0, 90))}</p>
    </div>`).join('');
  $$('#sectionList .section-item').forEach((el) => {
    el.onclick = () => showSection(Number(el.dataset.sec));
  });
  loadDeckCards();
}

async function loadDeckCards() {
  const d = S.deck;
  if (!d) return;
  const qs = new URLSearchParams({ deck_id: d.id, limit: '300' });
  if (S.deckFilter === 'new') qs.set('state', 'new');
  if (S.deckFilter === 'auto') qs.set('source', 'auto');
  if (S.deckFilter === 'ai') qs.set('source', 'ai');
  const cards = await api('/api/cards?' + qs.toString());
  $('#deckCards').innerHTML = cards.length ? cards.map((c) => `
    <div class="card-item" data-id="${c.id}">
      <div class="q">${renderCloze(c.front)}${c.suspended ? '<span class="src">已暂停</span>' : ''}<span class="src">${c.source === 'ai' ? 'AI' : c.source === 'auto' ? '自动' : '手动'}</span></div>
      <div class="a">${renderCloze(esc(c.back))}</div>
      <div class="row-act">
        <button class="link" data-act="edit">编辑</button>
        <button class="link" data-act="toggle">${c.suspended ? '恢复' : '暂停'}</button>
        <button class="link" data-act="del" style="color:var(--rose)">删除</button>
        <span class="muted" style="font-size:11px">${c.state === 'new' ? '未学' : '已学 ' + c.reps + ' 次'}</span>
      </div>
    </div>`).join('')
    : (S.deckFilter === 'ai'
      ? '<p class="hint">还没有 AI 生成的卡片。点上方「AI 逐页讲解」，在讲解报告里勾选核心考点的复习卡并导入。</p>'
      : '<p class="hint">还没有卡片。点上方「AI 逐页讲解」，勾选核心考点卡片导入；或点左侧某一页用「＋加卡」手动添加。</p>');
  if (S.deckFilter === 'ai' && cards.length) {
    const clr = document.createElement('button');
    clr.className = 'btn ghost sm';
    clr.style.cssText = 'margin-top:12px;color:var(--rose);border-color:rgba(231,117,152,.4)';
    clr.textContent = `清除本讲义全部 AI 卡（${cards.length} 张）`;
    clr.onclick = async () => {
      if (!confirm(`删除本讲义全部 ${cards.length} 张 AI 生成卡？此操作不可恢复。`)) return;
      await api('/api/cards/bulk', { method: 'POST',
        body: { action: 'delete', ids: cards.map((c) => c.id) } });
      toast('已清除', 'ok'); loadDeckCards(); refreshDeckStats();
    };
    $('#deckCards').appendChild(clr);
  }
  $$('#deckCards .card-item').forEach((el) => {
    const id = Number(el.dataset.id);
    const card = cards.find((c) => c.id === id);
    el.querySelector('[data-act="edit"]').onclick = () => editCard(card);
    el.querySelector('[data-act="del"]').onclick = async () => {
      if (!confirm('删除这张卡？')) return;
      await api('/api/cards/' + id, { method: 'DELETE' });
      loadDeckCards(); refreshDeckStats();
    };
    el.querySelector('[data-act="toggle"]').onclick = async () => {
      await api('/api/cards/' + id, { method: 'PATCH', body: { suspended: card.suspended ? 0 : 1 } });
      loadDeckCards();
    };
  });
}

function renderCloze(text) {
  return esc(text).replace(/_{2,}|＿{2,}/g, '<span class="blank">＿＿＿＿</span>').replace(/\n/g, '<br>');
}

function showSection(sid) {
  const s = S.deck.sections.find((x) => x.id === sid);
  if (!s) return;
  openModal({
    title: s.title || '小节',
    body: `
      <div style="white-space:pre-wrap;line-height:1.85">${esc(s.text || '')}</div>
      ${s.image_path ? `<img src="/${esc(s.image_path)}" alt="插图">` : ''}
      <div class="row" style="margin-top:16px">
        <button class="btn ghost sm" id="secAddCard">为这一页加卡片</button>
        <button class="btn ghost sm" id="secFeynman">费曼：讲给小孩听</button>
      </div>`,
    foot: [{ label: '关闭', onClick: closeModal }],
  });
  $('#secAddCard').onclick = () => {
    closeModal();
    editCard({ deck_id: S.deck.id, section_id: s.id, kind: 'basic', front: '', back: '', note: '' }, true);
  };
  $('#secFeynman').onclick = () => { closeModal(); feynman(s); };
}

function editCard(card, isNew = false) {
  openModal({
    title: isNew ? '新建卡片' : '编辑卡片',
    body: `
      <label>类型
        <select id="cdKind">
          <option value="basic" ${card.kind === 'basic' ? 'selected' : ''}>问答</option>
          <option value="cloze" ${card.kind === 'cloze' ? 'selected' : ''}>填空（用 ＿＿＿＿ 表示空）</option>
        </select>
      </label>
      <label>正面 / 问题<textarea id="cdFront" rows="3">${esc(card.front || '')}</textarea></label>
      <label>背面 / 答案<textarea id="cdBack" rows="3">${esc(card.back || '')}</textarea></label>
      <label>补充说明（可选）<textarea id="cdNote" rows="2">${esc(card.note || '')}</textarea></label>`,
    foot: [
      { label: '取消', onClick: closeModal },
      {
        label: '保存', cls: 'primary', onClick: async () => {
          const body = {
            deck_id: card.deck_id, section_id: card.section_id,
            kind: $('#cdKind').value, front: $('#cdFront').value,
            back: $('#cdBack').value, note: $('#cdNote').value,
          };
          try {
            if (isNew) await api('/api/cards', { method: 'POST', body });
            else await api('/api/cards/' + card.id, { method: 'PATCH', body });
            closeModal();
            if (S.deck) { loadDeckCards(); refreshDeckStats(); }
            toast('已保存', 'ok');
          } catch (e) { toast(e.message, 'err'); }
        },
      },
    ],
  });
}

async function refreshDeckStats() {
  if (!S.deck) return;
  try {
    S.deck = await api('/api/decks/' + S.deck.id);
    renderDeckDetail();
  } catch { /* ignore */ }
}

async function askDeck() {
  const q = $('#askInput').value.trim();
  if (!q) return;
  const box = $('#askAnswer');
  box.classList.remove('hidden');
  if (!S.aiReady) {
    box.innerHTML = '<p class="hint">这是可选功能：在「设置」里填入 AI 的 API 地址和 Key（OpenAI 兼容接口）即可启用。不配置也不影响其它功能。</p>';
    return;
  }
  box.innerHTML = '<p class="hint">正在检索讲义片段…</p>';
  try {
    const r = await api('/api/ai/ask', { method: 'POST', body: { question: q, deck_id: S.deck?.id } });
    box.innerHTML = `<div class="ask-body">${esc(r.answer).replace(/\n/g, '<br>')}</div>
      <div class="ask-src">依据：${r.sources.map((s) => esc(`${s.deck} · ${s.section}`)).join('；')}</div>`;
  } catch (e) { box.innerHTML = `<p class="hint">${esc(e.message)}</p>`; }
}

function feynman(section) {
  openModal({
    title: '费曼模式',
    body: `
      <p class="hint">假设你在给一个 12 岁的孩子讲这一页。不能用一个专业词，除非你先解释它。</p>
      <div style="background:rgba(255,255,255,.03);border:1px solid var(--line);border-radius:10px;padding:10px 12px;font-size:12.5px;color:var(--muted);max-height:110px;overflow:auto">${esc((section.text || '').slice(0, 400))}</div>
      <label style="margin-top:12px">我的解释
        <textarea id="fyText" rows="6" placeholder="如果讲到一个词卡住了，那就是你还没真懂的地方。"></textarea>
      </label>`,
    foot: [
      { label: '取消', onClick: closeModal },
      {
        label: '保存理解', cls: 'primary', onClick: async () => {
          const content = $('#fyText').value.trim();
          if (!content) { toast('先写点什么吧', 'err'); return; }
          try {
            await api('/api/notes', {
              method: 'POST',
              body: { deck_id: S.deck?.id, section_id: section.id, kind: 'feynman', content },
            });
            closeModal(); toast('已保存，以后可以回看自己的解释', 'ok');
          } catch (e) { toast(e.message, 'err'); }
        },
      },
    ],
  });
}

/* ============================================================
   复习
   ============================================================ */
async function loadReviewView() {
  const sel = $('#reviewDeck');
  const opts = ['<option value="">全部讲义</option>']
    .concat(S.decks.map((d) => `<option value="${d.id}">${esc(d.title)}</option>`));
  sel.innerHTML = opts.join('');
  const ov = S.overview || await api('/api/today');
  $('#reviewPlaceholder').innerHTML = (ov.due + ov.new_available) === 0
    ? '<div class="big-emoji">✓</div><p>今天没有需要复习的卡片了</p><p class="muted">可以去「讲义库」导入新内容，或做一次随堂测</p>'
    : `<div class="big-emoji">◧</div>
       <p>${ov.due} 张到期 · ${Math.max(0, ov.new_limit - ov.new_today)} 张新卡可选</p>
       <p class="muted">快捷键：空格翻面 · 1~4 评分 · Esc 退出</p>`;
}

async function startReview(deckId = null) {
  const sel = $('#reviewDeck');
  const id = deckId ?? (sel && sel.value ? Number(sel.value) : null);
  try {
    const r = await api(`/api/review/queue?limit=30${id ? '&deck_id=' + id : ''}`);
    if (!r.cards.length) { toast('这会儿没有要复习的卡片', 'err'); return; }
    S.queue = r.cards; S.qi = 0;
    S.rvStats = { n: 0, good: 0, ms: 0, neu: 0 };
    $('#reviewOverlay').classList.remove('hidden');
    $('#rvDone').classList.add('hidden');
    $('#rvStage').classList.remove('hidden');
    showCard();
  } catch (e) { toast(e.message, 'err'); }
}

function showCard() {
  const c = S.queue[S.qi];
  if (!c) { finishReview(); return; }
  S.revealed = false; S.conf = null; S.cardT0 = Date.now();
  const total = S.queue.length;
  $('#rvProgress').style.width = `${(S.qi / total) * 100}%`;
  $('#rvCounter').textContent = `${S.qi + 1} / ${total}`;
  $('#rvDeck').textContent = [c.deck_title, c.section_title].filter(Boolean).join(' · ');
  const badge = $('#rvBadge');
  badge.className = 'rev-badge ' + (c.state === 'new' ? 'new' : '');
  badge.textContent = c.state === 'new' ? '新卡' : c.state === 'relearning' ? '重学' : '复习';

  $('#rvFront').innerHTML = renderCloze(c.front) +
    (c.image_path ? `<div><img src="/${esc(c.image_path)}" alt=""></div>` : '');
  $('#rvInputWrap').classList.remove('hidden');
  $('#rvInput').value = '';
  $$('.chip.conf').forEach((b) => b.classList.remove('chosen'));
  $('#rvBack').classList.add('hidden');
  $('#rvBack').innerHTML = '';
  $('#rvNote').classList.add('hidden');
  $('#rvRating').classList.add('hidden');
  $('#rvReveal').classList.remove('hidden');
}

function revealCard() {
  if (S.revealed) return;
  const c = S.queue[S.qi];
  if (!c) return;
  S.revealed = true;
  $('#rvBack').innerHTML = renderCloze(c.back);
  $('#rvBack').classList.remove('hidden');
  if (c.note) { $('#rvNote').textContent = c.note; $('#rvNote').classList.remove('hidden'); }
  if (c.image_path) {
    $('#rvBack').insertAdjacentHTML('beforeend', `<div><img class="rv-image" src="/${esc(c.image_path)}" alt=""></div>`);
  }
  $('#rvReveal').classList.add('hidden');
  $('#rvInputWrap').classList.add('hidden');
  const r = $('#rvRating');
  r.classList.remove('hidden');
  const iv = c.intervals || {};
  $$('.rate', r).forEach((b) => {
    const k = b.dataset.rate;
    const t = iv[k] ? iv[k].text : '';
    b.querySelector('i').textContent = t;
  });
}

async function rateCard(rating) {
  const c = S.queue[S.qi];
  if (!c) return;
  const elapsed = Date.now() - S.cardT0;
  try {
    const res = await api('/api/review', {
      method: 'POST',
      body: { card_id: c.id, rating, confidence: S.conf, elapsed_ms: elapsed },
    });
    S.rvStats.n += 1;
    S.rvStats.ms += elapsed;
    if (rating >= 3) S.rvStats.good += 1;
    if (c.state === 'new') S.rvStats.neu += 1;
    S.qi += 1;
    if (S.qi >= S.queue.length) finishReview();
    else showCard();
    // 每 10 张刷新一次今天的统计，保持侧栏数字新鲜
    if (S.qi % 10 === 0) api('/api/today').then((ov) => { S.overview = ov; }).catch(() => {});
  } catch (e) { toast(e.message, 'err'); }
}

function finishReview() {
  $('#rvStage').classList.add('hidden');
  $('#rvDone').classList.remove('hidden');
  $('#rvProgress').style.width = '100%';
  const st = S.rvStats;
  const acc = st.n ? Math.round((st.good / st.n) * 100) : 0;
  const mins = Math.max(1, Math.round(st.ms / 60000));
  $('#rvCounter').textContent = `${st.n} / ${st.n}`;
  $('#rvDoneTitle').textContent = st.n ? '这一轮完成了' : '没有卡片要复习';
  $('#rvDoneStats').innerHTML = st.n ? `
    <div class="done-stat"><b>${st.n}</b><span>张卡片</span></div>
    <div class="done-stat"><b>${acc}%</b><span>一次答对</span></div>
    <div class="done-stat"><b>${st.neu}</b><span>张新卡</span></div>
    <div class="done-stat"><b>${mins}</b><span>分钟</span></div>` : '';
  $('#rvDoneHint').textContent = st.n
    ? (acc >= 80 ? '状态很好。现在别再加量了，去散散步，让睡眠把它固化下来。'
      : '记忆的进步常常发生在「有点吃力」之后。明天再来一轮就够了。')
    : '今天的到期卡片都清空了。';
  api('/api/today').then((ov) => { S.overview = ov; renderToday(); }).catch(() => {});
}

function exitReview() {
  $('#reviewOverlay').classList.add('hidden');
  if (S.view === 'today') loadToday().catch(() => {});
}

/* ============================================================
   随堂测
   ============================================================ */
async function loadQuizView() {
  const sel = $('#quizDeck');
  sel.innerHTML = ['<option value="">全部讲义</option>']
    .concat(S.decks.map((d) => `<option value="${d.id}">${esc(d.title)}</option>`)).join('');
}

async function startQuiz() {
  const sel = $('#quizDeck');
  const id = sel.value || '';
  try {
    const r = await api(`/api/quiz?n=10${id ? '&deck_id=' + id : ''}`);
    S.quiz = { items: r.items, idx: 0, answers: [], deckId: r.deck_id };
    renderQuizItem();
  } catch (e) { toast(e.message, 'err'); }
}

function renderQuizItem() {
  const q = S.quiz;
  if (!q || q.idx >= q.items.length) { return renderQuizResult(); }
  const item = q.items[q.idx];
  const body = $('#quizBody');
  const head = `<div class="q-index">第 ${q.idx + 1} / ${q.items.length} 题 · ${item.type === 'choice' ? '选择题' : '填空'}</div>`;
  if (item.type === 'choice') {
    body.innerHTML = `<div class="card quiz-card">
      ${head}
      <div class="q-text">${renderCloze(item.question)}</div>
      <div class="options" id="quizOptions">
        ${item.options.map((o, i) => `<button class="option" data-opt="${esc(o)}">${String.fromCharCode(65 + i)}. ${esc(o)}</button>`).join('')}
      </div>
      <div id="quizFb"></div>
    </div>`;
    $$('#quizOptions .option').forEach((b) => {
      b.onclick = () => {
        const picked = b.dataset.opt;
        const ok = picked === item.answer;
        $$('#quizOptions .option').forEach((x) => {
          x.disabled = true;
          if (x.dataset.opt === item.answer) x.classList.add('correct');
          else if (x === b) x.classList.add('wrong');
        });
        $('#quizFb').innerHTML = `<div class="feedback ${ok ? 'ok' : 'no'}">${ok ? '答对了。' : '正确答案：' + esc(item.answer) + '。'}
          <button class="link" id="quizNext">下一题 →</button></div>`;
        q.answers.push({ card_id: item.card_id, ok });
        $('#quizNext').onclick = () => { q.idx += 1; renderQuizItem(); };
      };
    });
  } else {
    body.innerHTML = `<div class="card quiz-card">
      ${head}
      <div class="q-text">${renderCloze(item.question)}</div>
      <div class="quiz-input">
        <input id="quizFill" placeholder="写下你的答案" autocomplete="off">
        <button class="btn primary" id="quizSubmit">提交</button>
      </div>
      <div id="quizFb"></div>
    </div>`;
    const submit = () => {
      const val = $('#quizFill').value.trim();
      if (!val) return;
      const ok = val === item.answer.trim() ||
        (item.answer.length > 2 && (item.answer.includes(val) || val.includes(item.answer)));
      $('#quizFill').disabled = true;
      $('#quizSubmit').disabled = true;
      $('#quizFb').innerHTML = `<div class="feedback ${ok ? 'ok' : 'no'}">${ok ? '答对了。' : '参考答案：' + esc(item.answer)}
        <button class="link" id="quizNext">下一题 →</button></div>`;
      q.answers.push({ card_id: item.card_id, ok });
      $('#quizNext').onclick = () => { q.idx += 1; renderQuizItem(); };
    };
    $('#quizSubmit').onclick = submit;
    $('#quizFill').onkeydown = (e) => { if (e.key === 'Enter') submit(); };
    setTimeout(() => $('#quizFill')?.focus(), 60);
  }
}

async function renderQuizResult() {
  const q = S.quiz;
  const correct = q.answers.filter((a) => a.ok).length;
  const total = q.answers.length || 1;
  const pct = Math.round((correct / total) * 100);
  $('#quizBody').innerHTML = `<div class="card quiz-card quiz-result">
    <div class="score-big">${pct}<span style="font-size:22px">%</span></div>
    <p>${correct} / ${total} 题正确</p>
    <p class="hint">${pct >= 80 ? '掌握得不错。错题已经反馈给算法，下次会更早出现。'
      : '别灰心：答错后的即时反馈，本身就是记忆效果最好的一次学习。'}</p>
    <div class="row center">
      <button class="btn primary" id="quizAgain">再来一组</button>
      <button class="btn ghost" id="quizToReview">去复习卡片</button>
    </div>
  </div>`;
  $('#quizAgain').onclick = startQuiz;
  $('#quizToReview').onclick = () => go('review');
  try {
    await api('/api/quiz/log', {
      method: 'POST',
      body: { deck_id: q.deckId, total: q.answers.length, correct, detail: q.answers },
    });
  } catch { /* 记录失败不影响体验 */ }
}

/* ============================================================
   学习数据
   ============================================================ */
async function loadStats() {
  const st = await api('/api/stats?days=112');
  S.stats = st;
  const s = st.streak || {};
  $('#statTiles').innerHTML = `
    <div class="stat-tile"><b>${st.total_reviews || 0}</b><span>累计复习次数</span></div>
    <div class="stat-tile"><b>${Math.round((st.total_minutes || 0) / 60 * 10) / 10}</b><span>累计学习小时</span></div>
    <div class="stat-tile"><b>${s.streak || 0}</b><span>当前连续天数</span></div>
    <div class="stat-tile"><b>${s.best || 0}</b><span>最长连续天数</span></div>
    <div class="stat-tile"><b>${st.retention_30d == null ? '—' : Math.round(st.retention_30d * 100) + '%'}</b><span>近 30 天正确率</span></div>`;

  renderHeat($('#heatmap'), st.heatmap || [], true);

  // 保持率曲线
  const curve = (st.retention_curve || []).filter((p) => p.retention != null);
  const box = $('#retentionChart');
  if (curve.length < 2) {
    box.innerHTML = '<p class="hint">学几张卡之后，这里会出现「未来 30 天记忆衰减」曲线。</p>';
  } else {
    const W = 600, H = 170, pad = 26;
    const xs = (i) => pad + (i / (curve.length - 1)) * (W - pad * 2);
    const ys = (v) => H - pad - v * (H - pad * 2);
    const pts = curve.map((p, i) => `${xs(i).toFixed(1)},${ys(p.retention).toFixed(1)}`);
    const area = `M${xs(0)},${H - pad} L${pts.join(' L')} L${xs(curve.length - 1)},${H - pad} Z`;
    box.innerHTML = `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
      <defs><linearGradient id="rg" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#6c8cff" stop-opacity=".45"/>
        <stop offset="100%" stop-color="#6c8cff" stop-opacity="0"/>
      </linearGradient></defs>
      ${[0.25, 0.5, 0.75, 1].map((v) => `<line x1="${pad}" y1="${ys(v)}" x2="${W - pad}" y2="${ys(v)}" stroke="#232c3d" stroke-dasharray="3 4"/>`).join('')}
      <path d="${area}" fill="url(#rg)"/>
      <polyline points="${pts.join(' ')}" fill="none" stroke="#6c8cff" stroke-width="2.2" stroke-linejoin="round"/>
      <text x="${pad}" y="${H - 6}" fill="#7d8899" font-size="10">今天</text>
      <text x="${W - pad - 34}" y="${H - 6}" fill="#7d8899" font-size="10">30 天后</text>
      <text x="4" y="${ys(1) + 4}" fill="#7d8899" font-size="10">100%</text>
      <text x="8" y="${ys(0.5) + 4}" fill="#7d8899" font-size="10">50%</text>
    </svg>`;
  }

  // 元认知
  const cal = st.calibration || [];
  const colors = { 1: 'var(--rose)', 2: 'var(--amber)', 3: 'var(--mint)' };
  $('#calibChart').innerHTML = cal.map((c) => `
    <div class="calib-row">
      <span class="muted">${esc(c.label)}</span>
      <div class="calib-bar"><i style="width:${c.actual == null ? 0 : Math.round(c.actual * 100)}%;background:${colors[c.confidence]}"></i></div>
      <span>${c.actual == null ? '—' : Math.round(c.actual * 100) + '%'}<span class="muted"> ·${c.n}</span></span>
    </div>`).join('') + (st.overconfidence > 0.25
      ? `<p class="hint" style="color:var(--amber)">你在「很有把握」时的错误率是 ${Math.round(st.overconfidence * 100)}%，说明熟悉感正在骗你。多用自己的话解释一遍。</p>` : '');

  // 时段
  const hours = st.hour_hist || [];
  const hmax = Math.max(1, ...hours);
  $('#hourHist').innerHTML = hours.map((v, i) => `<i style="height:${Math.round((v / hmax) * 100)}%" title="${i} 点：${v} 次"></i>`).join('');
  $('#peakHour').textContent = st.peak_hour == null ? '' : `最常学习：${st.peak_hour} 点前后`;

  // 讲义表
  $('#deckTable').innerHTML = `
    <thead><tr><th>讲义</th><th>卡片</th><th>未学</th><th>已学</th><th>正确率</th></tr></thead>
    <tbody>${(st.decks || []).map((d) => `
      <tr><td>${esc(d.title)}</td><td>${d.cards}</td><td>${d.new}</td><td>${d.review}</td>
      <td>${d.retention == null ? '—' : Math.round(d.retention * 100) + '%'}</td></tr>`).join('')
      || '<tr><td colspan="5" class="muted">还没有讲义</td></tr>'}</tbody>`;

  loadNotes();
}

async function loadNotes() {
  const box = $('#noteList');
  if (!box) return;
  try {
    const notes = await api('/api/notes');
    if (!notes.length) {
      box.innerHTML = '<p class="hint">还没有笔记。打开任一讲义的某一页，点「费曼：讲给小孩听」写一段试试。</p>';
      return;
    }
    box.innerHTML = notes.map((n) => `
      <div class="note-item" data-id="${n.id}">
        <div class="note-head">
          <span class="note-kind ${n.kind === 'feynman' ? 'fy' : ''}">${n.kind === 'feynman' ? '费曼' : '笔记'}</span>
          <span class="muted">${esc((n.created_at || '').replace('T', ' ').slice(0, 16))}</span>
          ${n.deck_id ? `<span class="muted">· 讲义 #${n.deck_id}</span>` : ''}
          <button class="link note-del">删除</button>
        </div>
        <p>${esc(n.content)}</p>
      </div>`).join('');
    $$('#noteList .note-del').forEach((b) => {
      b.onclick = async () => {
        const id = Number(b.closest('.note-item').dataset.id);
        try { await api('/api/notes/' + id, { method: 'DELETE' }); loadNotes(); }
        catch (e) { toast(e.message, 'err'); }
      };
    });
  } catch (e) { box.innerHTML = `<p class="hint">${esc(e.message)}</p>`; }
}

/* ============================================================
   AI 逐页讲解：老师视角讲课（英文自动译成中文）→ 只把核心考点草拟复习卡 → 勾选导入
   ============================================================ */
const WORTH_META = {
  high: { label: '核心内容', cls: 'worth-high', icon: '🔥' },
  mid: { label: '背景补充', cls: 'worth-mid', icon: '💡' },
  low: { label: '非核心', cls: 'worth-low', icon: '📑' },
};
const MAX_AN_CARDS = 3; // 与后端 analyze.MAX_CARDS 一致：每页最多 3 张复习卡草案

async function openAnalysis(opts = {}) {
  if (!S.deck) return;
  if (!S.aiStatus) { try { S.aiStatus = await api('/api/ai/providers'); } catch (e) { toast(e.message, 'err'); return; } }
  if (!S.aiStatus.effective) { toast('还没配置 AI 引擎：到「设置」填直连 Key，或装好本机 dsh 后选「自动」', 'err'); return; }
  const secs = (S.deck.sections || []).filter((s) => (s.text || '').trim());
  if (!secs.length) { toast('这份讲义没有可讲解的文本页', 'err'); return; }
  S.analysis = { analyses: [], running: false, stop: false, eng: '' };
  try {
    const r = await api(`/api/decks/${S.deck.id}/analysis`);
    S.analysis.analyses = r.analyses || [];
  } catch (e) { /* 无记录也继续 */ }
  openModal({
    title: `AI 逐页讲解 · ${S.deck.title}`,
    body: renderAnalysisBody(),
    foot: [
      { label: '开始 / 继续讲解', cls: 'primary', onClick: runAnalysis },
      { label: '停止', cls: 'ghost', id: 'anStopBtn', onClick: () => { S.analysis.stop = true; } },
      { label: '重新讲解全部', cls: 'ghost', onClick: async () => {
          if (!confirm('重新讲解会用 AI 覆盖这份讲义的全部分析页（已导入的卡片不受影响）。继续？')) return;
          try { await api(`/api/decks/${S.deck.id}/analysis/clear`, { method: 'POST' }); } catch (e) { toast(e.message, 'err'); return; }
          S.analysis.analyses = [];
          renderAnalysisList();
        } },
      { label: '关闭', cls: 'ghost', onClick: closeModal },
    ],
  });
  renderAnalysisList();
  const impBtn = $('#anImportBtn');
  if (impBtn) impBtn.onclick = importAnalysisCards;
  const sb = $('#anStopBtn'); if (sb) sb.classList.add('hidden');
  if (opts.auto) runAnalysis();
}

function renderAnalysisBody() {
  return `
    <p class="hint" id="anEngine">引擎：${S.aiStatus?.effective ? S.aiStatus.effective : '未检测到可用引擎'}</p>
    <div id="anProgress" class="an-progress hidden">
      <div class="an-bar"><i id="anBarFill"></i></div>
      <span class="muted" id="anProgText"></span>
    </div>
    <div id="anList" class="an-list"></div>
    <div id="anFoot" class="an-foot hidden">
      <label class="an-select-all"><input type="checkbox" id="anCheckAll" checked> 全选</label>
      <button class="btn primary sm" id="anImportBtn">导入勾选的复习卡</button>
      <span class="muted" id="anImportState"></span>
    </div>`;
}

function anaCounts() {
  let cards = 0, pages = 0;
  for (const a of S.analysis.analyses) {
    if (a.worth !== 'low' && a.cards?.length) { cards += a.cards.length; pages++; }
  }
  return { cards, pages };
}

function renderAnalysisList() {
  const list = $('#anList');
  if (!list) return;
  const done = new Set(S.analysis.analyses.map((a) => a.section_id));
  const pend = (S.deck.sections || []).filter((s) => (s.text || '').trim() && !done.has(s.id));
  if (!S.analysis.analyses.length && !S.analysis.running) {
    list.innerHTML = '<p class="hint">AI 会像老师一样逐页给你讲解：这一页在讲什么、为什么重要、怎么理解'
      + '（英文术语自动翻成中文）。只有碰到本课必须长期记住的核心考点时，才会顺便草拟一两张复习卡'
      + '——与课程主线无关的内容（行业新闻、政策背景之类）一律不制卡。讲解为主，卡片宁缺毋滥；'
      + '你觉得有价值的卡片勾选后统一导入。</p>';
    return;
  }
  const rows = S.analysis.analyses.map((a) => {
    const w = WORTH_META[a.worth] || WORTH_META.mid;
    const teaching = a.teaching || a.summary || '';
    const concepts = (a.concepts || []).map((c) =>
      `<span class="concept-chip" title="${esc(c.why || '')}">${esc(c.term)}${c.en ? `（${esc(c.en)}）` : ''} ${'★'.repeat(c.importance || 1)}</span>`).join('');
    const draftCards = (a.cards || []).map((c) => `
      <label class="draft-card">
        <input type="checkbox" data-draft="${a.id}" checked>
        <div>
          <div class="draft-front"><span class="src">${c.kind === 'cloze' ? '填空' : '问答'}</span>${renderCloze(c.front)}</div>
          <div class="draft-back">${renderCloze(esc(c.back))}</div>
          ${c.note ? `<div class="draft-note">💬 ${esc(c.note)}</div>` : ''}
        </div>
      </label>`).join('');
    return `
      <div class="an-item" data-w="${a.worth}">
        <div class="an-head">
          <span class="worth-badge ${w.cls}">${w.icon} ${w.label}</span>
          <span class="an-title">第 ${a.section_idx + 1} 页 · ${esc(a.section_title || '')}</span>
          ${a.imported ? `<span class="src">已导入 ${a.imported} 张</span>` : ''}
        </div>
        <div class="an-teaching">${esc(teaching)}</div>
        ${a.reason ? `<div class="an-reason">判断依据：${esc(a.reason)}</div>` : ''}
        ${concepts ? `<div class="an-concepts">${concepts}</div>` : ''}
        ${draftCards.length ? `<div class="an-cards"><div class="an-cards-head">🎯 复习卡草案 ${a.cards.length} 张（仅供复习，可编辑）</div>${draftCards}</div>`
          : '<div class="an-nocards">🚫 本页不制卡：非本课核心考点或与主线无关</div>'}
        <div class="an-time muted">${esc((a.engine || '').split(':')[0])} · ${esc(a.created_at || '')}</div>
      </div>`;
  }).join('');
  const pendTxt = pend.length && !S.analysis.running
    ? `<p class="hint">还有 ${pend.length} 页未讲解。</p>` : '';
  list.innerHTML = rows + pendTxt;
  const foot = $('#anFoot');
  if (foot) foot.classList.toggle('hidden', !S.analysis.analyses.length);
  $('#anCheckAll')?.addEventListener('change', (e) => {
    $$('#anList input[data-draft]').forEach((cb) => { cb.checked = e.target.checked; });
    updateImportState();
  });
  $$('#anList input[data-draft]').forEach((cb) => {
    cb.addEventListener('change', updateImportState);
  });
  updateImportState();
}

function updateImportState() {
  const cbs = $$('#anList input[data-draft]');
  const n = cbs.filter((c) => c.checked).length;
  const st = $('#anImportState');
  if (st) st.textContent = `已勾选 ${n} 张卡片草案`;
}

async function runAnalysis() {
  if (!S.deck || S.analysis.running) return;
  const secs = (S.deck.sections || []).filter((s) => (s.text || '').trim());
  const done = new Set(S.analysis.analyses.map((a) => a.section_id));
  const todo = secs.filter((s) => !done.has(s.id));
  if (!todo.length) { toast('所有页都已分析，可重新分析或直接导入', 'ok'); return; }
  S.analysis.running = true;
  S.analysis.stop = false;
  const stopBtn = $('#anStopBtn'); if (stopBtn) stopBtn.classList.remove('hidden');
  const prog = $('#anProgress'), fill = $('#anBarFill'), txt = $('#anProgText');
  prog.classList.remove('hidden');
  let i = 0;
  for (const sec of todo) {
    if (S.analysis.stop) break;
    i++;
    txt.textContent = `正在讲解第 ${sec.idx + 1}/${secs.length} 页 · ${sec.title || ''}（约需几秒到 1 分钟）`;
    fill.style.width = `${Math.round(i / todo.length * 100)}%`;
    try {
      const r = await api(`/api/decks/${S.deck.id}/analyze`, {
        method: 'POST', body: { section_id: sec.id, max_cards: MAX_AN_CARDS } });
      S.analysis.analyses.push(r);
      renderAnalysisList();
    } catch (e) {
      txt.textContent = `第 ${sec.idx + 1} 页失败：${e.message}`;
      toast(`第 ${sec.idx + 1} 页讲解失败，已跳过`, 'err');
      S.analysis.analyses.push({ section_id: sec.id, worth: 'low',
        teaching: `讲解失败：${e.message}（可点击底部「重新讲解全部」再试）`, reason: '', concepts: [], cards: [],
        section_idx: sec.idx, section_title: sec.title, engine: '', imported: 0 });
      renderAnalysisList();
    }
  }
  S.analysis.running = false;
  prog.classList.add('hidden');
  const stopBtnEnd = $('#anStopBtn'); if (stopBtnEnd) stopBtnEnd.classList.add('hidden');
  if (S.analysis.stop) {
    const taught = S.analysis.analyses.filter((a) => a.section_id !== undefined).length;
    toast(`已停止：共讲解 ${taught}/${secs.length} 页。剩余页面随时可点「继续讲解」`, 'ok');
    S.analysis.stop = false;
    return;
  }
  const counts = anaCounts();
  toast(`讲解完成：${todo.length} 页 → 其中 ${counts.pages} 页有值得制卡的考点，共 ${counts.cards} 张卡片草案`, 'ok');
}

async function importAnalysisCards() {
  const cbs = $$('#anList input[data-draft]');
  const ids = [...cbs].filter((c) => c.checked).map((c) => Number(c.dataset.draft));
  if (!ids.length) { toast('先勾选要导入的卡片草案', 'err'); return; }
  try {
    const r = await api(`/api/decks/${S.deck.id}/analysis/import`, {
      method: 'POST', body: { analysis_ids: ids } });
    toast(`已导入 ${r.imported} 张 AI 卡片，会进入今日新卡队列`, 'ok');
    closeModal();
    loadDeckCards();
    refreshDeckStats();
  } catch (e) { toast(e.message, 'err'); }
}

/* ============================================================
   设置
   ============================================================ */
async function loadSettings() {
  const s = S.settings;
  if (!s || !Object.keys(s).length) S.settings = await api('/api/settings');
  const st = S.settings;
  $('#setNewLimit').value = st.daily_new_limit ?? 20;
  $('#setGoalMin').value = st.daily_goal_minutes ?? 30;
  $('#setRetention').value = st.desired_retention ?? 0.9;
  $('#setMaxInterval').value = st.max_interval_days ?? 365;
  $('#setAiMode').value = st.ai_mode || 'auto';
  $('#setAiBase').value = st.ai_base || '';
  $('#setAiKey').value = st.ai_key === '******' ? '' : (st.ai_key || '');
  $('#setAiKey').placeholder = st.ai_key === '******' ? '已设置（留空则不修改）' : 'sk-...';
  $('#setAiModel').value = st.ai_model || 'deepseek-chat';
  $('#aiState').textContent = S.aiReady ? 'AI 已启用' : '未配置 AI（可选）';
  refreshProviders();
}

async function refreshProviders() {
  try {
    const st = S.aiStatus = await api('/api/ai/providers');
    const dshEl = $('#dshState');
    if (dshEl) {
      if (st.dsh.available) dshEl.textContent = `本机检测到 DeepSeek Harness（${st.dsh.node}），可直接用 dsh 引擎。`;
      else dshEl.textContent = '未检测到本机 dsh（DeepSeek Harness）。用 dsh 引擎需先装好 dsh：github.com/deepseek-ai/deepseek-harness';
    }
    if (st.effective) $('#aiState').textContent = `当前生效引擎：${st.effective === 'api' ? st.api.model : '本机 dsh'}`;
    else $('#aiState').textContent = '未配置任何可用引擎';
  } catch (e) { /* 忽略，未到关键路径 */ }
}

async function saveSettings() {
  const body = {
    daily_new_limit: Number($('#setNewLimit').value) || 20,
    daily_goal_minutes: Number($('#setGoalMin').value) || 30,
    desired_retention: Number($('#setRetention').value) || 0.9,
    max_interval_days: Number($('#setMaxInterval').value) || 365,
    ai_mode: $('#setAiMode').value || 'auto',
    ai_base: $('#setAiBase').value.trim(),
    ai_model: $('#setAiModel').value.trim() || 'deepseek-chat',
  };
  const key = $('#setAiKey').value.trim();
  if (key) body.ai_key = key;
  try {
    const r = await api('/api/settings', { method: 'PUT', body });
    S.settings = r.settings;
    toast('设置已保存', 'ok');
    const b = await api('/api/bootstrap');
    S.aiReady = b.ai_ready;
    await refreshProviders();
  } catch (e) { toast(e.message, 'err'); }
}

async function exportBackup() {
  try {
    const data = await api('/api/export');
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `拾知备份_${localDate()}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
    toast('备份文件已下载（JSON，含所有卡片与记录）', 'ok');
  } catch (e) { toast(e.message, 'err'); }
}

/* ============================================================
   方法论弹窗 & 全局事件
   ============================================================ */
function showMethod() {
  openModal({
    title: '这个软件为什么这样设计',
    body: `<p class="hint">每个功能都对应一条被反复验证的学习科学结论，而不是拍脑袋加的。</p>
      <table class="method-table">${METHOD.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join('')}</table>
      <p class="hint" style="margin-top:14px">核心算法是 FSRS-4.5（Free Spaced Repetition Scheduler）：它用「稳定性」和「难度」两个参数描述每张卡，再用遗忘曲线 R = (1 + t/S)^-0.5 估算当前还记得的概率，据此决定下一次见面的时间。</p>`,
    foot: [{ label: '知道了', cls: 'primary', onClick: closeModal }],
  });
}

function bindGlobal() {
  $$('.nav-item').forEach((b) => { b.onclick = () => go(b.dataset.nav); });
  $$('[data-goto]').forEach((b) => { b.onclick = () => go(b.dataset.goto); });

  $('#checkinBtn').onclick = doCheckin;
  $('#makeupBtn').onclick = doMakeup;
  $('#btnMethod').onclick = showMethod;
  $('#methodBtn2').onclick = showMethod;
  $('#startReview').onclick = () => startReview();
  $('#beginReview').onclick = () => startReview();
  $('#sleepBtn').onclick = async () => {
    toast('睡前巩固：只复习 15 张，不再学新的', 'ok');
    startReview();
  };

  $('#timerBtn').onclick = toggleFocus;
  $$('.timer-modes button').forEach((b) => {
    b.onclick = () => {
      if (S.focus.running) { toast('先暂停当前计时', 'err'); return; }
      $$('.timer-modes button').forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
      S.focus.total = Number(b.dataset.min) * 60;
      S.focus.left = S.focus.total;
      renderTimer();
    };
  });

  // 上传
  const dz = $('#dropzone'), fi = $('#fileInput');
  dz.onclick = () => fi.click();
  fi.onchange = () => { uploadFiles(fi.files); fi.value = ''; };
  ['dragenter', 'dragover'].forEach((ev) => dz.addEventListener(ev, (e) => {
    e.preventDefault(); dz.classList.add('drag');
  }));
  ['dragleave', 'drop'].forEach((ev) => dz.addEventListener(ev, (e) => {
    e.preventDefault(); dz.classList.remove('drag');
  }));
  dz.addEventListener('drop', (e) => uploadFiles(e.dataTransfer.files));

  // 讲义库
  $('#backToLib').onclick = () => {
    S.deck = null;
    $('#deckDetail').classList.add('hidden');
    $('#deckGrid').classList.remove('hidden');
    $('#dropzone').classList.remove('hidden');
    loadLibrary();
  };
  $('#aiAnalyzeBtn').onclick = () => openAnalysis();
  $('#feynmanBtn').onclick = () => feynman({ text: (S.deck?.sections || []).map((s) => s.text).join('\n').slice(0, 800) });
  $('#askBtn').onclick = askDeck;
  $('#askInput').onkeydown = (e) => { if (e.key === 'Enter') askDeck(); };
  $('#delDeckBtn').onclick = async () => {
    if (!S.deck) return;
    if (!confirm(`删除「${S.deck.title}」及其所有卡片？`)) return;
    try {
      await api('/api/decks/' + S.deck.id, { method: 'DELETE' });
      toast('已删除', 'ok');
      $('#backToLib').click();
    } catch (e) { toast(e.message, 'err'); }
  };
  $$('.mini-actions .chip').forEach((b) => {
    b.onclick = () => {
      $$('.mini-actions .chip').forEach((x) => x.classList.remove('active'));
      b.classList.add('active');
      S.deckFilter = b.dataset.filter;
      loadDeckCards();
    };
  });

  // 复习层
  $('#rvReveal').onclick = revealCard;
  $('#rvExit').onclick = exitReview;
  $('#rvToToday').onclick = () => { exitReview(); go('today'); };
  $('#rvAgain').onclick = () => {
    $('#reviewOverlay').classList.add('hidden');
    startReview(S.view === 'review' && $('#reviewDeck').value ? Number($('#reviewDeck').value) : null);
  };
  $$('.rate').forEach((b) => { b.onclick = () => rateCard(Number(b.dataset.rate)); });
  $$('.chip.conf').forEach((b) => {
    b.onclick = () => {
      $$('.chip.conf').forEach((x) => x.classList.remove('chosen'));
      b.classList.add('chosen');
      S.conf = Number(b.dataset.conf);
    };
  });

  // 测验
  $('#startQuiz').onclick = startQuiz;

  // 设置
  $('#saveSettings').onclick = saveSettings;
  $('#testAi').onclick = async () => {
    $('#aiState').textContent = '测试中…（dsh 引擎首次冷启动可能需数秒）';
    try {
      await saveSettings();
      const mode = $('#setAiMode').value || 'auto';
      const r = await api('/api/ai/test', { method: 'POST', body: { mode } });
      $('#aiState').textContent = `连接成功（引擎：${r.engine === 'dsh' ? '本机 dsh' : r.engine}）：${r.reply || 'ok'}`;
      toast('AI 连接成功', 'ok');
    } catch (e) {
      $('#aiState').textContent = '失败：' + e.message;
      toast(e.message, 'err');
    }
  };
  $('#exportBtn').onclick = $('#exportBtn2').onclick = exportBackup;

  // 弹窗
  $('#modalClose').onclick = closeModal;
  $('#modalBackdrop').onclick = (e) => { if (e.target.id === 'modalBackdrop') closeModal(); };

  // 键盘
  document.addEventListener('keydown', (e) => {
    const overlayOpen = !$('#reviewOverlay').classList.contains('hidden');
    if (e.key === 'Escape') {
      if (!$('#modalBackdrop').classList.contains('hidden')) closeModal();
      else if (overlayOpen) exitReview();
      return;
    }
    if (!overlayOpen) return;
    const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName);
    if (e.key === ' ' && !typing) { e.preventDefault(); if (!S.revealed) revealCard(); else return; }
    if (/^[1-4]$/.test(e.key) && S.revealed && !typing) { e.preventDefault(); rateCard(Number(e.key)); }
  });

  window.addEventListener('hashchange', () => {
    const v = location.hash.slice(1);
    if (v && v !== S.view && VIEW_LOADERS[v]) go(v);
  });
}

/* ============================================================
   启动
   ============================================================ */
async function boot() {
  bindGlobal();
  try {
    const b = await api('/api/bootstrap');
    S.settings = b.settings; S.overview = b.overview;
    S.decks = b.decks || []; S.aiReady = b.ai_ready;
  } catch (e) {
    toast('无法连接后端：' + e.message, 'err');
    return;
  }
  const initial = location.hash.slice(1);
  await go(VIEW_LOADERS[initial] ? initial : 'today');
}

document.addEventListener('DOMContentLoaded', boot);