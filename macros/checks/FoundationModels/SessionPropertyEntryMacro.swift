protocol SessionPropertyKey {
    associatedtype Value
    static var defaultValue: Value { get }
}

final class SessionPropertyValues {
    subscript<K: SessionPropertyKey>(key: K.Type) -> K.Value {
        get { fatalError() }
        set {}
    }
}

@attached(accessor)
@attached(peer, names: prefixed(__Key_))
macro SessionPropertyEntry() = #externalMacro(module: "FoundationModelsMacros", type: "SessionPropertyEntryMacro")

extension SessionPropertyValues {
    @SessionPropertyEntry var myFlag: Int = 42
}
