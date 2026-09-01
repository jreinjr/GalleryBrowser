import SwiftUI
import CoreLocation

/// Museums screen: "On View" section of museum shows.
struct MuseumsView: View {
    @EnvironmentObject private var store: ContentStore
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        List {
            Section {
                let shows = store.museumShows
                ForEach(Array(shows.enumerated()), id: \.element.id) { index, show in
                    NavigationLink(value: IndexedShows(shows: shows, index: index)) {
                        ShowRow(show: show)
                    }
                    .listRowBackground(Color.black)
                }
            } header: {
                Text("On View")
                    .font(.headline)
                    .foregroundStyle(Color.guideBlue)
                    .textCase(nil)
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .background(Color.black)
        .navigationTitle("Museums")
        .inlineTitleBar()
        .hideBackButton()
        .toolbar {
            ToolbarItem(placement: .guideLeading) {
                CircleIconButton(systemName: "chevron.left") { dismiss() }
            }
        }
    }
}

/// My Shows: the user's saved list, with the bookmark empty state.
struct MyShowsView: View {
    @EnvironmentObject private var store: ContentStore
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        List {
            let shows = store.savedShows
            ForEach(Array(shows.enumerated()), id: \.element.id) { index, show in
                NavigationLink(value: IndexedShows(shows: shows, index: index)) {
                    ShowRow(show: show)
                }
                .listRowBackground(Color.black)
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .background(Color.black)
        .overlay {
            if store.savedShows.isEmpty {
                VStack(spacing: 14) {
                    Image(systemName: "bookmark")
                        .font(.system(size: 56))
                        .foregroundStyle(.tertiary)
                    Text("No Saved Shows Yet")
                        .font(.title3.weight(.bold))
                    Text("Check out the Featured tab or the Editor's Picks\nlist to find something great.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                }
            }
        }
        .navigationTitle("My Shows")
        .hideBackButton()
        .toolbar {
            ToolbarItem(placement: .guideLeading) {
                CircleIconButton(systemName: "chevron.left") { dismiss() }
            }
        }
    }
}

/// Nearby: shows ordered by distance from the user (falls back to city center).
struct NearbyView: View {
    @EnvironmentObject private var store: ContentStore
    @StateObject private var locator = Locator()

    var body: some View {
        let origin = locator.location
            ?? CLLocation(latitude: store.selectedCity.center.latitude,
                          longitude: store.selectedCity.center.longitude)
        ShowListView(title: "Nearby", shows: store.showsSortedByDistance(from: origin))
            .onAppear { locator.request() }
    }
}

final class Locator: NSObject, ObservableObject, CLLocationManagerDelegate {
    @Published var location: CLLocation?
    private let manager = CLLocationManager()

    override init() {
        super.init()
        manager.delegate = self
    }

    func request() {
        manager.requestWhenInUseAuthorization()
        manager.requestLocation()
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        location = locations.last
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {}
}
