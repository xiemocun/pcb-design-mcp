#include "transport.h"
#include <string.h>

static void put32(uint8_t *p, uint32_t x) {
    p[0] = x >> 24; p[1] = x >> 16; p[2] = x >> 8; p[3] = x;
}
static uint32_t get32(const uint8_t *p) {
    return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
}
static int32_t delta(uint32_t a, uint32_t b) { return (int32_t)(a - b); }
void ride_pack(uint8_t out[RIDE_PACKET], const ride_frame *f) {
    memcpy(out, "RIDE", 4); out[4] = 1; out[5] = 0; out[6] = 0; out[7] = RIDE_PAYLOAD;
    put32(out + 8, f->session); put32(out + 12, f->seq);
    put32(out + 16, f->seq * RIDE_SAMPLES);
    memcpy(out + RIDE_HEADER, f->data, RIDE_PAYLOAD);
}
bool ride_unpack(ride_frame *f, const uint8_t *p, size_t n) {
    if (!p || !f || n != RIDE_PACKET || memcmp(p, "RIDE", 4) || p[4] != 1 ||
        p[5] || p[6] || p[7] != RIDE_PAYLOAD || !get32(p + 8) ||
        get32(p + 16) != get32(p + 12) * RIDE_SAMPLES) return false;
    f->session = get32(p + 8); f->seq = get32(p + 12);
    memcpy(f->data, p + RIDE_HEADER, RIDE_PAYLOAD); return true;
}
static void start(ride_jitter *j, const ride_frame *f, uint32_t now) {
    memset(j->slots, 0, sizeof(j->slots));
    j->session = f->session; j->next = j->newest = f->seq;
    j->active = true; j->playing = false; j->reset = true;
    j->start_ms = now; j->misses = 0;
}
bool ride_push(ride_jitter *j, const ride_frame *f, uint32_t now) {
    if (!f->session) { j->invalid++; return false; }
    if (j->session && f->session != j->session) {
        for (unsigned i = 0; i < 4; i++)
            if (j->retired[i] == f->session) { j->late++; return false; }
        if (j->active && now - j->last_rx_ms < 500) { j->late++; return false; }
        j->retired[j->retired_pos++ % 4] = j->session;
        start(j, f, now); j->resync++;
    } else if (!j->active) {
        /* A stale packet cannot resurrect an ended stream. */
        if (j->session && delta(f->seq, j->newest) <= 0) { j->late++; return false; }
        start(j, f, now);
    }
    int32_t ahead = delta(f->seq, j->next);
    if (ahead < 0) { j->late++; return false; }
    if (ahead >= RIDE_SLOTS) {
        /* A bounded discontinuity is preferable to replaying an old queue. */
        start(j, f, now); j->resync++;
    }
    ride_slot *slot = &j->slots[f->seq % RIDE_SLOTS];
    if (slot->valid && slot->frame.seq == f->seq) { j->duplicate++; return false; }
    slot->valid = true; slot->frame = *f;
    if (delta(f->seq, j->newest) > 0) j->newest = f->seq;
    j->last_rx_ms = now; j->accepted++; return true;
}
ride_play ride_pop(ride_jitter *j, ride_frame *f, uint32_t now, bool *reset) {
    *reset = false;
    if (!j->active) return RIDE_SILENCE;
    if (now - j->last_rx_ms >= 200) {
        j->active = false; j->playing = false; return RIDE_SILENCE;
    }
    if (!j->playing) {
        if (now - j->start_ms < RIDE_PREBUFFER * 20) return RIDE_SILENCE;
        j->playing = true;
    }
    if (delta(j->newest, j->next) >= 6) {
        j->next = j->newest - (RIDE_PREBUFFER - 1);
        j->reset = true; j->resync++;
    }
    *reset = j->reset; j->reset = false;
    ride_slot *slot = &j->slots[j->next % RIDE_SLOTS];
    if (slot->valid && slot->frame.seq == j->next) {
        *f = slot->frame; slot->valid = false; j->next++; j->misses = 0;
        return RIDE_AUDIO;
    }
    j->next++; j->concealed++;
    if (++j->misses >= 3) { j->active = false; j->playing = false; }
    return RIDE_PLC;
}
void ride_adc_unpack(int16_t *p, size_t n) {
    for (size_t i = 0; i < n * 4; i += 2) {
        int16_t t = p[i]; p[i] = p[i + 1]; p[i + 1] = t;
    }
}
