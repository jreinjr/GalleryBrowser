import SwiftUI
import MapKit

/// The Map tab: full-bleed map of the selected city's venues with a shows
/// filter menu; tapping a pin presents the show detail as a sheet.
struct MapTabView: View {
    @EnvironmentObject private var store: ContentStore
    @State private var filter: MapFilter = .all
    @State private var showCitySheet = false
    @State private var selectedShow: Show?
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

    var body: some View {
        Map(position: $camera) {
            ForEach(visibleShows) { show in
                Annotation(show.venue.isMuseum ? "🏛 \(show.venue.name)" : show.venue.name,
                           coordinate: show.venue.coordinate) {
                    Button {
                        selectedShow = show
                    } label: {
                        Circle()
                            .fill(Color.guideBlue.opacity(0.85))
                            .frame(width: 22, height: 22)
                            .overlay(Circle().stroke(.white.opacity(0.6), lineWidth: 1))
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
