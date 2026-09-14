@freestanding(declaration)
public macro Index<T: AnyObject>(_ indices: [PartialKeyPath<T>]...) = #externalMacro(module: "SwiftDataMacros", type: "IndexMacro")

final class Person {
    var lastName: String = ""
    var firstName: String = ""
    #Index<Person>([\.lastName], [\.firstName])
}
