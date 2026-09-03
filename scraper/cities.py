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
        # Deterministic seeding (seed_venues.py): a postal-code pattern that
        # marks a page as local, and per-zone `areas` (geocodable sub-districts
        # / street corners the Places sweep circles) + `anchors` (venue names
        # from the guidance above; zone_coverage checks the registry has them).
        "postal_re": r"\bCA\s+9[01]\d{3}\b",
        # Directory/listing sites the enumerator should fetch before recording
        # (build_enumerate_prompt). These used to be hardcoded in the prompt,
        # so every city was told to look for LA's directories.
        "directories": ["Gallery Platform LA (galleryplatform.la)",
                        "Contemporary Art Review LA's venue list"],
        "zones": {
            "Downtown/Arts District": {
                "areas": ["Grand Ave", "Little Tokyo", "Arts District", "Fashion District",
                          "Santa Fe Ave & Washington Blvd"],
                "anchors": ["The Broad", "MOCA Grand Avenue", "JANM", "LA Artcore",
                            "Hauser & Wirth", "ICA LA", "The Box", "Château Shatto",
                            "Track 16", "Vielmetter", "Night Gallery", "François Ghebaly",
                            "Wilding Cran"],
            },
            "Chinatown/East LA": {
                "areas": ["Chung King Road", "Chinatown", "Lincoln Heights", "Boyle Heights",
                          "El Sereno"],
                "anchors": ["Charlie James Gallery", "NOON Projects", "Bel Ami",
                            "Human Resources", "Corey Helford", "Parrasch Heijnen",
                            "Self Help Graphics"],
            },
            "Los Feliz/NELA": {
                "areas": ["Los Feliz", "Silver Lake", "Echo Park", "Frogtown", "Highland Park",
                          "Glassell Park", "Eagle Rock", "Glendale", "Griffith Park"],
                "anchors": ["Gattopardo", "La Loma Projects", "Odd Ark LA", "Autry Museum",
                            "Monte Vista Projects"],
            },
            "Hollywood": {
                "areas": ["Hollywood", "Highland Ave & Santa Monica Blvd",
                          "N La Brea Ave & Beverly Blvd", "Sunset Blvd & N Gardner St",
                          "East Hollywood"],
                "anchors": ["Regen Projects", "Jeffrey Deitch", "Various Small Fires",
                            "Nonaka-Hill", "Lisson", "David Zwirner", "Tanya Bonakdar",
                            "Sea View", "Hannah Hoffman", "Morán Morán", "Michael Kohn",
                            "Diane Rosenstein", "Shulamit Nazarian", "Fahey/Klein",
                            "Musichead", "Reisig and Taylor"],
            },
            "West Hollywood/Fairfax": {
                "areas": ["West Hollywood", "Melrose Ave", "N Almont Dr", "Fairfax",
                          "Beverly Grove"],
                "anchors": ["Louis Stern", "Steve Turner", "M+B", "Karma", "Matthew Marks",
                            "Timothy Hawkinson Gallery", "Nino Mier"],
            },
            "Beverly Hills": {
                "areas": ["Beverly Hills"],
                "anchors": ["Gagosian Beverly Hills", "Marc Selwyn Fine Art",
                            "UTA Artist Space", "Almine Rech", "Gary Snyder"],
            },
            "Mid-Wilshire/Koreatown": {
                "areas": ["Miracle Mile", "Hancock Park", "Koreatown", "Westlake",
                          "MacArthur Park", "S La Brea Ave & Wilshire Blvd"],
                "anchors": ["LACMA", "Academy Museum", "Craft Contemporary",
                            "Marciano Art Foundation", "Commonwealth & Council",
                            "Park View / Paul Soto", "induction gallery", "David Kordansky",
                            "1301 PE"],
            },
            "Culver City/West Adams": {
                "areas": ["La Cienega Blvd & Washington Blvd", "Culver City", "West Adams",
                          "Jefferson Park"],
                "anchors": ["Blum", "Anat Ebgi", "Philip Martin", "Luis De Jesus", "Arcana",
                            "Von Lintel", "Thinkspace Projects", "Band of Vices",
                            "Chris Sharp Gallery"],
            },
            "Santa Monica/Venice": {
                "areas": ["Bergamot Station", "Santa Monica", "Venice", "Mar Vista", "Malibu"],
                "anchors": ["Peter Fetterman", "Rosegallery", "Craig Krull", "William Turner",
                            "bG Gallery", "18th Street Arts Center", "L.A. Louver"],
            },
            "Westside/Brentwood": {
                "areas": ["Westwood", "Brentwood", "Sepulveda Pass", "Century City", "Sawtelle"],
                "anchors": ["Hammer Museum", "Getty Center", "Skirball Cultural Center",
                            "Marian Goodman"],
            },
            "South LA/Inglewood": {
                "areas": ["Exposition Park", "Leimert Park", "Crenshaw", "Inglewood"],
                "anchors": ["California African American Museum", "USC Fisher Museum",
                            "Art + Practice", "Residency Art Gallery"],
            },
            "Pasadena/San Gabriel": {
                "areas": ["Pasadena", "San Marino", "Monterey Park", "Alhambra", "Altadena"],
                "anchors": ["Norton Simon Museum", "USC Pacific Asia Museum",
                            "Armory Center for the Arts", "ArtCenter", "The Huntington",
                            "Vincent Price Art Museum"],
            },
        },
    },
    "tokyo": {
        "display_name": "Tokyo",
        "center": {"latitude": 35.6700, "longitude": 139.7500},
        "span": {"latitudeDelta": 0.30, "longitudeDelta": 0.30},
        # Tokyo's 23 wards, contemporary-art core only (Yokohama/Kanagawa and
        # the western suburbs past Kichijoji are out of scope). Zone-keyed
        # guidance: shards see only their zones' notes. The first six labels
        # are the original Tokyo zones and MUST NOT be renamed - existing show
        # and registry records carry them.
        "neighborhoods": [
            "Roppongi",
            "Ginza/Kyobashi",
            "Nihonbashi/Bakurocho",
            "Shibuya/Omotesando",
            "Shinjuku/Kagurazaka",
            "Ebisu/Meguro",
            "Kiyosumi-Shirakawa",
            "Tennozu",
            "Ueno/Yanaka",
            "Setagaya/West Tokyo",
        ],
        "guidance": {
            "*": (
                "Tokyo's 23 wards; Yokohama and the far western suburbs are out of "
                "scope. Many venues live on an upper floor of a named building - record "
                "the building name in address_detail. Verify current dates on the "
                "venue's own site (the Japanese page is often more current than the "
                "English one)."
            ),
            "Roppongi": (
                "Roppongi and Azabu: Mori Art Museum and Roppongi Hills, The National "
                "Art Center Tokyo, Suntory Museum of Art and 21_21 DESIGN SIGHT in Tokyo "
                "Midtown, Fujifilm Square, the complex665 building (Taka Ishii Gallery, "
                "ShugoArts, Tomio Koyama Gallery) and the Piramide building (Perrotin "
                "Tokyo, Ota Fine Arts, Wako Works of Art, Zen Foto Gallery, Yutaka "
                "Kikutake Gallery) on Roppongi 6-chome, Kaikai Kiki Gallery in "
                "Motoazabu, and the Azabudai Hills complex (Azabudai Hills Gallery, "
                "teamLab Borderless)."
            ),
            "Ginza/Kyobashi": (
                "Ginza's 1-8 chome, Kyobashi, Yurakucho and Shintomicho: Artizon "
                "Museum, Shiseido Gallery, Gallery Koyanagi, Ginza Graphic Gallery "
                "(ggg), Ginza Maison Hermes Le Forum, POLA Museum Annex, Tokyo Gallery "
                "+ BTAP, Nichido Gallery, Sokyo Ginza, Ginza Six. Dense with small "
                "rental galleries (kashi-garo) on the upper floors - record the real "
                "exhibition spaces, skip pure frame shops and art dealers with no "
                "public exhibition programme."
            ),
            "Nihonbashi/Bakurocho": (
                "Nihonbashi, Bakurocho / Higashi-Nihonbashi, Kayabacho, Ningyocho and "
                "Kanda-Jimbocho: Mitsui Memorial Museum, PARCEL and the Bakurocho "
                "artist-run cluster, Musashino Art University Gallery Alpha M, "
                "Radi-um von Roentgenwerke, the Kayabacho gallery spaces."
            ),
            "Shibuya/Omotesando": (
                "Shibuya, Jingumae/Harajuku, Omotesando, Aoyama, Gaienmae and "
                "Sendagaya: Nanzuka Underground and 2G, WATARI-UM, Espace Louis Vuitton "
                "Tokyo, Blum, GYRE GALLERY, Spiral, PARCO MUSEUM TOKYO, Shibuya Hikarie "
                "8/, SAI, Design Festa Gallery, The Shoto Museum of Art."
            ),
            "Shinjuku/Kagurazaka": (
                "Shinjuku, Nishi-Shinjuku, Hatsudai, Yoyogi and Kagurazaka: Tokyo Opera "
                "City Art Gallery and NTT InterCommunication Center in Hatsudai, Sompo "
                "Museum of Art, the Kagurazaka spaces (root K Contemporary, eitoeiko), "
                "Shinjuku department-store galleries."
            ),
            "Ebisu/Meguro": (
                "Ebisu, Daikanyama, Nakameguro, Meguro, Hiroo and Shirokane: Tokyo "
                "Photographic Art Museum in Yebisu Garden Place, MEM and NADiff "
                "a/p/a/r/t (which stacks several galleries in one building), Meguro "
                "Museum of Art, MA2 Gallery, the Shirokane gallery buildings, "
                "Matsuoka Museum of Art."
            ),
            "Kiyosumi-Shirakawa": (
                "Kiyosumi-Shirakawa, Kiba, Monzen-Nakacho, Ryogoku and Toyosu east of "
                "the Sumida: Museum of Contemporary Art Tokyo (MOT), Kana Kawanishi "
                "Gallery, Hiromi Yoshii, Mujin-to Production, teamLab Planets in "
                "Toyosu, The Sumida Hokusai Museum."
            ),
            "Tennozu": (
                "Tennozu Isle and the Shinagawa/Osaki waterfront: TERRADA ART COMPLEX I "
                "and II stack most of the zone's galleries in two buildings (KOTARO "
                "NUKAGA, KOSAKU KANECHIKA, MAKI Gallery, ANOMALY, Yukiko Mizutani, "
                "Taro Nasu among them) - enumerate the buildings' own tenant lists. "
                "Also WHAT MUSEUM, PIGMENT TOKYO and Warehouse TERRADA."
            ),
            "Ueno/Yanaka": (
                "Ueno Park's museum cluster (Tokyo Metropolitan Art Museum, The "
                "National Museum of Western Art, Tokyo National Museum, Ueno Royal "
                "Museum, The University Art Museum of Tokyo University of the Arts), "
                "plus Yanaka and Nezu (SCAI THE BATHHOUSE, HAGISO) and Asakusa/Kuramae."
            ),
            "Setagaya/West Tokyo": (
                "Setagaya, Sangenjaya, Shimokitazawa, Nakano, Koenji and Kichijoji - "
                "thin on commercial galleries but real: Setagaya Art Museum, Museum of "
                "Contemporary Sculpture, Suginami and Nakano ward galleries, the "
                "Kichijoji art spaces. Chofu, Tachikawa and everything further west "
                "are out of scope."
            ),
        },
        # Deterministic seeding (seed_venues.py): Japanese postal codes for the
        # 23 wards run 100-0000 to 179-9999; `areas` are geocodable districts
        # the Places sweep circles, `anchors` are venues zone_coverage expects
        # the registry to know (from the guidance above - never from See Saw).
        "postal_re": r"\b1[0-7]\d-\d{4}\b",
        "directories": ["Tokyo Art Beat (tokyoartbeat.com)",
                        "CADAN (cadan.or.jp), the contemporary dealers' association",
                        "ART iT (art-it.asia)",
                        "the tenant lists of the gallery buildings themselves "
                        "(complex665, Piramide, TERRADA ART COMPLEX, NADiff a/p/a/r/t)"],
        # Tokyo district names do not nest inside our zone labels the way LA's
        # do ("Shinagawa" is in Tennozu, "Harajuku" in Shibuya/Omotesando), so
        # tools.normalize_zone reads this map before its substring fallback.
        # Ward names that straddle two zones (Minato, Chuo) are deliberately
        # absent - an ambiguous label should fail and be re-asked.
        "zone_aliases": {
            "Azabu": "Roppongi", "Nishi-Azabu": "Roppongi", "Motoazabu": "Roppongi",
            "Higashi-Azabu": "Roppongi", "Azabudai": "Roppongi",
            "Azabudai Hills": "Roppongi", "Tokyo Midtown": "Roppongi",
            "Toranomon": "Roppongi", "Akasaka": "Roppongi",
            "Yurakucho": "Ginza/Kyobashi", "Shintomicho": "Ginza/Kyobashi",
            "Tsukiji": "Ginza/Kyobashi",
            "Kayabacho": "Nihonbashi/Bakurocho", "Ningyocho": "Nihonbashi/Bakurocho",
            "Jimbocho": "Nihonbashi/Bakurocho", "Jinbocho": "Nihonbashi/Bakurocho",
            "Kanda": "Nihonbashi/Bakurocho", "Marunouchi": "Nihonbashi/Bakurocho",
            "Otemachi": "Nihonbashi/Bakurocho", "Chiyoda": "Nihonbashi/Bakurocho",
            "Harajuku": "Shibuya/Omotesando", "Jingumae": "Shibuya/Omotesando",
            "Aoyama": "Shibuya/Omotesando", "Minami-Aoyama": "Shibuya/Omotesando",
            "Kita-Aoyama": "Shibuya/Omotesando", "Gaienmae": "Shibuya/Omotesando",
            "Sendagaya": "Shibuya/Omotesando", "Shoto": "Shibuya/Omotesando",
            "Hatsudai": "Shinjuku/Kagurazaka", "Yoyogi": "Shinjuku/Kagurazaka",
            "Ichigaya": "Shinjuku/Kagurazaka", "Yotsuya": "Shinjuku/Kagurazaka",
            "Daikanyama": "Ebisu/Meguro", "Hiroo": "Ebisu/Meguro",
            "Shirokane": "Ebisu/Meguro", "Shirokanedai": "Ebisu/Meguro",
            "Kiba": "Kiyosumi-Shirakawa", "Monzen-Nakacho": "Kiyosumi-Shirakawa",
            "Ryogoku": "Kiyosumi-Shirakawa", "Toyosu": "Kiyosumi-Shirakawa",
            "Fukagawa": "Kiyosumi-Shirakawa", "Morishita": "Kiyosumi-Shirakawa",
            "Koto": "Kiyosumi-Shirakawa", "Sumida": "Kiyosumi-Shirakawa",
            "Shinagawa": "Tennozu", "Higashi-Shinagawa": "Tennozu",
            "Kita-Shinagawa": "Tennozu", "Osaki": "Tennozu", "Gotanda": "Tennozu",
            "Terrada": "Tennozu",
            "Asakusa": "Ueno/Yanaka", "Kuramae": "Ueno/Yanaka", "Nezu": "Ueno/Yanaka",
            "Sendagi": "Ueno/Yanaka", "Taito": "Ueno/Yanaka", "Bunkyo": "Ueno/Yanaka",
            "Okachimachi": "Ueno/Yanaka",
            "Sangenjaya": "Setagaya/West Tokyo", "Shimokitazawa": "Setagaya/West Tokyo",
            "Nakano": "Setagaya/West Tokyo", "Koenji": "Setagaya/West Tokyo",
            "Kichijoji": "Setagaya/West Tokyo", "Musashino": "Setagaya/West Tokyo",
            "Suginami": "Setagaya/West Tokyo",
            "Futako-Tamagawa": "Setagaya/West Tokyo",
        },
        "zones": {
            "Roppongi": {
                "areas": ["Roppongi", "Nishi-Azabu", "Motoazabu", "Azabudai Hills",
                          "Tokyo Midtown Roppongi", "Toranomon Hills"],
                "anchors": ["Mori Art Museum", "The National Art Center, Tokyo",
                            "Suntory Museum of Art", "21_21 DESIGN SIGHT",
                            "Taka Ishii Gallery", "ShugoArts", "Tomio Koyama Gallery",
                            "Perrotin", "Ota Fine Arts", "Wako Works of Art",
                            "Zen Foto Gallery", "Kaikai Kiki Gallery"],
            },
            "Ginza/Kyobashi": {
                "areas": ["Ginza", "Kyobashi Chuo-ku Tokyo", "Yurakucho",
                          "Shintomi Chuo-ku Tokyo", "Higashi-Ginza"],
                "anchors": ["Artizon Museum", "Shiseido Gallery", "Gallery Koyanagi",
                            "Ginza Graphic Gallery", "Ginza Maison Hermes Le Forum",
                            "POLA Museum Annex", "Tokyo Gallery + BTAP",
                            "Nichido Gallery", "Sokyo Ginza"],
            },
            "Nihonbashi/Bakurocho": {
                "areas": ["Nihonbashi", "Bakurocho Tokyo", "Higashi-Nihonbashi",
                          "Kayabacho", "Ningyocho", "Jimbocho"],
                "anchors": ["Mitsui Memorial Museum", "PARCEL",
                            "Radi-um von Roentgenwerke"],
            },
            "Shibuya/Omotesando": {
                "areas": ["Shibuya", "Jingumae Harajuku", "Omotesando", "Aoyama Tokyo",
                          "Gaienmae", "Sendagaya"],
                "anchors": ["NANZUKA", "WATARI-UM", "Espace Louis Vuitton Tokyo",
                            "Blum", "GYRE GALLERY", "Spiral", "PARCO MUSEUM TOKYO",
                            "The Shoto Museum of Art", "Design Festa Gallery"],
            },
            "Shinjuku/Kagurazaka": {
                "areas": ["Shinjuku", "Nishi-Shinjuku", "Hatsudai", "Yoyogi",
                          "Kagurazaka"],
                "anchors": ["Tokyo Opera City Art Gallery",
                            "NTT InterCommunication Center", "Sompo Museum of Art"],
            },
            "Ebisu/Meguro": {
                "areas": ["Ebisu Shibuya-ku", "Daikanyama", "Nakameguro", "Meguro",
                          "Hiroo", "Shirokane Minato-ku"],
                "anchors": ["Tokyo Photographic Art Museum", "MEM", "NADiff a/p/a/r/t",
                            "Meguro Museum of Art", "MA2 Gallery",
                            "Matsuoka Museum of Art"],
            },
            "Kiyosumi-Shirakawa": {
                "areas": ["Kiyosumi-Shirakawa", "Kiba Koto-ku", "Monzen-Nakacho",
                          "Ryogoku", "Toyosu"],
                "anchors": ["Museum of Contemporary Art Tokyo", "Kana Kawanishi Gallery",
                            "Hiromi Yoshii", "Mujin-to Production", "teamLab Planets",
                            "The Sumida Hokusai Museum"],
            },
            "Tennozu": {
                "areas": ["Tennozu Isle", "Higashi-Shinagawa", "Kita-Shinagawa",
                          "Osaki Shinagawa-ku"],
                "anchors": ["KOTARO NUKAGA", "KOSAKU KANECHIKA", "MAKI Gallery",
                            "ANOMALY", "Yukiko Mizutani", "Taro Nasu", "PIGMENT TOKYO",
                            "WHAT MUSEUM"],
            },
            "Ueno/Yanaka": {
                "areas": ["Ueno Park", "Yanaka Taito-ku", "Nezu Bunkyo-ku", "Asakusa",
                          "Kuramae"],
                "anchors": ["Tokyo Metropolitan Art Museum",
                            "The National Museum of Western Art", "Tokyo National Museum",
                            "The Ueno Royal Museum", "SCAI THE BATHHOUSE",
                            "The University Art Museum, Tokyo University of the Arts"],
            },
            "Setagaya/West Tokyo": {
                "areas": ["Setagaya", "Sangenjaya", "Shimokitazawa", "Nakano Tokyo",
                          "Koenji", "Kichijoji"],
                "anchors": ["Setagaya Art Museum", "Museum of Contemporary Sculpture"],
            },
        },
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
