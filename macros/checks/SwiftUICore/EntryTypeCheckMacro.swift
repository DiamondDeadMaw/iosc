@freestanding(expression)
public macro _SwiftUIEntryTypeCheck<T>(_ type: T.Type, macroName: String, propertyName: String) -> Void = #externalMacro(module: "SwiftUIMacros", type: "EntryTypeCheckMacro")

func test() {
    #_SwiftUIEntryTypeCheck(Int.self, macroName: "Entry", propertyName: "count")
}
