// Tool definitions and executors for the Discover agent. Only present_list is
// strict (the harness parses it); the lookups return is_error on bad input,
// which the model corrects on the next turn. Five strict tools tripped the
// API's combined strict-schema limit in the scraper (scraper/harness.py).

import { DAYS, findShows, findVenues, getDetails } from './corpus.js';
import { planRoute } from './route.js';

const WEEKDAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];

// Server tools first, custom tools by name: a fixed order keeps the prefix cacheable.
export const SERVER_TOOLS = [
  { type: 'web_search_20260209', name: 'web_search', max_uses: 3 },
  { type: 'web_fetch_20260209', name: 'web_fetch', max_uses: 2, max_content_tokens: 8000 },
];

export function customTools(c) {
  return [
    {
      name: 'find_shows',
      description: "Search and filter the city's shows. Call this for any question that names a topic, medium, mood, artist, neighborhood, venue type, gallery tier, or a time window (this weekend, closing soon, opening receptions, open on Sunday). Omit query for pure filters. Returns matches sorted by relevance or by the gallery's city-wide rank with a grounding snippet, and reports strict_count and any relaxation applied (neighborhood, dates, concepts) when the strict filter found nothing. Prefer one call with all constraints over several narrow calls. Dates are YYYY-MM-DD in the city's own calendar.",
      input_schema: {
        type: 'object', additionalProperties: false,
        properties: {
          query: { type: 'string', description: 'Free-text topic: medium, theme, movement, mood, or an artist to be "similar to". Omit for pure filters.' },
          neighborhoods: { type: 'array', items: { type: 'string', enum: c.neighborhoods }, description: 'Exact neighborhood names from the list.' },
          venue_kind: { type: 'string', enum: ['gallery', 'museum', 'any'], description: 'gallery = commercial galleries only; museum = museums only; any (default) = also nonprofits, university galleries, project spaces and other venues.' },
          gallery_tier: { type: 'string', enum: ['top', 'notable', 'any'], description: 'top = the 20 best-ranked galleries; notable = the 50 best.' },
          status: { type: 'string', enum: ['on_view', 'upcoming', 'any'], description: 'Default on_view. any = on view or upcoming.' },
          closing_by: { type: 'string', description: 'Only shows whose last day is on or before this date (YYYY-MM-DD).' },
          opening_from: { type: 'string', description: 'Only shows whose first day is on or after this date.' },
          reception_from: { type: 'string', description: 'Only shows with a reception/event on or after this date.' },
          reception_to: { type: 'string', description: 'Only shows with a reception/event on or before this date.' },
          open_on: { type: 'string', enum: WEEKDAYS, description: 'Only venues open on this weekday (unknown hours pass).' },
          venue: { type: 'string', description: 'Venue id or name.' },
          artist: { type: 'string', description: 'Artist name as written on the show.' },
          limit: { type: 'integer', description: 'Default 12, max 40.' },
        },
      },
    },
    {
      name: 'find_venues',
      description: 'List venues (commercial galleries, museums, nonprofits, university galleries, project spaces) by neighborhood, kind, or tier, including venues with no show on view. Call this when the person asks about galleries or museums themselves rather than shows, or wants a walk in an area with few current shows. Returns each venue with its shows on view.',
      input_schema: {
        type: 'object', additionalProperties: false,
        properties: {
          neighborhoods: { type: 'array', items: { type: 'string', enum: c.neighborhoods } },
          kind: { type: 'string', enum: ['gallery', 'museum', 'any'], description: 'gallery = commercial galleries only; museum = museums only; any (default) = also nonprofits, university galleries, project spaces and other venues.' },
          tier: { type: 'string', enum: ['top', 'notable', 'any'], description: 'top = the 20 best-ranked galleries; notable = the 50 best.' },
          query: { type: 'string', description: 'Words to match in the venue name, blurb, program focus or roster.' },
          with_shows_only: { type: 'boolean' },
          limit: { type: 'integer', description: 'Default 20, max 40.' },
        },
      },
    },
    {
      name: 'get_details',
      description: "Full record for one show, venue, or artist by id or name: a show's complete description, dates note, source URLs and the venue's hours; a venue's hours by day, blurb, program focus, roster and shows on view; an artist's gallery relationships and show history. Call this before describing a show in detail, before recommending a venue for its program, when asked about hours or an address, or when asked about an artist. Returns candidates when a name is ambiguous.",
      input_schema: {
        type: 'object', additionalProperties: false, required: ['kind', 'id_or_name'],
        properties: {
          kind: { type: 'string', enum: ['show', 'venue', 'artist'] },
          id_or_name: { type: 'string' },
        },
      },
    },
    {
      name: 'plan_route',
      description: "Order shows into a walking route for a weekday, checked against each venue's hours that day: nearest-neighbour from the top-ranked stop, about 40 minutes per stop. Call this whenever the person asks to plan a day, an afternoon, a walk, a tour, or an itinerary; pass 4 to 8 candidate show ids from find_shows and the weekday. Returns ordered stops with arrival times and walking minutes, skipped stops with reasons, and total distance.",
      input_schema: {
        type: 'object', additionalProperties: false, required: ['show_ids', 'weekday'],
        properties: {
          show_ids: { type: 'array', items: { type: 'string' } },
          weekday: { type: 'string', enum: WEEKDAYS },
          start_time: { type: 'string', description: 'HH:MM, 24-hour. Default 12:00.' },
          max_stops: { type: 'integer', description: 'Default 6, max 8.' },
        },
      },
    },
    {
      name: 'present_list',
      strict: true,
      description: 'Present the final answer as a list the person can save. Call this exactly once, as the last thing you do, whenever your answer is a set of shows or a route. Entries are show ids from the catalog or tool results, in display order. Kind is "route" only after plan_route, in its order. Notes say why a show belongs, under twelve words; never times, dates, or hours. Include two or three short follow-up suggestions the person could tap, or an empty array when there is no natural next question.',
      input_schema: {
        type: 'object', additionalProperties: false, required: ['title', 'kind', 'entries', 'suggestions'],
        properties: {
          title: { type: 'string' },
          kind: { type: 'string', enum: ['list', 'route'] },
          entries: {
            type: 'array',
            items: { type: 'object', additionalProperties: false, required: ['show_id', 'note'],
              properties: { show_id: { type: 'string' }, note: { type: ['string', 'null'] } } },
          },
          suggestions: { type: 'array', items: { type: 'string' } },
        },
      },
    },
  ];
}

export function toolDefs(c) { return [...SERVER_TOOLS, ...customTools(c)]; }

// Runs a custom tool; returns { content, isError }.
export function runTool(c, name, input, today) {
  try {
    let out;
    if (name === 'find_shows') out = findShows(c, input, today);
    else if (name === 'find_venues') out = findVenues(c, input, today);
    else if (name === 'get_details') out = getDetails(c, input, today);
    else if (name === 'plan_route') out = planRoute(c, input, today);
    else return { content: `unknown tool ${name}`, isError: true };
    const isError = !!(out && out.error);
    return { content: JSON.stringify(out), isError };
  } catch (e) {
    return { content: `tool failed: ${e && e.message ? e.message : e}`, isError: true };
  }
}

// What the client shows while a tool runs, built from the arguments.
export function statusLabel(name, input) {
  const inp = input || {};
  if (name === 'find_shows') {
    const bits = [];
    if (inp.query) bits.push(inp.query);
    if (inp.neighborhoods && inp.neighborhoods.length) bits.push(inp.neighborhoods.join(', '));
    if (inp.closing_by) bits.push('closing soon');
    if (inp.reception_from || inp.reception_to) bits.push('receptions');
    if (inp.venue) bits.push(inp.venue);
    if (inp.artist) bits.push(inp.artist);
    return bits.length ? `Searching: ${bits.join(' · ')}` : 'Searching shows';
  }
  if (name === 'find_venues') return inp.neighborhoods && inp.neighborhoods.length ? `Looking at venues in ${inp.neighborhoods.join(', ')}` : 'Looking at venues';
  if (name === 'get_details') return `Looking up ${inp.id_or_name || inp.kind || 'details'}`;
  if (name === 'plan_route') { const d = inp.weekday ? { mon: 'Monday', tue: 'Tuesday', wed: 'Wednesday', thu: 'Thursday', fri: 'Friday', sat: 'Saturday', sun: 'Sunday' }[inp.weekday] : null; return d ? `Checking ${d} hours and distances` : 'Planning the route'; }
  if (name === 'web_search') return 'Searching the web';
  if (name === 'web_fetch') return 'Reading a web page';
  if (name === 'present_list') return 'Building the list';
  return 'Working';
}

export { DAYS };
