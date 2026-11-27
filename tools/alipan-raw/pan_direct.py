"""Upload immutable local files to Aliyun Drive using its authorized Open API."""
import base64
from dataclasses import dataclass
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
from xml.etree import ElementTree

import requests


API = 'https://openapi.alipan.com/adrive/v1.0/openFile/'
BLOCK = 4 * 1024**2


class DriveError(RuntimeError):
    def __init__(self, code, status=0):
        self.code = str(code) if re.fullmatch(r'[\w.-]{1,90}', str(code)) else 'unclassified'
        self.status = status
        super().__init__(f'Aliyun Drive HTTP {status}, {self.code}')


@dataclass(frozen=True)
class UploadAccount:
    state_dir: Path
    config_dir: Path
    cli: str

    def identity(self):
        try:
            with (self.config_dir / 'aliyunpan_config.json').open(encoding='utf-8-sig') as src:
                conf = json.load(src)
            user = next(u for u in conf['userList'] if str(u['userId']) == str(conf['activeUID']))
            drive = user['activeDriveId']
            token = user['openapiToken']['accessToken']
            if not drive or not token:
                raise ValueError()
            return drive, token
        except (OSError, KeyError, StopIteration, ValueError, TypeError):
            raise RuntimeError('Log in with aliyunpan and select the target drive first') from None

    def refresh(self, previous):
        # Serialize refreshes across jobs that use the same CLI account.
        with (self.config_dir / 'raw-image-refresh.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if self.identity()[1] != previous:
                return
            subprocess.run([self.cli, 'ls', '/'], check=True, timeout=120,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL,
                           env=dict(os.environ, ALIYUNPAN_CONFIG_DIR=str(self.config_dir)))


def checked(resp):
    if 200 <= resp.status_code < 300:
        return resp
    try:
        error = resp.json()['code']
    except (ValueError, KeyError, TypeError):
        try:
            error = ElementTree.fromstring(resp.content[:65536]).findtext('Code') or 'HTTPError'
        except ElementTree.ParseError:
            error = 'HTTPError'
    raise DriveError(error, resp.status_code)


class Drive:
    def __init__(self, account, expected_drive):
        self.account = account
        self.drive = expected_drive
        self.session = requests.Session()
        self.session.trust_env = False

    def api(self, action, data, refresh=True, proof_fd=None):
        drive, token = self.account.identity()
        if drive != self.drive:
            raise RuntimeError('Active drive changed during backup')
        data = dict(data, drive_id=drive)
        if proof_fd is not None:
            # Proof v1 must use the same bearer as this request, including after a refresh.
            size = data['size']
            offset = int(hashlib.md5(token.encode()).hexdigest()[:16], 16) % size if size else 0
            data['proof_code'] = base64.b64encode(os.pread(proof_fd, 8, offset)).decode('ascii')
            data['proof_version'] = 'v1'
        try:
            r = self.session.post(API+action, json=data, headers={'Authorization':'Bearer '+token},
                                  allow_redirects=False, timeout=(12, 65))
            return checked(r).json()
        except DriveError as exc:
            if refresh and (exc.status == 401 or exc.code.startswith('AccessToken')):
                self.account.refresh(token)
                return self.api(action, data, refresh=False, proof_fd=proof_fd)
            raise
        except requests.RequestException as exc:
            raise DriveError(type(exc).__name__) from None

    def find(self, path):
        try:
            return self.api('get_by_path', {'file_path':path})
        except DriveError as exc:
            if exc.status == 404 or exc.code in ('NotFound.File','NotFound.FileId','FileNotFound'):
                return None
            raise

    def uploaded(self, target):
        parts = {}
        marker = None
        while True:
            payload = dict(target)
            if marker:
                payload['part_number_marker'] = marker
            data = self.api('listUploadedParts',payload)
            for part in data.get('uploaded_parts') or []:
                parts[int(part['part_number'])] = int(part['part_size'])
            nxt = data.get('next_part_number_marker')
            if not nxt:
                return parts
            if nxt == marker:
                raise RuntimeError('Invalid cloud multipart pagination')
            marker = nxt


def matches(meta, size, digest):
    return bool(meta and meta.get('status') == 'available' and int(meta.get('size', -1)) == size and
                meta.get('content_hash_name','sha1').lower() == 'sha1' and
                str(meta.get('content_hash','')).lower() == digest)


def atomic_state(path, data):
    with tempfile.NamedTemporaryFile('w', dir=path.parent, delete=False) as out:
        json.dump(data,out)
        out.flush()
        os.fsync(out.fileno())
    os.replace(out.name, path)
    sync_dir(path.parent)


def sync_dir(directory):
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def put_part(drive, url, payload, md5_header):
    if urlsplit(url).scheme != 'https':
        raise RuntimeError('The drive did not provide an HTTPS upload URL')
    headers = {'Referer':'https://www.aliyundrive.com/'}
    if md5_header:
        headers['Content-MD5'] = base64.b64encode(hashlib.md5(payload).digest()).decode('ascii')
    try:
        r = drive.session.put(url, data=payload, headers=headers, allow_redirects=False,
                              timeout=(240, 240))
    except requests.RequestException as exc:
        # The transport exception may contain the signed URL.
        raise DriveError(type(exc).__name__) from None
    return checked(r)


def transfer(drive, file_path, state_file, state, parent_id, size, digest):
    num_parts = max(1, math.ceil(size/BLOCK))
    numbers = [{'part_number':n} for n in range(1,num_parts+1)]
    with file_path.open('rb') as src:
        if 'upload_id' not in state:
            created = drive.api('create', {'parent_file_id':parent_id,'name':state['temp_name'],
                'type':'file','size':size,'check_name_mode':'refuse','content_hash':digest.upper(),
                'content_hash_name':'sha1','part_info_list':numbers}, proof_fd=src.fileno())
            if created.get('rapid_upload') is True:
                return created['file_id']
            if created.get('exist') and not created.get('upload_id'):
                raise DriveError('FileAlreadyExist',409)
            state.update(file_id=created['file_id'],upload_id=created['upload_id'],block=BLOCK)
            atomic_state(state_file, state)
        target = {k:state[k] for k in ('file_id','upload_id')}
        if state['block'] != BLOCK:
            raise RuntimeError('Saved upload has a different part size')
        known = drive.uploaded(target)
        # The drive signs the PUT before it receives any per-part digest.
        md5_header = False
        urls = {}
        for number in range(1,num_parts+1):
            offset = (number-1)*BLOCK
            length = min(BLOCK,size-offset)
            if number in known:
                if known[number] != length:
                    raise RuntimeError(f'Cloud part {number} has unexpected length')
                continue
            chunk = os.pread(src.fileno(), length, offset)
            if len(chunk) != length:
                raise RuntimeError('Source file changed during transfer')
            for attempt in range(6):
                if number not in urls:
                    batch = numbers[number-1:number+15]
                    reply = drive.api('getUploadUrl',dict(target,part_info_list=batch))
                    urls.update({int(i['part_number']):i['upload_url'] for i in reply['part_info_list']})
                try:
                    put_part(drive, urls[number], chunk, md5_header)
                    if number == 1:
                        print('First upload part accepted for '+file_path.name, flush=True)
                    break
                except DriveError as exc:
                    if exc.code == 'MissingContentMD5' and not md5_header:
                        md5_header = True
                        continue
                    known = drive.uploaded(target)
                    if known.get(number) == length:
                        break
                    urls.clear()
                    if attempt == 5:
                        raise
                    time.sleep(min(20,2**attempt))
            else:
                raise RuntimeError(f'Upload of part {number} did not finish')
        drive.api('complete',target)
        return target['file_id']


def direct_upload(path, destination, expected_sha1, account, expected_drive, expected_folder):
    """Send one complete staged volume. Return its stable name's cloud file metadata."""
    path = Path(path)
    size = path.stat().st_size
    digest = expected_sha1.lower()
    if not re.fullmatch('[0-9a-f]{40}',digest):
        raise ValueError('Invalid expected SHA-1')
    destination = destination.rstrip('/')
    final_path = destination+'/'+path.name
    key = hashlib.sha256((final_path+'\0'+digest).encode()).hexdigest()
    state_dir = account.state_dir / 'uploads'
    state_dir.mkdir(mode=0o700,exist_ok=True)
    state_file = state_dir/(key+'.json')
    with (state_dir/(key+'.lock')).open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        drive = Drive(account, expected_drive)
        try:
            folder = drive.find(destination)
            if not folder or folder.get('file_id') != expected_folder:
                raise RuntimeError('Destination folder was replaced or moved')
            final = drive.find(final_path)
            if matches(final,size,digest):
                state_file.unlink(missing_ok=True)
                return final
            if final:
                raise RuntimeError('A different file already occupies the requested cloud name')
            def new_state():
                fresh = {'temp_name':'incoming-'+key[:20]+'-'+secrets.token_hex(8)+'.raw'}
                atomic_state(state_file, fresh)
                return fresh
            state = json.loads(state_file.read_text()) if state_file.exists() else new_state()
            pending = drive.find(destination+'/'+state['temp_name'])
            if matches(pending,size,digest):
                temp_id = pending['file_id']
            else:
                if pending and (pending.get('status') == 'available' or
                                state.get('file_id') != pending.get('file_id')):
                    state = new_state()
                try:
                    temp_id = transfer(drive,path,state_file,state,folder['file_id'],size,digest)
                except DriveError as exc:
                    if (exc.status == 409 and 'upload_id' not in state) or exc.code in (
                            'NoSuchUpload','NotFound.UploadId','InvalidUploadId','NotFound.FileId',
                            'AlreadyExist.File','AlreadyExist','FileAlreadyExist','FileAlreadyExists'):
                        new_state()
                    raise
            # Keep every existing cloud object. A concurrent rename must refuse a collision.
            meta = drive.api('get', {'file_id':temp_id})
            if not matches(meta, size, digest):
                raise RuntimeError('Cloud object has not acknowledged the expected digest')
            drive.api('update', {'file_id':temp_id, 'name':path.name, 'check_name_mode':'refuse'})
            published = drive.find(final_path)
            if not matches(published, size, digest):
                raise RuntimeError('Cloud name or digest was not acknowledged')
            state_file.unlink(missing_ok=True)
            return published
        finally:
            drive.session.close()
