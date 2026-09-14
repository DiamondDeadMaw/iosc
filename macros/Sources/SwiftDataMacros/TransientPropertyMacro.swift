import SwiftSyntax
import SwiftSyntaxMacros
import MacroSupport

// @Transient (SwiftData)
// Roles: peer
// Marker only. @Model's memberAttribute pass checks for this attribute on a
// property to exclude it from persisted storage and schema metadata; it
// introduces no code of its own.
public struct TransientPropertyMacro: PeerMacro {
    public static func expansion(
        of node: AttributeSyntax,
        providingPeersOf declaration: some DeclSyntaxProtocol,
        in context: some MacroExpansionContext
    ) throws -> [DeclSyntax] {
        []
    }
}
