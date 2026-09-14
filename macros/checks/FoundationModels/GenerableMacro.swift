protocol ConvertibleFromGeneratedContent {
    init(_ content: GeneratedContent) throws
}

protocol ConvertibleToGeneratedContent {
    var generatedContent: GeneratedContent { get }
}

protocol Generable: ConvertibleFromGeneratedContent, ConvertibleToGeneratedContent {
    associatedtype PartiallyGenerated: ConvertibleFromGeneratedContent = Self
    static var generationSchema: GenerationSchema { get }
}

struct GeneratedContent {
    init(properties: [String: any ConvertibleToGeneratedContent]) {}
    func value<T: ConvertibleFromGeneratedContent>(_ type: T.Type, forProperty: String) throws -> T {
        fatalError()
    }
}

struct GenerationSchema {
    struct Property {
        init<T>(name: String, description: String? = nil, type: T.Type) {}
    }
    init(type: Any.Type, description: String? = nil, properties: [Property]) {}
}

extension Int: ConvertibleFromGeneratedContent, ConvertibleToGeneratedContent {
    init(_ content: GeneratedContent) throws { self = 0 }
    var generatedContent: GeneratedContent { GeneratedContent(properties: [:]) }
}

extension String: ConvertibleFromGeneratedContent, ConvertibleToGeneratedContent {
    init(_ content: GeneratedContent) throws { self = "" }
    var generatedContent: GeneratedContent { GeneratedContent(properties: [:]) }
}

@attached(extension, conformances: Generable, names: named(init(_:)), named(generatedContent))
@attached(member, names: arbitrary)
macro Generable(description: String? = nil) = #externalMacro(module: "FoundationModelsMacros", type: "GenerableMacro")

@attached(peer)
macro Guide(description: String) = #externalMacro(module: "FoundationModelsMacros", type: "GuideMacro")

@Generable(description: "A person")
struct Person {
    @Guide(description: "the person's name")
    var name: String
    var age: Int
}
