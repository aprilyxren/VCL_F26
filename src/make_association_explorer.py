"""Write a self-contained HTML page for browsing person-place links by year.

Reads ``person_place_by_year.csv`` (from ``build_associations.py``) and embeds
it as JSON in a single HTML file: pick a year or range, search a person or
place, and see who is linked with what, with the evidence snippet.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


TEMPLATE = r"""<title>Virginia Company Links by Year</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Spectral:wght@500;600&family=Source+Sans+3:wght@400;600;700&display=swap">
<style>
:root{
  --paper:#f2f3ef; --surface:#fbfbf9; --ink:#1f2a2c; --muted:#5c6a6b; --rule:#d9ddd6;
  --accent:#1d6b72; --accent-soft:#dbeae9; --bar:#b9c9c6; --bar-on:#1d6b72;
  --bg-chip:#e4e5df; --bg-chip-ink:#5d625a; --ind:#7a4a1f; --ind-soft:#f1e5d8; --col:#5a4a7a; --col-soft:#e8e3f1;
  --focus:#1d6b72;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    color-scheme:dark;
    --paper:#121a1c; --surface:#182124; --ink:#e2e8e6; --muted:#97a6a7; --rule:#2b3739;
    --accent:#5fb0b8; --accent-soft:#1f3639; --bar:#34484b; --bar-on:#5fb0b8;
    --bg-chip:#283133; --bg-chip-ink:#a9b0ab; --ind:#e0a874; --ind-soft:#382a1e; --col:#b7a7e0; --col-soft:#2c2740;
    --focus:#5fb0b8;
  }
}
:root[data-theme="dark"]{
  color-scheme:dark;
  --paper:#121a1c; --surface:#182124; --ink:#e2e8e6; --muted:#97a6a7; --rule:#2b3739;
  --accent:#5fb0b8; --accent-soft:#1f3639; --bar:#34484b; --bar-on:#5fb0b8;
  --bg-chip:#283133; --bg-chip-ink:#a9b0ab; --ind:#e0a874; --ind-soft:#382a1e; --col:#b7a7e0; --col-soft:#2c2740;
  --focus:#5fb0b8;
}
*{box-sizing:border-box}
body{background:var(--paper);color:var(--ink);font:15px/1.5 "Source Sans 3",system-ui,-apple-system,"Segoe UI",sans-serif;padding-inline:16px;padding-block:20px 48px}
.wrap{max-width:1180px;margin:0 auto;display:grid;gap:18px}
header{display:grid;gap:4px}
h1{font:600 clamp(24px,3.4vw,32px)/1.15 Spectral,Georgia,"Times New Roman",serif;margin:0;text-wrap:balance}
.lede{color:var(--muted);margin:0;max-width:72ch}
.panel{background:var(--surface);border:1px solid var(--rule);border-radius:6px;padding:14px 16px;display:grid;gap:14px}
.label{font-size:12px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.years{display:grid;gap:8px}
.strip{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(26px,1fr);gap:4px;align-items:end;overflow-x:auto;padding-bottom:2px}
.yr{display:grid;gap:4px;justify-items:center;border:0;background:none;padding:0;cursor:pointer;color:var(--muted);font:inherit;font-size:11px;font-variant-numeric:tabular-nums}
.yr .b{width:100%;min-height:3px;background:var(--bar);border-radius:2px 2px 0 0}
.yr.on .b{background:var(--bar-on)} .yr.on{color:var(--ink);font-weight:700}
.yr:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
.row{display:flex;flex-wrap:wrap;gap:10px 18px;align-items:center}
.field{display:flex;gap:6px;align-items:center}
select,input[type=search]{font:inherit;color:var(--ink);background:var(--paper);border:1px solid var(--rule);border-radius:5px;padding:5px 8px}
input[type=search]{min-width:0;width:min(240px,70vw)}
select:focus-visible,input:focus-visible,button:focus-visible{outline:2px solid var(--focus);outline-offset:1px}
.chk{display:flex;gap:6px;align-items:center;cursor:pointer}
.btn{font:inherit;font-size:13px;border:1px solid var(--rule);background:var(--paper);color:var(--ink);border-radius:5px;padding:4px 10px;cursor:pointer}
.btn:hover{border-color:var(--accent)}
.summary{display:flex;flex-wrap:wrap;gap:6px 16px;align-items:baseline}
.summary b{font-variant-numeric:tabular-nums}
.tablewrap{overflow-x:auto;background:var(--surface);border:1px solid var(--rule);border-radius:6px}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--rule);vertical-align:top}
th{font-size:12px;letter-spacing:.05em;text-transform:uppercase;color:var(--muted);font-weight:700;white-space:nowrap;position:sticky;top:0;background:var(--surface)}
th button{all:unset;cursor:pointer} th button:focus-visible{outline:2px solid var(--focus)}
th.num,td.num{text-align:right}
tbody tr.main{cursor:pointer} tbody tr.main:hover td{background:var(--accent-soft)}
.who{font-weight:600}
.chip{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;padding:1px 6px;border-radius:3px;white-space:nowrap}
.k-place{background:var(--accent-soft);color:var(--accent)}
.k-indigenous_group{background:var(--ind-soft);color:var(--ind)}
.k-indigenous_collective{background:var(--col-soft);color:var(--col)}
.bgchip{background:var(--bg-chip);color:var(--bg-chip-ink);margin-left:6px}
.low{color:var(--muted);font-size:12px}
tr.ev td{background:var(--paper);color:var(--muted);font-size:13.5px;line-height:1.55}
tr.ev q{color:var(--ink);quotes:"“" "”"}
.more{display:flex;justify-content:center;padding:10px}
.empty{padding:28px;text-align:center;color:var(--muted)}
footer{color:var(--muted);font-size:13px;max-width:80ch}
@media (prefers-reduced-motion:no-preference){.yr .b{transition:height .2s ease}}
</style>

<div class="wrap">
  <header>
    <h1>Virginia Company Links by Year</h1>
    <p class="lede">People named in the Records of the Virginia Company, and the places and Indigenous peoples mentioned in the same court meeting or document. Pick a year, then search a name.</p>
  </header>

  <section class="panel" aria-label="Filters">
    <div class="years">
      <div class="row" style="justify-content:space-between">
        <span class="label">Year (bar height = number of links)</span>
        <div class="row">
          <label class="field" for="from">From <select id="from"></select></label>
          <label class="field" for="to">to <select id="to"></select></label>
          <button class="btn" id="all" type="button">All years</button>
        </div>
      </div>
      <div class="strip" id="strip" role="group" aria-label="Choose a year"></div>
    </div>
    <div class="row">
      <label class="field" for="person"><span class="label">Person</span><input type="search" id="person" placeholder="e.g. Yeardley"></label>
      <label class="field" for="place"><span class="label">Place or people</span><input type="search" id="place" placeholder="e.g. Martin's Hundred"></label>
      <label class="field" for="kind"><span class="label">Kind</span>
        <select id="kind">
          <option value="">All</option>
          <option value="place">Places</option>
          <option value="indigenous_group">Indigenous peoples (named)</option>
          <option value="indigenous_collective">Indigenous peoples (collective terms)</option>
        </select></label>
    </div>
    <div class="row">
      <label class="chk" for="hidebg"><input type="checkbox" id="hidebg" checked> Hide Virginia, England, London</label>
      <label class="chk" for="close"><input type="checkbox" id="close"> Only close mentions (within ~300 characters)</label>
      <label class="chk" for="nolow"><input type="checkbox" id="nolow"> Skip low-confidence dates</label>
    </div>
  </section>

  <div class="summary" id="summary" aria-live="polite"></div>

  <div class="tablewrap">
    <table>
      <thead><tr>
        <th><button data-sort="p" type="button">Person</button></th>
        <th><button data-sort="t" type="button">Associated with</button></th>
        <th>Kind</th>
        <th class="num"><button data-sort="m" type="button">Meetings</button></th>
        <th class="num"><button data-sort="c" type="button">Close</button></th>
        <th class="num"><button data-sort="d" type="button">Nearest</button></th>
        <th><button data-sort="y" type="button">Years</button></th>
      </tr></thead>
      <tbody id="rows"></tbody>
    </table>
    <div class="more" id="morebox" hidden><button class="btn" id="more" type="button">Show more</button></div>
  </div>

  <footer>
    <p><b>Meetings</b> counts court meetings or dated documents where both are named. <b>Close</b> counts those where the two names fall within about 300 characters, usually the same sentence or entry. <b>Nearest</b> is the shortest distance in characters. A year is the meeting's start date. Court Book dates are converted from Old Style, so January to March 24 count toward the following year. Click a row to see the text.</p>
  </footer>
</div>

<script id="data" type="application/json">__DATA__</script>
<script>
(() => {
  const RAW = JSON.parse(document.getElementById('data').textContent);
  const F = RAW.fields, rows = RAW.rows.map(r => Object.fromEntries(F.map((f, i) => [f, r[i]])));
  const KIND = {place:'Place', indigenous_group:'Indigenous people', indigenous_collective:'Collective term'};
  const years = [...new Set(rows.map(r => r.y))].sort();
  const $ = id => document.getElementById(id);
  const state = {from:'1622', to:'1622', sort:'c', dir:-1, shown:200, open:new Set()};

  const fromSel = $('from'), toSel = $('to');
  for (const y of years) { fromSel.add(new Option(y, y)); toSel.add(new Option(y, y)); }

  function filters() {
    const person = $('person').value.trim().toLowerCase();
    const place = $('place').value.trim().toLowerCase();
    return {person, place, kind:$('kind').value, hidebg:$('hidebg').checked, close:$('close').checked, nolow:$('nolow').checked};
  }
  function base(f, ignoreYears) {
    return rows.filter(r =>
      (ignoreYears || (r.y >= state.from && r.y <= state.to)) &&
      (!f.person || r.p.toLowerCase().includes(f.person)) &&
      (!f.place || r.t.toLowerCase().includes(f.place)) &&
      (!f.kind || r.k === f.kind) && !(f.hidebg && r.bg) && !(f.close && !r.c) && !(f.nolow && r.q === 'low'));
  }
  function strip(f) {
    const counts = {}; for (const r of base(f, true)) counts[r.y] = (counts[r.y] || 0) + 1;
    const max = Math.max(1, ...Object.values(counts));
    $('strip').innerHTML = years.map(y => {
      const n = counts[y] || 0, on = y >= state.from && y <= state.to;
      return `<button class="yr${on ? ' on' : ''}" type="button" data-y="${y}" aria-pressed="${on}" title="${y}: ${n} links">
        <span class="b" style="height:${Math.round(6 + 58 * n / max)}px"></span><span>${y.slice(2)}</span></button>`;
    }).join('');
  }
  function aggregate(list) {
    const m = new Map();
    for (const r of list) {
      const key = r.p + '\u0000' + r.t;
      let a = m.get(key);
      if (!a) m.set(key, a = {p:r.p, t:r.t, k:r.k, bg:r.bg, m:0, c:0, d:null, ys:new Set(), ex:'', exd:null, exy:''});
      a.m += r.m; a.c += r.c; a.ys.add(r.y);
      if (r.d !== null && (a.d === null || r.d < a.d)) a.d = r.d;
      if (r.ex && (a.exd === null || (r.d !== null && r.d < a.exd))) { a.ex = r.ex; a.exd = r.d; a.exy = r.fd; }
    }
    return [...m.values()];
  }
  const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  function render() {
    fromSel.value = state.from; toSel.value = state.to;
    const f = filters(); strip(f);
    const list = aggregate(base(f, false));
    const s = state.sort, dir = state.dir;
    list.sort((a, b) => {
      const v = s === 'y' ? [[...a.ys].sort()[0], [...b.ys].sort()[0]] : s === 'd' ? [a.d ?? 1e9, b.d ?? 1e9] : [a[s], b[s]];
      return (v[0] < v[1] ? -1 : v[0] > v[1] ? 1 : 0) * dir || b.m - a.m;
    });
    const people = new Set(list.map(a => a.p)).size, targets = new Set(list.map(a => a.t)).size;
    const span = state.from === state.to ? state.from : `${state.from}–${state.to}`;
    $('summary').innerHTML = `<span><b>${list.length.toLocaleString()}</b> links in <b>${span}</b></span>
      <span><b>${people}</b> people</span><span><b>${targets}</b> places or peoples</span>`;
    const shown = list.slice(0, state.shown);
    $('rows').innerHTML = shown.length ? shown.map((a, i) => {
      const key = esc(a.p + '|' + a.t), open = state.open.has(a.p + '|' + a.t);
      const ys = [...a.ys].sort(); const yr = ys.length > 1 ? `${ys[0]}–${ys[ys.length - 1]}` : ys[0];
      return `<tr class="main" data-key="${key}" tabindex="0" aria-expanded="${open}">
        <td class="who">${esc(a.p)}</td>
        <td>${esc(a.t)}${a.bg ? '<span class="chip bgchip">background</span>' : ''}</td>
        <td><span class="chip k-${a.k}">${KIND[a.k]}</span></td>
        <td class="num">${a.m}</td><td class="num">${a.c || '<span class="low">0</span>'}</td>
        <td class="num">${a.d === null ? '<span class="low">other page</span>' : a.d}</td>
        <td>${yr}</td></tr>` + (open ? `<tr class="ev"><td colspan="7">${a.ex ? `<q>${esc(a.ex)}</q> <span class="low">(${esc(a.exy)})</span>` : 'Named on different pages of the same meeting.'}</td></tr>` : '');
    }).join('') : `<tr><td colspan="7" class="empty">No links match. Try another year, clear a search, or include background places.</td></tr>`;
    $('morebox').hidden = list.length <= state.shown;
    $('more').textContent = `Show more (${(list.length - state.shown).toLocaleString()} left)`;
  }

  $('strip').addEventListener('click', e => {
    const b = e.target.closest('.yr'); if (!b) return;
    state.from = state.to = b.dataset.y; state.shown = 200; render();
  });
  fromSel.addEventListener('change', () => { state.from = fromSel.value; if (state.to < state.from) state.to = state.from; state.shown = 200; render(); });
  toSel.addEventListener('change', () => { state.to = toSel.value; if (state.from > state.to) state.from = state.to; state.shown = 200; render(); });
  $('all').addEventListener('click', () => { state.from = years[0]; state.to = years[years.length - 1]; state.shown = 200; render(); });
  for (const id of ['person', 'place']) $(id).addEventListener('input', () => { state.shown = 200; render(); });
  for (const id of ['kind', 'hidebg', 'close', 'nolow']) $(id).addEventListener('change', () => { state.shown = 200; render(); });
  $('more').addEventListener('click', () => { state.shown += 200; render(); });
  document.querySelector('thead').addEventListener('click', e => {
    const b = e.target.closest('button[data-sort]'); if (!b) return;
    const s = b.dataset.sort; state.dir = state.sort === s ? -state.dir : (s === 'p' || s === 't' || s === 'y' ? 1 : -1);
    state.sort = s; render();
  });
  const toggle = tr => { const k = tr.dataset.key.replace(/&amp;/g,'&').replace(/&quot;/g,'"').replace(/&lt;/g,'<').replace(/&gt;/g,'>');
    state.open.has(k) ? state.open.delete(k) : state.open.add(k); render(); };
  $('rows').addEventListener('click', e => { const tr = e.target.closest('tr.main'); if (tr) toggle(tr); });
  $('rows').addEventListener('keydown', e => { const tr = e.target.closest('tr.main'); if (tr && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); toggle(tr); } });
  render();
})();
</script>
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("by_year_csv", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    fields = ["y", "p", "t", "k", "bg", "m", "c", "d", "q", "fd", "ex"]
    rows = []
    with args.by_year_csv.open(encoding="utf-8", newline="") as handle:
        for r in csv.DictReader(handle):
            rows.append([
                r["year"], r["person"], r["target_name"], r["target_type"], 1 if r["background"] else 0,
                int(r["meetings"]), int(r["close_meetings"]),
                int(r["min_distance"]) if r["min_distance"] != "" else None,
                r["date_confidence"], r["first_date"], r["example"][:260],
            ])
    data = json.dumps({"fields": fields, "rows": rows}, ensure_ascii=False, separators=(",", ":"))
    html = TEMPLATE.replace("__DATA__", data.replace("</", "<\\/"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html, encoding="utf-8")
    print(f"Wrote {args.output} ({len(rows)} rows, {len(html) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
