import SwiftUI
import MapKit

/// Venue page: name/address/hours, a non-interactive map card with the venue
/// pin, and Directions / Open website / Call venue actions.
struct VenueDetailView: View {
    let venue: Venue

    @Environment(\.dismiss) private var dismiss
    @Environment(\.openURL) private var openURL

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
                CircleIconButton(systemName: "chevron.left") { dismiss() }
            }
        }
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
