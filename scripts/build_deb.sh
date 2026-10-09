#!/usr/bin/env bash
# Build the .deb package from the checkout, into dist/tai_<version>_all.deb.
#
# The package carries everything `tai` needs under /usr/lib/tai (the Python
# package, both shell plugins, the installer), a /usr/bin/tai wrapper, and a
# postinst that prints the two `source` lines the shell integration needs.
# Nothing outside those paths is touched, so the .deb and a source checkout
# can coexist on one machine, and `dpkg -r tai` removes them cleanly.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION="$(tr -d '[:space:]' < VERSION)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

install -d "$STAGE/DEBIAN" \
           "$STAGE/usr/bin" \
           "$STAGE/usr/lib/tai/tai" \
           "$STAGE/usr/lib/tai/plugins" \
           "$STAGE/usr/share/doc/tai"

# The Python package and the plugins, without test or scratch droppings.
find tai -name '*.py' -not -path '*__pycache__*' -exec install -D -m 644 \
    --target-directory="$STAGE/usr/lib/tai/tai" {} +
install -D -m 644 plugins/tai.zsh plugins/tai.bash \
    --target-directory="$STAGE/usr/lib/tai/plugins"
install -D -m 644 plugins/zsh/*.zsh --target-directory="$STAGE/usr/lib/tai/plugins/zsh"
install -D -m 644 plugins/bash/*.bash --target-directory="$STAGE/usr/lib/tai/plugins/bash"
install -D -m 755 install.sh "$STAGE/usr/lib/tai/install.sh"
install -D -m 644 readme.md VERSION "$STAGE/usr/lib/tai"
install -D -m 644 readme.md "$STAGE/usr/share/doc/tai/readme.md"

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

cat > "$STAGE/usr/bin/tai" <<'EOF'
#!/usr/bin/env bash
exec python3 -S -E /usr/lib/tai/tai/cli.py "$@"
EOF
chmod 755 "$STAGE/usr/bin/tai"

cat > "$STAGE/DEBIAN/control" <<EOF
Package: tai
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.8)
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
dpkg-deb --root-owner-group --build "$STAGE" "dist/tai_${VERSION}_all.deb"
echo "built dist/tai_${VERSION}_all.deb"
