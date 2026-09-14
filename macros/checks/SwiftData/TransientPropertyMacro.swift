@attached(peer)
public macro Transient() = #externalMacro(module: "SwiftDataMacros", type: "TransientPropertyMacro")

struct Item {
    @Transient var cache: String = ""
}
