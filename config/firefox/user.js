user_pref("network.proxy.type", 0);
// Cookie-Banner automatisch ablehnen (Mozilla-Regeln); uBlock Origin blendet den Rest mit den Cookie-Listen aus.
user_pref("cookiebanners.service.mode", 1);
user_pref("cookiebanners.service.mode.privateBrowsing", 1);
user_pref("browser.cache.disk.enable", true);
user_pref("browser.cache.memory.enable", true);
user_pref("browser.cache.memory.capacity", -1);
user_pref("browser.startup.couldRestoreSession.count", -1);
user_pref("network.dns.disableIPv6", true);
user_pref("network.dnsCacheEntries", 1000);
user_pref("network.dnsCacheExpiration", 3600);
user_pref("gfx.webrender.all", false);
user_pref("layers.acceleration.force-enabled", false);
user_pref("nglayout.initialpaint.delay", 0);
