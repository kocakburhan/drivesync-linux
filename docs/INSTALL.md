# Kurulum / Installation

Güncel adım adım kullanıcı kurulumu [Türkçe README](../README.md#kurulum-adım-adım)
ve [English README](../README.en.md#installation-step-by-step) içindedir.
Google OAuth: [Türkçe](GOOGLE-CLIENT-ID.md) / [English](GOOGLE-CLIENT-ID.en.md).
Geliştirme ve güvenli test kurulumu: [CONTRIBUTING.md](../CONTRIBUTING.md).

Betikler `--no-enable` ile systemctl çağrılarını atlar. `--prefix DİZİN` veya
`DRIVESYNC_PREFIX` bin/lib hedefini değiştirir; masaüstü/ikonlar `XDG_DATA_HOME`,
birimler `DRIVESYNC_SYSTEMD_DIR` (varsayılan `XDG_CONFIG_HOME/systemd/user`) altına gider.
Gerçek kullanıcı dizinlerine dokunmayan kurulum testleri geçici HOME ve XDG kökleri kullanır.
