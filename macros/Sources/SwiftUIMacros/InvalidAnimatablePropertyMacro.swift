import SwiftSyntax
import SwiftSyntaxMacros
import MacroSupport

// _SwiftUIAnimatableProperty (SwiftUICore)
// Roles: expression
// STUB. Replace each body with the real expansion, verified against the
// post expansion API in the SDK .swiftinterface for SwiftUICore.
public struct InvalidAnimatablePropertyMacro: ExpressionMacro {
    public static func expansion(
        of node: some FreestandingMacroExpansionSyntax,
        in context: some MacroExpansionContext
    ) throws -> ExprSyntax {
        return "EmptyAnimatableData.self"
    }
}
