import SwiftSyntax
import SwiftSyntaxMacros
import MacroSupport

// #Preview (Previews)
// Roles: declaration
// #Preview for a UIView or UIViewController body closure.
public struct KitViewMacro: DeclarationMacro {
    public static func expansion(
        of node: some FreestandingMacroExpansionSyntax,
        in context: some MacroExpansionContext
    ) throws -> [DeclSyntax] {
        [previewRegistryDecl(of: node, in: context)]
    }
}
