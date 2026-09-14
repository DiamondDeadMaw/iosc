@attached(extension, conformances: Animatable)
@attached(member, names: named(animatableData))
macro Animatable() = #externalMacro(module: "SwiftUIMacros", type: "AnimatableValuesMacro")

@attached(accessor, names: named(willSet))
macro AnimatableIgnored() = #externalMacro(module: "SwiftUIMacros", type: "AnimatableIgnoredMacro")

@Animatable
struct Point {
    var x: Double = 0
    var y: Double = 0
    @AnimatableIgnored var label: String = ""
}
