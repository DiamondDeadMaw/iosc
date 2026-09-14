public protocol VectorArithmetic {
    static var zero: Self { get }
}
extension Double: VectorArithmetic {
    public static var zero: Double { 0.0 }
}
extension Float: VectorArithmetic {
    public static var zero: Float { 0.0 }
}

public protocol Animatable {
    associatedtype AnimatableData: VectorArithmetic
    var animatableData: AnimatableData { get set }
}

public struct EmptyAnimatableData: VectorArithmetic {
    public static var zero: EmptyAnimatableData { .init() }
}

public struct AnimatablePair<First: VectorArithmetic, Second: VectorArithmetic>: VectorArithmetic {
    public var first: First
    public var second: Second
    public init(_ first: First, _ second: Second) {
        self.first = first
        self.second = second
    }
    public static var zero: AnimatablePair<First, Second> {
        .init(First.zero, Second.zero)
    }
}

@attached(accessor, names: named(willSet))
public macro AnimatableIgnored() = #externalMacro(
    module: "SwiftUIMacros", type: "AnimatableIgnoredMacro"
)

@attached(extension, conformances: Animatable)
@attached(member, names: named(animatableData))
public macro Animatable() = #externalMacro(
    module: "SwiftUIMacros", type: "AnimatableValuesMacro"
)

@Animatable
struct TestMultiBinding {
    var x: Double, y: Float
}

@Animatable
struct TestComputedAndStatic {
    static var ignoredStatic: Double = 1.0
    var a: Double
    var computed: Double {
        get { a * 2 }
        set { a = newValue / 2 }
    }
    @AnimatableIgnored
    var ignored: Float = 0
}
