import SwiftSyntax
import SwiftSyntaxMacros
import MacroSupport

// _SwiftUIAnimatableDataProperty (SwiftUICore)
// Roles: declaration
// STUB. Replace each body with the real expansion, verified against the
// post expansion API in the SDK .swiftinterface for SwiftUICore.
public struct AnimatablePairDataPropertyMacro: DeclarationMacro {
    public static func expansion(
        of node: some FreestandingMacroExpansionSyntax,
        in context: some MacroExpansionContext
    ) throws -> [DeclSyntax] {
        try failNotImplemented(pluginType: "AnimatablePairDataPropertyMacro", displayName: "_SwiftUIAnimatableDataProperty", framework: "SwiftUICore", node: node, context: context)
    }
}
