// ==UserScript==
// @name         Imgur Redirector
// @match        *://imgur.com/*
// @run-at       document-start
// ==/UserScript==

(function() {
    const host = location.hostname;
    const path = location.pathname.substring(1);

    if (host === "imgur.com") {
        if (path.startsWith('gallery/') || path.startsWith('a/')) {
            // Redirect galleries and albums
            location.href = `https://rimgo.totaldarkness.net/${path}`;
        } else if (path && !path.includes("/") && !path.includes("-")) {
            // Shortlink simple image posts
            location.href = `https://rimgo.totaldarkness.net/${path}`;
        }
    }
})();
