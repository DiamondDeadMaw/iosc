import SwiftSyntax
import SwiftSyntaxMacros
import MacroSupport

// @Attribute (SwiftData)
// Roles: peer
// Marker only. @Model's memberAttribute and member passes read the arguments
// off this attribute directly from the property's attribute list; it introduces
// no code of its own.
public struct AttributePropertyMacro: PeerMacro {
    public static func expansion(
        of node: AttributeSyntax,
        providingPeersOf declaration: some DeclSyntaxProtocol,
        in context: some MacroExpansionContext
    ) throws -> [DeclSyntax] {
        []
    }
}
