#include "src/am2302_decode.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
static size_t frame(am2302_pulse *p,const unsigned char bytes[5]){
 size_t n=0;p[n++]=(am2302_pulse){25,1};p[n++]=(am2302_pulse){80,0};p[n++]=(am2302_pulse){80,1};
 for(unsigned bit=0;bit<40;bit++){p[n++]=(am2302_pulse){50,0};p[n++]=(am2302_pulse){(bytes[bit/8]>>(7-bit%8))&1?70:28,1};}return n;
}
int main(void){
 am2302_pulse p[128];am2302_sample value={0};size_t n;
 const unsigned char positive[]={0x02,0x8e,0x00,0xfd,0x8d};
 n=frame(p,positive);assert(am2302_decode(p,n,&value)==AM_OK);assert(value.temperature_tenths_c==253 && value.humidity_tenths_rh==654);
 const unsigned char negative[]={0x01,0xc2,0x80,0x38,0x7b};
 n=frame(p,negative);assert(am2302_decode(p,n,&value)==AM_OK);assert(value.temperature_tenths_c==-56 && value.humidity_tenths_rh==450);
 unsigned char corrupted[]={0x02,0x8e,0x00,0xfd,0x8c};n=frame(p,corrupted);
 am2302_sample previous=value;assert(am2302_decode(p,n,&value)==AM_CHECKSUM);assert(memcmp(&value,&previous,sizeof(value))==0);
 n=frame(p,positive);assert(am2302_decode(p,n-1,&value)==AM_TRUNCATED);assert(am2302_decode(p,3,&value)==AM_TRUNCATED);
 n=frame(p,positive);p[4].duration_us=45;assert(am2302_decode(p,n,&value)==AM_TIMING);
 n=frame(p,positive);p[1].duration_us=15;assert(am2302_decode(p,n,&value)==AM_NO_PREAMBLE);
 const unsigned char impossible[]={0x04,0x00,0x00,0x00,0x04};n=frame(p,impossible);assert(am2302_decode(p,n,&value)==AM_RANGE);
 n=frame(p,positive);for(size_t i=3;i<n;i+=2){p[i].duration_us=35;p[i+1].duration_us=p[i+1].duration_us==70?55:15;}assert(am2302_decode(p,n,&value)==AM_OK);
 puts("Protocol parser: positive/negative values, checksum, truncated/no response, ambiguous pulse, range and timing limits passed. No hardware claim.");return 0;
}
