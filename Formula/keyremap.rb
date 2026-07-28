# Homebrew formula — `brew tap fire17/keyremap https://github.com/fire17/keyremap`
# then `brew install keyremap`.
#
# keyremap is pure standard library, so there are no resources to vendor and
# no virtualenv to build: install the package, then a thin launcher.
class Keyremap < Formula
  include Language::Python::Virtualenv

  desc "Remap one keyboard without touching the others, on macOS, Windows and Linux"
  homepage "https://github.com/fire17/keyremap"
  url "https://github.com/fire17/keyremap/archive/refs/tags/v1.0.0.tar.gz"
  version "1.0.0"
  license "MIT"
  head "https://github.com/fire17/keyremap.git", branch: "master"

  depends_on "python@3.12"

  def install
    libexec.install "keyremap", "remap.py", "config.yaml", "install-manifest.txt"
    (bin/"keyremap").write <<~SH
      #!/bin/sh
      exec "#{Formula["python@3.12"].opt_bin}/python3.12" "#{libexec}/remap.py" "$@"
    SH
    chmod 0755, bin/"keyremap"
  end

  def caveats
    <<~EOS
      keyremap needs Karabiner-Elements on macOS:
        brew install --cask karabiner-elements

      Then:
        keyremap doctor     # tells you exactly what is still missing
        keyremap selftest   # proves the install is sound before you trust it
        keyremap apply      # installs AND enables the rule in Karabiner

      Your config lives at #{HOMEBREW_PREFIX}/opt/keyremap/libexec/config.yaml
      Copy it somewhere you keep dotfiles and pass --config, or use
      `keyremap export` / `keyremap import` to move it between machines.
    EOS
  end

  test do
    # the formula is only good if the installed binary actually runs
    assert_match "keyremap", shell_output("#{bin}/keyremap --help")
    assert_match "layers applied", shell_output("#{bin}/keyremap check")
    system bin/"keyremap", "selftest"
  end
end
