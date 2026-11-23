#include "radio.h"
#include "pair.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_wifi.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "lwip/sockets.h"
#include <string.h>
#include <errno.h>
#include <assert.h>

atomic_bool ride_link_up;
static int udp = -1;
static struct sockaddr_in peer;
static void event(void *arg, esp_event_base_t base, int32_t id, void *data) {
    (void)arg; (void)data;
    if (base != WIFI_EVENT) return;
#if RIDE_ROLE_AP
    if (id == WIFI_EVENT_AP_STACONNECTED) atomic_store(&ride_link_up, true);
    if (id == WIFI_EVENT_AP_STADISCONNECTED) atomic_store(&ride_link_up, false);
#else
    if (id == WIFI_EVENT_STA_START || id == WIFI_EVENT_STA_DISCONNECTED) {
        atomic_store(&ride_link_up, false);
        esp_err_t e = esp_wifi_connect();
        if (e != ESP_OK) ESP_LOGW("radio", "connect: %s", esp_err_to_name(e));
    }
    if (id == WIFI_EVENT_STA_CONNECTED) atomic_store(&ride_link_up, true);
#endif
}
void ride_radio_init(void) {
    esp_err_t e = nvs_flash_init();
    if (e == ESP_ERR_NVS_NO_FREE_PAGES || e == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase()); e = nvs_flash_init();
    }
    ESP_ERROR_CHECK(e);
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_t *net;
#if RIDE_ROLE_AP
    net = esp_netif_create_default_wifi_ap(); assert(net);
    ESP_ERROR_CHECK(esp_netif_dhcps_stop(net));
#else
    net = esp_netif_create_default_wifi_sta(); assert(net);
    ESP_ERROR_CHECK(esp_netif_dhcpc_stop(net));
#endif
    esp_netif_ip_info_t ip = {0};
    IP4_ADDR(&ip.ip, 192, 168, 77, RIDE_ROLE_AP ? 1 : 2);
    IP4_ADDR(&ip.gw, 192, 168, 77, 1);
    IP4_ADDR(&ip.netmask, 255, 255, 255, 0);
    ESP_ERROR_CHECK(esp_netif_set_ip_info(net, &ip));
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, event, NULL));
    wifi_country_t country = {.cc = "CN", .schan = 1, .nchan = 13,
                              .max_tx_power = 20, .policy = WIFI_COUNTRY_POLICY_MANUAL};
    ESP_ERROR_CHECK(esp_wifi_set_country(&country));
    wifi_config_t config = {0};
    wifi_interface_t iface;
#if RIDE_ROLE_AP
    iface = WIFI_IF_AP;
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
    memcpy(config.ap.ssid, RIDE_SSID, sizeof(RIDE_SSID));
    memcpy(config.ap.password, RIDE_PSK, sizeof(RIDE_PSK));
    config.ap.ssid_len = sizeof(RIDE_SSID) - 1;
    config.ap.channel = 6; config.ap.max_connection = 1;
    config.ap.authmode = WIFI_AUTH_WPA2_PSK;
    config.ap.pmf_cfg.required = true;
#else
    iface = WIFI_IF_STA;
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    memcpy(config.sta.ssid, RIDE_SSID, sizeof(RIDE_SSID));
    memcpy(config.sta.password, RIDE_PSK, sizeof(RIDE_PSK));
    config.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;
    config.sta.pmf_cfg.capable = true; config.sta.pmf_cfg.required = true;
#endif
    ESP_ERROR_CHECK(esp_wifi_set_config(iface, &config));
    ESP_ERROR_CHECK(esp_wifi_set_protocol(iface, WIFI_PROTOCOL_LR));
    ESP_ERROR_CHECK(esp_wifi_set_bandwidth(iface, WIFI_BW_HT20));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    /* Initial conducted power cap 13 dBm. Not a radio certification assertion. */
    ESP_ERROR_CHECK(esp_wifi_set_max_tx_power(52));
    udp = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP); assert(udp >= 0);
    struct timeval timeout = {.tv_sec = 0, .tv_usec = 100000};
    assert(setsockopt(udp, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout)) == 0);
    struct sockaddr_in local = {.sin_family = AF_INET, .sin_port = htons(4242), .sin_addr.s_addr = htonl(INADDR_ANY)};
    assert(bind(udp, (struct sockaddr *)&local, sizeof(local)) == 0);
    peer.sin_family = AF_INET; peer.sin_port = htons(4242);
    peer.sin_addr.s_addr = inet_addr(RIDE_ROLE_AP ? "192.168.77.2" : "192.168.77.1");
    ESP_LOGI("radio", "offline LR %s, channel 6, 13 dBm cap", RIDE_ROLE_AP ? "A/AP" : "B/STA");
}
bool ride_send(const ride_frame *f) {
    if (!atomic_load(&ride_link_up)) return false;
    uint8_t packet[RIDE_PACKET]; ride_pack(packet, f);
    return sendto(udp, packet, sizeof(packet), MSG_DONTWAIT,
                  (struct sockaddr *)&peer, sizeof(peer)) == sizeof(packet);
}
bool ride_receive(ride_frame *f) {
    uint8_t packet[RIDE_PACKET + 1];
    struct sockaddr_in src; socklen_t sl = sizeof(src);
    int n = recvfrom(udp, packet, sizeof(packet), 0, (struct sockaddr *)&src, &sl);
    return n == RIDE_PACKET && src.sin_addr.s_addr == peer.sin_addr.s_addr &&
           src.sin_port == peer.sin_port && ride_unpack(f, packet, n);
}
