// Inline SVG stand-ins for the SF Symbols the app uses. All stroke/fill via currentColor.
(function () {
  const S = 'xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"';
  const stroke = 'fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"';

  window.ICONS = {
    star: `<svg ${S} fill="currentColor"><path d="M12 2.6l2.9 5.88 6.5.94-4.7 4.58 1.1 6.47L12 17.42l-5.8 3.05 1.1-6.47L2.6 9.42l6.5-.94z"/></svg>`,
    list: `<svg ${S} fill="currentColor"><circle cx="4" cy="6" r="1.5"/><circle cx="4" cy="12" r="1.5"/><circle cx="4" cy="18" r="1.5"/><rect x="8" y="5" width="13" height="2" rx="1"/><rect x="8" y="11" width="13" height="2" rx="1"/><rect x="8" y="17" width="13" height="2" rx="1"/></svg>`,
    map: `<svg ${S} fill="currentColor"><path d="M9 3.5L3.6 5.3a1 1 0 00-.6 1v13.4a.8.8 0 001.1.8L9 18.7l6 1.8 5.4-1.8a1 1 0 00.6-1V4.3a.8.8 0 00-1.1-.8L15 5.3 9 3.5zM9 5.6l6 1.8v11l-6-1.8v-11z"/></svg>`,
    bookmark: `<svg ${S} fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M6.5 3.5h11a1 1 0 011 1V21l-6.5-4-6.5 4V4.5a1 1 0 011-1z"/></svg>`,
    bookmarkFill: `<svg ${S} fill="currentColor"><path d="M6.5 3.5h11a1 1 0 011 1V21l-6.5-4-6.5 4V4.5a1 1 0 011-1z"/></svg>`,
    search: `<svg ${S} ${stroke}><circle cx="10.5" cy="10.5" r="6.5"/><path d="M15.5 15.5L21 21"/></svg>`,
    chevronLeft: `<svg ${S} ${stroke}><path d="M14.5 4L7 12l7.5 8"/></svg>`,
    chevronRight: `<svg ${S} ${stroke}><path d="M9.5 4L17 12l-7.5 8"/></svg>`,
    chevronUp: `<svg ${S} ${stroke}><path d="M4 15l8-7.5L20 15"/></svg>`,
    chevronDown: `<svg ${S} ${stroke}><path d="M4 9l8 7.5L20 9"/></svg>`,
    xmark: `<svg ${S} ${stroke}><path d="M5.5 5.5l13 13M18.5 5.5l-13 13"/></svg>`,
    check: `<svg ${S} ${stroke}><path d="M4 12.5l5.5 5.5L20 6.5"/></svg>`,
    expand: `<svg ${S} ${stroke}><path d="M14 4h6v6M10 20H4v-6M20 4l-6.5 6.5M4 20l6.5-6.5"/></svg>`,
    walk: `<svg ${S} fill="currentColor"><circle cx="13.2" cy="4" r="2"/><path d="M12.5 7.2c.7 0 1.3.3 1.7.9l1.2 1.8c.5.7 1.2 1.2 2 1.4l1.6.5-.5 1.8-2-.6a5 5 0 01-2.3-1.4l-.6 2.8 1.8 2 1 5-1.9.4-.9-4.4-2.2-2.2-.9 3-3.2 3.4-1.4-1.3 2.9-3.1 1.4-6.2-1.2.7-1 2.9-1.8-.6 1.2-3.6 4-2.4c.6-.4 1-.6 1.6-.6z"/></svg>`,
    compass: `<svg ${S} fill="currentColor"><path d="M12 2a10 10 0 100 20 10 10 0 000-20zm0 18a8 8 0 110-16 8 8 0 010 16zm4.8-12.8l-6.4 2.4-2.4 6.4 6.4-2.4 2.4-6.4zM12 13.2a1.2 1.2 0 110-2.4 1.2 1.2 0 010 2.4z"/></svg>`,
    phone: `<svg ${S} fill="currentColor"><path d="M6.8 3.2c.5-.5 1.3-.4 1.7.1l2 2.6c.4.5.4 1.2-.1 1.6l-1.2 1.2c.5 1.2 1.2 2.3 2.2 3.3s2.1 1.7 3.3 2.2l1.2-1.2c.4-.5 1.1-.5 1.6-.1l2.6 2c.5.4.6 1.2.1 1.7l-1.5 1.5c-.7.7-1.7 1-2.6.7-2.6-.8-5.1-2.3-7.2-4.4S5.6 9.9 4.8 7.3c-.3-.9 0-1.9.7-2.6l1.3-1.5z"/></svg>`,
    pin: `<svg ${S} fill="currentColor" stroke="rgba(255,255,255,0.85)" stroke-width="0.8"><path d="M12 1.8a7.5 7.5 0 00-7.5 7.5c0 5.3 6.3 11.7 7 12.4a.7.7 0 001 0c.7-.7 7-7.1 7-12.4A7.5 7.5 0 0012 1.8z"/><circle cx="12" cy="9.3" r="2.8" fill="#fff" stroke="none"/></svg>`,
  };
})();
