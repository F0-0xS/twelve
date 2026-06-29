import SwiftUI
import PhotosUI

@MainActor
final class SlotViewModel: ObservableObject {
    @Published var missedOptions: [MissedOption] = []
    @Published var isSubmitting = false
    @Published var errorMessage: String?
    @Published var selectedItem: PhotosPickerItem?
    @Published var selectedImage: UIImage?

    let challengeId: String
    let slotIndex: Int
    let participantId: String
    let onDone: () -> Void

    init(challengeId: String, slotIndex: Int, participantId: String, onDone: @escaping () -> Void) {
        self.challengeId = challengeId
        self.slotIndex = slotIndex
        self.participantId = participantId
        self.onDone = onDone
    }

    func loadOptions() async {
        if let options = try? await APIClient.shared.missedOptions() {
            missedOptions = options
        }
    }

    func loadImage(from item: PhotosPickerItem?) async {
        guard let item else { return }
        if let data = try? await item.loadTransferable(type: Data.self),
           let img  = UIImage(data: data) {
            selectedImage = img
        }
    }

    func submitPhoto() async {
        guard let img = selectedImage else { return }
        isSubmitting = true
        errorMessage = nil
        do {
            _ = try await APIClient.shared.submitPhoto(
                challengeId: challengeId, slotIndex: slotIndex,
                participantId: participantId, image: img)
            onDone()
        } catch {
            errorMessage = error.localizedDescription
        }
        isSubmitting = false
    }

    func submitMissed(reason: String, emoji: String) async {
        isSubmitting = true
        errorMessage = nil
        do {
            _ = try await APIClient.shared.submitMissed(
                challengeId: challengeId, slotIndex: slotIndex,
                participantId: participantId, reason: reason, emoji: emoji)
            onDone()
        } catch {
            errorMessage = error.localizedDescription
        }
        isSubmitting = false
    }
}

// MARK: - SlotView

struct SlotView: View {
    let challengeId: String
    let slotIndex: Int
    let slot: Slot
    let challengeDate: String
    let onDone: () -> Void

    @Environment(\.dismiss) private var dismiss
    @StateObject private var vm: SlotViewModel

    init(challengeId: String, slotIndex: Int, slot: Slot,
         challengeDate: String, onDone: @escaping () -> Void) {
        self.challengeId  = challengeId
        self.slotIndex    = slotIndex
        self.slot         = slot
        self.challengeDate = challengeDate
        self.onDone       = onDone

        let pid = Storage.shared.participantId(for: challengeId) ?? ""
        _vm = StateObject(wrappedValue: SlotViewModel(
            challengeId: challengeId, slotIndex: slotIndex,
            participantId: pid,
            onDone: { onDone() }
        ))
    }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            ScrollView {
                VStack(alignment: .leading, spacing: 24) {

                    // Header
                    VStack(alignment: .leading, spacing: 4) {
                        Text(slot.label)
                            .font(.system(size: 32, weight: .black, design: .monospaced))
                            .foregroundColor(Color(hex: "#e8d5b0"))
                        Text("Créneau du \(challengeDate)")
                            .font(.caption).foregroundColor(.gray)
                    }

                    if let err = vm.errorMessage {
                        Text(err).font(.caption).foregroundColor(.red)
                    }

                    // ── Photo section ──
                    VStack(alignment: .leading, spacing: 12) {
                        Text("📷 Envoyer une photo")
                            .font(.headline).foregroundColor(.white)

                        // Preview
                        if let img = vm.selectedImage {
                            Image(uiImage: img)
                                .resizable().scaledToFit()
                                .frame(maxHeight: 260)
                                .clipShape(RoundedRectangle(cornerRadius: 12))
                        }

                        PhotosPicker(
                            selection: $vm.selectedItem,
                            matching: .images,
                            photoLibrary: .shared()
                        ) {
                            Label(vm.selectedImage == nil ? "Choisir une photo" : "Changer la photo",
                                  systemImage: "photo.on.rectangle")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(TwelveOutlineButtonStyle())
                        .onChange(of: vm.selectedItem) { _, item in
                            Task { await vm.loadImage(from: item) }
                        }

                        if vm.selectedImage != nil {
                            Button(action: { Task { await vm.submitPhoto() } }) {
                                HStack {
                                    if vm.isSubmitting { ProgressView().tint(.black) }
                                    Text("Envoyer →").fontWeight(.bold)
                                }
                                .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(TwelvePrimaryButtonStyle())
                            .disabled(vm.isSubmitting)
                        }
                    }
                    .padding(16)
                    .background(Color(hex: "#1a1a1a"))
                    .cornerRadius(12)

                    // ── Missed section ──
                    VStack(alignment: .leading, spacing: 12) {
                        Text("😴 Signaler une absence")
                            .font(.headline).foregroundColor(.white)

                        if vm.missedOptions.isEmpty {
                            ProgressView().tint(Color(hex: "#e8d5b0"))
                        } else {
                            ForEach(vm.missedOptions) { opt in
                                Button(action: {
                                    Task { await vm.submitMissed(reason: opt.label, emoji: opt.emoji) }
                                }) {
                                    HStack {
                                        Text(opt.emoji).font(.title3)
                                        Text(opt.label).foregroundColor(.white)
                                        Spacer()
                                        if vm.isSubmitting {
                                            ProgressView().tint(.gray)
                                        }
                                    }
                                    .padding(12)
                                    .background(Color(hex: "#111111"))
                                    .cornerRadius(10)
                                    .overlay(
                                        RoundedRectangle(cornerRadius: 10)
                                            .stroke(Color(hex: "#2a2a2a"), lineWidth: 1)
                                    )
                                }
                                .buttonStyle(.plain)
                                .disabled(vm.isSubmitting)
                            }
                        }
                    }
                    .padding(16)
                    .background(Color(hex: "#1a1a1a"))
                    .cornerRadius(12)
                }
                .padding(20)
            }
        }
        .navigationTitle(slot.label)
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(Color.black, for: .navigationBar)
        .toolbarColorScheme(.dark, for: .navigationBar)
        .task { await vm.loadOptions() }
    }
}
