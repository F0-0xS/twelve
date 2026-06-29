import SwiftUI

struct HomeView: View {
    @State private var challengeName = ""
    @State private var challengeIdInput = ""
    @State private var isCreating = false
    @State private var isJoiningByLink = false
    @State private var errorMessage: String?
    @State private var navigateTo: NavigationTarget?

    enum NavigationTarget: Identifiable {
        case challenge(id: String)
        var id: String { if case .challenge(let id) = self { return id } ; return "" }
    }

    var body: some View {
        NavigationStack {
            ZStack {
                Color.black.ignoresSafeArea()

                ScrollView {
                    VStack(spacing: 32) {

                        // ── Logo ──
                        VStack(spacing: 8) {
                            Text("12")
                                .font(.system(size: 72, weight: .black, design: .rounded))
                                .foregroundColor(Color(hex: "#e8d5b0"))
                            Text("Un défi photo de 24h entre proches.\n12 créneaux · 1 lien · Aucun compte.")
                                .font(.subheadline)
                                .foregroundColor(.gray)
                                .multilineTextAlignment(.center)
                        }
                        .padding(.top, 48)

                        // ── Create ──
                        VStack(alignment: .leading, spacing: 12) {
                            Label("Créer un défi", systemImage: "plus.circle.fill")
                                .font(.headline)
                                .foregroundColor(Color(hex: "#e8d5b0"))

                            TextField("Nom du défi…", text: $challengeName)
                                .textFieldStyle(TwelveTextFieldStyle())
                                .autocorrectionDisabled()

                            if let err = errorMessage {
                                Text(err)
                                    .font(.caption)
                                    .foregroundColor(.red)
                            }

                            Button(action: createChallenge) {
                                HStack {
                                    if isCreating {
                                        ProgressView().tint(.black)
                                    }
                                    Text("Créer →")
                                        .fontWeight(.bold)
                                }
                                .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(TwelvePrimaryButtonStyle())
                            .disabled(challengeName.trimmingCharacters(in: .whitespaces).isEmpty || isCreating)
                        }
                        .padding(20)
                        .background(Color(hex: "#1a1a1a"))
                        .cornerRadius(16)

                        // ── Join by link ──
                        VStack(alignment: .leading, spacing: 12) {
                            Label("Rejoindre via un lien", systemImage: "link")
                                .font(.headline)
                                .foregroundColor(.gray)

                            TextField("Colle le lien ou l'ID du défi…", text: $challengeIdInput)
                                .textFieldStyle(TwelveTextFieldStyle())
                                .autocorrectionDisabled()
                                .autocapitalization(.none)

                            Button(action: joinByLink) {
                                Text("Rejoindre →")
                                    .fontWeight(.bold)
                                    .frame(maxWidth: .infinity)
                            }
                            .buttonStyle(TwelveOutlineButtonStyle())
                            .disabled(challengeIdInput.trimmingCharacters(in: .whitespaces).isEmpty || isJoiningByLink)
                        }
                        .padding(20)
                        .background(Color(hex: "#1a1a1a"))
                        .cornerRadius(16)

                        Spacer(minLength: 40)
                    }
                    .padding(.horizontal, 20)
                }
            }
            .navigationTitle("")
            .navigationBarHidden(true)
            .navigationDestination(item: $navigateTo) { target in
                if case .challenge(let id) = target {
                    ChallengeView(challengeId: id)
                }
            }
        }
    }

    // ── Actions ───────────────────────────────────────────────────────────────

    private func createChallenge() {
        let name = challengeName.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty else { return }
        isCreating = true
        errorMessage = nil
        Task {
            do {
                let challenge = try await APIClient.shared.createChallenge(name: name)
                await MainActor.run {
                    isCreating = false
                    navigateTo = .challenge(id: challenge.id)
                }
            } catch {
                await MainActor.run {
                    isCreating = false
                    errorMessage = error.localizedDescription
                }
            }
        }
    }

    private func joinByLink() {
        // Extract challenge ID from a full URL or use input directly
        var input = challengeIdInput.trimmingCharacters(in: .whitespaces)
        if let url = URL(string: input), let last = url.pathComponents.last, last.count >= 6 {
            input = last
        }
        guard !input.isEmpty else { return }
        navigateTo = .challenge(id: input)
    }
}

// MARK: - Styles

struct TwelveTextFieldStyle: TextFieldStyle {
    func _body(configuration: TextField<Self._Label>) -> some View {
        configuration
            .padding(12)
            .background(Color(hex: "#111111"))
            .cornerRadius(10)
            .foregroundColor(.white)
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .stroke(Color(hex: "#2a2a2a"), lineWidth: 1)
            )
    }
}

struct TwelvePrimaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .padding(.vertical, 14)
            .background(configuration.isPressed ? Color(hex: "#c9b88a") : Color(hex: "#e8d5b0"))
            .foregroundColor(.black)
            .cornerRadius(10)
            .animation(.easeInOut(duration: 0.1), value: configuration.isPressed)
    }
}

struct TwelveOutlineButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .padding(.vertical, 14)
            .foregroundColor(Color(hex: "#e8d5b0"))
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .stroke(Color(hex: "#2a2a2a"), lineWidth: 1)
            )
            .animation(.easeInOut(duration: 0.1), value: configuration.isPressed)
    }
}

// MARK: - Color helper

extension Color {
    init(hex: String) {
        let hex = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        var int: UInt64 = 0
        Scanner(string: hex).scanHexInt64(&int)
        let r = Double((int >> 16) & 0xFF) / 255
        let g = Double((int >> 8) & 0xFF) / 255
        let b = Double(int & 0xFF) / 255
        self.init(red: r, green: g, blue: b)
    }
}

#Preview {
    HomeView()
}
