@freestanding(expression)
public macro _SwiftUIEntryTypeCheck<T>(_ type: T.Type, macroName: String, propertyName: String) -> Void = #externalMacro(module: "SwiftUIMacros", type: "EntryTypeCheckClassMacro") where T: AnyObject

class MyClass {}

func test() {
    #_SwiftUIEntryTypeCheck(MyClass.self, macroName: "Entry", propertyName: "item")
}
