import SwiftSyntax
import SwiftSyntaxMacros
import SwiftSyntaxBuilder
import MacroSupport

// @State (SwiftUICore)
// Roles: accessor, peer
public struct StateMacro: AccessorMacro, PeerMacro {
    public static func expansion(
        of node: AttributeSyntax,
        providingAccessorsOf declaration: some DeclSyntaxProtocol,
        in context: some MacroExpansionContext
    ) throws -> [AccessorDeclSyntax] {
        guard let varDecl = declaration.as(VariableDeclSyntax.self),
              let binding = varDecl.bindings.first,
              let identifier = binding.pattern.as(IdentifierPatternSyntax.self)?.identifier.trimmed
        else {
            return []
        }

        // The accessor set references the _ peer. When the peer expansion will
        // refuse for a missing type, emitting these would bury that one message
        // under several not in scope errors.
        guard resolvedValueType(of: binding) != nil else {
            return []
        }

        let varName = identifier.text
        return [
            """
            @storageRestrictions(initializes: _\(raw: varName))
            init(initialValue) {
                _\(raw: varName) = SwiftUICore.State(initialValue: initialValue)
            }
            """,
            """
            get {
                _\(raw: varName).wrappedValue
            }
            """,
            """
            nonmutating set {
                _\(raw: varName).wrappedValue = newValue
            }
            """,
        ]
    }

    public static func expansion(
        of node: AttributeSyntax,
        providingPeersOf declaration: some DeclSyntaxProtocol,
        in context: some MacroExpansionContext
    ) throws -> [DeclSyntax] {
        guard let varDecl = declaration.as(VariableDeclSyntax.self),
              let binding = varDecl.bindings.first,
              let identifier = binding.pattern.as(IdentifierPatternSyntax.self)?.identifier.trimmed
        else {
            return []
        }

        let varName = identifier.text

        guard let valueType = resolvedValueType(of: binding) else {
            try failExpansion(
                id: "StateMacro.inferredType",
                reason: "@State could not infer the type of '\(varName)'. "
                    + "Add an explicit type: '@State var \(varName): T = ...'.",
                node: node,
                context: context
            )
        }

        let initializerClause: String
        if binding.typeAnnotation != nil, let initExpr = binding.initializer?.value.trimmedDescription {
            initializerClause = " = SwiftUICore.State(wrappedValue: \(initExpr))"
        } else {
            initializerClause = ""
        }

        let backingStorage: DeclSyntax =
            "var _\(raw: varName): SwiftUICore.State<\(raw: valueType)>\(raw: initializerClause)"
        let doubleUnderscoreStorage: DeclSyntax = """
        var __\(raw: varName): SwiftUICore.State<\(raw: valueType)> {
            _\(raw: varName)
        }
        """
        let projectedBinding: DeclSyntax = """
        var $\(raw: varName): SwiftUICore.Binding<\(raw: valueType)> {
            _\(raw: varName).projectedValue
        }
        """

        return [backingStorage, doubleUnderscoreStorage, projectedBinding]
    }

    // The explicit annotation wins. Otherwise recover the type from the
    // initializer for the cases the compiler could resolve on its own.
    private static func resolvedValueType(of binding: PatternBindingSyntax) -> String? {
        if let annotated = binding.typeAnnotation?.type.trimmedDescription {
            return annotated
        }
        if let initExpr = binding.initializer?.value {
            return inferredType(from: initExpr)
        }
        return nil
    }
}
