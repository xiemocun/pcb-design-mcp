idf_component_register(SRCS "main.c" "am2302_decode.c" INCLUDE_DIRS "." REQUIRES esp_driver_gpio esp_driver_rmt esp_timer esp_system esp_app_format)
