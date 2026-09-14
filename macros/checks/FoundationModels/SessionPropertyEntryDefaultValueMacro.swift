@attached(accessor)
macro __SessionPropertyEntryDefaultValue() = #externalMacro(module: "FoundationModelsMacros", type: "SessionPropertyEntryDefaultValueMacro")

struct Test {
    @__SessionPropertyEntryDefaultValue var x: Int = 1
}
