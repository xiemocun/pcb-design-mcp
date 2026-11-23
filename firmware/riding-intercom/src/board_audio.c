/* Korvo-2 V3.1 audio wiring. Driver setup follows Espressif's Apache-2.0
 * esp_codec_dev 1.6.2 test_board.c and esp32_s3_korvo_2 5.0.0 bsp_audio.c.
 * Copyright (c) 2023-2026 Espressif Systems (Shanghai) CO LTD (reference code).
 * SPDX-License-Identifier: Apache-2.0 */
#include "board_audio.h"
#include "driver/i2c_master.h"
#include "driver/i2s_std.h"
#include "driver/gpio.h"
#include "esp_codec_dev_defaults.h"
#include "esp_check.h"
#include <assert.h>

esp_codec_dev_handle_t ride_adc, ride_dac;
void ride_board_audio_init(void) {
    /* The PA must stay disabled through startup and until volume is set. */
    ESP_ERROR_CHECK(gpio_set_direction(GPIO_NUM_48, GPIO_MODE_OUTPUT));
    ESP_ERROR_CHECK(gpio_set_level(GPIO_NUM_48, 0));
    i2c_master_bus_handle_t bus;
    i2c_master_bus_config_t bc = {
        .i2c_port = 0, .sda_io_num = GPIO_NUM_17, .scl_io_num = GPIO_NUM_18,
        .clk_source = I2C_CLK_SRC_DEFAULT, .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    ESP_ERROR_CHECK(i2c_new_master_bus(&bc, &bus));
    ESP_ERROR_CHECK(i2c_master_probe(bus, 0x18, 100));
    ESP_ERROR_CHECK(i2c_master_probe(bus, 0x40, 100));
    i2s_chan_handle_t tx, rx;
    i2s_chan_config_t cc = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    cc.dma_desc_num = 4; cc.dma_frame_num = 160; cc.auto_clear = true;
    ESP_ERROR_CHECK(i2s_new_channel(&cc, &tx, &rx));
    i2s_std_config_t sc = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(16000),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = { .mclk = GPIO_NUM_16, .bclk = GPIO_NUM_9, .ws = GPIO_NUM_45,
                      .dout = GPIO_NUM_8, .din = GPIO_NUM_10 },
    };
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(tx, &sc));
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(rx, &sc));
    audio_codec_i2s_cfg_t dc = {.port = 0, .tx_handle = tx, .rx_handle = rx};
    const audio_codec_data_if_t *data = audio_codec_new_i2s_data(&dc);
    assert(data);
    audio_codec_i2c_cfg_t ic = {.port = 0, .addr = ES8311_CODEC_DEFAULT_ADDR,
                               .bus_handle = bus, .clock_speed_hz = 100000};
    const audio_codec_ctrl_if_t *dac_ctrl = audio_codec_new_i2c_ctrl(&ic);
    ic.addr = ES7210_CODEC_DEFAULT_ADDR;
    const audio_codec_ctrl_if_t *adc_ctrl = audio_codec_new_i2c_ctrl(&ic);
    const audio_codec_gpio_if_t *gpio = audio_codec_new_gpio();
    assert(dac_ctrl && adc_ctrl && gpio);
    es8311_codec_cfg_t dac_cfg = {
        .ctrl_if = dac_ctrl, .gpio_if = gpio, .codec_mode = ESP_CODEC_DEV_WORK_MODE_DAC,
        .pa_pin = -1, .use_mclk = true,
        .hw_gain = {.pa_voltage = 5.0, .codec_dac_voltage = 3.3},
    };
    es7210_codec_cfg_t adc_cfg = {
        .ctrl_if = adc_ctrl,
        .mic_selected = ES7210_SEL_MIC1 | ES7210_SEL_MIC2 | ES7210_SEL_MIC3 | ES7210_SEL_MIC4,
    };
    const audio_codec_if_t *dac_if = es8311_codec_new(&dac_cfg);
    const audio_codec_if_t *adc_if = es7210_codec_new(&adc_cfg);
    assert(dac_if && adc_if);
    esp_codec_dev_cfg_t dev = {.data_if = data, .codec_if = adc_if, .dev_type = ESP_CODEC_DEV_TYPE_IN};
    ride_adc = esp_codec_dev_new(&dev);
    dev.codec_if = dac_if; dev.dev_type = ESP_CODEC_DEV_TYPE_OUT;
    ride_dac = esp_codec_dev_new(&dev); assert(ride_adc && ride_dac);
    /* Open 4x16-bit capture first, fixing 64 BCLK cycles per sample. The
     * codec driver pads the later 2x16-bit playback to matching 32-bit slots. */
    esp_codec_dev_sample_info_t fs = {.sample_rate = 16000, .channel = 4, .bits_per_sample = 16};
    ESP_ERROR_CHECK(esp_codec_dev_open(ride_adc, &fs));
    fs.channel = 2;
    ESP_ERROR_CHECK(esp_codec_dev_open(ride_dac, &fs));
    ESP_ERROR_CHECK(esp_codec_dev_set_in_gain(ride_adc, 24.0));
    /* Analog DAC loopback is much larger than microphone signals. */
    ESP_ERROR_CHECK(esp_codec_dev_set_in_channel_gain(ride_adc, 1 << 2, 0.0));
    ESP_ERROR_CHECK(esp_codec_dev_set_out_vol(ride_dac, 45.0));
}
