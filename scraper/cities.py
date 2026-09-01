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
        "neighborhoods": [
            "Downtown/Eastside",
            "Hollywood",
            "Beverly Hills/West Hollywood",
            "Mid City/Westside",
        ],
        "guidance": (
            "LA's scene spans Downtown/Eastside (Hauser & Wirth DTLA, François Ghebaly, "
            "Night Gallery, Vielmetter, The Box, MOCA, The Broad, ICA LA), Hollywood "
            "(Regen Projects, Jeffrey Deitch, Various Small Fires, Nonaka-Hill, Tanya "
            "Bonakdar, Sea View, Hannah Hoffman), Beverly Hills/West Hollywood (Gagosian, "
            "Sprüth Magers, Matthew Marks, Karma LA, Morán Morán, Michael Kohn, LACMA), "
            "and Mid City/Westside (David Kordansky, David Zwirner, Blum, Anat Ebgi, "
            "Roberts Projects, Commonwealth & Council, Château Shatto, the Hammer Museum, "
            "Marian Goodman). Verify current dates on the venues' own sites."
        ),
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
