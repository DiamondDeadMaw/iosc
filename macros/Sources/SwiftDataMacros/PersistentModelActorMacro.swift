import SwiftSyntax
import SwiftSyntaxBuilder
import SwiftSyntaxMacros
import MacroSupport

// @ModelActor (SwiftData)
// Roles: member, extension
public struct PersistentModelActorMacro: MemberMacro, ExtensionMacro {
    public static func expansion(
        of node: AttributeSyntax,
        providingMembersOf declaration: some DeclGroupSyntax,
        conformingTo protocols: [TypeSyntax],
        in context: some MacroExpansionContext
    ) throws -> [DeclSyntax] {
        [
            """
            nonisolated let modelExecutor: any ModelExecutor
            """,
            """
            nonisolated let modelContainer: ModelContainer
            """,
            """
            init(modelContainer: ModelContainer) {
                let modelContext = ModelContext(modelContainer)
                self.modelExecutor = DefaultSerialModelExecutor(modelContext: modelContext)
                self.modelContainer = modelContainer
            }
            """,
        ]
    }

    public static func expansion(
        of node: AttributeSyntax,
        attachedTo declaration: some DeclGroupSyntax,
        providingExtensionsOf type: some TypeSyntaxProtocol,
        conformingTo protocols: [TypeSyntax],
        in context: some MacroExpansionContext
    ) throws -> [ExtensionDeclSyntax] {
        let ext: DeclSyntax = """
            extension \(type.trimmed): ModelActor {
            }
            """
        return [ext.cast(ExtensionDeclSyntax.self)]
    }
}
