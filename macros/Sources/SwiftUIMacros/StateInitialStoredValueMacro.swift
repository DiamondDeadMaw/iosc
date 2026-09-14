import SwiftSyntax
import SwiftSyntaxMacros
import SwiftSyntaxBuilder
import MacroSupport

// _StateInitialStoredValue (SwiftUICore)
// Roles: accessor
public struct StateInitialStoredValueMacro: AccessorMacro {
    public static func expansion(
        of node: AttributeSyntax,
        providingAccessorsOf declaration: some DeclSyntaxProtocol,
        in context: some MacroExpansionContext
    ) throws -> [AccessorDeclSyntax] {
        guard let arguments = node.arguments?.as(LabeledExprListSyntax.self),
              let firstArg = arguments.first,
              let initialValueName = firstArg.expression.as(StringLiteralExprSyntax.self)?.representedLiteralValue
        else {
            return []
        }

        let accessors: [AccessorDeclSyntax] = [
            """
            get {
                _\(raw: initialValueName)
            }
            """
        ]
        return accessors
    }
}

