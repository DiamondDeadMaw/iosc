public protocol EnvironmentKey {
    associatedtype Value
    static var defaultValue: Self.Value { get }
}

public struct EnvironmentValues {
    public subscript<K: EnvironmentKey>(key: K.Type) -> K.Value {
        get { fatalError() }
        set { fatalError() }
    }
}

@attached(accessor)
@attached(peer, names: prefixed(__Key_))
public macro Entry() = #externalMacro(module: "SwiftUIMacros", type: "EntryMacro")

extension EnvironmentValues {
    @Entry var customTitle: String = "Default"
}
