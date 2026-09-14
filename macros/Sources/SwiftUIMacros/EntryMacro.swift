import SwiftSyntax
import SwiftSyntaxMacros
import MacroSupport

// @Entry (SwiftUICore)
// Roles: accessor, peer
// STUB. Replace each body with the real expansion, verified against the
// post expansion API in the SDK .swiftinterface for SwiftUICore.
import SwiftSyntaxBuilder

public struct EntryMacro: AccessorMacro, PeerMacro {
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
        private struct \(raw: keyTypeName): EnvironmentKey {
            typealias Value = \(raw: valueType)
            \(raw: defaultValDecl)
        }
        """

        return [structDecl]
    }
}
