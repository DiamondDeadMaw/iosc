public enum TipKit {
    public enum Tips {
        public struct ParameterOption {
            public static var transient: ParameterOption { ParameterOption() }
        }
        public struct Parameter<Value> {
            public var wrappedValue: Value {
                get { fatalError() }
                set {}
            }
            public init(_ enclosingInstance: Any.Type, _ name: String, _ initialValue: Value, _ options: ParameterOption...) {}
        }
    }
}

@attached(accessor, names: named(get), named(set))
@attached(peer, names: prefixed(`$`))
public macro Parameter(_ options: TipKit.Tips.ParameterOption...) = #externalMacro(module: "TipKitMacros", type: "ParameterMacro")

struct CountTips {
    @Parameter
    var count: Int = 0

    @Parameter(.transient)
    var isShown: Bool = false
}
