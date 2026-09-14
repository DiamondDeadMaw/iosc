import SwiftSyntax
import SwiftSyntaxMacros
import MacroSupport

// _PersistedProperty (SwiftData)
// Roles: accessor, peer
// Attached by @Model to every stored property. Routes get/set through the
// model's persistentBackingData via getValue(forKey:)/setValue(forKey:to:),
// which SwiftData's PersistentModel extension provides by keypath.
// property becomes computed over backing data, no peer storage
public struct PersistedPropertyMacro: AccessorMacro, PeerMacro {
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
        var accessors: [AccessorDeclSyntax] = []

        // without this a property initializer is dropped
        // only emit where there is one. otherwise init(backingData:) has to assign every property
        if binding.initializer != nil {
            accessors.append("""
                @storageRestrictions(accesses: _$backingData)
                init(initialValue) {
                    _$backingData.setValue(forKey: \\.\(raw: varName), to: initialValue)
                }
                """)
        }

        accessors += [
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
        return []
    }
}
