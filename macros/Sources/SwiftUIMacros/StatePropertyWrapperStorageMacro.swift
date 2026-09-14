import SwiftSyntax
import SwiftSyntaxMacros
import SwiftSyntaxBuilder
import MacroSupport

// _StatePropertyWrapperStorage (SwiftUICore)
// Roles: accessor
public struct StatePropertyWrapperStorageMacro: AccessorMacro {
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

        // Check if an initialValue argument was provided to the macro, e.g. @_StatePropertyWrapperStorage(initialValue: "val")
        var initialValueName: String? = nil
        if let arguments = node.arguments?.as(LabeledExprListSyntax.self) {
            for arg in arguments {
                if arg.label?.text == "initialValue",
                   let stringLiteral = arg.expression.as(StringLiteralExprSyntax.self)?.representedLiteralValue {
                    initialValueName = stringLiteral
                } else if arg.label == nil,
                          let stringLiteral = arg.expression.as(StringLiteralExprSyntax.self)?.representedLiteralValue {
                    initialValueName = stringLiteral
                }
            }
        }

        let varName = identifier.text
        let accessors: [AccessorDeclSyntax]

        if let initName = initialValueName {
            accessors = [
                """
                @storageRestrictions(initializes: _\(raw: varName))
                init(initialValue) {
                    _\(raw: varName) = SwiftUICore.State(initialValue: \(raw: initName))
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
                """
            ]
        } else {
            accessors = [
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
                """
            ]
        }
        return accessors
    }
}

