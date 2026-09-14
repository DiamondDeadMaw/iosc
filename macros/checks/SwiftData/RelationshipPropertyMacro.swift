public enum SchemaRelationship {
    public struct Option {}
    public enum DeleteRule { case nullify, cascade, deny, noAction }
}

@attached(peer)
public macro Relationship(_ options: SchemaRelationship.Option..., deleteRule: SchemaRelationship.DeleteRule = .nullify, minimumModelCount: Int? = 0, maximumModelCount: Int? = 0, originalName: String? = nil, inverse: AnyKeyPath? = nil, hashModifier: String? = nil) = #externalMacro(module: "SwiftDataMacros", type: "RelationshipPropertyMacro")

class Item {
    @Relationship(deleteRule: .cascade) var children: [Item] = []
}
