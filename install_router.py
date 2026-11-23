"""Installer for the pinned Freerouting 2.4.1 JAR (requires Java 25)."""
import hashlib,json,subprocess,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent
URL='https://github.com/freerouting/freerouting/releases/download/v2.4.1/freerouting-2.4.1.jar'
# Pin established from the official HTTPS release in this acceptance run.
# This is NOT a separate publisher-supplied checksum.
SHA='251101c3eeac22d7e7dfcf6796603279e5d1000283eb82d8f093780f7afc6aa9'

def main():
    dest=ROOT/'vendor';dest.mkdir(exist_ok=True);jar=dest/'freerouting-2.4.1.jar'
    if not jar.exists() or hashlib.sha256(jar.read_bytes()).hexdigest()!=SHA:
        partial=dest/'freerouting-2.4.1.jar.pending'
        subprocess.run(['curl','--fail','--location','--show-error','--max-time','1800','--continue-at','-','--output',str(partial),URL],check=True)
        if hashlib.sha256(partial.read_bytes()).hexdigest()!=SHA:raise SystemExit('Release pin mismatch; JAR not installed. Remove a corrupt .pending file before retrying.')
        with zipfile.ZipFile(partial) as z:
            if z.testzip() is not None:raise SystemExit('Corrupt release archive; JAR not installed.')
        partial.replace(jar)
    record=dict(version='2.4.1',runtime='OpenJDK 25',source=URL,jar_sha256=SHA,digest_basis='Pinned after authenticated HTTPS release download, full ZIP CRC validated; not a publisher-supplied checksum',license='GPL-3.0',source_code='https://github.com/freerouting/freerouting/tree/v2.4.1')
    (dest/'engine-java.json').write_text(json.dumps(record,indent=2));print(json.dumps(record,indent=2))
if __name__=='__main__':main()
