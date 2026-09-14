import SwiftSyntax
import SwiftSyntaxMacros
import MacroSupport

// @SessionPropertyEntry (FoundationModels)
// Roles: accessor, peer
// This is the @Entry pattern applied to FoundationModels.SessionPropertyValues.
// The interface exposes an @inline(__always) subscript<K>(key: K.Type) -> K.Value
// where K : SessionPropertyKey on SessionPropertyValues, mirroring SwiftUI's
// EnvironmentValues subscript keyed by EnvironmentKey.
public struct SessionPropertyEntryMacro: AccessorMacro, PeerMacro {
    public static func expansion(
        of node: AttributeSyntax,
        providingAccessorsOf declaration: some DeclSyntaxProtocol,
        in context: some MacroExpansionContext
    ) throws -> [AccessorDeclSyntax] {
        guard let varDecl = declaration.as(VariableDeclSyntax.self),
              let binding = varDecl.bindings.first,
              let identifier = binding.pattern.as(IdentifierPatternSyntax.self)?.identifier.trimmed
        else {
            return []
        }

        let keyTypeName = "__Key_\(identifier)"
        return [
            AccessorDeclSyntax(
                """
                get {
                    self[\(raw: keyTypeName).self]
                }
                """
            ),
            AccessorDeclSyntax(
                """
                set {
                    self[\(raw: keyTypeName).self] = newValue
                }
                """
            )
        ]
    }

    public static func expansion(
        of node: AttributeSyntax,
        providingPeersOf declaration: some DeclSyntaxProtocol,
        in context: some MacroExpansionContext
    ) throws -> [DeclSyntax] {
        guard let varDecl = declaration.as(VariableDeclSyntax.self),
              let binding = varDecl.bindings.first,
              let identifier = binding.pattern.as(IdentifierPatternSyntax.self)?.identifier.trimmed
        else {
            return []
        }

        let keyTypeName = "__Key_\(identifier)"
        let typeAnnotation = binding.typeAnnotation?.type.trimmedDescription
        let initializer = binding.initializer?.value

        let valueType: String
        if let typeAnnotation {
            valueType = typeAnnotation
        } else if initializer != nil {
            valueType = "defaultValue"
        } else {
            valueType = "Void"
        }

        let defaultValDecl: String
        if let initializer {
            defaultValDecl = "static var defaultValue: Value { \(initializer.trimmedDescription) }"
        } else if let typeAnnotation, typeAnnotation.hasSuffix("?") || typeAnnotation.starts(with: "Optional<") {
            defaultValDecl = "static var defaultValue: Value { nil }"
        } else {
            defaultValDecl = "static var defaultValue: Value"
        }

        let structDecl: DeclSyntax = """
        private struct \(raw: keyTypeName): SessionPropertyKey {
            typealias Value = \(raw: valueType)
            \(raw: defaultValDecl)
        }
        """

        return [structDecl]
    }
}
