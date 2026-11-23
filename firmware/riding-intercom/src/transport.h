#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

enum { RIDE_SAMPLES = 320, RIDE_PAYLOAD = 40, RIDE_HEADER = 20,
       RIDE_PACKET = RIDE_HEADER + RIDE_PAYLOAD, RIDE_SLOTS = 8,
       RIDE_PREBUFFER = 3 };
typedef struct {
    uint32_t session, seq;
    uint8_t data[RIDE_PAYLOAD];
} ride_frame;
typedef struct { bool valid; ride_frame frame; } ride_slot;
typedef struct {
    ride_slot slots[RIDE_SLOTS];
    uint32_t session, next, newest, last_rx_ms, start_ms;
    uint32_t retired[4];
    unsigned retired_pos, misses;
    bool active, playing, reset;
    uint32_t accepted, duplicate, late, invalid, concealed, resync;
} ride_jitter;
typedef enum { RIDE_SILENCE, RIDE_AUDIO, RIDE_PLC } ride_play;
void ride_pack(uint8_t out[RIDE_PACKET], const ride_frame *frame);
bool ride_unpack(ride_frame *frame, const uint8_t *packet, size_t length);
bool ride_push(ride_jitter *j, const ride_frame *frame, uint32_t now_ms);
ride_play ride_pop(ride_jitter *j, ride_frame *frame, uint32_t now_ms, bool *reset);
/* Standard I2S RX stores each 32-bit wire slot in little-endian order.
 * ES7210 emits two 16-bit ADC words per slot. Restore MIC1,MIC2,REF,unused. */
void ride_adc_unpack(int16_t *samples, size_t frames);
