# Shared argument and destination handling for the two Bash entry points.
prefix=${DRIVESYNC_PREFIX:-"$HOME/.local"}
no_enable=false
purge=false
with_rclone=false
while (($#)); do
    case "$1" in
        --prefix)
            if (($# < 2)) || [[ -z $2 ]]; then
                echo 'Hata: --prefix için bir dizin gerekli.' >&2
                exit 2
            fi
            prefix=$2
            shift 2
            ;;
        --with-rclone)
            [[ $operation = install ]] || { echo 'Hata: --with-rclone yalnızca kurulum için.' >&2; exit 2; }
            with_rclone=true; shift ;;
        --no-enable) no_enable=true; shift ;;
        --purge)
            if [[ $operation != uninstall ]]; then
                echo 'Hata: --purge yalnızca kaldırma sırasında kullanılabilir.' >&2
                exit 2
            fi
            purge=true
            shift
            ;;
        *) echo "Hata: Bilinmeyen seçenek: $1" >&2; exit 2 ;;
    esac
done
[[ $prefix = /* ]] || prefix="$HOME/$prefix"
unit_dir=${DRIVESYNC_SYSTEMD_DIR:-"${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"}
[[ $unit_dir = /* ]] || unit_dir="$HOME/$unit_dir"
desktop_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
icon_dir="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
extension_dir="${XDG_DATA_HOME:-$HOME/.local/share}/nautilus-python/extensions"
[[ $desktop_dir = /* ]] || desktop_dir="$HOME/$desktop_dir"
[[ $icon_dir = /* ]] || icon_dir="$HOME/$icon_dir"
[[ $extension_dir = /* ]] || extension_dir="$HOME/$extension_dir"
export PYTHONDONTWRITEBYTECODE=1

refresh_desktop_caches() {
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$desktop_dir" >/dev/null 2>&1 || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -f -t "$icon_dir" >/dev/null 2>&1 || true
    fi
}
