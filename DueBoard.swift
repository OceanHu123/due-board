import AppKit
import Foundation
import UserNotifications

final class AppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    private let defaultsKey = "latestOpenTarget"

    func applicationDidFinishLaunching(_ notification: Notification) {
        UNUserNotificationCenter.current().delegate = self
        let args = Array(CommandLine.arguments.dropFirst())

        if args.count >= 2 {
            let title = args[0]
            let body = args[1]
            let openTarget = args.count >= 3 ? args[2] : Self.defaultOpenTarget()
            UserDefaults.standard.set(openTarget, forKey: defaultsKey)
            postNotification(title: title, body: body, openTarget: openTarget)
        } else {
            openLatestTarget()
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) {
                NSApp.terminate(nil)
            }
        }
    }

    /// Hardcoded last-resort target — can be overridden at runtime by passing
    /// a third argument (used by `remind.py` which reads `config.yaml.site_url`).
    private static func defaultOpenTarget() -> String {
        "https://usyd-due-web.onrender.com"
    }

    /// Build a URL from a string that may be an http(s) link, a file path,
    /// or an already-scheme-qualified URL. Never crashes on malformed input.
    private static func makeURL(_ raw: String) -> URL? {
        if let u = URL(string: raw), u.scheme != nil {
            return u
        }
        // Treat anything else as a local file path.
        return URL(fileURLWithPath: raw)
    }

    private func openLatestTarget() {
        let target = UserDefaults.standard.string(forKey: defaultsKey) ?? Self.defaultOpenTarget()
        guard let url = Self.makeURL(target) else {
            fputs("Unable to parse open target: \(target)\n", stderr)
            return
        }
        NSWorkspace.shared.open(url)
    }

    private func postNotification(title: String, body: String, openTarget: String) {
        let center = UNUserNotificationCenter.current()
        center.requestAuthorization(options: [.alert, .sound, .badge]) { granted, error in
            if let error = error {
                fputs("auth error: \(error.localizedDescription)\n", stderr)
                NSApp.terminate(nil)
                return
            }
            if !granted {
                fputs(
                    "notification permission not granted — enable DueBoard in System Settings → Notifications\n",
                    stderr
                )
                // Still open the target so the user sees details.
                DispatchQueue.main.async {
                    if let url = Self.makeURL(openTarget) {
                        NSWorkspace.shared.open(url)
                    }
                    NSApp.terminate(nil)
                }
                return
            }

            let content = UNMutableNotificationContent()
            content.title = title
            content.body = body
            content.sound = .default
            content.userInfo = ["openTarget": openTarget]
            content.categoryIdentifier = "DUE_SUMMARY"

            let request = UNNotificationRequest(
                identifier: UUID().uuidString,
                content: content,
                trigger: nil
            )
            center.add(request) { error in
                if let error = error {
                    fputs("notify error: \(error.localizedDescription)\n", stderr)
                }
                DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) {
                    NSApp.terminate(nil)
                }
            }
        }
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .list, .sound])
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        let info = response.notification.request.content.userInfo
        let target = (info["openTarget"] as? String)
            ?? UserDefaults.standard.string(forKey: defaultsKey)
            ?? Self.defaultOpenTarget()
        if let url = Self.makeURL(target) {
            NSWorkspace.shared.open(url)
        }
        completionHandler()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
            NSApp.terminate(nil)
        }
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
