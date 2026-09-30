from __future__ import annotations

import csv
import html
import json
import os
from datetime import datetime
from typing import List, Optional
from urllib.parse import quote

from .models import Lead

CSV_COLUMNS = [
    "id", "name", "city", "category", "address", "phone_e164", "phone", "email",
    "instagram", "facebook", "website", "website_status", "source",
    "ai_score", "ai_verdict", "ai_reason", "message_1", "message_2", "status",
]


def export_csv(leads: List[Lead], path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(CSV_COLUMNS)
        for lead in leads:
            row = lead.to_row()
            writer.writerow([row.get(column, "") for column in CSV_COLUMNS])
    return path


def _wa_link(phone_e164: str, text: str) -> str:
    if not phone_e164:
        return ""
    return "https://wa.me/%s?text=%s" % (phone_e164.lstrip("+"), quote(text))


def _viber_link(phone_e164: str) -> str:
    if not phone_e164:
        return ""
    return "viber://chat?number=%s" % quote(phone_e164)


def _mail_link(email: str, subject: str, body: str) -> str:
    if not email:
        return ""
    return "mailto:%s?subject=%s&body=%s" % (email, quote(subject), quote(body))


def export_html(
    leads: List[Lead],
    path: str,
    title: str = "Лиды",
    subject: str = "Сайт для вашего бизнеса",
) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    rows = []

    for lead in leads:
        msg_1 = lead.message_1 or ""
        msg_2 = lead.message_2 or ""
        phone = lead.phone_e164 or lead.phone

        actions = []
        wa = _wa_link(lead.phone_e164, msg_1)
        if wa:
            actions.append('<a class="btn wa" href="%s" target="_blank" rel="noopener">WhatsApp</a>' % html.escape(wa))
        viber = _viber_link(lead.phone_e164)
        if viber:
            actions.append('<a class="btn vb" href="%s">Viber</a>' % html.escape(viber))
        mail = _mail_link(lead.email, subject, msg_1)
        if mail:
            actions.append('<a class="btn ml" href="%s">Почта</a>' % html.escape(mail))
        if lead.instagram:
            actions.append('<a class="btn ig" href="%s" target="_blank" rel="noopener">Instagram</a>' % html.escape(lead.instagram))
        if lead.facebook:
            actions.append('<a class="btn fb" href="%s" target="_blank" rel="noopener">Facebook</a>' % html.escape(lead.facebook))
        if lead.lat and lead.lon:
            actions.append(
                '<a class="btn map" href="https://www.google.com/maps/search/?api=1&query=%s,%s" target="_blank" rel="noopener">Карта</a>'
                % (lead.lat, lead.lon)
            )

        score = lead.ai_score if lead.ai_score is not None else ""
        score_class = "hi" if (lead.ai_score or 0) >= 75 else ("mid" if (lead.ai_score or 0) >= 55 else "lo")

        rows.append(
            """
    <tr class="lead" data-id="{id}" data-score="{score_num}" data-text="{search}">
      <td class="c-check"><input type="checkbox" class="sent" title="отмечено как отправленное"></td>
      <td class="c-name">
        <div class="nm">{name}</div>
        <div class="meta">{city}{cat}</div>
        <div class="meta">{addr}</div>
      </td>
      <td class="c-score"><span class="score {score_class}">{score}</span><div class="meta">{reason}</div></td>
      <td class="c-contact">
        <div class="phone">{phone}</div>
        <div class="meta">{email}</div>
        <div class="meta src">{source}</div>
      </td>
      <td class="c-msg">
        <div class="msg-head">Сообщение 1 <button class="copy" data-target="m1-{id}">копировать</button></div>
        <div class="msg" id="m1-{id}">{msg1}</div>
        <div class="msg-head">Сообщение 2 <button class="copy" data-target="m2-{id}">копировать</button></div>
        <div class="msg second" id="m2-{id}">{msg2}</div>
      </td>
      <td class="c-act">{actions}</td>
    </tr>""".format(
                id=html.escape(lead.id),
                score_num=lead.ai_score or 0,
                search=html.escape(" ".join([lead.name, lead.city, lead.category, phone]).lower()),
                name=html.escape(lead.name),
                city=html.escape(lead.city),
                cat=(" · " + html.escape(lead.category)) if lead.category else "",
                addr=html.escape(lead.address),
                score=score,
                score_class=score_class,
                reason=html.escape(lead.ai_reason or ""),
                phone=html.escape(phone),
                email=html.escape(lead.email),
                source=html.escape(lead.source),
                msg1=html.escape(msg_1).replace("\n", "<br>"),
                msg2=html.escape(msg_2).replace("\n", "<br>"),
                actions="".join(actions) or '<span class="meta">нет прямых контактов</span>',
            )
        )

    document = TEMPLATE.format(
        title=html.escape(title),
        generated=datetime.now().strftime("%d.%m.%Y %H:%M"),
        count=len(leads),
        rows="".join(rows),
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(document)
    return path


TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{
    --bg: #f6f6f4; --card: #fff; --ink: #1c1c1a; --muted: #77776f;
    --line: #e4e4de; --accent: #2f6f4e; --hi: #1f7a4d; --mid: #9a7b1f; --lo: #8a8a82;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --bg: #17171a; --card: #1f1f23; --ink: #ededea; --muted: #9a9a93;
      --line: #2e2e34; --accent: #5cc08a; --hi: #5cc08a; --mid: #d9b64e; --lo: #7d7d76;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--ink);
    font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
  header {{ padding: 20px 16px 12px; max-width: 1400px; margin: 0 auto; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .sub {{ color: var(--muted); font-size: 13px; }}
  .bar {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 14px; }}
  input[type=search], select {{ padding: 8px 10px; border: 1px solid var(--line);
    border-radius: 8px; background: var(--card); color: var(--ink); font-size: 14px; }}
  input[type=search] {{ flex: 1 1 240px; }}
  .wrap {{ max-width: 1400px; margin: 0 auto; padding: 0 16px 60px; }}
  table {{ width: 100%; border-collapse: collapse; background: var(--card);
    border: 1px solid var(--line); border-radius: 12px; overflow: hidden; }}
  th {{ text-align: left; font-size: 12px; text-transform: uppercase; letter-spacing: .04em;
    color: var(--muted); padding: 10px; border-bottom: 1px solid var(--line); font-weight: 600; }}
  td {{ padding: 12px 10px; border-bottom: 1px solid var(--line); vertical-align: top; }}
  tr.done {{ opacity: .42; }}
  .c-check {{ width: 34px; }}
  .c-name {{ width: 21%; }}
  .c-score {{ width: 12%; }}
  .c-contact {{ width: 15%; }}
  .c-act {{ width: 13%; }}
  .nm {{ font-weight: 600; }}
  .meta {{ color: var(--muted); font-size: 12.5px; }}
  .src {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; }}
  .phone {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
  .score {{ font-weight: 700; font-size: 16px; }}
  .score.hi {{ color: var(--hi); }} .score.mid {{ color: var(--mid); }} .score.lo {{ color: var(--lo); }}
  .msg {{ white-space: pre-wrap; background: var(--bg); border: 1px solid var(--line);
    border-radius: 8px; padding: 9px 10px; margin: 4px 0 10px; font-size: 13.5px; }}
  .msg.second {{ margin-bottom: 0; }}
  .msg-head {{ font-size: 11.5px; text-transform: uppercase; letter-spacing: .04em;
    color: var(--muted); display: flex; align-items: center; gap: 8px; }}
  button.copy {{ font-size: 11px; padding: 2px 7px; border: 1px solid var(--line);
    background: var(--card); color: var(--muted); border-radius: 5px; cursor: pointer; }}
  button.copy:hover {{ color: var(--ink); }}
  .btn {{ display: block; text-align: center; margin-bottom: 5px; padding: 6px 8px;
    border-radius: 7px; font-size: 12.5px; text-decoration: none; color: #fff;
    background: var(--accent); }}
  .btn.vb {{ background: #6b4fa8; }} .btn.ml {{ background: #4a6fa5; }}
  .btn.ig {{ background: #b4417a; }} .btn.fb {{ background: #3b5998; }}
  .btn.map {{ background: #6d6d66; }}
  .hidden {{ display: none; }}
  footer {{ max-width: 1400px; margin: 0 auto; padding: 0 16px 40px;
    color: var(--muted); font-size: 12.5px; }}
  @media (max-width: 820px) {{
    table, thead, tbody, tr, td, th {{ display: block; width: auto; }}
    thead {{ display: none; }}
    tr.lead {{ border-bottom: 8px solid var(--bg); }}
    td {{ border: none; padding: 6px 12px; }}
  }}
</style>
</head>
<body>
<header>
  <h1>{title}</h1>
  <div class="sub">{count} лидов · собрано {generated} · отметки об отправке хранятся в этом браузере</div>
  <div class="bar">
    <input type="search" id="q" placeholder="поиск по названию, городу, телефону">
    <select id="minscore">
      <option value="0">любая оценка</option>
      <option value="55">от 55</option>
      <option value="70">от 70</option>
      <option value="85">от 85</option>
    </select>
    <select id="showsent">
      <option value="all">показывать все</option>
      <option value="todo">только неотправленные</option>
    </select>
  </div>
</header>
<div class="wrap">
<table>
  <thead><tr>
    <th></th><th>Бизнес</th><th>Оценка</th><th>Контакты</th><th>Сообщения</th><th>Отправить</th>
  </tr></thead>
  <tbody id="tb">{rows}</tbody>
</table>
</div>
<footer>
  Кнопки открывают мессенджер с уже подставленным первым сообщением. Отправку
  подтверждаешь ты сам, ничего не уходит автоматически. Перед отправкой прочитай
  текст: модель иногда выдумывает детали.
</footer>
<script>
  var KEY = 'leadfinder-sent';
  var sent = {{}};
  try {{ sent = JSON.parse(localStorage.getItem(KEY) || '{{}}'); }} catch (e) {{ sent = {{}}; }}

  function persist() {{
    try {{ localStorage.setItem(KEY, JSON.stringify(sent)); }} catch (e) {{}}
  }}

  document.querySelectorAll('tr.lead').forEach(function (row) {{
    var id = row.dataset.id;
    var box = row.querySelector('.sent');
    if (sent[id]) {{ box.checked = true; row.classList.add('done'); }}
    box.addEventListener('change', function () {{
      if (box.checked) {{ sent[id] = Date.now(); row.classList.add('done'); }}
      else {{ delete sent[id]; row.classList.remove('done'); }}
      persist();
      applyFilters();
    }});
  }});

  document.querySelectorAll('button.copy').forEach(function (btn) {{
    btn.addEventListener('click', function () {{
      var node = document.getElementById(btn.dataset.target);
      if (!node) return;
      var text = node.innerText;
      var done = function () {{
        var old = btn.textContent;
        btn.textContent = 'скопировано';
        setTimeout(function () {{ btn.textContent = old; }}, 1200);
      }};
      if (navigator.clipboard) {{ navigator.clipboard.writeText(text).then(done, done); }}
      else {{
        var ta = document.createElement('textarea');
        ta.value = text; document.body.appendChild(ta); ta.select();
        try {{ document.execCommand('copy'); }} catch (e) {{}}
        document.body.removeChild(ta); done();
      }}
    }});
  }});

  var q = document.getElementById('q');
  var minscore = document.getElementById('minscore');
  var showsent = document.getElementById('showsent');

  function applyFilters() {{
    var term = (q.value || '').toLowerCase().trim();
    var min = parseInt(minscore.value, 10) || 0;
    var mode = showsent.value;
    document.querySelectorAll('tr.lead').forEach(function (row) {{
      var ok = true;
      if (term && row.dataset.text.indexOf(term) === -1) ok = false;
      if (parseInt(row.dataset.score, 10) < min) ok = false;
      if (mode === 'todo' && sent[row.dataset.id]) ok = false;
      row.classList.toggle('hidden', !ok);
    }});
  }}

  q.addEventListener('input', applyFilters);
  minscore.addEventListener('change', applyFilters);
  showsent.addEventListener('change', applyFilters);
  applyFilters();
</script>
</body>
</html>
"""
