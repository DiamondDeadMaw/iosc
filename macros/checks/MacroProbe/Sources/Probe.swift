import Foundation
import SwiftData
import SwiftUI

// Every use site here exists to force one reimplemented macro through real type
// resolution against the extracted SDK, which host typecheck cannot do.
//
// SwiftData and SwiftUI are imported without the _SwiftData_SwiftUI bridge. @Query,
// modelContext and modelContainer(for:) below reach it through the automatic
// cross-import overlay, so this also guards that the overlay stays enabled.

struct CounterView: View {
    @State private var count: Int = 0

    var body: some View {
        Button("count \(count)") { count += 1 }
    }
}

// @State without a type annotation. The macro recovers the value type from the
// initializer the same way the compiler would: literals, constructor calls,
// dotted members.
@Observable final class ProbeSession {
    var launches = 0
}

struct InferredStateView: View {
    @State private var flag = false
    @State private var tally = 0
    @State private var label = ""
    @State private var ratio = 1.5
    @State private var session = ProbeSession()
    @State private var accent = Color.red

    var body: some View {
        VStack {
            Toggle(label, isOn: $flag)
            Stepper("t \(tally)", value: $tally)
            Text("\(ratio) \(session.launches)").foregroundStyle(accent)
        }
    }
}

extension EnvironmentValues {
    @Entry var probeTint: Color = .blue
}

@Model
final class Tag {
    var name: String

    init(name: String) {
        self.name = name
    }
}

@Model
final class Note {
    var title: String
    var createdAt: Date

    @Transient var scratch: Int = 0

    @Relationship(deleteRule: .cascade) var tags: [Tag]

    init(title: String, createdAt: Date = .now, tags: [Tag] = []) {
        self.title = title
        self.createdAt = createdAt
        self.tags = tags
    }
}

struct NoteList: View {
    @Query private var notes: [Note]
    @Environment(\.modelContext) private var context

    var body: some View {
        List(notes) { note in
            Text(note.title)
        }
    }

    func add() {
        context.insert(Note(title: "untitled"))
    }
}

struct ProbeApp: App {
    var body: some Scene {
        WindowGroup { NoteList() }
            .modelContainer(for: Note.self)
    }
}
