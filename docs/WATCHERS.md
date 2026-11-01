# Yerel değişiklik izleme

Daemon, etkin ve ilk eşitlemesi tamamlanmış her klasörde dizin başına bir
`Gio.FileMonitor` kurar (Linux’ta inotify). Hariç tutulan dizinler ve sembolik
bağlantılar taranmaz. Yeni veya taşınmış alt dizinler izlemeye alınır. Dosya
olayları `debounce_seconds` boyunca biriktirilir; her olay süreyi yeniden
başlatır. Sonunda ortak kuyruğa `local-change` isteği gider. Duraklatma ve
silme/yeniden eşitleme korumaları aynı kuyrukta uygulanır.

Klasörün kendi eşitlemesi boyunca ve bittikten sonraki 5 saniyede olaylar yok
sayılır. Ardından, eşitlemenin indirdiği/yeni oluşturduğu dizinleri kapsamak
üzere izleyiciler yenilenir. Böylece kendi yazmalarımız eşitleme döngüsü yaratmaz.
Bu sırada yapılan kullanıcı değişiklikleri sonraki aralıklı kontrolde yakalanır.

`config.json` içindeki `max_monitors` pozitif tamsayıdır; varsayılanı **20000**.
Sınır bütün eşitleme klasörlerinin toplamı içindir. Bir klasörün kurulumu veya
büyümesi sınırı aşarsa onun kısmi izleyicileri kaldırılır; diğer klasörler
izlenmeye devam eder. Aynı klasör için süreç boyunca yalnızca bir Türkçe uyarı
`daemon.log` dosyasına yazılır. İşletim sisteminin izleyici sınırı veya dizin
okuma hatası da aynı güvenli aralıklı kontrol davranışına döner.

İzleyici optimizasyonu, eşitlemenin filtre otoritesi değildir: gerçek eşitleme
mevcut `filters.render` çıktısını kullanır. İzleyici; `*`, `**`, `?`, karakter
sınıfları, köke bağlı yollar ve `/` ile biten dizin kurallarını tanır. Daha
karmaşık rclone süslü parantez/regex kuralları ek izleyiciler bırakabilir;
aralıklı kontrol ve rclone filtreleri aynen uygulanır.

## Salt okunur ölçüm

Test paketinden bağımsız komut (kendi örnek dizininizi seçin):

```sh
/usr/bin/python3 tools/measure_watchers.py ~/Projects
/usr/bin/python3 tools/measure_watchers.py ~/Projects --max-monitors 100000
```

Araç yalnızca dizin adlarını tarar ve geçici Gio izleyicilerini kurup kapatır;
dosya içeriklerini, kullanıcı config/state/data dizinlerini kullanmaz, rclone
başlatmaz ve Drive'a bağlanmaz. Yerleşik varsayılan hariç tutmaları kullanır.
Sonuçlar disk önbelleğine ve ağacın o andaki durumuna bağlıdır. Çıktı dosya
sayısını değil, hariç tutmalar sonrasında izlenen dizin sayısını gösterir.
Ölçüm gerçek kullanıcı yapılandırmasını değiştirmez.

## Ajanlar için inotify denetimi

`drivesync-cli doctor --offline --json` içindeki `inotify.watches` denetimi,
`/proc/sys/fs/inotify/max_user_watches` değerini hariç tutmalar sonrası tahmini
izleyici ihtiyacı ve `max_monitors` ile karşılaştırır. Tahmin en fazla 100.000
dizine bakar; eksik tarama veya yetersiz sınır `warn` üretir. Etkin fakat henüz
başlatılmamış klasörler de gelecekteki ihtiyaç için hesaba katılır.

Sistem değerini salt okunur görmek için:

```bash
sysctl fs.inotify.max_user_watches
```

İhtiyaç sınırı aşıyorsa aralıklı eşitleme çalışmaya devam eder. Daha az klasör
seçebilir veya uygun hariç tutmalar ekleyebilirsiniz; filtre değişiklikleri
başlatılmış klasörlerde yeniden önizleme/onay gerektirir. Sistem sınırını
artırmak gerekiyorsa kullanıcıdan sudo onayı alın; seçilen değeri `N` yerine
koyarak `sudo sysctl -w fs.inotify.max_user_watches=N` çalıştırın. Bu değişiklik
geçicidir; kalıcı sistem ayarını kullanıcıyla ayrıca kararlaştırın. Diğer
uygulamalar da kullanıcıya ait izleyici kotasını paylaşır. DriveSync'in kendi
`max_monitors` sınırı ayrıca geçerlidir; [şema](SPEC.md) ayrıntıları açıklar.
