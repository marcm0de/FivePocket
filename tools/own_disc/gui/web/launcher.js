// FivePocket Builder window: draws the state the local server (web_app.py, over builder_core.py) reports and sends the
// player's clicks back to it. It decides nothing itself. Without a server (a file opened for a look) it draws
// window.DEMO_STATE.
'use strict';

const token = new URLSearchParams(location.search).get('t') || '';
let state = window.DEMO_STATE || null;
let tab = 'build';
let section = { progress: null, results: 0, settings: 'general' };
let detail = null;            // the home page's open sidebar item: 'play' | 'install' | an edition ident
let dialog = window.DEMO_DIALOG || null;   // 'build' | null
let error = null;             // {title, text} shown in the error dialog
let seenFailure = null;
let logSince = 0;

const $ = (id) => document.getElementById(id);
const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') e.className = v; else if (k === 'text') e.textContent = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v); else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const kid of kids) if (kid != null) e.append(kid);
  return e;
};

async function api(path, body) {
  if (window.DEMO_STATE) return state;
  const r = await fetch(path, { method: body ? 'POST' : 'GET', headers: { 'X-Token': token, 'Content-Type': 'application/json' },
                                body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) { let t = await r.text(); try { t = JSON.parse(t); } catch (e) { /* plain text */ } throw new Error(t); }
  return r.json();
}
async function act(name, args = {}) {
  try { state = await api('/api/action', { name, args }); return true; }
  catch (e) { error = { title: 'Error', text: String(e.message || e) }; render(); return false; }
  finally { render(); }
}

// ---------------------------------------------------------------------------------------------- icons (our own)
function icon(kind, st) {
  const c = { ok: '#fff', bad: '#ff7a70', empty: '#6a6a6a', on: '#fff', off: '#878787', na: '#444' }[st];
  const fill = st === 'on' ? c : 'none';
  const svg = {
    disc: `<circle cx="15" cy="15" r="12" fill="none" stroke="${c}" stroke-width="2"/><circle cx="15" cy="15" r="3" fill="none" stroke="${c}" stroke-width="2"/>`,
    thor: `<rect x="8" y="3" width="14" height="10" rx="1.5" fill="none" stroke="${c}" stroke-width="2"/><rect x="8" y="16" width="14" height="11" rx="1.5" fill="${fill}" stroke="${c}" stroke-width="2"/>`,
    fold: `<rect x="9" y="3" width="12" height="24" rx="2" fill="${fill}" stroke="${c}" stroke-width="2"/>`,
    ios: `<rect x="5" y="4" width="20" height="22" rx="2.5" fill="${fill}" stroke="${c}" stroke-width="2"/>`,
    duo: `<rect x="3" y="7" width="11" height="16" rx="2" fill="${fill}" stroke="${c}" stroke-width="2"/><rect x="16" y="7" width="11" height="16" rx="2" fill="${fill}" stroke="${c}" stroke-width="2"/>`,
    extra: `<rect x="7" y="3" width="16" height="24" rx="2" fill="none" stroke="${c}" stroke-width="2"/><path d="M11 10h8M11 15h8M11 20h5" stroke="${c}" stroke-width="2"/>`,
  }[kind] || '';
  const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  s.setAttribute('viewBox', '0 0 30 30'); s.setAttribute('width', '30'); s.setAttribute('height', '30');
  s.innerHTML = svg;
  return s;
}

// --------------------------------------------------------------------------------------------- shared controls
function pill(label, onclick, kind = '', enabled = true) {
  return el('button', { class: 'pill ' + kind, type: 'button', disabled: !enabled, onclick, text: label });
}
function box(on, onclick, enabled = true) {
  return el('button', { class: 'box' + (on ? ' on' : ''), type: 'button', disabled: !enabled, onclick, 'aria-pressed': on ? 'true' : 'false' });
}
function row(title, desc, controls = [], cls = '') {
  const label = el('div', { class: 'label' }, el('div', { class: 'title', text: title }));
  for (const d of [].concat(desc || [])) {
    if (d == null || d === '') continue;
    label.append(typeof d === 'string' ? el('div', { class: 'desc', text: d }) : d);
  }
  return el('div', { class: 'row ' + cls }, label, el('div', { class: 'controls' }, ...controls));
}
const desc = (text, cls = '') => el('div', { class: 'desc ' + cls, text });
const mono = (text) => el('div', { class: 'desc mono', text });

// ---------------------------------------------------------------------------------------------------- sidebar
// home (measured on the 1288 x 600 capture): "Now available" cap 153, its item centred at 201; items every 50 px
function renderHomeSide() {
  const discs = $('disc-list'), eds = $('edition-list');
  discs.replaceChildren(); eds.replaceChildren();
  let y = 201;
  for (const slot of ['play', 'install']) {
    const d = state.discs[slot];
    const st = !d ? 'empty' : (d.checking ? 'empty' : (d.ok ? 'ok' : 'bad'));
    const sub = !d ? 'Choose your disc image' : (d.checking ? 'Checking…' : (d.ok ? d.name : (d.problems[0] || d.name)));
    discs.append(item(slot, 'disc', st, state.labels[slot], sub, y));
    y += 50;
  }
  // the player's own title update and DLC (next update): shown while a chosen edition takes them
  for (const x of state.extras || []) {
    if (!x.shown) continue;
    const st = !x.path ? 'empty' : (x.problems.length ? 'bad' : 'ok');
    const sub = !x.path ? (x.needed ? 'Needed: choose your file' : 'Optional: choose your files') : (x.problems[0] || x.name);
    discs.append(item(x.slot, 'extra', st, x.label, sub, y));
    y += 50;
  }
  $('head-editions').style.top = (y - 50 + 41 - 2) + 'px';
  y += 36;
  const twice = (e) => state.editions.filter((x) => x.shown && x.key === e.key).length > 1;
  for (const e of state.editions) {
    if (!e.shown) continue;
    const st = e.available ? (e.chosen ? 'on' : 'off') : 'na';
    const sub = (e.available ? (e.chosen ? 'Will be built' : 'Not built') : (e.reason ? 'Made ' + e.reason : 'Not on this computer')) + (twice(e) ? ' · ' + e.package : '');
    eds.append(item(e.ident, e.key, st, e.short + (twice(e) ? ` (${e.package})` : ''), sub, y));
    y += 50;
  }
  $('side-more').classList.toggle('dim', y < window.innerHeight - 140);
}

function item(key, kind, st, title, sub, y) {
  // the launcher's list item: a 30 px icon and the name only (grey; white when open); the state is the icon's
  const r = el('div', { class: 'item' + (detail === key ? ' on' : ''), style: `top:${y - 22}px`, title: sub },
    el('div', { class: 'icon' }, icon(kind, st)),
    el('div', { class: 'text' }, el('b', { text: title })));
  r.addEventListener('click', () => pick(key, kind));
  return r;
}

function pick(key, kind) {
  if (kind === 'disc' && !state.discs[key]) { detail = key; act('choose_disc', { slot: key }); return; }
  if (kind === 'extra' && !((state.extras || []).find((x) => x.slot === key) || {}).path) {
    detail = key; act('choose_extra', { slot: key, folder: key === 'dlc' }); return;
  }
  if (kind !== 'disc' && kind !== 'extra' && detail === key) {
    const e = state.editions.find((x) => x.ident === key);
    if (e && e.available && !state.running) { act('toggle_edition', { ident: key }); return; }
  }
  detail = detail === key ? null : key;
  render();
}

// the other pages (measured on the 1280 x 893 settings capture): a heading, then items every 34 px, accent bar
function renderSections(head, list, current, choose) {
  $('section-head').textContent = head;
  $('sections').replaceChildren(...list.map((s) => {
    const a = el('a', { class: s.id === current ? 'on' : '' }, el('span', { text: s.label }), s.tag ? el('span', { class: 'tag', text: s.tag }) : null);
    a.addEventListener('click', () => choose(s.id));
    return a;
  }));
}

// ------------------------------------------------------------------------------------------------------ pages
function show(name) { tab = name; detail = null; render(); }

function render() {
  if (!state) return;
  document.body.className = 'tab-' + tab;
  document.querySelectorAll('#nav a').forEach((a) => a.classList.toggle('on', a.dataset.tab === tab));
  $('page-build').hidden = tab !== 'build';
  $('page-rows').hidden = tab === 'build';
  const bd = state.backdrops.find((b) => b.id === state.backdrop);
  $('backdrop').style.setProperty('--bd', bd ? `url("${bd.url}")` : 'none');
  $('chrome').hidden = !state.frameless;
  $('side-version').textContent = state.version_lines.join('\n');
  if (tab === 'build') { renderHomeSide(); renderBuild(); } else { $('detail').hidden = true; }
  if (tab === 'progress') renderProgress();
  if (tab === 'results') renderResults();
  if (tab === 'settings') renderSettings();
  renderDialogs();
}

// the logo's second line: the edition(s) the BUILD button makes, by the names the maintainer gave them
const EDITION_WORD = { thor: 'THOR', fold: 'ANDROID', ios: 'iOS', duo: 'DUO' };
// the editions' words in the owner's logo file names (five-pocket-<word>-<colourway>.png)
const LOGO_WORD = { thor: 'thor', fold: 'android', ios: 'ios', duo: 'duo' };
const firstChosen = () => state.editions.find((e) => e.available && e.chosen);

function renderBuild() {
  const words = [...new Set(state.editions.filter((e) => e.available && e.chosen).map((e) => EDITION_WORD[e.key] || e.key.toUpperCase()))];
  const lines = ['FIVE', 'POCKET', words.join(' + ')];
  if (lines.join('|') !== renderBuild.drawn) {      // draw the lettering again only when the words change
    renderBuild.drawn = lines.join('|');
    const stack = $('stack');
    const height = drawLettering(stack, lines, { width: 221, unit: 5.2, gap: 4 });
    stack.setAttribute('transform', `translate(2 ${Math.max(0, 186 - height).toFixed(1)})`);   // sits on the button
  }
  // a logo file of the player's replaces the drawn lettering: the one for the chosen edition on the chosen backdrop,
  // else that edition's on any backdrop, else web/logo.*
  const first = firstChosen();
  const mine = first && state.logos ? state.logos[LOGO_WORD[first.key]] : null;
  const src = (mine && (mine[state.backdrop] || mine['*'])) || state.logo || null;
  const logo = $('logo-file');
  logo.hidden = !src;
  $('wordmark').style.display = src ? 'none' : '';
  if (src && logo.getAttribute('src') !== src) logo.setAttribute('src', src);
  const p = state.primary;
  $('primary-label').textContent = p.label.toUpperCase();
  placeButtonArt(p.label.toLowerCase());
  $('primary').disabled = !p.enabled;
  note(state.running ? 'A build is running. PROGRESS shows each step.' : (p.enabled ? '' : (state.ready_problems[0] || '')), !p.enabled && !state.running);
  renderDetail();
}

// the player's button picture for this word (else the blank one with the word over it): its rim, found from its
// alpha on the middle row and column, is fitted to the launcher button's box (258 x 66 at 320, 349)
const rims = {};
function placeButtonArt(word) {
  const art = state.button_art || {};
  const src = art[word] || art.blank;
  const btn = $('primary'), img = $('primary-art'), label = $('primary-label');
  btn.classList.toggle('art', !!src);
  img.hidden = !src;
  if (!src) btn.style.height = '';                  // the drawn button keeps the launcher's 66 px
  label.style.visibility = src && art[word] ? 'hidden' : '';
  if (!src) return;
  const fit = () => {                              // the button takes the picture's proportions: 258 wide, its height
    const r = rims[src];
    if (!r) return;
    const s = 258 / (r.right - r.left + 1), h = (r.bottom - r.top + 1) * s;
    btn.style.height = h.toFixed(1) + 'px';
    img.style.width = (img.naturalWidth * s) + 'px';
    img.style.left = (-r.left * s) + 'px';
    img.style.top = (-r.top * s) + 'px';
  };
  if (img.getAttribute('src') !== src) {
    img.onload = () => {
      if (!rims[src]) {
        const c = document.createElement('canvas'); c.width = img.naturalWidth; c.height = img.naturalHeight;
        const g = c.getContext('2d'); g.drawImage(img, 0, 0);
        const a = g.getImageData(0, 0, c.width, c.height).data, at = (x, y) => a[(y * c.width + x) * 4 + 3];
        const my = c.height >> 1, mx = c.width >> 2;
        let left = 0, right = c.width - 1, top = 0, bottom = c.height - 1;
        while (left < c.width && at(left, my) < 200) left++;
        while (right > 0 && at(right, my) < 200) right--;
        while (top < c.height && at(mx, top) < 200) top++;
        while (bottom > 0 && at(mx, bottom) < 200) bottom--;
        rims[src] = { left, right, top, bottom };
      }
      fit();
    };
    img.setAttribute('src', src);
  } else fit();
}

function note(text, warn) {
  const n = $('primary-note');
  n.textContent = text; n.classList.toggle('warn', !!warn);
}


function renderDetail() {
  // the open sidebar item, in the place and black of the launcher's card (635..1068 x 159), with the settings rows
  const b = $('detail');
  if (!detail) { b.hidden = true; return; }
  b.hidden = false;
  const close = el('a', { class: 'close', onclick: () => { detail = null; render(); } });
  close.innerHTML = '<svg viewBox="0 0 12 12" width="12" height="12"><path d="M1 1l10 10M11 1L1 11" stroke="currentColor" stroke-width="1.6"/></svg>';
  b.replaceChildren(close);
  const small = (label, onclick, kind = '', enabled = true) => pill(label, onclick, 'small ' + kind, enabled);
  if (detail === 'play' || detail === 'install') {
    const d = state.discs[detail];
    b.append(el('h4', { text: state.labels[detail] }));
    if (!d) b.append(desc('Choose your own disc image (.iso) or an extracted disc folder. It is checked the way the build identifies it.'));
    else if (d.checking) b.append(desc('Checking ' + d.name + '\u2026'));
    else {
      b.append(mono(d.path));
      b.append(desc(d.kind + (d.size ? ', ' + d.size_text : '')));
      if (d.recognised) b.append(desc((d.role === 'play' ? 'Recognised: ' : '') + d.recognised, d.ok ? 'ok' : ''));
      for (const w of d.warnings) b.append(desc(w, 'warn'));
      for (const pr of d.problems) b.append(desc(pr, 'bad'));
      if (d.sha256) b.append(desc('Image SHA-256, for your own records:'), mono(d.sha256));
      if (d.hashing) b.append(desc('Image SHA-256: computing, ' + d.hashing));
    }
    b.append(el('div', { class: 'detail-actions' },
      small('Choose', () => act('choose_disc', { slot: detail }), 'white', !state.running),
      small('Folder', () => act('choose_disc', { slot: detail, folder: true }), '', !state.running),
      small('SHA-256', () => act('hash_disc', { slot: detail }), '', !!(d && d.is_file && !d.sha256 && !d.hashing))));
    return;
  }
  const x = (state.extras || []).find((v) => v.slot === detail);
  if (x) {
    const dlc = x.slot === 'dlc';
    b.append(el('h4', { text: x.label }));
    b.append(desc(dlc
      ? 'Your own DLC packs, as they came off your console: a folder of them, or one pack. Each is read through and checked when the build starts, and your bundle carries them for the app.'
      : 'Your own title-update file (the package your console downloaded), or a folder of its files. The build patches your disc\u2019s game with it.'));
    if (x.path) b.append(mono(x.path));
    if (x.summary) b.append(desc(x.summary, 'ok'));
    for (const pr of x.problems) b.append(desc(pr, 'bad'));
    b.append(el('div', { class: 'detail-actions' },
      small(dlc ? 'Folder' : 'Choose', () => act('choose_extra', { slot: x.slot, folder: dlc }), 'white', !state.running),
      small(dlc ? 'One pack' : 'Folder', () => act('choose_extra', { slot: x.slot, folder: !dlc }), '', !state.running),
      small('Remove', () => act('clear_extra', { slot: x.slot }), '', !!x.path && !state.running)));
    return;
  }
  const e = state.editions.find((x) => x.ident === detail);
  if (!e) { b.hidden = true; return; }
  b.append(el('h4', { text: e.label }));
  b.append(desc(e.kind === 'bundle'
    ? 'Makes the game\u2019s code bundle (.bundle). The FivePocket app for this edition (its APK from the release page) imports it from the phone\u2019s storage and shows its seal.'
    : 'Makes the unsigned iOS app (.ipa). Sign it with your own Apple ID before installing.'));
  if (!e.available) b.append(desc(e.reason, 'warn'));
  for (const pr of e.problems || []) b.append(desc(pr, 'bad'));
  if (e.root) b.append(desc('Builder package:'), mono(e.root));
  if (e.available) b.append(el('div', { class: 'detail-actions' },
    small(e.chosen ? 'Skip it' : 'Build it', () => act('toggle_edition', { ident: e.ident }), 'white', !state.running)));
}

// PROGRESS: the steps in the left column (the running one marked), the chosen step's rows and the log on the right
function renderProgress() {
  const s = state.session, rows = $('rows'), log = $('log');
  rows.replaceChildren();
  if (!s) {
    renderSections('Build', [], null, () => {});
    rows.append(row('No build yet', 'Choose your discs and press BUILD.', [pill('Build', () => show('build'), 'white')], 'short'));
    log.hidden = true;
    return;
  }
  const running = s.steps.findIndex((x) => x.state === 'running');
  if (section.progress == null || (running >= 0 && state.running)) section.progress = running >= 0 ? running : s.steps.findIndex((x) => x.state !== 'done');
  if (section.progress < 0) section.progress = s.steps.length - 1;
  const mark = { pending: '', running: '…', done: '✓', failed: '✗', cancelled: 'stopped' };
  renderSections('Build', s.steps.map((x, i) => ({ id: i, label: x.label, tag: mark[x.state] })), section.progress, (i) => { section.progress = i; render(); });
  const st = s.steps[section.progress];
  const word = { pending: 'Waiting', running: 'Running', done: 'Finished', failed: 'Failed', cancelled: 'Stopped' }[st.state];
  const controls = [];
  if (state.running) controls.push(pill('Cancel', () => act('cancel')));
  else if (!s.finished) {
    controls.push(pill('Start over', () => act('start_over')));
    controls.push(pill('Resume', () => act('resume'), 'white', !s.resume_problems.length));
  } else controls.push(pill('Results', () => show('results'), 'white'));
  rows.append(row(st.label, [desc(word + (st.time ? ' after ' + st.time : ''), st.state === 'failed' ? 'bad' : (st.state === 'done' ? 'ok' : '')),
    ...s.resume_problems.map((p) => desc(p, 'bad'))], controls));
  if (st.stages && st.stages.length) {
    const done = st.stages.filter((x) => ['ran', 'skipped'].includes(x.status) || st.state === 'done').length;
    rows.append(el('div', { class: 'heading', text: `Build stages: ${done} of ${st.stages.length}` }));
    for (const x of st.stages) {
      const status = st.state === 'done' && !x.status ? 'skipped' : x.status;
      const label = { ran: 'Done', skipped: 'Kept', FAILED: 'Failed', running: 'Running' }[status] || '';
      const cls = { ran: 'done', skipped: 'done', FAILED: 'failed', running: 'running' }[status] || '';
      const r = row(x.id, x.note || null, [el('span', { class: 'status ' + cls, text: label })], 'short');
      rows.append(r);
    }
  }
  log.hidden = false;
  log.style.top = (rows.offsetTop + rows.offsetHeight + 24) + 'px';
}

// RESULTS: one section per finished file; its rows: file, size, SHA-256, seal, what to do next
function renderResults() {
  const outs = state.outputs, rows = $('rows');
  $('log').hidden = true;
  rows.replaceChildren();
  renderSections('Results', outs.map((o, i) => ({ id: i, label: o.kind === 'apk' ? o.edition + ' app' : o.edition })), section.results, (i) => { section.results = i; render(); });
  if (!outs.length) { rows.append(row('Nothing built yet', 'The finished files and their hashes appear here.', [], 'short')); return; }
  const i = Math.min(section.results, outs.length - 1), o = outs[i];
  rows.append(row(o.name, [mono(o.path)], [pill('Open folder', () => act('open', { what: 'output', index: i }))]));
  if (o.missing) rows.append(row('Not downloaded', desc(o.missing, 'warn')));
  else {
    rows.append(row('Size', o.size_text));
    rows.append(row('SHA-256', [mono(o.sha256 || '')], [pill('Copy', () => act('copy_hashes'))]));
  }
  if (o.seal) rows.append(row('Bundle seal', [mono(o.seal), desc('The FivePocket app shows the same seal when it imports the bundle.')]));
  if (o.verified != null) rows.append(row('Verify', desc(o.verified ? 'Verified by the builder’s verify step' : 'Not verified', o.verified ? 'ok' : 'bad')));
  if (o.bundle_id) rows.append(row('Bundle identifier', [mono(o.bundle_id), desc('Always sign with this identifier: another one makes a new app with empty storage.', 'warn')]));
  rows.append(row('Next', o.kind === 'apk'
    ? 'Install this APK on the phone: it is the FivePocket ' + o.edition + ' app. Then import the bundle beside it in that app.'
    : o.kind === 'bundle'
      ? 'Copy this bundle to the phone and import it in the FivePocket ' + o.edition + ' app (the APK beside it).'
      : 'Sign the .ipa with your own Apple ID (see the README) and install it.'));
}

// SETTINGS: General (backdrop, output folder, log), Compilers, Builder packages, About
function renderSettings() {
  const rows = $('rows');
  $('log').hidden = true;
  rows.replaceChildren();
  const list = [{ id: 'general', label: 'General' }, { id: 'compilers', label: 'Compilers' },
                { id: 'packages', label: 'Builder packages' }, { id: 'about', label: 'About' }];
  renderSections('Builder settings', list, section.settings, (id) => { section.settings = id; render(); });
  if (section.settings === 'general') {
    const select = el('div', { class: 'select' }, el('span', { text: (state.backdrops.find((b) => b.id === state.backdrop) || {}).name || 'None' }));
    select.insertAdjacentHTML('beforeend', '<svg viewBox="0 0 12 7" width="12" height="7"><path d="M1 1l5 5 5-5" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>');
    const choice = el('select', { 'aria-label': 'Backdrop' }, ...state.backdrops.map((b) => el('option', { value: b.id, text: b.name })));
    choice.value = state.backdrop || '';
    choice.addEventListener('change', () => act('set_backdrop', { id: choice.value }));
    select.append(choice);
    rows.append(row('Backdrop', null, [select]));
    rows.append(el('div', { class: 'row short' }, el('div', { class: 'thumbs' }, ...state.backdrops.map((b) =>
      el('div', { class: 'thumb' + (b.id === state.backdrop ? ' on' : ''), title: b.name, style: `background-image:url("${b.thumb || b.url}")`,
                  onclick: () => act('set_backdrop', { id: b.id }) })))));
    // the app icon (owner 2026-11-22): the logo of the edition being built, in the colourway picked here; each build passes it
    const icons = state.app_icons || [];
    const picked = icons.find((c) => c.id === state.app_icon) || icons[0];
    const iconSelect = el('div', { class: 'select' }, el('span', { text: picked ? picked.name : '' }));
    iconSelect.insertAdjacentHTML('beforeend', '<svg viewBox="0 0 12 7" width="12" height="7"><path d="M1 1l5 5 5-5" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>');
    const iconChoice = el('select', { 'aria-label': 'App icon' }, ...icons.map((c) => el('option', { value: c.id, text: c.name })));
    iconChoice.value = state.app_icon || '';
    iconChoice.addEventListener('change', () => act('set_app_icon', { id: iconChoice.value }));
    iconSelect.append(iconChoice);
    rows.append(row('App icon', 'The logo your app shows on the home screen. Every app or bundle you build gets this icon.', [iconSelect]));
    const previews = ((state.logos || {})[LOGO_WORD[(firstChosen() || {}).key]]) || {};
    rows.append(el('div', { class: 'row short' }, el('div', { class: 'thumbs' }, ...icons.map((c) =>
      el('div', { class: 'thumb icon' + (c.id === state.app_icon ? ' on' : ''), title: c.name, text: previews[c.id] ? '' : c.name,
                  style: previews[c.id] ? `background-image:url("${previews[c.id]}")` : null,
                  onclick: () => act('set_app_icon', { id: c.id }) })))));
    rows.append(row('Output folder', [mono(state.out), ...state.out_problems.map((p) => desc(p, 'bad')),
      desc('Where the finished bundle or app is written. The work files stay in each builder package’s local-analysis folder; an interrupted build continues from them.')],
      [pill('Default', () => act('default_out'), '', state.out_custom && !state.running), pill('Browse', () => act('choose_out'), '', !state.running)]));
    rows.append(row('Build log', [mono(state.log_file)], [pill('Open', () => act('open', { what: 'log' }))]));
  } else if (section.settings === 'compilers') {
    if (state.compilers === 'checking') rows.append(row('Compilers', 'Checking the compilers already fetched (their files are hashed; this can take a minute)…'));
    else if (state.compilers === 'ready') rows.append(row('Compilers', desc('Fetched and verified for every chosen edition.', 'ok')));
    else {
      for (const t of state.licences) rows.append(row(t.name, [desc(`${t.mb} MB, fetched once from its publisher and checked against the SHA-256 pinned in the builder.`), mono(t.download), mono('Terms: ' + t.url)]));
      rows.append(row('Accept the licences', 'I have read these terms, accept them and want the compilers fetched. Nothing is downloaded before this is ticked.',
        [box(state.accepted, () => act('accept', { value: !state.accepted }))]));
    }
  } else if (section.settings === 'packages') {
    for (const e of state.editions.filter((x) => x.root)) rows.append(row(e.label, [mono(e.root), e.available ? null : desc(e.reason, 'warn')]));
    rows.append(row('Add a builder folder', 'Each builder download makes its own edition. Unpack another one beside this one, or pick its folder.', [pill('Browse', () => act('add_package'), '', !state.running)]));
  } else {
    rows.append(row('FivePocket Builder', state.version_lines.slice(1).join(' · ')));
    rows.append(row('Discord', 'The FivePocket community server.', [pill('Open', () => act('open_link', { key: 'discord' }))]));
    rows.append(row('X', 'News about FivePocket.', [pill('Open', () => act('open_link', { key: 'x' }))]));
    rows.append(row('GitHub', 'Releases, source and the builder guide.', [pill('Open', () => act('open_link', { key: 'github' }))]));
    rows.append(row('Your files stay yours', 'The builder reads your own discs on this computer. It sends nothing anywhere; its only download is the compilers, after you accept their licences.'));
  }
}

// ---------------------------------------------------------------------------------------------------- dialogs
function renderDialogs() {
  const s = state.session;
  const failed = s && !state.running && s.steps.find((x) => x.state === 'failed');
  if (failed && state.failure && seenFailure !== state.failure.key) {
    seenFailure = state.failure.key;
    error = { title: 'The build stopped', text: state.failure.text };
  }
  const build = dialog === 'build' && !state.running;
  $('build-dialog').hidden = !build;
  $('error-dialog').hidden = !error;
  $('veil').hidden = !(build || error);
  if (build) renderBuildDialog();
  if (error) { $('error-title').textContent = error.title; $('error-text').textContent = error.text; }
}

function renderBuildDialog() {
  const chosen = state.editions.filter((e) => e.available && e.chosen);
  const bd = state.backdrops.find((b) => b.id === state.backdrop);
  $('build-art').style.backgroundImage = bd ? `url("${bd.url}")` : 'none';
  $('build-sub').textContent = chosen.length
    ? chosen.map((e) => e.short).join(' and ') + (chosen.length > 1 ? ' are' : ' is') + ' going to be built from your discs.'
    : 'Choose what to build.';
  $('build-out').textContent = state.out;
  const sp = state.space;
  $('build-space').replaceChildren(document.createTextNode(sp.required_text + ' REQUIRED'), el('b', { text: sp.available_text + ' AVAILABLE' }), el('i', { text: sp.enough ? '✓' : '✗' }));
  $('build-space').classList.toggle('short', !sp.enough);
  const opts = $('build-options');
  opts.replaceChildren();
  for (const e of state.editions.filter((x) => x.shown)) {
    const o = el('div', { class: 'option' + (e.available ? (e.chosen ? ' on' : '') : ' na') },
      el('div', {}, document.createTextNode(e.short), el('small', { text: e.available ? (e.kind === 'bundle' ? 'Code bundle for the Android app' : 'Unsigned iOS app') : (e.reason ? 'Made ' + e.reason : 'Not on this computer') })),
      box(e.chosen && e.available, () => act('toggle_edition', { ident: e.ident }), e.available));
    opts.append(o);
  }
  const icon = (state.app_icons || []).find((c) => c.id === state.app_icon);
  if (icon) {
    opts.append(el('div', { class: 'option on' },
      el('div', {}, document.createTextNode('App icon: ' + icon.name), el('small', { text: 'The logo your app shows (SETTINGS › General)' }))));
  }
  if (state.compilers === 'missing') {
    opts.append(el('div', { class: 'option' + (state.accepted ? ' on' : '') },
      el('div', {}, document.createTextNode('Fetch the compilers'), el('small', { text: state.licences.map((t) => `${t.name} (${t.mb} MB)`).join(', ') + ': I accept their licences (SETTINGS › Compilers)' })),
      box(state.accepted, () => act('accept', { value: !state.accepted }))));
  }
  const problems = state.ready_problems;
  $('build-problem').textContent = problems[0] || '';
  $('build-go').disabled = !!problems.length;
}

// ------------------------------------------------------------------------------------------------------- loop
async function poll() {
  try {
    state = await api('/api/state');
    if (tab === 'progress') await pollLog();
    render();
  } catch (e) { /* the server ended: the window stays as it was */ }
}

async function pollLog() {
  if (window.DEMO_STATE) return;
  const r = await api('/api/log?since=' + logSince);
  const pre = $('log');
  if (r.reset) { pre.replaceChildren(); }
  const atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 4;
  for (const t of r.lines) {
    const cls = /REFUSED|FAILED|^ERROR/.test(t) ? 'err' : (/^Done\.|: verified\./.test(t) ? 'good' : '');
    pre.append(el('span', { class: cls, text: t + '\n' }));
  }
  logSince = r.next;
  while (pre.childNodes.length > 4000) pre.firstChild.remove();
  if (atEnd) pre.scrollTop = pre.scrollHeight;
}

document.querySelectorAll('#nav a').forEach((a) => a.addEventListener('click', () => show(a.dataset.tab)));
document.querySelectorAll('#side-links .link').forEach((a) => a.addEventListener('click', () => act('open_link', { key: a.dataset.link })));
document.querySelectorAll('#chrome a').forEach((a) => a.addEventListener('click', () => act('window', { what: a.dataset.chrome })));
$('primary').addEventListener('click', () => {
  const p = state.primary;
  if (!p.enabled) return;
  if (p.action === 'start') { dialog = 'build'; render(); return; }      // BUILD opens the build dialog first
  act(p.action).then((ok) => { if (ok && p.action === 'resume') show('progress'); });
});
$('build-cancel').addEventListener('click', () => { dialog = null; render(); });
$('build-browse').addEventListener('click', () => act('choose_out'));
$('build-go').addEventListener('click', () => act('start').then((ok) => { if (ok) { dialog = null; show('progress'); } }));
$('error-ok').addEventListener('click', () => { error = null; render(); });
$('error-x').addEventListener('click', () => { error = null; render(); });
$('side-more').addEventListener('click', () => $('side').scrollBy(0, 100));
window.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { if (error) error = null; else if (dialog) dialog = null; else detail = null; render(); }
  if (e.key === 'Enter' && tab === 'build' && !dialog && !error && state && state.primary.enabled) $('primary').click();
});
window.addEventListener('beforeunload', (e) => {      // closing the window stops a running build (it resumes later)
  if (state && state.running) { e.preventDefault(); e.returnValue = ''; }
});
if (window.DEMO_TAB) tab = window.DEMO_TAB;
if (state) render();
if (!window.DEMO_STATE) { poll(); setInterval(poll, 700); }
