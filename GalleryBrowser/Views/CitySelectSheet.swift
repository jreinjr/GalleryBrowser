import SwiftUI

/// "Select City" modal sheet with a checkmark on the current city.
struct CitySelectSheet: View {
    @EnvironmentObject private var store: ContentStore
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section {
                    ForEach(City.all) { city in
                        Button {
                            store.selectedCityKey = city.key
                            dismiss()
                        } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(city.displayName)
                                        .foregroundStyle(.white)
                                    if let note = city.availabilityNote {
                                        Text(note)
                                            .font(.subheadline)
                                            .foregroundStyle(.secondary)
                                    }
                                }
                                Spacer()
                                if city.key == store.selectedCityKey {
                                    Image(systemName: "checkmark")
                                        .foregroundStyle(Color.guideBlue)
                                }
                            }
                        }
                    }
                }
            }
            .guideGroupedList()
            .scrollContentBackground(.hidden)
            .background(Color.black)
            .navigationTitle("Select City")
            .inlineTitleBar()
            .toolbar {
                ToolbarItem(placement: .guideTrailing) {
                    CircleIconButton(systemName: "xmark") { dismiss() }
                }
            }
        }
        .preferredColorScheme(.dark)
        #if os(macOS)
        .frame(width: 380, height: 640)
        .overlay(alignment: .topTrailing) {
            CircleIconButton(systemName: "xmark") { dismiss() }
                .padding(12)
        }
        #endif
    }
}
