import SwiftUI

@MainActor
final class ChallengeViewModel: ObservableObject {
    @Published var state: ChallengeState?
    @Published var isLoading = true
    @Published var errorMessage: String?
    @Published var myParticipantId: String?
    @Published var showJoin = false
    @Published var showRename = false
    @Published var joinName = ""
    @Published var isJoining = false

    let challengeId: String

    init(challengeId: String) {
        self.challengeId = challengeId
        myParticipantId = Storage.shared.participantId(for: challengeId)
    }

    var me: Participant? {
        guard let pid = myParticipantId,
              let participants = state?.participants else { return nil }
        return participants.first { $0.id == pid }
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        do {
            state = try await APIClient.shared.getChallenge(challengeId)
            // Show join form if not yet a participant
            if myParticipantId == nil || me == nil {
                showJoin = true
            }
        } catch {
            errorMessage = error.localizedDescription
        }
        isLoading = false
    }

    func join() async {
        let name = joinName.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty else { return }
        isJoining = true
        do {
            let (_, participant) = try await APIClient.shared.join(challengeId: challengeId, name: name)
            Storage.shared.setParticipantId(participant.id, for: challengeId)
            myParticipantId = participant.id
            showJoin = false
            await load()
        } catch {
            errorMessage = error.localizedDescription
        }
        isJoining = false
    }

    func rename(to newName: String) async {
        guard let pid = myParticipantId else { return }
        do {
            try await APIClient.shared.rename(challengeId: challengeId, participantId: pid, newName: newName)
            await load()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// Compute slot status locally using device time (timezone-aware).
    func localStatus(for slot: Slot) -> SlotStatus {
        guard let challenge = state?.challenge else { return .past }
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"
        formatter.timeZone = .current

        let openDate  = formatter.date(from: "\(challenge.challengeDate)T\(String(format: "%02d", slot.hour)):00:00") ?? Date.distantPast
        let closeHour = slot.hour + 2
        let closeDate: Date
        if closeHour >= 24 {
            closeDate = formatter.date(from: "\(challenge.challengeDate)T23:59:59")?.addingTimeInterval(1) ?? Date.distantPast
        } else {
            closeDate = formatter.date(from: "\(challenge.challengeDate)T\(String(format: "%02d", closeHour)):00:00") ?? Date.distantPast
        }

        let now = Date()
        if now < openDate  { return .upcoming }
        if now < closeDate { return .open }
        return .past
    }
}

// MARK: - ChallengeView

struct ChallengeView: View {
    let challengeId: String
    @StateObject private var vm: ChallengeViewModel

    init(challengeId: String) {
        self.challengeId = challengeId
        _vm = StateObject(wrappedValue: ChallengeViewModel(challengeId: challengeId))
    }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            if vm.isLoading {
                ProgressView("Chargement…").tint(Color(hex: "#e8d5b0"))
            } else if let error = vm.errorMessage {
                VStack(spacing: 16) {
                    Text("Erreur").font(.headline).foregroundColor(.red)
                    Text(error).foregroundColor(.gray).multilineTextAlignment(.center)
                    Button("Réessayer") { Task { await vm.load() } }
                        .buttonStyle(TwelveOutlineButtonStyle())
                }
                .padding()
            } else if let state = vm.state {
                mainContent(state: state)
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(Color.black, for: .navigationBar)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .task { await vm.load() }
        .sheet(isPresented: $vm.showJoin)  { joinSheet }
        .sheet(isPresented: $vm.showRename) { renameSheet }
    }

    // ── Main content ──────────────────────────────────────────────────────────

    @ViewBuilder
    private func mainContent(state: ChallengeState) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {

                // Title
                VStack(alignment: .leading, spacing: 4) {
                    Text(state.challenge.name)
                        .font(.system(size: 26, weight: .black))
                        .foregroundColor(.white)
                    Text("Défi du \(state.challenge.challengeDate)")
                        .font(.caption)
                        .foregroundColor(.gray)
                }

                // Participants row
                participantsRow(state: state)

                // Share
                ShareStrip(challengeId: challengeId)

                // Timeline
                Text("Timeline")
                    .font(.caption)
                    .fontWeight(.semibold)
                    .foregroundColor(.gray)
                    .textCase(.uppercase)

                ForEach(state.slots) { slot in
                    SlotRowView(
                        slot: slot,
                        status: vm.localStatus(for: slot),
                        participants: state.participants,
                        myContrib: state.contributions.first {
                            $0.slotIndex == slot.index && $0.participantId == (vm.myParticipantId ?? "")
                        },
                        challengeId: challengeId,
                        challengeDate: state.challenge.challengeDate,
                        isJoined: vm.me != nil,
                        onRefresh: { Task { await vm.load() } }
                    )
                }
            }
            .padding(20)
        }
        .refreshable { await vm.load() }
    }

    // ── Participants ──────────────────────────────────────────────────────────

    @ViewBuilder
    private func participantsRow(state: ChallengeState) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Participants (\(state.participants.count)/6)")
                    .font(.caption).fontWeight(.semibold).foregroundColor(.gray).textCase(.uppercase)
                Spacer()
                if vm.me != nil {
                    Button("✏️ Modifier") { vm.showRename = true }
                        .font(.caption).foregroundColor(Color(hex: "#e8d5b0"))
                }
            }
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(state.participants) { p in
                        ParticipantChip(participant: p, isMe: p.id == vm.myParticipantId)
                    }
                }
            }
        }
    }

    // ── Sheets ────────────────────────────────────────────────────────────────

    @ViewBuilder
    private var joinSheet: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            VStack(spacing: 20) {
                Text("Rejoins le défi").font(.title2).fontWeight(.bold).foregroundColor(.white)
                Text("Entre ton prénom pour participer.").foregroundColor(.gray)
                TextField("Ton prénom…", text: $vm.joinName)
                    .textFieldStyle(TwelveTextFieldStyle())
                    .autocorrectionDisabled()
                if let err = vm.errorMessage {
                    Text(err).font(.caption).foregroundColor(.red)
                }
                Button(action: { Task { await vm.join() } }) {
                    HStack {
                        if vm.isJoining { ProgressView().tint(.black) }
                        Text("Rejoindre →").fontWeight(.bold)
                    }
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(TwelvePrimaryButtonStyle())
                .disabled(vm.joinName.trimmingCharacters(in: .whitespaces).isEmpty || vm.isJoining)
            }
            .padding(28)
        }
        .presentationDetents([.medium])
    }

    @ViewBuilder
    private var renameSheet: some View {
        RenameSheet(
            currentName: vm.me?.name ?? "",
            onSave: { newName in
                vm.showRename = false
                Task { await vm.rename(to: newName) }
            },
            onCancel: { vm.showRename = false }
        )
    }
}

// MARK: - SlotRowView

struct SlotRowView: View {
    let slot: Slot
    let status: SlotStatus
    let participants: [Participant]
    let myContrib: Contribution?
    let challengeId: String
    let challengeDate: String
    let isJoined: Bool
    let onRefresh: () -> Void

    @State private var navigate = false

    var body: some View {
        Button(action: { if isJoined { navigate = true } }) {
            HStack(alignment: .top, spacing: 12) {
                // Dot
                Circle()
                    .fill(dotColor)
                    .frame(width: 12, height: 12)
                    .padding(.top, 6)

                VStack(alignment: .leading, spacing: 6) {
                    HStack {
                        Text(slot.label)
                            .font(.system(.body, design: .monospaced))
                            .fontWeight(.bold)
                            .foregroundColor(.white)
                        Spacer()
                        statusBadge
                    }

                    // Contributions thumbnails
                    if !slot.contributions.isEmpty {
                        ContribsRow(contributions: slot.contributions, participants: participants)
                    }

                    // Missing chips
                    if status != .upcoming && !slot.missingParticipantIds.isEmpty {
                        MissingChips(ids: slot.missingParticipantIds, participants: participants)
                    }
                }
                .padding(12)
                .background(Color(hex: "#1a1a1a"))
                .cornerRadius(12)
                .overlay(
                    RoundedRectangle(cornerRadius: 12)
                        .stroke(borderColor, lineWidth: 1)
                )
            }
        }
        .buttonStyle(.plain)
        .navigationDestination(isPresented: $navigate) {
            SlotView(challengeId: challengeId, slotIndex: slot.index,
                     slot: slot, challengeDate: challengeDate,
                     onDone: { navigate = false; onRefresh() })
        }
    }

    private var dotColor: Color {
        if myContrib?.type == "photo" { return Color.green }
        if myContrib?.type == "missed" { return Color.red }
        switch status {
        case .open:     return Color(hex: "#f59e0b")
        case .upcoming: return Color(hex: "#2a2a2a")
        case .past:     return Color(hex: "#1a1a1a")
        }
    }

    private var borderColor: Color {
        if myContrib?.type == "photo" { return Color.green.opacity(0.3) }
        switch status {
        case .open: return Color(hex: "#f59e0b").opacity(0.4)
        default:    return Color(hex: "#2a2a2a")
        }
    }

    @ViewBuilder
    private var statusBadge: some View {
        if myContrib?.type == "photo" {
            Badge(label: "envoyé", color: .green)
        } else if myContrib?.type == "missed" {
            Badge(label: "manqué", color: .red)
        } else {
            switch status {
            case .open:     Badge(label: "ouvert", color: Color(hex: "#f59e0b"))
            case .upcoming: Badge(label: "à venir", color: .gray)
            case .past:     Badge(label: "passé",   color: Color(hex: "#333333"))
            }
        }
    }
}

// MARK: - Supporting Views

struct ParticipantChip: View {
    let participant: Participant
    let isMe: Bool

    var body: some View {
        HStack(spacing: 6) {
            Text(String(participant.name.prefix(1)).uppercased())
                .font(.caption).fontWeight(.bold)
                .foregroundColor(.black)
                .frame(width: 22, height: 22)
                .background(Color(hex: "#e8d5b0"))
                .clipShape(Circle())
            Text(participant.name)
                .font(.subheadline).foregroundColor(.white)
            if isMe {
                Text("· toi").font(.caption).foregroundColor(.gray)
            }
        }
        .padding(.horizontal, 10).padding(.vertical, 6)
        .background(Color(hex: "#1a1a1a"))
        .cornerRadius(99)
        .overlay(RoundedRectangle(cornerRadius: 99).stroke(Color(hex: "#2a2a2a"), lineWidth: 1))
    }
}

struct Badge: View {
    let label: String
    let color: Color
    var body: some View {
        Text(label)
            .font(.system(size: 11, weight: .semibold))
            .foregroundColor(color)
            .padding(.horizontal, 8).padding(.vertical, 3)
            .background(color.opacity(0.15))
            .cornerRadius(99)
    }
}

struct ContribsRow: View {
    let contributions: [Contribution]
    let participants: [Participant]
    private let participantMap: [String: Participant]

    init(contributions: [Contribution], participants: [Participant]) {
        self.contributions = contributions
        self.participants = participants
        self.participantMap = Dictionary(uniqueKeysWithValues: participants.map { ($0.id, $0) })
    }

    var body: some View {
        HStack(spacing: 6) {
            ForEach(contributions) { c in
                if c.type == "photo", let urlStr = c.photoUrl,
                   let url = URL(string: API_BASE_URL + urlStr) {
                    AsyncImage(url: url) { img in
                        img.resizable().scaledToFill()
                    } placeholder: {
                        Color.gray.opacity(0.3)
                    }
                    .frame(width: 52, height: 52)
                    .clipShape(RoundedRectangle(cornerRadius: 6))
                } else if c.type == "missed" {
                    ZStack {
                        RoundedRectangle(cornerRadius: 6)
                            .fill(Color(hex: "#111111"))
                            .frame(width: 52, height: 52)
                        Text(c.missedEmoji ?? "❓").font(.title3)
                    }
                }
            }
        }
    }
}

struct MissingChips: View {
    let ids: [String]
    let participants: [Participant]
    private let map: [String: Participant]

    init(ids: [String], participants: [Participant]) {
        self.ids = ids
        self.participants = participants
        self.map = Dictionary(uniqueKeysWithValues: participants.map { ($0.id, $0) })
    }

    var body: some View {
        HStack(spacing: 4) {
            ForEach(ids, id: \.self) { id in
                if let p = map[id] {
                    Text(p.name)
                        .font(.system(size: 11)).foregroundColor(.gray)
                        .padding(.horizontal, 8).padding(.vertical, 2)
                        .background(Color(hex: "#111111"))
                        .cornerRadius(99)
                }
            }
        }
    }
}

struct ShareStrip: View {
    let challengeId: String
    @State private var copied = false

    private var shareUrl: String {
        "\(API_BASE_URL)/challenge/\(challengeId)"
    }

    var body: some View {
        VStack(spacing: 10) {
            HStack(spacing: 10) {
                Text(shareUrl)
                    .font(.caption)
                    .foregroundColor(.gray)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer()
                Button(copied ? "✓ Copié" : "Copier") {
                    UIPasteboard.general.string = shareUrl
                    copied = true
                    DispatchQueue.main.asyncAfter(deadline: .now() + 2) { copied = false }
                }
                .font(.caption).fontWeight(.semibold)
                .foregroundColor(Color(hex: "#e8d5b0"))
            }
            // Share sheet button
            ShareLink(item: URL(string: shareUrl)!) {
                Label("Partager le lien", systemImage: "square.and.arrow.up")
                    .font(.subheadline).fontWeight(.semibold)
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(TwelveOutlineButtonStyle())
        }
        .padding(14)
        .background(Color(hex: "#1a1a1a"))
        .cornerRadius(12)
    }
}

struct RenameSheet: View {
    let currentName: String
    let onSave: (String) -> Void
    let onCancel: () -> Void
    @State private var name = ""

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            VStack(spacing: 20) {
                Text("Modifier mon prénom").font(.title3).fontWeight(.bold).foregroundColor(.white)
                TextField(currentName, text: $name)
                    .textFieldStyle(TwelveTextFieldStyle())
                    .autocorrectionDisabled()
                HStack(spacing: 12) {
                    Button("Annuler", action: onCancel)
                        .buttonStyle(TwelveOutlineButtonStyle())
                        .frame(maxWidth: .infinity)
                    Button("Enregistrer") { onSave(name.trimmingCharacters(in: .whitespaces)) }
                        .buttonStyle(TwelvePrimaryButtonStyle())
                        .frame(maxWidth: .infinity)
                        .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
            .padding(28)
        }
        .presentationDetents([.medium])
        .onAppear { name = currentName }
    }
}

#Preview {
    NavigationStack {
        ChallengeView(challengeId: "preview")
    }
}
