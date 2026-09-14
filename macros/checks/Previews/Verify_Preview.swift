@freestanding(declaration)
macro Preview(_ name: String? = nil, body: @escaping () -> Int) = #externalMacro(module: "PreviewsMacros", type: "SwiftUIView")

#Preview("Demo") { 42 }
