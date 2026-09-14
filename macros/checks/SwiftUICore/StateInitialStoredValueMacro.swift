enum SwiftUICore {
    struct State<Value> {
        var wrappedValue: Value
    }
}
@attached(accessor, names: named(get))
public macro _StateInitialStoredValue(_ initialValue: String) = #externalMacro(module: "SwiftUIMacros", type: "StateInitialStoredValueMacro")

struct TestView {
    var _foo: SwiftUICore.State<Int>
    @_StateInitialStoredValue("foo")
    var fooInitial: SwiftUICore.State<Int>
}

