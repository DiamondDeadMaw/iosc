import SwiftSyntax
import SwiftSyntaxMacros
import SwiftSyntaxBuilder
import MacroSupport

// @Parameter (TipKit)
// Roles: accessor, peer
// Peer emits $<name>, a Tips.Parameter backing wrapper built from the
// enclosing type, the property name, its initial value, and any passed
// ParameterOption values; the accessor routes get/set through wrappedValue.
public struct ParameterMacro: AccessorMacro, PeerMacro {
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
                $\(raw: varName).wrappedValue
            }
            """,
            """
            set {
                $\(raw: varName).wrappedValue = newValue
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
              let initialValue = binding.initializer?.value
        else {
            return []
        }

        let varName = identifier.text
        let typeAnnotation = binding.typeAnnotation?.type.trimmedDescription
        let typeClause = typeAnnotation.map { ": TipKit.Tips.Parameter<\($0)>" } ?? ""

        let options: [String]
        if let arguments = node.arguments?.as(LabeledExprListSyntax.self) {
            options = arguments.map { $0.expression.trimmedDescription }
        } else {
            options = []
        }
        let optionsClause = options.isEmpty ? "" : ", " + options.joined(separator: ", ")

        let isStatic = varDecl.modifiers.contains { $0.name.tokenKind == .keyword(.static) }
        let staticPrefix = isStatic ? "static " : ""

        let backing: DeclSyntax = """
        \(raw: staticPrefix)var $\(raw: varName)\(raw: typeClause) = TipKit.Tips.Parameter(Self.self, "\(raw: varName)", \(raw: initialValue.trimmedDescription)\(raw: optionsClause))
        """
        return [backing]
    }
}
