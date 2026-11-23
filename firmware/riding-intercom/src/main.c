#include "board_audio.h"
#include "radio.h"
#include "transport.h"
#include "esp_afe_sr_models.h"
#include "esp_opus_enc.h"
#include "esp_opus_dec.h"
#include "esp_heap_caps.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "driver/gpio.h"
#include "model_path.h"
#include "freertos/semphr.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

static const char *TAG = "ride";
static const esp_afe_sr_iface_t *afe;
static esp_afe_sr_data_t *afe_data;
static ride_jitter jitter;
static SemaphoreHandle_t jitter_lock;
static atomic_bool muted;
static atomic_int volume = 45;
static atomic_uint tx_ok, tx_drop, enc_fail, dec_fail, capture_frames;
static void *encoder, *decoder;
static uint32_t session;
static uint32_t now_ms(void) { return (uint32_t)(esp_timer_get_time() / 1000); }

static void capture(void *arg) {
    (void)arg;
    int frames = afe->get_feed_chunksize(afe_data);
    assert(afe->get_feed_channel_num(afe_data) == 4);
    int16_t *pcm = heap_caps_aligned_alloc(16, frames * 8, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    assert(pcm);
    unsigned blocks = 0;
    for (;;) {
        ESP_ERROR_CHECK(esp_codec_dev_read(ride_adc, pcm, frames * 8));
        ride_adc_unpack(pcm, frames);
        /* Diagnostic measures the three physical inputs before AEC. Validate
         * mapping with separate mic taps and DAC test tone on the actual board. */
        if (++blocks % 250 == 0) {
            double energy[3] = {0}; unsigned clips[3] = {0};
            for (int i = 0; i < frames; i++) for (int c = 0; c < 3; c++) {
                int x = pcm[4*i+c]; energy[c] += (double)x*x;
                if (x > 32000 || x < -32000) clips[c]++;
            }
            ESP_LOGI(TAG, "ADC rms M1/M2/REF=%.0f/%.0f/%.0f clips=%u/%u/%u",
                     sqrt(energy[0]/frames), sqrt(energy[1]/frames), sqrt(energy[2]/frames),
                     clips[0], clips[1], clips[2]);
        }
        int ret = afe->feed(afe_data, pcm);
        if (ret < 0) ESP_LOGE(TAG, "AFE feed failed: %d", ret);
        atomic_fetch_add(&capture_frames, frames);
    }
}
static void encode_send(void *arg) {
    (void)arg;
    int16_t pcm[RIDE_SAMPLES]; unsigned used = 0;
    int in_bytes, out_bytes;
    assert(esp_opus_enc_get_frame_size(encoder, &in_bytes, &out_bytes) == ESP_AUDIO_ERR_OK);
    assert(in_bytes == sizeof(pcm) && out_bytes >= RIDE_PAYLOAD);
    uint8_t *encoded = malloc(out_bytes); assert(encoded);
    ride_frame f = {.session = session};
    for (;;) {
        afe_fetch_result_t *r = afe->fetch(afe_data);
        if (!r || r->ret_value < 0 || r->data_size <= 0 || r->data_size % 2) {
            ESP_LOGE(TAG, "AFE fetch failed"); used = 0; continue;
        }
        for (int i = 0; i < r->data_size / 2; i++) {
            pcm[used++] = atomic_load(&muted) ? 0 : r->data[i];
            if (used != RIDE_SAMPLES) continue;
            used = 0;
            esp_audio_enc_in_frame_t input = {.buffer = (uint8_t *)pcm, .len = sizeof(pcm)};
            esp_audio_enc_out_frame_t output = {.buffer = encoded, .len = out_bytes};
            esp_audio_err_t e = esp_opus_enc_process(encoder, &input, &output);
            if (e == ESP_AUDIO_ERR_OK && output.encoded_bytes == RIDE_PAYLOAD) {
                memcpy(f.data, encoded, RIDE_PAYLOAD);
                if (ride_send(&f)) atomic_fetch_add(&tx_ok, 1);
                else atomic_fetch_add(&tx_drop, 1);
            } else {
                atomic_fetch_add(&enc_fail, 1);
                ESP_LOGE(TAG, "Opus CBR frame rejected: error=%d bytes=%lu", e, (unsigned long)output.encoded_bytes);
            }
            f.seq++;
        }
    }
}
static void receive(void *arg) {
    (void)arg; ride_frame f;
    for (;;) if (ride_receive(&f)) {
        xSemaphoreTake(jitter_lock, portMAX_DELAY);
        ride_push(&jitter, &f, now_ms());
        xSemaphoreGive(jitter_lock);
    }
}
static void playback(void *arg) {
    (void)arg;
    int16_t mono[RIDE_SAMPLES], stereo[RIDE_SAMPLES * 2] = {0};
    int last_volume = -1; bool was_audible = false;
    /* Fill the DAC DMA with silence before enabling the amplifier. */
    ESP_ERROR_CHECK(esp_codec_dev_write(ride_dac, stereo, sizeof(stereo)));
    ESP_ERROR_CHECK(gpio_set_level(GPIO_NUM_48, 1));
    for (;;) {
        ride_frame f; bool reset;
        xSemaphoreTake(jitter_lock, portMAX_DELAY);
        ride_play state = ride_pop(&jitter, &f, now_ms(), &reset);
        xSemaphoreGive(jitter_lock);
        if (reset) assert(esp_opus_dec_reset(decoder) == ESP_AUDIO_ERR_OK);
        memset(mono, 0, sizeof(mono));
        if (state != RIDE_SILENCE) {
            esp_audio_dec_in_raw_t raw = {
                .buffer = state == RIDE_AUDIO ? f.data : NULL,
                .len = state == RIDE_AUDIO ? RIDE_PAYLOAD : 0,
                .frame_recover = state == RIDE_AUDIO ? ESP_AUDIO_DEC_RECOVERY_NONE : ESP_AUDIO_DEC_RECOVERY_PLC,
            };
            esp_audio_dec_out_frame_t out = {.buffer = (uint8_t *)mono, .len = sizeof(mono)};
            esp_audio_dec_info_t info;
            esp_audio_err_t e = esp_opus_dec_decode(decoder, &raw, &out, &info);
            if (e != ESP_AUDIO_ERR_OK || out.decoded_size != sizeof(mono)) {
                atomic_fetch_add(&dec_fail, 1); memset(mono, 0, sizeof(mono));
            }
        }
        bool audible = state != RIDE_SILENCE;
        /* Short fade on restart; output is limited before the analog reference
         * tap so AEC sees the exact signal that reaches the amplifier. */
        for (int i = 0; i < RIDE_SAMPLES; i++) {
            int x = mono[i];
            if (!was_audible && i < 80) x = x * i / 80;
            if (x > 26000) x = 26000;
            if (x < -26000) x = -26000;
            stereo[2*i] = stereo[2*i+1] = x;
        }
        was_audible = audible;
        int v = atomic_load(&volume);
        if (v != last_volume) {
            ESP_ERROR_CHECK(esp_codec_dev_set_out_vol(ride_dac, v)); last_volume = v;
        }
        /* I2S hardware is the playback clock, not UDP packet arrival or a
         * software delay. Queue resynchronization bounds clock drift. */
        ESP_ERROR_CHECK(esp_codec_dev_write(ride_dac, stereo, sizeof(stereo)));
    }
}
static void control_key(int key) {
    if (key == 'm') {
        bool old = atomic_load(&muted);
        while (!atomic_compare_exchange_weak(&muted, &old, !old)) {}
        return;
    }
    int old = atomic_load(&volume), next;
    do {
        next = old + (key == '+' ? 5 : -5);
        if (next < 0) next = 0;
        if (next > 80) next = 80;
    } while (!atomic_compare_exchange_weak(&volume, &old, next));
}
static void buttons(void *arg) {
    (void)arg;
    const gpio_num_t pins[3] = {GPIO_NUM_4, GPIO_NUM_5, GPIO_NUM_6};
    const int keys[3] = {'m', '+', '-'};
    gpio_config_t config = {
        .pin_bit_mask = (1ULL << 4) | (1ULL << 5) | (1ULL << 6),
        .mode = GPIO_MODE_INPUT, .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE, .intr_type = GPIO_INTR_DISABLE
    };
    ESP_ERROR_CHECK(gpio_config(&config));
    bool raw[3] = {true, true, true}, stable[3] = {true, true, true};
    unsigned count[3] = {0, 0, 0};
    for (;;) {
        for (unsigned i = 0; i < 3; ++i) {
            bool level = gpio_get_level(pins[i]) != 0;
            if (level != raw[i]) { raw[i] = level; count[i] = 1; }
            else if (count[i] < 3) ++count[i];
            if (count[i] >= 3 && stable[i] != level) {
                stable[i] = level;
                if (!level) control_key(keys[i]);
            }
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}
static void console(void *arg) {
    (void)arg;
    for (;;) {
        int c = getchar();
        if (c == 'm' || c == '+' || c == '-') control_key(c);
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}
void app_main(void) {
    ESP_LOGI(TAG, "RIDING PROTOTYPE: physical radio/audio qualification pending; console m/+/-");
    jitter_lock = xSemaphoreCreateMutex(); assert(jitter_lock);
    session = esp_random(); if (!session) session = 1;
    ride_board_audio_init();
    srmodel_list_t *models = esp_srmodel_init("model"); assert(models);
    afe_config_t *cfg = afe_config_init("MMRN", models, AFE_TYPE_FD, AFE_MODE_HIGH_PERF);
    assert(cfg);
    cfg->aec_init = true; cfg->aec_mode = AEC_MODE_FD_HIGH_PERF;
    cfg->ns_init = true; cfg->afe_ns_mode = AFE_NS_MODE_NET;
    cfg->ns_model_name = esp_srmodel_filter(models, "nsnet2", NULL); assert(cfg->ns_model_name);
    cfg->vad_init = false; cfg->wakenet_init = false; cfg->agc_init = false;
    cfg->output_playback_channel = false;
    cfg->memory_alloc_mode = AFE_MEMORY_ALLOC_MORE_PSRAM;
    cfg->afe_perferred_core = 1; cfg->afe_perferred_priority = 8;
    cfg->afe_ringbuf_size = 4;
    afe = esp_afe_handle_from_config(cfg); assert(afe);
    afe_data = afe->create_from_config(cfg); assert(afe_data);
    afe_config_free(cfg);
    esp_opus_enc_config_t ec = ESP_OPUS_ENC_CONFIG_DEFAULT();
    ec.sample_rate = 16000; ec.channel = 1; ec.bitrate = 16000; ec.complexity = 3;
    /* Fixed 40-byte CBR, no DTX and no unimplemented FEC claim. */
    ec.enable_fec = false; ec.enable_dtx = false; ec.enable_vbr = false;
    assert(esp_opus_enc_open(&ec, sizeof(ec), &encoder) == ESP_AUDIO_ERR_OK);
    esp_opus_dec_cfg_t dc = ESP_OPUS_DEC_CONFIG_DEFAULT();
    dc.sample_rate = 16000; dc.channel = 1; dc.frame_duration = ESP_OPUS_DEC_FRAME_DURATION_20_MS;
    assert(esp_opus_dec_open(&dc, sizeof(dc), &decoder) == ESP_AUDIO_ERR_OK);
    ride_radio_init();
    assert(xTaskCreatePinnedToCore(playback, "play", 6144, NULL, 7, NULL, 0) == pdPASS);
    assert(xTaskCreatePinnedToCore(capture, "capture", 4096, NULL, 9, NULL, 1) == pdPASS);
    assert(xTaskCreatePinnedToCore(encode_send, "encode", 8192, NULL, 6, NULL, 1) == pdPASS);
    assert(xTaskCreatePinnedToCore(receive, "receive", 4096, NULL, 6, NULL, 0) == pdPASS);
    assert(xTaskCreate(buttons, "buttons", 3072, NULL, 2, NULL) == pdPASS);
    assert(xTaskCreate(console, "console", 3072, NULL, 2, NULL) == pdPASS);
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(10000));
        xSemaphoreTake(jitter_lock, portMAX_DELAY);
        uint32_t accepted = jitter.accepted, concealed = jitter.concealed, resync = jitter.resync;
        xSemaphoreGive(jitter_lock);
        ESP_LOGI(TAG, "link=%d tx=%u drop=%u rx=%lu plc=%lu resync=%lu enc_err=%u dec_err=%u samples=%u heap=%u mute=%d vol=%d",
            atomic_load(&ride_link_up), atomic_load(&tx_ok), atomic_load(&tx_drop),
            (unsigned long)accepted, (unsigned long)concealed, (unsigned long)resync,
            atomic_load(&enc_fail), atomic_load(&dec_fail), atomic_load(&capture_frames),
            (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL), atomic_load(&muted), atomic_load(&volume));
    }
}
