import SwiftSyntax
import SwiftSyntaxBuilder
import SwiftSyntaxMacros
import MacroSupport

// @Model (SwiftData)
// Roles: member, memberAttribute, extension
public struct PersistentModelMacro: MemberMacro, MemberAttributeMacro, ExtensionMacro {
    public static func expansion(
        of node: AttributeSyntax,
        providingMembersOf declaration: some DeclGroupSyntax,
        conformingTo protocols: [TypeSyntax],
        in context: some MacroExpansionContext
    ) throws -> [DeclSyntax] {
        let properties = persistedStoredProperties(of: declaration)
        let typeName = concreteTypeName(of: declaration)

        var members: [DeclSyntax] = []

        // The default matters: a model's own init does not know about this stored
        // property, so without one every hand written init fails to compile
        members.append("""
            private var _$backingData: any BackingData<\(raw: typeName)> = \(raw: typeName).createBackingData()
            """)

        members.append("""
            public var persistentBackingData: any BackingData<\(raw: typeName)> {
                get { _$backingData }
                set { _$backingData = newValue }
            }
            """)

        let metadataEntries = properties.map { property -> String in
            guard let relationshipArguments = property.relationshipArguments,
                  let typeAnnotation = property.typeAnnotation,
                  let destination = relationshipDestination(typeAnnotation)
            else {
                return "Schema.PropertyMetadata(name: \"\(property.name)\", keypath: \\\(typeName).\(property.name))"
            }
            let defaultClause = property.initializer.map { ", defaultValue: \($0)" } ?? ""
            return """
                Schema.PropertyMetadata(name: "\(property.name)", keypath: \\\(typeName).\(property.name)\(defaultClause), metadata: {
                        let relationship = Schema.Relationship(\(relationshipArguments))
                        relationship.name = "\(property.name)"
                        relationship.keypath = \\\(typeName).\(property.name)
                        relationship.valueType = (\(typeAnnotation)).self
                        relationship.destination = "\(destination)"
                        return relationship
                    }())
                """
        }.joined(separator: ",\n        ")

        members.append("""
            public static var schemaMetadata: [Schema.PropertyMetadata] {
                [
                \(raw: metadataEntries.isEmpty ? "" : "        " + metadataEntries)
                ]
            }
            """)

        members.append("""
            public required init(backingData: any BackingData<\(raw: typeName)>) {
                self._$backingData = backingData
            }
            """)

        members.append("""
            private let _$observationRegistrar = ObservationRegistrar()
            """)

        members.append("""
            private enum _SwiftDataNoType {
            }
            """)

        members.append("""
            internal nonisolated func access<Member>(
                keyPath: KeyPath<\(raw: typeName), Member>
            ) {
                _$observationRegistrar.access(self, keyPath: keyPath)
            }
            """)

        members.append("""
            internal nonisolated func withMutation<Member, MutationResult>(
                keyPath: KeyPath<\(raw: typeName), Member>,
                _ mutation: () throws -> MutationResult
            ) rethrows -> MutationResult {
                try _$observationRegistrar.withMutation(of: self, keyPath: keyPath, mutation)
            }
            """)

        return members
    }

    public static func expansion(
        of node: AttributeSyntax,
        attachedTo declaration: some DeclGroupSyntax,
        providingAttributesFor member: some DeclSyntaxProtocol,
        in context: some MacroExpansionContext
    ) throws -> [AttributeSyntax] {
        guard let varDecl = member.as(VariableDeclSyntax.self), isPersistedStoredProperty(varDecl) else {
            return []
        }
        return [
            AttributeSyntax(
                attributeName: IdentifierTypeSyntax(name: .identifier("_PersistedProperty"))
            )
        ]
    }

    public static func expansion(
        of node: AttributeSyntax,
        attachedTo declaration: some DeclGroupSyntax,
        providingExtensionsOf type: some TypeSyntaxProtocol,
        conformingTo protocols: [TypeSyntax],
        in context: some MacroExpansionContext
    ) throws -> [ExtensionDeclSyntax] {
        let ext: DeclSyntax = """
            extension \(type.trimmed): Observable, PersistentModel, Sendable {
            }
            """
        return [ext.cast(ExtensionDeclSyntax.self)]
    }
}

private struct PersistedProperty {
    let name: String
    let typeAnnotation: String?
    let relationshipArguments: String?
    let initializer: String?
}

private func relationshipDestination(_ typeAnnotation: String) -> String? {
    var name = typeAnnotation
    if name.hasSuffix("?") {
        name = String(name.dropLast())
    }
    if name.hasPrefix("[") && name.hasSuffix("]") {
        name = String(name.dropFirst().dropLast())
    }
    return name.isEmpty ? nil : name
}

private func concreteTypeName(of declaration: some DeclGroupSyntax) -> String {
    if let classDecl = declaration.as(ClassDeclSyntax.self) {
        return classDecl.name.text
    }
    if let structDecl = declaration.as(StructDeclSyntax.self) {
        return structDecl.name.text
    }
    if let actorDecl = declaration.as(ActorDeclSyntax.self) {
        return actorDecl.name.text
    }
    return "Self"
}

private func persistedStoredProperties(of declaration: some DeclGroupSyntax) -> [PersistedProperty] {
    declaration.memberBlock.members.compactMap { member -> PersistedProperty? in
        guard let varDecl = member.decl.as(VariableDeclSyntax.self), isPersistedStoredProperty(varDecl) else {
            return nil
        }
        guard let binding = varDecl.bindings.first,
              let identifier = binding.pattern.as(IdentifierPatternSyntax.self) else {
            return nil
        }

        var relationshipArguments: String? = nil
        for attribute in varDecl.attributes {
            guard case .attribute(let attr) = attribute,
                  attr.attributeName.as(IdentifierTypeSyntax.self)?.name.text == "Relationship"
            else {
                continue
            }
            relationshipArguments = attr.arguments?.as(LabeledExprListSyntax.self)?.trimmedDescription ?? ""
        }

        return PersistedProperty(
            name: identifier.identifier.text,
            typeAnnotation: binding.typeAnnotation?.type.trimmedDescription,
            relationshipArguments: relationshipArguments,
            initializer: binding.initializer?.value.trimmedDescription
        )
    }
}

private func isPersistedStoredProperty(_ varDecl: VariableDeclSyntax) -> Bool {
    if varDecl.modifiers.contains(where: { $0.name.tokenKind == .keyword(.static) }) {
        return false
    }
    guard let binding = varDecl.bindings.first, binding.pattern.is(IdentifierPatternSyntax.self) else {
        return false
    }
    if let accessors = binding.accessorBlock {
        switch accessors.accessors {
        case .accessors(let list):
            let isStored = list.allSatisfy { accessor in
                accessor.accessorSpecifier.tokenKind == .keyword(.willSet)
                    || accessor.accessorSpecifier.tokenKind == .keyword(.didSet)
            }
            if !isStored { return false }
        case .getter:
            return false
        }
    }
    let hasTransient = varDecl.attributes.contains { attribute in
        guard case .attribute(let attr) = attribute,
              let name = attr.attributeName.as(IdentifierTypeSyntax.self)?.name.text else {
            return false
        }
        return name == "Transient"
    }
    return !hasTransient
}
