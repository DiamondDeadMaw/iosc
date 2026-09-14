public protocol Widget {}
public protocol TimelineProvider {}
public enum WidgetFamily { case systemSmall }

@freestanding(declaration)
public macro Preview<WidgetType, ProviderType>(
    _ name: String? = nil,
    as family: WidgetFamily,
    widget: () -> WidgetType,
    timelineProvider: () -> ProviderType
) = #externalMacro(module: "PreviewsMacros", type: "Common")

struct DemoWidget: Widget {}
struct DemoProvider: TimelineProvider {}

#Preview("Widget Demo", as: .systemSmall) {
    DemoWidget()
} timelineProvider: {
    DemoProvider()
}
