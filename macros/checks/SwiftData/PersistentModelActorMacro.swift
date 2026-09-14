protocol Actor {}

protocol ModelExecutor {}

final class ModelContainer {}

final class ModelContext {
    init(_ container: ModelContainer) {}
}

final class DefaultSerialModelExecutor: ModelExecutor {
    init(modelContext: ModelContext) {}
}

protocol ModelActor: Actor {
    nonisolated var modelExecutor: any ModelExecutor { get }
    nonisolated var modelContainer: ModelContainer { get }
}

@attached(member, names: named(modelExecutor), named(modelContainer), named(init))
@attached(extension, conformances: ModelActor)
public macro ModelActor() = #externalMacro(module: "SwiftDataMacros", type: "PersistentModelActorMacro")

@ModelActor
actor BackgroundActor {
}
