Name:           watermark-overlay
Version:        0.5.0
Release:        1%{?dist}
Summary:        Full-screen anti-leak watermark overlaying the logged-in user's name
License:        Proprietary
URL:            https://github.com/romprs/data2
BuildArch:      noarch

Source0:        %{name}-%{version}.tar.gz

Requires:       python3
Requires:       python3-pip
Requires(post): shadow-utils

%description
Draws a full-screen, click-through, always-on-top watermark tiling the
current OS user's name across every connected display, so screenshots,
phone photos of the screen, and (in a later module) printed pages carry
visible attribution without interfering with normal work -- clicks and
keystrokes pass straight through to whatever is underneath.

Installs system-wide via XDG autostart (/etc/xdg/autostart): a single
`dnf install` on the machine makes the watermark appear automatically
for every user who logs into a graphical session, no per-user setup.
Text, opacity and the other visual settings -- including whether the
watermark always shows or only while a configured list of applications
is open, and whether it renders free text or the OS account identity --
are controlled centrally via /etc/watermark-overlay/config.json and
apply live (no restart) to every running instance.

%prep
%setup -q

%install
rm -rf %{buildroot}
install -Dm755 watermark_overlay.py %{buildroot}%{_datadir}/watermark-overlay/watermark_overlay.py
install -Dm644 dotcode.py %{buildroot}%{_datadir}/watermark-overlay/dotcode.py
install -Dm755 decode_dots.py %{buildroot}%{_datadir}/watermark-overlay/decode_dots.py
install -Dm755 watermark-overlay.wrapper %{buildroot}%{_bindir}/watermark-overlay
install -Dm755 watermark-decode.wrapper %{buildroot}%{_bindir}/watermark-decode
install -Dm644 watermark-overlay.desktop %{buildroot}%{_sysconfdir}/xdg/autostart/watermark-overlay.desktop
install -Dm644 config.json %{buildroot}%{_sysconfdir}/watermark-overlay/config.json

%post
if ! python3 -c "import PyQt5, Xlib" >/dev/null 2>&1; then
    echo "watermark-overlay: installing Python dependencies (PyQt5, python-xlib)..."
    python3 -m pip install --quiet PyQt5 python-xlib || \
        echo "watermark-overlay: WARNING - could not auto-install PyQt5/python-xlib via pip (no network access at install time?). Install manually, e.g.: dnf install python3-qt5 python3-xlib   -- or --   python3 -m pip install PyQt5 python-xlib" >&2
fi
if ! python3 -c "import cryptography" >/dev/null 2>&1; then
    echo "watermark-overlay: installing Python dependency (cryptography, for watermark_type=dots)..."
    python3 -m pip install --quiet cryptography || \
        echo "watermark-overlay: WARNING - could not auto-install cryptography via pip. watermark_type=dots will not work until you: python3 -m pip install cryptography" >&2
fi
if ! python3 -c "import numpy, PIL" >/dev/null 2>&1; then
    echo "watermark-overlay: installing Python dependencies (numpy, Pillow, for watermark-decode)..."
    python3 -m pip install --quiet numpy Pillow || \
        echo "watermark-overlay: WARNING - could not auto-install numpy/Pillow via pip. 'watermark-decode' will not work until you: python3 -m pip install numpy Pillow" >&2
fi
if [ ! -s %{_sysconfdir}/watermark-overlay/watermark.key ]; then
    echo "watermark-overlay: generating AES-256 key for watermark_type=dots at %{_sysconfdir}/watermark-overlay/watermark.key ..."
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -out %{_sysconfdir}/watermark-overlay/watermark.key 32
    else
        python3 -c "import secrets; open('%{_sysconfdir}/watermark-overlay/watermark.key','wb').write(secrets.token_bytes(32))"
    fi
fi
# The key was previously 0644 (world-readable) because the overlay
# reads it while running as the logged-in user, not root. That let ANY
# local account read it, not just interactively-logging-in humans --
# service/system accounts included, with zero legitimate need to. Move
# it to a dedicated group and add existing local human accounts
# (UID >= 1000, the standard RED OS/RHEL cutoff for real users) to it;
# 0644 -> 0640 narrows exposure but does not fully close it -- see the
# "Честно про хранение ключа" note in overlay/linux/README.md for what
# would (a privileged component doing the encryption instead of the
# user's own process). Known gap: this local-group step does not reach
# AD/SSO-joined accounts, which do not exist in /etc/passwd at install
# time -- flagged as an open question in PROJECT_PLAN.md section 11.
getent group watermark-overlay >/dev/null || groupadd -r watermark-overlay
chgrp watermark-overlay %{_sysconfdir}/watermark-overlay/watermark.key
chmod 0640 %{_sysconfdir}/watermark-overlay/watermark.key
awk -F: '$3>=1000 && $3<60000 {print $1}' /etc/passwd | while read -r u; do
    usermod -aG watermark-overlay "$u" 2>/dev/null || true
done
echo "watermark-overlay: installed. It will start automatically the next time each user logs into a graphical (X11) session."
echo "watermark-overlay: edit /etc/watermark-overlay/config.json to change settings for everyone (applies live, no restart needed)."
echo "watermark-overlay: to decode a watermark_type=dots screenshot later: watermark-decode SCREENSHOT.png"

%files
%{_datadir}/watermark-overlay/watermark_overlay.py
%{_datadir}/watermark-overlay/dotcode.py
%{_datadir}/watermark-overlay/decode_dots.py
%{_bindir}/watermark-overlay
%{_bindir}/watermark-decode
%{_sysconfdir}/xdg/autostart/watermark-overlay.desktop
%config(noreplace) %{_sysconfdir}/watermark-overlay/config.json
%ghost %attr(0640, root, watermark-overlay) %config(noreplace) %{_sysconfdir}/watermark-overlay/watermark.key

%changelog
* Mon Aug 17 2026 Watermark Project <noreply@example.com> - 0.5.0-1
- Security: the overlay no longer just dies when killed -- the
  %{_bindir}/watermark-overlay wrapper is now a restart loop (basic
  anti-tamper, PROJECT_PLAN.md section 6). Verified this was a real
  gap, not theoretical: on a real RED OS VM, `kill <pid>` on the
  previously-installed 0.2.0 build left no watermark process running
  at all until next login -- the systemd unit in overlay/linux/systemd/
  that would have restarted it was never actually packaged/installed by
  this RPM. mode=none (exit code 0) is still respected as a deliberate
  stop, not treated as a crash to restart from.
- Security: watermark.key moved from 0644 (world-readable by any local
  account) to 0640, owned by a new dedicated `watermark-overlay` group
  that existing local human accounts (UID 1000-59999) are added to at
  install. Narrows exposure to accounts with a legitimate reason to
  read it (interactive graphical users, who need it to encode) instead
  of every local account including system/service ones. Does not fully
  close the key-confidentiality gap already documented in
  overlay/linux/README.md -- that needs a privileged-helper redesign,
  and this local-group mechanism does not reach AD/SSO-joined accounts
  (see PROJECT_PLAN.md section 11). Users added to the group need to
  log out/in once for the new group membership to take effect.

* Mon Aug 17 2026 Watermark Project <noreply@example.com> - 0.4.1-1
- Fix decode_dots.py: the bit threshold used a median split assuming
  roughly 50/50 ones-vs-zeros per capture, which silently misdecoded
  whenever a specific ciphertext's actual ratio skewed enough that the
  median landed on a tied value (found by testing -- failed on some
  renders, not others). Each dot/no-dot delta is genuinely one of only
  two values, so the fix uses the midpoint between that candidate's own
  min and max delta instead, which is correct regardless of the bit
  ratio. Verified with 5 consecutive fresh-key end-to-end runs after
  the fix, all passing (previously reproducible intermittently).

* Mon Aug 17 2026 Watermark Project <noreply@example.com> - 0.4.0-1
- Add watermark_type="dots": instead of readable text, encodes
  username+timestamp as AES-256-GCM ciphertext tiled as a faint dot
  pattern -- addresses two concerns: it's less distracting than
  readable text, and (unlike readable text, or an unencrypted dot
  pattern) it can't be casually read or forged, only decoded offline
  with the shared key via the new `watermark-decode` tool
  (decode_dots.py). Key auto-generated at
  /etc/watermark-overlay/watermark.key on install if missing.
  Verified end-to-end under Xvfb: render -> screenshot (full and an
  arbitrary off-grid crop) -> decode recovers the correct user; wrong
  key fails cleanly. NOT verified against real phone photos (only
  pixel-perfect screenshots) -- perspective distortion, screen-camera
  moire and lighting are a separate, harder problem needing real
  hardware to test, flagged as unproven in the docs.

* Thu Aug 13 2026 Watermark Project <noreply@example.com> - 0.3.1-1
- Disable Qt's GLX/EGL probing (QT_XCB_GL_INTEGRATION=none) since the
  overlay never uses OpenGL -- on machines without a hardware GL driver
  wired up for the X session (common on minimal/server installs) that
  probe was falling back to Mesa's software rasterizer and pulling in
  libLLVM.so, roughly doubling the process's memory footprint.
  Measured: RSS dropped from ~115MB to ~61MB. Set from inside the
  script itself (os.environ.setdefault, so it applies no matter how
  the process is launched) rather than in the unit/desktop file, and
  an operator can still override it by setting the env var explicitly
  before launch.

* Thu Aug 13 2026 Watermark Project <noreply@example.com> - 0.3.0-1
- Add mode="none" as a central kill switch: set before launch and the
  process exits without opening any window/X connection; flip it on an
  already-running instance (e.g. by editing the shared /etc config)
  and it quits within ~1s. Fixed two related bugs: a QApplication
  created but never entering exec() could hang on shutdown (now
  avoided by checking mode=="none" before creating QApplication at
  all), and config files watched by QFileSystemWatcher were only
  picked up if they existed at process start (now also watches each
  config file's parent directory, so a config created after startup is
  noticed too).
- Autostart .desktop now sets X-GNOME-Autostart-Delay=0 and
  X-KDE-autostart-phase=1 so no desktop environment artificially
  delays the watermark after login.

* Thu Aug 13 2026 Watermark Project <noreply@example.com> - 0.2.0-1
- Add "mode" (always / app_list + "apps" WM_CLASS list) to show the
  watermark only while selected applications are open, and
  "watermark_type" (text / account) to switch between a free-text
  template and the fixed OS account identity. Both are set centrally
  in /etc/watermark-overlay/config.json and apply live.

* Thu Aug 13 2026 Watermark Project <noreply@example.com> - 0.1.0-1
- Initial system-wide RED OS package: XDG autostart for every user,
  shared /etc/watermark-overlay/config.json for centralized control
  of text/opacity/font/angle/spacing with live reload.
