#!/bin/bash
# Wordt aangeroepen door de Finder-snelactie "Anonimiseer" (zie README, "Finder-snelactie").
# Anonimiseert de geselecteerde bestanden en meldt het resultaat met een melding.
# Zonder terminal: er worden geen vragen gesteld (--niet-vragen).

MAP="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$HOME/.pdf-anonimiseren/laatste_run.txt"
mkdir -p "$(dirname "$LOG")"

if [ -f "$MAP/.venv/bin/activate" ]; then
    source "$MAP/.venv/bin/activate"
fi
cd "$MAP" || exit 1

python3 anonimiseer.py --niet-vragen "$@" > "$LOG" 2>&1
STATUS=$?

if grep -q "_CONTROLEREN\|FOUT\|Traceback" "$LOG"; then
    TITEL="Anonimiseren: nakijken!"
    TEKST="Er is iets gevonden dat je moet nakijken. Zie het verslag."
    TOON_LOG=1
else
    TITEL="Anonimiseren klaar"
    TEKST="De bestanden staan in de map geanonimiseerd."
    TOON_LOG=0
fi
osascript -e "display notification \"$TEKST\" with title \"$TITEL\"" 2>/dev/null

# Uitvoermap van het eerste bestand openen, en bij problemen het verslag.
EERSTE="$1"
if [ -n "$EERSTE" ]; then
    if [ -d "$EERSTE" ]; then UIT="$EERSTE/geanonimiseerd"; else UIT="$(dirname "$EERSTE")/geanonimiseerd"; fi
    [ -d "$UIT" ] && open "$UIT"
fi
[ "$TOON_LOG" = "1" ] && open -e "$LOG"
exit $STATUS
