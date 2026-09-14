enum SwiftUICore {
    struct Binding<Value> {}
    struct State<Value> {
        var projectedValue: Binding<Value> { Binding<Value>() }
    }
}
@attached(accessor, names: named(get))
public macro _StateProjectedValue() = #externalMacro(module: "SwiftUIMacros", type: "StateProjectedValueMacro")

struct TestView {
    var _foo: SwiftUICore.State<Int> = SwiftUICore.State<Int>()
    @_StateProjectedValue
    var foo: SwiftUICore.Binding<Int>
}

