"""Read a Linux block device or regular file and send plain raw volumes to Alipan."""
import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys

from pan_direct import Drive, UploadAccount, direct_upload, sync_dir
from pan_sha256 import PanSHA256


def source_geometry(fd, path, allow_live):
    info = os.fstat(fd)
    identity = {'path': str(path)}
    if stat.S_ISREG(info.st_mode):
        identity.update(device=info.st_dev, inode=info.st_ino, mtime_ns=info.st_mtime_ns)
        size = info.st_size
    elif stat.S_ISBLK(info.st_mode):
        # Linux BLKROGET reports the device's logical read-only flag.
        readonly = int.from_bytes(fcntl.ioctl(fd, 0x125e, bytes(4)), sys.byteorder)
        if not readonly and not allow_live:
            raise ValueError('Writable block device requires --allow-live-device or a read-only snapshot')
        identity['block_device'] = info.st_rdev
        size = os.lseek(fd, 0, os.SEEK_END)
    else:
        raise ValueError('Source must be a regular file or Linux block device')
    if size <= 0:
        raise ValueError('Source is empty')
    return dict(identity, size=size)


def create_folder(drive, destination, starting):
    parent = 'root'
    prefix = ''
    for name in destination.strip('/').split('/'):
        prefix += '/' + name
        meta = drive.find(prefix)
        if not meta:
            drive.api('create', {'parent_file_id': parent, 'name': name, 'type': 'folder',
                                 'check_name_mode': 'refuse'})
            meta = drive.find(prefix)
        if not meta or meta.get('type') != 'folder':
            raise ValueError('Destination path contains an unavailable folder')
        parent = meta['file_id']
    if starting:
        listing = drive.api('list', {'parent_file_id': parent, 'limit': 1})
        if listing['items']:
            raise ValueError('A new backup requires a dedicated empty destination directory')
    return parent


def volume_name(index):
    return f'disk.raw.part{index:08d}'


def stage_volume(fd, where, offset, length, whole):
    sha1, sha256 = hashlib.sha1(), hashlib.sha256()
    temp = where.with_name(where.name + '.partial')
    with temp.open('wb') as out:
        end = offset + length
        while offset < end:
            buf = os.pread(fd, min(1024**2, end - offset), offset)
            if not buf:
                raise OSError('Short source read')
            out.write(buf)
            whole.update(buf)
            sha1.update(buf)
            sha256.update(buf)
            offset += len(buf)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temp, where)
    sync_dir(where.parent)
    return sha1.hexdigest(), sha256.hexdigest(), json.dumps(whole.checkpoint())


def send_image(fd, conf, state_dir, db, upload, workers):
    size, unit = conf['source']['size'], conf['volume_bytes']
    count = (size + unit - 1) // unit
    if count > 100_000_000:
        raise ValueError('Choose a larger volume size to fit eight-digit part numbering')
    spool = state_dir / 'spool'
    spool.mkdir(mode=0o700, exist_ok=True)
    rows = db.execute('SELECT idx, sha1, sent FROM parts ORDER BY idx').fetchall()
    if len(rows) > count or any(i != row[0] for i, row in enumerate(rows)):
        raise RuntimeError('Checkpoint table is inconsistent')
    for i, _, sent in rows:
        if sent:
            (spool / volume_name(i)).unlink(missing_ok=True)
    next_index = len(rows)
    waiting = iter((i, digest) for i, digest, sent in rows if not sent)
    whole = PanSHA256()
    if next_index:
        snap = db.execute('SELECT ctx FROM parts WHERE idx=?', (next_index-1,)).fetchone()[0]
        whole = PanSHA256.restore(json.loads(snap), min(next_index*unit, size))
    jobs = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while True:
            while len(jobs) < workers:
                row = next(waiting, None)
                if row is not None:
                    i, sha1 = row
                    path = spool / volume_name(i)
                    if not path.is_file() or path.stat().st_size != min(unit, size - i*unit):
                        raise RuntimeError('An unacknowledged spool volume is missing or truncated')
                    with path.open('rb') as retained:
                        if hashlib.file_digest(retained, 'sha1').hexdigest() != sha1:
                            raise RuntimeError('An unacknowledged spool volume changed')
                elif next_index < count:
                    i = next_index
                    path = spool / volume_name(i)
                    length = min(unit, size - i*unit)
                    sha1, sha256, snap = stage_volume(fd, path, i*unit, length, whole)
                    with db:
                        db.execute('INSERT INTO parts VALUES(?,?,?,?,0)', (i, sha1, sha256, snap))
                    next_index += 1
                else:
                    break
                jobs[pool.submit(upload, path, sha1)] = (i, path)
            if not jobs:
                break
            ready, _ = wait(jobs, return_when=FIRST_COMPLETED)
            for fut in ready:
                i, path = jobs.pop(fut)
                fut.result()
                with db:
                    db.execute('UPDATE parts SET sent=1 WHERE idx=?', (i,))
                path.unlink(missing_ok=True)
                print(f'Cloud accepted volume {i+1}/{count}', flush=True)
    if stat.S_ISREG(os.fstat(fd).st_mode):
        if os.fstat(fd).st_mtime_ns != conf['source']['mtime_ns']:
            raise RuntimeError('Source file changed. Use an immutable source and a new job')
    sums = ''.join(f'{digest}  {volume_name(i)}\n' for i, digest in
                   db.execute('SELECT idx,sha256 FROM parts ORDER BY idx'))
    for name, data in [('SHA256SUMS', sums), ('IMAGE.sha256', f'{whole.hexdigest()}  disk.raw\n')]:
        path = state_dir / name
        raw = data.encode('ascii')
        path.write_bytes(raw)
        upload(path, hashlib.sha1(raw).hexdigest())
    with db:
        db.execute("INSERT OR REPLACE INTO job VALUES('complete','true')")
    print('All volumes and both manifests accepted', flush=True)


def private_state_directory(state):
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    if state.stat().st_uid != os.getuid() or state.stat().st_mode & 0o077:
        raise ValueError('The job directory must be private to the current account (mode 0700)')


def run(args):
    state = Path(args.state_dir).expanduser().resolve()
    source = Path(args.source).expanduser().resolve(strict=True)
    config_dir = Path(args.config_dir).expanduser().resolve(strict=True)
    if state == config_dir or state.is_relative_to(config_dir) or config_dir.is_relative_to(state):
        raise ValueError('Keep the CLI profile and job state in separate directories')
    if source.is_relative_to(state):
        raise ValueError('Source cannot be inside the job directory')
    private_state_directory(state)
    cli = shutil.which(args.cli)
    if not cli:
        raise ValueError('Cannot find the authorized aliyunpan executable')
    account = UploadAccount(state, config_dir, cli)
    drive_id, _ = account.identity()
    with (state/'job.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # O_NONBLOCK ensures accidentally naming a FIFO cannot hang this check.
        fd = os.open(source, os.O_RDONLY | os.O_NONBLOCK)
        try:
            source_info = source_geometry(fd, source, args.allow_live_device)
            conf = {'version': 1, 'source': source_info, 'drive': drive_id,
                    'destination': args.destination, 'volume_bytes': args.volume_mib*1024**2}
            with closing(sqlite3.connect(state/'job.sqlite')) as db:
                db.executescript('''
                    CREATE TABLE IF NOT EXISTS job (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS parts (idx INTEGER PRIMARY KEY, sha1 TEXT NOT NULL,
                        sha256 TEXT NOT NULL, ctx TEXT NOT NULL, sent INTEGER NOT NULL);
                ''')
                row = db.execute("SELECT value FROM job WHERE key='definition'").fetchone()
                expected = json.loads(row[0]) if row else None
                if expected and any(expected.get(k) != v for k, v in conf.items()):
                    raise ValueError('Job differs from the saved source, drive, destination or volume size')
                if db.execute("SELECT 1 FROM job WHERE key='complete'").fetchone():
                    print('Backup already complete')
                    return
                drive = Drive(account, drive_id)
                try:
                    folder_id = create_folder(drive, args.destination, starting=expected is None)
                finally:
                    drive.session.close()
                if expected and expected.get('folder') != folder_id:
                    raise ValueError('Destination folder was replaced')
                conf['folder'] = folder_id
                with db:
                    db.execute("INSERT OR REPLACE INTO job VALUES('definition',?)", (json.dumps(conf),))
                def upload(path, digest):
                    return direct_upload(path, args.destination, digest, account, drive_id, folder_id)
                send_image(fd, conf, state, db, upload, args.workers)
        finally:
            os.close(fd)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', required=True)
    p.add_argument('--destination', required=True, help='Absolute path to a dedicated cloud folder')
    p.add_argument('--state-dir', required=True)
    p.add_argument('--config-dir', required=True, help='CLI directory used during login')
    p.add_argument('--cli', default='aliyunpan')
    p.add_argument('--volume-mib', type=int, default=512)
    p.add_argument('--workers', type=int, choices=[1, 2, 3], default=3)
    p.add_argument('--allow-live-device', action='store_true')
    args = p.parse_args()
    if not all(value.strip() for value in (args.source, args.state_dir, args.config_dir)):
        p.error('source, state-dir and config-dir must be explicitly set')
    if not 1 <= args.volume_mib <= 4096:
        p.error('volume-mib must be in [1, 4096]')
    path = args.destination
    if (not path.startswith('/') or any(item in ('', '.', '..') for item in path[1:].split('/'))
            or re.search(r'[\\\x00-\x1f\x7f]', path)):
        p.error('destination must be an absolute cloud folder path, without empty or relative components')
    return args


if __name__ == '__main__':
    os.umask(0o077)
    try:
        run(parse_args())
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        sys.exit(str(exc))
