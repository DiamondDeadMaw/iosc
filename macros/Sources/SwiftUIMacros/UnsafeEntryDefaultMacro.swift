import SwiftSyntax
import SwiftSyntaxMacros
import MacroSupport

// _EntryDefaultValue (SwiftUICore)
// Roles: declaration
// STUB. Replace each body with the real expansion, verified against the
// post expansion API in the SDK .swiftinterface for SwiftUICore.
public struct UnsafeEntryDefaultMacro: DeclarationMacro {
    public static func expansion(
        of node: some FreestandingMacroExpansionSyntax,
        in context: some MacroExpansionContext
    ) throws -> [DeclSyntax] {
        guard let argument = node.arguments.first?.expression else {
            return []
        }
        return [
            "nonisolated(unsafe) static var defaultValue: Value { \(argument) }"
        ]
    }
}
