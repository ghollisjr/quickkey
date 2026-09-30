# Maintainer: ghollisjr <ghollisjr@gmail.com>

pkgname=quickkey
pkgver=1.0.0
pkgrel=1
pkgdesc='Hotkey menu of commands whose output goes to the clipboard'
arch=('any')
url='https://github.com/ghollisjr/quickkey'
license=('MIT')
# Arch's python ships _tkinter.so but does NOT depend on tk, and that .so
# links libtk8.6.so -- so without tk here, `import tkinter` fails at runtime.
# namcap reports tk as possibly unneeded; it is wrong, do not drop it.
depends=('python' 'tk')
optdepends=(
  'xclip: clipboard support under X11'
  'xsel: clipboard support under X11, alternative to xclip'
  'wl-clipboard: clipboard support under Wayland'
)
# Built straight from this directory -- there is no release tarball yet.
# Re-run `updpkgsums` after editing any of these.
source=(
  'quickkey.py'
  'quickkey.conf'
  'quickkey.service'
  'README.md'
  'LICENSE'
)
sha256sums=('32adabf39614e7371e91f9b46a5d6e7b5715df13ec36d0a82a5b085ce9a02469'
            'c98a57f230f6334178c09ba6e91cda3af3cb1a557497bb0a49892a04623bc741'
            'e71fac3043b03c2c25a7e1804f7a5bbc4100828a60701cfe190292caa9d93da0'
            '13e08c47437e40875a0e6dc15f89e13a1a7b12b11410dd0be0221075121b4e7b'
            '0fb5c41305e448dc1350b077e889ce87eb2a6b34518266756219499c51ebff73')

check() {
  cd "$srcdir"
  python -c "import ast; ast.parse(open('quickkey.py').read())"
  python quickkey.py --config quickkey.conf --list >/dev/null
}

package() {
  cd "$srcdir"

  install -Dm755 quickkey.py "$pkgdir/usr/bin/$pkgname"

  # a user unit: `systemctl --user enable --now quickkey.service`
  install -Dm644 quickkey.service \
    "$pkgdir/usr/lib/systemd/user/$pkgname.service"

  # last-resort config, so a fresh install has something in the menu; copy it
  # to ~/.config/quickkey/config to make it yours
  install -Dm644 quickkey.conf "$pkgdir/usr/share/$pkgname/quickkey.conf"

  install -Dm644 README.md "$pkgdir/usr/share/doc/$pkgname/README.md"
  install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
}
