import SwiftSyntax
import SwiftSyntaxMacros
import MacroSupport

// @ComputedProperty/@DeferredProperty (AppIntents)
// Roles: peer, accessor
// Both attributes route through this single macro type. The interface shows
// the same peer/accessor shape for both (prefixed `_`/`$` peers backed by
// AppEntity.Property, i.e. EntityProperty<Value>, plus get/set accessors
// routed through the `_` backing storage's wrappedValue). Offline expansion
// has no way to observe async loading behavior differences between
// ComputedProperty and DeferredProperty, so both share this one path.
public struct EntityPropertyMacro: PeerMacro, AccessorMacro {
    public static func expansion(
        of node: AttributeSyntax,
        providingPeersOf declaration: some DeclSyntaxProtocol,
        in context: some MacroExpansionContext
    ) throws -> [DeclSyntax] {
        guard let varDecl = declaration.as(VariableDeclSyntax.self),
              let binding = varDecl.bindings.first,
              let identifier = binding.pattern.as(IdentifierPatternSyntax.self)?.identifier.trimmed,
              let valueType = binding.typeAnnotation?.type.trimmedDescription
        else {
            return []
        }

        let backingName = "_\(identifier)"
        let projectedName = "$\(identifier)"

        let initializerCall: String
        if let title = macroArgument(named: "title", in: node) {
            initializerCall = "Property<\(valueType)>(title: \(title))"
        } else {
            initializerCall = "Property<\(valueType)>()"
        }

        let backing: DeclSyntax = """
            private var \(raw: backingName): Property<\(raw: valueType)> = \(raw: initializerCall)
            """

        let projected: DeclSyntax = """
            var \(raw: projectedName): Property<\(raw: valueType)> {
                \(raw: backingName)
            }
            """

        return [backing, projected]
    }

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

        let backingName = "_\(identifier)"

        return [
            AccessorDeclSyntax(
                """
                get {
                    \(raw: backingName).wrappedValue
                }
                """
            ),
            AccessorDeclSyntax(
                """
                set {
                    \(raw: backingName).wrappedValue = newValue
                }
                """
            )
        ]
    }
}

private func macroArgument(named label: String, in node: AttributeSyntax) -> String? {
    guard case .argumentList(let list) = node.arguments else { return nil }
    for argument in list where argument.label?.text == label {
        return argument.expression.trimmedDescription
    }
    return nil
}
