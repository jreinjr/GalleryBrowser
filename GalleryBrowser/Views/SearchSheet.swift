import SwiftUI

/// Search modal: rounded field, "On View" results section, big empty state.
struct SearchSheet: View {
    @EnvironmentObject private var store: ContentStore
    @Environment(\.dismiss) private var dismiss
    @State private var query = ""
    @FocusState private var fieldFocused: Bool

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                HStack(spacing: 8) {
                    Image(systemName: "magnifyingglass")
                        .foregroundStyle(.secondary)
                    TextField("", text: $query)
                        .focused($fieldFocused)
                        .autocorrectionDisabled()
                    if !query.isEmpty {
                        Button {
                            query = ""
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 11)
                .background(Color(white: 0.16), in: RoundedRectangle(cornerRadius: 14))
                .padding(.horizontal, 16)
                .padding(.top, 8)

                let results = store.search(query)
                if query.trimmingCharacters(in: .whitespaces).isEmpty {
                    Spacer()
                    VStack(spacing: 14) {
                        Image(systemName: "magnifyingglass")
                            .font(.system(size: 56))
                            .foregroundStyle(.tertiary)
                        Text("Search Shows")
                            .font(.title2.weight(.bold))
                    }
                    Spacer()
                    Spacer()
                } else if results.isEmpty {
                    Spacer()
                    Text("No matching shows.")
                        .foregroundStyle(.secondary)
                    Spacer()
                } else {
                    List {
                        Section {
                            ForEach(Array(results.enumerated()), id: \.element.id) { index, show in
                                NavigationLink(value: IndexedShows(shows: results, index: index)) {
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
                }
            }
            .background(Color.black)
            .navigationTitle("Search")
            .inlineTitleBar()
            .toolbar {
                ToolbarItem(placement: .guideTrailing) {
                    CircleIconButton(systemName: "xmark") { dismiss() }
                }
            }
            .navigationDestination(for: IndexedShows.self) { selection in
                ShowDetailView(shows: selection.shows, index: selection.index)
            }
            .onAppear { fieldFocused = true }
        }
        .preferredColorScheme(.dark)
        #if os(macOS)
        .frame(width: 380, height: 700)
        .overlay(alignment: .topTrailing) {
            CircleIconButton(systemName: "xmark") { dismiss() }
                .padding(12)
        }
        #endif
    }
}
