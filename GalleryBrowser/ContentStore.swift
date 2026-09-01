import Foundation
import SwiftUI
import CoreLocation

/// Loads bundled show content (scraped JSON + images) and holds app state:
/// selected city and the user's saved ("My Shows") list.
@MainActor
final class ContentStore: ObservableObject {

    @Published var selectedCityKey: String {
        didSet { UserDefaults.standard.set(selectedCityKey, forKey: "selectedCityKey") }
    }

    @Published private(set) var savedShowIDs: Set<String> {
        didSet { UserDefaults.standard.set(Array(savedShowIDs), forKey: "savedShowIDs") }
    }

    private var cache: [String: [Show]] = [:]

    init() {
        selectedCityKey = UserDefaults.standard.string(forKey: "selectedCityKey") ?? "seattle"
        savedShowIDs = Set(UserDefaults.standard.stringArray(forKey: "savedShowIDs") ?? [])
    }

    var selectedCity: City { City.named(selectedCityKey) }

    // MARK: - Content loading

    private static let contentRoot: URL? = {
        // The macOS preview runner points this at the repo's content directory;
        // on iOS the folder ships inside the app bundle.
        if let override = ProcessInfo.processInfo.environment["GALLERY_CONTENT_DIR"] {
            return URL(fileURLWithPath: override)
        }
        return Bundle.main.resourceURL?.appendingPathComponent("content")
    }()

    func shows(for cityKey: String) -> [Show] {
        if let cached = cache[cityKey] { return cached }
        guard let url = Self.contentRoot?.appendingPathComponent("\(cityKey).json"),
              let data = try? Data(contentsOf: url) else {
            cache[cityKey] = []
            return []
        }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        struct Payload: Codable { let shows: [Show] }
        let shows = (try? decoder.decode(Payload.self, from: data))?.shows ?? []
        cache[cityKey] = shows
        return shows
    }

    var currentShows: [Show] { shows(for: selectedCityKey) }

    static func imageURL(_ relativePath: String) -> URL? {
        contentRoot?.appendingPathComponent(relativePath)
    }

    static func image(_ relativePath: String) -> PlatformImage? {
        guard let url = imageURL(relativePath) else { return nil }
        return PlatformImage(contentsOfFile: url.path)
    }

    // MARK: - My Shows

    func isSaved(_ show: Show) -> Bool { savedShowIDs.contains(show.id) }

    func toggleSaved(_ show: Show) {
        if savedShowIDs.contains(show.id) {
            savedShowIDs.remove(show.id)
        } else {
            savedShowIDs.insert(show.id)
        }
    }

    var savedShows: [Show] { currentShows.filter { savedShowIDs.contains($0.id) } }

    // MARK: - Smart lists

    func shows(in neighborhood: String) -> [Show] {
        currentShows.filter { $0.venue.neighborhood == neighborhood }
    }

    var museumShows: [Show] { currentShows.filter { $0.venue.isMuseum } }

    var openingThisWeek: [Show] {
        let now = Date()
        let week: TimeInterval = 7 * 24 * 3600
        return currentShows.filter {
            guard let start = $0.start else { return false }
            return abs(start.timeIntervalSince(now)) <= week
        }
    }

    var closingThisWeek: [Show] {
        let now = Date()
        let week: TimeInterval = 7 * 24 * 3600
        return currentShows.filter {
            guard let end = $0.end else { return false }
            let dt = end.timeIntervalSince(now)
            return dt >= 0 && dt <= week
        }
    }

    var editorsPicks: [Show] { currentShows.filter { $0.editorsPick } }

    var receptionShows: [Show] { currentShows.filter { $0.reception != nil } }

    func showsSortedByDistance(from location: CLLocation) -> [Show] {
        currentShows.sorted {
            let a = CLLocation(latitude: $0.venue.latitude, longitude: $0.venue.longitude)
            let b = CLLocation(latitude: $1.venue.latitude, longitude: $1.venue.longitude)
            return a.distance(from: location) < b.distance(from: location)
        }
    }

    func search(_ query: String) -> [Show] {
        let q = query.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else { return [] }
        return currentShows.filter {
            $0.title.localizedCaseInsensitiveContains(q)
                || ($0.artist?.localizedCaseInsensitiveContains(q) ?? false)
                || $0.venue.name.localizedCaseInsensitiveContains(q)
        }
    }
}
