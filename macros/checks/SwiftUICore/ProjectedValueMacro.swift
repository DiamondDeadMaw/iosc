enum SwiftUICore {
    struct Binding<Value> {}
    struct State<Value> {
        var projectedValue: Binding<Value> { Binding<Value>() }
    }
}
@attached(accessor, names: named(get))
public macro _PropertyWrapperProjectedValue() = #externalMacro(module: "SwiftUIMacros", type: "ProjectedValueMacro")

struct TestView {
    var _foo: SwiftUICore.State<Int> = SwiftUICore.State<Int>()
    @_PropertyWrapperProjectedValue
    var foo: SwiftUICore.Binding<Int>
}

