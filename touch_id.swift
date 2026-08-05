import LocalAuthentication
import Foundation

let context = LAContext()
var error: NSError?
let semaphore = DispatchSemaphore(value: 0)

var reason = "Authenticate to unlock your Android device"
if CommandLine.arguments.count > 1 {
    reason = CommandLine.arguments[1]
}

if context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &error) {
    context.evaluatePolicy(.deviceOwnerAuthentication, localizedReason: reason) { success, authError in
        if success {
            print("SUCCESS")
            exit(0)
        } else {
            if let err = authError as? LAError {
                print("ERROR: \(err.localizedDescription)")
            } else {
                print("FAILED")
            }
            exit(1)
        }
        semaphore.signal()
    }
    _ = semaphore.wait(timeout: .distantFuture)
} else {
    if let err = error {
        print("NOT_AVAILABLE: \(err.localizedDescription)")
    } else {
        print("NOT_AVAILABLE")
    }
    exit(2)
}
