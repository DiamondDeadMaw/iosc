struct LocalizedStringResource {
    init(stringLiteral value: String) {}
}
extension LocalizedStringResource: ExpressibleByStringLiteral {}

final class EntityProperty<Value> {
    var wrappedValue: Value!
    init() {}
    init(title: LocalizedStringResource) {}
}

typealias Property = EntityProperty

@attached(peer, names: prefixed(`$`), prefixed(`_`))
@attached(accessor, names: named(get), named(set))
macro ComputedProperty() = #externalMacro(module: "AppIntentsMacros", type: "EntityPropertyMacro")

@attached(peer, names: prefixed(`$`), prefixed(`_`))
@attached(accessor, names: named(get), named(set))
macro DeferredProperty(title: LocalizedStringResource) = #externalMacro(module: "AppIntentsMacros", type: "EntityPropertyMacro")

struct Recipe {
    @ComputedProperty
    var isFavorite: Bool

    @DeferredProperty(title: "Rating")
    var rating: Int
}
