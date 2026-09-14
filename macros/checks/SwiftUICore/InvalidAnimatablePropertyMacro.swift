public struct EmptyAnimatableData {}

@freestanding(expression)
public macro _SwiftUIAnimatableProperty<T>(_ t: T.Type) -> EmptyAnimatableData.Type = #externalMacro(
    module: "SwiftUIMacros", type: "InvalidAnimatablePropertyMacro"
)

let t = #_SwiftUIAnimatableProperty(String.self)
