enum StateReporting {
    struct ReportableMetadataValue {
        init(_ value: String) {}
        init(_ value: Int) {}
    }
    protocol ReportableMetadata {
        var metadataDictionary: [String: ReportableMetadataValue] { get }
    }
}

@attached(member, names: named(metadataDictionary))
@attached(extension, conformances: StateReporting.ReportableMetadata)
public macro ReportableMetadata() = #externalMacro(module: "StateReportingMacros", type: "ReportableMetadata")

@attached(peer)
public macro ReportableMetadataIgnored() = #externalMacro(module: "StateReportingMacros", type: "ReportableMetadataIgnored")

@attached(peer)
public macro ReportableMetadataKey(_ key: String) = #externalMacro(module: "StateReportingMacros", type: "ReportableMetadataKey")

@ReportableMetadata
struct DeviceState {
    var battery: Int = 0

    @ReportableMetadataKey("device_name")
    var name: String = "iphone"

    @ReportableMetadataIgnored
    var cache: Int = 0
}
