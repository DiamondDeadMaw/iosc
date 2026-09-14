// Empty consumer. Its only job is to depend on SwiftDataMacros so a plain build links
// the plugin tool. It uses no macro, so it builds while macro bodies are stubs.
print("swiftdata macros plugin smoke")
