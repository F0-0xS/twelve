import Foundation

/// Persists participant IDs across sessions (equivalent to the web cookie).
/// Key: challenge ID  →  Value: participant ID
final class Storage {
    static let shared = Storage()
    private let key = "twelve_participant_ids"

    private init() {}

    func participantId(for challengeId: String) -> String? {
        all()[challengeId]
    }

    func setParticipantId(_ participantId: String, for challengeId: String) {
        var map = all()
        map[challengeId] = participantId
        UserDefaults.standard.set(map, forKey: key)
    }

    func removeParticipantId(for challengeId: String) {
        var map = all()
        map.removeValue(forKey: challengeId)
        UserDefaults.standard.set(map, forKey: key)
    }

    private func all() -> [String: String] {
        UserDefaults.standard.dictionary(forKey: key) as? [String: String] ?? [:]
    }
}
