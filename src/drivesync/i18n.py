"""User-facing text in Turkish and English, shared by the daemon, CLI, tray and app.

Every constant keeps its NAME (``i18n.SYNC_NOW``) and resolves to the active language on each
attribute access (module ``__getattr__``).  Language selection lives in ``current_language()``.
"""

import json
import os
from collections.abc import MutableMapping
from pathlib import Path

APP_NAME = "DriveSync"
LANGUAGES = ("tr", "en")
_TR_LINK = "https://github.com/kocakburhan/drivesync-linux#kurulum-ad\u0131m-ad\u0131m"
_EN_LINK = "https://github.com/kocakburhan/drivesync-linux#installation-step-by-step"

_override = None
_config_cache = {}


def set_language(value=None):
    """Force a language ("tr" / "en" / "auto"); None clears the override (tests)."""
    global _override
    if value not in (None, "auto", *LANGUAGES):
        raise ValueError(value)
    _override = None if value is None else value


def _config_language():
    """Raw ``language`` from config.json; no import of config (circular) and never raises."""
    base = os.environ.get("XDG_CONFIG_HOME", "")
    base = Path(base) if base and os.path.isabs(base) else Path.home() / ".config"
    path = base / "drivesync" / "config.json"
    try:
        stamp = path.stat().st_mtime_ns
        key = (str(path), stamp)
        if key not in _config_cache:
            _config_cache.clear()
            value = json.loads(path.read_text(encoding="utf-8")).get("language", "auto")
            _config_cache[key] = value if value in LANGUAGES else "auto"
        return _config_cache[key]
    except (OSError, ValueError, AttributeError, RuntimeError):
        return "auto"


def _locale_language():
    for name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(name, "")
        if value:
            first = value.split(":")[0].strip().lower()
            if first in ("c", "posix"):
                continue
            return "tr" if first.startswith("tr") else "en"
    return "en"


def current_language():
    """"tr" or "en": explicit override, config ``language``, ``DRIVESYNC_LANG``, then locale."""
    for choice in (_override, _config_language(), os.environ.get("DRIVESYNC_LANG", "").strip().lower()):
        if choice in LANGUAGES:
            return choice
    return _locale_language()


class _Localized(MutableMapping):
    """Mapping of key -> (tr, en).  Plain str values assigned later count for both languages."""

    def __init__(self, items):
        self._items = dict(items)

    def __getitem__(self, key):
        value = self._items[key]
        return value if isinstance(value, str) else value[current_language() == "en"]

    def __setitem__(self, key, value):
        self._items[key] = value

    def __delitem__(self, key):
        del self._items[key]

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)

    def raw(self, key):
        value = self._items[key]
        return (value, value) if isinstance(value, str) else value


def __getattr__(name):
    """Resolve table constants; plain module-level constants defined below bypass this."""
    try:
        pair = _STRINGS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    return pair[current_language() == "en"]


_STRINGS = {
    "INSTALL_LINK": (_TR_LINK, _EN_LINK),
    "CONFIG_ERROR": (
        'Yapılandırma geçersiz: {field}.',
        'Invalid configuration: {field}.'),
    "READ_ERROR": (
        'Dosya okunamadı: {path}.',
        'Could not read file: {path}.'),
    "STATE_ERROR": (
        'Durum dosyası geçersiz; güvenlik için işlem durduruldu.',
        'The state file is invalid; the operation was stopped for safety.'),
    "BUSY": (
        'Başka bir eşitleme işlemi çalışıyor; bitmesini bekleyin.',
        'Another sync operation is running; wait for it to finish.'),
    "NO_DAEMON": (
        'Arka plan servisi çalışmıyor veya oturum veri yoluna erişilemiyor.',
        'The background service is not running or the session bus is unreachable.'),
    "ALREADY_RUNNING": (
        'DriveSync arka plan servisi zaten çalışıyor.',
        'The DriveSync background service is already running.'),
    "DAEMON_FAILED": (
        'Arka plan servisi başlatılamadı.',
        'The background service could not be started.'),
    "UNKNOWN_FOLDER": (
        'Klasör bulunamadı: {id}.',
        'Folder not found: {id}.'),
    "FILTER_CHANGED": (
        'Filtreler değişti; önce resync ile deneme ve yeniden eşitleme yapın.',
        'The excludes changed; run a resync preview first, then resync.'),
    "PAIR_CHANGED": (
        'Klasör yolları değişti; önce resync ile deneme ve yeniden eşitleme yapın.',
        'The folder paths changed; run a resync preview first, then resync.'),
    "UNVERIFIED": (
        'Önce init ile deneme ve ilk eşitlemeyi tamamlayın.',
        'Run an init preview and complete the initial sync first.'),
    "RESYNC_REQUIRED": (
        'Eşitleme durduruldu; resync veya elle düzeltme gerekiyor.',
        'Sync stopped; a resync or a manual fix is required.'),
    "DELETE_BLOCKED": (
        'Silme sınırı aşıldı. İnceleyip force-sync ile açıkça onaylayın.',
        'The deletion limit was exceeded. Review the changes and approve explicitly with force-sync.'),
    "SYNC_ERROR": (
        'Eşitleme başarısız; kayıt dosyasını inceleyin.',
        'Sync failed; check the log file.'),
    "OFFLINE": (
        'Drive bağlantısı 30 dakikadan uzun süredir kesik.',
        'The connection to Drive has been down for more than 30 minutes.'),
    "PROBE_ERROR": (
        'Drive erişim kontrolü başarısız; kayıt dosyasını inceleyin.',
        'The Drive access check failed; check the log file.'),
    "CONFLICTS": (
        '{count} dosyada çakışma: en yeni sürüm tutuldu',
        '{count} files had conflicts: the newest version was kept'),
    "RECENT_CONFLICTS": (
        'Son çakışmalar ({count})',
        'Recent conflicts ({count})'),
    "CONFLICT_HISTORY": (
        'Son çakışmalar',
        'Recent conflicts'),
    "CONFLICT_POLICY": (
        'Çakışmalar otomatik çözülür: en yeni sürüm tutulur, kaybeden kopya silinir.',
        'Conflicts are resolved automatically: the newest version is kept and the losing copy is deleted.'),
    "NO_CONFLICTS": (
        'Kayıtlı çakışma yok.',
        'No conflicts recorded.'),
    "CLEAR": (
        'Temizle',
        'Clear'),
    "WINNER_LOCAL": (
        'Bilgisayardaki sürüm tutuldu',
        'The version on this computer was kept'),
    "WINNER_REMOTE": (
        "Drive'daki sürüm tutuldu",
        'The version on Drive was kept'),
    "WINNER_UNKNOWN": (
        'Kazanan taraf günlükten belirlenemedi',
        'The winning side could not be determined from the log'),
    "NAUTILUS_SYNC": (
        'DriveSync: Şimdi eşitle',
        'DriveSync: Sync now'),
    "NAUTILUS_DRIVE": (
        "DriveSync: Drive'da göster",
        'DriveSync: Show in Drive'),
    "NOTIFY_ERROR": (
        'Eşitleme hatası: {name}',
        'Sync error: {name}'),
    "CHECK_TEXT": (
        'DriveSync erişim denetim dosyasıdır. Silmeyin veya adını değiştirmeyin.\n',
        'This is a DriveSync access check file. Do not delete or rename it.\n'),
    "LOCAL_UNSAFE": (
        'Yerel klasör yok, dizin değil veya sembolik bağlantı; güvenlik için durduruldu.',
        'The local folder is missing, is not a directory or is a symbolic link; stopped for safety.'),
    "CHECK_UNSAFE": (
        'Erişim denetim dosyası güvenli biçimde oluşturulamadı.',
        'The access check file could not be created safely.'),
    "DRY_RUN_REQUIRED": (
        'Aynı yollar ve filtrelerle son 24 saatte başarılı bir --dry-run gerekli.',
        'A successful --dry-run with the same paths and excludes within the last 24 hours is required.'),
    "DRY_FAILED": (
        'Deneme başarısız; aşağıdaki hataları ve kayıt dosyasını inceleyin.',
        'The preview failed; check the errors below and the log file.'),
    "REMOTE_READY": (
        'Drive klasörü hazır; yoksa boş klasör oluşturuldu.',
        'The Drive folder is ready; an empty folder was created if it was missing.'),
    "DRY_SUMMARY": (
        "Deneme: Drive'a {to_remote}, bilgisayara {to_local} kopya, {deletes} silme.",
        'Preview: {to_remote} to copy to Drive, {to_local} to copy to this computer, {deletes} deletions.'),
    "DRY_UNKNOWN": (
        'Yönü belirlenemeyen {count} işlem var; ayrıntılı kaydı inceleyin.',
        '{count} actions have an undetermined direction; check the detailed log.'),
    "DRY_ACTIONS": (
        'Planlanan işlemler (ilk 50 satır):',
        'Planned actions (first 50 lines):'),
    "DRY_OK": (
        'Deneme başarılı. Uygulamak için komutu --dry-run olmadan çalıştırın.',
        'The preview succeeded. To apply it, run the command without --dry-run.'),
    "BOOTSTRAP_DRY": (
        'İlk denemede erişim dosyası kontrolü atlanır; gerçek eşitlemede iki tarafta zorunludur.',
        'The access check file is skipped in the first preview; it is required on both sides in a real sync.'),
    "INIT_OK": (
        'İlk eşitleme tamamlandı.',
        'Initial sync completed.'),
    "RESYNC_OK": (
        'Yeniden eşitleme tamamlandı.',
        'Resync completed.'),
    "FORCE_CONFIRM": (
        'Silme koruması bu işlemde aşılacak. Devam edilsin mi? [y/N] ',
        'The deletion guard will be bypassed in this run. Continue? [y/N] '),
    "CANCELLED": (
        'İşlem iptal edildi.',
        'Operation cancelled.'),
    "FORCE_INELIGIBLE": (
        'Bu klasör ilk eşitleme veya resync bekliyor; force-sync kullanılamaz.',
        'This folder is waiting for an initial sync or resync; force-sync cannot be used.'),
    "FORCE_ONLY_BLOCKED": (
        'force-sync yalnızca silme korumasının durdurduğu klasörlerde kullanılabilir.',
        'force-sync can only be used on folders stopped by the deletion guard.'),
    "DISABLED": (
        'Klasör devre dışı.',
        'The folder is disabled.'),
    "NO_LOG": (
        'Bu klasör için kayıt yok.',
        'There is no log for this folder.'),
    "REQUEST_OK": (
        'İstek kabul edildi.',
        'Request accepted.'),
    "REQUEST_REFUSED": (
        'İstek kabul edilmedi; yapılandırma ve klasör durumunu inceleyin.',
        'The request was not accepted; check the configuration and folder state.'),
    "INTERRUPTED": (
        'İşlem kesildi; bir sonraki çalışmada kurtarma denenecek.',
        'The operation was interrupted; recovery will be attempted on the next run.'),
    "CHANGED_DURING_RUN": (
        'İşlem sırasında yapılandırma değişti; yeni bir deneme gerekli.',
        'The configuration changed during the operation; a new attempt is required.'),
    "CLI_DESCRIPTION": (
        'Seçili klasörleri Google Drive ile güvenli biçimde eşitleyin.',
        'Safely sync selected folders with Google Drive.'),
    "HELP_OPTION": (
        'Bu yardım iletisini göster ve çık',
        'Show this help message and exit'),
    "DRY_HELP": (
        'Değişiklik yapmadan planı göster',
        'Show the plan without making changes'),
    "YES_HELP": (
        'Kullanıcının açık onayını kaydet',
        "Record the user's explicit approval"),
    "FOLDER_HELP": (
        'Yapılandırmadaki klasör kimliği',
        'Folder ID from the configuration'),
    "COMMAND": (
        'komut',
        'command'),
    "OPTIONS": (
        'seçenekler',
        'options'),
    "POSITIONAL": (
        'konumsal bağımsız değişkenler',
        'positional arguments'),
    "USAGE": (
        'kullanım: ',
        'usage: '),
    "ARG_ERROR": (
        '{program}: hata: {message}\n',
        '{program}: error: {message}\n'),
    "HEADERS": (
        'Klasör                 Durum                    Son başarı',
        'Folder                 Status                   Last success'),
    "DAEMON_STATUS": (
        'Servis: {status} | Duraklatıldı: {paused}',
        'Service: {status} | Paused: {paused}'),
    "YES": (
        'evet',
        'yes'),
    "NO": (
        'hayır',
        'no'),
    "NEVER": (
        'henüz yok',
        'never'),
    "TRAY_STOPPED": (
        'Arka plan servisi çalışmıyor',
        'The background service is not running'),
    "TRAY_CURRENT": (
        'Güncel · Son eşitleme: {time}',
        'Up to date · Last sync: {time}'),
    "TRAY_SYNCING": (
        'Eşitleniyor: {name}',
        'Syncing: {name}'),
    "TRAY_INITIAL_SYNCING": (
        'İlk eşitleme sürüyor: {name}',
        'Initial sync in progress: {name}'),
    "TRAY_MIXED": (
        '✓ {current} klasör güncel · {waiting} klasör ilk eşitleme bekliyor',
        '✓ {current} folders up to date · {waiting} folders waiting for initial sync'),
    "TRAY_ERRORS": (
        'Hata: {count} klasör',
        'Error: {count} folders'),
    "TRAY_ERROR": (
        'Hata',
        'Error'),
    "TRAY_OFFLINE": (
        'Çevrimdışı',
        'Offline'),
    "TRAY_PAUSED": (
        'Duraklatıldı',
        'Paused'),
    "SYNC_NOW": (
        'Şimdi eşitle',
        'Sync now'),
    "PAUSE": (
        'Duraklat',
        'Pause'),
    "RESUME": (
        'Devam et',
        'Resume'),
    "OPEN_LOGS": (
        'Kayıtları aç',
        'Open logs'),
    "OPEN_DRIVE": (
        "Drive'da aç",
        'Open in Drive'),
    "OPEN_WINDOW": (
        'Pencereyi aç',
        'Open window'),
    "START_SERVICE": (
        'Servisi başlat',
        'Start service'),
    "JUST_NOW": (
        'az önce',
        'just now'),
    "MINUTES_AGO": (
        '{count} dk önce',
        '{count} min ago'),
    "HOURS_AGO": (
        '{count} sa önce',
        '{count} h ago'),
    "YESTERDAY": (
        'dün',
        'yesterday'),
    "DAYS_AGO": (
        '{count} gün önce',
        '{count} days ago'),
    "APP_CURRENT": (
        '✓ Her şey güncel · Son eşitleme: {time}',
        '✓ Everything is up to date · Last sync: {time}'),
    "LAST_SYNC_SEPARATOR": (
        'Son eşitleme: ',
        'Last sync: '),
    "FOLDER_GROUP": (
        'Eşitlenen klasörler',
        'Synced folders'),
    "ADD_FOLDER": (
        '+ Klasör ekle',
        '+ Add folder'),
    "CHOOSE_FOLDER": (
        'Eşitlenecek klasörü seçin',
        'Choose a folder to sync'),
    "REMOTE_NAME": (
        'Drive klasör adı',
        'Drive folder name'),
    "ADD": (
        'Ekle',
        'Add'),
    "CANCEL": (
        'İptal',
        'Cancel'),
    "SAVE": (
        'Kaydet',
        'Save'),
    "SETTINGS": (
        'Ayarlar',
        'Settings'),
    "LOGS": (
        'Kayıtlar',
        'Logs'),
    "OPEN_FOLDER": (
        'Klasörü aç',
        'Open folder'),
    "REMOVE_FOLDER": (
        'Listeden çıkar',
        'Remove from list'),
    "REMOVE_HEADING": (
        'Klasör listeden çıkarılsın mı?',
        'Remove folder from the list?'),
    "REMOVE_BODY": (
        '{name} yalnızca eşitleme listesinden çıkarılacak. Bilgisayardaki ve Drive’daki dosyalar silinmez. Çalışan eşitleme varsa tamamlanır.',
        '{name} will only be removed from the sync list. Files on this computer and on Drive are not deleted. A running sync will finish first.'),
    "INIT_HINT": (
        'İlk eşitlemeyi başlat düğmesiyle değişiklik planını inceleyin.',
        'Use the Start initial sync button to review the planned changes.'),
    "RESYNC_HINT": (
        'Yeniden eşitle düğmesiyle yeni planı inceleyin.',
        'Use the Resync button to review the new plan.'),
    "ROW_STATUS": (
        '{status} · Son başarı: {time}',
        '{status} · Last success: {time}'),
    "FOLDER_OVERLAP": (
        'Klasör kök dizin olamaz; başka bir eşitleme klasörüyle aynı, onun içinde veya onu kapsayan bir klasör seçmeyin.',
        'The folder cannot be a root directory; do not choose a folder that is the same as, inside or containing another synced folder.'),
    "REMOTE_UNSAFE": (
        'Drive klasör adı boş olamaz; /, \\, :, denetim karakteri veya başta/sonda boşluk içeremez.',
        'The Drive folder name cannot be empty; it cannot contain /, \\, :, control characters or leading/trailing spaces.'),
    "REMOTE_DUPLICATE": (
        'Bu Drive klasör adı zaten kullanılıyor.',
        'This Drive folder name is already in use.'),
    "BANNER_RESYNC": (
        'Bazı klasörlerde yeniden eşitleme gerekiyor. Yeniden eşitle düğmesini kullanın.',
        'Some folders need a resync. Use the Resync button.'),
    "REVIEW": (
        'İncele',
        'Review'),
    "POLL_INTERVAL": (
        'Drive kontrol sıklığı (dakika)',
        'Drive check interval (minutes)'),
    "DEBOUNCE": (
        'Yerel değişiklik bekleme süresi (saniye)',
        'Local change delay (seconds)'),
    "MAX_DELETE": (
        'En fazla silme (%)',
        'Maximum deletions (%)'),
    "EXCLUDES": (
        'Hariç tutulanlar',
        'Excludes'),
    "ADD_EXCLUDE": (
        'Kural ekle',
        'Add rule'),
    "EXCLUDE_PATTERN": (
        'Hariç tutma kuralı',
        'Exclude rule'),
    "EXCLUDES_WARNING": (
        'Hariç tutma kuralları değişirse başlatılmış klasörlerde otomatik eşitleme durur. Yeniden eşitle düğmesiyle önce planı inceleyin.',
        'If the excludes change, automatic sync stops for folders that have already been initialized. Review the plan with the Resync button first.'),
    "ERROR_TITLE": (
        'İşlem tamamlanamadı',
        'The operation could not be completed'),
    "CLOSE": (
        'Kapat',
        'Close'),
    "WATCH_FALLBACK": (
        '{id}: Yerel izleme sınırı aşıldı veya izleyici kurulamadı; bu klasör aralıklı kontrolle eşitlenecek.',
        '{id}: The local watch limit was exceeded or the watcher could not be set up; this folder will be synced with periodic checks.'),
    "WATCH_STARTUP": (
        'Yerel izleme: %d klasör izleyicisi, %.3f saniye; aralıklı kontrol: %d klasör.',
        'Local watching: %d folder watchers, %.3f seconds; periodic checks: %d folders.'),
    "RCLONE_REQUIRED": (
        'rclone bulunamadı veya sürümü desteklenmiyor; rclone 1.66 veya üstü gerekli. Kurulum: ' + _TR_LINK,
        'rclone was not found or its version is unsupported; rclone 1.66 or newer is required. Installation: ' + _EN_LINK),
    "REMOTE_MISSING": (
        'Google Drive bağlantısı kurulmamış. drivesync-cli setup-remote --client-json <JSON-dosyası> ile bağlanın. Kurulum: ' + _TR_LINK,
        'The Google Drive connection is not set up. Connect with drivesync-cli setup-remote --client-json <JSON-file>. Installation: ' + _EN_LINK),
    "REMOTE_CHECK_FAILED": (
        'rclone bağlantı listesi okunamadı. rclone listremotes ve yapılandırmayı kontrol edin. Kurulum: ' + _TR_LINK,
        'The rclone remote list could not be read. Check rclone listremotes and the configuration. Installation: ' + _EN_LINK),
    "CLIENT_INVALID": (
        'Geçerli bir Google OAuth Masaüstü uygulaması (Desktop app) istemci JSON dosyası seçin.',
        'Choose a valid Google OAuth Desktop app client JSON file.'),
    "REMOTE_EXISTS": (
        'Bu bağlantı zaten var. Değiştirmek için --replace kullanın; önce rclone.conf yedeklenecek.',
        'This remote already exists. Use --replace to change it; rclone.conf will be backed up first.'),
    "REMOTE_SETUP_FAILED": (
        'Google Drive bağlantısı kurulamadı. Tarayıcı onayını ve JSON dosyasını kontrol edin. Kurulum: ' + _TR_LINK,
        'The Google Drive connection could not be set up. Check the browser approval and the JSON file. Installation: ' + _EN_LINK),
    "REMOTE_SETUP_OK": (
        'Google Drive bağlantısı kuruldu ve erişim doğrulandı: {remote}:',
        'The Google Drive connection was set up and access verified: {remote}:'),
    "REMOTE_BACKUP_FAILED": (
        'rclone.conf güvenle yedeklenemedi; bağlantı değiştirilmedi.',
        'rclone.conf could not be backed up safely; the remote was not changed.'),
    "REMOTE_CUSTOM": (
        'DriveSync config.json içindeki remote değerini {remote}: olarak ayarlayın.',
        'Set the remote value in DriveSync config.json to {remote}:.'),
    "CLIENT_JSON_HELP": (
        'İndirilen Google OAuth Desktop istemci JSON dosyası',
        'Downloaded Google OAuth Desktop client JSON file'),
    "REMOTE_HELP": (
        'rclone bağlantı adı (varsayılan: gdrive)',
        'rclone remote name (default: gdrive)'),
    "REPLACE_HELP": (
        'Mevcut bağlantıyı yedekleyip değiştir',
        'Back up and replace the existing remote'),
    "JSON_HELP": (
        'Deneme veya arka plan başlatma sonucunu JSON olarak yazdır',
        'Print the preview or background start result as JSON'),
    "VERSION_HELP": (
        'Sürümü göster ve çık',
        'Show the version and exit'),
    "ADD_LOCAL_HELP": (
        'Eşitlenecek yerel dizin',
        'Local directory to sync'),
    "ADD_NAME_HELP": (
        'Drive altında kullanılacak klasör adı',
        'Folder name to use under Drive'),
    "FOLDER_ADDED": (
        'Klasör eklendi. Kimlik: {id}',
        'Folder added. ID: {id}'),
    "INIT_START": (
        'İlk eşitlemeyi başlat',
        'Start initial sync'),
    "INIT_RESYNC": (
        'Yeniden eşitle',
        'Resync'),
    "INIT_PREVIEW": (
        'Eşitleme planı hazırlanıyor…',
        'Preparing the sync plan…'),
    "INIT_SUMMARY": (
        "Drive'a {to_remote} dosya, bilgisayara {to_local} dosya, {deletes} silme",
        '{to_remote} files to Drive, {to_local} files to this computer, {deletes} deletions'),
    "INIT_DETAILS": (
        'Planlanan işlemler (ilk 50)',
        'Planned actions (first 50)'),
    "INIT_WARNING": (
        'DİKKAT: Bu plan dosya silmeyi içeriyor. Kayıtları inceleyin ve yedeğiniz olduğundan emin olun. Devam etmek için klasör adını tam olarak yazın: {name}',
        'WARNING: This plan includes deleting files. Review the logs and make sure you have a backup. To continue, type the folder name exactly: {name}'),
    "INIT_UNKNOWN": (
        'Yönü belirlenemeyen {count} işlem var. Başlatmadan önce ayrıntılı kayıtları inceleyin.',
        '{count} actions have an undetermined direction. Review the detailed logs before starting.'),
    "INIT_CONFIRM": (
        'Başlat',
        'Start'),
    "INIT_OFFER": (
        'İlk eşitlemeyi şimdi başlat?',
        'Start the initial sync now?'),
    "INIT_OFFER_BODY": (
        '{name} eklendi. Önce değişiklik planını inceleyebilirsiniz.',
        '{name} was added. You can review the planned changes first.'),
    "INIT_LAUNCH_FAILED": (
        'Eşitleme başlatılamadı. Arka plan servisinin çalıştığını ve kayıtları kontrol edin.',
        'Sync could not be started. Check that the background service is running and the logs.'),
    "INIT_PLAN_INVALID": (
        'Eşitleme planı okunamadı; işlem başlatılmadı.',
        'The sync plan could not be read; the operation was not started.'),
    "RCLONE_ARCH": (
        'Otomatik rclone kurulumu yalnızca Linux amd64 ve arm64 için destekleniyor.',
        'Automatic rclone installation is only supported on Linux amd64 and arm64.'),
    "RCLONE_CHECKSUM": (
        'rclone SHA256 doğrulaması başarısız; hiçbir dosya kurulmadı.',
        'The rclone SHA256 verification failed; nothing was installed.'),
    "RCLONE_INSTALL_FAILED": (
        'rclone indirilemedi veya doğrulanamadı. Mevcut dosya korundu. https://rclone.org/downloads/',
        'rclone could not be downloaded or verified. The existing file was kept. https://rclone.org/downloads/'),
    "RCLONE_INSTALLED": (
        'rclone SHA256 ile doğrulanıp kuruldu: {path}',
        'rclone was verified with SHA256 and installed: {path}'),
    "MACHINE_JSON_HELP": (
        'Sonucu JSON olarak yazdır',
        'Print the result as JSON'),
    "OFFLINE_HELP": (
        'Uzak erişim denetimini atla',
        'Skip the remote access check'),
    "BACKGROUND_HELP": (
        'Servise ilet (varsayılan; --yes ile aynı davranış)',
        'Hand it to the service (default; same as --yes)'),
    "YES_REQUIRED": (
        'Bu işlem açık onay gerektirir. Onaydan sonra --yes ile tekrar çalıştırın.',
        'This operation requires explicit approval. After approval, run it again with --yes.'),
    "CLI_DONE": (
        'Yapılandırma güncellendi.',
        'Configuration updated.'),
    "BACKGROUND_STARTED": (
        'İlk eşitleme arka planda başlatıldı: {unit}',
        'Initial sync started in the background: {unit}'),
    "BACKGROUND_FAILED": (
        'Arka plan işi başlatılamadı; systemd kullanıcı oturumunu ve journalctl kayıtlarını kontrol edin.',
        'The background job could not be started; check the systemd user session and the journalctl logs.'),
    "GI_REQUIRED": (
        'Python GI/GLib/Gio gerekli. Paketler için doctor --offline çalıştırın.',
        'Python GI/GLib/Gio is required. Run doctor --offline for the packages.'),
    "CONFIG_KEY_HELP": (
        'İzin verilen ayar anahtarı',
        'Allowed setting key'),
    "CONFIG_VALUE_HELP": (
        'Yeni ayar değeri',
        'New setting value'),
    "EXCLUDE_HELP": (
        'Hariç tutma deseni (başında - varsa önce -- kullanın)',
        'Exclude pattern (if it starts with -, use -- first)'),
    "PROGRESS_LINE": (
        '{id}: {status} — {progress}',
        '{id}: {status} — {progress}'),
    "DOCTOR_LINE": (
        '{id}: {status} — {message}\n  Çözüm: {fix}',
        '{id}: {status} — {message}\n  Fix: {fix}'),
    "APPINDICATOR_HINT": (
        "GNOME Uzantılar'da AppIndicator and KStatusNotifierItem Support etkin olmalı.",
        'AppIndicator and KStatusNotifierItem Support must be enabled in GNOME Extensions.'),
    "PERCENT": ("%{value}", "{value}%"),
    "ETA_LEFT": ("kalan ~{duration}", "~{duration} left"),
    "DURATION_H": ("{value} sa", "{value} h"),
    "DURATION_M": ("{value} dk", "{value} min"),
    "DURATION_S": ("{value} sn", "{value} s"),
    "RATE": ("{value} {unit}/sn", "{value} {unit}/s"),
    "FILES_PROGRESS": ("{done}/{total} dosya", "{done}/{total} files"),
    "TOTAL_PROGRESS": ("Toplam: {percent}", "Total: {percent}"),
    "REMOTE_CHANGE_CHECK_FAILED": (
        'Drive değişiklik denetimi başarısız oldu; tam eşitlemeye dönüldü.',
        'Remote change check failed; falling back to a full sync.'),
    "TRAY_INIT_QUEUED": (
        'İlk eşitleme sırada: {name}',
        'Initial sync queued: {name}'),
    "TRAY_INIT_APPROVAL": (
        'İlk eşitleme yeniden onay bekliyor: {name}',
        'Initial sync is waiting for approval again: {name}'),
    "INIT_REAPPROVE": (
        'Yeniden onayla',
        'Approve again'),
    "INIT_NEEDS_APPROVAL_NOTIFY": (
        'İlk eşitleme yeniden onay bekliyor: {name}',
        'Initial sync is waiting for approval again: {name}'),
    "INIT_NEEDS_APPROVAL": (
        'İlk eşitleme yeniden onay bekliyor: plan onaylandığınızdan beri değişti (yeni silme, daha fazla indirme veya ayarlar). Planı yeniden inceleyip onaylayın.',
        'Initial sync is waiting for approval again: the plan changed since you approved it (new deletions, more downloads or settings). Review the plan again and approve it.'),
    "INIT_CONFIG_CHANGED": (
        'İlk eşitleme yeniden onay bekliyor: klasör yolları veya filtreler onaydan sonra değişti.',
        'Initial sync is waiting for approval again: folder paths or filters changed after approval.'),
    "INIT_GAVE_UP": (
        'İlk eşitleme tekrar tekrar başarısız oldu; kayıt dosyasını inceleyip yeniden başlatın.',
        'Initial sync failed repeatedly; check the log file and start it again.'),
    "INIT_WAITING": (
        'İlk eşitleme sırada; bağlantı veya hata nedeniyle yeniden denenecek.',
        'Initial sync is queued and will be retried after a connection problem or an error.'),
    "INIT_NO_DAEMON": (
        'Arka plan servisi çalışmıyor; ilk eşitleme sıraya alınamadı. Servisi başlatın (systemctl --user start drivesync.service) veya --foreground ile bu pencerede çalıştırın.',
        'The background service is not running; the initial sync could not be queued. Start the service (systemctl --user start drivesync.service) or run it in this window with --foreground.'),
    "INIT_SUBMITTED": (
        'İlk eşitleme servise iletildi; bilgisayar yeniden başlasa da devam edecek: {id}',
        'Initial sync was handed to the service; it will continue even after a reboot: {id}'),
    "INIT_SUBMIT_REFUSED": (
        'Servis ilk eşitlemeyi kabul etmedi; durumu ve duraklatma ayarını inceleyin.',
        'The service did not accept the initial sync; check the status and the pause setting.'),
    "FOREGROUND_HELP": (
        'Servis yerine bu işlemde, ön planda çalıştır (yeniden başlatmada kaybolur)',
        'Run in the foreground in this process instead of the service (lost on restart)'),
    # --- per-folder excludes ---
    "FOLDER_EXCLUDES": (
        'Hariç tutmalar…',
        'Excludes…'),
    "FOLDER_EXCLUDES_TITLE": (
        'Hariç tutmalar: {name}',
        'Excludes: {name}'),
    "FOLDER_EXCLUDES_NOTE": (
        'Bu kurallar, Ayarlar\'daki genel kurallara ek olarak yalnızca bu klasör için geçerlidir.',
        'These rules apply to this folder only, in addition to the global rules in Settings.'),
}

HELP = _Localized({
    'status': (
        'Eşitleme durumunu göster',
        'Show the sync status'),
    'sync': (
        'Bir klasörü veya uygun tüm klasörleri sıraya al',
        'Queue one folder or all eligible folders'),
    'pause': (
        'Otomatik eşitlemeyi duraklat',
        'Pause automatic sync'),
    'resume': (
        'Otomatik eşitlemeye devam et',
        'Resume automatic sync'),
    'init': (
        'İlk eşitlemeyi hazırla veya uygula',
        'Prepare or run the initial sync'),
    'resync': (
        'Yeniden eşitlemeyi hazırla veya uygula',
        'Prepare or run a resync'),
    'force-sync': (
        'Silme korumasını bir kez açık onayla aş',
        'Bypass the deletion guard once, with explicit approval'),
    'logs': (
        'Son kayıt dosyasının yolunu göster',
        'Show the path of the latest log file'),
    'config-path': (
        'Yapılandırma dosyasının yolunu göster',
        'Show the path of the configuration file'),
    'setup-remote': (
        'Google OAuth JSON dosyası ile Drive bağlantısını kur',
        'Set up the Drive connection with a Google OAuth JSON file'),
    'add-folder': (
        'Yerel klasörü eşitleme listesine ekle',
        'Add a local folder to the sync list'),
    'doctor': (
        'Kurulumu denetle ve somut çözümleri göster',
        'Check the installation and show concrete fixes'),
    'folder': (
        'Eşitlenecek klasörleri yönet',
        'Manage the folders to sync'),
    'config': (
        'Güvenli ayarları ve hariç tutmaları yönet',
        'Manage safe settings and excludes'),
    'progress': (
        'Bir klasörün canlı ilerlemesini göster',
        'Show the live progress of a folder'),
})

STATUSES = _Localized({
    'idle': (
        'Bekliyor',
        'Waiting'),
    'syncing': (
        'Eşitleniyor',
        'Syncing'),
    'paused': (
        'Duraklatıldı',
        'Paused'),
    'offline': (
        'Bağlantı yok',
        'Offline'),
    'error': (
        'Hata',
        'Error'),
    'ok': (
        'Güncel',
        'Up to date'),
    'queued': (
        'Sırada',
        'Queued'),
    'blocked_delete': (
        'Silme engellendi',
        'Deletion blocked'),
    'not_initialized': (
        'İlk eşitleme bekliyor',
        'Waiting for initial sync'),
    'disabled': (
        'Devre dışı',
        'Disabled'),
    'initial_syncing': (
        'İlk eşitleme sürüyor',
        'Initial sync in progress'),
    'needs_resync': (
        'Yeniden eşitleme gerekiyor',
        'Resync required'),
    'init_queued': (
        'İlk eşitleme sırada (yeniden başlatma sonrası devam edecek)',
        'Initial sync queued (will resume after restart)'),
    'needs_approval': (
        'Yeniden onay gerekiyor',
        'Needs approval again'),
})

DOCTOR_MESSAGES = _Localized({
    'environment.os': (
        'Linux işletim sistemi denetimi.',
        'Linux operating system check.'),
    'environment.desktop': (
        'Masaüstü ve X11/Wayland oturum bilgileri.',
        'Desktop and X11/Wayland session information.'),
    'python.version': (
        'Python 3.12 veya üzeri gerekli.',
        'Python 3.12 or newer is required.'),
    'gi.GLib': (
        'GLib 2.0 GI kitaplığı denetimi.',
        'GLib 2.0 GI library check.'),
    'gi.Gio': (
        'Gio 2.0 GI kitaplığı denetimi.',
        'Gio 2.0 GI library check.'),
    'gi.GObject': (
        'GObject 2.0 GI kitaplığı denetimi.',
        'GObject 2.0 GI library check.'),
    'gi.Gtk3': (
        'Tepsi için GTK 3.0 GI kitaplığı denetimi.',
        'GTK 3.0 GI library check for the tray.'),
    'gi.Gtk4': (
        'Pencere için GTK 4.0 GI kitaplığı denetimi.',
        'GTK 4.0 GI library check for the window.'),
    'gi.Gdk4': (
        'Pencere için Gdk 4.0 GI kitaplığı denetimi.',
        'Gdk 4.0 GI library check for the window.'),
    'gi.Adw': (
        'Libadwaita 1 GI kitaplığı denetimi.',
        'Libadwaita 1 GI library check.'),
    'gi.Notify': (
        'Notify 0.7 GI kitaplığı denetimi.',
        'Notify 0.7 GI library check.'),
    'gi.AyatanaAppIndicator3': (
        'AyatanaAppIndicator3 0.1 GI kitaplığı denetimi.',
        'AyatanaAppIndicator3 0.1 GI library check.'),
    'gi.Nautilus': (
        'Nautilus 4.0 GI kitaplığı denetimi.',
        'Nautilus 4.0 GI library check.'),
    'desktop.appindicator': (
        "GNOME'da tepsi simgesi için AppIndicator eklentisini kontrol edin.",
        'Check the AppIndicator extension for the tray icon on GNOME.'),
    'rclone.version': (
        'rclone 1.66 veya üzeri gerekli.',
        'rclone 1.66 or newer is required.'),
    'remote.configured': (
        'Yapılandırılan rclone bağlantısı denetimi.',
        'Check of the configured rclone remote.'),
    'remote.reachable': (
        'Google Drive erişim denetimi (çevrimdışı seçeneğiyle atlanabilir).',
        'Google Drive access check (can be skipped with the offline option).'),
    'install.files': (
        'Kurulu uygulama, başlatıcı ve Nautilus dosyaları denetimi.',
        'Check of the installed app, launchers and Nautilus files.'),
    'install.units': (
        'systemd kullanıcı birimi dosyaları denetimi.',
        'Check of the systemd user unit files.'),
    'systemd.drivesync.enabled': (
        'Arka plan servisi oturum başlangıcında etkin olmalı.',
        'The background service must be enabled at session start.'),
    'systemd.drivesync.active': (
        'Arka plan servisi çalışıyor olmalı.',
        'The background service must be running.'),
    'systemd.drivesync-tray.enabled': (
        'Tepsi servisi oturum başlangıcında etkin olmalı.',
        'The tray service must be enabled at session start.'),
    'systemd.drivesync-tray.active': (
        'Tepsi servisi çalışıyor olmalı.',
        'The tray service must be running.'),
    'config.valid': (
        'Yapılandırma dosyası ve şema denetimi.',
        'Check of the configuration file and schema.'),
    'inotify.watches': (
        'inotify sınırı ile tahmini dizin izleyici ihtiyacı karşılaştırması.',
        'Comparison of the inotify limit with the estimated directory watcher need.'),
    'state.folders': (
        'Klasörlerin ilk eşitleme, hata ve ilerleme özeti.',
        "Summary of the folders' initial sync, errors and progress."),
})

# --- How to add strings ---------------------------------------------------------------------
# Bilingual (preferred): add ``"NAME": ("Türkçe metin", "English text")`` to ``_STRINGS`` above
# (or ``key: (tr, en)`` to HELP / STATUSES / DOCTOR_MESSAGES).  Placeholders such as {count} or
# %d must be identical in both languages; tests/test_i18n.py enforces this.
# Quick additions: a plain module-level constant defined BELOW this comment (``NAME = "..."``)
# is found before ``__getattr__`` runs and is used as-is for both languages, so Turkish-only text
# keeps working until an English translation is moved into ``_STRINGS``.  Mapping items assigned
# as plain str (``STATUSES["x"] = "..."``) behave the same way.
