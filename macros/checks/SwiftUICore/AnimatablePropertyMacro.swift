public protocol VectorArithmetic {}
extension Double: VectorArithmetic {}

@freestanding(expression)
public macro _SwiftUIAnimatableProperty<T>(_ t: T.Type) -> T.Type = #externalMacro(
    module: "SwiftUIMacros", type: "AnimatablePropertyMacro"
) where T : VectorArithmetic

let t = #_SwiftUIAnimatableProperty(Double.self)
