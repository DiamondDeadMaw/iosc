import SwiftSyntax
import SwiftSyntaxMacros
import MacroSupport

// @ReportableMetadataIgnored (StateReporting)
// Roles: peer
// Marker only. @ReportableMetadata reads this attribute directly off the
// property's attribute list to exclude it from metadataDictionary.
public struct ReportableMetadataIgnored: PeerMacro {
    public static func expansion(
        of node: AttributeSyntax,
        providingPeersOf declaration: some DeclSyntaxProtocol,
        in context: some MacroExpansionContext
    ) throws -> [DeclSyntax] {
        []
    }
}
