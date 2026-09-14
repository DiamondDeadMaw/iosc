import SwiftSyntax
import SwiftSyntaxMacros
import MacroSupport

// #Preview<T> (Previews)
// Roles: declaration
// #Preview for a SwiftUI View driven by an `arguments:` array, one preview per element.
public struct SwiftUIViewGroup_1: DeclarationMacro {
    public static func expansion(
        of node: some FreestandingMacroExpansionSyntax,
        in context: some MacroExpansionContext
    ) throws -> [DeclSyntax] {
        [previewRegistryDecl(of: node, in: context)]
    }
}
