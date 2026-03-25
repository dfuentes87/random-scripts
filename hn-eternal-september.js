// ==UserScript==
// @name         HN Hide Newer Accounts (with subtree hiding)
// @namespace    https://news.ycombinator.com/
// @version      1.0.0
// @description  Hide Hacker News comments (and their descendant subtrees) from accounts created after 2020-01-01.
// @match        https://news.ycombinator.com/item?id=*
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_registerMenuCommand
// @grant        GM_xmlhttpRequest
// @connect      hacker-news.firebaseio.com
// @run-at       document-idle
// ==/UserScript==

(() => {
  'use strict';

  const CONFIG = {
    cutoffIso: '2020-01-01T00:00:00Z',
    apiBase: 'https://hacker-news.firebaseio.com/v0/user/',
    requestTimeoutMs: 10000,
    concurrency: 6,
    cachePrefix: 'hn-user-created:',
    hiddenClass: 'tm-hn-hidden-new-account',
    processedAttr: 'data-tm-hn-processed',
    observerDebounceMs: 150,
    logPrefix: '[HN Hide Newer Accounts]'
  };

  const CUTOFF_UNIX = Math.floor(Date.parse(CONFIG.cutoffIso) / 1000);

  let enabled = true;
  let observer = null;
  let processTimer = null;
  let menuToggleId = null;
  let menuReprocessId = null;

  function log(...args) {
    console.log(CONFIG.logPrefix, ...args);
  }

  function warn(...args) {
    console.warn(CONFIG.logPrefix, ...args);
  }

  function injectStyles() {
    if (document.getElementById('tm-hn-hide-newer-accounts-style')) return;
    const style = document.createElement('style');
    style.id = 'tm-hn-hide-newer-accounts-style';
    style.textContent = `
      .${CONFIG.hiddenClass} {
        display: none !important;
      }
    `;
    document.head.appendChild(style);
  }

  function updateMenu() {
    if (typeof GM_registerMenuCommand !== 'function') return;

    try {
      if (typeof GM_unregisterMenuCommand === 'function') {
        if (menuToggleId != null) GM_unregisterMenuCommand(menuToggleId);
        if (menuReprocessId != null) GM_unregisterMenuCommand(menuReprocessId);
      }
    } catch (_) {
      // Ignore if unregister is unavailable in this environment.
    }

    menuToggleId = GM_registerMenuCommand(
      enabled ? 'Disable HN account-age filter' : 'Enable HN account-age filter',
      async () => {
        enabled = !enabled;
        await GM_setValue('hn-filter-enabled', enabled);
        updateMenu();
        if (enabled) {
          scheduleProcess();
        } else {
          unhideAll();
        }
      }
    );

    menuReprocessId = GM_registerMenuCommand('Re-scan current thread', () => {
      scheduleProcess(true);
    });
  }

  function gmGetValue(key, defaultValue) {
    try {
      const v = GM_getValue(key, defaultValue);
      if (v && typeof v.then === 'function') return v;
      return Promise.resolve(v);
    } catch (err) {
      return Promise.reject(err);
    }
  }

  function gmSetValue(key, value) {
    try {
      const v = GM_setValue(key, value);
      if (v && typeof v.then === 'function') return v;
      return Promise.resolve(v);
    } catch (err) {
      return Promise.reject(err);
    }
  }

  function xhrGetJson(url) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: 'GET',
        url,
        timeout: CONFIG.requestTimeoutMs,
        headers: {
          'Accept': 'application/json'
        },
        onload: (response) => {
          if (response.status >= 200 && response.status < 300) {
            try {
              resolve(JSON.parse(response.responseText));
            } catch (err) {
              reject(new Error(`Invalid JSON from ${url}: ${err.message}`));
            }
          } else {
            reject(new Error(`HTTP ${response.status} for ${url}`));
          }
        },
        onerror: () => reject(new Error(`Network error for ${url}`)),
        ontimeout: () => reject(new Error(`Timeout for ${url}`))
      });
    });
  }

  async function getCachedCreated(username) {
    return gmGetValue(`${CONFIG.cachePrefix}${username}`, null);
  }

  async function setCachedCreated(username, created) {
    return gmSetValue(`${CONFIG.cachePrefix}${username}`, created);
  }

  async function fetchUserCreated(username) {
    const cached = await getCachedCreated(username);
    if (typeof cached === 'number') return cached;
    if (cached === false) return null; // cached negative result

    const url = `${CONFIG.apiBase}${encodeURIComponent(username)}.json`;
    try {
      const data = await xhrGetJson(url);
      const created = (data && typeof data.created === 'number') ? data.created : null;
      await setCachedCreated(username, created ?? false);
      return created;
    } catch (err) {
      warn(`Failed user lookup for ${username}:`, err);
      return null;
    }
  }

  async function mapLimit(items, limit, asyncFn) {
    const results = new Array(items.length);
    let nextIndex = 0;

    async function worker() {
      while (true) {
        const i = nextIndex++;
        if (i >= items.length) return;
        results[i] = await asyncFn(items[i], i);
      }
    }

    const workerCount = Math.min(limit, items.length);
    const workers = [];
    for (let i = 0; i < workerCount; i++) workers.push(worker());
    await Promise.all(workers);
    return results;
  }

  function getCommentRows() {
    return Array.from(document.querySelectorAll('tr.comtr'));
  }

  function getUsernameFromRow(row) {
    const userLink = row.querySelector('a.hnuser, .comhead a[href^="user?id="]');
    if (!userLink) return null;
    const username = userLink.textContent.trim();
    return username || null;
  }

  function getIndentLevel(row) {
    const img = row.querySelector('td.ind img[width]');
    const width = img ? parseInt(img.getAttribute('width') || '0', 10) : 0;
    if (!Number.isFinite(width)) return 0;
    // HN indentation is historically width/40.
    return Math.floor(width / 40);
  }

  function markHidden(row) {
    row.classList.add(CONFIG.hiddenClass);
  }

  function unmarkHidden(row) {
    row.classList.remove(CONFIG.hiddenClass);
  }

  function unhideAll() {
    document.querySelectorAll(`.${CONFIG.hiddenClass}`).forEach((el) => {
      el.classList.remove(CONFIG.hiddenClass);
    });
  }

  function hideSubtreeFromIndex(rows, startIndex) {
    const baseLevel = getIndentLevel(rows[startIndex]);
    markHidden(rows[startIndex]);

    for (let i = startIndex + 1; i < rows.length; i++) {
      const level = getIndentLevel(rows[i]);
      if (level <= baseLevel) break;
      markHidden(rows[i]);
    }
  }

  function buildHidePlan(rows, shouldHideUsernames) {
    const indicesToHide = new Set();

    for (let i = 0; i < rows.length; i++) {
      const username = getUsernameFromRow(rows[i]);
      if (!username) continue;
      if (shouldHideUsernames.has(username)) {
        indicesToHide.add(i);
      }
    }

    return indicesToHide;
  }

  async function processPage(force = false) {
    if (!enabled) return;

    const rows = getCommentRows();
    if (!rows.length) return;

    const usernames = [];
    const seen = new Set();

    for (const row of rows) {
      if (!force && row.getAttribute(CONFIG.processedAttr) === '1') {
        // Still include username if needed elsewhere; no-op here.
      }
      const username = getUsernameFromRow(row);
      if (username && !seen.has(username)) {
        seen.add(username);
        usernames.push(username);
      }
    }

    const createdValues = await mapLimit(usernames, CONFIG.concurrency, async (username) => {
      const created = await fetchUserCreated(username);
      return [username, created];
    });

    const shouldHide = new Set();
    for (const [username, created] of createdValues) {
      if (typeof created === 'number' && created >= CUTOFF_UNIX) {
        shouldHide.add(username);
      }
    }

    unhideAll();

    const indicesToHide = buildHidePlan(rows, shouldHide);
    const hiddenByAncestor = new Set();

    for (const idx of indicesToHide) {
      if (hiddenByAncestor.has(idx)) continue;

      const baseLevel = getIndentLevel(rows[idx]);
      hideSubtreeFromIndex(rows, idx);

      for (let i = idx + 1; i < rows.length; i++) {
        const level = getIndentLevel(rows[i]);
        if (level <= baseLevel) break;
        hiddenByAncestor.add(i);
      }
    }

    for (const row of rows) {
      row.setAttribute(CONFIG.processedAttr, '1');
    }

    log(`Processed ${rows.length} comments, hid ${indicesToHide.size} matching roots.`);
  }

  function scheduleProcess(force = false) {
    if (processTimer) clearTimeout(processTimer);
    processTimer = setTimeout(() => {
      processPage(force).catch((err) => warn('Processing failed:', err));
    }, CONFIG.observerDebounceMs);
  }

  async function init() {
    injectStyles();
    enabled = await gmGetValue('hn-filter-enabled', true);
    updateMenu();

    scheduleProcess(true);

    observer = new MutationObserver(() => {
      scheduleProcess(false);
    });

    observer.observe(document.body, {
      childList: true,
      subtree: true
    });
  }

  init().catch((err) => warn('Initialization failed:', err));
})();
