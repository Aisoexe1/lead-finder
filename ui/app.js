'use strict';

/* ── Состояние ──────────────────────────────────────────────────────────── */

const S = {
  page: 'search',
  leads: [],
  current: null,
  status: '',
  counts: {},
  hasKey: false,
  logLen: 0,
  polling: null,
  busy: false,
  sources: [],
  outcomes: [],
  funnel: {},
};

const STATUSES = [
  { key: 'due',      label: 'На сегодня' },
  { key: 'written',  label: 'Готовы' },
  { key: 'kept',     label: 'Отобраны' },
  { key: 'verified', label: 'Ждут отсева' },
  { key: 'new',      label: 'Не проверены' },
  { key: 'dropped',  label: 'Отсеяны' },
  { key: '',         label: 'Все' },
];

const EMPTY = {
  due:      ['На сегодня никого', 'Сюда попадают те, кому пора отправить второе сообщение.'],
  written:  ['Сообщений пока нет', 'Пройди шаги на экране «Поиск»: найти, проверить, отсеять, написать.'],
  kept:     ['Отобранных нет', 'Запусти отсев мусора на экране «Поиск».'],
  verified: ['Нечего проверять', 'Сначала найди лиды на экране «Поиск».'],
  new:      ['Новых нет', 'Запусти поиск на экране «Поиск».'],
  dropped:  ['Отсеянных нет', 'Сюда попадёт то, что модель или ты сам уберёте из списка.'],
  '':       ['База пуста', 'Начни с экрана «Поиск»: укажи нишу, город и нажми запуск.'],
};

const MODES = [
  ['hybrid',   'Мой шаблон, модель подгоняет под каждого',
   'Текст остаётся твой, но перестаёт выглядеть рассылкой. Обычно нужен этот.'],
  ['template', 'Только мой шаблон, без ИИ',
   'Подставляются плейсхолдеры. Мгновенно и бесплатно, но все письма одинаковые.'],
  ['ai',       'Модель пишет сама',
   'Шаблон не используется, модель опирается на задачу, стиль и примеры.'],
];

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const esc = (s) => String(s == null ? '' : s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;');

/* ── Мост ───────────────────────────────────────────────────────────────── */

async function call(method, ...args) {
  try {
    const res = await window.pywebview.api[method](...args);
    if (res && res.ok === false && res.error && res.error !== 'no_key') {
      toast(res.error, 'bad');
    }
    return res || {};
  } catch (err) {
    toast('Сбой вызова ' + method + ': ' + err, 'bad');
    return { ok: false, error: String(err) };
  }
}

/* ── Мелочи интерфейса ──────────────────────────────────────────────────── */

function toast(text, kind = '') {
  const el = document.createElement('div');
  el.className = 'toast ' + kind;
  el.textContent = text;
  $('#toasts').append(el);
  setTimeout(() => {
    el.classList.add('out');
    setTimeout(() => el.remove(), 200);
  }, 3400);
}

function setStatus(text) {
  S.status = text;
  $('#status').textContent = text;
}

function confirmBox(title, text, okLabel = 'Убрать') {
  return new Promise((resolve) => {
    const modal = $('#modal');
    $('#modalTitle').textContent = title;
    $('#modalText').textContent = text;
    $('#modalOk').textContent = okLabel;
    modal.hidden = false;

    const done = (value) => {
      modal.hidden = true;
      $('#modalOk').onclick = null;
      $('#modalCancel').onclick = null;
      resolve(value);
    };
    $('#modalOk').onclick = () => done(true);
    $('#modalCancel').onclick = () => done(false);
  });
}

function showPage(page) {
  S.page = page;
  $$('.page').forEach((el) => el.classList.toggle('is-active', el.dataset.page === page));
  $$('.nav-item').forEach((el) => el.classList.toggle('is-active', el.dataset.page === page));
  if (page === 'leads') loadLeads();
}

/* ── Воронка в сайдбаре ─────────────────────────────────────────────────── */

function renderFunnel() {
  const rows = [
    ['new', 'не проверено'],
    ['verified', 'ждёт отсева'],
    ['kept', 'отобрано'],
    ['written', 'готово'],
  ].filter(([key]) => S.counts[key]);

  const badge = $('#navBadge');
  const ready = S.counts.written || 0;
  badge.hidden = !ready;
  badge.textContent = ready;

  const f = S.funnel || {};
  // блок виден и когда отметили исход, не отмечая отправку
  const after = (f.sent || f.answered)
    ? `<div class="funnel-after">
         ${f.sent ? `<div class="funnel-row"><span class="funnel-n">${f.sent}</span>
           <span class="funnel-l">отправлено</span></div>` : ''}
         ${f.answered ? `<div class="funnel-row"><span class="funnel-n">${f.answered}</span>
           <span class="funnel-l">ответили</span></div>` : ''}
         ${f.client ? `<div class="funnel-row"><span class="funnel-n ok">${f.client}</span>
           <span class="funnel-l">клиенты</span></div>` : ''}
       </div>` : '';

  if (!rows.length) {
    $('#funnel').innerHTML = after || '<div class="funnel-empty">база пуста</div>';
    return;
  }
  const max = Math.max(...rows.map(([key]) => S.counts[key]));
  $('#funnel').innerHTML = rows.map(([key, label]) => `
    <div class="funnel-row">
      <span class="funnel-n">${S.counts[key]}</span>
      <span class="funnel-l">${label}</span>
    </div>
    <div class="funnel-bar"><i style="width:${Math.round(S.counts[key] / max * 100)}%"></i></div>
  `).join('') + after;
}

/* ── Экран поиска ───────────────────────────────────────────────────────── */

function renderSources() {
  $('#sources').innerHTML = S.sources.map((s) => `
    <label class="source">
      <input type="checkbox" value="${esc(s.key)}" ${s.enabled ? 'checked' : ''}>
      <span class="box"></span>
      <span>
        <b>${esc(s.title)}</b>
        <span class="note">${esc(s.note)}</span>
      </span>
    </label>
  `).join('');
}

function searchParams() {
  return {
    niche: $('#niche').value.trim(),
    cities: $('#cities').value.split('\n').map((s) => s.trim()).filter(Boolean),
    limit: parseInt($('#limit').value, 10) || 150,
    sources: $$('#sources input:checked').map((el) => el.value),
  };
}

function renderLog(lines, append) {
  const log = $('#log');
  if (!append) log.innerHTML = '';
  for (const raw of lines) {
    const line = String(raw);
    let html;
    if (/^\s*(###|===)/.test(line)) {
      html = '<b>' + esc(line.replace(/###|===/g, '').trim()) + '</b>';
    } else {
      let cls = '';
      const low = line.toLowerCase();
      if (low.includes('ошибк') || low.includes('сорвал') || low.includes('недоступен')) cls = 'bad';
      else if (low.startsWith('готово') || low.includes('рабочий')) cls = 'ok';
      else if (/^\s\s/.test(line)) cls = 'dim';
      html = cls ? `<span class="${cls}">${esc(line)}</span>` : esc(line);
    }
    log.insertAdjacentHTML('beforeend', html + '\n');
  }
  log.scrollTop = log.scrollHeight;
}

function setBusy(busy) {
  S.busy = busy;
  $('#spinner').hidden = !busy;
  $('#runFull').disabled = busy;
  $$('.step').forEach((el) => { el.disabled = busy; });
  $('#msgSaveRewrite').disabled = busy;
  $('#keyCheck').disabled = busy;
}

async function runStep(step) {
  if (S.busy) { toast('Дождись, пока закончится текущий шаг'); return; }
  const needParams = step === 'scrape' || step === 'full';
  const res = await call('run_step', step, needParams ? searchParams() : {});

  if (res.error === 'no_key') {
    toast('Сначала вставь ключ Gemini', 'bad');
    showPage('settings');
    $('#apiKey').focus();
    return;
  }
  if (!res.ok) return;

  S.logLen = 0;
  renderLog([], false);
  showPage('search');
  setBusy(true);
  poll();
}

function poll() {
  clearInterval(S.polling);
  S.polling = setInterval(async () => {
    const res = await call('job_status', S.logLen);
    if (!res.ok) { clearInterval(S.polling); setBusy(false); return; }

    if (res.lines && res.lines.length) {
      renderLog(res.lines, true);
      S.logLen = res.total;
    }
    S.counts = res.counts || {};
    S.hasKey = res.has_key;
    renderFunnel();
    setStatus(res.running ? 'выполняется: ' + res.title : S.status);

    if (!res.running) {
      clearInterval(S.polling);
      setBusy(false);
      if (res.error) {
        setStatus('сорвалось');
        toast(res.error, 'bad');
      } else {
        setStatus('готово: ' + res.title);
        toast('Готово: ' + res.title.toLowerCase(), 'good');
      }
      if (S.page === 'leads') loadLeads();
    }
  }, 320);
}

async function putInClipboard(text) {
  if (!text || !text.trim()) return false;
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (_) {
    // в окне приложения буфер иногда недоступен через API, пробуем по-старому
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.append(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (_) { ok = false; }
    ta.remove();
    return ok;
  }
}

/* ── Экран лидов ────────────────────────────────────────────────────────── */

let leadFilter = 'written';

function renderChips() {
  $('#statusChips').innerHTML = STATUSES.map((s) => {
    const n = s.key ? (S.counts[s.key] || 0)
                    : Object.values(S.counts).reduce((a, b) => a + b, 0);
    return `<button class="chip ${s.key === leadFilter ? 'is-active' : ''}" data-status="${s.key}">
      ${esc(s.label)}<i>${n}</i></button>`;
  }).join('');
}

async function loadLeads(keepSelection) {
  const res = await call('leads', leadFilter, $('#leadSearch').value);
  if (!res.ok) return;
  S.leads = res.leads;
  S.counts = res.counts || {};
  S.funnel = res.funnel || S.funnel;
  renderChips();
  renderFunnel();
  renderList();

  if (keepSelection && S.current) {
    const again = S.leads.find((l) => l.id === S.current.id);
    if (again) { selectLead(again.id); return; }
  }
  renderDetail(null);
  $('#leadsSub').textContent = S.leads.length
    ? S.leads.length + ' в списке' : 'Список и отправка';
}

function renderList() {
  const list = $('#leadList');
  if (!S.leads.length) {
    const q = $('#leadSearch').value.trim();
    const [title, text] = q
      ? ['Ничего не нашлось', 'Попробуй другой запрос или сними фильтр.']
      : (EMPTY[leadFilter] || EMPTY['']);
    list.innerHTML = `<div class="empty"><h3>${esc(title)}</h3><p>${esc(text)}</p></div>`;
    return;
  }
  list.innerHTML = S.leads.map((l) => {
    const cls = l.score >= 75 ? 'hi' : (l.score >= 55 ? 'mid' : '');
    const meta = [l.city, l.phone || l.email || l.instagram].filter(Boolean).join(' · ');
    return `<div class="lead ${l.sent_at ? 'is-sent' : ''}" data-id="${esc(l.id)}">
      <div class="lead-top">
        <span class="lead-name">${esc(l.name)}</span>
        <span class="lead-score ${cls}">${l.score == null ? '' : l.score}</span>
      </div>
      <div class="lead-meta">${esc(meta)}</div>
    </div>`;
  }).join('');
}

function selectLead(id) {
  const lead = S.leads.find((l) => l.id === id);
  if (!lead) return;
  S.current = lead;
  $$('.lead').forEach((el) => el.classList.toggle('is-active', el.dataset.id === id));
  renderDetail(lead);
}

function renderDetail(lead) {
  const box = $('#leadDetail');
  if (!lead) {
    box.innerHTML = `<div class="empty">
      <h3>Выбери лид</h3><p>Слева список. Здесь появятся сообщения и кнопки отправки.</p>
    </div>`;
    return;
  }

  const meta = [lead.city, lead.category, lead.address].filter(Boolean).join('   ·   ');
  const PHONE_KIND = { mobile: 'мобильный', landline: 'городской',
                       tollfree: 'бесплатная линия', unknown: 'тип номера неизвестен' };

  const tags = [];
  if (lead.score != null) tags.push(`<span class="tag accent">оценка ${lead.score}</span>`);
  if (lead.phone_kind && lead.phone_kind !== 'mobile') {
    tags.push(`<span class="tag warn">${esc(PHONE_KIND[lead.phone_kind] || lead.phone_kind)}</span>`);
  }
  if (lead.sent_at) tags.push(`<span class="tag warn">отправлено ${esc(lead.sent_at.replace('T', ' '))}</span>`);
  if (lead.reason) tags.push(`<span class="tag">${esc(lead.reason)}</span>`);
  tags.push(`<span class="tag">${esc(lead.source)}</span>`);

  const contacts = [lead.phone, lead.email].filter(Boolean).join('   ·   ');

  box.innerHTML = `
    <div class="detail-head">
      <div class="detail-name selectable">${esc(lead.name)}</div>
      <div class="detail-meta selectable">${esc(meta)}${contacts ? '<br>' + esc(contacts) : ''}</div>
      <div class="detail-tags">${tags.join('')}</div>
    </div>

    <div class="msg-grid">
      <div>
        <div class="msg-head">Первое сообщение <button data-copy="1">копировать</button></div>
        <textarea id="m1">${esc(lead.message_1)}</textarea>
      </div>
      <div>
        <div class="msg-head">Второе, если не ответили <button data-copy="2">копировать</button></div>
        <textarea id="m2">${esc(lead.message_2)}</textarea>
      </div>
    </div>

    <div class="send-row">
      ${lead.can_message ? `
        <button class="btn btn-primary" data-act="wa">WhatsApp</button>
        <button class="btn btn-send tg" data-act="tg">Telegram</button>
        <button class="btn btn-send vb" data-act="viber">Viber</button>` : ''}
      ${lead.email ? '<button class="btn btn-send ml" data-act="mail">Почта</button>' : ''}
      ${lead.phone_e164 && !lead.can_message
        ? '<button class="btn btn-send tel" data-act="call">Позвонить</button>' : ''}
      ${!lead.phone_e164 && !lead.email
        ? '<span class="note">прямых контактов нет, остаётся профиль в соцсети</span>' : ''}
      <span class="note send-note">${
        lead.can_message
          ? 'текст для WhatsApp подставится сам, для Telegram и Viber скопируется в буфер'
          : (lead.phone_e164
              ? 'номер городской, аккаунтов в мессенджерах на таких не бывает'
              : '')
      }</span>
    </div>

    <div class="detail-actions">
      <button class="btn" data-act="save">Сохранить правки</button>
      <button class="btn btn-ghost" data-act="rewrite" title="переписать сообщения заново">Переписать</button>
      ${lead.instagram || lead.facebook || lead.maps
        ? '<button class="btn btn-ghost" data-act="profile">Профиль</button>' : ''}
      <span class="spacer"></span>
      <button class="btn btn-ghost" data-act="sent">${lead.sent_at ? 'Снять отметку' : 'Отметить отправленным'}</button>
      ${lead.status === 'dropped'
        ? '<button class="btn btn-ghost" data-act="restore">Вернуть</button>'
        : '<button class="btn btn-danger" data-act="drop">Убрать</button>'}
    </div>

    <div class="track">
      <div class="track-head">Что было дальше</div>
      <div class="outcomes">
        ${S.outcomes.map((o) => `
          <button class="pill ${lead.outcome === o.key ? 'is-active' : ''}"
                  data-outcome="${esc(o.key)}">${esc(o.label)}</button>`).join('')}
        ${lead.outcome ? '<button class="pill clear" data-outcome="">снять</button>' : ''}
      </div>
      <div class="track-row">
        <label>Написать снова</label>
        <input type="date" id="nextTouch" value="${esc(lead.next_touch)}">
        <button class="btn btn-ghost btn-mini" data-plus="3">+3 дня</button>
        <button class="btn btn-ghost btn-mini" data-plus="7">+7 дней</button>
        ${lead.next_touch ? '<button class="btn btn-ghost btn-mini" data-plus="clear">убрать</button>' : ''}
      </div>
      <textarea id="leadNote" rows="2" placeholder="заметка: что ответили, о чём договорились"
                >${esc(lead.note)}</textarea>
    </div>`;
}

async function leadAction(act) {
  const lead = S.current;
  if (!lead) return;

  if (act === 'save') {
    const res = await call('lead_save', lead.id, $('#m1').value, $('#m2').value);
    if (res.ok) { toast('Правки сохранены', 'good'); loadLeads(true); }
    return;
  }
  if (act === 'wa') {
    const text = encodeURIComponent($('#m1').value);
    await call('open_external', 'https://wa.me/' + lead.phone_e164.replace('+', '') + '?text=' + text);
    setStatus('WhatsApp открыт, отправляешь сам');
    return;
  }
  // Telegram и Viber не дают подставить текст в чат с обычным человеком:
  // такой возможности нет в их схемах ссылок. Поэтому кладём текст в буфер,
  // чтобы осталось только вставить.
  if (act === 'tg' || act === 'viber') {
    const copied = await putInClipboard($('#m1').value);
    const url = act === 'tg'
      ? 'https://t.me/' + lead.phone_e164
      : 'viber://chat?number=' + encodeURIComponent(lead.phone_e164);
    await call('open_external', url);
    const where = act === 'tg' ? 'Telegram' : 'Viber';
    if (copied) {
      toast(where + ' открыт, текст в буфере: вставь через Cmd+V', 'good');
      setStatus(where + ' открыт, текст скопирован');
    } else {
      toast(where + ' открыт, текст скопировать не удалось');
    }
    return;
  }
  if (act === 'call') {
    await call('open_external', 'tel:' + lead.phone_e164);
    setStatus('звонок, номер городской');
    return;
  }
  if (act === 'mail') {
    const subject = encodeURIComponent('Сайт для ' + lead.name);
    const body = encodeURIComponent($('#m1').value);
    await call('open_external', 'mailto:' + lead.email + '?subject=' + subject + '&body=' + body);
    setStatus('почтовая программа открыта');
    return;
  }
  if (act === 'rewrite') {
    const res = await call('lead_rewrite', lead.id);
    if (res.error === 'no_key') {
      toast('Сначала вставь ключ Gemini', 'bad');
      showPage('settings');
      return;
    }
    if (res.ok) {
      S.logLen = 0;
      renderLog([], false);
      setBusy(true);
      poll();
      toast('Переписываю, это займёт несколько секунд');
    }
    return;
  }
  if (act === 'profile') {
    await call('open_external', lead.instagram || lead.facebook || lead.maps);
    return;
  }
  if (act === 'drop') {
    const ok = await confirmBox('Убрать лид', 'Убрать «' + lead.name + '» в отсев?');
    if (!ok) return;
    const res = await call('lead_flag', lead.id, 'drop');
    if (res.ok) { toast('Убран в отсев'); S.current = null; loadLeads(); }
    return;
  }
  const map = { sent: lead.sent_at ? 'unsent' : 'sent', restore: 'restore' };
  const res = await call('lead_flag', lead.id, map[act] || act);
  if (res.ok) {
    toast(map[act] === 'unsent' ? 'Отметка снята' : 'Отмечено');
    loadLeads(true);
  }
}

async function trackLead(fields) {
  if (!S.current) return;
  const res = await call('lead_track', S.current.id,
    fields.outcome === undefined ? null : fields.outcome,
    fields.note === undefined ? null : fields.note,
    fields.next_touch === undefined ? null : fields.next_touch);
  if (res.ok) {
    S.counts = res.counts || S.counts;
    S.funnel = res.funnel || S.funnel;
    renderFunnel();
    renderChips();
    loadLeads(true);
  }
  return res.ok;
}

function plusDays(n) {
  const d = new Date();
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}

/* ── Экран сообщений ────────────────────────────────────────────────────── */

function renderModes(active) {
  $('#modes').innerHTML = MODES.map(([key, title, note]) => `
    <label class="mode ${key === active ? 'is-active' : ''}" data-mode="${key}">
      <input type="radio" name="mode" value="${key}" ${key === active ? 'checked' : ''}>
      <span class="dot"></span>
      <span><b>${esc(title)}</b><span class="note">${esc(note)}</span></span>
    </label>`).join('');
}

async function loadMessages() {
  const res = await call('messages_get');
  if (!res.ok) return;
  const m = res.messages || {};

  renderModes((m.mode || 'hybrid').toLowerCase());
  $('#language').value = m.language || '';
  $('#sender').value = Object.entries(m.sender || {}).map(([k, v]) => k + ': ' + v).join('\n');

  const st = m.style || {};
  $('#tone').value = st.tone || '';
  $('#maxChars').value = st.max_chars || 420;
  $('#noEmoji').checked = st.no_emoji !== false;
  $('#noDashes').checked = st.no_dashes !== false;
  $('#forbid').value = (st.forbid || []).join('\n');

  for (const key of ['message_1', 'message_2']) {
    const card = $(`.card[data-msg="${key}"]`);
    const block = m[key] || {};
    $('[data-field=goal]', card).value = block.goal || '';
    $('[data-field=template]', card).value = block.template || '';
    $('[data-field=examples]', card).value = (block.examples || []).join('\n');
  }
  $('#extra').value = m.extra_instructions || '';
}

function collectMessages() {
  const sender = {};
  for (const line of $('#sender').value.split('\n')) {
    const i = line.indexOf(':');
    if (i > 0) sender[line.slice(0, i).trim()] = line.slice(i + 1).trim();
  }
  const lines = (el) => el.value.split('\n').map((s) => s.trim()).filter(Boolean);

  const data = {
    mode: ($('input[name=mode]:checked') || {}).value || 'hybrid',
    language: $('#language').value.trim(),
    sender,
    style: {
      tone: $('#tone').value.trim(),
      max_chars: parseInt($('#maxChars').value, 10) || 420,
      no_emoji: $('#noEmoji').checked,
      no_dashes: $('#noDashes').checked,
      forbid: lines($('#forbid')),
    },
    extra_instructions: $('#extra').value.trim(),
  };
  for (const key of ['message_1', 'message_2']) {
    const card = $(`.card[data-msg="${key}"]`);
    data[key] = {
      goal: $('[data-field=goal]', card).value.trim(),
      template: $('[data-field=template]', card).value.trim(),
      examples: lines($('[data-field=examples]', card)),
    };
  }
  return data;
}

async function saveMessages() {
  const res = await call('messages_save', collectMessages());
  if (res.ok) toast('Шаблоны сохранены', 'good');
  return res.ok;
}

/* ── Экран настроек ─────────────────────────────────────────────────────── */

async function loadSettings() {
  const res = await call('settings_get');
  if (!res.ok) return;
  $('#apiKey').value = res.api_key || '';
  $('#model').value = res.model || 'gemini-2.5-flash';
  $('#minScore').value = res.min_score;
  $('#rules').value = res.rules || '';
  $('#region').value = res.region || 'UA';
  $('#rpm').value = res.rpm;
  $('#batchSize').value = res.batch_size;
  S.hasKey = !!(res.api_key || '').trim();
  $('#keyDot').classList.toggle('on', S.hasKey);
}

async function saveSettings(quiet) {
  const res = await call('settings_save', {
    api_key: $('#apiKey').value.trim(),
    model: $('#model').value,
    min_score: parseInt($('#minScore').value, 10),
    rules: $('#rules').value,
    region: $('#region').value,
    rpm: parseInt($('#rpm').value, 10),
    batch_size: parseInt($('#batchSize').value, 10),
  });
  if (res.ok) {
    S.hasKey = res.has_key;
    $('#keyDot').classList.toggle('on', S.hasKey);
    if (!quiet) toast('Настройки сохранены', 'good');
  }
  return res.ok;
}

/* ── Слушатели ──────────────────────────────────────────────────────────── */

function wire() {
  $('#nav').addEventListener('click', (e) => {
    const item = e.target.closest('.nav-item');
    if (item) showPage(item.dataset.page);
  });

  $('#runFull').addEventListener('click', () => runStep('full'));
  $('#steps').addEventListener('click', (e) => {
    const step = e.target.closest('.step');
    if (step) runStep(step.dataset.step);
  });

  $('#statusChips').addEventListener('click', (e) => {
    const chip = e.target.closest('.chip');
    if (!chip) return;
    leadFilter = chip.dataset.status;
    S.current = null;
    loadLeads();
  });

  let searchTimer;
  $('#leadSearch').addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => loadLeads(), 180);
  });

  $('#leadList').addEventListener('click', (e) => {
    const row = e.target.closest('.lead');
    if (row) selectLead(row.dataset.id);
  });

  $('#leadDetail').addEventListener('click', async (e) => {
    const copy = e.target.closest('[data-copy]');
    if (copy) {
      const text = $('#m' + copy.dataset.copy).value;
      if (!text.trim()) { toast('Сообщение пустое'); return; }
      const ok = await putInClipboard(text);
      copy.textContent = ok ? 'скопировано' : 'не вышло';
      setTimeout(() => { copy.textContent = 'копировать'; }, 1300);
      return;
    }
    const outcome = e.target.closest('[data-outcome]');
    if (outcome) {
      const ok = await trackLead({ outcome: outcome.dataset.outcome });
      if (ok) {
        const label = outcome.textContent.trim();
        toast(outcome.dataset.outcome ? 'Отмечено: ' + label : 'Отметка снята');
      }
      return;
    }
    const plus = e.target.closest('[data-plus]');
    if (plus) {
      const value = plus.dataset.plus === 'clear' ? '' : plusDays(parseInt(plus.dataset.plus, 10));
      if (await trackLead({ next_touch: value })) {
        toast(value ? 'Напомню ' + value : 'Напоминание убрано');
      }
      return;
    }
    const act = e.target.closest('[data-act]');
    if (act) leadAction(act.dataset.act);
  });

  // заметку и дату сохраняем, когда поле теряет фокус
  $('#leadDetail').addEventListener('change', async (e) => {
    if (e.target.id === 'leadNote') await trackLead({ note: e.target.value });
    if (e.target.id === 'nextTouch') await trackLead({ next_touch: e.target.value });
  });

  $('#importCsv').addEventListener('click', async () => {
    const res = await call('import_csv', '');
    if (res.cancelled) return;
    if (res.ok) {
      toast(`Загружено ${res.added} из ${res.file}` +
            (res.skipped ? `, пропущено ${res.skipped} без контактов` : ''), 'good');
      S.counts = res.counts || S.counts;
      renderFunnel();
      leadFilter = 'new';
      loadLeads();
    }
  });

  $$('[data-export]').forEach((btn) => btn.addEventListener('click', async () => {
    const res = await call('export', btn.dataset.export, leadFilter);
    if (res.ok) toast('Выгружено ' + res.count + ' в ' + res.path, 'good');
  }));

  $('#modes').addEventListener('click', (e) => {
    const mode = e.target.closest('.mode');
    if (!mode) return;
    $$('.mode').forEach((el) => el.classList.toggle('is-active', el === mode));
    $('input', mode).checked = true;
  });

  $('#msgSave').addEventListener('click', () => saveMessages());
  $('#msgSaveRewrite').addEventListener('click', async () => {
    if (await saveMessages()) runStep('rewrite');
  });
  $('#msgRestore').addEventListener('click', async () => {
    const ok = await confirmBox('Вернуть образец',
      'Заменить всё содержимое экрана образцом из поставки? Твои тексты будут потеряны.',
      'Вернуть');
    if (!ok) return;
    const res = await call('messages_restore');
    if (res.ok) { await loadMessages(); toast('Образец восстановлен'); }
  });

  $('#setSave').addEventListener('click', () => saveSettings());
  $('#keyToggle').addEventListener('click', () => {
    const input = $('#apiKey');
    const shown = input.type === 'text';
    input.type = shown ? 'password' : 'text';
    $('#keyToggle').textContent = shown ? 'Показать' : 'Скрыть';
  });
  $('#keyCheck').addEventListener('click', async () => {
    if (!$('#apiKey').value.trim()) { toast('Вставь ключ, потом проверяй', 'bad'); return; }
    await saveSettings(true);
    runStep('check_key');
  });

  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 's') {
      e.preventDefault();
      if (S.page === 'messages') saveMessages();
      if (S.page === 'settings') saveSettings();
      if (S.page === 'leads' && S.current) leadAction('save');
    }
    if (e.key === 'Escape' && !$('#modal').hidden) $('#modalCancel').click();
  });
}

/* ── Старт ──────────────────────────────────────────────────────────────── */

async function boot() {
  wire();
  const b = await call('bootstrap');
  if (!b.ok) { setStatus('не удалось прочитать настройки'); return; }

  $('#niche').value = b.search.niche;
  $('#cities').value = (b.search.cities || []).join('\n');
  $('#limit').value = b.search.limit;
  S.sources = b.sources || [];
  renderSources();

  S.counts = b.counts || {};
  S.funnel = b.funnel || {};
  S.outcomes = b.outcomes || [];
  S.hasKey = b.has_key;
  renderFunnel();

  // открываем лиды на том статусе, где что-то есть
  for (const s of STATUSES) {
    if (s.key && S.counts[s.key]) { leadFilter = s.key; break; }
  }
  renderChips();

  await loadMessages();
  await loadSettings();

  renderLog(['<span class="empty">Заполни нишу и города, затем нажми «Пройти всё целиком».</span>'], false);
  $('#log').innerHTML = '<span class="dim">Заполни нишу и города, затем нажми «Пройти всё целиком».\n' +
    'Здесь будет видно, что нашлось и почему часть отсеялась.</span>';

  setStatus(S.hasKey ? 'готов к работе' : 'нужен ключ Gemini в настройках');
}

if (window.pywebview && window.pywebview.api) boot();
else window.addEventListener('pywebviewready', boot);
