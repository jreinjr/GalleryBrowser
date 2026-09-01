import SwiftUI

/// The List tab root: banner slot, My Shows, All Current Shows + neighborhoods,
/// then the smart lists — mirroring the grouped-inset layout of the original.
struct ListRootView: View {
    @EnvironmentObject private var store: ContentStore
    @State private var showCitySheet = false
    @State private var showSearch = false

    var body: some View {
        NavigationStack {
            List {
                if !store.museumShows.isEmpty {
                    Section {
                        NavigationLink(value: ListTarget.museums) {
                            Text("🏛")
                                .font(.system(size: 34))
                                .frame(maxWidth: .infinity)
                        }
                        .listRowBackground(Color.white)
                    }
                }

                Section {
                    NavigationLink(value: ListTarget.myShows) {
                        Label {
                            Text("My Shows")
                        } icon: {
                            Image(systemName: "bookmark.fill")
                                .foregroundStyle(Color.guideBlue)
                        }
                    }
                }

                Section {
                    NavigationLink(value: ListTarget.all) {
                        Text("All Current Shows")
                    }
                    ForEach(store.selectedCity.neighborhoods, id: \.self) { hood in
                        NavigationLink(value: ListTarget.neighborhood(hood)) {
                            Text(hood)
                        }
                    }
                }

                Section {
                    NavigationLink(value: ListTarget.opening) { Text("Opening This Week") }
                    NavigationLink(value: ListTarget.closing) { Text("Closing This Week") }
                    NavigationLink(value: ListTarget.picks) { Text("Editor's Picks") }
                    NavigationLink(value: ListTarget.nearby) { Text("Nearby") }
                }
            }
            .guideGroupedList()
            .scrollContentBackground(.hidden)
            .background(Color.black)
            .navigationTitle(store.selectedCity.displayName)
            .toolbar {
                ToolbarItem(placement: .guideLeading) {
                    Button("Cities") { showCitySheet = true }
                        .tint(Color.guideBlue)
                }
                ToolbarItem(placement: .guideTrailing) {
                    Button {
                        showSearch = true
                    } label: {
                        Image(systemName: "magnifyingglass")
                    }
                    .tint(Color.guideBlue)
                }
            }
            .navigationDestination(for: ListTarget.self) { target in
                destination(for: target)
            }
            .navigationDestination(for: IndexedShows.self) { selection in
                ShowDetailView(shows: selection.shows, index: selection.index)
            }
            .sheet(isPresented: $showCitySheet) { CitySelectSheet() }
            .sheet(isPresented: $showSearch) { SearchSheet() }
        }
    }

    @ViewBuilder
    private func destination(for target: ListTarget) -> some View {
        switch target {
        case .museums:
            MuseumsView()
        case .myShows:
            MyShowsView()
        case .all:
            ShowListView(title: "All Current Shows", shows: store.currentShows)
        case .neighborhood(let hood):
            ShowListView(title: hood, shows: store.shows(in: hood))
        case .opening:
            ShowListView(title: "Opening This Week", shows: store.openingThisWeek)
        case .closing:
            ShowListView(title: "Closing This Week", shows: store.closingThisWeek)
        case .picks:
            ShowListView(title: "Editor's Picks", shows: store.editorsPicks)
        case .nearby:
            NearbyView()
        }
    }
}

enum ListTarget: Hashable {
    case museums, myShows, all, opening, closing, picks, nearby
    case neighborhood(String)
}

/// A standard show list row: display name, venue line, address, bookmark toggle.
struct ShowRow: View {
    let show: Show
    @EnvironmentObject private var store: ContentStore

    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 3) {
                Text(show.displayName)
                    .font(.headline)
                Text(show.venue.listLine)
                    .font(.subheadline)
                Text(show.venue.address)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button {
                store.toggleSaved(show)
            } label: {
                Image(systemName: store.isSaved(show) ? "bookmark.fill" : "bookmark")
                    .foregroundStyle(store.isSaved(show) ? Color.guideBlue : .secondary)
                    .frame(width: 38, height: 38)
                    .background(Color(white: 0.16), in: Circle())
            }
            .buttonStyle(.plain)
        }
        .padding(.vertical, 3)
    }
}

/// A reusable list-of-shows screen with a large title.
struct ShowListView: View {
    let title: String
    let shows: [Show]

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        List {
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
            if shows.isEmpty {
                Text("No shows in this list right now.")
                    .foregroundStyle(.secondary)
            }
        }
        .navigationTitle(title)
        .hideBackButton()
        .toolbar {
            ToolbarItem(placement: .guideLeading) {
                CircleIconButton(systemName: "chevron.left") { dismiss() }
            }
        }
    }
}
