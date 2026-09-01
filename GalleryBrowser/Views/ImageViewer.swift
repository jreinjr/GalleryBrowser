import SwiftUI

/// Full-screen viewer for a show's images: page through all of them, pinch or
/// double-tap to zoom, drag to pan. X closes; "n / m" counter at the bottom.
struct FullScreenImageViewer: View {
    let images: [String]
    @State var index: Int
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            #if os(iOS)
            TabView(selection: $index) {
                ForEach(Array(images.enumerated()), id: \.offset) { i, path in
                    ZoomableImage(path: path, onDismissPull: { dismiss() })
                        .tag(i)
                }
            }
            .tabViewStyle(.page(indexDisplayMode: .never))
            .ignoresSafeArea()
            #else
            ZoomableImage(path: images[index])
                .id(index)
                .overlay(alignment: .leading) {
                    if index > 0 {
                        CircleIconButton(systemName: "chevron.left") { index -= 1 }
                            .padding(.leading, 10)
                    }
                }
                .overlay(alignment: .trailing) {
                    if index < images.count - 1 {
                        CircleIconButton(systemName: "chevron.right") { index += 1 }
                            .padding(.trailing, 10)
                    }
                }
            #endif
        }
        .overlay(alignment: .topTrailing) {
            CircleIconButton(systemName: "xmark") { dismiss() }
                .padding(.top, 14)
                .padding(.trailing, 16)
        }
        .overlay(alignment: .bottom) {
            if images.count > 1 {
                Text("\(index + 1) / \(images.count)")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 6)
                    .background(.black.opacity(0.5), in: Capsule())
                    .padding(.bottom, 22)
            }
        }
        .preferredColorScheme(.dark)
        #if os(macOS)
        .frame(width: 380, height: 740)
        #endif
    }
}

/// One image with pinch-to-zoom (1x-5x), drag-to-pan while zoomed, and
/// double-tap (double-click on the Mac preview) to toggle 2.5x.
/// An unzoomed downward pull asks the presenter to dismiss.
struct ZoomableImage: View {
    let path: String
    var onDismissPull: (() -> Void)? = nil

    @State private var scale: CGFloat = 1
    @State private var steadyScale: CGFloat = 1
    @State private var offset: CGSize = .zero
    @State private var steadyOffset: CGSize = .zero

    var body: some View {
        GeometryReader { geo in
            Group {
                if let image = ContentStore.image(path) {
                    Image(platform: image)
                        .resizable()
                        .scaledToFit()
                } else {
                    Color.black
                }
            }
            .frame(width: geo.size.width, height: geo.size.height)
            .scaleEffect(scale)
            .offset(offset)
            .contentShape(Rectangle())
            .gesture(magnification.simultaneously(with: pan))
            .onTapGesture(count: 2) {
                withAnimation(.spring(duration: 0.3)) {
                    if scale > 1.01 {
                        reset()
                    } else {
                        scale = 2.5
                        steadyScale = 2.5
                    }
                }
            }
        }
        .ignoresSafeArea()
    }

    private var magnification: some Gesture {
        MagnificationGesture()
            .onChanged { value in
                scale = min(max(steadyScale * value, 0.8), 5)
            }
            .onEnded { _ in
                withAnimation(.spring(duration: 0.25)) {
                    scale = min(max(scale, 1), 5)
                    if scale <= 1.01 { reset() }
                }
                steadyScale = scale
            }
    }

    private var pan: some Gesture {
        DragGesture()
            .onChanged { value in
                guard scale > 1.01 else { return }
                offset = CGSize(width: steadyOffset.width + value.translation.width,
                                height: steadyOffset.height + value.translation.height)
            }
            .onEnded { value in
                if scale <= 1.01 {
                    if value.translation.height > 110,
                       abs(value.translation.width) < abs(value.translation.height) {
                        onDismissPull?()
                    }
                    return
                }
                steadyOffset = offset
            }
    }

    private func reset() {
        scale = 1
        steadyScale = 1
        offset = .zero
        steadyOffset = .zero
    }
}
