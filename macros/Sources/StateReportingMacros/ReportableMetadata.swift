import SwiftSyntax
import SwiftSyntaxMacros
import SwiftSyntaxBuilder
import MacroSupport

// @ReportableMetadata (StateReporting)
// Roles: member, extension
// Emits metadataDictionary mapping each stored property to a
// ReportableMetadataValue, keyed by @ReportableMetadataKey when present,
// skipping properties marked @ReportableMetadataIgnored. The extension
// adds the ReportableMetadata conformance the member satisfies.
public struct ReportableMetadata: MemberMacro, ExtensionMacro {
    private struct ReportableProperty {
        let name: String
        let key: String
    }

    private static func attributeName(_ attribute: AttributeSyntax) -> String {
        attribute.attributeName.trimmedDescription
    }

    private static func customKey(_ attribute: AttributeSyntax) -> String? {
        guard let arguments = attribute.arguments?.as(LabeledExprListSyntax.self),
              let firstArg = arguments.first,
              let literal = firstArg.expression.as(StringLiteralExprSyntax.self)?.representedLiteralValue
        else {
            return nil
        }
        return literal
    }

    private static func isStored(_ binding: PatternBindingSyntax) -> Bool {
        guard let accessorBlock = binding.accessorBlock else { return true }
        switch accessorBlock.accessors {
        case .accessors(let accessors):
            return accessors.allSatisfy { accessor in
                accessor.accessorSpecifier.tokenKind == .keyword(.willSet)
                    || accessor.accessorSpecifier.tokenKind == .keyword(.didSet)
            }
        case .getter:
            return false
        }
    }

    private static func reportableProperties(of declaration: some DeclGroupSyntax) -> [ReportableProperty] {
        var properties: [ReportableProperty] = []
        for member in declaration.memberBlock.members {
            guard let varDecl = member.decl.as(VariableDeclSyntax.self) else { continue }
            if varDecl.modifiers.contains(where: { $0.name.tokenKind == .keyword(.static) }) { continue }
            let attributes = varDecl.attributes.compactMap { $0.as(AttributeSyntax.self) }
            if attributes.contains(where: { attributeName($0) == "ReportableMetadataIgnored" }) { continue }
            let keyAttribute = attributes.first { attributeName($0) == "ReportableMetadataKey" }

            for binding in varDecl.bindings {
                guard isStored(binding),
                      let identifier = binding.pattern.as(IdentifierPatternSyntax.self)?.identifier.trimmed
                else { continue }
                let name = identifier.text
                let key = keyAttribute.flatMap(customKey) ?? name
                properties.append(ReportableProperty(name: name, key: key))
            }
        }
        return properties
    }

    public static func expansion(
        of node: AttributeSyntax,
        providingMembersOf declaration: some DeclGroupSyntax,
        conformingTo protocols: [TypeSyntax],
        in context: some MacroExpansionContext
    ) throws -> [DeclSyntax] {
        let properties = reportableProperties(of: declaration)

        let entries = properties
            .map { "\"\($0.key)\": StateReporting.ReportableMetadataValue(\($0.name))" }
            .joined(separator: ",\n            ")

        let body = properties.isEmpty ? "[:]" : "[\n            \(entries)\n        ]"

        let member: DeclSyntax = """
        public var metadataDictionary: [Swift.String: StateReporting.ReportableMetadataValue] {
            \(raw: body)
        }
        """
        return [member]
    }

    public static func expansion(
        of node: AttributeSyntax,
        attachedTo declaration: some DeclGroupSyntax,
        providingExtensionsOf type: some TypeSyntaxProtocol,
        conformingTo protocols: [TypeSyntax],
        in context: some MacroExpansionContext
    ) throws -> [ExtensionDeclSyntax] {
        let ext: DeclSyntax = "extension \(type.trimmed): StateReporting.ReportableMetadata {}"
        guard let extensionDecl = ext.as(ExtensionDeclSyntax.self) else { return [] }
        return [extensionDecl]
    }
}
