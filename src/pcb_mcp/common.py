import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import uuid


class DomainError(Exception):
    def __init__(self,code,message,**details):
        self.payload=dict(code=code,message=message,retryable=False,**details)
        super().__init__(json.dumps(self.payload,ensure_ascii=False))


def require(condition,code,message,**details):
    if not condition:raise DomainError(code,message,**details)


def new_id():return uuid.uuid4().hex


def request_fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def create_project_id(key):
    return request_fingerprint(['project_create', key])[:32]


def fork_project_id(parent, key):
    return request_fingerprint(['project_fork', parent, key])[:32]


def publish_initial_project(folder, state):
    # A killed service must not expose a half-written project to a reconnect.
    with TemporaryDirectory(prefix='.project-stage-', dir=folder.parent) as temporary:
        staging = Path(temporary)/folder.name
        (staging/'builds').mkdir(parents=True)
        write_json(staging/'state.json', state)
        if folder.exists() or folder.is_symlink():
            raise FileExistsError(folder)
        staging.rename(folder)


PCB_REVIEW_RECEIPT = 'review-receipt.json'


def file_snapshot(folder):
    result = {}
    for path in sorted(folder.rglob('*')):
        if path.is_file():
            with path.open('rb') as stream:
                result[path.relative_to(folder).as_posix()] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return result


def write_json(path,value):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2));temp.replace(path)
