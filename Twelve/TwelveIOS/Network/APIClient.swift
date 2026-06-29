import Foundation
import UIKit

// MARK: - Configuration

/// Change this to your Mac's LAN IP when testing on a real iPhone.
/// Example: "http://192.168.1.42:8888"
/// For the iOS Simulator, "http://localhost:8888" works directly.
var API_BASE_URL = "http://localhost:8888"

// MARK: - Errors

enum APIError: LocalizedError {
    case invalidURL
    case serverError(Int, String)
    case decodingError(Error)
    case networkError(Error)

    var errorDescription: String? {
        switch self {
        case .invalidURL:              return "URL invalide"
        case .serverError(_, let m):   return m
        case .decodingError(let e):    return "Décodage: \(e.localizedDescription)"
        case .networkError(let e):     return e.localizedDescription
        }
    }
}

// MARK: - APIClient

final class APIClient {
    static let shared = APIClient()
    private let session = URLSession.shared
    private let decoder: JSONDecoder = {
        let d = JSONDecoder()
        return d
    }()

    private init() {}

    // ── Helpers ───────────────────────────────────────────────────────────────

    private func url(_ path: String) throws -> URL {
        guard let url = URL(string: API_BASE_URL + path) else { throw APIError.invalidURL }
        return url
    }

    private func jsonRequest(_ url: URL, method: String, body: [String: Any]? = nil,
                              participantId: String? = nil) -> URLRequest {
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let pid = participantId {
            req.setValue(pid, forHTTPHeaderField: "X-Participant-Id")
        }
        if let body = body {
            req.httpBody = try? JSONSerialization.data(withJSONObject: body)
        }
        return req
    }

    private func perform<T: Decodable>(_ req: URLRequest) async throws -> T {
        let (data, response): (Data, URLResponse)
        do {
            (data, response) = try await session.data(for: req)
        } catch {
            throw APIError.networkError(error)
        }
        if let http = response as? HTTPURLResponse, http.statusCode >= 400 {
            let msg = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["error"] as? String
                ?? HTTPURLResponse.localizedString(forStatusCode: http.statusCode)
            throw APIError.serverError(http.statusCode, msg)
        }
        do {
            return try decoder.decode(T.self, from: data)
        } catch {
            throw APIError.decodingError(error)
        }
    }

    // ── API calls ─────────────────────────────────────────────────────────────

    /// Create a new challenge. Sends today's local date.
    func createChallenge(name: String) async throws -> Challenge {
        let req = jsonRequest(
            try url("/api/challenges"),
            method: "POST",
            body: ["name": name, "challenge_date": localDateString()]
        )
        return try await perform(req)
    }

    /// Fetch full challenge state (challenge + participants + slots + contributions).
    func getChallenge(_ id: String) async throws -> ChallengeState {
        let req = URLRequest(url: try url("/api/challenges/\(id)"))
        return try await perform(req)
    }

    /// Join a challenge with a first name. Returns challenge + participant.
    func join(challengeId: String, name: String) async throws -> (Challenge, Participant) {
        let req = jsonRequest(
            try url("/api/challenges/\(challengeId)/join"),
            method: "POST",
            body: ["name": name]
        )
        struct JoinResponse: Decodable {
            let challenge: Challenge
            let participant: Participant
        }
        let r: JoinResponse = try await perform(req)
        return (r.challenge, r.participant)
    }

    /// Submit a photo for a slot.
    func submitPhoto(challengeId: String, slotIndex: Int,
                     participantId: String, image: UIImage) async throws -> Contribution {
        let reqUrl = try url("/api/challenges/\(challengeId)/slots/\(slotIndex)/contribute")
        var req = URLRequest(url: reqUrl)
        req.httpMethod = "POST"
        req.setValue(participantId, forHTTPHeaderField: "X-Participant-Id")

        let boundary = "Boundary-\(UUID().uuidString)"
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")

        guard let jpeg = image.jpegData(compressionQuality: 0.85) else {
            throw APIError.serverError(400, "Impossible de compresser l'image")
        }

        var body = Data()
        body.append("--\(boundary)\r\n".data(using: .utf8)!)
        body.append("Content-Disposition: form-data; name=\"photo\"; filename=\"photo.jpg\"\r\n".data(using: .utf8)!)
        body.append("Content-Type: image/jpeg\r\n\r\n".data(using: .utf8)!)
        body.append(jpeg)
        body.append("\r\n--\(boundary)--\r\n".data(using: .utf8)!)
        req.httpBody = body

        return try await perform(req)
    }

    /// Submit a "missed" contribution.
    func submitMissed(challengeId: String, slotIndex: Int,
                      participantId: String, reason: String, emoji: String) async throws -> Contribution {
        let req = jsonRequest(
            try url("/api/challenges/\(challengeId)/slots/\(slotIndex)/contribute"),
            method: "POST",
            body: ["action": "missed", "reason": reason, "emoji": emoji],
            participantId: participantId
        )
        return try await perform(req)
    }

    /// Rename participant.
    func rename(challengeId: String, participantId: String, newName: String) async throws {
        let req = jsonRequest(
            try url("/api/challenges/\(challengeId)/rename"),
            method: "POST",
            body: ["name": newName],
            participantId: participantId
        )
        struct OKResponse: Decodable { let ok: Bool }
        let _: OKResponse = try await perform(req)
    }

    /// Fetch missed options list.
    func missedOptions() async throws -> [MissedOption] {
        let req = URLRequest(url: try url("/api/missed-options"))
        struct Wrapper: Decodable { let options: [MissedOption] }
        let w: Wrapper = try await perform(req)
        return w.options
    }

    // ── Helpers ───────────────────────────────────────────────────────────────

    /// Returns today's date as "YYYY-MM-DD" in the device's local timezone.
    private func localDateString() -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.timeZone = .current
        return f.string(from: Date())
    }
}
