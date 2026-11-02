#!/bin/sh
# builds vendor/adi/libadi_native.so for a linux host, x86_64 or aarch64
set -eu

here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "$here/../.." && pwd)"
out="$root/vendor/adi"
mkdir -p "$out"

"${CXX:-g++}" -shared -fPIC -O2 -fno-exceptions \
    -o "$out/libadi_native.so" \
    "$here/adi_native.cpp" "$here/elfloader.cpp" "$here/platform_linux.cpp" \
    -static-libgcc -static-libstdc++

echo "built $out/libadi_native.so"
