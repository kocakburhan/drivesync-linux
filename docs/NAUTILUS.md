# Nautilus eklentisi (Zorin OS 18 / Nautilus 46)

Eklenti `python3-nautilus`, `Nautilus 4.0` GI arayüzünü kullanır; GTK sürümü seçmez.
`install.sh`, `${XDG_DATA_HOME:-$HOME/.local/share}/nautilus-python/extensions/drivesync.py`
yükleyicisini ve `icons/hicolor/scalable/emblems/` altına üç özgün SVG kurar.
Yükleyici kurulu `drivesync.nautilus_ext` sınıfını alır; aynı adlı Python paketini
gölgelememesi için paket arama yolu da tanımlanır. Özel `--prefix` desteklenir.
Kaldırma betiği yalnızca bu yükleyiciyi ve DriveSync simgelerini kaldırır.

Kurulumdan sonra açık Dosyalar pencerelerini kapatıp `nautilus -q` ile Nautilus'u
sonlandırmak ve yeniden açmak gerekir. **Kurulum betiği bunu çalıştırmaz.**
Bu geliştirme sırasında da Nautilus yeniden başlatılmadı; gerçek kullanıcı
config/state/data dizinlerine eklenti veya deneme dosyası yazılmadı.

## Nautilus 46'da işaretler nerede?

GNOME 43'ün GTK 4 geçişinde çizim değişti: işaretler artık ana dosya simgesine
bindirilen eski köşe süsleri olarak düşünülmemeli. GNOME'un sürüm notları
43.alpha'da ana simgeden bağımsız emblem çiziminin geri geldiğini, 44.1'de
uzantıların özel emblemlerinin yeniden gösterilmesinin düzeltildiğini kaydediyor.
Bu nedenle “43 sonrası bütün emblemler kaldırıldı” sonucu doğru değil.
Kaynak: [GNOME Nautilus NEWS](https://github.com/GNOME/nautilus/blob/main/NEWS).

Kurulu `/usr/bin/nautilus` dosyasındaki **46 sürümünün gömülü GtkBuilder
şablonları**, uygulamayı başlatmadan `gresource extract` ile okundu:

- `/org/gnome/nautilus/ui/nautilus-grid-cell.ui`: `emblems_box`, simgenin sağında,
  dikey bir kutu; 16 piksel genişlik isteği ve 6 piksel aralık var.
- `/org/gnome/nautilus/ui/nautilus-name-cell.ui`: liste görünümünde `emblems_box`,
  dosya adından sonra yatay bir kutu; yine 6 piksel aralık kullanıyor.

**Sonuç:** Bu makinenin Nautilus 46 şablonlarında hem ızgara hem liste görünümünde
emblem alanı var. Liste görünümünde işareti dosya adının yanında, ızgarada simgenin
sağında aramak gerekir. Bu, yerel şablon incelemesidir; gerçek Dosyalar penceresinde
piksel/görünürlük denemesi yapılmadı. Tema, yakınlaştırma ve dar satır alanı gerçek
görünürlüğü etkileyebilir; her görünümde eski köşe rozeti görünümü beklenmemeli.
`hicolor` altına kurulum, etkin tema kendi DriveSync simgesini sağlamadığında
standart tema geri dönüşünü sağlar.

Güvenli manuel kontrol: kurulumdan ve kullanıcı tarafından yeniden başlatmadan
sonra başlatılmış bir eşitleme klasörünü hem ızgara hem liste görünümünde açın;
✓, yerel dosyayı değiştirdikten sonra ↻, klasör hatasında ⚠ beklenir. Hariç tutulan
ve henüz başlatılmamış öğelerde işaret beklenmez. Gerekirse F5 ile yenileyin.
Bu kontrol için yeni bir Drive eşitlemesi başlatmak gerekmez.

## Durumun anlamı ve maliyeti

- ✓: dosyanın yerel mtime değeri klasörün `last_success` zamanını geçmiyor.
- ↻: mtime daha yeni veya klasör `syncing` / `queued` durumunda.
- ⚠: klasör `error`, `blocked_delete` veya `needs_resync` durumunda.
- Başlatılmamış/devre dışı klasör, hariç yol, eksik/geçersiz veri: işaret yok.

Bu bir dosya hash'i veya Drive sorgusu değildir; **yaklaşık durumdur**. Alt klasörün
mtime değeri bütün alt ağacın değişimini göstermez. Uzakta henüz kontrol edilmemiş
bir değişiklik bu yerel işaretten anlaşılamaz.

JSON dosyaları salt okunur; config varsayılanı oluşturulmaz ve durum kilidi alınmaz.
Her dosya bilgisi çağrısı yerel yolda bir kez eşzamanlı `os.stat` yapar;
sembolik bağlantıları izlemez. Config/state aynı ana döngüde SnapshotCache ile
okunur: en erken bir saniye arayla mtime/size/inode anahtarı kontrol edilir,
yalnızca değişen JSON okunur (dosya başına en çok 4 MiB). İlk çağrı işareti
hemen hesaplar; arka plan işçisi yoktur. Bir saniyelik zamanlayıcı yalnızca
D-Bus bağlantısını ve config/state değişikliklerini kontrol eder. rclone veya
eşzamanlı D-Bus çağrısı yoktur. Hatalarda eski başarı işaretleri temizlenir.

Görülen son 512 `Nautilus.FileInfo` nesnesi yalnızca weak reference ile izlenir;
Nautilus'un görünürlük bilgisini ayrıca veren bir API'si olmadığı için bu kayıt
son görülen canlı nesneleri temsil eder. `StatusChanged` sinyalinde yalnızca
etkilenen klasörlerin nesnelerine `invalidate_extension_info()` çağrılır.
Config/state değişince yalnızca etkilenen klasörlerdeki nesneler yenilenir;
yerel mtime her dosya bilgisi çağrısında tekrar okunur. Görünümde
512'den fazla öğe varsa eski kayıtlar düşer; tekrar bilgi istendiğinde geri alınır.

Hariç tutma mantığı watcher'ın GI bağımsız rclone glob alt kümesini kullanır:
`*`, `?`, `**`, karakter sınıfları, köke bağlı yollar ve dizin sonundaki `/`.
Desteklenmeyen gelişmiş rclone deseni (ör. `{a,b}`) varsa yanlış ✓ vermemek için
eklenti işaret/menü göstermeyi bırakır; gerçek eşitleme filtresi rclone'da çalışır.

Sağ tık menüsündeki “DriveSync: Şimdi eşitle”, ilgili klasör kimliğiyle asenkron
`SyncNow` çağırır; daemon'un güvenlik/başlatılma kuralları geçerlidir. Çoklu seçimde
her farklı klasör bir kez çağrılır. “DriveSync: Drive'da göster” tek seçimde dosya
adını URL kodlayarak Drive aramasını açar; belirli bir Drive dosya kimliğine bağlanmaz.

API kaynakları:
[InfoProvider](https://gnome.pages.gitlab.gnome.org/nautilus-python/class-nautilus-python-info-provider.html),
[MenuProvider](https://gnome.pages.gitlab.gnome.org/nautilus-python/class-nautilus-python-menu-provider.html).
