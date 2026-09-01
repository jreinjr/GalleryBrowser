import SwiftUI

/// Show detail: carousel, artist + italic title, dates, Add to My Shows,
/// venue block, and the long description. Pushed from feeds/lists (back +
/// up/down chevrons) or presented as a sheet from the map (X to close).
struct ShowDetailView: View {
    let shows: [Show]
    @State var index: Int
    var presentedAsSheet = false

    @EnvironmentObject private var store: ContentStore
    @Environment(\.dismiss) private var dismiss
    @State private var viewerSelection: ViewerSelection?

    private var show: Show { shows[index] }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                ImageCarousel(imagePaths: show.images, dotsAlignment: .bottom) { tapped in
                    viewerSelection = ViewerSelection(images: show.images, index: tapped)
                }
                .frame(height: 340)
                .overlay(alignment: .bottomTrailing) {
                    Image(systemName: "arrow.up.left.and.arrow.down.right")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(.white)
                        .frame(width: 30, height: 30)
                        .background(.black.opacity(0.45), in: Circle())
                        .padding(10)
                        .allowsHitTesting(false)
                }
                .id(show.id)

                VStack(alignment: .leading, spacing: 10) {
                    if let artist = show.artist {
                        Text(artist)
                            .font(.title2)
                    }
                    Text(show.title)
                        .font(.title2)
                        .italic()
                    Text(show.dateLine)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    if let reception = show.reception {
                        Text("Reception: \(reception)")
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                    }

                    BlueCapsuleButton(
                        title: store.isSaved(show) ? "Added to My Shows" : "Add to My Shows",
                        systemImage: store.isSaved(show) ? "bookmark.fill" : "bookmark"
                    ) {
                        store.toggleSaved(show)
                    }
                    .padding(.vertical, 8)

                    venueBlock

                    Divider()
                        .padding(.vertical, 8)

                    Text(show.description)
                        .font(.body)
                        .lineSpacing(3)
                }
                .padding(.horizontal, 18)
                .padding(.top, 16)
                .padding(.bottom, 90)
            }
        }
        .background(Color.black)
        .ignoresSafeArea(edges: .top)
        .navigationTitle("")
        .hideBackButton()
        #if os(iOS)
        .fullScreenCover(item: $viewerSelection) { selection in
            FullScreenImageViewer(images: selection.images, index: selection.index)
        }
        #else
        .sheet(item: $viewerSelection) { selection in
            FullScreenImageViewer(images: selection.images, index: selection.index)
        }
        #endif
        .toolbar {
            ToolbarItem(placement: .guideLeading) {
                CircleIconButton(systemName: presentedAsSheet ? "xmark" : "chevron.left") {
                    dismiss()
                }
            }
            if !presentedAsSheet && shows.count > 1 {
                ToolbarItem(placement: .guideTrailing) {
                    HStack(spacing: 14) {
                        Button {
                            withAnimation { index -= 1 }
                        } label: {
                            Image(systemName: "chevron.up")
                                .font(.system(size: 16, weight: .semibold))
                        }
                        .disabled(index == 0)

                        Button {
                            withAnimation { index += 1 }
                        } label: {
                            Image(systemName: "chevron.down")
                                .font(.system(size: 16, weight: .semibold))
                        }
                        .disabled(index == shows.count - 1)
                    }
                    .tint(Color.guideBlue)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 8)
                    .background(.black.opacity(0.45), in: Capsule())
                }
            }
        }
    }

    struct ViewerSelection: Identifiable {
        let images: [String]
        let index: Int
        var id: Int { index }
    }

    private var venueBlock: some View {
        NavigationLink(value: show.venue) {
            HStack {
                VStack(alignment: .leading, spacing: 5) {
                    Text(show.venue.name)
                        .font(.headline)
                    Text(show.venue.fullAddress)
                        .font(.subheadline)
                    ForEach(show.venue.hours, id: \.self) { line in
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
        .navigationDestination(for: Venue.self) { venue in
            VenueDetailView(venue: venue)
        }
    }
}
