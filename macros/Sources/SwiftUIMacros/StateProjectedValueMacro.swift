import SwiftSyntax
import SwiftSyntaxMacros
import SwiftSyntaxBuilder
import MacroSupport

// _StateProjectedValue (SwiftUICore)
// Roles: accessor
public struct StateProjectedValueMacro: AccessorMacro {
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

        var baseName = identifier.text
        if baseName.hasPrefix("$") {
            baseName = String(baseName.dropFirst())
        }

        let accessors: [AccessorDeclSyntax] = [
            """
            get {
                _\(raw: baseName).projectedValue
            }
            """
        ]
        return accessors
    }
}

