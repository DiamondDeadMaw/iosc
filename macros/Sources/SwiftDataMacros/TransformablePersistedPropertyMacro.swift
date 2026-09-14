import SwiftSyntax
import SwiftSyntaxMacros
import MacroSupport

// _TransformablePersistedProperty (SwiftData)
// Roles: accessor, peer
// Same shape as _PersistedProperty. The transform (custom value transformer)
// is selected by overload resolution on Value's Decodable/Encodable
// conformance inside getValue/setValue, not by anything the macro emits.
// The init accessor only assigns the peer backing storage: an init accessor
// may only touch properties listed in its initializes/accesses clause.
public struct TransformablePersistedPropertyMacro: AccessorMacro, PeerMacro {
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
            @storageRestrictions(initializes: _\(raw: varName))
            init(initialValue) {
                _\(raw: varName) = initialValue
            }
            """,
            """
            get {
                self.getValue(forKey: \\.\(raw: varName))
            }
            """,
            """
            set {
                self.setValue(forKey: \\.\(raw: varName), to: newValue)
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
              let identifier = binding.pattern.as(IdentifierPatternSyntax.self)?.identifier.trimmed
        else {
            return []
        }

        let varName = identifier.text
        let typeClause = binding.typeAnnotation?.type.trimmedDescription ?? "Any"

        let backingStorage: DeclSyntax = "private var _\(raw: varName): \(raw: typeClause)"
        return [backingStorage]
    }
}
