@attached(accessor, names: named(init), named(get), named(set))
@attached(peer, names: prefixed(`_`))
public macro _PersistedProperty() = #externalMacro(module: "SwiftDataMacros", type: "PersistedPropertyMacro")

final class Item {
    func getValue<Value>(forKey: KeyPath<Item, Value>) -> Value { fatalError() }
    func setValue<Value>(forKey: KeyPath<Item, Value>, to newValue: Value) {}

    @_PersistedProperty var name: String = "x"
}
