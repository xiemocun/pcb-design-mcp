#ifndef AM2302_DECODE_H
#define AM2302_DECODE_H
#include <stddef.h>
#include <stdint.h>
typedef struct { uint16_t duration_us; uint8_t level; } am2302_pulse;
typedef struct { int16_t temperature_tenths_c; uint16_t humidity_tenths_rh; uint8_t bytes[5]; } am2302_sample;
typedef enum { AM_OK, AM_NO_PREAMBLE, AM_TRUNCATED, AM_TIMING, AM_CHECKSUM, AM_RANGE } am2302_status;
am2302_status am2302_decode(const am2302_pulse *p, size_t n, am2302_sample *result);
const char *am2302_status_name(am2302_status s);
#endif
