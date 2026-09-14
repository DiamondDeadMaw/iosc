import SwiftSyntax
import SwiftSyntaxMacros
import MacroSupport

// @ReportableMetadataKey (StateReporting)
// Roles: peer
// Marker only. @ReportableMetadata reads the key string argument directly
// off the property's attribute list to rename its metadataDictionary entry.
public struct ReportableMetadataKey: PeerMacro {
    public static func expansion(
        of node: AttributeSyntax,
        providingPeersOf declaration: some DeclSyntaxProtocol,
        in context: some MacroExpansionContext
    ) throws -> [DeclSyntax] {
        []
    }
}
