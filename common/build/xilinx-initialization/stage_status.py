#!/usr/bin/env python3
"""Read-only artifact provenance snapshots; never launch a build or extract files.

Only archive headers/manifests are read. This is deliberately not an archive
integrity verification or a claim that current source code has been built.
"""
import argparse
import json
import tarfile
from pathlib import Path, PurePosixPath

from artifact import ROOT, SCHEMA, safe_member_name, sha256_file
from openamp_contract import contract_digest, load_contract


def newest(directory, pattern):
    matches = [p for p in directory.glob(pattern) if p.is_file()]
    return max(matches, key=lambda p: (p.stat().st_mtime_ns, p.name), default=None)


def manifest(path, stage, product, target, machine):
    # Our writer puts the manifest first. Streaming avoids decompressing a
    # multi-GB Yocto payload merely to display its provenance.
    with tarfile.open(path, 'r|gz') as archive:
        for member in archive:
            if member.name == ROOT.rstrip('/') and member.isdir():
                continue
            if member.name != ROOT + 'manifest.json' or not member.isfile():
                raise ValueError('artifact does not begin with its manifest')
            if member.size > 4 * 1024 * 1024:
                raise ValueError('artifact manifest exceeds 4 MiB')
            data = json.load(archive.extractfile(member))
            break
        else:
            raise ValueError('artifact manifest is missing')
    if not isinstance(data, dict) or data.get('schema') != SCHEMA:
        raise ValueError('unsupported artifact schema')
    if data.get('stage') != stage or data.get('product') != product:
        raise ValueError('artifact stage/product mismatch')
    metadata = data.get('metadata')
    files = data.get('files')
    if not isinstance(metadata, dict) or not isinstance(files, dict):
        raise ValueError('artifact metadata/files map is missing')
    for key, value in (('build_target', target), ('machine', machine)):
        if (target or key == 'build_target') and value and metadata.get(key) != value:
            raise ValueError(f'artifact {key} mismatch: expected {value}')
    for name, digest in files.items():
        if PurePosixPath(name).is_absolute():
            raise ValueError(f'absolute payload path: {name}')
        safe_member_name(ROOT + 'payload/' + name)
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError(f'invalid payload digest: {name}')
    return data


class Snapshot:
    def __init__(self, args):
        self.args = args
        self.prefix = args.stage.upper() + '_STATUS_'
        self.issues = []
        self.cache = {}

    def emit(self, key, value):
        print(self.prefix + key + '=' + str(value).replace('\n', ' ').replace('\r', ' '))

    def artifact(self, stage):
        if stage not in self.cache:
            path = newest(self.args.bin_dir, f'{self.args.product}_{stage}_*.tar.gz')
            data = None
            if path is None:
                self.issues.append(f'{stage} artifact missing')
            else:
                try:
                    data = manifest(path, stage, self.args.product,
                                    self.args.target, self.args.machine)
                except (ValueError, OSError, tarfile.TarError, EOFError) as exc:
                    self.issues.append(f'{stage} artifact invalid: {exc}')
            self.cache[stage] = (path, data)
        return self.cache[stage]

    def compare(self, metadata, key, actual, label):
        expected = metadata.get(key)
        result = 'matches' if expected and actual and expected == actual else (
            'unknown (digest/input missing)' if not expected or not actual else 'mismatch')
        self.emit(label, result)
        if result != 'matches':
            self.issues.append(f'{label.lower()}: {result}')

    def local_file(self, label, path, expected=None):
        exists = path.is_file()
        self.emit(label, f'{path} ({"present" if exists else "missing"})')
        if not exists:
            self.issues.append(f'{label.lower()} missing')
        elif expected and sha256_file(path) != expected:
            self.issues.append(f'{label.lower()} differs from packaged output')
            self.emit(label + '_PACKAGED', 'mismatch')

    def run(self):
        a = self.args
        self.emit('TARGET', a.target or a.product)
        self.emit('MACHINE', a.machine)
        path, data = self.artifact(a.stage)
        self.emit('ARTIFACT', path or 'missing')
        if data:
            meta, files = data['metadata'], data['files']
            self.emit('CREATED_UTC', data.get('created_utc', 'unknown'))
            self.emit('PAYLOAD_FILES', len(files))
            if not files:
                self.issues.append('artifact payload manifest empty')
            self.compare(meta, 'xsa_sha256', sha256_file(a.xsa) if a.xsa.is_file() else None, 'XSA')
            if a.contract:
                digest = contract_digest(load_contract(a.contract))
                self.compare(meta, 'openamp_contract_sha256', digest, 'CONTRACT')
            dependencies = {'rpu': [] if a.contract else ['mconf'],
                            'mconf': ['pl_sdtgen'], 'yocto': ['rpu', 'mconf']}[a.stage]
            for dep in dependencies:
                dep_path, dep_data = self.artifact(dep)
                self.emit(dep.upper() + '_ARTIFACT', dep_path or 'missing')
                self.compare(meta, dep + '_sha256',
                             sha256_file(dep_path) if dep_data else None,
                             dep.upper() + '_INPUT')
                if dep_data:
                    self.compare(meta, 'xsa_sha256', dep_data['metadata'].get('xsa_sha256'),
                                 dep.upper() + '_XSA')
                    if a.contract and dep in ('rpu', 'mconf'):
                        self.compare(meta, 'openamp_contract_sha256',
                                     dep_data['metadata'].get('openamp_contract_sha256'),
                                     dep.upper() + '_CONTRACT')
            if a.stage == 'rpu':
                for core in ('R5c0', 'R5c1'):
                    name = core + '.elf'
                    if name not in files:
                        self.issues.append(f'{name} absent from artifact manifest')
                    self.local_file(core.upper() + '_ELF', a.bin_dir / name, files.get(name))
            elif a.stage == 'mconf':
                name = f'yocto-conf/machine/{a.machine}.conf'
                if name not in files:
                    self.issues.append('machine configuration absent from artifact manifest')
                self.emit('BUILD_DIR', a.yocto_build)
                self.emit('MACHINE_CONF', a.yocto_build / f'conf/machine/{a.machine}.conf')
                checked, missing, changed = 0, 0, 0
                for name, digest in files.items():
                    if name.startswith('yocto-conf/'):
                        local = a.yocto_build / 'conf' / name.removeprefix('yocto-conf/')
                        checked += 1
                        if not local.is_file():
                            missing += 1
                        elif sha256_file(local) != digest:
                            changed += 1
                self.emit('INSTALLED_CONFIG', f'{checked} files; {missing} missing; {changed} changed')
                if missing or changed:
                    self.issues.append('installed configuration differs from packaged output')
            else:
                self.emit('IMAGE_TARGET', meta.get('image_target', 'unknown'))
                self.emit('DEPLOY_DIR', a.yocto_build / 'tmp/deploy/images' / a.machine)
        self.emit('SCOPE', 'artifact metadata and input compatibility; payload integrity and source freshness not checked')
        self.emit('VERDICT', '; '.join(self.issues) if self.issues else 'packaged outputs present; recorded inputs match')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', required=True, choices=['rpu', 'mconf', 'yocto'])
    parser.add_argument('--product', required=True)
    parser.add_argument('--target', default='')
    parser.add_argument('--machine', required=True)
    for name in ('bin-dir', 'xsa', 'yocto-build'):
        parser.add_argument('--' + name, required=True, type=Path)
    parser.add_argument('--contract', type=Path)
    args = parser.parse_args()
    try:
        Snapshot(args).run()
    except (ValueError, OSError, tarfile.TarError, EOFError) as exc:
        print(f'{args.stage.upper()}_STATUS_ERROR={exc}')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
