import Foundation
import CoreLocation

struct Show: Identifiable, Codable, Hashable {
    let city: String
    let slug: String
    let title: String
    let artist: String?
    // Either date can be missing in scraped content (the scraper holds such
    // shows back as pending, but published files must never break the app).
    let startDate: String?
    let endDate: String?
    let description: String
    let editorsPick: Bool
    let featured: Bool
    let reception: String?
    let images: [String]
    let sourceUrls: [String]
    let venue: Venue

    var id: String { "\(city)/\(slug)" }

    /// The name shown on feed cards and list rows: artist when present, else title.
    var displayName: String { artist ?? title }

    var start: Date? { startDate.flatMap { Self.isoDay.date(from: $0) } }
    var end: Date? { endDate.flatMap { Self.isoDay.date(from: $0) } }

    var dateLine: String {
        guard let end else { return "" }
        if let start, start > Date() {
            return "Opens \(Self.longDay.string(from: start))"
        }
        return "Through \(Self.longDay.string(from: end))"
    }

    static let isoDay: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.locale = Locale(identifier: "en_US_POSIX")
        return f
    }()

    static let longDay: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "EEEE, MMMM d, yyyy"
        return f
    }()
}

struct Venue: Codable, Hashable {
    let name: String
    let isMuseum: Bool
    let address: String
    let addressDetail: String?
    let neighborhood: String
    let hours: [String]
    let phone: String?
    let website: String?
    let latitude: Double
    let longitude: Double

    var coordinate: CLLocationCoordinate2D {
        CLLocationCoordinate2D(latitude: latitude, longitude: longitude)
    }

    var fullAddress: String {
        if let detail = addressDetail, !detail.isEmpty {
            return "\(address), \(detail)"
        }
        return address
    }

    /// List-row venue line, with the museum glyph the way See Saw draws it.
    var listLine: String { isMuseum ? "🏛 \(name)" : name }

    /// Every show embeds its own copy of the venue, so "same venue" is decided
    /// by normalized name plus coordinate rounded to ~1 m. Used to collapse
    /// several concurrent shows into one map pin / one venue page.
    var groupingKey: String {
        let normalized = name
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
            .split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
        let position = String(format: "%.5f,%.5f", latitude, longitude)
        return normalized.isEmpty ? position : "\(normalized)@\(position)"
    }
}

struct City: Identifiable, Hashable {
    let key: String
    let displayName: String
    let neighborhoods: [String]
    let center: CLLocationCoordinate2D
    let spanDegrees: Double
    let availabilityNote: String?

    var id: String { key }

    static func == (lhs: City, rhs: City) -> Bool { lhs.key == rhs.key }
    func hash(into hasher: inout Hasher) { hasher.combine(key) }

    static let all: [City] = [
        City(key: "seattle", displayName: "Seattle",
             neighborhoods: ["Pioneer Square", "Downtown", "Capitol Hill",
                             "Ballard/Fremont", "Georgetown", "University District"],
             center: CLLocationCoordinate2D(latitude: 47.6062, longitude: -122.3321),
             spanDegrees: 0.35, availabilityNote: nil),
        City(key: "new-york", displayName: "New York",
             neighborhoods: ["Chelsea", "Downtown", "Uptown", "Brooklyn/Queens", "Hamptons"],
             center: CLLocationCoordinate2D(latitude: 40.7359, longitude: -73.9911),
             spanDegrees: 0.5, availabilityNote: nil),
        City(key: "los-angeles", displayName: "Los Angeles",
             neighborhoods: ["Downtown/Arts District", "Chinatown/East LA",
                             "Los Feliz/NELA", "Hollywood",
                             "West Hollywood/Fairfax", "Beverly Hills",
                             "Mid-Wilshire/Koreatown", "Culver City/West Adams",
                             "Santa Monica/Venice", "Westside/Brentwood",
                             "South LA/Inglewood", "Pasadena/San Gabriel"],
             center: CLLocationCoordinate2D(latitude: 34.0622, longitude: -118.3080),
             spanDegrees: 0.55, availabilityNote: nil),
        City(key: "tokyo", displayName: "Tokyo",
             neighborhoods: ["Roppongi", "Ginza/Kyobashi", "Shibuya/Omotesando",
                             "Ebisu/Meguro", "Kiyosumi-Shirakawa", "Tennozu"],
             center: CLLocationCoordinate2D(latitude: 35.6700, longitude: 139.7500),
             spanDegrees: 0.30, availabilityNote: nil),
        City(key: "berlin", displayName: "Berlin",
             neighborhoods: ["Mitte", "Kreuzberg", "Charlottenburg", "Schöneberg"],
             center: CLLocationCoordinate2D(latitude: 52.5200, longitude: 13.4050),
             spanDegrees: 0.35, availabilityNote: nil),
        City(key: "london", displayName: "London",
             neighborhoods: ["Mayfair", "East End", "South London", "West End/Soho"],
             center: CLLocationCoordinate2D(latitude: 51.5074, longitude: -0.1278),
             spanDegrees: 0.35, availabilityNote: nil),
        City(key: "paris", displayName: "Paris",
             neighborhoods: ["Marais", "Saint-Germain", "Avenue Matignon/8th", "Belleville/Pantin"],
             center: CLLocationCoordinate2D(latitude: 48.8606, longitude: 2.3376),
             spanDegrees: 0.3, availabilityNote: nil),
        City(key: "venice", displayName: "Venice",
             neighborhoods: ["San Marco", "Dorsoduro", "Cannaregio", "Castello", "Giudecca"],
             center: CLLocationCoordinate2D(latitude: 45.4371, longitude: 12.3326),
             spanDegrees: 0.15, availabilityNote: "Available through Sunday, November 22"),
    ]

    static func named(_ key: String) -> City {
        all.first { $0.key == key } ?? all[0]
    }
}
