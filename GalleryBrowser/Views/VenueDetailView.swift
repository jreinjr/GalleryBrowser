import SwiftUI
import MapKit

/// Venue page: name/address/hours, the venue's current shows, a
/// non-interactive map card with the venue pin, and Directions / Open website /
/// Call venue actions. Pushed from a show detail (back chevron) or presented
/// as a sheet from the map when a venue has several shows (X to close).
struct VenueDetailView: View {
    let venue: Venue
    var presentedAsSheet = false

    @EnvironmentObject private var store: ContentStore
    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL

    private var shows: [Show] { store.shows(at: venue) }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
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
                    if let about = venue.about, !about.isEmpty {
                        Text(about)
                            .font(.subheadline)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.top, 6)
                    }
                }

                if !shows.isEmpty {
                    showsSection
                }

                mapCard

                BlueCapsuleButton(title: "Directions to venue", systemImage: "figure.walk") {
                    let placemark = MKPlacemark(coordinate: venue.coordinate)
                    let item = MKMapItem(placemark: placemark)
                    item.name = venue.name
                    item.openInMaps(launchOptions: [
                        MKLaunchOptionsDirectionsModeKey: MKLaunchOptionsDirectionsModeWalking
                    ])
                }
                if let site = venue.website, let url = URL(string: site) {
                    BlueCapsuleButton(title: "Open website", systemImage: "safari") {
                        openURL(url)
                    }
                }
                if let phone = venue.phone,
                   let url = URL(string: "tel:\(phone.filter { !$0.isWhitespace })") {
                    BlueCapsuleButton(title: "Call venue", systemImage: "phone") {
                        openURL(url)
                    }
                }
            }
            .padding(.horizontal, 18)
            .padding(.bottom, 90)
        }
        .background(Color.black)
        .navigationTitle(venue.name)
        .inlineTitleBar()
        .hideBackButton()
        .toolbar {
            ToolbarItem(placement: .guideLeading) {
                CircleIconButton(systemName: presentedAsSheet ? "xmark" : "chevron.left") { dismiss() }
            }
        }
    }

    /// Every current show at this venue; each row pushes its show detail.
    private var showsSection: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text("Shows")
                .font(.caption)
                .textCase(.uppercase)
                .foregroundStyle(.secondary)
                .padding(.leading, 16)
            VStack(spacing: 0) {
                ForEach(Array(shows.enumerated()), id: \.element.id) { index, show in
                    if index > 0 {
                        Divider()
                            .padding(.leading, 16)
                    }
                    NavigationLink {
                        ShowDetailView(shows: [show], index: 0)
                    } label: {
                        ShowRow(show: show)
                            .padding(.horizontal, 16)
                            .padding(.vertical, 11)
                    }
                    .buttonStyle(.plain)
                }
            }
            .background(Color(white: 0.11), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        }
        .padding(.top, 6)
    }

    private var mapCard: some View {
        Map(initialPosition: .region(MKCoordinateRegion(
            center: venue.coordinate,
            span: MKCoordinateSpan(latitudeDelta: 0.02, longitudeDelta: 0.02)
        )), interactionModes: []) {
            Marker(venue.name, coordinate: venue.coordinate)
                .tint(Color.guideBlue)
        }
        .frame(height: 320)
        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
    }
}
