import Cocoa
import CoreGraphics
import Foundation

let arguments = CommandLine.arguments
if arguments.count < 2 {
    print("Usage: get_window_id <search_term>")
    exit(1)
}
let searchTerm = arguments[1].lowercased()

let windowListInfo = CGWindowListCopyWindowInfo(.optionOnScreenOnly, kCGNullWindowID) as? [[String: Any]] ?? []
for entry in windowListInfo {
    let owner = (entry[kCGWindowOwnerName as String] as? String ?? "").lowercased()
    let name = (entry[kCGWindowName as String] as? String ?? "").lowercased()
    if owner.contains(searchTerm) || name.contains(searchTerm) {
        if let id = entry[kCGWindowNumber as String] as? Int {
            print(id)
            exit(0)
        }
    }
}
exit(1)
