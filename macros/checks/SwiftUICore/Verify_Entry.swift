@attached(accessor)
@attached(peer, names: prefixed(__Key_))
macro Entry() = #externalMacro(module: "SwiftUIMacros", type: "EntryMacro")

struct S {
    @Entry var myFlag: Int = 42
}
