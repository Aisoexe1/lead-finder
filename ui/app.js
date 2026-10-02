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
  cities: [],
  picked: [],
  dropIndex: -1,
};

// 25 областных центров: по ним чаще всего и работают
const CENTERS = [
  'Київ', 'Харків', 'Дніпро', 'Одеса', 'Донецьк', 'Львів', 'Запоріжжя',
  'Миколаїв', 'Вінниця', 'Херсон', 'Полтава', 'Чернігів', 'Черкаси', 'Суми',
  'Житомир', 'Хмельницький', 'Чернівці', 'Рівне', 'Кропивницький', 'Івано-Франківськ',
  'Кременчук', 'Тернопіль', 'Луцьк', 'Ужгород', 'Луганськ', 'Сімферополь',
];

const STATUSES = [
  { key: 'due',      label: 'На сегодня' },
  { key: 'social',   label: 'С Instagram' },
  { key: 'written',  label: 'Готовы' },
  { key: 'kept',     label: 'Отобраны' },
  { key: 'verified', label: 'Ждут отсева' },
  { key: 'new',      label: 'Не проверены' },
  { key: 'dropped',  label: 'Отсеяны' },
  { key: '',         label: 'Все' },
];

const EMPTY = {
  due:      ['На сегодня никого', 'Сюда попадают те, кому пора отправить второе сообщение.'],
  social:   ['Профилей не нашлось', 'Ни у одного лида в базе не записан Instagram или Facebook.'],
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

const ICONS = {
  instagram: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" ' +
    'stroke-linecap="round"><rect x="2.5" y="2.5" width="19" height="19" rx="5.4"/>' +
    '<circle cx="12" cy="12" r="4.1"/><circle cx="17.4" cy="6.6" r="1.15" fill="currentColor" stroke="none"/></svg>',
  facebook: '<svg viewBox="0 0 24 24" fill="currentColor">' +
    '<path d="M14.1 21v-8h2.7l.4-3.1h-3.1V7.9c0-.9.25-1.5 1.55-1.5H17.3V3.6c-.29-.04-1.27-.12-2.41-.12' +
    '-2.39 0-4.02 1.46-4.02 4.13V9.9H8.2V13h2.67v8h3.23z"/></svg>',
  map: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" ' +
    'stroke-linejoin="round"><path d="M12 21.2s6.8-6.1 6.8-10.7a6.8 6.8 0 1 0-13.6 0c0 4.6 6.8 10.7 6.8 10.7z"/>' +
    '<circle cx="12" cy="10.4" r="2.5"/></svg>',
};

/* Поиск по названию должен прощать раскладку и язык: "винниц" обязан
   находить "Вінниця". Сводим обе азбуки к общему виду. */
const TRANSLIT = {
  'а':'a','б':'b','в':'v','г':'g','ґ':'g','д':'d','е':'e','є':'e','ё':'e','ж':'j',
  'з':'z','и':'i','і':'i','ї':'i','й':'i','к':'k','л':'l','м':'m','н':'n','о':'o',
  'п':'p','р':'r','с':'s','т':'t','у':'u','ф':'f','х':'h','ц':'c','ч':'ch','ш':'sh',
  'щ':'sh','ъ':'','ы':'i','ь':'','э':'e','ю':'u','я':'a','\u0027':'','’':'','`':'',
  '-':'', ' ':'',
};

function fold(text) {
  let out = '';
  for (const ch of String(text || '').toLowerCase()) {
    out += (ch in TRANSLIT) ? TRANSLIT[ch] : ch;
  }
  return out;
}

/* Названия, которые фонетикой не связать: другой корень или переименование.
   Ключ слева это то, что набирают по привычке. */
const CITY_ALIASES = {
  'николаев': 'Миколаїв',
  'днепропетровск': 'Дніпро',
  'кировоград': 'Кропивницький',
  'ильичевск': 'Чорноморськ',
  'артемовск': 'Бахмут',
  'комсомольск': 'Горішні Плавні',
  'красноармейск': 'Покровськ',
  'димитров': 'Мирноград',
  'котовск': 'Подільськ',
  'переяслав-хмельницкий': 'Переяслав',
  'владимир-волынский': 'Володимир',
  'кузнецовск': 'Вараш',
  'щорс': 'Сновськ',
  'днепродзержинск': 'Кам’янське',
  'орджоникидзе': 'Покров',
};

/* "Львів" и "Львов", "Харків" и "Харьков" расходятся только гласными.
   Скелет из согласных сводит такие пары к одному виду: lvv и hrkv. */
const VOWELS = /[aeiou]/g;

function skeleton(text) {
  // двойные согласные тоже схлопываем: "Одесса" и "Одеса" должны совпасть
  return fold(text).replace(VOWELS, '').replace(/(.)\1+/g, '$1');
}

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

/* ── Выбор городов ──────────────────────────────────────────────────────── */

function renderPicked() {
  $('#cityPicked').innerHTML = S.picked.map((name) => `
    <span class="city-chip">${esc(name)}<button data-remove="${esc(name)}" title="убрать">×</button></span>
  `).join('');
  $('#cityInput').placeholder = S.picked.length ? 'добавить ещё' : 'начни вводить название';
}

function addCity(name) {
  name = (name || '').trim();
  if (!name || S.picked.includes(name)) return;
  S.picked.push(name);
  renderPicked();
  closeDrop();
  $('#cityInput').value = '';
  $('#cityInput').focus();
}

function removeCity(name) {
  S.picked = S.picked.filter((c) => c !== name);
  renderPicked();
}

function matchCities(query) {
  const q = fold(query);
  if (!q) {
    // пустой запрос: показываем крупнейшие, они нужны чаще всего
    return S.cities.slice(0, 12);
  }
  // привычное русское или прежнее название
  const plain = String(query).toLowerCase().trim();
  for (const [alias, real] of Object.entries(CITY_ALIASES)) {
    if (alias.startsWith(plain) || plain.startsWith(alias)) {
      const found = S.cities.find((c) => c.name === real);
      if (found) return [found];
    }
  }

  // три корзины по убыванию точности. Совпадение с начала названия всегда
  // важнее совпадения где-то внутри: иначе "киев" выдаёт "Єнакієве"
  const qs = skeleton(query);
  const starts = [];
  const sounds = [];
  const inside = [];

  for (const city of S.cities) {
    const folded = city._f || (city._f = fold(city.name));
    if (folded.startsWith(q)) { starts.push(city); continue; }

    const sk = city._s || (city._s = skeleton(city.name));
    if (qs.length >= 2 && sk.startsWith(qs)) { sounds.push(city); continue; }

    if (folded.includes(q)) inside.push(city);
    if (starts.length >= 40) break;
  }
  return starts.concat(sounds, inside).slice(0, 40);
}

function renderDrop(items) {
  const drop = $('#cityDrop');
  if (!items.length) {
    drop.innerHTML = `<div class="drop-empty">Ничего не нашлось.
      Можно вписать своё название и нажать Enter.</div>`;
    drop.hidden = false;
    return;
  }
  drop.innerHTML = items.map((c, i) => `
    <button class="drop-item ${i === S.dropIndex ? 'is-on' : ''}" data-city="${esc(c.name)}">
      <span>${esc(c.name)}</span>
      <i>${c.pop ? fmtPop(c.pop) : (c.place === 'city' ? 'місто' : '')}</i>
    </button>`).join('');
  drop.hidden = false;
}

function fmtPop(n) {
  if (n >= 1000000) return (n / 1000000).toFixed(1).replace('.0', '') + ' млн';
  if (n >= 1000) return Math.round(n / 1000) + ' тыс';
  return String(n);
}

function closeDrop() {
  $('#cityDrop').hidden = true;
  S.dropIndex = -1;
}

function wireCityPicker() {
  const input = $('#cityInput');

  input.addEventListener('focus', () => { S.dropIndex = -1; renderDrop(matchCities(input.value)); });
  input.addEventListener('input', () => { S.dropIndex = -1; renderDrop(matchCities(input.value)); });

  input.addEventListener('keydown', (e) => {
    const items = matchCities(input.value);
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const step = e.key === 'ArrowDown' ? 1 : -1;
      S.dropIndex = Math.max(-1, Math.min(items.length - 1, S.dropIndex + step));
      renderDrop(items);
      const on = $('.drop-item.is-on');
      if (on) on.scrollIntoView({ block: 'nearest' });
      return;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      // выбран пункт списка, иначе берём то, что набрано руками
      addCity(S.dropIndex >= 0 && items[S.dropIndex] ? items[S.dropIndex].name : input.value);
      return;
    }
    if (e.key === 'Escape') { closeDrop(); return; }
    if (e.key === 'Backspace' && !input.value && S.picked.length) {
      removeCity(S.picked[S.picked.length - 1]);
    }
  });

  $('#cityDrop').addEventListener('mousedown', (e) => {
    const item = e.target.closest('[data-city]');
    if (item) { e.preventDefault(); addCity(item.dataset.city); }
  });

  $('#cityPicked').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-remove]');
    if (btn) removeCity(btn.dataset.remove);
  });

  document.addEventListener('click', (e) => {
    if (!e.target.closest('#cityPicker')) closeDrop();
  });

  $$('[data-quick]').forEach((btn) => btn.addEventListener('click', () => {
    const mode = btn.dataset.quick;
    if (mode === 'clear') { S.picked = []; }
    else if (mode === 'big') { S.picked = S.cities.slice(0, 10).map((c) => c.name); }
    else if (mode === 'centers') {
      const known = new Set(S.cities.map((c) => c.name));
      S.picked = CENTERS.filter((name) => known.has(name));
    }
    renderPicked();
    closeDrop();
  }));
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
  // то, что набрано, но не подтверждено, тоже считаем выбором
  const typed = $('#cityInput').value.trim();
  const cities = typed && !S.picked.includes(typed) ? S.picked.concat([typed]) : S.picked;
  return {
    niche: $('#niche').value.trim(),
    cities,
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
    const meta = [l.city, l.phone || l.email].filter(Boolean).join(' · ');
    const ig = l.instagram
      ? `<span class="ig-dot" title="${esc(l.instagram_handle)}">${ICONS.instagram}</span>` : '';
    return `<div class="lead ${l.sent_at ? 'is-sent' : ''}" data-id="${esc(l.id)}">
      <div class="lead-top">
        <span class="lead-name">${esc(l.name)}</span>
        ${ig}
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

  const profiles = [];
  if (lead.instagram) {
    profiles.push(`<button class="circle ig" data-open="${esc(lead.instagram)}"
      title="Открыть ${esc(lead.instagram_handle)}">${ICONS.instagram}</button>`);
  }
  if (lead.facebook) {
    profiles.push(`<button class="circle fb" data-open="${esc(lead.facebook)}"
      title="Открыть Facebook">${ICONS.facebook}</button>`);
  }
  if (lead.maps) {
    profiles.push(`<button class="circle map" data-open="${esc(lead.maps)}"
      title="Показать на карте">${ICONS.map}</button>`);
  }
  if (lead.instagram_handle) {
    profiles.push(`<span class="handle selectable">${esc(lead.instagram_handle)}</span>`);
  }

  box.innerHTML = `
    <div class="detail-head">
      <div class="detail-name selectable">${esc(lead.name)}</div>
      <div class="detail-meta selectable">${esc(meta)}${contacts ? '<br>' + esc(contacts) : ''}</div>
      ${profiles.length ? `<div class="profiles">${profiles.join('')}</div>` : ''}
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
      ${lead.instagram ? '<button class="btn btn-send ig" data-act="direct">Директ</button>' : ''}
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
  if (act === 'direct') {
    // текст в директ не подставляется, кладём в буфер
    const copied = await putInClipboard($('#m1').value);
    await call('open_external', lead.instagram);
    toast(copied ? 'Instagram открыт, текст в буфере: вставь в директ'
                 : 'Instagram открыт', copied ? 'good' : '');
    setStatus('Instagram открыт');
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
    const link = e.target.closest('[data-open]');
    if (link) {
      await call('open_external', link.dataset.open);
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
  $('#limit').value = b.search.limit;

  const cityList = await call('cities');
  S.cities = (cityList.cities || []);
  // в старых конфигах города попадались склеенными в одну строку
  S.picked = [];
  for (const entry of (b.search.cities || [])) {
    for (const part of String(entry).split(/[,;\n]/)) {
      const name = part.trim();
      if (name && !S.picked.includes(name)) S.picked.push(name);
    }
  }
  renderPicked();
  wireCityPicker();
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
