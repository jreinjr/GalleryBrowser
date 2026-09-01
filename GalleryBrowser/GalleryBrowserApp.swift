import SwiftUI

@main
struct GalleryBrowserApp: App {
    @StateObject private var store = ContentStore()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(store)
                .preferredColorScheme(.dark)
                .tint(Color.guideBlue)
                #if os(macOS)
                .frame(width: 393, height: 800)
                #endif
        }
        #if os(macOS)
        .windowResizability(.contentSize)
        #endif
    }
}

struct RootView: View {
    #if os(iOS)
    var body: some View {
        TabView {
            FeaturedView()
                .tabItem { Label("Featured", systemImage: "star.fill") }
            ListRootView()
                .tabItem { Label("List", systemImage: "list.bullet") }
            MapTabView()
                .tabItem { Label("Map", systemImage: "map.fill") }
        }
    }
    #else
    // macOS preview build: replicate the floating pill tab bar the iOS system
    // tab bar provides, so the demo matches the phone look.
    @State private var tab = 0

    var body: some View {
        ZStack(alignment: .bottom) {
            Group {
                switch tab {
                case 0: FeaturedView()
                case 1: ListRootView()
                default: MapTabView()
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)

            HStack(spacing: 6) {
                tabButton(0, "Featured", "star.fill")
                tabButton(1, "List", "list.bullet")
                tabButton(2, "Map", "map.fill")
            }
            .padding(6)
            .background(.ultraThinMaterial, in: Capsule())
            .padding(.bottom, 14)
        }
    }

    private func tabButton(_ id: Int, _ title: String, _ icon: String) -> some View {
        Button {
            tab = id
        } label: {
            VStack(spacing: 3) {
                Image(systemName: icon)
                    .font(.system(size: 17, weight: .semibold))
                Text(title)
                    .font(.system(size: 10, weight: .medium))
            }
            .foregroundStyle(tab == id ? Color.guideBlue : .white)
            .frame(width: 62, height: 48)
            .background(tab == id ? Color.white.opacity(0.12) : .clear, in: Capsule())
        }
        .buttonStyle(.plain)
    }
    #endif
}
