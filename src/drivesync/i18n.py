"""Turkish user-facing text, shared by the daemon and CLI."""

APP_NAME = "DriveSync"
CONFIG_ERROR = "Yapılandırma geçersiz: {field}."
READ_ERROR = "Dosya okunamadı: {path}."
STATE_ERROR = "Durum dosyası geçersiz; güvenlik için işlem durduruldu."
BUSY = "Başka bir eşitleme işlemi çalışıyor; bitmesini bekleyin."
NO_DAEMON = "Arka plan servisi çalışmıyor veya oturum veri yoluna erişilemiyor."
ALREADY_RUNNING = "DriveSync arka plan servisi zaten çalışıyor."
DAEMON_FAILED = "Arka plan servisi başlatılamadı."
UNKNOWN_FOLDER = "Klasör bulunamadı: {id}."
FILTER_CHANGED = "Filtreler değişti; önce resync ile deneme ve yeniden eşitleme yapın."
PAIR_CHANGED = "Klasör yolları değişti; önce resync ile deneme ve yeniden eşitleme yapın."
UNVERIFIED = "Önce init ile deneme ve ilk eşitlemeyi tamamlayın."
RESYNC_REQUIRED = "Eşitleme durduruldu; resync veya elle düzeltme gerekiyor."
DELETE_BLOCKED = "Silme sınırı aşıldı. İnceleyip force-sync ile açıkça onaylayın."
SYNC_ERROR = "Eşitleme başarısız; kayıt dosyasını inceleyin."
OFFLINE = "Drive bağlantısı 30 dakikadan uzun süredir kesik."
PROBE_ERROR = "Drive erişim kontrolü başarısız; kayıt dosyasını inceleyin."
CONFLICTS = "{count} dosyada çakışma: en yeni sürüm tutuldu"
RECENT_CONFLICTS = "Son çakışmalar ({count})"
CONFLICT_HISTORY = "Son çakışmalar"
CONFLICT_POLICY = "Çakışmalar otomatik çözülür: en yeni sürüm tutulur, kaybeden kopya silinir."
NO_CONFLICTS = "Kayıtlı çakışma yok."
CLEAR = "Temizle"
WINNER_LOCAL = "Bilgisayardaki sürüm tutuldu"
WINNER_REMOTE = "Drive'daki sürüm tutuldu"
WINNER_UNKNOWN = "Kazanan taraf günlükten belirlenemedi"
NAUTILUS_SYNC = "DriveSync: Şimdi eşitle"
NAUTILUS_DRIVE = "DriveSync: Drive'da göster"
NOTIFY_ERROR = "Eşitleme hatası: {name}"
CHECK_TEXT = "DriveSync erişim denetim dosyasıdır. Silmeyin veya adını değiştirmeyin.\n"
LOCAL_UNSAFE = "Yerel klasör yok, dizin değil veya sembolik bağlantı; güvenlik için durduruldu."
CHECK_UNSAFE = "Erişim denetim dosyası güvenli biçimde oluşturulamadı."
DRY_RUN_REQUIRED = "Aynı yollar ve filtrelerle son 24 saatte başarılı bir --dry-run gerekli."
DRY_FAILED = "Deneme başarısız; aşağıdaki hataları ve kayıt dosyasını inceleyin."
REMOTE_READY = "Drive klasörü hazır; yoksa boş klasör oluşturuldu."
DRY_SUMMARY = "Deneme: Drive'a {to_remote}, bilgisayara {to_local} kopya, {deletes} silme."
DRY_UNKNOWN = "Yönü belirlenemeyen {count} işlem var; ayrıntılı kaydı inceleyin."
DRY_ACTIONS = "Planlanan işlemler (ilk 50 satır):"
DRY_OK = "Deneme başarılı. Uygulamak için komutu --dry-run olmadan çalıştırın."
BOOTSTRAP_DRY = "İlk denemede erişim dosyası kontrolü atlanır; gerçek eşitlemede iki tarafta zorunludur."
INIT_OK = "İlk eşitleme tamamlandı."
RESYNC_OK = "Yeniden eşitleme tamamlandı."
FORCE_CONFIRM = "Silme koruması bu işlemde aşılacak. Devam edilsin mi? [y/N] "
CANCELLED = "İşlem iptal edildi."
FORCE_INELIGIBLE = "Bu klasör ilk eşitleme veya resync bekliyor; force-sync kullanılamaz."
FORCE_ONLY_BLOCKED = "force-sync yalnızca silme korumasının durdurduğu klasörlerde kullanılabilir."
DISABLED = "Klasör devre dışı."
NO_LOG = "Bu klasör için kayıt yok."
REQUEST_OK = "İstek kabul edildi."
REQUEST_REFUSED = "İstek kabul edilmedi; yapılandırma ve klasör durumunu inceleyin."
INTERRUPTED = "İşlem kesildi; bir sonraki çalışmada kurtarma denenecek."
CHANGED_DURING_RUN = "İşlem sırasında yapılandırma değişti; yeni bir deneme gerekli."
CLI_DESCRIPTION = "Seçili klasörleri Google Drive ile güvenli biçimde eşitleyin."
HELP = {
    "status": "Eşitleme durumunu göster",
    "sync": "Bir klasörü veya uygun tüm klasörleri sıraya al",
    "pause": "Otomatik eşitlemeyi duraklat",
    "resume": "Otomatik eşitlemeye devam et",
    "init": "İlk eşitlemeyi hazırla veya uygula",
    "resync": "Yeniden eşitlemeyi hazırla veya uygula",
    "force-sync": "Silme korumasını bir kez açık onayla aş",
    "logs": "Son kayıt dosyasının yolunu göster",
    "config-path": "Yapılandırma dosyasının yolunu göster",
}
HELP_OPTION = "Bu yardım iletisini göster ve çık"
DRY_HELP = "Değişiklik yapmadan planı göster"
YES_HELP = "Kullanıcının açık onayını kaydet"
FOLDER_HELP = "Yapılandırmadaki klasör kimliği"
COMMAND = "komut"
OPTIONS = "seçenekler"
POSITIONAL = "konumsal bağımsız değişkenler"
USAGE = "kullanım: "
ARG_ERROR = "{program}: hata: {message}\n"
HEADERS = "Klasör                 Durum                    Son başarı"
DAEMON_STATUS = "Servis: {status} | Duraklatıldı: {paused}"
YES = "evet"
NO = "hayır"
NEVER = "henüz yok"
STATUSES = {
    "idle": "Bekliyor", "syncing": "Eşitleniyor", "paused": "Duraklatıldı",
    "offline": "Bağlantı yok", "error": "Hata", "ok": "Güncel",
    "queued": "Sırada", "blocked_delete": "Silme engellendi",
    "not_initialized": "İlk eşitleme bekliyor", "disabled": "Devre dışı",
    "initial_syncing": "İlk eşitleme sürüyor",
}

TRAY_STOPPED = "Arka plan servisi çalışmıyor"
TRAY_CURRENT = "Güncel · Son eşitleme: {time}"
TRAY_SYNCING = "Eşitleniyor: {name}"
TRAY_INITIAL_SYNCING = "İlk eşitleme sürüyor: {name}"
TRAY_MIXED = "✓ {current} klasör güncel · {waiting} klasör ilk eşitleme bekliyor"
TRAY_ERRORS = "Hata: {count} klasör"
TRAY_ERROR = "Hata"
TRAY_OFFLINE = "Çevrimdışı"
TRAY_PAUSED = "Duraklatıldı"
SYNC_NOW = "Şimdi eşitle"
PAUSE = "Duraklat"
RESUME = "Devam et"
OPEN_LOGS = "Kayıtları aç"
OPEN_DRIVE = "Drive'da aç"
OPEN_WINDOW = "Pencereyi aç"
START_SERVICE = "Servisi başlat"
JUST_NOW = "az önce"
MINUTES_AGO = "{count} dk önce"
HOURS_AGO = "{count} sa önce"
YESTERDAY = "dün"
DAYS_AGO = "{count} gün önce"

# Main window / preferences
APP_CURRENT = "✓ Her şey güncel · Son eşitleme: {time}"
LAST_SYNC_SEPARATOR = "Son eşitleme: "
FOLDER_GROUP = "Eşitlenen klasörler"
ADD_FOLDER = "+ Klasör ekle"
CHOOSE_FOLDER = "Eşitlenecek klasörü seçin"
REMOTE_NAME = "Drive klasör adı"
ADD = "Ekle"
CANCEL = "İptal"
SAVE = "Kaydet"
SETTINGS = "Ayarlar"
LOGS = "Kayıtlar"
OPEN_FOLDER = "Klasörü aç"
REMOVE_FOLDER = "Listeden çıkar"
REMOVE_HEADING = "Klasör listeden çıkarılsın mı?"
REMOVE_BODY = "{name} yalnızca eşitleme listesinden çıkarılacak. Bilgisayardaki ve Drive’daki dosyalar silinmez. Çalışan eşitleme varsa tamamlanır."
INIT_HINT = "İlk eşitlemeyi başlat düğmesiyle değişiklik planını inceleyin."
RESYNC_HINT = "Yeniden eşitle düğmesiyle yeni planı inceleyin."
ROW_STATUS = "{status} · Son başarı: {time}"
FOLDER_OVERLAP = "Klasör kök dizin olamaz; başka bir eşitleme klasörüyle aynı, onun içinde veya onu kapsayan bir klasör seçmeyin."
REMOTE_UNSAFE = "Drive klasör adı boş olamaz; /, \\, :, denetim karakteri veya başta/sonda boşluk içeremez."
REMOTE_DUPLICATE = "Bu Drive klasör adı zaten kullanılıyor."
BANNER_RESYNC = "Bazı klasörlerde yeniden eşitleme gerekiyor. Yeniden eşitle düğmesini kullanın."
REVIEW = "İncele"
POLL_INTERVAL = "Drive kontrol sıklığı (dakika)"
DEBOUNCE = "Yerel değişiklik bekleme süresi (saniye)"
MAX_DELETE = "En fazla silme (%)"
EXCLUDES = "Hariç tutulanlar"
ADD_EXCLUDE = "Kural ekle"
EXCLUDE_PATTERN = "Hariç tutma kuralı"
EXCLUDES_WARNING = "Hariç tutma kuralları değişirse başlatılmış klasörlerde otomatik eşitleme durur. Yeniden eşitle düğmesiyle önce planı inceleyin."
ERROR_TITLE = "İşlem tamamlanamadı"
CLOSE = "Kapat"
STATUSES["needs_resync"] = "Yeniden eşitleme gerekiyor"
WATCH_FALLBACK = "{id}: Yerel izleme sınırı aşıldı veya izleyici kurulamadı; bu klasör aralıklı kontrolle eşitlenecek."

WATCH_STARTUP = "Yerel izleme: %d klasör izleyicisi, %.3f saniye; aralıklı kontrol: %d klasör."

# Public-release setup and initialization flow
INSTALL_LINK = "https://github.com/kocakburhan/drivesync-linux#kurulum-adım-adım"
RCLONE_REQUIRED = "rclone bulunamadı veya sürümü desteklenmiyor; rclone 1.66 veya üstü gerekli. Kurulum: " + INSTALL_LINK
REMOTE_MISSING = "Google Drive bağlantısı kurulmamış. drivesync-cli setup-remote --client-json <JSON-dosyası> ile bağlanın. Kurulum: " + INSTALL_LINK
REMOTE_CHECK_FAILED = "rclone bağlantı listesi okunamadı. rclone listremotes ve yapılandırmayı kontrol edin. Kurulum: " + INSTALL_LINK
CLIENT_INVALID = "Geçerli bir Google OAuth Masaüstü uygulaması (Desktop app) istemci JSON dosyası seçin."
REMOTE_EXISTS = "Bu bağlantı zaten var. Değiştirmek için --replace kullanın; önce rclone.conf yedeklenecek."
REMOTE_SETUP_FAILED = "Google Drive bağlantısı kurulamadı. Tarayıcı onayını ve JSON dosyasını kontrol edin. Kurulum: " + INSTALL_LINK
REMOTE_SETUP_OK = "Google Drive bağlantısı kuruldu ve erişim doğrulandı: {remote}:"
REMOTE_BACKUP_FAILED = "rclone.conf güvenle yedeklenemedi; bağlantı değiştirilmedi."
REMOTE_CUSTOM = "DriveSync config.json içindeki remote değerini {remote}: olarak ayarlayın."
HELP["setup-remote"] = "Google OAuth JSON dosyası ile Drive bağlantısını kur"
HELP["add-folder"] = "Yerel klasörü eşitleme listesine ekle"
CLIENT_JSON_HELP = "İndirilen Google OAuth Desktop istemci JSON dosyası"
REMOTE_HELP = "rclone bağlantı adı (varsayılan: gdrive)"
REPLACE_HELP = "Mevcut bağlantıyı yedekleyip değiştir"
JSON_HELP = "Deneme veya arka plan başlatma sonucunu JSON olarak yazdır"
VERSION_HELP = "Sürümü göster ve çık"
ADD_LOCAL_HELP = "Eşitlenecek yerel dizin"
ADD_NAME_HELP = "Drive altında kullanılacak klasör adı"
FOLDER_ADDED = "Klasör eklendi. Kimlik: {id}"
INIT_START = "İlk eşitlemeyi başlat"
INIT_RESYNC = "Yeniden eşitle"
INIT_PREVIEW = "Eşitleme planı hazırlanıyor…"
INIT_SUMMARY = "Drive'a {to_remote} dosya, bilgisayara {to_local} dosya, {deletes} silme"
INIT_DETAILS = "Planlanan işlemler (ilk 50)"
INIT_WARNING = "DİKKAT: Bu plan dosya silmeyi içeriyor. Kayıtları inceleyin ve yedeğiniz olduğundan emin olun. Devam etmek için klasör adını tam olarak yazın: {name}"
INIT_UNKNOWN = "Yönü belirlenemeyen {count} işlem var. Başlatmadan önce ayrıntılı kayıtları inceleyin."
INIT_CONFIRM = "Başlat"
INIT_OFFER = "İlk eşitlemeyi şimdi başlat?"
INIT_OFFER_BODY = "{name} eklendi. Önce değişiklik planını inceleyebilirsiniz."
INIT_LAUNCH_FAILED = "Eşitleme başlatılamadı. systemd kullanıcı oturumunu ve kayıtları kontrol edin."
INIT_PLAN_INVALID = "Eşitleme planı okunamadı; işlem başlatılmadı."
RCLONE_ARCH = "Otomatik rclone kurulumu yalnızca Linux amd64 ve arm64 için destekleniyor."
RCLONE_CHECKSUM = "rclone SHA256 doğrulaması başarısız; hiçbir dosya kurulmadı."
RCLONE_INSTALL_FAILED = "rclone indirilemedi veya doğrulanamadı. Mevcut dosya korundu. https://rclone.org/downloads/"
RCLONE_INSTALLED = "rclone SHA256 ile doğrulanıp kuruldu: {path}"

# Agent-facing CLI and diagnostics retain Turkish human-readable strings.
HELP.update({"doctor": "Kurulumu denetle ve somut çözümleri göster",
             "folder": "Eşitlenecek klasörleri yönet",
             "config": "Güvenli ayarları ve hariç tutmaları yönet",
             "progress": "Bir klasörün canlı ilerlemesini göster"})
MACHINE_JSON_HELP = "Sonucu JSON olarak yazdır"
OFFLINE_HELP = "Uzak erişim denetimini atla"
BACKGROUND_HELP = "İlk eşitlemeyi ayrı systemd kullanıcı işi olarak başlat"
YES_REQUIRED = "Bu işlem açık onay gerektirir. Onaydan sonra --yes ile tekrar çalıştırın."
CLI_DONE = "Yapılandırma güncellendi."
BACKGROUND_STARTED = "İlk eşitleme arka planda başlatıldı: {unit}"
BACKGROUND_FAILED = "Arka plan işi başlatılamadı; systemd kullanıcı oturumunu ve journalctl kayıtlarını kontrol edin."
GI_REQUIRED = "Python GI/GLib/Gio gerekli. Paketler için doctor --offline çalıştırın."
CONFIG_KEY_HELP = "İzin verilen ayar anahtarı"
CONFIG_VALUE_HELP = "Yeni ayar değeri"
EXCLUDE_HELP = "Hariç tutma deseni (başında - varsa önce -- kullanın)"
PROGRESS_LINE = "{id}: {status} — {progress}"
DOCTOR_LINE = "{id}: {status} — {message}\n  Çözüm: {fix}"
APPINDICATOR_HINT = "GNOME Uzantılar'da AppIndicator and KStatusNotifierItem Support etkin olmalı."
DOCTOR_MESSAGES = {
    "environment.os": "Linux işletim sistemi denetimi.",
    "environment.desktop": "Masaüstü ve X11/Wayland oturum bilgileri.",
    "python.version": "Python 3.12 veya üzeri gerekli.",
    "gi.GLib": "GLib 2.0 GI kitaplığı denetimi.",
    "gi.Gio": "Gio 2.0 GI kitaplığı denetimi.",
    "gi.GObject": "GObject 2.0 GI kitaplığı denetimi.",
    "gi.Gtk3": "Tepsi için GTK 3.0 GI kitaplığı denetimi.",
    "gi.Gtk4": "Pencere için GTK 4.0 GI kitaplığı denetimi.",
    "gi.Gdk4": "Pencere için Gdk 4.0 GI kitaplığı denetimi.",
    "gi.Adw": "Libadwaita 1 GI kitaplığı denetimi.",
    "gi.Notify": "Notify 0.7 GI kitaplığı denetimi.",
    "gi.AyatanaAppIndicator3": "AyatanaAppIndicator3 0.1 GI kitaplığı denetimi.",
    "gi.Nautilus": "Nautilus 4.0 GI kitaplığı denetimi.",
    "desktop.appindicator": "GNOME'da tepsi simgesi için AppIndicator eklentisini kontrol edin.",
    "rclone.version": "rclone 1.66 veya üzeri gerekli.",
    "remote.configured": "Yapılandırılan rclone bağlantısı denetimi.",
    "remote.reachable": "Google Drive erişim denetimi (çevrimdışı seçeneğiyle atlanabilir).",
    "install.files": "Kurulu uygulama, başlatıcı ve Nautilus dosyaları denetimi.",
    "install.units": "systemd kullanıcı birimi dosyaları denetimi.",
    "systemd.drivesync.enabled": "Arka plan servisi oturum başlangıcında etkin olmalı.",
    "systemd.drivesync.active": "Arka plan servisi çalışıyor olmalı.",
    "systemd.drivesync-tray.enabled": "Tepsi servisi oturum başlangıcında etkin olmalı.",
    "systemd.drivesync-tray.active": "Tepsi servisi çalışıyor olmalı.",
    "config.valid": "Yapılandırma dosyası ve şema denetimi.",
    "inotify.watches": "inotify sınırı ile tahmini dizin izleyici ihtiyacı karşılaştırması.",
    "state.folders": "Klasörlerin ilk eşitleme, hata ve ilerleme özeti.",
}


# --- remote change detection ---
REMOTE_CHANGE_CHECK_FAILED = "Drive değişiklik denetimi başarısız oldu; tam eşitlemeye dönüldü."
