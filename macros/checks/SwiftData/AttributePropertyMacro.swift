public enum SchemaAttribute {
    public struct Option {}
}

@attached(peer)
public macro Attribute(_ options: SchemaAttribute.Option..., originalName: String? = nil, hashModifier: String? = nil) = #externalMacro(module: "SwiftDataMacros", type: "AttributePropertyMacro")

struct Item {
    @Attribute(originalName: "itemName") var name: String
}
