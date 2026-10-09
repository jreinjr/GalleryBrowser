import SwiftUI

#if canImport(UIKit)
import UIKit
typealias PlatformImage = UIImage
extension Image {
    init(platform image: PlatformImage) { self.init(uiImage: image) }
}
#else
import AppKit
typealias PlatformImage = NSImage
extension Image {
    init(platform image: PlatformImage) { self.init(nsImage: image) }
}
#endif

extension Color {
    /// The light sky blue used for buttons, selected tabs, and section headers.
    static let guideBlue = Color(red: 0.38, green: 0.68, blue: 0.95)
}

extension View {
    /// iOS-only chrome modifiers, no-ops in the macOS preview build.
    @ViewBuilder func inlineTitleBar() -> some View {
        #if os(iOS)
        self.navigationBarTitleDisplayMode(.inline)
        #else
        self
        #endif
    }

    @ViewBuilder func hideBackButton() -> some View {
        // Cross-platform since macOS 13; the circular chrome button stands in.
        self.navigationBarBackButtonHidden(true)
    }

    @ViewBuilder func guideGroupedList() -> some View {
        #if os(iOS)
        self.listStyle(.insetGrouped)
        #else
        self.listStyle(.inset)
        #endif
    }
}

extension ToolbarItemPlacement {
    static var guideLeading: ToolbarItemPlacement {
        #if os(iOS)
        .topBarLeading
        #else
        .navigation
        #endif
    }

    static var guideTrailing: ToolbarItemPlacement {
        #if os(iOS)
        .topBarTrailing
        #else
        .primaryAction
        #endif
    }
}

/// Circular chrome button (back chevron, close X) used on detail screens.
struct CircleIconButton: View {
    let systemName: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: systemName)
                .font(.system(size: 17, weight: .semibold))
                .foregroundStyle(.white)
                .frame(width: 38, height: 38)
                .background(.black.opacity(0.45), in: Circle())
        }
        .buttonStyle(.plain)
    }
}

/// Full-width light-blue capsule action button (e.g. "Add to My Shows").
struct BlueCapsuleButton: View {
    let title: String
    let systemImage: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 8) {
                Image(systemName: systemImage)
                Text(title)
            }
            .font(.system(size: 17, weight: .semibold))
            .foregroundStyle(.white)
            .frame(maxWidth: .infinity)
            .padding(.vertical, 13)
            .background(Color.guideBlue, in: Capsule())
        }
        .buttonStyle(.plain)
    }
}

/// Paged image carousel for a show, backed by bundled image files.
/// Dots sit bottom-center; feed cards raise them by `dotsBottomInset` to
/// clear the frosted footer that covers the photo's bottom edge. When `onImageTap` is set, tapping the
/// image opens the full-screen zoomable viewer at the current page.
struct ImageCarousel: View {
    let imagePaths: [String]
    var dotsAlignment: Alignment = .bottom
    var dotsBottomInset: CGFloat = 0
    var onImageTap: ((Int) -> Void)? = nil

    @State private var page = 0

    private func pageImage(_ path: String) -> some View {
        // Color.clear adopts the proposed size, so the filled image is confined
        // to the carousel's own bounds instead of pushing them outward.
        Color.clear
            .overlay {
                if let image = ContentStore.image(path) {
                    Image(platform: image)
                        .resizable()
                        .scaledToFill()
                } else {
                    Rectangle().fill(Color(white: 0.14))
                        .overlay(ProgressView().tint(Color.guideBlue))
                }
            }
            .clipped()
    }

    /// Attach the open-viewer tap only when a handler exists — an always-on
    /// gesture would swallow taps meant for an enclosing NavigationLink card.
    @ViewBuilder
    private func viewerTap<V: View>(_ view: V, index: Int) -> some View {
        if let onImageTap {
            view.contentShape(Rectangle()).onTapGesture { onImageTap(index) }
        } else {
            view
        }
    }

    var body: some View {
        Group {
            #if os(iOS)
            TabView(selection: $page) {
                ForEach(Array(imagePaths.enumerated()), id: \.offset) { index, path in
                    viewerTap(pageImage(path).clipped(), index: index)
                        .tag(index)
                }
            }
            .tabViewStyle(.page(indexDisplayMode: .never))
            #else
            viewerTap(pageImage(imagePaths[min(page, imagePaths.count - 1)]).clipped(),
                      index: page)
                .overlay(alignment: .leading) {
                    if page > 0 {
                        CircleIconButton(systemName: "chevron.left") { page -= 1 }
                            .padding(.leading, 6)
                            .opacity(0.7)
                    }
                }
                .overlay(alignment: .trailing) {
                    if page < imagePaths.count - 1 {
                        CircleIconButton(systemName: "chevron.right") { page += 1 }
                            .padding(.trailing, 6)
                            .opacity(0.7)
                    }
                }
            #endif
        }
        .overlay(alignment: dotsAlignment) {
            if imagePaths.count > 1 {
                HStack(spacing: 5) {
                    ForEach(0..<imagePaths.count, id: \.self) { index in
                        Circle()
                            .fill(.white.opacity(index == page ? 0.95 : 0.45))
                            .frame(width: 6, height: 6)
                            // keeps white dots readable over pale photos
                            .shadow(color: .black.opacity(0.5), radius: 1.5)
                    }
                }
                .padding(10)
                .padding(.bottom, dotsBottomInset)
            }
        }
    }
}
