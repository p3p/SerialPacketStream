## Packet frame layout

#### Packet Frame Start (2 Bytes)
**[14bit] Packet Frame Start token**  
[0xB5, 0xAC] 14 bits used as token, 2 bits _Packet Type_ field  

**[ 2bit] Packet Type**  
The packet type is the 2 lsb of the 2nd packet framing byte (0xAC)  
0 : Response Packet  
1 : Data Packet  
2 : Data NACK Packet (no ack but still nacked on payload corruption) "Data Stream Packet"  
3 : DATA FaF Packet, Fire and Forget, Gets no response and does not need stream synchronisation (Header SYNC field not used, SYNC not incremented)

Depending on the _Packet Type_ the Packet can have 2 formats:  

#### Data Packet (Frame Start + 6 bytes + Payload Length + 2 Bytes)

**[ 8 bit] Sync** : Previous Packet Sync + 1  
**[ 8 bit] Channel** : The Service channel to send the packet to  
**[ 8 bit] Packet ID** : Packet ID passed to Service running on _Channel_  
**[16 bit] Payload Length**: Lengths of the optional Packet Payload  
**[ 8 bit] Header Checksum** : CRC8 of all Header bytes  
_optional_  
**Packet Payload**: _Payload Length_ bytes of data  
**[16 bit] Payload Checksum**: CRC16 CCIT Poly : 0x1021 checksum of the payload  

#### Response Packet (Frame Start + 3 Byte)

**[8 bit] Response Code**  
**[8 bit] Sync ID** : The Sync ID of the packet being responded to  
**[8 bit] Header Checksum** : CRC8 of all Header bytes  
