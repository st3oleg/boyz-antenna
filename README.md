# Terminator — Raspberry Base RTK

**Stato:** server Base e unità systemd preparati in locale; installazione e verifica hardware ancora da eseguire.

## Hardware e rete

- Raspberry Pi 4 con HAT Waveshare LC29H-BS configurata come stazione Base.
- Antenna GNSS installata fissa e con cielo aperto.
- Wi-Fi domestico Vodafone; Tailscale previsto per il collegamento privato con il Rover e il PC.
- HAT collegata al Raspberry tramite UART GPIO: `/dev/serial0` punta a `/dev/ttyS0`; la prova ha usato 115200 baud. `/dev/ttyUSB0` assente è atteso con il jumper in modalità UART Raspberry.

## Stato osservato il 16 novembre 2026

La lettura manuale di `/dev/ttyS0` ha restituito byte che iniziano con `D3` e include un header RTCM di tipo 1005, oltre a messaggi di osservazione. La seriale della BS quindi emette frame RTCM3; il test con `grep GGA` non li mostrava perché cerca solo testo NMEA.

Il servizio Raspberry esistente è stato osservato in `auto-restart` con `SyntaxError: bytes can only contain ASCII literal characters` alla stringa HTML `PAGINA_DI_TEST = b"..."`, dovuto al carattere `…`; è stato poi fermato. Questa cartella contiene una versione locale rinnovata di `base_manager.py` e `base.service`. Non sono stati copiati sul Raspberry e non sostituiscono il servizio remoto finché non vengono installati manualmente.

Il sorgente remoto incollato prevedeva di estrarre la posizione Base da RTCM 1005/1006, inoltrare i frame validi ai client Rover e servire HTTP/WebSocket. La versione locale sostituisce il WebSocket Base con un'API HTTP interrogata dal frontend e non è stata ancora verificata end-to-end.

La nuova versione locale inoltra frame RTCM con CRC valido sul TCP 5000, espone `GET /api/state` e `POST /api/rover` su HTTP 8080, e conserva stato Base/ultima posizione Rover in `base_state.sqlite3`. Il POST Rover richiede `ROVER_API_TOKEN`; la pagina statica è ospitata dal repository Rover/Netlify, non da questo server Base.

## Installazione prevista sul Raspberry Base

Dal clone locale di `antenna-main`, dopo aver verificato il branch e i file:

```bash
sudo install -m 0644 base_manager.py /home/antenna/Documents/base_manager.py
sudo install -m 0644 base.service /etc/systemd/system/base.service
sudo install -d -m 0750 /etc/terminator
sudo nano /etc/terminator/base.env
```

Configurare in `/etc/terminator/base.env` almeno:

```text
BASE_SERIAL_PORT=/dev/serial0
BASE_BAUD_RATE=115200
BASE_RTCM_PORT=5000
BASE_HTTP_PORT=8080
BASE_DB_PATH=/home/antenna/Documents/base_state.sqlite3
ROVER_API_TOKEN=<token-lungo-generato-fuori-dal-repository>
ALLOWED_ORIGINS=https://<sito-netlify>,https://<nome-https-tailscale-della-base>
```

Creare lo stesso token nell'ambiente del Rover come `BASE_API_TOKEN`; non salvarlo nel frontend o nel Git. Il demone serve HTTP, mentre la pagina Netlify deve raggiungere l'API tramite HTTPS nella tailnet. Le porte Base non devono essere inoltrate direttamente da Internet; limitare l'accesso TCP RTCM con le ACL/firewall Tailscale.

Poi caricare e avviare l'unità:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now base.service
sudo systemctl status base.service --no-pager -l
```

Queste istruzioni non sono state eseguite sul Raspberry. `pyserial` deve essere installato e l'utente `antenna` deve poter usare `/dev/serial0` tramite il gruppo `dialout`.

## Diagnostica

Controllare lo stato del servizio:

```bash
sudo systemctl status base.service --no-pager -l
sudo journalctl -u base.service -n 50 --no-pager
```

Verificare chi usa la seriale prima di una lettura manuale:

```bash
sudo fuser -v /dev/ttyS0
```

Con il servizio fermo e la seriale libera, la presenza di frame `D3` è compatibile con RTCM3. Una ricerca testuale di `GGA` non verifica un flusso RTCM binario. Non modificare la configurazione del ricevitore finché la variante hardware, la seriale e lo stato del servizio non sono stati verificati.

## Prossimi passaggi

1. Verificare nel sorgente locale le escape RTCM e avviare l'unità senza restart loop.
2. Verificare CRC e coordinate dal messaggio 1005/1006, oltre alla modalità/completamento survey-in.
3. Configurare `ROVER_API_TOKEN` e `ALLOWED_ORIGINS` sul Base e i parametri `BASE_*` sul Rover.
4. Confermare collegamento RTCM TCP via Tailscale e stato FIX del DA.
5. Configurare HTTPS reverse proxy Tailscale per l'API browser e distribuire il frontend statico su Netlify.

Per architettura condivisa, requisiti Netlify/Tailscale e criteri d'integrazione, vedere `boyz-terminator-main/docs/architettura-rtk-base-rover.md` nel repository Rover.

## GitHub

Aggiornare il clone con `git pull --ff-only`. Per pubblicare le modifiche:

```bash
git status --short --branch
git add README.md base_manager.py base.service
git commit -m "Aggiorna server Base RTCM e API"
git push
```

L'unità sorgente è `base.service` e va installata come `/etc/systemd/system/base.service`.
