#!/usr/bin/env python3
"""
Base RTK Station - Terminator GNSS Base
- Legge correzioni RTCM3 dal modulo GNSS su /dev/serial0 (115200 baud)
- Server TCP asincrono su porta 5000 per lo streaming verso il Rover via Tailscale
"""

import sys
import os
import time
import asyncio
import serial

PORTA_SERIALE = "/dev/serial0"
BAUDRATE = 115200
PORTA_TCP = 5000

client_connessi = set()


async def serial_reader_task():
    """Legge lo stream RTCM3 dal GNSS e lo invia a tutti i client TCP."""
    global client_connessi
    while True:
        try:
            print(f"[SERIAL] Apertura {PORTA_SERIALE} a {BAUDRATE} baud...")
            ser = serial.Serial(PORTA_SERIALE, BAUDRATE, timeout=1)
            print(f"[SERIAL] Porta aperta con successo. In attesa di frame RTCM...")
            loop = asyncio.get_running_loop()

            while True:
                # Lettura non bloccante dei pacchetti seriali
                data = await loop.run_in_executor(None, ser.read, 1024)
                if data:
                    if client_connessi:
                        for writer in list(client_connessi):
                            try:
                                writer.write(data)
                                await writer.drain()
                            except Exception:
                                client_connessi.discard(writer)
                else:
                    await asyncio.sleep(0.01)

        except serial.SerialException as e:
            print(f"[SERIAL-ERR] Errore porta seriale: {e}. Nuovo tentativo tra 3s...")
            await asyncio.sleep(3)
        except Exception as e:
            print(f"[ERR] Errore inatteso: {e}. Nuovo tentativo tra 3s...")
            await asyncio.sleep(3)


async def handle_client(reader, writer):
    """Gestisce la connessione TCP in entrata dal Rover."""
    addr = writer.get_extra_info('peername')
    print(f"[TCP] Rover connesso da {addr}")
    client_connessi.add(writer)

    try:
        while True:
            data = await reader.read(1024)
            if not data:
                break
    except Exception:
        pass
    finally:
        client_connessi.discard(writer)
        print(f"[TCP] Rover disconnesso da {addr}")
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def main():
    print("=" * 60)
    print(" BASE RTK GNSS SERVER - TERMINATOR")
    print(f" Seriale GNSS: {PORTA_SERIALE} @ {BAUDRATE} baud")
    print(f" Server TCP:   porta {PORTA_TCP} (0.0.0.0)")
    print("=" * 60)

    server = await asyncio.start_server(handle_client, '0.0.0.0', PORTA_TCP)
    asyncio.create_task(serial_reader_task())

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[BASE] Chiusura server.")