# Privacy policy template / Gizlilik politikası şablonu

Replace every bracketed field before publishing this text on a public HTTPS page.
This file is a template, not a hosted policy. A Turkish version follows the English one.

## [Your application name] — Privacy policy

Last updated: [YYYY-MM-DD]
Operator and contact: [Your name/organization and contact method]
Home page: [Your public application home page URL]

This application is a local Linux desktop folder synchronization tool built from DriveSync,
using rclone bisync. It accesses Google Drive with the user's explicit OAuth permission.
Full Drive scope allows reading, creating, changing and deleting Drive files; the application
operates on folders configured by the user. Transfers take place between the user's computer
and Google Drive. The operator does not receive file contents, OAuth tokens or credentials
through a separate application server. [Revise this statement if your deployment adds any server.]

OAuth client details are stored locally in the user's DriveSync configuration directory.
rclone stores authorization tokens in its own local configuration. Local sync state and logs
can contain file names and paths. The application does not add advertising or analytics.
[Describe any additional collection in your deployment, including retention and recipients.]

Two-way sync propagates changes and deletions. Drive deletions use trash by default;
local deletions do not use the desktop trash. Independent backups remain the user's responsibility.
Users can stop sync, remove configured folders without deleting their files, delete local
application configuration, and revoke authorization in their Google Account's third-party connections.
Google's privacy policy also governs processing by Google: https://policies.google.com/privacy.

The use and transfer of information received from Google APIs will adhere to the
Google API Services User Data Policy, including the Limited Use requirements.
Policy: https://developers.google.com/terms/api-services-user-data-policy.
[Ensure your actual deployment complies before publishing this commitment.]

## [Uygulama adınız] — Gizlilik politikası

Son güncelleme: [YYYY-AA-GG]
İşleten ve iletişim: [Adınız/kuruluşunuz ve iletişim yöntemi]
Ana sayfa: [Herkese açık uygulama ana sayfası URL'si]

Bu uygulama DriveSync kodundan oluşturulmuş yerel bir Linux klasör eşitleme aracıdır;
rclone bisync kullanır. Google Drive'a kullanıcının açık OAuth izniyle erişir.
Tam Drive kapsamı dosya okuma, oluşturma, değiştirme ve silmeye izin verir; uygulama
kullanıcının yapılandırdığı klasörlerde işlem yapar. Aktarımlar bilgisayar ve Google Drive
arasında gerçekleşir. İşleten ayrı bir uygulama sunucusu üzerinden dosya içeriklerini,
OAuth jetonlarını veya kimlik bilgilerini almaz. [Sunucu eklerseniz bu açıklamayı değiştirin.]

OAuth istemci bilgileri yerel DriveSync yapılandırma dizininde saklanır. rclone yetki
jetonlarını kendi yerel yapılandırmasında tutar. Yerel durum/günlükler dosya adları ve
yollar içerebilir. Uygulama reklam veya analitik eklemez.
[Dağıtımınızdaki ek veri toplama, saklama süreleri ve alıcıları açıklayın.]

İki yönlü eşitleme değişiklikleri ve silmeleri karşı tarafa yansıtır. Drive silmeleri
varsayılan olarak çöp kutusunu kullanır; yerel silmeler masaüstü çöp kutusuna gitmez.
Ayrı yedek almak kullanıcının sorumluluğudur. Kullanıcı eşitlemeyi durdurabilir,
klasörleri dosyalarını silmeden listeden çıkarabilir, yerel yapılandırmayı silebilir ve
Google hesabının üçüncü taraf bağlantılarından yetkiyi iptal edebilir.
Google'ın işlemleri için https://policies.google.com/privacy geçerlidir.

Google API'lerinden alınan bilgilerin kullanımı ve aktarımı, Limited Use gereklilikleri
dahil Google API Services User Data Policy'ye uyacaktır.
Politika: https://developers.google.com/terms/api-services-user-data-policy.
[Bu taahhüdü yayınlamadan önce dağıtımınızın gerçekten uyduğundan emin olun.]
