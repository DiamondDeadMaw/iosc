public protocol EnvironmentKey {
    associatedtype Value
    static var defaultValue: Self.Value { get }
}

@freestanding(declaration, names: named(defaultValue))
public macro _EntryDefaultValue<T>(_ value: T, macroName: String) = #externalMacro(module: "SwiftUIMacros", type: "UnsafeEntryDefaultMacro")

struct TestKey: EnvironmentKey {
    typealias Value = Int
    #_EntryDefaultValue(42, macroName: "Entry")
}
