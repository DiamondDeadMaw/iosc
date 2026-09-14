import SwiftCompilerPlugin
import SwiftSyntaxMacros

@main
struct SwiftDataMacrosPlugin: CompilerPlugin {
    let providingMacros: [Macro.Type] = [
        PersistentModelMacro.self,
        PersistentModelActorMacro.self,
        AttributePropertyMacro.self,
        RelationshipPropertyMacro.self,
        TransientPropertyMacro.self,
        UniqueConstraintsMacro.self,
        IndexMacro.self,
        PersistedPropertyMacro.self,
        TransformablePersistedPropertyMacro.self,
        QueryMacro.self,
    ]
}
