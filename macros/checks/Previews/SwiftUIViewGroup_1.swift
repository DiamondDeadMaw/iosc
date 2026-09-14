public protocol View {}
public struct PreviewTrait<T> {}
public enum ViewTraitsNamespace { public enum ViewTraits {} }

@resultBuilder public struct ViewBuilder {
    public static func buildBlock<Content: View>(_ content: Content) -> Content { content }
}

@freestanding(declaration)
public macro Preview<T>(
    _ name: String? = nil,
    traits: PreviewTrait<ViewTraitsNamespace.ViewTraits>...,
    arguments: [T],
    @ViewBuilder body: @escaping @MainActor (T) -> any View
) = #externalMacro(module: "PreviewsMacros", type: "SwiftUIViewGroup_1")

struct DemoView: View {}

#Preview("Demo", arguments: [1, 2, 3]) { value in
    DemoView()
}
