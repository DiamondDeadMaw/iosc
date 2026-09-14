import SwiftSyntax
import SwiftSyntaxMacros
import MacroSupport

// @Guide (FoundationModels)
// Roles: peer
// Declared @attached(peer) with no names clause, so it may not introduce any
// new declaration. It is a marker consumed by GenerableMacro when it reads the
// property's attribute list to build the generation schema.
public struct GuideMacro: PeerMacro {
    public static func expansion(
        of node: AttributeSyntax,
        providingPeersOf declaration: some DeclSyntaxProtocol,
        in context: some MacroExpansionContext
    ) throws -> [DeclSyntax] {
        []
    }
}
