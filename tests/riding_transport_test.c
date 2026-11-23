#include "transport.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static ride_frame frame(uint32_t session, uint32_t seq) {
    ride_frame f = {.session = session, .seq = seq};
    memset(f.data, (int)seq, sizeof(f.data)); return f;
}
int main(void) {
    ride_frame f = frame(123, UINT32_MAX), out;
    uint8_t p[RIDE_PACKET + 1]; ride_pack(p, &f);
    assert(ride_unpack(&out, p, RIDE_PACKET));
    assert(out.seq == f.seq && out.session == f.session && !memcmp(out.data, f.data, RIDE_PAYLOAD));
    for (unsigned n = 0; n < RIDE_PACKET; n++) assert(!ride_unpack(&out, p, n));
    assert(!ride_unpack(&out, p, RIDE_PACKET + 1));
    p[16] ^= 1; assert(!ride_unpack(&out, p, RIDE_PACKET));
    ride_pack(p, &f); p[4] = 2; assert(!ride_unpack(&out, p, RIDE_PACKET));
    ride_jitter j = {0}; bool reset;
    f = frame(1, UINT32_MAX - 1); assert(ride_push(&j, &f, 0));
    f = frame(1, 0); assert(ride_push(&j, &f, 40));
    f = frame(1, UINT32_MAX); assert(ride_push(&j, &f, 41));
    assert(!ride_push(&j, &f, 42));
    assert(ride_pop(&j, &out, 59, &reset) == RIDE_SILENCE);
    assert(ride_pop(&j, &out, 60, &reset) == RIDE_AUDIO && reset && out.seq == UINT32_MAX - 1);
    assert(ride_pop(&j, &out, 80, &reset) == RIDE_AUDIO && !reset && out.seq == UINT32_MAX);
    assert(ride_pop(&j, &out, 100, &reset) == RIDE_AUDIO && out.seq == 0);
    assert(ride_pop(&j, &out, 120, &reset) == RIDE_PLC);
    assert(ride_pop(&j, &out, 140, &reset) == RIDE_PLC);
    assert(ride_pop(&j, &out, 160, &reset) == RIDE_PLC);
    assert(ride_pop(&j, &out, 180, &reset) == RIDE_SILENCE);
    f = frame(1, 0); assert(!ride_push(&j, &f, 190));
    f = frame(2, 0); assert(ride_push(&j, &f, 200));
    f = frame(1, 50); assert(!ride_push(&j, &f, 1000));
    f = frame(2, 100); assert(ride_push(&j, &f, 1000));
    assert(ride_pop(&j, &out, 1060, &reset) == RIDE_AUDIO && reset && out.seq == 100);
    assert(ride_pop(&j, &out, 1200, &reset) == RIDE_SILENCE);
    int16_t wire[] = {2,1,4,3,-2,-1,-4,-3};
    const int16_t ordered[] = {1,2,3,4,-1,-2,-3,-4};
    ride_adc_unpack(wire, 2); assert(!memcmp(wire, ordered, sizeof(wire)));
    /* 60 seconds in each direction; independent sample clocks, bursts, loss,
     * duplicates, reorder and wrap. All arrivals must remain bounded. */
    for (unsigned direction = 0; direction < 2; direction++) {
        memset(&j, 0, sizeof(j));
        unsigned rx = 0, played = 0, plc = 0;
        for (unsigned t = 0; t < 60000; t++) {
            unsigned period = direction ? 19 : 21; /* deliberately > realistic clock drift */
            if (t % period == 0) {
                unsigned seq = t / period;
                if (seq % 37 != 0 && !(t >= 20000 && t < 21000)) {
                    f = frame(99, seq); rx += ride_push(&j, &f, t);
                    if (seq % 13 == 0) assert(!ride_push(&j, &f, t));
                }
            }
            if (t % 20 == 0) {
                ride_play s = ride_pop(&j, &out, t, &reset);
                if (s == RIDE_AUDIO) {
                    played++; assert(t - out.seq * period < 200);
                }
                plc += s == RIDE_PLC;
            }
        }
        assert(rx > 2000 && played > 2000 && plc > 0);
    }
    puts("PASS: wire validation, sequence wrap, reorder, duplicate, loss/PLC, restart, stale session, bounded drift, ADC word mapping");
}
