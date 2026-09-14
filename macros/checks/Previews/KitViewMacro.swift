public class UIView {
    public init() {}
}
public struct PreviewTrait<T> {}
public enum ViewTraitsNamespace { public enum ViewTraits {} }

@freestanding(declaration)
public macro Preview(
    _ name: String? = nil,
    traits: PreviewTrait<ViewTraitsNamespace.ViewTraits>...,
    body: @escaping @MainActor () -> UIView
) = #externalMacro(module: "PreviewsMacros", type: "KitViewMacro")

#Preview("Kit Demo") {
    UIView()
}
