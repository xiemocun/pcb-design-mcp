"""Transparent first-pass arithmetic, not a radio/voice qualification model."""
import json,math
from pathlib import Path

root=Path('work/riding-intercom')
frequency_mhz=2400.;distance_km=1.;eirp_dbm=20.;rx_gain_dbi=0.
loss_db=32.44+20*math.log10(frequency_mhz)+20*math.log10(distance_km)
frame_s=.020;codec_bps=16000;audio_bytes=codec_bps*frame_s/8
# Proposed packet accounting, no measured effective throughput or codec quality.
application_bytes=16;ip_udp_bytes=28;mac_security_estimate_bytes=40;fec_bytes=audio_bytes*.25
wire_bytes=audio_bytes+application_bytes+ip_udp_bytes+mac_security_estimate_bytes+fec_bytes
traffic_bps=wire_bytes*8/frame_s*2
report=dict(scope='Illustrative engineering screening only; no candidate part sensitivity, antenna efficiency, obstruction model, measured noise or firmware timing is assumed proven',
    radio=dict(frequency_mhz=frequency_mhz,distance_km=distance_km,assumed_eirp_dbm=eirp_dbm,assumed_receive_gain_dbi=rx_gain_dbi,free_space_loss_db=loss_db,ideal_receive_dbm=eirp_dbm+rx_gain_dbi-loss_db,additional_loss_scenarios=[dict(assumed_extra_loss_db=x,receive_dbm=eirp_dbm+rx_gain_dbi-loss_db-x) for x in (0,10,20,30)],first_fresnel_midpoint_radius_m=math.sqrt((299792458/(frequency_mhz*1e6))*1000/4),notes=['20dBm is a screening assumption, not proof of regulatory compliance or actual module output.','Additional losses are sensitivity scenarios, not predictions for a specific building.','No verified receiver threshold or fade margin: cannot establish1km urban availability.']),
    audio=dict(sample_rate_hz=16000,bits_per_sample=16,raw_two_way_pcm_bps=16000*16*2,proposed_codec_bps_per_direction=codec_bps,frame_ms=20,encoded_bytes_per_frame=audio_bytes,assumed_packet_bytes=wire_bytes,estimated_two_way_bits_without_radio_preambles_ack_contention_or_retries=traffic_bps,raw_phy_rate_bps=250000,minimum_airtime_fraction=traffic_bps/250000,codec_quality_verified=False,effective_airtime_verified=False),
    wind=dict(riding_speed_kmh=25,still_air_relative_speed_m_s=25/3.6,ambient_wind_not_included=True),
    range_verdict='unverified',speech_quality_verdict='unverified',core_modified=False)
(root/'budget-screening.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
