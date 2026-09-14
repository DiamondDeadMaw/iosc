public protocol EnvironmentKey {
    associatedtype Value
    static var defaultValue: Self.Value { get }
}

@attached(accessor)
public macro __EntryDefaultValue() = #externalMacro(module: "SwiftUIMacros", type: "EntryDefaultValueMacro")

struct Test {
    @__EntryDefaultValue var x: Int = 1
}
