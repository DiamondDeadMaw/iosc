import SwiftSyntax
import SwiftSyntaxMacros
import MacroSupport

// @Generable (FoundationModels)
// Roles: extension, member
// Reference: FoundationModels.Generable / GenerationSchema / GeneratedContent
// in the SDK swiftinterface.
public struct GenerableMacro: ExtensionMacro, MemberMacro {
    public static func expansion(
        of node: AttributeSyntax,
        attachedTo declaration: some DeclGroupSyntax,
        providingExtensionsOf type: some TypeSyntaxProtocol,
        conformingTo protocols: [TypeSyntax],
        in context: some MacroExpansionContext
    ) throws -> [ExtensionDeclSyntax] {
        let properties = storedProperties(of: declaration)

        let initAssignments = properties.isEmpty
            ? ""
            : properties.map { property in
                "self.\(property.name) = try generatedContent.value(\(property.type).self, forProperty: \"\(property.name)\")"
            }.joined(separator: "\n        ")

        let contentPairs = properties.isEmpty
            ? "[:]"
            : "[" + properties.map { "\"\($0.name)\": \($0.name)" }.joined(separator: ", ") + "]"

        let ext: DeclSyntax = """
            extension \(type.trimmed): Generable {
                public init(_ generatedContent: GeneratedContent) throws {
                    \(raw: initAssignments)
                }

                public var generatedContent: GeneratedContent {
                    GeneratedContent(properties: \(raw: contentPairs))
                }
            }
            """
        return [ext.cast(ExtensionDeclSyntax.self)]
    }

    public static func expansion(
        of node: AttributeSyntax,
        providingMembersOf declaration: some DeclGroupSyntax,
        conformingTo protocols: [TypeSyntax],
        in context: some MacroExpansionContext
    ) throws -> [DeclSyntax] {
        let properties = storedProperties(of: declaration)
        let description = macroArgument(named: "description", in: node) ?? "nil"

        let propertyEntries = properties.map { property -> String in
            if let propertyDescription = property.description {
                return "GenerationSchema.Property(name: \"\(property.name)\", description: \(propertyDescription), type: \(property.type).self)"
            }
            return "GenerationSchema.Property(name: \"\(property.name)\", type: \(property.type).self)"
        }.joined(separator: ",\n                ")

        let schema: DeclSyntax = """
            public static var generationSchema: GenerationSchema {
                GenerationSchema(
                    type: Self.self,
                    description: \(raw: description),
                    properties: [
                        \(raw: propertyEntries)
                    ]
                )
            }
            """
        return [schema]
    }
}

private struct GenerableProperty {
    let name: String
    let type: String
    let description: String?
}

private func storedProperties(of declaration: some DeclGroupSyntax) -> [GenerableProperty] {
    declaration.memberBlock.members.compactMap { member -> GenerableProperty? in
        guard let varDecl = member.decl.as(VariableDeclSyntax.self) else { return nil }
        if varDecl.modifiers.contains(where: { $0.name.tokenKind == .keyword(.static) }) {
            return nil
        }
        guard let binding = varDecl.bindings.first,
              let identifier = binding.pattern.as(IdentifierPatternSyntax.self)?.identifier.text,
              let typeAnnotation = binding.typeAnnotation?.type.trimmedDescription
        else {
            return nil
        }
        if let accessors = binding.accessorBlock {
            switch accessors.accessors {
            case .accessors(let list):
                let isStored = list.allSatisfy {
                    $0.accessorSpecifier.tokenKind == .keyword(.willSet) || $0.accessorSpecifier.tokenKind == .keyword(.didSet)
                }
                if !isStored { return nil }
            case .getter:
                return nil
            }
        }
        return GenerableProperty(name: identifier, type: typeAnnotation, description: guideDescription(of: varDecl))
    }
}

private func guideDescription(of varDecl: VariableDeclSyntax) -> String? {
    for attribute in varDecl.attributes {
        guard case .attribute(let attr) = attribute,
              attr.attributeName.trimmedDescription == "Guide",
              case .argumentList(let list) = attr.arguments
        else {
            continue
        }
        for argument in list where argument.label == nil || argument.label?.text == "description" {
            return argument.expression.trimmedDescription
        }
    }
    return nil
}

private func macroArgument(named label: String, in node: AttributeSyntax) -> String? {
    guard case .argumentList(let list) = node.arguments else { return nil }
    for argument in list where argument.label?.text == label {
        return argument.expression.trimmedDescription
    }
    return nil
}
