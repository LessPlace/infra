@inline(never)
func synchronousCrash() {
    UnsafeMutablePointer<Int>(bitPattern: 0x42)!.pointee = 1
}

@inline(never)
func asynchronousCrash() async {
    await Task.yield()
    UnsafeMutablePointer<Int>(bitPattern: 0x42)!.pointee = 2
}

@inline(__always)
func inlinedCrashLeaf(_ value: Int) -> Int {
    UnsafeMutablePointer<Int>(bitPattern: 0x42)!.pointee = value
    return value
}

@inline(never)
func inlinedCrashOuter(_ value: Int) -> Int {
    inlinedCrashLeaf(value) + 1
}

@main
struct CrashProbe {
    static func main() async {
        switch CommandLine.arguments.dropFirst().first {
        case "async": await asynchronousCrash()
        case "inline": print(inlinedCrashOuter(CommandLine.arguments.count))
        case "fatal": fatalError("Isolated crash-report verification")
        default: synchronousCrash()
        }
    }
}
