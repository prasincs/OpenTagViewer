import CryptoKit
import Foundation
import Darwin

func fail() -> Never { fputs("Secure vault operation failed\n", stderr); exit(1) }
func load(_ path: String) throws -> SecureEnclave.P256.KeyAgreement.PrivateKey {
    let fd = open(path, O_RDONLY | O_NOFOLLOW)
    guard fd >= 0 else { fail() }; defer { close(fd) }
    var info = stat()
    guard fstat(fd, &info) == 0, (info.st_mode & S_IFMT) == S_IFREG,
          info.st_uid == getuid(), info.st_nlink == 1, info.st_mode & 0o077 == 0,
          info.st_size > 0, info.st_size <= 4096 else { fail() }
    let raw = FileHandle(fileDescriptor: fd, closeOnDealloc: false).readData(ofLength: 4097)
    return try SecureEnclave.P256.KeyAgreement.PrivateKey(dataRepresentation: raw)
}
guard CommandLine.arguments.count == 3, SecureEnclave.isAvailable else { fail() }
let command = CommandLine.arguments[1], path = CommandLine.arguments[2]
do {
    let key: SecureEnclave.P256.KeyAgreement.PrivateKey
    if command == "init" {
        guard !FileManager.default.fileExists(atPath: path) else { fail() }
        key = try SecureEnclave.P256.KeyAgreement.PrivateKey()
        let fd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0o600)
        guard fd >= 0 else { fail() }; defer { close(fd) }
        let bytes = key.dataRepresentation
        try bytes.withUnsafeBytes { buffer in
            var offset = 0
            while offset < buffer.count {
                let count = write(fd, buffer.baseAddress!.advanced(by: offset), buffer.count-offset)
                guard count > 0 else { fail() }; offset += count
            }
        }
        guard fsync(fd) == 0 else { fail() }
    } else { key = try load(path) }
    if command == "init" || command == "public" {
        print(key.publicKey.x963Representation.base64EncodedString())
    } else {
        let raw = FileHandle.standardInput.readDataToEndOfFile()
        guard raw.count <= 8192, let value = try JSONSerialization.jsonObject(with: raw) as? [String:String],
              let aad = Data(base64Encoded: value["aad"] ?? "") else { fail() }
        let result: [String:String]
        if command == "seal" {
            guard let plaintext = Data(base64Encoded: value["plaintext"] ?? ""), plaintext.count <= 1024 else { fail() }
            let ephemeral = P256.KeyAgreement.PrivateKey()
            let secret = try ephemeral.sharedSecretFromKeyAgreement(with: key.publicKey)
            let symmetric = secret.hkdfDerivedSymmetricKey(using: SHA256.self, salt: Data(), sharedInfo: aad, outputByteCount: 32)
            let sealed = try AES.GCM.seal(plaintext, using: symmetric, authenticating: aad)
            result = ["ephemeral_public": ephemeral.publicKey.x963Representation.base64EncodedString(),
                      "sealed": sealed.combined!.base64EncodedString()]
        } else if command == "open" {
            guard let publicBytes = Data(base64Encoded: value["ephemeral_public"] ?? ""),
                  let sealedBytes = Data(base64Encoded: value["sealed"] ?? "") else { fail() }
            let peer = try P256.KeyAgreement.PublicKey(x963Representation: publicBytes)
            let secret = try key.sharedSecretFromKeyAgreement(with: peer)
            let symmetric = secret.hkdfDerivedSymmetricKey(using: SHA256.self, salt: Data(), sharedInfo: aad, outputByteCount: 32)
            let box = try AES.GCM.SealedBox(combined: sealedBytes)
            let plaintext = try AES.GCM.open(box, using: symmetric, authenticating: aad)
            result = ["plaintext": plaintext.base64EncodedString()]
        } else { fail() }
        let output = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
        FileHandle.standardOutput.write(output)
    }
} catch { fail() }
