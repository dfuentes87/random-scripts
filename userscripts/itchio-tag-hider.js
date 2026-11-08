// ==UserScript==
// @name         itch.io Tag Hider
// @namespace    local.itch-tag-hider
// @version      1.2
// @description  Hide itch.io game cards whose displayed genre/tag matches a list (browse, search, tag, jam & dev pages).
// @match        *://itch.io/*
// @match        *://*.itch.io/*
// @run-at       document-idle
// @grant        GM_getValue
// @grant        GM_setValue
// ==/UserScript==

(function () {
  'use strict';

  // ---------- storage (GM_* with localStorage fallback) ----------
  const store = {
    get(k, d) {
      try { return GM_getValue(k, d); } catch (e) {
        try { return localStorage.getItem(k) ?? d; } catch (e2) { return d; }
      }
    },
    set(k, v) {
      try { GM_setValue(k, v); } catch (e) {
        try { localStorage.setItem(k, String(v)); } catch (e2) {}
      }
    },
  };

  // ---------- config ----------
  const cfg = {
    // One tag per line.
    tags: store.get('tags', 'gambling\nai generated\nnudity').split(/\r?\n/),
    substring: store.get('substring', true),   // "horror" matches "Psychological Horror"
    wholeCard: store.get('wholeCard', false),  // also scan full card text (risk: false positives)
    hide: true,                                // if false, dim instead (debug)
  };
  function save() {
    store.set('tags', cfg.tags.join('\n'));
    store.set('substring', cfg.substring);
    store.set('wholeCard', cfg.wholeCard);
  }

  const norm = s => (s || '').toLowerCase().replace(/\s+/g, ' ').trim();

  function tagSources(card) {
    const out = [];
    const g = card.querySelector('.game_genre');
    if (g) out.push(g.textContent);
    for (const a of card.querySelectorAll('a[href*="/tag-"]')) out.push(a.textContent);
    if (cfg.wholeCard) {
      const clone = card.cloneNode(true);
      clone.querySelectorAll(
        '.title_block, .game_title, .game_text, .game_author, .price_tag, .game_rating, .user_name').forEach(n => n.remove());
      out.push(clone.textContent);
    }
    return out.map(norm);
  }

  function shouldHide(card) {
    const sources = tagSources(card);
    if (!sources.some(s => s)) return false;
    return cfg.tags
      .map(norm)
      .filter(Boolean)
      .some(t => sources.some(s => s === t || (cfg.substring && s.includes(t))));
  }

  // ---------- filtering (stateless: safe to run any number of times) ----------
  let chip = null;

  function updateChip(n) {
    if (n === 0 && !chip) return;
    if (!chip) {
      chip = document.createElement('div');
      chip.style.cssText =
        'all:initial;position:fixed;left:12px;bottom:12px;z-index:2147483646;' +
        'font:12px/1.4 system-ui,sans-serif;background:#222a;color:#eee;' +
        'padding:5px 9px;border-radius:4px;pointer-events:none;opacity:.85';
      document.body.appendChild(chip);
    }
    chip.textContent = `itch tag hider: ${n} hidden`;
    chip.style.display = n ? '' : 'none';
  }

  // Re-evaluate every card from scratch and set styles directly. Idempotent.
  function applyAll() {
    let n = 0, total = 0;
    for (const c of document.querySelectorAll('.game_cell')) {
      const isGame = !!c.dataset.game_id;
      if (!isGame) continue;
      total++;
      const shouldHidden = shouldHide(c);
      if (shouldHidden) {
        n++;
        c.style.display = cfg.hide ? 'none' : '';
        c.style.opacity = cfg.hide ? '' : '0.05';
      }
    }
    console.info('[tag-hider] applied:', { matching: n, cards: total, tags: cfg.tags });
    updateChip(n);
  }

  function unhideAll() {
    for (const c of document.querySelectorAll('.game_cell')) {
      if (!c.dataset.game_id) continue;
      c.style.display = '';
      c.style.opacity = '';
    }
    console.info('[tag-hider] showing all');
    updateChip(0);
  }

  // Watch for lazily rendered cards (pagination/infinite scroll)
  let scheduled = false;
  const mo = new MutationObserver(records => {
    let relevant = false;
    for (const r of records) {
      for (const n of r.addedNodes) {
        if (n.nodeType !== 1) continue;
        if (n.matches?.('.game_cell') || n.querySelector?.('.game_cell')) { relevant = true; break; }
      }
      if (relevant) break;
    }
    if (!relevant || scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => { scheduled = false; applyAll(); });
  });
  mo.observe(document.documentElement, { childList: true, subtree: true });
  applyAll();

  // ---------- tiny settings UI ----------
  const btn = document.createElement('button');
  btn.textContent = '⚙ tag hider';
  btn.style.cssText =
    'all:initial;position:fixed;right:12px;bottom:12px;z-index:2147483647;' +
    'font:12px system-ui,sans-serif;padding:5px 9px;border-radius:4px;cursor:pointer;' +
    'background:#333;color:#eee;box-shadow:0 1px 4px rgba(0,0,0,.4)';
  document.body.appendChild(btn);

  let panel = null;
  function closePanel() { panel?.remove(); panel = null; }

  btn.addEventListener('click', () => {
    if (panel) { closePanel(); return; }
    panel = document.createElement('div');
    panel.style.cssText =
      'all:initial;position:fixed;right:12px;bottom:46px;z-index:2147483647;' +
      'width:280px;background:#1c1e22;color:#eee;font:13px system-ui,sans-serif;' +
      'padding:12px;border-radius:6px;box-shadow:0 2px 12px rgba(0,0,0,.5)';
    panel.innerHTML =
      '<div style="margin-bottom:6px;font-weight:600">Hidden tags (one per line)</div>' +
      '<textarea id="th-tags" spellcheck="false" style="width:100%;height:110px;box-sizing:border-box;' +
      'font:13px ui-monospace,monospace;background:#2a2d33;color:#eee;border:0;padding:6px"></textarea>' +
      '<label style="display:block;margin-top:8px"><input id="th-sub" type="checkbox"> substring match</label>' +
      '<label style="display:block"><input id="th-whole" type="checkbox"> scan full card text <small style="opacity:.6">(may false-positive on descriptions)</small></label>' +
      '<div style="display:flex;gap:8px;margin-top:10px">' +
      '<button id="th-save" style="flex:1;padding:5px;cursor:pointer;background:#7a5cff;color:#111;border:0;border-radius:3px">Save + apply</button>' +
      '<button id="th-clear" style="flex:1;padding:5px;cursor:pointer;background:#3a3d44;color:#eee;border:0;border-radius:3px">Show all</button>' +
      '</div>' +
      '<div style="margin-top:8px">' +
      '<button id="th-apply" style="width:100%;padding:5px;cursor:pointer;background:#3a3d44;color:#eee;border:0;border-radius:3px">Re-apply filter</button>' +
      '</div>';
    panel.querySelector('#th-tags').value = cfg.tags.join('\n');
    panel.querySelector('#th-sub').checked = cfg.substring;
    panel.querySelector('#th-whole').checked = cfg.wholeCard;
    panel.querySelector('#th-save').addEventListener('click', () => {
      cfg.tags = panel.querySelector('#th-tags').value.split(/\r?\n/);
      cfg.substring = panel.querySelector('#th-sub').checked;
      cfg.wholeCard = panel.querySelector('#th-whole').checked;
      save();
      closePanel();
      applyAll();
    });
    panel.querySelector('#th-clear').addEventListener('click', () => unhideAll());
    panel.querySelector('#th-apply').addEventListener('click', () => applyAll());
    document.body.appendChild(panel);
  });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closePanel(); });
})();
