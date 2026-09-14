import SwiftSyntax
import SwiftSyntaxMacros
import MacroSupport

// #Preview (Previews)
// Roles: declaration
// #Preview for a WidgetKit widget. Covers the widget/provider/timeline/relevance
// overload family; each forwards its own argument shape straight into Preview's
// matching initializer, so one expansion body serves all of them.
public struct Common: DeclarationMacro {
    public static func expansion(
        of node: some FreestandingMacroExpansionSyntax,
        in context: some MacroExpansionContext
    ) throws -> [DeclSyntax] {
        [previewRegistryDecl(of: node, in: context)]
    }
}
