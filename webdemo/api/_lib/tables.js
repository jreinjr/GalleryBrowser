// Query-expansion tables for the Discover corpus index. Data, not logic: the
// neighborhood aliases map what people type onto the payload's exact
// neighborhood names; the concept table maps a topic onto the words that
// carry it in show descriptions and gallery blurbs. A leading or trailing
// space in a term means a word boundary (' tv' must not read "activity").

export const NEIGHBORHOOD_ALIASES = {
  'los-angeles': [
    ['West Hollywood/Fairfax', ['west hollywood', 'weho', 'fairfax', 'melrose']],
    ['Hollywood', ['hollywood']],
    ['Downtown/Arts District', ['arts district', 'downtown', 'dtla', 'little tokyo', 'bunker hill']],
    ['Chinatown/East LA', ['chinatown', 'east la', 'boyle heights', 'chung king']],
    ['Los Feliz/NELA', ['los feliz', 'silver lake', 'silverlake', 'highland park', 'eagle rock', 'nela', 'glassell park', 'frogtown']],
    ['Beverly Hills', ['beverly hills']],
    ['Mid-Wilshire/Koreatown', ['koreatown', 'mid-wilshire', 'mid wilshire', 'miracle mile', 'wilshire', 'hancock park']],
    ['Culver City/West Adams', ['culver city', 'west adams']],
    ['Santa Monica/Venice', ['santa monica', 'venice']],
    ['Westside/Brentwood', ['brentwood', 'westwood', 'westside', 'west la']],
    ['South LA/Inglewood', ['inglewood', 'south la', 'leimert park', 'south central']],
    ['Pasadena/San Gabriel', ['pasadena', 'san gabriel', 'altadena']],
  ],
};

export const CONCEPTS = {
  queer: ['queer', 'lgbtq', 'transgender', ' trans ', 'nonbinary', 'non-binary', ' gay ', 'lesbian', ' drag ', 'gender identity', 'gender nonconforming', 'genderqueer', 'gay liberation'],
  video: ['video', 'media art', 'new media', 'television', ' tv', 'screens', 'screen-based', 'projection', 'broadcast', 'moving image', 'monitor', 'film'],
  interactive: ['interactive', 'electronic', 'sound art', 'sound installation', 'sound work', 'soundscape', 'audio', 'kinetic', 'generative', 'sensor ', 'sensors', 'software', 'neon', 'robot', 'machine learning', 'algorithm'],
  photography: ['photograph', 'photo', 'camera', 'darkroom', 'gelatin'],
  ceramics: ['ceramic', 'clay', 'porcelain', 'stoneware', 'glaze', 'pottery'],
  painting: ['painting', 'painter', 'canvas', 'oil on'],
  sculpture: ['sculpture', 'sculptural', 'bronze', 'carved', 'cast '],
  textile: ['textile', 'weav', 'quilt', 'fabric', 'embroider', 'tapestry', 'fiber'],
  drawing: ['drawing', 'drawings', 'works on paper', 'graphite', 'ink on'],
  printmaking: ['print', 'lithograph', 'etching', 'screenprint', 'woodcut'],
  abstract: ['abstract', 'abstraction', 'geometric'],
  figurative: ['figurative', 'figure', 'portrait'],
  latinx: ['latinx', 'latino', 'latina', 'chicano', 'chicana', 'mexican'],
  black: ['black artist', 'black american', 'african american', 'black californian', 'diaspora'],
  asian: ['asian american', 'japanese', 'korean', 'chinese', 'filipino', 'vietnamese'],
  japanese: ['japanese', 'japan', 'tokyo'],
  korean: ['korean', 'korea'],
  performance: ['performance', 'performative'],
  installation: ['installation', 'immersive'],
  landscape: ['landscape', 'nature', 'desert', 'ocean'],
  fluxus: ['fluxus', 'happening', 'conceptual'],
  political: ['political', 'protest', 'activis', 'liberation'],
  feminist: ['feminist', 'feminism', 'women artists'],
  design: ['design', 'furniture', 'architecture', 'architectural'],
  outsider: ['self-taught', 'outsider', 'folk art', 'visionary'],
};

// Query words that switch a concept on (the concept's own terms always do).
export const TRIGGERS = [
  [/\bqueer|lgbtq|trans(gender)?\b|nonbinary|gay\b|lesbian/i, 'queer'],
  [/\bvideo|media art|new media|television|\btv\b|moving image|film\b/i, 'video'],
  [/interactive|electronic|kinetic|generative|sound art|digital/i, 'interactive'],
  [/photograph|photo\b|photos\b|photographers?/i, 'photography'],
  [/ceramic|clay|pottery|porcelain/i, 'ceramics'],
  [/painting|paintings|painters?/i, 'painting'],
  [/sculptur/i, 'sculpture'],
  [/textile|weaving|quilt|fiber/i, 'textile'],
  [/drawing|works on paper/i, 'drawing'],
  [/\bprints?\b|printmaking|lithograph|etching/i, 'printmaking'],
  [/abstract/i, 'abstract'],
  [/figurative|portrait/i, 'figurative'],
  [/latinx|latino|latina|chican/i, 'latinx'],
  [/black artists?|african american/i, 'black'],
  [/asian american/i, 'asian'],
  [/japanese|japan\b/i, 'japanese'],
  [/korean|korea\b/i, 'korean'],
  [/performance/i, 'performance'],
  [/installation|immersive/i, 'installation'],
  [/landscape|nature/i, 'landscape'],
  [/fluxus|conceptual/i, 'fluxus'],
  [/political|protest|activis/i, 'political'],
  [/feminis/i, 'feminist'],
  [/\bdesign|furniture|architect/i, 'design'],
  [/self-taught|outsider|folk art/i, 'outsider'],
];

// "similar to <artist>" for artists who are not in the data: a bundle of
// concepts plus the reading the model can state.
export const ARTIST_PROFILES = {
  'nam june paik': { concepts: ['video', 'fluxus'], reading: 'video, television and electronic media work' },
  'james turrell': { concepts: ['interactive', 'installation'], reading: 'light and immersive installation' },
  'yayoi kusama': { concepts: ['installation', 'painting'], reading: 'immersive installation and pattern painting' },
  'cindy sherman': { concepts: ['photography', 'figurative'], reading: 'staged photography and portraiture' },
  'bruce nauman': { concepts: ['video', 'performance', 'fluxus'], reading: 'video, performance and conceptual work' },
  'ed ruscha': { concepts: ['painting', 'photography'], reading: 'text painting and deadpan photography' },
  'mark rothko': { concepts: ['abstract', 'painting'], reading: 'abstract painting' },
  'agnes martin': { concepts: ['abstract', 'drawing'], reading: 'quiet geometric abstraction' },
  'kerry james marshall': { concepts: ['figurative', 'painting', 'black'], reading: 'figurative painting of Black life' },
};

export const STOPWORDS = new Set(('a an and are as at be by for from i in is it of on or the to what which who with show shows me some any ' +
  'current currently now there that this these those find give list like liked really last week want would could please about ' +
  'art artist artists gallery galleries exhibition exhibitions museum museums also else other similar new near around ' +
  'showing neighborhood neighbourhood area nearby close next best good great recommend recommendations suggestions something anything things see visit going right ' +
  'la los angeles city town').split(' '));
