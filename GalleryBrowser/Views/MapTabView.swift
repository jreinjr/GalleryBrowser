import SwiftUI
import MapKit

/// One map pin: a venue, every visible show running there, and the gallery's rank.
private struct VenuePin: Identifiable {
    let venue: Venue
    var shows: [Show]
    let rank: Int?
    var id: String { venue.groupingKey }

    /// Dot style by rank band, matching the web map: the top 100 share one
    /// size, the top 25 a deep blue and 26–100 a lighter blue; the rest small grey.
    var dotColor: Color {
        guard let rank, rank <= 100 else { return Color(white: 0.51).opacity(0.7) }
        return rank <= 25 ? Color(red: 0.08, green: 0.40, blue: 0.84) : Color(red: 0.63, green: 0.80, blue: 0.97).opacity(0.95)
    }
    var dotSize: CGFloat { (rank ?? .max) <= 100 ? 12.5 : 7 }
    var hasRing: Bool { (rank ?? .max) <= 100 }
}

/// The Map tab: full-bleed map of the selected city's venues with a shows
/// filter menu and the person's location; tapping a pin presents a compact
/// card: the shows' images, the gallery's address and hours, and Directions.
struct MapTabView: View {
    @EnvironmentObject private var store: ContentStore
    @State private var filter: MapFilter = .all
    @State private var showCitySheet = false
    @State private var selectedPin: VenuePin?
    @State private var locationManager = CLLocationManager()
    @State private var camera: MapCameraPosition = .automatic

    enum MapFilter: String, CaseIterable {
        case myShows = "My Shows"
        case all = "All Shows"
        case receptions = "Receptions"
    }

    private var visibleShows: [Show] {
        switch filter {
        case .all: return store.currentShows
        case .myShows: return store.savedShows
        case .receptions: return store.receptionShows
        }
    }

    /// Shows grouped by venue, one pin per venue, worst rank first so the
    /// best-ranked dots draw on top.
    private var venuePins: [VenuePin] {
        var pins: [VenuePin] = []
        var indexByKey: [String: Int] = [:]
        for show in visibleShows {
            let key = show.venue.groupingKey
            if let i = indexByKey[key] {
                pins[i].shows.append(show)
            } else {
                indexByKey[key] = pins.count
                pins.append(VenuePin(venue: show.venue, shows: [show], rank: store.galleryRank(of: show)))
            }
        }
        return pins.sorted { ($0.rank ?? .max) > ($1.rank ?? .max) }
    }

    var body: some View {
        Map(position: $camera) {
            ForEach(venuePins) { pin in
                Annotation(pin.venue.isMuseum ? "🏛 \(pin.venue.name)" : pin.venue.name,
                           coordinate: pin.venue.coordinate) {
                    Button {
                        selectedPin = pin
                    } label: {
                        Circle()
                            .fill(store.isToSee(pin.venue) ? Color(red: 0.90, green: 0.22, blue: 0.21) : pin.dotColor)
                            .frame(width: pin.dotSize, height: pin.dotSize)
                            .overlay(Circle().stroke(Color(red: 0.10, green: 0.29, blue: 0.53).opacity(pin.hasRing ? 0.75 : 0), lineWidth: 1))
                            .overlay(alignment: .topTrailing) {
                                if pin.shows.count > 1 {
                                    Text("\(pin.shows.count)")
                                        .font(.system(size: 8, weight: .bold))
                                        .foregroundStyle(.black)
                                        .padding(.horizontal, 3)
                                        .frame(minWidth: 13, minHeight: 13)
                                        .background(.white, in: Capsule())
                                        .shadow(color: .black.opacity(0.6), radius: 1.5, y: 1)
                                        .offset(x: 7, y: -6)
                                }
                            }
                    }
                    .buttonStyle(.plain)
                }
            }
            UserAnnotation()
        }
        .mapStyle(.standard(pointsOfInterest: .excludingAll))
        // A light street map reads better than the app's dark scheme.
        .environment(\.colorScheme, .light)
        .mapControls {
            MapUserLocationButton()
        }
        .ignoresSafeArea(edges: .top)
        .overlay(alignment: .topLeading) {
            Button("Cities") { showCitySheet = true }
                .tint(Color.guideBlue)
                .padding(.horizontal, 16)
                .padding(.vertical, 9)
                .background(.thinMaterial, in: Capsule())
                .padding(.leading, 16)
        }
        .overlay(alignment: .topTrailing) {
            Menu {
                ForEach(MapFilter.allCases, id: \.self) { option in
                    Button {
                        filter = option
                    } label: {
                        if filter == option {
                            Label(option.rawValue, systemImage: "checkmark")
                        } else {
                            Text(option.rawValue)
                        }
                    }
                }
            } label: {
                HStack(spacing: 5) {
                    Text(filter.rawValue)
                    Image(systemName: "chevron.down")
                        .font(.caption.weight(.semibold))
                }
                .tint(Color.guideBlue)
                .padding(.horizontal, 16)
                .padding(.vertical, 9)
                .background(.thinMaterial, in: Capsule())
            }
            .padding(.trailing, 16)
        }
        .sheet(isPresented: $showCitySheet) { CitySelectSheet() }
        .sheet(item: $selectedPin) { pin in
            NavigationStack {
                MapVenueCard(venue: pin.venue, shows: pin.shows)
            }
            .preferredColorScheme(.dark)
            #if os(macOS)
            .frame(width: 380, height: 720)
            #endif
        }
        .onAppear {
            recenter()
            locationManager.requestWhenInUseAuthorization()
        }
        .onChange(of: store.selectedCityKey) { recenter() }
    }

    private func recenter() {
        let city = store.selectedCity
        camera = .region(MKCoordinateRegion(
            center: city.center,
            span: MKCoordinateSpan(latitudeDelta: city.spanDegrees,
                                   longitudeDelta: city.spanDegrees)
        ))
    }
}

/// The map's tap card: the images of the shows on view, then only the
/// gallery's name, address and hours (tap through to the full gallery page)
/// and a Directions button. No show text.
private struct MapVenueCard: View {
    let venue: Venue
    let shows: [Show]

    @EnvironmentObject private var store: ContentStore
    @Environment(\.dismiss) private var dismiss

    private var images: [String] { shows.flatMap(\.images) }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if !images.isEmpty {
                    ImageCarousel(imagePaths: images, dotsAlignment: .bottom) { _ in }
                        .frame(height: 300)
                }
                VStack(alignment: .leading, spacing: 16) {
                    NavigationLink(value: venue) {
                        HStack {
                            VStack(alignment: .leading, spacing: 5) {
                                Text(venue.name)
                                    .font(.headline)
                                Text(venue.fullAddress)
                                    .font(.subheadline)
                                ForEach(venue.hours, id: \.self) { line in
                                    Text(line)
                                        .font(.subheadline)
                                        .foregroundStyle(.secondary)
                                }
                            }
                            Spacer()
                            Image(systemName: "chevron.right")
                                .foregroundStyle(.secondary)
                        }
                    }
                    .buttonStyle(.plain)

                    // "See" marks the gallery as one to visit; its map dot turns red.
                    let marked = store.isToSee(venue)
                    Button {
                        store.toggleToSee(venue)
                    } label: {
                        Label(marked ? "Marked to see" : "See", systemImage: marked ? "checkmark" : "eye")
                            .font(.headline)
                            .foregroundStyle(.white)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 13)
                            .background(marked ? Color(red: 0.90, green: 0.22, blue: 0.21) : Color.white.opacity(0.12),
                                        in: Capsule())
                    }
                    .buttonStyle(.plain)

                    BlueCapsuleButton(title: "Directions", systemImage: "figure.walk") {
                        let item = MKMapItem(placemark: MKPlacemark(coordinate: venue.coordinate))
                        item.name = venue.name
                        item.openInMaps(launchOptions: [
                            MKLaunchOptionsDirectionsModeKey: MKLaunchOptionsDirectionsModeWalking
                        ])
                    }
                }
                .padding(.horizontal, 18)
            }
            .padding(.bottom, 40)
        }
        .background(Color.black)
        .ignoresSafeArea(edges: .top)
        .navigationDestination(for: Venue.self) { venue in
            VenueDetailView(venue: venue)
        }
        .hideBackButton()
        .toolbar {
            ToolbarItem(placement: .guideLeading) {
                CircleIconButton(systemName: "xmark") { dismiss() }
            }
        }
    }
}
