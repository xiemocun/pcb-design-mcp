/* Split hardware interface: GPIO26 -> open-drain driver; Schmitt receiver -> GPIO27. */
#include <stdio.h>
#include <inttypes.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "driver/gpio.h"
#include "driver/rmt_rx.h"
#include "esp_timer.h"
#include "esp_rom_sys.h"
#include "esp_system.h"
#include "esp_app_desc.h"
#include "am2302_decode.h"
#include "board_pins.h"
#include "bench_identity.h"
static QueueHandle_t completed;
static bool rx_done(rmt_channel_handle_t channel,const rmt_rx_done_event_data_t *event,void *context){
    BaseType_t wake=pdFALSE;size_t count=event->num_symbols;
    xQueueSendFromISR(completed,&count,&wake);return wake==pdTRUE;
}
void app_main(void){
    /* Set the release latch before enabling output to avoid an unwanted LOW. */
    ESP_ERROR_CHECK(gpio_set_level(DRIVE_GPIO,1));
    gpio_config_t cfg={.pin_bit_mask=1ULL<<DRIVE_GPIO,.mode=GPIO_MODE_OUTPUT};
    ESP_ERROR_CHECK(gpio_config(&cfg));
    const esp_app_desc_t *app=esp_app_get_description();
    char elf_hash[65];
    for(size_t i=0;i<32;i++)snprintf(elf_hash+2*i,3,"%02x",app->app_elf_sha256[i]);
    printf("{\"event\":\"boot\",\"protocol\":1,\"project_id\":\"%s\",\"revision\":%d,\"pcb_build_id\":\"%s\",\"app_elf_sha256\":\"%s\",\"reset_reason\":%d,\"drive_gpio\":%d,\"read_gpio\":%d}\n",
           BENCH_PROJECT_ID,BENCH_REVISION,BENCH_PCB_BUILD_ID,elf_hash,(int)esp_reset_reason(),(int)DRIVE_GPIO,(int)READ_GPIO);
    fflush(stdout);
    completed=xQueueCreate(1,sizeof(size_t));configASSERT(completed);
    rmt_channel_handle_t rx=NULL;
    rmt_rx_channel_config_t rc={.gpio_num=READ_GPIO,.clk_src=RMT_CLK_SRC_DEFAULT,.resolution_hz=1000000,.mem_block_symbols=64,.intr_priority=0};
    ESP_ERROR_CHECK(rmt_new_rx_channel(&rc,&rx));
    rmt_rx_event_callbacks_t callbacks={.on_recv_done=rx_done};
    ESP_ERROR_CHECK(rmt_rx_register_event_callbacks(rx,&callbacks,NULL));
    ESP_ERROR_CHECK(rmt_enable(rx));
    vTaskDelay(pdMS_TO_TICKS(2500)); /* AM2302 requires >2s after power-up. */
    uint32_t sequence=0;
    for(;;){
        rmt_symbol_word_t symbols[64]={0};size_t count=0;am2302_sample sample;
        rmt_receive_config_t receive={.signal_range_min_ns=1000,.signal_range_max_ns=150000};
        xQueueReset(completed);
        ESP_ERROR_CHECK(gpio_set_level(DRIVE_GPIO,0));
        esp_rom_delay_us(1100); /* Host start LOW >=1ms, not a delay in the ISR. */
        /* Arm after start LOW so the host pulse does not terminate RX as idle. */
        esp_err_t armed=rmt_receive(rx,symbols,sizeof(symbols),&receive);
        ESP_ERROR_CHECK(gpio_set_level(DRIVE_GPIO,1));
        am2302_status status=AM_NO_PREAMBLE;
        if(armed==ESP_OK && xQueueReceive(completed,&count,pdMS_TO_TICKS(50))==pdTRUE){
            am2302_pulse pulses[128];size_t n=0;
            for(size_t i=0;i<count && i<64;i++){
                if(symbols[i].duration0)pulses[n++]=(am2302_pulse){symbols[i].duration0,symbols[i].level0};
                if(symbols[i].duration1)pulses[n++]=(am2302_pulse){symbols[i].duration1,symbols[i].level1};
            }
            status=am2302_decode(pulses,n,&sample);
        }
        sequence++;
        int64_t now=esp_timer_get_time()/1000;
        if(status==AM_OK)
            printf("{\"sequence\":%"PRIu32",\"uptime_ms\":%"PRId64",\"status\":\"ok\",\"temperature_c\":%.1f,\"humidity_rh\":%.1f}\n",sequence,now,sample.temperature_tenths_c/10.0,sample.humidity_tenths_rh/10.0);
        else
            printf("{\"sequence\":%"PRIu32",\"uptime_ms\":%"PRId64",\"status\":\"error\",\"reason\":\"%s\"}\n",sequence,now,am2302_status_name(status));
        fflush(stdout);
        /* Abort any incomplete receive before the next attempt. */
        ESP_ERROR_CHECK(rmt_disable(rx));ESP_ERROR_CHECK(rmt_enable(rx));
        vTaskDelay(pdMS_TO_TICKS(2500));
    }
}
