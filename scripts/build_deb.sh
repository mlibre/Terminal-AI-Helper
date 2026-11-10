#!/usr/bin/env bash
# Build the .deb package from the checkout, into dist/tai_<version>_all.deb.
#
# The package carries the compiled binary in /usr/bin/tai, the shell plugins
# and the installer under /usr/lib/tai, and a postinst that prints the two
# `source` lines the shell integration needs. Nothing outside those paths is
# touched, so the .deb and a source checkout can coexist on one machine, and
# `dpkg -r tai` removes them cleanly. The architecture is the machine this
# builds on: the binary is compiled here, so the package is no longer "all".
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(tr -d '[:space:]' < VERSION)"
ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# The toolchain is where rustup puts it when the shell has not been handed it.
command -v cargo >/dev/null 2>&1 || export PATH="$HOME/.cargo/bin:$PATH"

echo "→ building the release binary…"
cargo build --release --quiet

install -d "$STAGE/DEBIAN" \
           "$STAGE/usr/bin" \
           "$STAGE/usr/lib/tai/plugins/zsh" \
           "$STAGE/usr/lib/tai/plugins/bash" \
           "$STAGE/usr/share/doc/tai"

install -m 755 target/release/tai "$STAGE/usr/bin/tai"
install -m 644 plugins/tai.zsh plugins/tai.bash \
    --target-directory="$STAGE/usr/lib/tai/plugins"
install -m 644 plugins/zsh/*.zsh --target-directory="$STAGE/usr/lib/tai/plugins/zsh"
install -m 644 plugins/bash/*.bash --target-directory="$STAGE/usr/lib/tai/plugins/bash"
install -m 755 install.sh "$STAGE/usr/lib/tai/install.sh"
install -m 644 readme.md VERSION "$STAGE/usr/lib/tai"
install -m 644 readme.md "$STAGE/usr/share/doc/tai/readme.md"

cat > "$STAGE/usr/share/doc/tai/copyright" <<EOF
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: tai
Source: https://github.com/mlibre/Terminal-AI-Helper
EOF

cat > "$STAGE/usr/share/doc/tai/README.Debian" <<EOF
tai ${VERSION}

After installing, enable the shell integration by adding one line to your
shell's rc file and restarting the shell:

  zsh:   source /usr/lib/tai/plugins/tai.zsh
  bash:  source /usr/lib/tai/plugins/tai.bash

Then run 'tai refresh' once to learn from your history and build the index.
EOF

cat > "$STAGE/DEBIAN/control" <<EOF
Package: tai
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: ${ARCH}
Depends: libc6 (>= 2.17)
Recommends: bash, zsh
Maintainer: mlibre <m.gh@linuxmail.org>
Homepage: https://github.com/mlibre/Terminal-AI-Helper
Description: learns the commands you run and suggests them as you type
 Terminal-AI-Helper (tai) records the commands you actually run, ranks
 them by frequency, recency and context, and suggests the next command
 as you type — in bash and zsh, entirely offline, with no daemon.
EOF

cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
# Print, never write: the rc files belong to the user, and an install that
# edited them silently would not be an upgrade people can reason about.
cat <<'MSG'
tai: to enable the shell integration, add one line to your rc file:
tai:   zsh:   source /usr/lib/tai/plugins/tai.zsh
tai:   bash:  source /usr/lib/tai/plugins/tai.bash
tai: then run 'tai refresh' once, and restart your shell.
MSG
EOF
chmod 755 "$STAGE/DEBIAN/postinst"

chmod 755 "$STAGE"
mkdir -p dist
dpkg-deb --root-owner-group --build "$STAGE" "dist/tai_${VERSION}_${ARCH}.deb"
echo "built dist/tai_${VERSION}_${ARCH}.deb"
