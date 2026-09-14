import SwiftSyntax
import SwiftSyntaxMacros
import MacroSupport

// @Previewable (Previews)
// Roles: peer
// Not offline expressible. @Previewable does not add sibling declarations, it tells
// the compiler to hoist the annotated local out of the #Preview body's view builder
// closure into external state so it can be read and written across preview refreshes.
// That rewrite happens inside the closure that contains the attribute, which a peer
// macro cannot reach. Left fail-loud rather than emitting a no-op peer that would
// silently drop the hoisting behavior real code depends on.
public struct Previewable: PeerMacro {
    public static func expansion(
        of node: AttributeSyntax,
        providingPeersOf declaration: some DeclSyntaxProtocol,
        in context: some MacroExpansionContext
    ) throws -> [DeclSyntax] {
        try failNotImplemented(pluginType: "Previewable", displayName: "@Previewable", framework: "Previews", node: node, context: context)
    }
}
