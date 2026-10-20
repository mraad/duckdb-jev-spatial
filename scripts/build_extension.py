"""Build and package the local Rust extension for the installed DuckDB 1.5.6."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys

import duckdb


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', action='store_true')
    args = parser.parse_args()
    if duckdb.__version__ != '1.5.6':
        parser.error('This extension targets DuckDB 1.5.6 exactly')
    root = Path(__file__).resolve().parents[1]
    command = ['cargo', 'build', '--manifest-path', str(root/'native/Cargo.toml'), '--locked', '--offline']
    if args.release:
        command.append('--release')
    subprocess.run(command, check=True)
    platform = duckdb.sql('PRAGMA platform').fetchone()[0]
    name = 'libjev_spatial.dylib' if sys.platform == 'darwin' else 'libjev_spatial.so'
    if sys.platform == 'win32':
        name = 'jev_spatial.dll'
    source = root/'native/target'/('release' if args.release else 'debug')/name
    output = root/'native/dist/jev_spatial.duckdb_extension'
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.tmp')
    shutil.copyfile(source, temporary)
    # DuckDB extension-ci-tools footer: eight 32-byte fields and an empty unsigned signature.
    # https://github.com/duckdb/extension-ci-tools/blob/main/scripts/append_extension_metadata.py
    fields = ['', '', '', 'C_STRUCT_UNSTABLE', '0.1.0', 'v1.5.6', platform, '4']
    with temporary.open('ab') as handle:
        handle.write(b'\x00\x93\x04\x10duckdb_signature\x80\x04')
        for field in fields:
            value = field.encode('ascii')
            if len(value) > 32:
                raise ValueError('Extension metadata field too long')
            handle.write(value.ljust(32, b'\x00'))
        handle.write(b'\x00' * 256)
    temporary.replace(output)
    print(output)


if __name__ == '__main__':
    main()
