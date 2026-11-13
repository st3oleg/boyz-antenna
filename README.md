# Stazione base antenna

Questa cartella contiene il programma della stazione base RTK e il file di servizio associato:

- `base_manager.py`: server TCP della stazione base.
- `base_service`: configurazione del servizio systemd.

## Aggiornare il Raspberry da GitHub

Dalla cartella del progetto, scarica gli aggiornamenti:

```bash
cd ~/Documents
git pull --ff-only
```

Controlla lo stato e l’ultimo commit:

```bash
git status --short --branch
git log -1 --oneline
```

## Pubblicare modifiche su GitHub

Dopo aver modificato uno o più file, controlla cosa è cambiato:

```bash
cd ~/Documents
git status --short --branch
```

Aggiungi i file modificati, crea un commit e pubblicalo:

```bash
git add README.md base_manager.py base_service
git commit -m "Descrizione breve della modifica"
git push
```

Se hai modificato altri file, aggiungi i loro nomi al comando `git add`. Se hai modificato solo il README, puoi usare `git add README.md`.

## Comandi del servizio

Dopo aver installato `base_service` come unità systemd, puoi controllarlo, riavviarlo e consultare gli ultimi log:

```bash
sudo systemctl status base_service
sudo systemctl restart base_service
sudo journalctl -u base_service -n 50 --no-pager
```

Se appare `Unit base_service.service could not be found`, il file `base_service` non è ancora installato come unità systemd.