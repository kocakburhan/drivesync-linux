[Türkçe](GOOGLE-CLIENT-ID.md) | [English](GOOGLE-CLIENT-ID.en.md)

# Google Drive için kendi istemci kimliğinizi oluşturun

DriveSync, sizin oluşturduğunuz **OAuth Desktop app** kimliğiyle rclone üzerinden Google'a bağlanır.
Bu işlem ücretsizdir; Google Drive API için bu kullanımda **faturalandırma hesabı veya kredi kartı gerekmez**.
Cloud Console'daki “Start free / Free trial” teklifini başlatmanız gerekmez.
Google arayüzü dilinize/hesabınıza göre farklı görünebilir; aşağıdaki İngilizce adlar Google Auth Platform içindir.
Kurumsal Workspace hesaplarında yönetici politikaları bu kurulumu engelleyebilir.

## 1. Proje oluşturun

1. [Google Cloud Console](https://console.cloud.google.com/) sayfasını açın ve Drive için kullanacağınız hesapla giriş yapın.
2. Üst çubuktaki proje seçicisine → **New project** tıklayın.
3. Genel bir proje adı yazın (örneğin `DriveSync Personal`). Kuruluş seçimi istenirse kişisel hesapta **No organization** kullanın.
4. **Create**'e basın. Oluşmasını bekleyin ve üst seçiciden bu projeyi seçin.
   Ekranın başka bir projeyi göstermediğinden her aşamada emin olun.

## 2. Google Drive API'yi etkinleştirin

1. Sol menü **APIs & Services → Library**.
2. `Google Drive API` arayın, Google'ın **Google Drive API** sonucunu açın.
3. **Enable**'a basın. **Manage** görünüyorsa zaten etkindir.

## 3. Google Auth Platform başlangıcını tamamlayın

1. Sol menüden **Google Auth Platform**'u açın (menüde yoksa üst aramada bu adı arayın).
   Eski **APIs & Services → OAuth consent screen** bağlantısı da bu alana yönlendirebilir.
2. **Get started**'a tıklayın.
3. **App Information**: App name için `DriveSync Personal` gibi bir ad yazın;
   **User support email** alanında kendi e-posta adresinizi seçin. **Next**.
4. **Audience**: **External** seçin. Kişisel Gmail hesaplarında Internal kullanılamaz. **Next**.
5. **Contact Information**: bildirimler için kendi e-posta adresinizi girin. **Next**.
6. Google API Services User Data Policy onay kutusunu okuyup işaretleyin; **Continue → Create**.

Bu adresler sizin hesabınıza aittir; DriveSync deposuna veya bir hata raporuna yazılmaz.

## 4. Branding ve yayın adresleri

**Google Auth Platform → Branding** sayfasını açın. Uygulama adı ve destek adresini kontrol edin.
Yayın hazırlığı için **App domain** alanlarını tamamlayın: **Application home page**,
**Application privacy policy link** ve **Authorized domains**. Geçerli, herkese açık adresler kullanın.
İki seçenek vardır:

- **(a) Projenin herkese açık sayfaları**:
  - Home page: `https://kocakburhan.github.io/drivesync/`
  - Privacy policy: `https://kocakburhan.github.io/drivesync/gizlilik.html`
  - **Authorized domains → Add domain**: `kocakburhan.github.io` (https veya yol eklemeyin).
- **(b) Kendi kopyanız**: [gizlilik şablonunu](privacy-template.md) uyarlayın, bir ana sayfa ve gizlilik
  sayfasını kendi kontrol ettiğiniz sitede yayınlayın. Bu iki HTTPS adresini ve kendi yetkili alanınızı girin.
  Şablondaki köşeli parantezli alanları gerçek bilgilerinizle doldurun; Markdown dosyasının yerel yolu bir web adresi değildir.

Adresleri önce tarayıcıda açıp erişilebilir ve uygulamanız için doğru olduklarını kontrol edin.
Proje sayfaları yayınlanmamışsa/erişilemiyorsa (b)'yi kullanın. Alan sahipliği doğrulaması istenirse
kendi kontrol etmediğiniz alanı doğrulayamazsınız; kendi sitenizi kullanın.
Bu alanları doldurmak Google'ın doğrulama sürecini tamamlamaz. Logo yüklemek zorunlu değildir;
gereksiz marka/logo değişiklikleri doğrulama isteklerine yol açabilir. **Save**'e basın.

## 5. Kapsamı tanımlayın ve uygulamayı yayınlayın

1. **Data Access → Add or remove scopes**'a girin.
2. Google Drive API'nin `https://www.googleapis.com/auth/drive` kapsamını seçin.
   Listede yoksa manuel scope alanına bu URI'yi ekleyin; **Update → Save**.
3. **Audience** sayfasını açın. **Publishing status** başlangıçta **Testing** olabilir.
4. **Publish app**'e ve onay penceresinde **Confirm**'e basın. **In production** durumunu görün.

**Neden yayınlıyoruz?** External + Testing uygulamalarında tam Drive kapsamı için verilen yenileme
jetonu 7 gün sonra sona erer. In production bu haftalık sona erme koşulunu kaldırır;
jetonlar yine kullanıcı iptali veya Google güvenlik politikaları gibi başka nedenlerle geçersizleşebilir.
Publish app, uygulamanın Google tarafından doğrulandığı anlamına gelmez. Kendi kullanımınız için
doğrulanmamış uygulama uyarısı ve kullanıcı sınırı kalabilir; geniş kitleye dağıtımda Google doğrulaması gerekebilir.

Testing'de geçici olarak kalmanız gerekirse **Audience → Test users → Add users** ile kendi Google hesabınızı ekleyin.
Bu, 7 günlük süreyi kaldırmaz. Her DriveSync kullanıcısının kendi istemcisini oluşturması önerilir.

**Neden tam Drive izni?** İki yönlü eşitleme, Drive'da zaten bulunan seçilmiş klasörlerin dosyalarını
okumayı, değiştirmeyi ve silmeyi gerektirir. `drive.file` yalnızca uygulamanın oluşturduğu/seçiciyle
paylaşılan dosyalarla sınırlıdır; burada rclone `scope=drive` kullanır. İzni yalnızca güvendiğiniz kendi
uygulamanıza verin. Bu izin, seçtiğiniz eşitleme klasörlerinin dışına da teknik olarak erişebilir;
DriveSync'in yapılandırılmış klasör sınırı erişimi uygulama düzeyinde daraltır.

## 6. Desktop app istemcisini oluşturup JSON indirin

1. **Google Auth Platform → Clients → Create client**.
2. **Application type → Desktop app** seçin. Web application veya Service account seçmeyin.
3. Name alanına `DriveSync Desktop` gibi genel bir ad verin; **Create**.
4. Oluşturma penceresinde **Download JSON** düğmesini kullanın.
   Görünmüyorsa **Clients** listesindeki yeni istemciyi açıp JSON indirme simgesini/düğmesini kullanın.
5. Dosyayı güvenli bir yerde saklayın. İçinde `installed`, `client_id`, `client_secret` alanları bulunur.
   GitHub'a veya herkese açık bir klasöre koymayın; içeriğini terminale yazdırmayın.

Desktop app için web redirect URI eklemeniz gerekmez; rclone yerel tarayıcı yetkilendirme akışını yönetir.

## 7. DriveSync üzerinden yetki verin

Terminal'de dosya adını indirdiğiniz gerçek dosyayla değiştirin:

```bash
drivesync-cli setup-remote --client-json "$HOME/Downloads/client_secret_….json"
```

1. Açılan tarayıcıda doğru hesabı seçin.
2. **Google hasn't verified this app** ekranı çıkarsa uygulama adının sizin oluşturduğunuz ad olduğundan emin olun.
3. Yalnızca kendi uygulamanız için **Advanced → Go to [uygulama adı] (unsafe)** yolunu izleyin
   (Türkçe arayüzde **Gelişmiş → … uygulamasına git (güvenli değil)**).
4. Drive erişimini okuyup **Continue / Allow** ile onaylayın. Gerekirse “Select all”/Drive kutusunu işaretleyin.
5. rclone'un başarı sayfası açılınca Terminal'e dönün. DriveSync `rclone about gdrive:` ile erişimi doğrulayıp
   “Google Drive bağlantısı kuruldu ve erişim doğrulandı” yazmalıdır.

Tarayıcı açılmıyorsa grafik oturumdaki varsayılan tarayıcınızı düzeltip tekrar deneyin;
bu yardımcı yerel tarayıcı akışı içindir. `--remote AD` farklı bir rclone bağlantısı oluşturur;
DriveSync config'inin `remote` alanını da `AD:` olarak düzenleyin.

Mevcut bağlantı izinsiz değiştirilmez. Jeton süresi dolduğunda önce Audience durumunu düzeltin, sonra:

```bash
drivesync-cli setup-remote --client-json "$HOME/Downloads/client_secret_….json" --replace
```

`--replace`, önce rclone.conf'un özel bir yedeğini `~/.config/drivesync/` altına yazar.
İstemci dosyası da bu dizinde `google-client.json` olarak 0600 izinleriyle saklanır.
Bu yedekler diğer rclone bağlantılarının jetonlarını da içerebilir; asla paylaşmayın.
Yetkiyi sonradan Google hesabınızın üçüncü taraf bağlantıları sayfasından iptal edebilirsiniz.

Sonraki adım: [README — klasör ekleme ve ilk eşitleme](../README.md#6-klasör-ekleyin-ve-ilk-eşitlemeyi-başlatın).
