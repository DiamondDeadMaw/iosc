import SwiftSyntax
import SwiftSyntaxMacros
import MacroSupport

// __EntryDefaultValue (SwiftUICore)
// Roles: accessor
// STUB. Replace each body with the real expansion, verified against the
// post expansion API in the SDK .swiftinterface for SwiftUICore.
public struct EntryDefaultValueMacro: AccessorMacro {
    public static func expansion(
        of node: AttributeSyntax,
        providingAccessorsOf declaration: some DeclSyntaxProtocol,
        in context: some MacroExpansionContext
    ) throws -> [AccessorDeclSyntax] {
        try failNotImplemented(pluginType: "EntryDefaultValueMacro", displayName: "__EntryDefaultValue", framework: "SwiftUICore", node: node, context: context)
    }
}
