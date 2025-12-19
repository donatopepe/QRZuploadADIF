# QRZ ADIF Uploader

Script Python per caricare file ADIF su QRZ Logbook.

## Requisiti
- Python 3.x installato (Windows: scarica da https://www.python.org/downloads/ e seleziona “Add Python to PATH”).
- Modulo `requests` installato:
  - `pip install requests`

## Configurazione (`configuration.json`)
Il file viene creato automaticamente se manca. Compila i campi:
- `login_url`: lascia `https://www.qrz.com/login`.
*- `adif_url`: lascia `https://logbook.qrz.com/adif`.
- `username`: il tuo username/callsign QRZ (es. `TUO_CALL`).
- `password`: la tua password QRZ.
- `book_id`: ID del logbook (es. `123456`). Puoi leggerlo da:
  - testo “Book #<numero>” nella pagina del logbook,
  - sorgente/rete di `https://logbook.qrz.com/logbook` cercando `bid`/`bookid`,
  - input nascosto `id="bid"` nel modale “Import ADI File”.
- `sbook`: `0` se usi un solo logbook.
- `adif_path`: percorso completo al file ADIF (es. `C:\\Percorso\\file.adi`).
- `allow_duplicates`: `false` (default) o `true` per accettare duplicati.
- `email_report`: `false` (default) o `true` per richiedere report email.
- `log_path`: file di log (default `upload_adif.log`).
- `twofactor_code` (opzionale): codice 2FA se richiesto; lascia vuoto se non serve.
- `trust_device` (opzionale): `false` (default) o `true` per “Trust this device for 30 days”.

## Uso
1) Assicurati che Python sia nel PATH (`python --version`) e che `requests` sia installato (`pip install requests`).
2) Compila `configuration.json` con i tuoi dati.
3) Esegui:
   - `run_upload_adif.bat` (Windows), oppure
   - `python upload_adif.py`
4) Controlla l’esito in console e nel log (`upload_adif.log`).

## File
- `upload_adif.py`: script principale.
- `run_upload_adif.bat`: avvio rapido su Windows.
- `configuration.json`: credenziali e parametri (ignorato da git).
- `upload_adif.log`: log esecuzione (ignorato da git).

## Note
- Non committare credenziali: `configuration.json` e `upload_adif.log` sono già ignorati in `.gitignore`.
- Se usi 2FA, inserisci `twofactor_code` prima di eseguire. Se non è richiesto, lascialo vuoto.
