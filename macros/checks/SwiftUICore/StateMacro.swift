enum SwiftUICore {
    struct State<Value> {
        var wrappedValue: Value { get { fatalError() } nonmutating set {} }
        var projectedValue: Binding<Value> { fatalError() }
        init(wrappedValue: Value) {}
        init(initialValue: Value) {}
    }
    struct Binding<Value> {}
}
@attached(accessor, names: named(init), named(get), named(set))
@attached(peer, names: prefixed(`_`), prefixed(__), prefixed(`$`))
public macro State() = #externalMacro(module: "SwiftUIMacros", type: "StateMacro")

struct TestView {
    @State var count: Int = 0
}

