public protocol View {}

@resultBuilder public struct ViewBuilder {
    public static func buildBlock<Content: View>(_ content: Content) -> Content { content }
}

@freestanding(declaration)
public macro Preview(
    _ name: String? = nil,
    @ViewBuilder body: @escaping @MainActor () -> any View
) = #externalMacro(module: "PreviewsMacros", type: "SwiftUIView")

struct DemoView: View {}

#Preview("Demo") {
    DemoView()
}
