import AppKit
import Foundation
import UserNotifications

final class AppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    private let defaultsKey = "latestDuePage"

    func applicationDidFinishLaunching(_ notification: Notification) {
        UNUserNotificationCenter.current().delegate = self
        let args = Array(CommandLine.arguments.dropFirst())

        if args.count >= 2 {
            let title = args[0]
            let body = args[1]
            let openPath = args.count >= 3 ? args[2] : Self.defaultPagePath()
            UserDefaults.standard.set(openPath, forKey: defaultsKey)
            postNotification(title: title, body: body, openPath: openPath)
        } else {
            openLatestPage()
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.4) {
                NSApp.terminate(nil)
            }
        }
    }

    private static func defaultPagePath() -> String {
        NSString(string: "~/.local/state/usyd-due-reminders/dues.html").expandingTildeInPath
    }

    private func openLatestPage() {
        let path = UserDefaults.standard.string(forKey: defaultsKey) ?? Self.defaultPagePath()
        let url = URL(fileURLWithPath: path)
        if FileManager.default.fileExists(atPath: path) {
            NSWorkspace.shared.open(url)
        } else {
            fputs("No due page at \(path)\n", stderr)
        }
    }

    private func postNotification(title: String, body: String, openPath: String) {
        let center = UNUserNotificationCenter.current()
        center.requestAuthorization(options: [.alert, .sound, .badge]) { granted, error in
            if let error = error {
                fputs("auth error: \(error.localizedDescription)\n", stderr)
                NSApp.terminate(nil)
                return
            }
            if !granted {
                fputs(
                    "notification permission not granted — enable UsydDueReminders in System Settings → Notifications\n",
                    stderr
                )
                // Still open the page so the user sees details.
                DispatchQueue.main.async {
                    NSWorkspace.shared.open(URL(fileURLWithPath: openPath))
                    NSApp.terminate(nil)
                }
                return
            }

            let content = UNMutableNotificationContent()
            content.title = title
            content.body = body
            content.sound = .default
            content.userInfo = ["openPath": openPath]
            // Help the system associate clicks with this app.
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
                // Keep the process alive briefly so an immediate click can be handled;
                // later clicks relaunch the app with no args and open the saved page.
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
        let path = (info["openPath"] as? String)
            ?? UserDefaults.standard.string(forKey: defaultsKey)
            ?? Self.defaultPagePath()
        NSWorkspace.shared.open(URL(fileURLWithPath: path))
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
