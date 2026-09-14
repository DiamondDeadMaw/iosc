import SwiftSyntax
import SwiftSyntaxMacros
import MacroSupport

// @Query (SwiftData, declared in _SwiftData_SwiftUI)
// Roles: accessor, peer
// Peer builds a _SwiftData_SwiftUI.Query<Element, Result> backing from the
// attribute's own argument list (labels match Query's initializers 1:1).
// Accessor is get-only: Query.wrappedValue has no setter.
public struct QueryMacro: AccessorMacro, PeerMacro {
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

        let varName = identifier.text
        let accessors: [AccessorDeclSyntax] = [
            """
            get {
                _\(raw: varName).wrappedValue
            }
            """
        ]
        return accessors
    }

    public static func expansion(
        of node: AttributeSyntax,
        providingPeersOf declaration: some DeclSyntaxProtocol,
        in context: some MacroExpansionContext
    ) throws -> [DeclSyntax] {
        guard let varDecl = declaration.as(VariableDeclSyntax.self),
              let binding = varDecl.bindings.first,
              let identifier = binding.pattern.as(IdentifierPatternSyntax.self)?.identifier.trimmed,
              let declaredType = binding.typeAnnotation?.type
        else {
            return []
        }

        let varName = identifier.text
        let resultType = declaredType.trimmedDescription
        let elementType: String
        if let arrayType = declaredType.as(ArrayTypeSyntax.self) {
            elementType = arrayType.element.trimmedDescription
        } else {
            elementType = resultType
        }

        let forwardedArgs = node.arguments?.as(LabeledExprListSyntax.self)?.trimmedDescription ?? ""

        let backingStorage: DeclSyntax = """
        private var _\(raw: varName): _SwiftData_SwiftUI.Query<\(raw: elementType), \(raw: resultType)> = _SwiftData_SwiftUI.Query(\(raw: forwardedArgs))
        """
        return [backingStorage]
    }
}
