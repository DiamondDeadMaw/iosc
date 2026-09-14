import SwiftSyntax
import SwiftSyntaxMacros
import MacroSupport

// #Preview<T> (Previews)
// Roles: declaration
// #Preview for a UIView or UIViewController body closure driven by an `arguments:` array.
public struct PreviewCommonGroup: DeclarationMacro {
    public static func expansion(
        of node: some FreestandingMacroExpansionSyntax,
        in context: some MacroExpansionContext
    ) throws -> [DeclSyntax] {
        [previewRegistryDecl(of: node, in: context)]
    }
}
