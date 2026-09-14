@attached(accessor, names: named(willSet))
public macro AnimatableIgnored() = #externalMacro(
    module: "SwiftUIMacros", type: "AnimatableIgnoredMacro"
)

struct Foo {
    @AnimatableIgnored
    var x: Int = 0
}
