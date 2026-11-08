// ==UserScript==
// @name         YouTube Smaller Thumbnails
// @namespace    https://www.youtube.com/
// @version      1.0
// @description  Limit YouTube thumbnail dimensions
// @author       You
// @match        https://www.youtube.com/*
// @grant        none
// ==/UserScript==

(function() {
    'use strict';
    const css = `
        #thumbnail,
        #details,
        ytd-rich-item-renderer,
        ytd-rich-item-renderer div {
            max-width: 300px !important;
            max-height: 250px !important;
        }
    `;
    const style = document.createElement('style');
    style.type = 'text/css';
    style.appendChild(document.createTextNode(css));
    document.head.appendChild(style);
})();
