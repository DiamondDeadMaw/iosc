import SwiftSyntax
import SwiftSyntaxMacros
import MacroSupport

// #Rule (TipKit)
// Roles: expression
// Left fail-loud. The macro's body parameter is a plain
// (repeat (each Input).Value) -> Bool closure, but every Tips.Rule
// initializer wants a Foundation predicate expression tree (a
// StandardPredicateExpression built over a Variable), the same
// transform #Predicate performs on a closure body. Reproducing that
// closure-to-predicate-expression rewrite for arbitrary bodies is not a
// syntax-only transform; a wrong or partial expansion here would silently
// miscompile call sites, so this stays fail-loud rather than emitting a
// guess.
public struct RuleMacro: ExpressionMacro {
    public static func expansion(
        of node: some FreestandingMacroExpansionSyntax,
        in context: some MacroExpansionContext
    ) throws -> ExprSyntax {
        try failNotImplemented(pluginType: "RuleMacro", displayName: "#Rule", framework: "TipKit", node: node, context: context)
    }
}
