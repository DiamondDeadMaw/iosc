import SwiftSyntax
import SwiftSyntaxMacros
import SwiftSyntaxBuilder
import MacroSupport

// _SwiftUIState (SwiftUICore)
// Roles: declaration
public struct StateTypeMacro: DeclarationMacro {
    public static func expansion(
        of node: some FreestandingMacroExpansionSyntax,
        in context: some MacroExpansionContext
    ) throws -> [DeclSyntax] {
        let args = Array(node.arguments)
        guard args.count >= 2 else {
            return []
        }

        var typeExpr = args[0].expression.trimmedDescription
        if typeExpr.hasSuffix(".self") {
            typeExpr = String(typeExpr.dropLast(5))
        }

        guard let nameLiteral = args[1].expression.as(StringLiteralExprSyntax.self)?.representedLiteralValue else {
            return []
        }

        let decl: DeclSyntax = "var _\(raw: nameLiteral): SwiftUICore.State<\(raw: typeExpr)>"
        return [decl]
    }
}

