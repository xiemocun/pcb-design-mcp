// SOFTWARE TEST FIXTURE ONLY. No user presence, no hardware identity, no live approval.
import Foundation
import CryptoKit
let folder=URL(fileURLWithPath:CommandLine.arguments[1]);let raw=try Data(contentsOf:folder.appendingPathComponent("approval_payload.json"));let p=try JSONSerialization.jsonObject(with:raw) as! [String:Any]
func sha(_ d:Data)->String{SHA256.hash(data:d).map{String(format:"%02x",$0)}.joined()}
func json(_ v:Any)throws->Data{try JSONSerialization.data(withJSONObject:v,options:[.sortedKeys,.withoutEscapingSlashes])}
let key=P256.Signing.PrivateKey();let keyID=sha(key.publicKey.x963Representation)
let authority:[String:Any]=["algorithm":"P256_SHA256","key_id":keyID,"public_key_x963_base64":key.publicKey.x963Representation.base64EncodedString(),"test_fixture_only":true]
try json(authority).write(to:folder.appendingPathComponent("test-authority.json"))
let e:[String:Any]=["action":"export_gerber","key_id":keyID,"payload_sha256":sha(raw),"review_id":p["review_id"]!,"signed_at":Int(Date().timeIntervalSince1970)]
let signature=try key.signature(for:Data("PCB-MCP-FABRICATION-APPROVAL-v1\n".utf8)+json(e))
try json(["envelope":e,"signature_der_base64":signature.derRepresentation.base64EncodedString()]).write(to:folder.appendingPathComponent("approval.json"))
print("software-fixture-signed; not a real human approval")
