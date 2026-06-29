import Foundation

// MARK: - Challenge

struct Challenge: Codable, Identifiable {
    let id: String
    let name: String
    let createdAt: String
    let challengeDate: String

    enum CodingKeys: String, CodingKey {
        case id, name
        case createdAt  = "created_at"
        case challengeDate = "challenge_date"
    }
}

// MARK: - Participant

struct Participant: Codable, Identifiable {
    let id: String
    let challengeId: String
    let name: String
    let joinedAt: String

    enum CodingKeys: String, CodingKey {
        case id, name
        case challengeId = "challenge_id"
        case joinedAt    = "joined_at"
    }
}

// MARK: - Contribution

struct Contribution: Codable, Identifiable {
    let id: String
    let challengeId: String
    let participantId: String
    let slotIndex: Int
    let type: String          // "photo" | "missed"
    let photoUrl: String?
    let missedReason: String?
    let missedEmoji: String?
    let createdAt: String

    enum CodingKeys: String, CodingKey {
        case id, type
        case challengeId    = "challenge_id"
        case participantId  = "participant_id"
        case slotIndex      = "slot_index"
        case photoUrl       = "photo_url"
        case missedReason   = "missed_reason"
        case missedEmoji    = "missed_emoji"
        case createdAt      = "created_at"
    }
}

// MARK: - Slot

struct Slot: Codable, Identifiable {
    let index: Int
    let hour: Int
    let label: String
    let status: String        // "upcoming" | "open" | "past"
    let contributions: [Contribution]
    let missingParticipantIds: [String]

    var id: Int { index }

    enum CodingKeys: String, CodingKey {
        case index, hour, label, status, contributions
        case missingParticipantIds = "missing_participant_ids"
    }

    /// Status computed client-side from local time (timezone-aware)
    var localStatus: SlotStatus {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        // challengeDate is passed separately; use static helper below
        return SlotStatus(rawValue: status) ?? .past
    }
}

enum SlotStatus: String {
    case upcoming, open, past
}

// MARK: - ChallengeState (full API response)

struct ChallengeState: Codable {
    let challenge: Challenge
    let participants: [Participant]
    let contributions: [Contribution]
    let slots: [Slot]
}

// MARK: - MissedOption

struct MissedOption: Codable, Identifiable {
    let label: String
    let emoji: String
    var id: String { label }
}
