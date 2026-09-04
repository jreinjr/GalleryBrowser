/* Discover corpus checks (no API call): the index and tools over the built
 * corpus in webdemo/api/_data. Run after build.py:
 *   node webdemo/tests/corpus.test.mjs
 */
import { getCorpus, findShows, findVenues, getDetails, catalog, weekendOf, addDays, hoursOn, nowIn } from '../api/_lib/corpus.js';
import { planRoute } from '../api/_lib/route.js';
import { validateList } from '../api/_lib/agent.js';
import { systemText, contextMessage } from '../api/_lib/prompt.js';

const TODAY = process.env.TODAY || '2026-09-03';
const results = [];
const check = (name, ok, detail) => { results.push({ name, ok }); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`); };

const c = getCorpus('los-angeles');
check('corpus loads', !!c && c.shows.length > 100, `${c.shows.length} shows, ${Object.keys(c.venues).length} venues, ${c.artists.length} artists`);

// catalog
const cat = catalog(c, TODAY);
const lines = cat.split('\n').filter(l => l.includes(' | ') && !l.startsWith('#'));
const tokensEst = Math.round(cat.length / 3.6);
check('catalog has one line per running/upcoming show', lines.length >= 150 && lines.length <= c.shows.length, `${lines.length} lines, ~${tokensEst} tokens`);
check('catalog lines carry an id, venue and dates', lines.every(l => /^[a-z0-9-]+ \| .+ \| .+ \| .+ \(.+\) \| \d{4}-\d{2}-\d{2}\.\./.test(l) || /\| \?\.\./.test(l)), lines.find(l => !/^[a-z0-9-]+ \| .+ \| .+ \| .+ \(.+\) \| /.test(l)));
const sys = systemText(c, TODAY);
check('system prompt under 40k chars', sys.length < 40000, `${sys.length} chars`);
check('system prompt has no clock time', !/\d{1,2}:\d{2} (AM|PM)/.test(sys));

// dates
check('weekend from a Thursday is Fri..Sun', JSON.stringify(weekendOf('2026-09-03')) === JSON.stringify({ from: '2026-09-04', to: '2026-09-06' }));
check('weekend on a Saturday starts today', weekendOf('2026-09-05').from === '2026-09-05' && weekendOf('2026-09-05').to === '2026-09-06');
check('addDays crosses the month', addDays('2026-09-28', 5) === '2026-10-03');
const ctx = contextMessage(c, { today: TODAY, filterSummary: 'Galleries · Featured · Active', savedLists: ['Chinatown walk'], savedShows: ['deitch-urs-fischer'] }, new Date('2026-09-03T21:15:00Z'));
check('context message names the day and weekend', /Thursday, September 3, 2026/.test(ctx) && /Sep 4 – Sep 6/.test(ctx), ctx.split('\n')[0]);
check('context clock is in LA time', /2:15 PM/.test(ctx), ctx.split('\n')[0]);
check('nowIn handles midnight hour', nowIn('America/Los_Angeles', new Date('2026-09-04T07:05:00Z')).time === '00:05');

// find_shows: filters
const closing = findShows(c, { closing_by: addDays(TODAY, 7) }, TODAY);
check('closing this week is a non-empty, dated, ascending set', closing.count > 0 && closing.hits.every(h => h.dates.split('..')[1] <= addDays(TODAY, 7)) && closing.hits.every((h, i, a) => !i || a[i - 1].dates.split('..')[1] <= h.dates.split('..')[1]), `${closing.count} closing by ${addDays(TODAY, 7)}`);
const wk = weekendOf(TODAY);
const rec = findShows(c, { reception_from: wk.from, reception_to: wk.to, status: 'any' }, TODAY);
check('receptions this weekend are inside the window', rec.count > 0 && rec.hits.every(h => h.reception >= wk.from && h.reception <= wk.to), `${rec.count} receptions; kinds ${[...new Set(rec.hits.map(h => h.reception_kind))].join(',')}`);
const museums = findShows(c, { venue_kind: 'museum' }, TODAY);
check('museum filter returns only museums', museums.count > 0 && museums.hits.every(h => h.flags.includes('museum')), `${museums.count} museum shows`);
const deitch = findShows(c, { venue: 'Jeffrey Deitch' }, TODAY);
check('venue by name resolves', deitch.count >= 1 && deitch.hits.every(h => h.venue_id === 'jeffrey-deitch'), `${deitch.count}`);
const ruscha = findShows(c, { artist: 'Ed Ruscha' }, TODAY);
check('artist filter finds The Cars of Los Angeles', ruscha.hits.some(h => h.id === 'bel-ami-cars-of-los-angeles'));
const alias = findShows(c, { neighborhoods: ['dtla'], limit: 40 }, TODAY);
check('neighborhood alias dtla resolves', alias.applied.neighborhoods[0] === 'Downtown/Arts District' && alias.count > 0, `${alias.count}`);
const badHood = findShows(c, { neighborhoods: ['Narnia'] }, TODAY);
check('unknown neighborhood is reported', badHood.errors && /unknown neighborhoods/.test(badHood.errors[0]));

// find_shows: search + relaxation
const ceramics = findShows(c, { query: 'ceramics', neighborhoods: ['Chinatown'] }, TODAY);
check('ceramics in Chinatown finds grounded hits', ceramics.count > 0 && ceramics.hits.every(h => h.why && h.why.snippet), `${ceramics.count} (strict ${ceramics.strict_count}, relaxed ${ceramics.relaxed})`);
const queer = findShows(c, { query: 'queer artists', neighborhoods: ['Hollywood'] }, TODAY);
check('queer artists in Hollywood: honest relaxation', queer.strict_count === 0 ? queer.relaxed === 'neighborhood' && queer.count > 0 : queer.hits.every(h => h.neighborhood === 'Hollywood'), `strict ${queer.strict_count}, relaxed ${queer.relaxed}, count ${queer.count}`);
const paik = findShows(c, { query: 'similar to Nam June Paik' }, TODAY);
check('artist profile reading applies', paik.applied.reading && /video/.test(paik.applied.reading) && paik.count > 3, `${paik.count}: ${paik.applied.reading}`);
const subs = findShows(c, { query: 'submarines' }, TODAY);
check('nonsense query returns nothing', subs.count === 0, `${subs.count}`);
const video = findShows(c, { query: 'video art' }, TODAY);
check('video art ranks a video show first', video.hits.length > 5 && /video/i.test(c.showsById.get(video.hits[0].id).description), video.hits.slice(0, 3).map(h => h.id).join(', '));

// venues, details
const chinatownVenues = findVenues(c, { neighborhoods: ['Chinatown/East LA'], kind: 'gallery' }, TODAY);
check('find_venues lists Chinatown galleries', chinatownVenues.count > 3 && chinatownVenues.venues.every(v => v.kind !== 'museum'), `${chinatownVenues.count}`);
const vd = getDetails(c, { kind: 'venue', id_or_name: 'Vielmetter' }, TODAY);
check('venue details resolve a partial name', vd.id === 'vielmetter-los-angeles' || /vielmetter/i.test(vd.name || ''), vd.id || vd.error);
check('venue details carry hours by day', vd.hours_by_day && Object.keys(vd.hours_by_day).length === 7, JSON.stringify(vd.hours_by_day));
const ad = getDetails(c, { kind: 'artist', id_or_name: 'Ed Ruscha' }, TODAY);
check('artist details list galleries', ad.galleries && ad.galleries.length > 0, ad.galleries && ad.galleries.map(g => g.venue).join(', '));
const sd = getDetails(c, { kind: 'show', id_or_name: 'los-angeles/deitch-urs-fischer' }, TODAY);
check('show details accept a prefixed id', sd.id === 'deitch-urs-fischer' && sd.description.length > 100);
const missing = getDetails(c, { kind: 'show', id_or_name: 'nope-nope' }, TODAY);
check('missing show reports an error', !!missing.error);

// route
const arts = findShows(c, { neighborhoods: ['Downtown/Arts District'], limit: 8 }, TODAY);
const rt = planRoute(c, { show_ids: arts.hits.map(h => h.id), weekday: 'sat' }, TODAY);
check('route orders stops with times', rt.stops.length >= 3 && rt.stops.every(s => /^\d{2}:\d{2}$/.test(s.arrive)) && rt.stops[0].walk_min === 0, `${rt.stops.length} stops, ${rt.total_km} km, ${rt.start_label}–${rt.end_label}, skipped ${rt.skipped.length}`);
check('route stops are open on Saturday', rt.stops.every(s => !s.hours_known || !/closed/.test(s.hours)));
check('route reports unknown ids', planRoute(c, { show_ids: ['nope'], weekday: 'sat' }, TODAY).unknown_ids.length === 1);

// hours
const v1301 = c.venues['1301-pe'];
check('1301PE is open Saturday 11–6 and closed Monday', v1301 && hoursOn(v1301, 'sat').opens === 660 && hoursOn(v1301, 'mon').open === false);

// present_list validation
const val = validateList(c, { title: 'T', kind: 'route', entries: [{ show_id: 'deitch-urs-fischer', note: 'fine' }, { show_id: 'los-angeles/broad-yoko-ono', note: 'Sat 10am–6pm' }, { show_id: 'nope', note: null }, { show_id: 'deitch-urs-fischer', note: 'dup' }], suggestions: ['a', 'b', 'c', 'd'] });
check('present_list validation drops unknown ids, dedups, strips timed notes, caps suggestions', val.entries.length === 2 && val.dropped[0] === 'nope' && val.entries[1].note === null && val.entries[0].id === 'los-angeles/deitch-urs-fischer' && val.suggestions.length === 3, JSON.stringify(val));

const failed = results.filter(r => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
