public class UIView {
    public init() {}
}
public struct PreviewTrait<T> {}
public enum ViewTraitsNamespace { public enum ViewTraits {} }

@freestanding(declaration)
public macro Preview<T>(
    _ name: String? = nil,
    traits: PreviewTrait<ViewTraitsNamespace.ViewTraits>...,
    arguments: [T],
    body: @escaping @MainActor (T) -> UIView
) = #externalMacro(module: "PreviewsMacros", type: "PreviewCommonGroup")

#Preview("Kit Demo", arguments: ["a", "b"]) { value in
    UIView()
}
