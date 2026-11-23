#include "am2302_decode.h"
#include <string.h>
static int within(unsigned v,unsigned lo,unsigned hi){ return v>=lo && v<=hi; }
am2302_status am2302_decode(const am2302_pulse *p,size_t n,am2302_sample *result){
    size_t start=n;int found=0;
    /* Discard the host-release edge; response is approximately 80us LOW/HIGH. */
    for(size_t i=0;i+1<n;i++)
        if(p[i].level==0 && within(p[i].duration_us,60,100) && p[i+1].level==1 && within(p[i+1].duration_us,60,100)){start=i+2;found=1;break;}
    if(!found)return AM_NO_PREAMBLE;
    if(n-start<80)return AM_TRUNCATED;
    am2302_sample tmp={0};
    for(size_t bit=0;bit<40;bit++){
        const am2302_pulse *low=&p[start+2*bit],*high=low+1;
        if(low->level!=0 || high->level!=1 || !within(low->duration_us,35,65))return AM_TIMING;
        unsigned one;
        if(within(high->duration_us,15,40))one=0;
        else if(within(high->duration_us,55,90))one=1;
        else return AM_TIMING;
        tmp.bytes[bit/8]=(uint8_t)((tmp.bytes[bit/8]<<1)|one);
    }
    if((uint8_t)(tmp.bytes[0]+tmp.bytes[1]+tmp.bytes[2]+tmp.bytes[3])!=tmp.bytes[4])return AM_CHECKSUM;
    tmp.humidity_tenths_rh=(uint16_t)((tmp.bytes[0]<<8)|tmp.bytes[1]);
    unsigned magnitude=((tmp.bytes[2]&0x7f)<<8)|tmp.bytes[3];
    tmp.temperature_tenths_c=(tmp.bytes[2]&0x80)?-(int16_t)magnitude:(int16_t)magnitude;
    if(tmp.humidity_tenths_rh>1000 || tmp.temperature_tenths_c < -400 || tmp.temperature_tenths_c > 800)return AM_RANGE;
    *result=tmp;return AM_OK;
}
const char *am2302_status_name(am2302_status s){
    static const char *names[]={"ok","no_response_preamble","truncated_frame","pulse_timing","checksum","out_of_range"};
    return (unsigned)s<sizeof(names)/sizeof(names[0])?names[s]:"unknown_error";
}
