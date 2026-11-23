idf_component_register(SRCS "main.c" "board_audio.c" "radio.c" "transport.c"
    INCLUDE_DIRS "." "../provisioned"
    REQUIRES esp_driver_i2s esp_driver_i2c esp_driver_gpio esp_wifi esp_netif
             nvs_flash esp_timer esp_psram espressif__esp_codec_dev
             espressif__esp_audio_codec espressif__esp-sr espressif__esp-dl)
