protocol Observable {}

protocol BackingData<Model> {
    associatedtype Model
    init(for modelType: Model.Type)
}

protocol PersistentModel: AnyObject, Observable {
    init(backingData: any BackingData<Self>)
    var persistentBackingData: any BackingData<Self> { get set }
    static var schemaMetadata: [Schema.PropertyMetadata] { get }
}

enum Schema {
    struct PropertyMetadata {
        init(name: String, keypath: AnyKeyPath) {}
    }
}

final class ObservationRegistrar {
    init() {}
    func access<Subject, Member>(_ subject: Subject, keyPath: KeyPath<Subject, Member>) {}
    func withMutation<Subject, Member, MutationResult>(
        of subject: Subject,
        keyPath: KeyPath<Subject, Member>,
        _ mutation: () throws -> MutationResult
    ) rethrows -> MutationResult {
        try mutation()
    }
}

@attached(accessor, names: named(init), named(get), named(set))
@attached(peer, names: prefixed(`_`))
public macro _PersistedProperty() = #externalMacro(module: "SwiftDataMacros", type: "PersistedPropertyMacro")

@attached(peer)
public macro Transient() = #externalMacro(module: "SwiftDataMacros", type: "TransientPropertyMacro")

@attached(member, conformances: Observable, PersistentModel, Sendable, names: named(_$backingData), named(persistentBackingData), named(schemaMetadata), named(init), named(_$observationRegistrar), named(_SwiftDataNoType), named(access), named(withMutation))
@attached(memberAttribute)
@attached(extension, conformances: Observable, PersistentModel, Sendable)
public macro Model() = #externalMacro(module: "SwiftDataMacros", type: "PersistentModelMacro")

@Model
final class Item {
    var name: String = ""
    var count: Int = 0
    @Transient var cache: String = ""
}
