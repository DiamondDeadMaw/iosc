@attached(peer)
public macro Previewable() = #externalMacro(module: "PreviewsMacros", type: "Previewable")

func demo() {
    @Previewable var x = 0
    _ = x
}
