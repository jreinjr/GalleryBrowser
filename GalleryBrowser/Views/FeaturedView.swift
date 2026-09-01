import SwiftUI

/// The Featured tab: large-title city feed of image-carousel show cards.
struct FeaturedView: View {
    @EnvironmentObject private var store: ContentStore
    @State private var showCitySheet = false

    private var featuredShows: [Show] {
        store.currentShows.filter { $0.featured }
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(spacing: 22) {
                    ForEach(Array(featuredShows.enumerated()), id: \.element.id) { index, show in
                        NavigationLink(value: IndexedShows(shows: featuredShows, index: index)) {
                            ShowCardView(show: show)
                        }
                        .buttonStyle(.plain)
                    }
                    if featuredShows.isEmpty {
                        emptyState
                    }
                }
                .padding(.horizontal, 16)
                .padding(.top, 6)
                .padding(.bottom, 80)
            }
            .background(Color.black)
            .navigationTitle(store.selectedCity.displayName)
            .toolbar {
                ToolbarItem(placement: .guideLeading) {
                    Button("Cities") { showCitySheet = true }
                        .tint(Color.guideBlue)
                }
            }
            .navigationDestination(for: IndexedShows.self) { selection in
                ShowDetailView(shows: selection.shows, index: selection.index)
            }
            .sheet(isPresented: $showCitySheet) {
                CitySelectSheet()
            }
        }
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            ProgressView().tint(Color.guideBlue)
            Text("Loading shows...")
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 160)
    }
}

/// Navigation payload: a show list plus the tapped position, so the detail
/// screen's up/down chevrons can step through neighbors.
struct IndexedShows: Hashable {
    let shows: [Show]
    let index: Int
}

/// One feed card: rounded image carousel with dots top-left and a blurred
/// footer carrying the display name and "Venue • Address".
struct ShowCardView: View {
    let show: Show

    var body: some View {
        ImageCarousel(imagePaths: show.images, dotsAlignment: .topLeading)
            .frame(height: 310)
            .overlay(alignment: .bottom) { footer }
            .clipShape(RoundedRectangle(cornerRadius: 24, style: .continuous))
    }

    private var footer: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(show.displayName)
                .font(.title3.weight(.bold))
                .foregroundStyle(.black)
            Text("\(show.venue.isMuseum ? "🏛 " : "")\(show.venue.name) • \(show.venue.address)")
                .font(.subheadline)
                .foregroundStyle(.black.opacity(0.65))
        }
        .lineLimit(1)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(.ultraThinMaterial)
        .environment(\.colorScheme, .light)
    }
}
