Name:           watermark-overlay
Version:        0.2.0
Release:        1%{?dist}
Summary:        Full-screen anti-leak watermark overlaying the logged-in user's name
License:        Proprietary
URL:            https://github.com/romprs/data2
BuildArch:      noarch

Source0:        %{name}-%{version}.tar.gz

Requires:       python3
Requires:       python3-pip

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
install -Dm755 watermark-overlay.wrapper %{buildroot}%{_bindir}/watermark-overlay
install -Dm644 watermark-overlay.desktop %{buildroot}%{_sysconfdir}/xdg/autostart/watermark-overlay.desktop
install -Dm644 config.json %{buildroot}%{_sysconfdir}/watermark-overlay/config.json

%post
if ! python3 -c "import PyQt5, Xlib" >/dev/null 2>&1; then
    echo "watermark-overlay: installing Python dependencies (PyQt5, python-xlib)..."
    python3 -m pip install --quiet PyQt5 python-xlib || \
        echo "watermark-overlay: WARNING - could not auto-install PyQt5/python-xlib via pip (no network access at install time?). Install manually, e.g.: dnf install python3-qt5 python3-xlib   -- or --   python3 -m pip install PyQt5 python-xlib" >&2
fi
echo "watermark-overlay: installed. It will start automatically the next time each user logs into a graphical (X11) session."
echo "watermark-overlay: edit /etc/watermark-overlay/config.json to change text/opacity for everyone (applies live, no restart needed)."

%files
%{_datadir}/watermark-overlay/watermark_overlay.py
%{_bindir}/watermark-overlay
%{_sysconfdir}/xdg/autostart/watermark-overlay.desktop
%config(noreplace) %{_sysconfdir}/watermark-overlay/config.json

%changelog
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
