import SwiftSyntax
import SwiftSyntaxMacros
import MacroSupport

// #Index (SwiftData)
// Roles: declaration
// The macro declaration carries no names: clause, so it introduces zero new
// declarations. Its index list is consumed by @Model's own source level
// introspection of the class body, not by anything this expansion produces.
public struct IndexMacro: DeclarationMacro {
    public static func expansion(
        of node: some FreestandingMacroExpansionSyntax,
        in context: some MacroExpansionContext
    ) throws -> [DeclSyntax] {
        []
    }
}
