@attached(accessor, names: named(`init`), named(get), named(set))
@attached(peer, names: prefixed(`_`), prefixed(__), prefixed(`$`))
macro State() = #externalMacro(module: "SwiftUIMacros", type: "StateMacro")

struct V {
    @State var count: Int = 0
}
