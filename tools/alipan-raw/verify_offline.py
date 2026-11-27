"""Exercise the actual backup and multipart code with synthetic bytes and an in-memory drive."""
import hashlib
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

import backup
import pan_direct


class Interrupted(Exception):
    pass


class MemoryDrive(pan_direct.Drive):
    def __init__(self, account, expected_drive):
        self.account = account
        self.drive = expected_drive
        self.session = self

    def close(self):
        pass

    def put(self, url, *, data, headers, **kwargs):
        file_id, number = urlsplit(url).path.strip('/').split('/')
        number = int(number)
        parts = self.objects[file_id]['chunks']
        if self.interrupt and number == 2:
            type(self).interrupt = False
            raise Interrupted()
        assert number not in parts, 'Acknowledged part transmitted twice'
        parts[number] = data
        return SimpleNamespace(status_code=200)

    def find(self, path):
        return next((obj for obj in self.objects.values() if obj['path'] == path), None)

    def api(self, action, data, *, proof_fd=None):
        if action == 'list':
            return {'items': [v for v in self.objects.values()
                              if v.get('parent') == data['parent_file_id']]}
        if action == 'create':
            parent = data['parent_file_id']
            dest = self.objects[parent]['path'].rstrip('/') + '/' + data['name']
            assert self.find(dest) is None
            file_id = str(len(self.objects))
            obj = dict(path=dest, parent=parent, type=data['type'], file_id=file_id)
            self.objects[file_id] = obj
            if data['type'] == 'file':
                obj.update(status='uploading', chunks={}, expected=data['content_hash'])
            return dict(file_id=file_id, upload_id=file_id)
        obj = self.objects[data['file_id']]
        if action == 'get':
            return obj
        if action == 'listUploadedParts':
            return {'uploaded_parts': [dict(part_number=n, part_size=len(v))
                                       for n, v in obj['chunks'].items()]}
        if action == 'getUploadUrl':
            return {'part_info_list': [dict(i, upload_url='https://example.invalid/'
                         + obj['file_id'] + '/' + str(i['part_number'])) for i in data['part_info_list']]}
        if action == 'complete':
            blob = b''.join(v for _, v in sorted(obj['chunks'].items()))
            digest = hashlib.sha1(blob).hexdigest()
            assert digest.upper() == obj['expected']
            obj.update(status='available', size=len(blob), content_hash_name='sha1', content_hash=digest, data=blob)
            return obj
        if action == 'update':
            path = self.objects[obj['parent']]['path'].rstrip('/') + '/' + data['name']
            assert self.find(path) is None
            obj['path'] = path
            return obj
        raise AssertionError(action)


def check(directory):
    root = Path(directory)
    source = root/'fixture.img'
    content = (bytes(range(251)) * 11000)[:2*1024**2 + 377]
    source.write_bytes(content)
    profile = root/'profile'
    profile.mkdir()
    state = root/'job'
    args = SimpleNamespace(state_dir=state, source=source, config_dir=profile, cli=sys.executable,
                           allow_live_device=False, destination='/offline/job', volume_mib=1, workers=1)
    MemoryDrive.objects = {'root': dict(path='/', type='folder', file_id='root')}
    MemoryDrive.interrupt = True
    with (patch.object(backup, 'Drive', MemoryDrive), patch.object(pan_direct, 'Drive', MemoryDrive),
          patch.object(pan_direct, 'BLOCK', 128*1024),
          patch.object(backup.UploadAccount, 'identity', return_value=('test-drive', 'offline-fixture'))):
        try:
            backup.run(args)
        except Interrupted:
            pass
        else:
            raise AssertionError('Expected the simulated interruption')
        assert len(list((state/'uploads').glob('*.json'))) == 1
        args.workers = 1
        backup.run(args)
        def get(name):
            return next(obj['data'] for obj in MemoryDrive.objects.values()
                        if obj['path'] == args.destination+'/'+name)
        sha_manifest = get('SHA256SUMS').decode().splitlines()
        volumes = []
        assert len(sha_manifest) == 3
        for i, line in enumerate(sha_manifest):
            digest, name = line.split('  ')
            assert name == backup.volume_name(i)
            volume = get(name)
            assert hashlib.sha256(volume).hexdigest() == digest
            volumes.append(volume)
        assert b''.join(volumes) == content and len(volumes[-1]) == 377
        assert get('IMAGE.sha256').decode() == hashlib.sha256(content).hexdigest()+'  disk.raw\n'
        before = len(MemoryDrive.objects)
        backup.run(args)
        assert len(MemoryDrive.objects) == before
        args.state_dir = root/'different-job'
        try:
            backup.run(args)
        except ValueError as exc:
            assert 'empty' in str(exc)
        else:
            raise AssertionError('Existing destination reused by a fresh job')
        args.state_dir = state
        args.volume_mib = 2
        try:
            backup.run(args)
        except ValueError as exc:
            assert 'differs' in str(exc)
        else:
            raise AssertionError('Mismatching resume settings accepted')
    print('PASS fresh job, interrupted multipart, resume, partial final volume, manifests and conflicts')


if __name__ == '__main__':
    def deny_network(event, args):
        if event in ('socket.connect', 'socket.connect_ex', 'socket.getaddrinfo'):
            raise AssertionError('Offline verification attempted network access')
    sys.addaudithook(deny_network)
    with tempfile.TemporaryDirectory() as work:
        check(work)
