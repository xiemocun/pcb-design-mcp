import Foundation
import AppKit
import WebKit
import CryptoKit
import Security
@preconcurrency import LocalAuthentication

let domain=Data("PCB-MCP-FABRICATION-APPROVAL-v1\n".utf8)
func digest(_ data:Data)->String { SHA256.hash(data:data).map{String(format:"%02x",$0)}.joined() }
func canonical(_ value:Any)throws->Data {try JSONSerialization.data(withJSONObject:value,options:[.sortedKeys,.withoutEscapingSlashes])}
struct ReviewError:LocalizedError {let message:String;var errorDescription:String?{message}}
func check(_ ok:Bool,_ message:String)throws {if !ok {throw ReviewError(message:message)}}

@MainActor final class Reviewer:NSObject,NSApplicationDelegate,WKNavigationDelegate {
 var folder:URL;var authorityDir:URL;var payload:[String:Any]=[:];var payloadHash="";var window:NSWindow!;var approve:NSButton!;var checkbox:NSButton!
 init(folder:URL,authority:URL){self.folder=folder.standardizedFileURL.resolvingSymlinksInPath();self.authorityDir=authority.standardizedFileURL.resolvingSymlinksInPath()}
 func verifyFiles()throws {
  let raw=try Data(contentsOf:folder.appendingPathComponent("approval_payload.json"));let p=try JSONSerialization.jsonObject(with:raw) as! [String:Any]
  try check(p["schema"] as? String == "pcb-human-review-1" && p["action"] as? String == "export_gerber","审批用途或格式错误。")
  try check((p["expires_at"] as? Double ?? 0)>=Date().timeIntervalSince1970,"审阅已过期，请重新准备。")
  if !payloadHash.isEmpty {try check(digest(raw)==payloadHash,"审阅对象已改变，不能签署。")}
  for (name,hash) in p["files"] as? [String:String] ?? [:] {
   let path=folder.appendingPathComponent(name).standardizedFileURL.resolvingSymlinksInPath()
   try check(path.path.hasPrefix(folder.path+"/") && !name.hasPrefix("/") && !name.split(separator:"/").contains(".."),"文件路径无效。")
   try check(digest(try Data(contentsOf:path))==hash,"文件已改变："+name)
  }
  try check((p["files"] as? [String:String])?["review.html"] != nil,"缺少签署对象中的审阅页面。")
  payload=p;payloadHash=digest(raw)
 }
 func applicationDidFinishLaunching(_ notification:Notification){
  do {try verifyFiles();showWindow()}catch{showError(error);NSApp.terminate(nil)}
 }
 func showWindow(){
  window=NSWindow(contentRect:NSRect(x:0,y:0,width:850,height:820),styleMask:[.titled,.closable,.resizable],backing:.buffered,defer:false)
  window.title="PCB 人工审阅 — 导出文件，不下单";window.center();window.minSize=NSSize(width:650,height:600)
  let content=window.contentView!;let config=WKWebViewConfiguration();config.defaultWebpagePreferences.allowsContentJavaScript=false
  let web=WKWebView(frame:NSRect(x:0,y:100,width:850,height:720),configuration:config);web.autoresizingMask=[.width,.height];web.navigationDelegate=self;web.loadFileURL(folder.appendingPathComponent("review.html"),allowingReadAccessTo:folder);content.addSubview(web)
  checkbox=NSButton(checkboxWithTitle:"我已审阅上述器件、封装、供电假设和未验证事项。",target:self,action:#selector(toggle));checkbox.frame=NSRect(x:20,y:62,width:790,height:24);checkbox.autoresizingMask=[.width,.maxYMargin];content.addSubview(checkbox)
  let label=NSTextField(labelWithString:"签字将经 macOS 身份验证，并绑定这一版本的文件哈希。");label.frame=NSRect(x:20,y:38,width:620,height:20);label.font=NSFont.systemFont(ofSize:12);content.addSubview(label)
  approve=NSButton(title:"同意导出 Gerber / 钻孔",target:self,action:#selector(sign));approve.frame=NSRect(x:590,y:8,width:240,height:30);approve.autoresizingMask=[.minXMargin,.maxYMargin];approve.bezelStyle = .rounded;approve.isEnabled=false;content.addSubview(approve)
  let cancel=NSButton(title:"暂不批准",target:self,action:#selector(close));cancel.frame=NSRect(x:20,y:5,width:120,height:30);cancel.bezelStyle = .rounded;content.addSubview(cancel)
  window.makeKeyAndOrderFront(nil);NSApp.activate(ignoringOtherApps:true)
 }
 func webView(_ webView:WKWebView,decidePolicyFor action:WKNavigationAction,decisionHandler:@escaping (WKNavigationActionPolicy)->Void){
  if action.navigationType == .linkActivated,let url=action.request.url,url.scheme=="https" {NSWorkspace.shared.open(url);decisionHandler(.cancel)}else{decisionHandler(.allow)}
 }
 @objc func toggle(){approve.isEnabled=checkbox.state == .on}
 @objc func close(){
  do {
   let marker:[String:Any]=["review_id":payload["review_id"] ?? "","payload_sha256":payloadHash,"decision":"rejected_or_withdrawn","at":Int(Date().timeIntervalSince1970)]
   try canonical(marker).write(to:folder.appendingPathComponent("rejected.json"),options:.atomic)
   NSApp.terminate(nil)
  }catch{showError(error)}
 }
 func showError(_ error:Error){let alert=NSAlert();alert.messageText="未签署";alert.informativeText=error.localizedDescription;alert.alertStyle = .warning;alert.runModal()}
 @objc func sign(){
  do {try verifyFiles()}catch{showError(error);return}
  let context=LAContext();var error:NSError?
  guard SecureEnclave.isAvailable,context.canEvaluatePolicy(.deviceOwnerAuthentication,error:&error) else {showError(error ?? ReviewError(message:"本机不能提供所需的系统认证/安全隔区，未批准任何导出。") as NSError);return}
  approve.isEnabled=false
  context.evaluatePolicy(.deviceOwnerAuthentication,localizedReason:"批准此固定版本 PCB 的 Gerber/钻孔文件导出；不下单。") { success,error in
   DispatchQueue.main.async {
    guard success else {self.showError(error ?? ReviewError(message:"用户取消了系统认证。"));self.approve.isEnabled=true;return}
    do {try self.completeSignature(context:context)}catch{self.showError(error);self.approve.isEnabled=true}
   }
  }
 }
 func completeSignature(context:LAContext)throws {
  try check(!FileManager.default.fileExists(atPath:folder.appendingPathComponent("rejected.json").path),"该审阅已被拒绝，需要重新准备审阅。")
  try verifyFiles();let fm=FileManager.default;let pub=authorityDir.appendingPathComponent("public.json");let blob=authorityDir.appendingPathComponent("key.enclave")
  try fm.createDirectory(at:authorityDir,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])
  let key:SecureEnclave.P256.Signing.PrivateKey
  if fm.fileExists(atPath:blob.path) {
   try check(fm.fileExists(atPath:pub.path),"审批公钥丢失，拒绝自动替换。")
   key=try SecureEnclave.P256.Signing.PrivateKey(dataRepresentation:Data(contentsOf:blob),authenticationContext:context)
  } else {
   try check(!fm.fileExists(atPath:pub.path),"审批身份不完整，拒绝自动替换。")
   var e:Unmanaged<CFError>?
   guard let acl=SecAccessControlCreateWithFlags(nil,kSecAttrAccessibleWhenUnlockedThisDeviceOnly,[.privateKeyUsage,.userPresence],&e) else {throw e!.takeRetainedValue()}
   key=try SecureEnclave.P256.Signing.PrivateKey(accessControl:acl,authenticationContext:context)
   try key.dataRepresentation.write(to:blob,options:.atomic);try fm.setAttributes([.posixPermissions:0o600],ofItemAtPath:blob.path)
   let data:[String:Any]=["algorithm":"P256_SHA256","key_id":digest(key.publicKey.x963Representation),"public_key_x963_base64":key.publicKey.x963Representation.base64EncodedString(),"private_key_protection":"SecureEnclave userPresence; no plaintext private key"]
   try canonical(data).write(to:pub,options:.atomic)
  }
  let keyID=digest(key.publicKey.x963Representation);let stored=try JSONSerialization.jsonObject(with:Data(contentsOf:pub)) as! [String:Any]
  try check(stored["key_id"] as? String == keyID,"硬件密钥与受信任公钥不匹配。")
  if let expected=payload["authority_key_id"] as? String {try check(expected==keyID,"审阅准备后审批身份已改变。")}
  let envelope:[String:Any]=["action":"export_gerber","key_id":keyID,"payload_sha256":payloadHash,"review_id":payload["review_id"]!,"signed_at":Int(Date().timeIntervalSince1970)]
  let signature=try key.signature(for:domain+canonical(envelope));try verifyFiles()
  let receipt:[String:Any]=["envelope":envelope,"signature_der_base64":signature.derRepresentation.base64EncodedString()]
  try canonical(receipt).write(to:folder.appendingPathComponent("approval.json"),options:.atomic)
  let alert=NSAlert();alert.messageText="此版本已签署";alert.informativeText="签名已保存。MCP 会再次核对版本、文件哈希、有效期与签名，之后才可导出制造文件。本窗口没有下单。";alert.runModal();NSApp.terminate(nil)
 }
 func applicationShouldTerminateAfterLastWindowClosed(_ sender:NSApplication)->Bool {true}
}

@main struct Main {
 @MainActor static func main(){
  if CommandLine.arguments.contains("--probe") {var e:NSError?;let c=LAContext();print("{\"secure_enclave_available\":\(SecureEnclave.isAvailable),\"owner_auth_available\":\(c.canEvaluatePolicy(.deviceOwnerAuthentication,error:&e))}");return}
  func arg(_ name:String)->String? {guard let i=CommandLine.arguments.firstIndex(of:name),i+1<CommandLine.arguments.count else{return nil};return CommandLine.arguments[i+1]}
  var review=arg("--review"),authority=arg("--authority-dir")
  if review==nil,let path=Bundle.main.url(forResource:"review-request",withExtension:"json"),let d=try? Data(contentsOf:path),let obj=try? JSONSerialization.jsonObject(with:d) as? [String:String] {review=obj["review"];authority=obj["authority"]}
  guard let review=review,let authority=authority else{fputs("Use --review DIR --authority-dir DIR, or open a prepared review app.\n",stderr);return}
  let app=NSApplication.shared;app.setActivationPolicy(.regular);let delegate=Reviewer(folder:URL(fileURLWithPath:review),authority:URL(fileURLWithPath:authority));app.delegate=delegate;withExtendedLifetime(delegate){app.run()}
 }
}
