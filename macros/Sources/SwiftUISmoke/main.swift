// Empty consumer. Its only job is to depend on SwiftUIMacros so a plain build links
// the plugin tool. It uses no macro, so it builds while macro bodies are stubs.
print("swiftui macros plugin smoke")
