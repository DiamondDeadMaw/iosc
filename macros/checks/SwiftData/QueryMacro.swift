public enum _SwiftData_SwiftUI {
    public struct Query<Element, Result> {
        public var wrappedValue: Result { fatalError() }
        public init(filter: Any? = nil, sort descriptors: [Any] = [], transaction: Any? = nil) where Result == [Element] {}
    }
}

@attached(accessor)
@attached(peer, names: prefixed(`_`))
public macro Query() = #externalMacro(module: "SwiftDataMacros", type: "QueryMacro")

protocol PersistentModel {}
final class Item: PersistentModel {}

struct ItemList {
    @Query var items: [Item]
}
