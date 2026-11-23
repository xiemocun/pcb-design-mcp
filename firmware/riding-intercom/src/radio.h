#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <stdatomic.h>
#include "transport.h"
extern atomic_bool ride_link_up;
void ride_radio_init(void);
bool ride_send(const ride_frame *frame);
bool ride_receive(ride_frame *frame);
