"""Per-city configuration for the gallery scraper agent."""

CITIES = {
    "seattle": {
        "display_name": "Seattle",
        "center": {"latitude": 47.6062, "longitude": -122.3321},
        "span": {"latitudeDelta": 0.35, "longitudeDelta": 0.35},
        "neighborhoods": [
            "Pioneer Square",
            "Downtown",
            "Capitol Hill",
            "Ballard/Fremont",
            "Georgetown",
            "University District",
        ],
        "guidance": (
            "Seattle's gallery core is Pioneer Square (First Thursday art walks). Notable "
            "galleries include Greg Kucera Gallery, Traver Gallery, Foster/White Gallery, "
            "Stonington Gallery, Davidson Galleries, Koplin Del Rio, J. Rinehart Gallery, "
            "studio e, SOIL, Gallery 4Culture, Winston Wächter Fine Art, Harris Harvey "
            "Gallery, Linda Hodges Gallery, ABMEYER + WOOD, Patricia Rovzar Gallery, "
            "Photographic Center Northwest, and museums: Frye Art Museum, Henry Art "
            "Gallery, Seattle Art Museum, Bellevue Arts Museum. Verify which shows are "
            "actually on view right now on the venues' own websites."
        ),
    },
    "new-york": {
        "display_name": "New York",
        "center": {"latitude": 40.7359, "longitude": -73.9911},
        "span": {"latitudeDelta": 0.5, "longitudeDelta": 0.5},
        "neighborhoods": ["Chelsea", "Downtown", "Uptown", "Brooklyn/Queens", "Hamptons"],
        "guidance": (
            "Focus on major galleries: Gagosian, David Zwirner, Hauser & Wirth, Pace, "
            "Skarstedt, Gladstone, Lisson, or museums like the Whitney or New Museum."
        ),
    },
    "los-angeles": {
        "display_name": "Los Angeles",
        "center": {"latitude": 34.0622, "longitude": -118.3080},
        "span": {"latitudeDelta": 0.55, "longitudeDelta": 0.55},
        # Greater LA core: LA city + Santa Monica/Venice, Culver City, WeHo,
        # Beverly Hills, Pasadena/Glendale. Long Beach and Orange County are
        # out of scope. Zone-keyed guidance: shards see only their zones' notes.
        "neighborhoods": [
            "Downtown/Arts District",
            "Chinatown/East LA",
            "Los Feliz/NELA",
            "Hollywood",
            "West Hollywood/Fairfax",
            "Beverly Hills",
            "Mid-Wilshire/Koreatown",
            "Culver City/West Adams",
            "Santa Monica/Venice",
            "Westside/Brentwood",
            "South LA/Inglewood",
            "Pasadena/San Gabriel",
        ],
        "guidance": {
            "*": (
                "Greater LA core (excludes Long Beach/Orange County). Verify current "
                "dates on the venues' own sites."
            ),
            "Downtown/Arts District": (
                "Grand Ave (The Broad, MOCA Grand Avenue), Little Tokyo (JANM, LA "
                "Artcore), the Arts District (Hauser & Wirth, ICA LA, The Box), the "
                "Fashion District (Château Shatto, Track 16), and the Santa Fe Ave/"
                "Washington Blvd corridor (Vielmetter, Night Gallery, François Ghebaly, "
                "Wilding Cran). Dense with project spaces beyond the anchors."
            ),
            "Chinatown/East LA": (
                "Chung King Road and Chinatown (Charlie James Gallery, NOON Projects, "
                "Bel Ami, Human Resources), Lincoln Heights, Boyle Heights (Corey "
                "Helford, Parrasch Heijnen, Self Help Graphics), El Sereno."
            ),
            "Los Feliz/NELA": (
                "Los Feliz, Silver Lake, Echo Park, Frogtown/Elysian Valley, Highland "
                "Park and Glassell Park (Gattopardo, La Loma Projects, Odd Ark LA), "
                "Eagle Rock, Glendale, plus the Autry Museum in Griffith Park."
            ),
            "Hollywood": (
                "Hollywood and the Highland/Santa Monica Blvd gallery row (Regen "
                "Projects, Jeffrey Deitch, Various Small Fires, Nonaka-Hill, Lisson, "
                "David Zwirner, Tanya Bonakdar, Sea View, Hannah Hoffman, Morán Morán, "
                "Michael Kohn, Diane Rosenstein, Shulamit Nazarian), the La Brea "
                "corridor (Fahey/Klein), Sunset Blvd (Musichead), East Hollywood/"
                "Western Ave (Reisig and Taylor)."
            ),
            "West Hollywood/Fairfax": (
                "West Hollywood, Melrose Ave (Louis Stern, Steve Turner), the Almont/"
                "Robertson cluster (M+B), Fairfax and Beverly Grove (Karma, Matthew "
                "Marks, Timothy Hawkinson Gallery, Nino Mier spaces)."
            ),
            "Beverly Hills": (
                "Gagosian Beverly Hills, Marc Selwyn Fine Art, UTA Artist Space, "
                "Almine Rech, Christie's/Sotheby's exhibition spaces, Gary Snyder."
            ),
            "Mid-Wilshire/Koreatown": (
                "Miracle Mile museum row (LACMA, Academy Museum, Craft Contemporary), "
                "Marciano Art Foundation, Hancock Park, Koreatown (Commonwealth & "
                "Council, Park View/Paul Soto), Westlake/MacArthur Park (induction "
                "gallery), and David Kordansky on La Brea south of Wilshire."
            ),
            "Culver City/West Adams": (
                "The La Cienega/Washington corridor (Blum, Anat Ebgi, Philip Martin, "
                "Luis De Jesus), Culver City proper (Arcana, Von Lintel), West Adams "
                "and Jefferson Park (Thinkspace Projects, Band of Vices, Chris Sharp "
                "Gallery)."
            ),
            "Santa Monica/Venice": (
                "Bergamot Station Arts Center (Peter Fetterman, Rosegallery, Craig "
                "Krull, William Turner and many more under one roof), bG Gallery, "
                "18th Street Arts Center, Venice (LA Louver), Mar Vista, Malibu."
            ),
            "Westside/Brentwood": (
                "Westwood (Hammer Museum, UCLA galleries), Brentwood (Getty Center), "
                "Skirball Cultural Center on Sepulveda, Century City, Sawtelle, "
                "Marian Goodman's LA space if active."
            ),
            "South LA/Inglewood": (
                "Exposition Park (California African American Museum, USC Fisher "
                "Museum), Leimert Park (Art + Practice, Band of Vices' roots), "
                "Crenshaw, Inglewood (Residency Art Gallery)."
            ),
            "Pasadena/San Gabriel": (
                "Pasadena (Norton Simon Museum, USC Pacific Asia Museum, Armory "
                "Center for the Arts, ArtCenter's galleries), the Huntington in San "
                "Marino, Vincent Price Art Museum in Monterey Park, Alhambra, "
                "Altadena."
            ),
        },
    },
    "tokyo": {
        "display_name": "Tokyo",
        "center": {"latitude": 35.6700, "longitude": 139.7500},
        "span": {"latitudeDelta": 0.30, "longitudeDelta": 0.30},
        "neighborhoods": [
            "Roppongi",
            "Ginza/Kyobashi",
            "Shibuya/Omotesando",
            "Ebisu/Meguro",
            "Kiyosumi-Shirakawa",
            "Tennozu",
        ],
        "guidance": (
            "Tokyo's contemporary core clusters in Roppongi (Mori Art Museum, National Art "
            "Center, the complex665 building with Taka Ishii, ShugoArts and Tomio Koyama, "
            "and Piramide with Perrotin, Ota Fine Arts and Wako Works), Ginza/Kyobashi "
            "(Artizon Museum, Shiseido Gallery, Gallery Koyanagi), Tennozu's TERRADA Art "
            "Complex (KOTARO NUKAGA and others), Kiyosumi-Shirakawa (Museum of Contemporary "
            "Art Tokyo, smaller galleries), and Shibuya/Omotesando (Nanzuka, Watari-um, "
            "Espace Louis Vuitton, Blum). Verify dates on the venues' own sites."
        ),
    },
    "berlin": {
        "display_name": "Berlin",
        "center": {"latitude": 52.5200, "longitude": 13.4050},
        "span": {"latitudeDelta": 0.35, "longitudeDelta": 0.35},
        "neighborhoods": ["Mitte", "Kreuzberg", "Charlottenburg", "Schöneberg"],
        "guidance": (
            "Focus on major galleries: Sprüth Magers, Esther Schipper, neugerriemschneider, "
            "Galerie Eigen+Art, König, Contemporary Fine Arts, or Hamburger Bahnhof."
        ),
    },
    "london": {
        "display_name": "London",
        "center": {"latitude": 51.5074, "longitude": -0.1278},
        "span": {"latitudeDelta": 0.35, "longitudeDelta": 0.35},
        "neighborhoods": ["Mayfair", "East End", "South London", "West End/Soho"],
        "guidance": (
            "Focus on major galleries: White Cube, Gagosian, Sadie Coles HQ, Victoria Miro, "
            "Lisson, Maureen Paley, Thomas Dane, or Serpentine / Whitechapel Gallery."
        ),
    },
    "paris": {
        "display_name": "Paris",
        "center": {"latitude": 48.8606, "longitude": 2.3376},
        "span": {"latitudeDelta": 0.3, "longitudeDelta": 0.3},
        "neighborhoods": ["Marais", "Saint-Germain", "Avenue Matignon/8th", "Belleville/Pantin"],
        "guidance": (
            "Focus on major galleries: Perrotin, Thaddaeus Ropac, Almine Rech, kamel mennour, "
            "Galerie Lelong, Templon, Marian Goodman, or Bourse de Commerce / Palais de Tokyo."
        ),
    },
    "venice": {
        "display_name": "Venice",
        "center": {"latitude": 45.4371, "longitude": 12.3326},
        "span": {"latitudeDelta": 0.12, "longitudeDelta": 0.18},
        "neighborhoods": ["San Marco", "Dorsoduro", "Cannaregio", "Castello", "Giudecca"],
        "guidance": (
            "Focus on current institutional and collateral shows: Palazzo Grassi, Punta della "
            "Dogana, Peggy Guggenheim Collection, Fondazione Prada, Palazzo Fortuny, or "
            "gallery outposts and Biennale-adjacent exhibitions currently on view."
        ),
    },
}
