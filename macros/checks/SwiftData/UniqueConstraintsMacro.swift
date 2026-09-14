@freestanding(declaration)
public macro Unique<T: AnyObject>(_ constraints: [PartialKeyPath<T>]...) = #externalMacro(module: "SwiftDataMacros", type: "UniqueConstraintsMacro")

final class Person {
    var email: String = ""
    #Unique<Person>([\.email])
}
