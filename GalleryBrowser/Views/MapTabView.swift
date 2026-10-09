import SwiftUI
import MapKit

/// One map pin: a venue, every visible show running there, and the gallery's rank.
private struct VenuePin: Identifiable {
    let venue: Venue
    var shows: [Show]
    let rank: Int?
    var id: String { venue.groupingKey }

    /// Dot style by rank band, matching the web map: the top 150 share one
    /// size, the top 25 blue and 26–150 a lighter blue; the rest small grey.
    var dotColor: Color {
        guard let rank, rank <= 150 else { return Color(white: 0.6).opacity(0.7) }
        return rank <= 25 ? Color.guideBlue.opacity(0.95) : Color(red: 0.70, green: 0.85, blue: 0.98).opacity(0.95)
    }
    var dotSize: CGFloat { (rank ?? .max) <= 150 ? 22 : 12 }
    var hasRing: Bool { (rank ?? .max) <= 150 }
}

/// The Map tab: full-bleed map of the selected city's venues with a shows
/// filter menu; tapping a pin presents the show detail as a sheet, or the
/// venue page when several shows share the venue.
struct MapTabView: View {
    @EnvironmentObject private var store: ContentStore
    @State private var filter: MapFilter = .all
    @State private var showCitySheet = false
    @State private var selectedShow: Show?
    @State private var selectedVenue: VenuePin?
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
                        if pin.shows.count > 1 {
                            selectedVenue = pin
                        } else {
                            selectedShow = pin.shows.first
                        }
                    } label: {
                        Circle()
                            .fill(pin.dotColor)
                            .frame(width: pin.dotSize, height: pin.dotSize)
                            .overlay(Circle().stroke(.white.opacity(pin.hasRing ? 0.6 : 0), lineWidth: 1))
                            .overlay(alignment: .topTrailing) {
                                if pin.shows.count > 1 {
                                    Text("\(pin.shows.count)")
                                        .font(.system(size: 10, weight: .bold))
                                        .foregroundStyle(.black)
                                        .padding(.horizontal, 4)
                                        .frame(minWidth: 17, minHeight: 17)
                                        .background(.white, in: Capsule())
                                        .shadow(color: .black.opacity(0.6), radius: 1.5, y: 1)
                                        .offset(x: 8, y: -7)
                                }
                            }
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .mapStyle(.standard)
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
        .sheet(item: $selectedShow) { show in
            NavigationStack {
                ShowDetailView(shows: [show], index: 0, presentedAsSheet: true)
            }
            .preferredColorScheme(.dark)
            #if os(macOS)
            .frame(width: 380, height: 720)
            #endif
        }
        .sheet(item: $selectedVenue) { pin in
            NavigationStack {
                VenueDetailView(venue: pin.venue, presentedAsSheet: true)
            }
            .preferredColorScheme(.dark)
            #if os(macOS)
            .frame(width: 380, height: 720)
            #endif
        }
        .onAppear { recenter() }
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
