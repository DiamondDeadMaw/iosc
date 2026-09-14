// End to end: @Model (PersistentModelMacro) attaches @_PersistedProperty
// (PersistedPropertyMacro), which must expand in the same pass. Type resolution of
// SwiftData types is not the point here; we only check the macro layer expands with
// no not-implemented diagnostics and no macro crashes.
@attached(member, names: named(_$backingData), named(persistentBackingData), named(schemaMetadata), named(`init`), named(_$observationRegistrar), named(_SwiftDataNoType), named(access), named(withMutation))
@attached(memberAttribute)
@attached(extension, conformances: Observable, PersistentModel, Sendable)
macro Model() = #externalMacro(module: "SwiftDataMacros", type: "PersistentModelMacro")

@attached(accessor, names: named(`init`), named(get), named(set))
@attached(peer, names: prefixed(`_`))
macro _PersistedProperty() = #externalMacro(module: "SwiftDataMacros", type: "PersistedPropertyMacro")

@attached(peer)
macro Transient() = #externalMacro(module: "SwiftDataMacros", type: "TransientPropertyMacro")

@Model
class Item {
    var name: String = ""
    var count: Int = 0
    @Transient var cache: String = ""
}
