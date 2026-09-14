enum SwiftUICore {
    struct State<Value> {}
}
@freestanding(declaration, names: arbitrary)
public macro _SwiftUIState<T>(_ type: T.Type, _ named: String) = #externalMacro(module: "SwiftUIMacros", type: "StateTypeMacro")

struct TestView {
    #_SwiftUIState(Int.self, "counter")
}

