enum SwiftUICore {
    struct State<Value> {
        var wrappedValue: Value { get { fatalError() } nonmutating set {} }
        init(initialValue: Value) {}
    }
}
@attached(accessor, names: named(init), named(get), named(set))
public macro _StatePropertyWrapperStorage(initialValue: String) = #externalMacro(module: "SwiftUIMacros", type: "StatePropertyWrapperStorageMacro")

@attached(accessor, names: named(init), named(get), named(set))
public macro _StatePropertyWrapperStorage() = #externalMacro(module: "SwiftUIMacros", type: "StatePropertyWrapperStorageMacro")

struct TestView {
    var _count: SwiftUICore.State<Int>
    @_StatePropertyWrapperStorage
    var count: Int

    var _other: SwiftUICore.State<Int>
    @_StatePropertyWrapperStorage(initialValue: "other")
    var other: Int
}

