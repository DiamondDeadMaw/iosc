@attached(accessor, names: named(init), named(get), named(set))
@attached(peer, names: prefixed(`_`))
public macro _TransformablePersistedProperty() = #externalMacro(module: "SwiftDataMacros", type: "TransformablePersistedPropertyMacro")

final class Widget {
    func getValue<Value>(forKey: KeyPath<Widget, Value>) -> Value { fatalError() }
    func setValue<Value>(forKey: KeyPath<Widget, Value>, to newValue: Value) {}

    @_TransformablePersistedProperty var payload: [String: Int] = [:]
}
