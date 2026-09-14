@attached(peer)
macro Guide(description: String) = #externalMacro(module: "FoundationModelsMacros", type: "GuideMacro")

struct Person {
    @Guide(description: "the person's name")
    var name: String
}
