// ==UserScript==
// @name         HN Block User Comments
// @namespace    https://news.ycombinator.com
// @match        https://news.ycombinator.com/*
// @grant        GM_setValue
// @grant        GM_getValue
// @grant        GM_registerMenuCommand
// @version      1.0
// @description  Hide comments from specified Hacker News users
// ==/UserScript==

(function() {
    'use strict';

    const STORAGE_KEY = 'hn_blocked_users';

    function getBlockedUsers() {
        return GM_getValue(STORAGE_KEY, []);
    }

    function saveBlockedUsers(users) {
        GM_setValue(STORAGE_KEY, users);
    }

    function isBlocked(username) {
        return getBlockedUsers().includes(username);
    }

    function hideComment(commentEl) {
        commentEl.style.display = 'none';
    }

    function processComments() {
        const blocked = getBlockedUsers();
        if (!blocked.length) return;

        document.querySelectorAll('a.hnuser').forEach(userLink => {
            const username = userLink.textContent.trim();
            if (blocked.includes(username)) {
                const comment = userLink.closest('.comtr');
                if (comment) hideComment(comment);
            }
        });
    }

    function addBlockButton() {
        document.querySelectorAll('a.hnuser').forEach(userLink => {
            if (userLink.nextSibling && userLink.nextSibling.classList?.contains('block-btn')) return;

            const btn = document.createElement('a');
            btn.textContent = ' [block]';
            btn.className = 'block-btn';
            btn.style.marginLeft = '4px';
            btn.style.cursor = 'pointer';
            btn.style.fontSize = '10px';
            btn.style.color = '#828282';

            btn.onclick = (e) => {
                e.preventDefault();
                const username = userLink.textContent.trim();
                const users = getBlockedUsers();
                if (!users.includes(username)) {
                    users.push(username);
                    saveBlockedUsers(users);
                    alert(`Blocked ${username}. Refresh to hide their comments.`);
                }
            };

            userLink.parentNode.insertBefore(btn, userLink.nextSibling);
        });
    }

    function showBlockedList() {
        const users = getBlockedUsers();
        const list = users.length ? users.join(', ') : '(none)';
        const toRemove = prompt(`Currently blocked: ${list}\n\nEnter username to unblock (leave blank to cancel):`);
        if (!toRemove) return;
        const filtered = users.filter(u => u !== toRemove.trim());
        saveBlockedUsers(filtered);
        alert(`Unblocked ${toRemove.trim()}.`);
    }

    GM_registerMenuCommand('Manage blocked HN users', showBlockedList);

    // Run on load
    processComments();
    addBlockButton();

    // Re-run if new comments load via AJAX (rare on HN, but safe)
    const observer = new MutationObserver(() => {
        processComments();
        addBlockButton();
    });
    observer.observe(document.body, { childList: true, subtree: true });
})();
