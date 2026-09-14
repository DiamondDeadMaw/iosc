@attached(extension, conformances: Generable, names: named(`init`(_:)), named(generatedContent))
@attached(member, names: arbitrary)
macro Generable(description: String? = nil) = #externalMacro(module: "FoundationModelsMacros", type: "GenerableMacro")

@Generable
struct Person {
    var name: String
    var age: Int
}
