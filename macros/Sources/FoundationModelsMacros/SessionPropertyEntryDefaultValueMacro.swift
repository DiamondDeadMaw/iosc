import SwiftSyntax
import SwiftSyntaxMacros
import MacroSupport

// @__SessionPropertyEntryDefaultValue (FoundationModels)
// Roles: accessor
// STUB. Replace each body with the real expansion, verified against the
// post expansion API in the SDK .swiftinterface for FoundationModels.
public struct SessionPropertyEntryDefaultValueMacro: AccessorMacro {
    public static func expansion(
        of node: AttributeSyntax,
        providingAccessorsOf declaration: some DeclSyntaxProtocol,
        in context: some MacroExpansionContext
    ) throws -> [AccessorDeclSyntax] {
        try failNotImplemented(pluginType: "SessionPropertyEntryDefaultValueMacro", displayName: "@__SessionPropertyEntryDefaultValue", framework: "FoundationModels", node: node, context: context)
    }
}
